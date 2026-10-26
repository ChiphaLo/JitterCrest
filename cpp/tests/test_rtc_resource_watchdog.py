"""Harness-only controls: a stalled channel must fail, not hang or pass.

The fake peer is used only for watchdog behavior; native resource assertions
continue to require real browser ICE/DTLS/SCTP and more than 24000 sends.
"""
import unittest

from playwright.sync_api import sync_playwright
from rtc_resource_client import RESOURCE_CLIENT


FAKE_PEER = r"""stalled => {
  let clock = 0;
  if (stalled) Object.defineProperty(performance, 'now', {value: () => clock += 1000});
  window.fetch = async () => ({status: 200, json: async () => ({type:'answer',sdp:''})});
  window.RTCPeerConnection = class {
    constructor(){ this.iceGatheringState='complete'; this.channels=[]; }
    createDataChannel(label){
      const c = new EventTarget(); c.readyState='open'; c.bufferedAmount=0; c.sent=0;
      c.send=()=>{ c.sent++; if(stalled) c.bufferedAmount=262145;
        else if(label==='test' && c.sent===24001) this.close(); };
      this.channels.push(c); return c;
    }
    async createOffer(){ return {sdp:''}; }
    async setLocalDescription(v){ this.localDescription=v; }
    async setRemoteDescription(){}
    close(){ for(const c of this.channels){c.readyState='closed';c.dispatchEvent(new Event('close'));} }
  };
}"""


class WatchdogTest(unittest.TestCase):
    def test_stalled_and_healthy_controls(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                for stalled in [True, False]:
                    with self.subTest(stalled=stalled):
                        page = browser.new_page()
                        try:
                            page.evaluate(FAKE_PEER, stalled)
                            page.evaluate(RESOURCE_CLIENT)
                            peer = page.evaluate("() => window.__rtcResources.open('watchdog-test')")
                            outcome = page.evaluate(r"""async id => {
                              try { return {result:await window.__rtcResources.testFlood(id)}; }
                              catch(error) { return {error:String(error)}; }
                            }""", peer['id'])
                            if stalled:
                                self.assertIn('resource flood watchdog', outcome['error'])
                                self.assertNotIn('result', outcome)
                            else:
                                self.assertEqual(outcome['result']['sent'], 24001)
                                self.assertNotIn('error', outcome)
                        finally:
                            page.close()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
