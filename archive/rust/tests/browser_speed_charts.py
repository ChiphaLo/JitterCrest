"""Optional real Rust/Caddy HTTP/2 browser regression; no npm or Node project."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
exe = '.exe' if os.name == 'nt' else ''
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, default=ROOT / ('target/release/speedtest-rust' + exe))
parser.add_argument('--caddy', default=shutil.which('caddy') or str(Path.home() / ('go/bin/caddy' + exe)))
parser.add_argument('--output', type=Path, default=ROOT / 'target/browser-artifacts')
parser.add_argument('--streams', choices=('1', '2', '4', '8'), default='1')
parser.add_argument('--download-mbps', type=float, default=33.554432)
parser.add_argument('--upload-mbps', type=float, default=25.165824)
parser.add_argument('--latency-ms', type=float, default=5)
parser.add_argument('--force-chunked', action='store_true', help='Test the multi-stream fallback for browsers without request streaming')
args = parser.parse_args()
assert args.binary.is_file() and Path(args.caddy).is_file(), 'Build the Rust binary and specify an installed Caddy'
args.output.mkdir(parents=True, exist_ok=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


backend_port, proxy_port = free_port(), free_port()
origin = f'https://127.0.0.1:{proxy_port}'
evidence = {'backend': 'Rust', 'streams': int(args.streams), 'page_errors': [],
            'network_limits_mbps': [args.download_mbps, args.upload_mbps], 'latency_ms': args.latency_ms,
            'forced_chunked_upload': args.force_chunked}
temporary_root = ROOT / 'target/browser-temporary'
temporary_root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='loopback-', dir=temporary_root) as temporary:
    directory = Path(temporary).resolve()
    assert directory.is_relative_to(temporary_root.resolve())
    config = directory / 'Caddyfile'
    config.write_text('''{
 admin off
 skip_install_trust
 auto_https disable_redirects
}
''' + origin + ''' {
 bind 127.0.0.1
 tls internal
 reverse_proxy 127.0.0.1:''' + str(backend_port) + '\n}\n')
    env = dict(os.environ, SPEEDTEST_HOST='127.0.0.1', PORT=str(backend_port), TRUST_PROXY_HEADERS='0',
               SPEEDTEST_NODES='[{"id":"local","label":"Local server","base":""}]', ALLOWED_ORIGINS=origin,
               SPEEDTEST_RUST_RTC_EXPERIMENTAL='0', DOWNLOAD_BYTE_BUDGET_PER_IP=str(32 * 1024**3),
               UPLOAD_BYTE_BUDGET_PER_IP=str(32 * 1024**3))
    children = []
    with (directory / 'services.log').open('w') as log:
        try:
            children.append(subprocess.Popen([str(args.binary.resolve())], cwd=directory, env=env, stdout=log, stderr=log))
            children.append(subprocess.Popen([args.caddy, 'run', '--config', str(config), '--adapter', 'caddyfile'],
                env=dict(os.environ, APPDATA=str(directory), XDG_DATA_HOME=str(directory), XDG_CONFIG_HOME=str(directory)),
                stdout=log, stderr=log))
            for _ in range(200):
                assert all(child.poll() is None for child in children), 'test service exited'
                try:
                    # The only TLS exception is this disposable loopback listener.
                    with urlopen(origin + '/health', context=ssl._create_unverified_context(), timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('loopback Rust/Caddy did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=['--ignore-certificate-errors'])
                context = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 1100})
                if args.force_chunked:
                    # Modify only the disposable browser's worker Blob, not the
                    # application source or its CSP, to exercise the fallback.
                    context.add_init_script('''(() => {
                      const OriginalBlob = globalThis.Blob;
                      globalThis.Blob = class extends OriginalBlob {
                        constructor(parts, options) {
                          super(parts.map(part => typeof part === 'string'
                            ? part.replace('function supportsRequestStreams() {', 'function supportsRequestStreams() { return false;')
                            : part), options);
                        }
                      };
                    })();''')
                context.add_init_script('''(() => {
                  globalThis.__workerRecords = [];
                  const OriginalWorker = globalThis.Worker;
                  globalThis.Worker = class extends OriginalWorker {
                    constructor(...args) {
                      super(...args);
                      this.record = {kind: null, samples: [], result: null, error: null};
                      globalThis.__workerRecords.push(this.record);
                      this.addEventListener('message', ({data}) => {
                        if (data.type === 'sample') this.record.samples.push({value: data.value, atMs: data.atMs, source: data.source});
                        if (data.type === 'result') this.record.result = {
                          stable: data.stable, bytes: String(data.bytes), ms: data.ms,
                          queued: String(data.clientBytesQueued || ''), totalBytes: String(data.totalBytes || '')};
                        if (data.type === 'error') this.record.error = data.message;
                      });
                    }
                    postMessage(data, ...args) {
                      this.record.kind = data.kind;
                      return super.postMessage(data, ...args);
                    }
                  };
                })();''')
                page = context.new_page()
                page.on('pageerror', lambda error: evidence['page_errors'].append(str(error)))
                cdp = context.new_cdp_session(page)
                cdp.send('Network.enable')
                cdp.send('Performance.enable')
                cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': args.latency_ms,
                    'downloadThroughput': args.download_mbps * 1e6 / 8, 'uploadThroughput': args.upload_mbps * 1e6 / 8})
                # Existing loopback-only hook skips unrelated RTC startup, not the speed workers.
                page.goto(origin + '/?testMode=webrtc-contract&singleProtocol=h2')
                page.wait_for_selector('#downloadSpeedChart svg')
                assert page.evaluate("performance.getEntriesByType('navigation')[0].nextHopProtocol") == 'h2'
                page.select_option('#speedThreads', args.streams)
                page.eval_on_selector('#run', 'button => { button.disabled = false; button.click(); }')
                page.wait_for_function("() => Number(document.querySelector('#downloadSpeedChart svg').dataset.sampleCount) >= 3 || document.getElementById('phase').textContent === 'Speed error'", timeout=25000)
                assert page.locator('#phase').inner_text() != 'Speed error', page.locator('#log').inner_text()
                assert page.locator('#downloadSpeedChart .speed-result-line').get_attribute('data-provisional') == 'true'
                evidence['download_live_before_final'] = True
                before = page.evaluate('() => __speedtestChartDiagnostics().download')
                perf_before = {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
                page.wait_for_timeout(1000)
                after = page.evaluate('() => __speedtestChartDiagnostics().download')
                perf_after = {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
                evidence['main_thread_one_second'] = {k: perf_after[k]-perf_before[k] for k in ['Timestamp','TaskDuration','ScriptDuration','LayoutDuration','RecalcStyleDuration','LayoutCount']}
                evidence['download_frames_per_second'] = after['frames'] - before['frames']
                assert evidence['download_frames_per_second'] >= 30
                assert after['axisBuilds'] < after['frames'] / 3
                page.locator('#speedTestContainer').screenshot(path=str(args.output / 'live.png'))
                page.wait_for_function("() => document.querySelector('#downloadSpeedChart .speed-result-line') && Number(document.querySelector('#uploadSpeedChart svg').dataset.sampleCount) >= 3", timeout=30000)
                assert page.locator('#uploadSpeedChart .speed-result-line').get_attribute('data-provisional') == 'true'
                evidence['upload_live_before_final'] = True
                page.wait_for_function("() => Number(document.querySelector('#uploadSpeedChart svg').dataset.sampleCount) >= 60",timeout=15000)
                assert page.locator('#uploadSpeedChart .speed-result-line').get_attribute('data-provisional') == 'true'
                page.locator('#uploadSpeedChart').screenshot(path=str(args.output / 'upload-live.png'))
                page.wait_for_function("() => ['Speed complete','Speed error'].includes(document.getElementById('phase').textContent)", timeout=30000)
                assert page.locator('#phase').inner_text() == 'Speed complete', page.locator('#log').inner_text()
                page.wait_for_function("() => [...document.querySelectorAll('.speed-result-line')].every(line => line.dataset.provisional === 'false')")
                evidence['render_diagnostics'] = page.evaluate('() => __speedtestChartDiagnostics()')
                for kind in ['download','upload']:
                    points = evidence['render_diagnostics'][kind]['points']
                    assert 0 <= points[0]['atMs'] < 500, (kind, 'warmup offset leaked into chart', points[0])

                assert evidence['render_diagnostics']['upload']['sources'] == ['server-received']
                evidence['workers'] = page.evaluate('() => globalThis.__workerRecords')
                evidence['charts'] = page.evaluate('''() => ['download','upload'].map(kind => {
                  const chart = document.getElementById(kind + 'SpeedChart');
                  const line = chart.querySelector('.speed-result-line');
                  return {kind, samples: Number(chart.querySelector('svg').dataset.sampleCount),
                    result: Number(line.dataset.resultMbps), display: Number(document.getElementById(kind).textContent),
                    horizontal: line.getAttribute('y1') === line.getAttribute('y2'), dashed: line.getAttribute('stroke-dasharray'),
                    timeAxis: chart.textContent.includes('Time (s)')};
                })''')
                # Retain diagnostics even if a subsequent rendering assertion fails.
                (args.output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
                for chart in evidence['charts']:
                    assert 50 <= chart['samples'] <= 2048 and chart['horizontal'] and chart['dashed'] == '6 4'
                    assert abs(chart['result'] - chart['display']) <= 0.0051 and chart['timeAxis']
                page.locator('#speedTestContainer').screenshot(path=str(args.output / 'desktop.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_timeout(150)
                assert page.eval_on_selector('#speedTestContainer', 'node => node.scrollWidth <= node.clientWidth + 1')
                page.locator('#speedTestContainer').screenshot(path=str(args.output / 'mobile.png'))
                page.emulate_media(color_scheme='dark')
                page.locator('#speedTestContainer').screenshot(path=str(args.output / 'mobile-dark.png'))
                page.eval_on_selector('#run', 'button => button.click()')
                assert page.locator('.speed-result-line[data-result-mbps]').count() == 0
                page.wait_for_function("() => document.querySelector('#uploadSpeedChart svg').dataset.sampleCount === '0'")
                page.reload()
                page.wait_for_selector('#downloadSpeedChart svg')
                assert page.locator('.speed-result-line[data-result-mbps]').count() == 0
                assert page.locator('#downloadSpeedChart svg').get_attribute('data-sample-count') == '0'
                assert not evidence['page_errors'], evidence['page_errors']
                evidence['reset_verified'] = True
                browser.close()
        finally:
            for child in reversed(children):
                child.terminate()
                child.wait(timeout=10)
(args.output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
summary = {key: value for key, value in evidence.items() if key not in ('workers', 'render_diagnostics')}
summary['worker_summaries'] = [{
    'kind': worker['kind'], 'samples': len(worker['samples']),
    'zero_samples': sum(sample['value'] == 0 for sample in worker['samples']),
    'result': worker['result'], 'error': worker['error'],
} for worker in evidence.get('workers', [])]
print(json.dumps(summary, indent=2))
