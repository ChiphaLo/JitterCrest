"""Document the unchanged Rust/C++ post-result receipt boundary.

This is a real native-browser baseline observation, NOT valid measurement
acceptance. A delayed upload can be ACKed after the final receipt snapshot.
Do not weaken frontend accounting checks or extend result deadlines to pass it.
"""
import argparse
import json
from pathlib import Path

from http_contract import Server
from playwright.sync_api import sync_playwright
from rtc_browser_client import WIRE_CLIENT


LATE_RECEIPT = r"""async lateReceipt(id) {
 const p=peers.get(id),testId='late-'+crypto.randomUUID(),sessionId='late-'+crypto.randomUUID();
 const sessionHash=hash(sessionId),testHash=hash(testId),ids={testId,sessionId,sessionHash,testHash};
 const key=sessionHash+':'+testHash;
 const m={...ids,upAcks:new Map(),down:new Set(),samples:new Map(),ready:null,result:null};
 p.states.set(key,m);
 try {
  p.control.send(JSON.stringify({type:'downStart',...ids,rate:20,durationMs:100}));
  await until(()=>m.ready,4000,'measurementReady');
  await wait(120);
  sendCompletion(p,m,{type:'uploadComplete',...ids,planned:2,scheduled:2,skipped:0,actualSent:2,sentSeqs:[0,1]},false);
  await until(()=>m.result,6000,'downResult before delayed upload');
  const result=JSON.parse(JSON.stringify(m.result));
  for(const seq of [0,1])p.test.send(frame(1,sessionHash,testHash,seq,performance.now()));
  await until(()=>m.upAcks.size===2,4000,'delayed upload ACKs');
  return {result,lateAckSequences:[...m.upAcks.keys()].sort((a,b)=>a-b),errors:p.errors,
          validMeasurement:false};
 } finally {p.states.delete(key);}
},
"""
assert WIRE_CLIENT.count('async closeAll(){') == 1
CLIENT = WIRE_CLIENT.replace('async closeAll(){', LATE_RECEIPT+'async closeAll(){')


def run_backend(binary, browser, rounds, log):
    server = Server(binary, {'SPEEDTEST_RUST_RTC_EXPERIMENTAL': '1',
                            'SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST': '1',
                            'RTC_OFFER_MAX_PER_IP': '200', 'RATE_MAX': '1000'}, allow_self=True)
    page = browser.new_page()
    rows = []
    try:
        page.goto(f'http://127.0.0.1:{server.port}/?testMode=webrtc-contract')
        page.evaluate(CLIENT)
        for index in range(rounds):
            peer = page.evaluate('()=>window.__rtcWire.connect()')
            try:
                value = page.evaluate('id=>window.__rtcWire.lateReceipt(id)', peer)
                r = value['result']
                assert not value['errors'], value['errors']
                assert r['complete'] and r['final'] and not r['invalidReasons'], r
                assert r['planned'] == r['scheduled'] == r['actualSent'] == 2, r
                assert r['uploadReceived'] == 0 and r['uploadReceivedSeqs'] == [], r
                assert value['lateAckSequences'] == [0, 1], value
                assert r['finalizedAtMs']-r['uploadCompleteAtMs'] >= 299, r
                rows.append(dict(round=index,observed=True,valid_measurement=False,
                                 snapshot_upload_received=0,late_upload_acks=2,
                                 receipt_accounting_contradiction=True))
            finally:
                page.evaluate('id=>window.__rtcWire.close(id)', peer)
        return rows
    finally:
        page.close()
        try:
            server.close()
        finally:
            log.write_text(server.output)
        assert 'rtc_native_cleanup_delayed' not in server.output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpp', type=Path, required=True)
    p.add_argument('--rust', type=Path, required=True)
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert 1 <= a.rounds <= 10
    report = dict(passed=False,baseline_observation_only=True,measurement_acceptance=False,results={})
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,args=['--disable-gpu','--disable-features=WebRtcHideLocalIpsWithMdns'])
            try:
                for name,binary in [('rust',a.rust),('cpp',a.cpp)]:
                    report['results'][name] = run_backend(binary,browser,a.rounds,a.output.with_name(a.output.stem+'-'+name+'.log'))
                    print(name+': post-result snapshot/late-ACK baseline reproduced '+str(a.rounds)+' rounds',flush=True)
            finally:
                browser.close()
        assert report['results']['rust'] == report['results']['cpp']
        report['passed'] = True
    except BaseException as e:
        report.update(error_type=type(e).__name__,error=str(e))
        raise
    finally:
        a.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
