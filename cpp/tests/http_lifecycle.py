"""Slow/lifecycle actual-process contracts; takes about 35 seconds per pair."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import time
from http_contract import Server, request, normalized, raw_response


def observe(port):
    rows = {}
    created = request(port, "POST", "/upload-progress?lanes=1&warmupMs=0", b"")["json"]
    id_ = created["id"]
    def upload():
        with socket.create_connection(("127.0.0.1", port), timeout=4) as s:
            stream = s.makefile("rb")
            s.sendall((f"POST /upload-stream?warmupMs=0&progressId={id_}&lane=0 HTTP/1.1\r\n"
                       "Host: localhost\r\nTransfer-Encoding: chunked\r\n\r\n").encode())
            for _ in range(6):
                s.sendall(b"2800\r\n"+bytes(10240)+b"\r\n")
                time.sleep(.08)
            s.sendall(b"0\r\n\r\n")
            reply = raw_response(stream)
            assert reply["status"] == 200 and json.loads(reply["body"])["bytes"] == 61440
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        c.request("GET", f"/upload-progress?id={id_}")
        response = c.getresponse()
        with ThreadPoolExecutor(max_workers=1) as pool:
            uploading = pool.submit(upload)
            samples = []
            while True:
                line = response.readline()
                if not line:
                    break
                samples.append(json.loads(line))
            uploading.result()
        assert len(samples) >= 4
        assert any(0 < s["bytes"] < 61440 and not s["done"] for s in samples)
        assert all(a["bytes"] <= b["bytes"] for a, b in zip(samples, samples[1:]))
        final = samples[-1]
        assert final["done"] and not final["failed"] and final["bytes"] == final["measuredBytes"] == 61440
        rows["live-progress"] = dict(intermediate_samples=True, monotonic=True, final_bytes=61440)
    finally:
        c.close()

    with socket.create_connection(("127.0.0.1", port), timeout=4) as s:
        stream = s.makefile("rb")
        s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\n")
        assert raw_response(stream)["status"] == 200
        for _ in range(5):
            s.sendall(b"x")
            time.sleep(.7)
        s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        reply = raw_response(stream)
        rows["ignored-body-survives-idle-window"] = normalized(dict(reply, json=json.loads(reply["body"])))

    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
        s.sendall(b"GET /download?size=100663296 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        stream = s.makefile("rb")
        assert stream.readline().startswith(b"HTTP/1.1 200")
        while stream.readline() != b"\r\n":
            pass
        time.sleep(1.3)  # Writer is backpressured; deadline must still close it.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        received = 0
        while True:
            try:
                part = stream.read(65536)
            except ConnectionResetError:
                break
            if not part:
                break
            received += len(part)
        assert received < 100663296
        rows["fixed-download-deadline-while-blocked"] = dict(truncated=True, connection_closed=True)
    assert request(port, "GET", "/health")["status"] == 200
    return rows


def slow_header(port, partial=True):
    with socket.create_connection(("127.0.0.1", port), timeout=36) as s:
        if partial:
            s.sendall(b"GET /health HTTP/1.1\r\nHo")
        start = time.monotonic()
        reply = raw_response(s.makefile("rb"))
        elapsed = time.monotonic()-start
        assert reply["status"] == 408 and 25 <= elapsed <= 33, elapsed
        return normalized(reply)


def idle(port):
    with socket.create_connection(("127.0.0.1", port), timeout=4) as s:
        s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert raw_response(s.makefile("rb"))["status"] == 200
        start = time.monotonic()
        assert s.recv(1) == b""
        assert 1.8 <= time.monotonic()-start <= 3.5
    return dict(idle_closed=True, node_buffer_ms=1000)


def slow_offer(port):
    with socket.create_connection(("127.0.0.1", port), timeout=36) as s:
        s.sendall(b"POST /webrtc/offer HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{")
        start = time.monotonic()
        reply = raw_response(s.makefile("rb"))
        assert reply["status"] == 408 and 25 <= time.monotonic()-start <= 33
        return normalized(reply)


def run(args):
    env = {"MAX_UPLOAD_MS": "1000", "HTTP_HEADERS_TIMEOUT_MS": "1000", "HTTP_REQUEST_TIMEOUT_MS": "1000",
           "HTTP_KEEP_ALIVE_TIMEOUT_MS": "1000", "MAX_FIXED_DOWNLOAD_MS": "1000"}
    servers = []
    try:
        for binary in [args.rust, args.cpp]:
            servers.append(Server(binary, env))
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [{"slow-header": pool.submit(slow_header, s.port),
                        "silent-initial-header": pool.submit(slow_header, s.port, False),
                        "slow-offer-body": pool.submit(slow_offer, s.port),
                        "idle": pool.submit(idle, s.port), "lifecycle": pool.submit(observe, s.port)} for s in servers]
            results = [{name: future.result() for name, future in row.items()} for row in futures]
    finally:
        for s in servers:
            s.close()
    assert results[0] == results[1], results
    shutdown = []
    for binary in [args.rust, args.cpp]:
        server = Server(binary, {"MAX_UPLOAD_MS": "10000"})
        with socket.create_connection(("127.0.0.1", server.port), timeout=7) as s:
            s.sendall(b"GET /health HTTP/1.1\r\nHo")
            time.sleep(.1)
            start = time.monotonic()
            server.close()
            elapsed = time.monotonic()-start
            assert 4.8 <= elapsed < 6.5, elapsed
            assert s.recv(1) == b""
            shutdown.append(dict(bounded=True, listener_stopped=True))
    assert shutdown[0] == shutdown[1]
    report = dict(passed=True, contracts=8, results=results[1], forced_shutdown=shutdown[1],
                  processes_exited=True, rtc="not covered")
    args.output.write_text(json.dumps(report, indent=2, default=lambda value: value.decode("utf-8"))+"\n")
    print(json.dumps({k: report[k] for k in ["passed", "contracts", "processes_exited"]}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rust", required=True, type=Path)
    p.add_argument("--cpp", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    run(p.parse_args())
