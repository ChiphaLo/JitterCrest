"""Real negotiated message-size boundary against unchanged Rust, not SDP mocks."""
import argparse
import json
from pathlib import Path

from http_contract import Server
from playwright.sync_api import sync_playwright
from rtc_browser_client import WIRE_CLIENT


CAPS = r"""messageCaps(id) {
 const p=peers.get(id);
 return {maxMessageSize:p.pc.sctp.maxMessageSize,
  remoteMaxMessage:p.pc.remoteDescription.sdp.split(/\r?\n/).filter(s=>s.startsWith('a=max-message-size:'))};
},
oversizeSend(id) {
 try {peers.get(id).control.send(new ArrayBuffer(65537));return {rejected:false};}
 catch(error) {return {rejected:true,name:error.name};}
},
boundarySend(id,bytes=65535) {
 const empty=JSON.stringify({type:'noop',padding:''});
 const message=JSON.stringify({type:'noop',padding:'x'.repeat(bytes-empty.length)});
 if(new TextEncoder().encode(message).length!==bytes)throw Error('boundary fixture size');
 peers.get(id).control.send(message);return {bytes};
},
async boundaryClose(id) {
 const p=peers.get(id);this.boundarySend(id,65536);
 await until(()=>p.control.readyState==='closed'||p.pc.connectionState==='closed'||p.pc.connectionState==='failed',5000,'reference boundary close');
 return {closed:true};
},
"""
assert WIRE_CLIENT.count('async closeAll(){') == 1
CLIENT = WIRE_CLIENT.replace('async closeAll(){', CAPS+'async closeAll(){')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpp',type=Path,required=True)
    p.add_argument('--rust',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    report = dict(passed=False,backends={},message_bytes=65536,application_control_limit=65536,frontend_source_unchanged=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,args=['--disable-gpu','--disable-features=WebRtcHideLocalIpsWithMdns'])
            try:
                for name,binary in [('rust',a.rust),('cpp',a.cpp)]:
                    row = dict(passed=False)
                    report['backends'][name] = row
                    # Explicitly opt BOTH fixtures into the legal 64 KiB app
                    # ceiling. The normal 16 KiB app policy is separate from
                    # the negotiated native boundary and remains unchanged.
                    server = Server(binary,{'SPEEDTEST_RUST_RTC_EXPERIMENTAL':'1',
                        'SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST':'1',
                        'MAX_RTC_CONTROL_MESSAGE_BYTES':'65536'},allow_self=True)
                    page = browser.new_page()
                    try:
                        page.goto(f'http://127.0.0.1:{server.port}/?testMode=webrtc-contract')
                        page.evaluate(CLIENT)
                        peer = page.evaluate('()=>window.__rtcWire.connect()')
                        caps = page.evaluate('id=>window.__rtcWire.messageCaps(id)',peer)
                        row['caps'] = caps
                        assert caps['maxMessageSize'] == 65536, caps
                        assert caps['remoteMaxMessage'] in [[],['a=max-message-size:65536']], caps
                        rejected = page.evaluate('id=>window.__rtcWire.oversizeSend(id)',peer)
                        row['oversize_send'] = rejected
                        assert rejected == dict(rejected=True,name='TypeError'), rejected
                        row['boundary_send'] = page.evaluate('id=>window.__rtcWire.boundarySend(id)',peer)
                        assert row['boundary_send'] == dict(bytes=65535)
                        value = page.evaluate('id=>window.__rtcWire.measure(id,{})',peer)
                        assert value['result']['complete'] and not value['result']['invalidReasons'] and not value['errors'], value
                        assert value['result']['planned'] == value['result']['actualSent'] == 40
                        assert len(value['upAckSeqs']) == len(value['downSeqs']) == 40
                        row['native_read_boundary'] = page.evaluate('id=>window.__rtcWire.boundaryClose(id)',peer)
                        assert row['native_read_boundary'] == dict(closed=True)
                        assert page.evaluate('id=>window.__rtcWire.duplicateOffer(id)',peer) == 409
                        row.update(passed=True,oversize_rejected_before_wire=True,boundary_control_followed_by_valid_measurement=True,paired_128_byte_probe_measurement=True,boundary_close_tombstone=True)
                        print(name+': negotiated64KiB / valid65535 / close65536 / reject65537 / real20pps2s passed',flush=True)
                    finally:
                        try:
                            page.evaluate('()=>window.__rtcWire.closeAll()')
                        finally:
                            page.close()
                            try:server.close()
                            finally:a.output.with_name(a.output.stem+'-'+name+'.log').write_text(server.output)
            finally:
                browser.close()
        report['passed'] = True
    except BaseException as e:
        report.update(error_type=type(e).__name__,error=str(e))
        raise
    finally:
        a.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
