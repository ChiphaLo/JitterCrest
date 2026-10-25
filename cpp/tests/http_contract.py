"""Actual-process Rust/C++ HTTP contract runner. Python standard library only.

Both binaries must be local Linux executables. Native WebRTC remains a separate
required suite; this suite deliberately sets RTC disabled on BOTH servers.
Evidence is written locally, not included in the published source checkout.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time


def allocate_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, binary, env, allow_self=False):
        self.port = allocate_port()
        self.log = tempfile.TemporaryFile()
        base = {k: v for k, v in os.environ.items() if not k.startswith((
            "SPEEDTEST_", "MAX_", "RATE_", "SPEED_RATE_", "HTTP_", "RTC_", "DOWNLOAD_",
            "UPLOAD_", "BYTE_", "TRUST", "ALLOWED_", "PUBLIC_", "ENABLE_"))}
        base.update(PORT=str(self.port), SPEEDTEST_HOST="127.0.0.1",
                    SPEEDTEST_NODE_NAME="contract", TRUST_PROXY_HEADERS="0",
                    SPEEDTEST_RUST_RTC_EXPERIMENTAL="0", ALLOWED_ORIGINS="https://example.invalid",
                    SPEEDTEST_NODES='[{"base":"","id":"local","label":"Local"}]')
        base.update(env)
        if allow_self:
            base["ALLOWED_ORIGINS"] = f"http://127.0.0.1:{self.port}"
        self.process = subprocess.Popen([str(Path(binary).resolve())], env=base,
                                        stdout=self.log, stderr=self.log)
        try:
            for _ in range(200):
                if self.process.poll() is not None:
                    raise RuntimeError("server exited before readiness")
                try:
                    if request(self.port, "GET", "/health")["status"] == 200:
                        return
                except OSError:
                    pass
                time.sleep(.03)
            raise RuntimeError("server readiness timeout")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
            raise AssertionError("shutdown exceeded grace")
        finally:
            self.log.seek(0)
            self.output = self.log.read().decode(errors="replace")
            self.log.close()
        if self.process.returncode != 0:
            raise AssertionError(f"server exited {self.process.returncode}; log: {self.output[-2000:]}")
        if any(word in self.output for word in ["ERROR: AddressSanitizer", "runtime error:",
                                                "WARNING: ThreadSanitizer", "LeakSanitizer"]):
            raise AssertionError("sanitizer finding in server log")


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        fields = {k.lower(): v for k, v in response.getheaders()}
        if "content-length" in fields and method != "HEAD" and response.status != 204:
            assert int(fields["content-length"]) == len(data)
        result = dict(status=response.status, headers=fields, size=len(data), body=data)
        if data and fields.get("content-type", "").startswith("application/json"):
            result["json"] = json.loads(data)
        return result
    finally:
        connection.close()


def normalized(row):
    result = dict(row)
    result["headers"] = dict(row["headers"])
    result["headers"].pop("date", None)
    if "json" in row:
        # Timing fields are runtime observations, not deterministic fixtures.
        value = dict(row["json"])
        for key in ["elapsedMs", "measuredElapsedMs", "requestElapsedMs"]:
            if key in value:
                assert isinstance(value[key], (int, float)) and value[key] >= 0
                value[key] = "runtime-ms"
        if "id" in value:
            assert re.fullmatch(r"[0-9a-f]{32}", value["id"])
            value["id"] = "random-id"
        result["json"] = value
        result["headers"]["content-length"] = "validated-body-length"
        result.pop("body")
        result.pop("size")
    elif row["headers"].get("content-type", "").startswith("text/html"):
        result["body"] = re.sub(rb"[0-9a-f]{36}", b"NONCE", row["body"])
        result["headers"]["content-security-policy"] = re.sub(
            r"nonce-[0-9a-f]{36}", "nonce-NONCE", result["headers"]["content-security-policy"])
    elif row["headers"].get("content-type") == "application/octet-stream":
        assert row["size"] >= 1024
        result.pop("body")  # Each server independently generates entropy.
    return result


def raw_response(stream, head=False):
    line = stream.readline()
    if not line:
        return None
    status = int(line.split()[1])
    fields = {}
    while True:
        line = stream.readline()
        if line in (b"\r\n", b""):
            break
        k, v = line.decode().split(":", 1)
        fields[k.lower()] = v.strip()
    data = b""
    if not head and status not in (100, 204, 304):
        if fields.get("transfer-encoding") == "chunked":
            while True:
                size = int(stream.readline().split(b";", 1)[0], 16)
                if not size:
                    assert stream.readline() == b"\r\n"
                    break
                data += stream.read(size)
                assert stream.read(2) == b"\r\n"
        elif "content-length" in fields:
            data = stream.read(int(fields["content-length"]))
    return dict(status=status, headers=fields, body=data, size=len(data))


def raw_scenarios(port):
    rows = {}
    # GET handler must reply before an ignored body's first byte, then parse
    # the next request only after all remaining body bytes have been consumed.
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        stream = s.makefile("rb")
        s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 4\r\n\r\n")
        rows["ignored-body-early-response"] = normalized(raw_response(stream))
        s.sendall(b"testGET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        rows["ignored-body-reuse"] = normalized(raw_response(stream))
    # Chunked body with fragments must be fully counted, including trailers.
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        stream = s.makefile("rb")
        s.sendall(b"POST /upload-stream?warmupMs=0 HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n")
        for payload in [b"1\r\na\r\n", b"3\r\nbcd\r\n", b"0\r\nX-Trailer: value\r\n\r\n"]:
            s.sendall(payload)
            time.sleep(.01)
        row = raw_response(stream)
        row["json"] = json.loads(row["body"])
        assert row["json"]["bytes"] == row["json"]["measuredBytes"] == 4
        rows["chunked-upload"] = normalized(row)
    # Backpressure: client advertises bytes but stops sending at one byte.
    with socket.create_connection(("127.0.0.1", port), timeout=4) as s:
        stream = s.makefile("rb")
        s.sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\na")
        row = raw_response(stream)
        assert row["status"] == 408
        rows["upload-timeout"] = normalized(dict(row, json=json.loads(row["body"])))
    # Half-close must not crash or retain admission leases; normal next request works.
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        s.sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10000\r\n\r\npartial")
        s.shutdown(socket.SHUT_WR)
        try:
            s.recv(8192)
        except ConnectionResetError:
            pass
    rows["after-half-close"] = normalized(request(port, "GET", "/health"))
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        stream = s.makefile("rb")
        s.sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 4\r\nExpect: 100-continue\r\n\r\n")
        interim = raw_response(stream)
        assert interim["status"] == 100
        s.sendall(b"test")
        row = raw_response(stream)
        rows["expect-continue"] = dict(interim=normalized(interim), final=normalized(dict(row, json=json.loads(row["body"]))))
    # Truncated and invalid HTTP framing gets a parser error, not a handler.
    for name, payload in {
        "invalid-method": b"G@T /health HTTP/1.1\r\nHost: localhost\r\n\r\n",
        "invalid-content-length": b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: x\r\n\r\n",
        "ambiguous-framing": b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n",
    }.items():
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            s.sendall(payload)
            row = raw_response(s.makefile("rb"))
            rows[name] = normalized(row) if row else None
    return rows


def progress_scenario(port, reusable):
    created = request(port, "POST", f"/upload-progress?lanes=2&warmupMs=0&chunked={int(reusable)}", b"")["json"]
    id_ = created["id"]
    def lane(n):
        sizes = [10240, 102400] if reusable else [1048576]
        for size in sizes:
            row = request(port, "POST", f"/upload-stream?warmupMs=0&progressId={id_}&lane={n}", bytes(size))
            assert row["status"] == 200 and row["json"]["bytes"] == size
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lane, range(2)))
    if reusable:
        assert request(port, "POST", f"/upload-progress?id={id_}&finish=1", b"")["json"] == {"closed": True}
    row = request(port, "GET", f"/upload-progress?id={id_}")
    samples = [json.loads(line) for line in row["body"].splitlines()]
    final = samples[-1]
    expected = 2*(10240+102400) if reusable else 2*1048576
    assert final["done"] and not final["failed"] and not final["expired"]
    assert final["bytes"] == final["measuredBytes"] == expected
    assert final["elapsedMs"] >= final["measuredElapsedMs"] >= 0
    for key in ["elapsedMs", "measuredElapsedMs"]:
        final[key] = "runtime-ms"
    return final


def profile_scenarios(binary, common):
    """Separate startup profiles prevent a prior case's quota from masking bugs."""
    rows = {}
    def profile(env, action):
        server = Server(binary, dict(common, **env))
        try:
            return action(server.port)
        finally:
            server.close()

    def rate(port):
        replies = [request(port, "GET", "/download?size=1024") for _ in range(17)]
        assert [r["status"] for r in replies] == [200]*16+[429]
        return [normalized(r) for r in replies]
    rows["speed-rate-limit"] = profile({"SPEED_RATE_MAX": "16"}, rate)

    def quota(port):
        first = request(port, "GET", "/download?size=1048576")
        last = request(port, "GET", "/download?size=1024")
        assert first["status"] == 200 and last["status"] == 429
        return normalized(last)
    rows["download-budget"] = profile({"DOWNLOAD_BYTE_BUDGET_PER_IP": "1048576"}, quota)

    def upload_limit(port):
        reply = request(port, "POST", "/upload", bytes(1025))
        assert reply["status"] == 413 and reply["headers"]["connection"] == "close"
        return normalized(reply)
    rows["upload-size-limit"] = profile({"MAX_UPLOAD": "1024"}, upload_limit)

    def stream_quota(port):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
        try:
            c.request("GET", "/download-stream?maxMs=1000")
            reply = c.getresponse()
            try:
                reply.read()
                raise AssertionError("quota-exhausted stream returned successful EOF")
            except http.client.IncompleteRead as error:
                assert len(error.partial) == 1048576
                return dict(status=reply.status, partial_bytes=len(error.partial), aborted=True)
        finally:
            c.close()
    rows["stream-budget-aborts"] = profile({"DOWNLOAD_BYTE_BUDGET_PER_IP": "1048576"}, stream_quota)

    def pipelines(port):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            stream = s.makefile("rb")
            s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"*4)
            replies = [raw_response(stream) for _ in range(4)]
            assert [r["status"] for r in replies] == [200, 200, 503, 503]
            return [normalized(dict(r, json=json.loads(r["body"]))) if r["status"] == 200 else normalized(r) for r in replies]
    rows["socket-request-limit-pipeline"] = profile({"HTTP_MAX_REQUESTS_PER_SOCKET": "2"}, pipelines)

    def excess_headers(port):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n"+b"X-F: a\r\n"*17+b"\r\n")
            reply = raw_response(s.makefile("rb"))
            assert reply["status"] == 431
            return normalized(reply)
    rows["header-count-limit"] = profile({"HTTP_MAX_HEADERS_COUNT": "16"}, excess_headers)

    def cors(port):
        return [normalized(request(port, "GET", path, headers={"Origin": "https://other.invalid"}))
                for path in ["/health", "/latency", "/download?size=1024", "/upload-progress"]]
    rows["public-cors-is-not-data-opt-in"] = profile({"PUBLIC_CORS": "1"}, cors)
    rows["allowed-cross-site-opt-in"] = profile({"SPEEDTEST_ALLOW_CROSS_SITE": "1"}, lambda port:
        normalized(request(port, "GET", "/download?size=1024", headers={"Origin": "https://example.invalid", "Sec-Fetch-Site": "cross-site"})))

    def proxy(port):
        return normalized(request(port, "GET", "/client-info", headers={"X-Real-IP": "::ffff:192.168.1.8", "X-Forwarded-For": "192.168.1.9"}))
    rows["trusted-proxy-peer"] = profile({"TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1"}, proxy)
    rows["untrusted-proxy-peer"] = profile({"TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "192.0.2.1"}, proxy)

    nodes = [None, 42, {"id": 7, "base": "https://EXAMPLE.invalid:443/a?x=1"},
             {"id": "primary", "base": "http://[2001:db8::1]:8080/a", "label": "<>&"},
             {"id": "bad", "base": "https://user:pw@example.invalid/", "directPage": "javascript:alert(1)"}]
    rows["node-config-csp-escaping"] = profile({"SPEEDTEST_NODES": json.dumps(nodes, separators=(",", ":"))},
                                               lambda port: normalized(request(port, "GET", "/")))

    def active_limit(port):
        sockets = []
        try:
            for _ in range(8):
                s = socket.create_connection(("127.0.0.1", port), timeout=3)
                s.sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10000\r\n\r\na")
                sockets.append(s)
            time.sleep(.15)
            reply = request(port, "GET", "/download?size=1024")
            assert reply["status"] == 429
            return normalized(reply)
        finally:
            for s in sockets:
                s.close()
    rows["active-ip-admission"] = profile({"MAX_ACTIVE_PER_IP": "8", "MAX_UPLOAD_MS": "3000"}, active_limit)
    return rows


def run(args):
    results, failures = [], []
    common = {"MAX_UPLOAD_MS": "1000", "SPEED_RATE_MAX": "2000", "RATE_MAX": "1000"}
    rust = Server(args.rust, common)
    cpp = None
    try:
        # Private deployment Rust may have legacy CSP origins; discover that
        # operational profile without hardcoding/publishing real infrastructure.
        root = request(rust.port, "GET", "/")
        csp = root["headers"].get("content-security-policy", "")
        prefix = re.search(r"connect-src 'self' (.*?);", csp)
        profile = {"SPEEDTEST_CSP_COMPAT_ORIGINS": prefix.group(1).strip()} if prefix else {}
        cpp = Server(args.cpp, dict(common, **profile))

        def compare(name, left, right):
            equal = left == right
            results.append(dict(name=name, passed=equal))
            if not equal:
                # No body, addresses or provider responses in public summaries.
                failures.append(dict(name=name, rust=repr(left)[:2500], cpp=repr(right)[:2500]))

        cases = [("GET", "/health", None, {}), ("GET", "/client-info", None, {}),
                 ("GET", "/", None, {}), ("HEAD", "/", None, {}),
                 ("GET", "/favicon.svg", None, {}), ("HEAD", "/favicon.svg", None, {}),
                 ("GET", "/latency", None, {}), ("HEAD", "/latency", None, {}),
                 ("OPTIONS", "/unknown", None, {}), ("GET", "/missing", None, {}),
                 ("HEAD", "/missing", None, {}), ("POST", "/health", b"", {}),
                 ("GET", "/upload-progress", None, {})]
        for path in ["/download", "/download-100k", "/speed/download"]:
            for size in ["10240", "102400", "1048576", "0", "-1", "1.9e4", "NaN", "bad", "+10240", "%2010240"]:
                cases.append(("GET", f"{path}?size={size}", None, {}))
        for path in ["/upload", "/upload-stream?warmupMs=0", "/upload-stream?warmupMs=2000"]:
            for size in [0, 10240, 102400, 1048576]:
                cases.append(("POST", path, bytes(size), {}))
        for path in ["/health", "/latency", "/download?size=10240", "/webrtc/offer"]:
            for headers in [{"Origin": "https://example.invalid"}, {"Origin": "https://denied.invalid"},
                            {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null", "Sec-Fetch-Site": "CROSS-SITE"}]:
                cases.append(("POST" if "offer" in path else "GET", path, b"{}" if "offer" in path else None, headers))
        for path in ["/speed/health", "/speed/latency", "/speed", "/speed/", "/speed/favicon.svg", "/speed/upload-progress"]:
            cases.append(("GET", path, None, {}))
        for lanes in ["1", "2", "4", "8", "3", "256", "-1", "+2", "0", "bad", ""]:
            cases.append(("POST", f"/upload-progress?lanes={lanes}", b"", {}))
        for path in ["/upload-progress?id=bad", "/upload-progress?id=bad&finish=1",
                     "/upload-progress?id="+"a"*32, "/upload?progressId=bad"]:
            cases.append(("POST", path, b"", {}))
        for value in [b"", b"{", b"[]", b"null", b"{}", b'{"attemptId":"bad space"}',
                      b'{"attemptId":"test","type":"answer","sdp":""}',
                      b'{"attemptId":"test","type":"offer","sdp":"v=0\\r\\n"}',
                      b'{"attemptId":"test","action":"cancel"}']:
            cases.append(("POST", "/webrtc/offer", value, {"Content-Type": "application/json", "X-Speedtest-Attempt-Id": "test"}))
        cases.append(("POST", "/webrtc/offer", b"x"*(256*1024+1), {"Content-Type": "application/json"}))
        for index, (method, path, body, headers) in enumerate(cases):
            compare(f"http-{index}:{method} {path}", normalized(request(rust.port, method, path, body, headers)),
                    normalized(request(cpp.port, method, path, body, headers)))
        left, right = raw_scenarios(rust.port), raw_scenarios(cpp.port)
        for key in left:
            compare("raw:"+key, left[key], right[key])
        for reusable in [False, True]:
            compare(f"progress-reusable-{reusable}", progress_scenario(rust.port, reusable), progress_scenario(cpp.port, reusable))
        def concurrent(port):
            with ThreadPoolExecutor(max_workers=16) as pool:
                rows = list(pool.map(lambda _: normalized(request(port, "GET", "/download?size=10240")), range(128)))
            assert all(row["status"] == 200 for row in rows)
            return rows
        compare("128-requests-16-concurrent", concurrent(rust.port), concurrent(cpp.port))
        left, right = profile_scenarios(args.rust, common), profile_scenarios(args.cpp, dict(common, **profile))
        for key in left:
            compare("profile:"+key, left[key], right[key])
    finally:
        rust.close()
        if cpp:
            cpp.close()
    report = dict(passed=not failures, cases=len(results), results=results, failures=failures,
                  rtc="disabled on both; native RTC not covered", processes_exited=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({k: report[k] for k in ["passed", "cases", "processes_exited"]}), flush=True)
    for failure in failures:
        print(failure["name"], flush=True)
    return not failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--cpp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args()) else 1)
