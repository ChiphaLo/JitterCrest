"""Optional headless central UI -> two Rust-only agents regression (local TLS only)."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright


def free_port():
    # Exclude Chromium's restricted ports and keep all allocated ports distinct.
    while True:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port > 10000 and port not in ports:
            ports.add(port)
            return port


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ui-binary', type=Path, required=True)
parser.add_argument('--agent-binary', type=Path, required=True)
parser.add_argument('--caddy', default=shutil.which('caddy'))
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
assert args.ui_binary.is_file() and args.agent_binary.is_file() and args.caddy
args.output.mkdir(parents=True, exist_ok=True)
ports = set()
ui_port, agent1_port, agent2_port = [free_port() for _ in range(3)]
backend_ports = [free_port() for _ in range(3)]
ui = f'https://127.0.0.1:{ui_port}'
agents = [f'https://localhost:{agent1_port}', f'https://127.0.0.1:{agent2_port}']
nodes = [{'id': f'agent{i+1}', 'label': f'Agent {i+1}', 'base': base, 'rtcTargetMs': 25}
         for i, base in enumerate(agents)]
evidence = {'ui': ui, 'agents': agents, 'nodes': [], 'requests': [], 'page_errors': []}
with tempfile.TemporaryDirectory(prefix='central-ui-') as temporary:
    directory = Path(temporary)
    config = directory / 'Caddyfile'
    config.write_text('{\n admin off\n auto_https disable_redirects\n}\n' + '\n'.join(
        origin + ' {\n bind 127.0.0.1\n tls internal\n' +
        # No measurement traffic can pass through the UI proxy, even by accident.
        (' @measurement path /download* /upload* /webrtc/*\n respond @measurement 418\n' if i == 0 else '') +
        f' reverse_proxy 127.0.0.1:{backend_ports[i]}\n}}\n'
        for i, origin in enumerate([ui] + agents)))
    children = []
    with (args.output / 'services.log').open('w') as log:
        try:
            for i, port in enumerate(backend_ports):
                env = dict(os.environ, SPEEDTEST_HOST='127.0.0.1', PORT=str(port), TRUST_PROXY_HEADERS='0',
                    SPEEDTEST_NODE_NAME=f'test-{i}', ALLOWED_ORIGINS=ui, SPEEDTEST_ALLOW_CROSS_SITE='1',
                    PUBLIC_CORS='0', SPEEDTEST_NODES=json.dumps(nodes), SPEEDTEST_WEBRTC_UDP4='',
                    SPEEDTEST_RUST_RTC_EXPERIMENTAL='1', SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST='1',
                    SPEEDTEST_RTC_LEGACY_CONTROL='0', DOWNLOAD_BYTE_BUDGET_PER_IP=str(32 * 1024**3),
                    UPLOAD_BYTE_BUDGET_PER_IP=str(32 * 1024**3))
                binary = args.ui_binary if i == 0 else args.agent_binary
                children.append(subprocess.Popen([str(binary.resolve())], cwd=directory, env=env, stdout=log, stderr=log))
            children.append(subprocess.Popen([args.caddy, 'run', '--config', str(config), '--adapter', 'caddyfile'],
                env=dict(os.environ, APPDATA=str(directory), XDG_DATA_HOME=str(directory), XDG_CONFIG_HOME=str(directory)),
                stdout=log, stderr=log))
            for origin in [ui] + agents:
                for _ in range(150):
                    assert all(child.poll() is None for child in children), 'test service exited'
                    try:
                        # Disposable loopback TLS only. No production trust configuration is changed.
                        with urlopen(origin + '/health', context=ssl._create_unverified_context(), timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError('Rust/Caddy startup failed')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=['--ignore-certificate-errors'])
                context = browser.new_context(ignore_https_errors=True)
                context.on('request', lambda r: evidence['requests'].append({'url': r.url, 'method': r.method}))
                page = context.new_page()
                page.on('pageerror', lambda e: evidence['page_errors'].append(str(e)))
                cdp = context.new_cdp_session(page)
                cdp.send('Network.enable')
                cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': 5,
                    'downloadThroughput': 32e6/8, 'uploadThroughput': 24e6/8})
                page.goto(ui + '/?singleProtocol=h2')
                # Exercise the actual served H1 URL selector with an independent
                # UI host. DevTools evaluation only; no test hook in product code.
                source = re.search(r'function singleStreamDownloadConfig\(\) \{.*?\n    \}\n    function api', page.content(), re.S).group(0).rsplit('function api', 1)[0]
                primary_id = re.search(r'selectedNode.id === "([^"]+)" && !forceH2', source).group(1)
                targets = page.evaluate(
                    "([id]) => { const location={search:''}; let selectedNode={id,base:'https://agent.example.net/'};"
                    "const serviceBase=()=>selectedNode.base;" + source +
                    "const inferred=singleStreamDownloadConfig(); selectedNode.singleDownloadBase='https://h1.example.net:9443/';"
                    "return {inferred,explicit:singleStreamDownloadConfig()};}", [primary_id])
                assert targets['inferred']['base'] == 'https://agent.example.net:8443/'
                assert targets['explicit']['base'] == 'https://h1.example.net:9443/'
                evidence['single_download_targets'] = targets
                for i, node in enumerate(nodes):
                    if i:
                        page.select_option('#nodeSelect', node['id'])
                    page.wait_for_function("() => document.getElementById('phase').textContent === 'RTT complete' || globalThis.__speedtestEvidence?.phase === 'webrtc-failed'", timeout=90000)
                    result = page.evaluate("({phase: document.getElementById('phase').textContent, rtc: globalThis.__speedtestEvidence})")
                    assert result['phase'] == 'RTT complete', result
                    assert result['rtc']['rtt']['upload']['complete'] and result['rtc']['rtt']['download']['complete']
                    assert page.url.startswith(ui), 'node switch navigated away from central UI'
                    # A real browser CORS check including the custom signaling headers.
                    result['preflight_and_http'] = page.evaluate('''async base => {
                      const up = await fetch(base+'/upload', {method:'POST', headers:{
                        'Content-Type':'application/octet-stream', 'X-Speedtest-Attempt-Id':'central-test'},
                        body:new Uint8Array(1024)});
                      const down = await fetch(base+'/download?size=10240');
                      return {upload:up.status, accounting:await up.json(), download:down.status,
                        bytes:(await down.arrayBuffer()).byteLength};
                    }''', agents[i])
                    assert result['preflight_and_http']['accounting']['bytes'] == 1024
                    assert result['preflight_and_http']['bytes'] == 10240
                    page.locator('#run').click()
                    page.wait_for_function("() => ['Speed complete','Speed error'].includes(document.getElementById('phase').textContent)", timeout=90000)
                    assert page.locator('#phase').inner_text() == 'Speed complete', page.locator('#log').inner_text()
                    result['charts'] = page.evaluate('''() => ['download','upload'].map(kind => {
                      const line=document.querySelector('#'+kind+'SpeedChart .speed-result-line');
                      return {kind,result:Number(line.dataset.resultMbps),
                        samples:Number(document.querySelector('#'+kind+'SpeedChart svg').dataset.sampleCount),
                        horizontal:line.getAttribute('y1')===line.getAttribute('y2')};
                    })''')
                    assert all(c['result'] > 0 and c['samples'] > 2 and c['horizontal'] for c in result['charts'])
                    evidence['nodes'].append(result)
                assert not evidence['page_errors'], evidence['page_errors']
                for agent in agents:
                    assert any(r['url'].startswith(agent+'/webrtc/offer') for r in evidence['requests'])
                    assert any(r['url'].startswith(agent+'/upload') for r in evidence['requests'])
                    assert context.request.get(agent+'/').status == 404, 'agent unexpectedly serves UI'
                assert not any(r['url'].startswith(ui+path) for r in evidence['requests'] for path in ['/download','/upload','/webrtc/'])
                evidence['passed'] = True
                browser.close()
        finally:
            for child in reversed(children):
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            (args.output / 'report.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
print(json.dumps({'passed': evidence.get('passed', False), 'report': str(args.output / 'report.json')}))
