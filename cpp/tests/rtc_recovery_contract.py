"""Paired native SCTP recovery, preserving the original frontend result window.

Only owned loopback servers and a bounded encrypted UDP relay are used. These
are adverse-path/error contracts, NOT valid completed stability measurements.
No payload bodies, keys or credentials are captured; write reports privately.
"""
import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
from http_contract import Server
from rtc_browser_client import WIRE_CLIENT
from rtc_impaired_relay import ImpairedRelay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpp', type=Path, required=True)
    parser.add_argument('--rust', type=Path, required=True)
    parser.add_argument('--rounds', type=int, choices=range(1, 6), default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('choose a fresh report; retain earlier failures')
    report = dict(passed=False,rounds=args.rounds,cases=[],
        original_frontend_result_window_seconds=25,measurement_acceptance=False,
        packet_identical=False,application_payloads_saved=False,
        production_operations=False,thresholds_and_protocol_unchanged=True)
    binaries = dict(cpp=args.cpp,rust=args.rust)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,args=[
                '--disable-gpu','--disable-features=WebRtcHideLocalIpsWithMdns'])
            try:
                for turn in range(args.rounds):
                    order = ['cpp','rust'] if turn % 2 == 0 else ['rust','cpp']
                    for mode in ['delay','up-blackout']:
                        for backend in order:
                            row = dict(round=turn+1,mode=mode,backend=backend,passed=False)
                            report['cases'].append(row)
                            relay = ImpairedRelay(mode)
                            server = page = None
                            try:
                                server = Server(binaries[backend],{
                                    'SPEEDTEST_RUST_RTC_EXPERIMENTAL':'1',
                                    'SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST':'1',
                                    'SPEEDTEST_IO_THREADS':'2','TOKIO_WORKER_THREADS':'2',
                                    'RATE_MAX':'1000'},allow_self=True)
                                page = browser.new_page()
                                page.expose_function('__proxyTarget',relay.target)
                                page.goto(f'http://127.0.0.1:{server.port}/?testMode=webrtc-contract')
                                page.evaluate(WIRE_CLIENT)
                                peer = page.evaluate('ports=>window.__rtcWire.connect(ports)',relay.ports)
                                relay.activate()
                                started = time.monotonic()
                                options = dict(rate=20,durationMs=2000,fragmented=True) if mode=='delay' else dict(rate=100,durationMs=20000,fragmented=True)
                                value = page.evaluate('([id,options])=>window.__rtcWire.measure(id,options)',[peer,options])
                                elapsed = time.monotonic()-started
                                result = value['result']
                                row.update(elapsed_seconds=elapsed,value=value)
                                assert not value['errors'],value['errors']
                                if mode=='delay':
                                    assert result['complete'] and not result['invalidReasons'],result
                                    assert result['planned']==result['actualSent']==result['received']==result['uploadReceived']==40
                                    assert len(value['upAckSeqs'])==len(value['downSeqs'])==40
                                else:
                                    assert elapsed<=25, f'result missed original25s window: {elapsed:.3f}s'
                                    assert not result['complete'], 'outage must not claim a complete scheduler'
                                    assert result['invalidReasons']==['DOWNLOAD_SEND_FAILED','DOWNLOAD_SCHEDULER_INCOMPLETE'],result['invalidReasons']
                                    assert result['planned']==2000 and 0<result['actualSent']<2000
                                    assert 0<result['received']<2000 and 0<result['uploadReceived']<2000
                                row['passed'] = True
                                print(f'{turn+1} {mode} {backend}: PASS result={elapsed:.3f}s complete={result["complete"]}',flush=True)
                            except Exception as error:
                                row.update(error_type=type(error).__name__,error=str(error))
                                print(f'{turn+1} {mode} {backend}: FAIL retained {error}',flush=True)
                            finally:
                                if page:
                                    try:page.evaluate('()=>window.__rtcWire.closeAll()')
                                    except Exception:pass
                                    try:page.close()
                                    except Exception as error:row.update(passed=False,page_cleanup_error=str(error))
                                try:relay.close()
                                except Exception as error:row.update(passed=False,relay_cleanup_error=str(error))
                                row['impairment'] = relay.observation()
                                if row['impairment']['unexpected_failure']:
                                    row['passed'] = False
                                drops = row['impairment']['intended_drops']
                                if mode=='up-blackout' and (not drops or not all(
                                        d['direction']=='up' and 6<=d['seconds']<20 for d in drops)):
                                    row.update(passed=False,drop_policy_error=True)
                                if server:
                                    try:server.close()
                                    except Exception as error:row.update(passed=False,server_cleanup_error=str(error))
                                    log=args.output.with_name(args.output.stem+f'-{turn+1}-{mode}-{backend}.log')
                                    log.write_text(server.output)
                                args.output.write_text(json.dumps(report,indent=2)+'\n')
            finally:
                browser.close()
        report['passed'] = len(report['cases'])==args.rounds*4 and all(row['passed'] for row in report['cases'])
    finally:
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
