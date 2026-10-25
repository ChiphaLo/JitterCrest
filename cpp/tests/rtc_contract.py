"""Shared real-browser native RTC contracts. No Node server or Rust wrapper.

The unchanged frontend's existing loopback-only contract hooks are exercised,
followed by a single identical browser wire client against each backend.
Raw reports stay outside the published checkout.
"""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from http_contract import Server
from rtc_browser_client import WIRE_CLIENT
from rtc_udp_proxy import UdpProxy


def loss_cases(page):
    proxy = UdpProxy()
    page.expose_function("__proxyTarget", proxy.target)
    handle = None
    try:
        handle = page.evaluate("relay => window.__rtcWire.connect(relay)", proxy.ports)
        page.evaluate("id => window.__rtcWire.probes(id,[0])", handle)
        page.wait_for_function("id => window.__rtcWire.acks(id).includes(0)", arg=handle)
        page.wait_for_timeout(250)
        proxy.arm("up")
        page.evaluate("id => window.__rtcWire.probes(id,[7])", handle)
        page.wait_for_timeout(250)
        page.evaluate("id => window.__rtcWire.probes(id,[8,9,10,11,12,13,14,15])", handle)
        page.wait_for_timeout(3000)
        up = page.evaluate("id => window.__rtcWire.acks(id)", handle)
        assert 7 not in up and all(n in up for n in range(8, 16)), (up, proxy.drops)
        assert len(proxy.drops) == 1 and proxy.drops[0]["direction"] == "up", proxy.drops
        proxy.arm("down")
        page.evaluate("id => window.__rtcWire.probes(id,[20])", handle)
        page.wait_for_timeout(250)
        page.evaluate("id => window.__rtcWire.probes(id,[21,22,23,24,25,26,27,28])", handle)
        page.wait_for_timeout(3000)
        down = page.evaluate("id => window.__rtcWire.acks(id)", handle)
        assert 20 not in down and all(n in down for n in range(21, 29)), (down, proxy.drops)
        assert len(proxy.drops) == 2 and proxy.drops[1]["direction"] == "down", proxy.drops
        return dict(one_upload_datagram_dropped=True, one_native_download_datagram_dropped=True,
                    later_same_channel_sequences_delivered=True, zero_retry_missing_sequences_preserved=True)
    finally:
        if handle:
            page.evaluate("id => window.__rtcWire.close(id)", handle)
        proxy.close()


def mapped_answer(binary, browser):
    # Serialization-only NAT test. Never apply this answer to a browser peer,
    # so no ICE traffic is sent to the public fixture address.
    server = Server(binary, {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1",
                            "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1",
                            "SPEEDTEST_WEBRTC_UDP4": "1.1.1.1"}, allow_self=True)
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{server.port}/?testMode=webrtc-contract")
        value = page.evaluate(r"""async () => {
          const pc=new RTCPeerConnection({iceServers:[]});pc.createDataChannel('control');
          const attempt='mapping-'+crypto.randomUUID(),headers={'Content-Type':'application/json','X-Speedtest-Attempt-Id':attempt};
          try {
            await pc.setLocalDescription(await pc.createOffer());
            const end=performance.now()+5000;while(pc.iceGatheringState!=='complete'){if(performance.now()>end)throw Error('gather');await new Promise(r=>setTimeout(r,5));}
            const r=await fetch('/webrtc/offer',{method:'POST',headers,body:JSON.stringify({attemptId:attempt,type:'offer',sdp:pc.localDescription.sdp})});
            const answer=await r.json();if(r.status!==200)throw Error(JSON.stringify(answer));
            const candidates=answer.sdp.split(/\r?\n/).filter(x=>x.startsWith('a=candidate:')).map(x=>x.split(/\s+/)[4]);
            const cancel=await fetch('/webrtc/offer',{method:'POST',headers,body:JSON.stringify({attemptId:attempt,action:'cancel'})});
            return {candidates,cancel:cancel.status};
          }finally{pc.close();}
        }""")
        assert value["candidates"] and set(value["candidates"]) == {"1.1.1.1"}, value
        assert value["cancel"] == 204
        return dict(mapped_public_candidate=True, no_answer_applied_to_browser=True)
    finally:
        page.close()
        server.close()


def wire_cases(page):
    page.evaluate(WIRE_CLIENT)
    results = {}

    def measure(name, options, invalid=None):
        started = time.monotonic()
        print(f"  RUN {name}", flush=True)
        handle = page.evaluate("() => window.__rtcWire.connect()")
        try:
            value = page.evaluate("([id,o]) => window.__rtcWire.measure(id,o)", [handle, options])
            result = value["result"]
            assert not value["errors"], value["errors"]
            assert result["invalidReasons"] == (invalid or []), result
            assert result["complete"] == result["final"] == (invalid is None), result
            assert result["actualSent"] == result["sent"] == len(result["sentSeqs"])
            assert result["received"] == result["acked"] == len(result["samples"])
            assert result["uploadReceived"] == len(result["uploadReceivedSeqs"])
            assert result["probeTransport"] == {"ordered": False, "maxRetransmits": 0, "zeroRetransmitHonored": True}
            if invalid is None:
                assert result["scheduled"]+result["skipped"] == result["planned"]
                assert result["actualSent"] >= .99*result["planned"], result
                assert value["upAckSeqs"] == result["uploadReceivedSeqs"] == list(range(result["planned"])), value
                assert value["downSeqs"] == result["sentSeqs"], value
                assert value["sampleSeqs"] == [s["seq"] for s in result["samples"]], value
                assert result["finalizedAtMs"]-result["uploadCompleteAtMs"] >= 299
            results[name] = dict(passed=True, reasons=result["invalidReasons"], planned=result["planned"], complete=result["complete"])
            if name == "standard-20pps-2s":
                assert result["planned"] == result["actualSent"] == 40
                assert value["resultFragments"] > 1
            if name == "reorder-duplicate-probes":
                assert value["reordered"] == [1], value
            print(f"  PASS {name} ({time.monotonic()-started:.2f}s)", flush=True)
            return value
        finally:
            page.evaluate("id => window.__rtcWire.close(id)", handle)

    measure("standard-20pps-2s", {})
    measure("stability-100pps-20s", dict(rate=100, durationMs=20000, fragmented=True))
    measure("reorder-duplicate-probes", dict(durationMs=200, order=[0, 2, 1, 1, 3]))
    quick = dict(durationMs=100, earlyInvalid=True)
    for name, options, reasons in [
        ("invalid-planned-negative", dict(completion={"planned": -1}), ["UPLOAD_PLANNED_INVALID", "UPLOAD_PLANNED_MISMATCH", "UPLOAD_SCHEDULER_ACCOUNTING", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-scheduled-string", dict(completion={"scheduled": "2"}), ["UPLOAD_SCHEDULED_INVALID", "UPLOAD_SCHEDULER_ACCOUNTING", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-actual-string", dict(completion={"actualSent": "2"}), ["UPLOAD_ACTUALSENT_INVALID", "UPLOAD_SENT_SEQS_COUNT_MISMATCH", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-missing-scheduled", dict(omitField="scheduled"), ["UPLOAD_SCHEDULED_INVALID", "UPLOAD_SCHEDULER_ACCOUNTING", "UPLOAD_SCHEDULER_BOUNDS", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-planned", dict(completion={"planned": 3}), ["UPLOAD_PLANNED_MISMATCH", "UPLOAD_SCHEDULER_ACCOUNTING", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-skipped", dict(completion={"skipped": -1}), ["UPLOAD_SKIPPED_INVALID", "UPLOAD_SCHEDULER_ACCOUNTING", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-actual", dict(completion={"actualSent": 3}), ["UPLOAD_SCHEDULER_BOUNDS", "UPLOAD_SENT_SEQS_COUNT_MISMATCH", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-sequence-duplicate", dict(completion={"sentSeqs": [0, 0]}), ["UPLOAD_SENT_SEQS_INVALID", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-sequence-fraction", dict(completion={"sentSeqs": [0, 1.5]}), ["UPLOAD_SENT_SEQS_INVALID", "UPLOAD_COMPLETE_INVALID"]),
        ("invalid-identity", dict(completion={"sessionId": "wrong"}), ["UPLOAD_COMPLETE_ID_MISMATCH", "UPLOAD_COMPLETE_INVALID"]),
        ("duplicate-completion", dict(duplicate=True), ["UPLOAD_COMPLETE_DUPLICATE"]),
        ("missing-completion", dict(missing=True), ["UPLOAD_COMPLETE_MISSING"]),
    ]:
        measure(name, dict(quick, **options), reasons)
    handle = page.evaluate("() => window.__rtcWire.connect()")
    try:
        assert page.evaluate("id => window.__rtcWire.duplicateOffer(id)", handle) == 409
        assert page.evaluate("id => window.__rtcWire.cancel(id)", handle) == 204
        assert page.evaluate("id => window.__rtcWire.cancel(id)", handle) == 204
        assert page.evaluate("id => window.__rtcWire.cancelledOffer(id)", handle) == 409
        results["active-cancel-tombstone"] = dict(passed=True)
    finally:
        page.evaluate("id => window.__rtcWire.close(id)", handle)
    assert page.evaluate("() => window.__rtcWire.cancelUnknown()") == [204, 204, 409]
    results["unknown-cancel-idempotence"] = dict(passed=True)
    assert page.evaluate("() => window.__rtcWire.badOffer()") == [500, 409]
    results["invalid-sdp-cleanup"] = dict(passed=True)
    handle = page.evaluate("() => window.__rtcWire.connect()")
    try:
        page.evaluate("id => window.__rtcWire.ignoreNoise(id)", handle)
        value = page.evaluate("id => window.__rtcWire.measure(id,{durationMs:100})", handle)
        assert value["result"]["complete"] and value["result"]["uploadReceived"] == 2
        results["malformed-control-ignored"] = dict(passed=True)
    finally:
        page.evaluate("id => window.__rtcWire.close(id)", handle)
    handle = page.evaluate("() => window.__rtcWire.connect()")
    try:
        assert page.evaluate("id => window.__rtcWire.malformed(id)", handle) == 409
        results["malformed-frame-cleanup"] = dict(passed=True)
    finally:
        page.evaluate("id => window.__rtcWire.close(id)", handle)
    simultaneous = page.evaluate("async () => {const ids=await Promise.all([window.__rtcWire.connect(),window.__rtcWire.connect()]);try{return await Promise.all(ids.map(id=>window.__rtcWire.measure(id,{})));}finally{await Promise.all(ids.map(id=>window.__rtcWire.close(id)));}}")
    assert all(v["result"]["complete"] and v["result"]["uploadReceived"] == 40 for v in simultaneous)
    results["concurrent-native-peers"] = dict(passed=True, peers=2)
    admission = page.evaluate("""async () => {
      const ids=[];try{for(let i=0;i<4;i++)ids.push(await window.__rtcWire.connect());
        try{await window.__rtcWire.connect();return 'unexpected success';}catch(error){return String(error);}
      }finally{await window.__rtcWire.closeAll();}
    }""")
    assert '429' in admission and 'too many WebRTC peers from this IP' in admission, admission
    results["per-ip-admission-limit"] = dict(passed=True, admitted=4, rejected_status=429)
    return results


def run_backend(binary, browser, label, log_path=None):
    server = Server(binary, {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1",
                            "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1",
                            "RTC_OFFER_MAX_PER_IP": "200", "RATE_MAX": "1000"}, allow_self=True)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"http://127.0.0.1:{server.port}/?testMode=webrtc-contract")
        page.wait_for_function("() => Boolean(window.__speedtestTestHooks && typeof window.__speedtestTestHooks.runPairedMeasurementForTest === 'function')")
        measured = page.evaluate("() => window.__speedtestTestHooks.runPairedMeasurementForTest({planned:3})")
        assert measured["complete"] and not measured["invalidReasons"], measured
        assert measured["uploadReceivedSeqs"] == [0, 1, 2], measured
        assert measured["finalizedAtMs"]-measured["uploadCompleteAtMs"] >= 299, measured
        assert not errors, errors
        print(label+": unchanged frontend native RTC measurement passed", flush=True)
        # Exercise the real button -> latencyRun -> webRtcTests path as well as
        # the existing protocol hook. Contract mode only skips automatic startup.
        page.click("#restartBtn")
        page.wait_for_function("() => ['RTT complete','Error'].includes(document.querySelector('#phase').textContent)", timeout=15000)
        assert page.locator("#phase").inner_text() == "RTT complete", page.locator("#log").inner_text()
        assert page.locator("#rttIncompleteBadge").evaluate("node => node.hidden")
        for selector in ["#rttUpAvg", "#rttDownAvg", "#rttUpP95", "#rttDownP95"]:
            assert float(page.locator(selector).inner_text()) >= 0
        assert not errors, errors
        print(label+": real frontend Restart / 20pps RTT rendering passed", flush=True)
        cases = wire_cases(page)
        print(label+f": {len(cases)} native wire contracts passed", flush=True)
        loss = loss_cases(page)
        print(label+": actual encrypted UDP loss / zero-retry behavior passed", flush=True)
        return dict(frontend_complete=True, frontend_restart_complete=True,
                    upload_sequences=[0, 1, 2], result_grace=True, wire=cases, loss=loss)
    finally:
        page.close()
        try:
            server.close()
        finally:
            if log_path is not None:
                log_path.write_text(server.output)
        assert "rtc_native_cleanup_delayed" not in server.output


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cpp", type=Path, required=True)
    p.add_argument("--rust", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    results = {}
    with sync_playwright() as playwright:
        # These are data-channel/network contracts, not rendering benchmarks.
        # Keep the client off the host GPU on both sides of the comparison.
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu", "--disable-features=WebRtcHideLocalIpsWithMdns"])
        try:
            for name, binary in [("rust", args.rust), ("cpp", args.cpp)]:
                if binary:
                    print(f"START backend={name}", flush=True)
                    results[name] = run_backend(binary, browser, name, args.output.with_name(args.output.stem+"-"+name+".log"))
                    results[name]["nat_mapping"] = mapped_answer(binary, browser)
                    print(f"PASS backend={name} (including mapped candidate)", flush=True)
        except BaseException as error:
            args.output.write_text(json.dumps(dict(passed=False, results=results,
                                                  error_type=type(error).__name__,
                                                  error=str(error)), indent=2)+"\n")
            print(f"FAIL {type(error).__name__}: {error}", flush=True)
            raise
        finally:
            browser.close()
    if args.rust:
        assert results["rust"] == results["cpp"], results
    args.output.write_text(json.dumps(dict(passed=True, results=results, processes_exited=True), indent=2)+"\n")
    print("Native RTC shared-browser contracts passed", flush=True)
