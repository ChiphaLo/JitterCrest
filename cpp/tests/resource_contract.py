"""Bounded real-process admission/abort/progress exhaustion contracts.

Shared HTTP scenarios use the same client against independent Rust/C++ servers.
The extra C++ socket ceiling is explicitly separate: the original Rust reference
does not expose MAX_HTTP_CONNECTIONS, so it is not an invented equivalence claim.
These are finite adversarial tests, not proof of absence of every resource leak.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import struct
import time

from http_capacity import established
from http_contract import Server, request, raw_response


@contextmanager
def backend(binary, env, log_path):
    server = Server(binary, env)
    try:
        yield server
    finally:
        try:
            server.close()
        finally:
            log_path.write_text(server.output)


def headers(identity):
    return {"X-Real-IP": f"198.19.0.{identity}"}


def hold_upload(server, identity):
    client = socket.create_connection(("127.0.0.1", server.port), timeout=4)
    try:
        client.sendall(("POST /upload HTTP/1.1\r\nHost: localhost\r\n"
                        f"X-Real-IP: 198.19.0.{identity}\r\n"
                        "Content-Length: 1024\r\nExpect: 100-continue\r\n\r\n").encode())
        # 100 Continue is sent only after admission. Avoid guessing that an
        # opened TCP connection has already acquired a throughput lease.
        stream = client.makefile("rb")
        try:
            reply = raw_response(stream)
            assert reply and reply["status"] == 100, reply
        finally:
            stream.close()
        return client
    except BaseException:
        client.close()
        raise


def abort(clients):
    for client in clients:
        try:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        finally:
            client.close()


def active_limits(binary, per_ip, log_path):
    env = {"MAX_ACTIVE_GLOBAL": "16" if per_ip else "8", "MAX_ACTIVE_PER_IP": "8",
           "TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1",
           "RATE_MAX": "1000", "SPEED_RATE_MAX": "2000", "MAX_UPLOAD_MS": "10000"}
    with backend(binary, env, log_path) as server:
        for round_ in range(3):
            clients = []
            try:
                for i in range(8):
                    clients.append(hold_upload(server, 1 if per_ip else i + 1))
                rejected = request(server.port, "POST", "/upload", b"x",
                                   headers=headers(1 if per_ip else 9))
                assert rejected["status"] == 429, rejected
                expected = "too many active connections" if per_ip else "too many global active connections"
                assert rejected["json"]["error"] == expected, rejected
                assert request(server.port, "GET", "/health")["status"] == 200
            finally:
                abort(clients)
            deadline = time.monotonic() + 3
            while True:
                recovered = request(server.port, "POST", "/upload", b"recovered", headers=headers(1))
                if recovered["status"] == 200:
                    assert recovered["json"]["bytes"] == 9
                    break
                assert recovered["status"] == 429 and time.monotonic() < deadline, recovered
                time.sleep(.02)
        return dict(admitted_per_round=8, rejected_status=429, health_remained_available=True,
                    abrupt_reset_released_leases=True, recovery_rounds=3)


def progress_limit(binary, log_path):
    env = {"MAX_ACTIVE_GLOBAL": "8", "MAX_ACTIVE_PER_IP": "8", "RATE_MAX": "1000",
           "SPEED_RATE_MAX": "2000", "TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1"}
    with backend(binary, env, log_path) as server:
        ids = []
        for i in range(1, 9):
            created = request(server.port, "POST", "/upload-progress?lanes=1&warmupMs=0", b"", headers(i))
            assert created["status"] == 200, created
            ids.append(created["json"]["id"])
        overflow = request(server.port, "POST", "/upload-progress?lanes=1", b"", headers(9))
        assert overflow["status"] == 429 and overflow["json"]["error"] == "upload progress capacity exceeded", overflow
        wrong_owner = request(server.port, "GET", "/upload-progress?id=" + ids[0], headers=headers(9))
        assert wrong_owner["status"] == 404
        for i, id_ in enumerate(ids, 1):
            done = request(server.port, "POST", f"/upload-stream?progressId={id_}&lane=0&warmupMs=0", b"", headers(i))
            assert done["status"] == 200 and done["json"]["bytes"] == 0, done
        repeated = request(server.port, "POST", f"/upload-stream?progressId={ids[0]}&lane=0", b"", headers(1))
        assert repeated["status"] == 400 and repeated["json"]["error"] == "upload progress lane already attached", repeated
        # Completed records deliberately remain readable for five seconds.
        # They must not be prematurely evicted simply to pass the cap test.
        full = request(server.port, "POST", "/upload-progress?lanes=1", b"", headers(9))
        assert full["status"] == 429
        started, deadline = time.monotonic(), time.monotonic() + 7
        while True:
            created = request(server.port, "POST", "/upload-progress?lanes=1", b"", headers(9))
            if created["status"] == 200:
                assert time.monotonic() - started >= 4.5
                break
            assert created["status"] == 429 and time.monotonic() < deadline, created
            time.sleep(.1)
        assert request(server.port, "GET", "/upload-progress?id=" + ids[0], headers=headers(1))["status"] == 404
        return dict(admitted=8, rejected_status=429, owner_isolation=True,
                    one_use_lane_enforced=True, final_retention_preserved=True,
                    expired_capacity_reclaimed=True)


def socket_ceiling(binary, log_path):
    with backend(binary, {"MAX_HTTP_CONNECTIONS": "8"}, log_path) as server:
        clients = []
        try:
            for _ in range(8):
                client = socket.create_connection(("127.0.0.1", server.port), timeout=3)
                clients.append(client)
                client.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                stream = client.makefile("rb")
                try:
                    assert raw_response(stream)["status"] == 200
                finally:
                    stream.close()
            assert established(server.process.pid, server.port) == 8
            with socket.create_connection(("127.0.0.1", server.port), timeout=3) as extra:
                try:
                    extra.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    assert extra.recv(1) == b"", "excess socket was admitted"
                except (ConnectionResetError, BrokenPipeError):
                    pass
        finally:
            abort(clients)
        deadline = time.monotonic() + 3
        while established(server.process.pid, server.port):
            assert time.monotonic() < deadline, "accepted sockets were retained after reset"
            time.sleep(.02)
        assert request(server.port, "GET", "/health")["status"] == 200
        return dict(accepted_established=8, ninth_socket_closed=True, recovered=True,
                    reference_equivalence_claimed=False)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ["cpp", "rust", "output"]:
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    results = {}
    protected = [Path(__file__), Path(__file__).with_name("http_contract.py"),
                 Path(__file__).with_name("http_capacity.py"),
                 Path(__file__).resolve().parents[1] / "benchmarks/benchmark.py", args.cpp, args.rust]
    identities = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    try:
        for label, binary in [("rust", args.rust), ("cpp", args.cpp)]:
            observed = {}
            for name, function in [("global-active-abort", lambda: active_limits(binary, False,
                    args.output.with_name(args.output.stem + "-" + label + "-global.log"))),
                    ("per-ip-active-abort", lambda: active_limits(binary, True,
                    args.output.with_name(args.output.stem + "-" + label + "-per-ip.log"))),
                    ("progress-capacity", lambda: progress_limit(binary,
                    args.output.with_name(args.output.stem + "-" + label + "-progress.log")))]:
                print("RUN resource " + label + " " + name, flush=True)
                observed[name] = function()
                print("PASS resource " + label + " " + name, flush=True)
            results[label] = observed
        assert results["rust"] == results["cpp"], results
        extra = socket_ceiling(args.cpp, args.output.with_name(args.output.stem + "-cpp-sockets.log"))
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                   for path, digest in identities.items()), "test sources or executables changed during validation"
    except BaseException as error:
        args.output.write_text(json.dumps(dict(passed=False, results=results,
            error_type=type(error).__name__, error=str(error)), indent=2) + "\n")
        raise
    args.output.write_text(json.dumps(dict(passed=True, shared=results, cpp_socket_ceiling=extra,
                                          processes_exited=True, unchanged_sources_and_executables=True,
                                          sha256=identities), indent=2) + "\n")
    print("PASS shared resource admission/reset/progress and C++ socket ceiling", flush=True)
