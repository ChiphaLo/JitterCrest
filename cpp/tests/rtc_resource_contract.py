"""Shared actual-native WebRTC admission, expiry, FIFO and channel-bound tests.

This uses real browser ICE/DTLS/SCTP and unmodified server binaries. Thirty-second
tombstone expiry is observed in real time; no fake clock or relaxed probe gate.
Raw timing/traffic/process evidence stays private, never in the source export.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
from http_contract import Server, request
from rtc_resource_client import RESOURCE_CLIENT


def post(server, attempt, action=None, ip=1):
    body = {"attemptId": attempt, "action": "cancel"} if action else {
        "attemptId": attempt, "type": "offer", "sdp": "v=0\r\n"}
    return request(server.port, "POST", "/webrtc/offer", json.dumps(body).encode(), {
        "Content-Type": "application/json", "X-Speedtest-Attempt-Id": attempt,
        "X-Real-IP": f"198.19.0.{ip}"})


class Case:
    def __init__(self, binary, browser, log_path, env):
        self.server = Server(binary, {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1",
            "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1", "TRUST_PROXY_HEADERS": "1",
            "TRUSTED_PROXY_IPS": "127.0.0.1", "RATE_MAX": "1000", "RTC_OFFER_MAX_PER_IP": "200",
            "RTC_CANCEL_MAX_PER_IP": "1000", "SPEEDTEST_IO_THREADS": "2", "TOKIO_WORKER_THREADS": "2",
            **env}, allow_self=True)
        self.browser, self.log_path = browser, log_path
        self.pages, self.expected_log = [], []

    def page(self, ip=1):
        page = self.browser.new_page(extra_http_headers={"X-Real-IP": f"198.19.0.{ip}"})
        self.pages.append(page)
        page.goto(f"http://127.0.0.1:{self.server.port}/?testMode=webrtc-contract")
        page.evaluate(RESOURCE_CLIENT)
        return page

    def close(self):
        try:
            for page in self.pages:
                try:
                    page.evaluate("() => window.__rtcResources.closeAll()")
                finally:
                    page.close()
        finally:
            try:
                self.server.close()
            finally:
                self.log_path.write_text(self.server.output)
        for marker in self.expected_log:
            assert marker in self.server.output, (marker, self.server.output[-3000:])
        assert not any(marker in self.server.output for marker in [
            "rtc_native_cleanup_delayed", "rtc_native_cleanup_timeout", '"event":"executor_error"'])


@contextmanager
def scenario(binary, browser, log_path, **env):
    case = Case(binary, browser, log_path, env)
    try:
        yield case
    finally:
        case.close()


def open_peer(page, attempt=None):
    value = page.evaluate("attempt => window.__rtcResources.open(attempt)", attempt)
    assert value["status"] == 200, value
    return value


def close_wait(page, peer, ms=5000):
    return page.evaluate("([id,ms]) => window.__rtcResources.closed(id,ms)", [peer["id"], ms])


def global_admission(c):
    pages = [c.page(ip) for ip in range(1, 5)]
    admitted = [open_peer(page) for page in pages[:3]]
    for page, peer in zip(pages, admitted):
        assert page.evaluate("id => window.__rtcResources.probe(id)", peer["id"])
    rejected = pages[3].evaluate("() => window.__rtcResources.open()")
    assert rejected["status"] == 503 and rejected["body"]["error"] == "too many active WebRTC peers", rejected
    assert pages[0].evaluate("id => window.__rtcResources.cancel(id)", admitted[0]["id"])["status"] == 204
    close_wait(pages[0], admitted[0])
    replacement = open_peer(pages[3])
    assert pages[3].evaluate("id => window.__rtcResources.probe(id)", replacement["id"])
    assert pages[0].evaluate("id => window.__rtcResources.replay(id)", admitted[0]["id"])["status"] == 409
    return dict(admitted=3, next_status=503, independent_identities=4, active_cancel_released_capacity=True)


def session_expiry(c):
    page = c.page()
    peers = [open_peer(page) for _ in range(3)]
    assert page.evaluate("() => window.__rtcResources.open()")["status"] == 503
    for peer in peers:
        assert page.evaluate("id => window.__rtcResources.probe(id)", peer["id"])
    elapsed = [close_wait(page, peer, 15000)["elapsedMs"] for peer in peers]
    assert all(9500 <= value <= 15000 for value in elapsed), elapsed
    assert all(page.evaluate("id => window.__rtcResources.replay(id)", peer["id"])["status"] == 409 for peer in peers)
    replacement = open_peer(page)
    assert page.evaluate("id => window.__rtcResources.probe(id)", replacement["id"])
    return dict(ttl_ms=10000, all_three_expired=True, replay_tombstoned=True, capacity_recovered=True), dict(close_ms=elapsed)


def cancel_rate(c):
    page = c.page()
    active = open_peer(page)
    # A foreign identity may tombstone its own key, not close the owner's peer.
    assert post(c.server, active["attempt"], "cancel", ip=2)["status"] == 204
    assert page.evaluate("id => window.__rtcResources.probe(id)", active["id"])
    started = time.monotonic()
    for index in range(10):
        assert post(c.server, f"cancel-{index}", "cancel")["status"] == 204
    for _ in range(3):
        assert post(c.server, "cancel-0", "cancel")["status"] == 204
    rejected = post(c.server, "cancel-overflow", "cancel")
    assert rejected["status"] == 429 and rejected["json"]["error"] == "too many WebRTC cancels", rejected
    assert page.evaluate("id => window.__rtcResources.cancel(id)", active["id"])["status"] == 204
    close_wait(page, active)
    replacement = open_peer(page)
    assert page.evaluate("id => window.__rtcResources.probe(id)", replacement["id"])
    time.sleep(max(0, started + 10.1 - time.monotonic()))
    assert post(c.server, "cancel-after-window", "cancel")["status"] == 204
    return dict(unknown_cancel_limit=10, next_status=429, duplicate_exempt=True,
                foreign_cancel_isolated=True, active_cancel_exempt=True, window_recovered=True)


def offer_rate(c):
    assert post(c.server, "offer-cached", "cancel")["status"] == 204
    started = time.monotonic()
    assert post(c.server, "offer-cached")["status"] == 409
    assert post(c.server, "offer-failure")["status"] == 500
    assert post(c.server, "offer-failure")["status"] == 409
    overflow = post(c.server, "offer-cached")
    assert overflow["status"] == 429 and overflow["json"]["error"] == "too many WebRTC offers", overflow
    assert post(c.server, "offer-failure", ip=2)["status"] == 500
    time.sleep(max(0, started + 10.1 - time.monotonic()))
    assert post(c.server, "offer-cached")["status"] == 409
    assert post(c.server, "offer-new")["status"] == 500
    return dict(limit=3, failed_and_conflicting_offers_counted=True, rate_precedes_tombstone=True,
                independent_identity=True, window_recovered=True)


def tombstone_fifo(c):
    for index in range(128):
        assert post(c.server, f"fifo-{index}", "cancel")["status"] == 204
    for _ in range(3):
        assert post(c.server, "fifo-0", "cancel")["status"] == 204
    assert post(c.server, "fifo-128", "cancel")["status"] == 204
    assert post(c.server, "fifo-1")["status"] == 409
    # A forgotten key reaches native SDP validation, then gets a new tombstone.
    assert post(c.server, "fifo-0")["status"] == 500
    assert post(c.server, "fifo-1")["status"] == 500
    assert post(c.server, "fifo-128")["status"] == 409
    return dict(capacity=128, oldest_evicted=True, duplicate_does_not_reorder=True,
                next_oldest_evicted_after_reinsert=True, newest_retained=True)


def tombstone_expiry(c):
    started = time.monotonic()
    assert post(c.server, "age", "cancel")["status"] == 204
    time.sleep(max(0, started + 20 - time.monotonic()))
    assert post(c.server, "age", "cancel")["status"] == 204
    time.sleep(max(0, started + 29 - time.monotonic()))
    assert post(c.server, "age")["status"] == 409
    time.sleep(max(0, started + 30.2 - time.monotonic()))
    assert post(c.server, "age")["status"] == 500
    return dict(ttl_ms=30000, retained_at_29s=True, expired_after_30s=True,
                duplicate_at_20s_did_not_refresh=True)


def generation_reuse(c):
    page = c.page()
    for round_ in range(3):
        old = open_peer(page, "generation-reused")
        assert page.evaluate("id => window.__rtcResources.cancel(id)", old["id"])["status"] == 204
        for index in range(128):
            assert post(c.server, f"generation-{round_}-{index}", "cancel")["status"] == 204
        replacement = open_peer(page, "generation-reused")
        assert page.evaluate("id => window.__rtcResources.probe(id,0)", replacement["id"])
        page.wait_for_timeout(300)
        assert page.evaluate("id => window.__rtcResources.probe(id,1)", replacement["id"])
        assert page.evaluate("id => window.__rtcResources.cancel(id)", replacement["id"])["status"] == 204
        # Evict this generation's tombstone before the next round reuses it.
        for index in range(128):
            assert post(c.server, f"between-{round_}-{index}", "cancel")["status"] == 204
    return dict(rounds=3, same_key_reaccepted_after_fifo_eviction=True,
                replacement_kept_acking_with_old_browser_peers_retained=True,
                deterministic_native_callback_pause_claimed=False)


def channel_bound(c, kind):
    page = c.page()
    peer = open_peer(page)
    id_ = peer["id"]
    observations = {}
    if kind == "control-count":
        page.evaluate("id => window.__rtcResources.controls(id,4)", id_)
        assert page.evaluate("id => window.__rtcResources.probe(id)", id_)
        page.evaluate("id => window.__rtcResources.controls(id,1)", id_)
        c.expected_log.append("control-message-limit")
    elif kind == "control-size":
        page.evaluate("id => window.__rtcResources.controls(id,1,257)", id_)
        c.expected_log.append("control-message-limit")
    elif kind == "inbound-bytes":
        page.evaluate("id => window.__rtcResources.controls(id,63,4096)", id_)
        assert page.evaluate("id => window.__rtcResources.probe(id)", id_)
        page.evaluate("id => window.__rtcResources.controls(id,1,4096)", id_)
        c.expected_log.append("inbound-byte-limit")
    elif kind == "test-count":
        observations = page.evaluate("id => window.__rtcResources.testFlood(id)", id_)
        assert observations["sent"] > 24000, observations
        c.expected_log.append("test-message-limit")
    else:
        first = page.evaluate("id => window.__rtcResources.downStart(id,'first')", id_)
        assert page.evaluate("([id,t]) => window.__rtcResources.ready(id,t)", [id_, first])
        page.evaluate("id => window.__rtcResources.downStart(id,'second')", id_)
        c.expected_log.append("down-start-limit")
    assert close_wait(page, peer)["closed"]
    assert page.evaluate("id => window.__rtcResources.replay(id)", id_)["status"] == 409
    replacement = open_peer(page)
    assert page.evaluate("id => window.__rtcResources.probe(id)", replacement["id"])
    return dict(violation=kind, peer_closed=True, replay_tombstoned=True, replacement_usable=True), observations


def measurement_bound(c):
    page = c.page()
    peer = open_peer(page)
    token = page.evaluate("id => window.__rtcResources.downStart(id,'bounded',5000)", peer["id"])
    result = page.evaluate("([id,t]) => window.__rtcResources.result(id,t)", [peer["id"], token])
    assert result["planned"] == 20 and result["scheduled"] + result["skipped"] == 20, result
    assert result["actualSent"] <= 20 and not result["complete"] and not result["final"], result
    assert "UPLOAD_COMPLETE_MISSING" in result["invalidReasons"], result
    return dict(requested_ms=5000, maximum_ms=1000, planned=20, missing_upload_not_complete=True), result


def run(binary, browser, output, label):
    results, observations = {}, {}
    cases = [
        ("global-admission", global_admission, {"MAX_RTC_PEERS": "3", "MAX_RTC_PEERS_PER_IP": "3"}),
        ("session-expiry", session_expiry, {"MAX_RTC_PEERS": "3", "MAX_RTC_PEERS_PER_IP": "3", "MAX_RTC_SESSION_MS": "10000"}),
        ("cancel-rate", cancel_rate, {"RTC_CANCEL_MAX_PER_IP": "10"}),
        ("offer-rate", offer_rate, {"RTC_OFFER_MAX_PER_IP": "3"}),
        ("tombstone-fifo", tombstone_fifo, {"MAX_RTC_ATTEMPT_TOMBSTONES": "128"}),
        ("tombstone-expiry", tombstone_expiry, {}),
        ("generation-reuse", generation_reuse, {"MAX_RTC_ATTEMPT_TOMBSTONES": "128"}),
        ("control-count", lambda c: channel_bound(c, "control-count"), {"MAX_RTC_CONTROL_MESSAGES_PER_PEER": "4"}),
        ("control-size", lambda c: channel_bound(c, "control-size"), {"MAX_RTC_CONTROL_MESSAGE_BYTES": "256"}),
        ("inbound-bytes", lambda c: channel_bound(c, "inbound-bytes"), {"MAX_RTC_INBOUND_BYTES_PER_PEER": "262144", "MAX_RTC_CONTROL_MESSAGE_BYTES": "4096", "MAX_RTC_CONTROL_MESSAGES_PER_PEER": "512"}),
        ("test-count", lambda c: channel_bound(c, "test-count"), {"MAX_RTC_TEST_MESSAGES_PER_PEER": "24000", "MAX_RTC_INBOUND_BYTES_PER_PEER": "16777216", "MAX_RTC_SESSION_MS": "300000"}),
        ("down-start-count", lambda c: channel_bound(c, "down-start-count"), {"MAX_DOWN_TESTS_PER_SOCKET": "1"}),
        ("measurement-bound", measurement_bound, {"MAX_RTC_MEASUREMENT_MS": "1000"}),
    ]
    for name, function, env in cases:
        print(f"RUN native resource {label} {name}", flush=True)
        with scenario(binary, browser, output.with_name(output.stem + "-" + label + "-" + name + ".log"), **env) as case:
            value = function(case)
            if isinstance(value, tuple):
                results[name], observations[name] = value
            else:
                results[name] = value
        print(f"PASS native resource {label} {name}", flush=True)
    return dict(contracts=results, observations=observations)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ["cpp", "rust", "output"]:
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    protected = [Path(__file__), Path(__file__).with_name("rtc_resource_client.py"),
                 Path(__file__).with_name("http_contract.py"), args.cpp, args.rust]
    identities = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    results = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-gpu", "--disable-features=WebRtcHideLocalIpsWithMdns"])
            try:
                for label, binary in [("rust", args.rust), ("cpp", args.cpp)]:
                    results[label] = run(binary, browser, args.output, label)
            finally:
                browser.close()
        assert results["rust"]["contracts"] == results["cpp"]["contracts"], results
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in identities.items())
    except BaseException as error:
        args.output.write_text(json.dumps(dict(passed=False, results=results, error_type=type(error).__name__, error=str(error)), indent=2) + "\n")
        raise
    args.output.write_text(json.dumps(dict(passed=True, contracts_per_backend=13, results=results,
        processes_exited=True, unchanged_sources_and_executables=True, sha256=identities), indent=2) + "\n")
    print("PASS 13 shared native RTC resource/expiry contracts", flush=True)
