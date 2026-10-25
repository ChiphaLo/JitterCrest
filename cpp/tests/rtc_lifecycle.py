"""Shared native RTC process shutdown contracts while native work is still live."""
import argparse
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright
from http_contract import Server
from rtc_browser_client import WIRE_CLIENT


def observe(binary, browser, label, output):
    rows = {}
    for scenario in ["open-peer", "active-measurement", "offer-shutdown-race"]:
        server = Server(binary, {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1",
                                "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1"}, allow_self=True)
        page = browser.new_page()
        stopped = False
        try:
            page.goto(f"http://127.0.0.1:{server.port}/?testMode=webrtc-contract")
            if scenario == "offer-shutdown-race":
                page.evaluate("""async () => {
                  const pc=window.lifecyclePeer=new RTCPeerConnection({iceServers:[]});
                  pc.createDataChannel('control');
                  await pc.setLocalDescription(await pc.createOffer());
                  const end=performance.now()+5000;
                  while(pc.iceGatheringState!=='complete'){
                    if(performance.now()>end)throw Error('gather timeout');
                    await new Promise(r=>setTimeout(r,5));
                  }
                  const id=crypto.randomUUID();
                  window.lifecycleOffer=fetch('/webrtc/offer',{method:'POST',
                    headers:{'Content-Type':'application/json','X-Speedtest-Attempt-Id':id},
                    body:JSON.stringify({type:'offer',attemptId:id,sdp:pc.localDescription.sdp})
                  }).then(r=>r.status).catch(()=>0);
                }""")
            else:
                page.evaluate(WIRE_CLIENT)
                handle = page.evaluate("() => window.__rtcWire.connect()")
                if scenario == "active-measurement":
                    page.evaluate("id => {window.lifecycleState='pending';window.lifecycleMeasurement=window.__rtcWire.measure(id,{rate:100,durationMs:20000}).then(()=>window.lifecycleState='complete',()=>window.lifecycleState='failed');}", handle)
                    # An ACK proves that measurementReady was received and
                    # actual native measurement traffic is in flight.
                    page.wait_for_function("id => window.__rtcWire.acks(id).length > 0", arg=handle, timeout=4000)
                    assert page.evaluate("() => window.lifecycleState") == "pending"
            # Intentionally stop BEFORE closing the browser or its peer. This
            # tests backend-owned timers/callbacks, not a clean client teardown.
            started = time.monotonic()
            try:
                server.close()
            finally:
                stopped = True
                output.with_name(output.stem+f"-{label}-{scenario}.log").write_text(server.output)
            elapsed = time.monotonic()-started
            assert elapsed < 8, elapsed
            assert all(event not in server.output for event in ["executor_error", "rtc_native_cleanup_delayed",
                       "rtc_native_cleanup_timeout", "rtc_shutdown_failed"]), server.output
            rows[scenario] = dict(passed=True, exit_code=server.process.returncode,
                                  shutdown_seconds=elapsed)
            print(f"PASS {label} {scenario} shutdown ({elapsed:.3f}s)", flush=True)
        finally:
            page.close()
            if not stopped:
                try:
                    server.close()
                finally:
                    output.with_name(output.stem+f"-{label}-{scenario}.log").write_text(server.output)
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cpp", type=Path, required=True)
    p.add_argument("--rust", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu",
            "--disable-features=WebRtcHideLocalIpsWithMdns"])
        try:
            for label, binary in [("rust", args.rust), ("cpp", args.cpp)]:
                results[label] = observe(binary, browser, label, args.output)
            assert results["rust"].keys() == results["cpp"].keys()
        except BaseException as error:
            args.output.write_text(json.dumps(dict(passed=False, results=results, error=str(error)), indent=2)+"\n")
            raise
        finally:
            browser.close()
    args.output.write_text(json.dumps(dict(passed=True, results=results, processes_exited=True), indent=2)+"\n")
