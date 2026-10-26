"""Authorized isolated HTTPS target: real HTTP, browser and public UDP checks.

This is a client, never a server. Supply your own test CA and scoped SPKI pin.
Optional temporary acceptance tokens stay in memory/a private file and are
never written into reports. There are no request-route mocks, fake clocks,
local SDP rewrites or blanket certificate-error bypasses.
"""
import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from rtc_browser_client import WIRE_CLIENT

STATS = r"""async stats(id) {
 const p=peers.get(id),rows=await p.pc.getStats(),transport=[...rows.values()].find(v=>v.type==='transport'&&v.selectedCandidatePairId);
 const pair=transport?rows.get(transport.selectedCandidatePairId):[...rows.values()].find(v=>v.type==='candidate-pair'&&v.nominated&&v.state==='succeeded');
 if(!pair)throw Error('no selected real candidate pair');
 const fields=v=>({address:v.address||v.ip,port:v.port,protocol:v.protocol,candidateType:v.candidateType});
 return {pc:p.pc.connectionState,dtls:p.pc.sctp.transport.state,sctp:p.pc.sctp.state,
   pairState:pair.state,bytesSent:pair.bytesSent,bytesReceived:pair.bytesReceived,
   local:fields(rows.get(pair.localCandidateId)),remote:fields(rows.get(pair.remoteCandidateId))};
 },
 diagnostics() {
  return [...peers.entries()].map(([id,p])=>({id,pc:p.pc.connectionState,ice:p.pc.iceConnectionState,
   gathering:p.pc.iceGatheringState,test:p.test.readyState,control:p.control.readyState,
   offerCandidateCount:p.offer.sdp.split(/\r?\n/).filter(v=>v.startsWith('a=candidate:')).length,
   answerCandidates:p.answerCandidates}));
 },
 """
assert WIRE_CLIENT.count('async closeAll(){') == 1
REMOTE_CLIENT = WIRE_CLIENT.replace('async closeAll(){', STATS + 'async closeAll(){')
assert REMOTE_CLIENT.count('let answer=response.body;') == 1
REMOTE_CLIENT = REMOTE_CLIENT.replace('let answer=response.body;',
    r"""let answer=response.body;p.answerCandidates=answer.sdp.split(/\r?\n/).filter(v=>v.startsWith('a=candidate:')).map(v=>{const a=v.split(/\s+/);return {protocol:a[2],address:a[4],port:Number(a[5]),type:a[7]};});""")


class Target:
    def __init__(self, base, ca, token=''):
        value = urlsplit(base)
        if (value.scheme != 'https' or not value.hostname or value.path not in ('', '/') or
                value.username or value.password or value.query or value.fragment):
            raise ValueError('a plain HTTPS origin is required')
        self.host, self.port = value.hostname, value.port or 443
        self.base = base.rstrip('/')
        self.context = ssl.create_default_context(cafile=str(ca))
        self.headers = {'X-Speedtest-Acceptance': token} if token else {}

    def connection(self):
        return http.client.HTTPSConnection(self.host, self.port, context=self.context, timeout=15)

    def request(self, method, path, body=None, authenticated=True):
        connection = self.connection()
        try:
            connection.request(method, path, body=body, headers=self.headers if authenticated else {})
            response = connection.getresponse()
            data = response.read()
            headers = {key.lower(): value for key, value in response.getheaders()}
            return dict(status=response.status, headers=headers, body=data)
        finally:
            connection.close()


def http_checks(target, role, backend):
    # A rejected default-trust handshake is not a connection timeout/refusal.
    connection = http.client.HTTPSConnection(target.host, target.port, timeout=15)
    try:
        try:
            connection.request('GET', '/health')
            connection.getresponse()
        except ssl.SSLCertVerificationError:
            trust_rejected = True
        else:
            raise AssertionError('ephemeral certificate unexpectedly trusted by default')
    finally:
        connection.close()
    if target.headers:
        assert target.request('GET', '/health', authenticated=False)['status'] == 403
    health = target.request('GET', '/health')
    assert health['status'] == 200
    assert health['headers']['x-speedtest-acceptance-backend'] == backend
    assert target.request('GET', '/latency')['status'] == 204
    result = dict(verified_chain_and_ip_hostname=True, default_trust_rejected=trust_rejected,
                  acceptance_auth_enforced=bool(target.headers), proxy_backend=backend)
    if role == 'probe':
        statuses = {}
        for method, path in [('GET', '/download?size=10240'), ('GET', '/download-stream'),
                             ('GET', '/download-100k'), ('POST', '/upload'),
                             ('POST', '/upload-stream'), ('POST', '/upload-progress')]:
            response = target.request(method, path, b'' if method == 'POST' else None)
            assert response['status'] == 404, (method, path, response['status'])
            statuses[method + ' ' + path] = response['status']
        result['probe_payload_denials'] = statuses
        return result
    sizes = [10240, 102400, 1048576]
    for size in sizes:
        response = target.request('GET', '/download?size=' + str(size))
        assert response['status'] == 200 and len(response['body']) == size
        response = target.request('POST', '/upload', b'x' * size)
        assert response['status'] == 200 and json.loads(response['body'])['bytes'] == size
    created = target.request('POST', '/upload-progress?lanes=1&warmupMs=0&chunked=1', b'')
    assert created['status'] == 200
    identity = json.loads(created['body'])['id']
    connection = target.connection()
    try:
        connection.request('GET', '/upload-progress?id=' + identity, headers=target.headers)
        stream = connection.getresponse()
        assert stream.status == 200
        first = json.loads(stream.readline())
        assert not first['done'] and first['bytes'] == 0, first
        for size in sizes:
            response = target.request('POST', '/upload-stream?warmupMs=0&lane=0&progressId=' + identity, b'x' * size)
            assert response['status'] == 200 and json.loads(response['body'])['bytes'] == size
        assert target.request('POST', '/upload-progress?finish=1&id=' + identity, b'')['status'] == 200
        records = [first]
        while not records[-1]['done']:
            line = stream.readline()
            assert line, 'progress stream ended without a final record'
            records.append(json.loads(line))
        final = records[-1]
        assert not final['failed'] and final['bytes'] == final['measuredBytes'] == sum(sizes)
    finally:
        connection.close()
    result.update(upload_download_sizes=sizes, ndjson_before_upload=True, final_progress_bytes=sum(sizes))
    return result


def validate_observation(value, stats, expected_ip):
    result = value['result']
    assert not value['errors']
    planned = result['planned']
    assert planned > 0
    assert result['scheduled'] + result['skipped'] == planned
    assert 0 <= result['actualSent'] <= result['scheduled']
    assert result['actualSent'] == result['sent'] == len(result['sentSeqs'])
    assert len(set(result['sentSeqs'])) == result['actualSent']
    assert result['received'] == result['acked'] == len(result['samples'])
    assert 0 <= result['received'] <= result['actualSent']
    assert result['uploadReceived'] == len(result['uploadReceivedSeqs']) == len(set(result['uploadReceivedSeqs']))
    uploaded = result['clientUploadActualSent']
    if uploaded is None:
        assert not result['complete']
        assert any(reason in result['invalidReasons'] for reason in
                   ['UPLOAD_COMPLETE_MISSING', 'UPLOAD_COMPLETE_INVALID'])
        assert 0 <= result['uploadReceived'] <= planned
    else:
        assert 0 <= result['uploadReceived'] <= uploaded
    assert result['complete'] == result['final']
    if result['complete']:
        assert not result['invalidReasons']
    else:
        assert result['invalidReasons'], 'An incomplete protocol result needs reasons'
    assert result['probeTransport'] == dict(ordered=False, maxRetransmits=0, zeroRetransmitHonored=True)
    assert stats['pc'] == stats['dtls'] == stats['sctp'] == 'connected'
    assert stats['pairState'] == 'succeeded' and stats['bytesSent'] > 0 and stats['bytesReceived'] > 0
    remote = ipaddress.ip_address(stats['remote']['address'])
    assert remote.is_global and str(remote) == expected_ip
    assert stats['remote']['protocol'] == 'udp'
    # Match the actual Rust/frontend semantics: complete/final acknowledge
    # protocol finalization, NOT loss-free delivery. The .99 density criterion
    # concerns scheduler actualSent/planned, not received/actualSent. Preserve
    # missing ACKs as measurements, never reinterpret loss as invalid metadata.
    return dict(protocol_complete=result['complete'],
                scheduler_density=result['actualSent'] / planned,
                upload_scheduler_density=None if uploaded is None else uploaded / planned,
                scheduler_density_at_least_099=(result['actualSent'] / planned >= .99 and
                                                uploaded is not None and uploaded / planned >= .99),
                missing_download_acks=result['actualSent'] - result['received'],
                missing_upload_probes=None if uploaded is None else uploaded - result['uploadReceived'])


def browser_checks(target, pin, role, rounds=3, observe=lambda name, value: None):
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=[
            '--disable-gpu', '--disable-features=WebRtcHideLocalIpsWithMdns',
            '--ignore-certificate-errors-spki-list=' + pin])
        page = browser.new_page(extra_http_headers=target.headers)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            response = page.goto(target.base + '/', wait_until='domcontentloaded', timeout=30000)
            assert response.status == (200 if role == 'ui' else 404)
            frontend = None
            if role == 'ui':
                page.wait_for_function("() => ['RTT complete','Error'].includes(document.querySelector('#phase').textContent)", timeout=45000)
                frontend = dict(phase=page.locator('#phase').inner_text(),
                                incomplete=not page.locator('#rttIncompleteBadge').evaluate('node => node.hidden'))
                observe('actual_frontend', frontend)
                assert frontend['phase'] == 'RTT complete', page.locator('#log').inner_text()
                for selector in ['#rttUpAvg', '#rttDownAvg', '#rttUpP95', '#rttDownP95']:
                    assert float(page.locator(selector).inner_text()) >= 0
            page.evaluate(REMOTE_CLIENT)
            workloads = [dict(rate=20, durationMs=2000) for _ in range(rounds)]
            workloads.append(dict(rate=100, durationMs=20000))
            for index, workload in enumerate(workloads):
                try:
                    peer = page.evaluate('() => window.__rtcWire.connect()')
                except BaseException:
                    observe('connect-failure-' + str(index),
                            page.evaluate('() => window.__rtcWire.diagnostics()'))
                    raise
                try:
                    value = page.evaluate('([id,options]) => window.__rtcWire.measure(id,options)', [peer, workload])
                    stats = page.evaluate('id => window.__rtcWire.stats(id)', peer)
                    result = value['result']
                    raw = dict(round=index, workload=workload, result=result, selected_pair=stats)
                    observe('round-' + str(index), raw)
                    assert result['planned'] == workload['rate'] * workload['durationMs'] // 1000
                    raw['validation'] = validate_observation(value, stats, target.host)
                    observe('round-' + str(index), raw)
                    results.append(raw)
                finally:
                    page.evaluate('id => window.__rtcWire.close(id)', peer)
                time.sleep(.5)
            assert not errors, errors
            assert all(value['validation']['protocol_complete'] for value in results)
            assert all(value['validation']['scheduler_density_at_least_099'] for value in results)
            return dict(actual_frontend=frontend, rounds=results, no_route_mocks=True,
                        no_sdp_rewrites=True, actual_public_udp_selected=True,
                        strict_probe_transport_verified=True, scoped_certificate_pin=True)
        finally:
            try:
                page.evaluate('() => window.__rtcWire && window.__rtcWire.closeAll()')
            except Exception:
                pass
            browser.close()


def checks(base, ca, token, pin, role, backend, rounds=3, observe=lambda name, value: None):
    target = Target(base, ca, token)
    http = http_checks(target, role, backend)
    observe('http', http)
    return dict(http=http, browser=browser_checks(target, pin, role, rounds, observe))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['url', 'ca', 'pin', 'backend', 'role', 'output']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    if args.role not in ['ui', 'agent', 'probe'] or args.backend not in ['cpp', 'rust'] or not 1 <= args.rounds <= 10:
        parser.error('invalid role/backend/round count')
    output = Path(args.output)
    if output.exists():
        parser.error('choose a new evidence output; never overwrite a previous result')
    output.parent.mkdir(parents=True, exist_ok=True)
    token = args.token_file.read_text().strip() if args.token_file else ''
    report = dict(passed=False, observations={})
    def observe(name, value):
        report['observations'][name] = value
        if name == 'http':
            print('PASS authorized HTTPS/API preliminary checks', flush=True)
        elif name.startswith('round-') and 'validation' in value:
            status = value['validation']
            print('OBSERVED ' + name + ': protocol_complete=' + str(status['protocol_complete']) +
                  ', scheduler_density=' + str(status['scheduler_density']), flush=True)
        elif name.startswith('connect-failure'):
            print('FAIL connection; candidate/state metadata retained in private evidence', flush=True)
    print('RUN authorized remote ' + args.backend + '/' + args.role + ' checks', flush=True)
    try:
        report['result'] = checks(args.url, Path(args.ca), token, args.pin, args.role,
                                  args.backend, args.rounds, observe)
        report['passed'] = True
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error).replace(token, '[redacted]') if token else str(error))
        raise
    finally:
        with output.open('x') as evidence:
            evidence.write(json.dumps(report, indent=2) + '\n')
        print('Remote check ' + ('PASSED' if report['passed'] else 'FAILED') +
              '; private evidence saved', flush=True)
