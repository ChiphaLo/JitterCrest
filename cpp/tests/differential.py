"""Shared deterministic client: original Rust pure modules vs independent C++ core.

No Rust code is linked into or invoked by the C++ library/service. This optional
development test invokes both independent test executables using the same JSONL.
"""
import argparse
import base64
import json
import math
from pathlib import Path
import random
import subprocess


def vectors():
    rng = random.Random(20261025)
    cases = []
    def add(op, **kwargs):
        cases.append(dict(op=op, **kwargs))
    for text in ["", "a", "test-123", "测速", "😀", "a\0b", "𝄞é\U0010ffff"]:
        add("hash", text=text)
    for _ in range(200):
        text = "".join(chr(rng.choice([rng.randrange(32, 127), rng.randrange(0x10000, 0x110000)]))
                       for _ in range(rng.randrange(30)))
        add("hash", text=text)
    for n in [0, 1, 27, 28, 127, 129, 256, 4096] + [128] * 1000:
        add("frame", hex=rng.randbytes(n).hex())
    for _ in range(1000):
        values = [rng.uniform(-1e6, 1e6) for _ in range(rng.randrange(100))]
        add("percentile", values=values, p=rng.choice([-.5, 0, .1, .5, .95, .99, 1, 2]))
    for n in [0, 1, 2, 99, 100, 101, 2000, 30000, 2**32, 2**63, 2**64-1]:
        for density in [-1, 0, .5, .99, 1, 2]:
            add("density", planned=n, density=density)
    for _ in range(300):
        n = rng.randrange(10000)
        sent = rng.randrange(n+1)
        add("accounting", planned=n, scheduled=sent, skipped=n-sent, actualSent=rng.randrange(sent+1))
        add("accounting", planned=n, scheduled=sent, skipped=n-sent+1, actualSent=sent+1)
    add("accounting", planned=1, scheduled=2**64-1, skipped=2, actualSent=0)
    hosts = ["8.8.8.8", "1.1.1.1", "127.0.0.1", "192.0.2.1", "198.51.100.2",
             "203.0.113.1", "192.31.196.1", "192.175.48.1", "100.64.0.1", "224.0.0.1",
             "2606:4700::1111", "2001:db8::1", "2001:30::1", "2002::1", "3fff::1",
             "::ffff:8.8.8.8", "host.local", "PEER.LOCAL.", "invalid"]
    for host in hosts:
        for separator in ["\r\n", "\n", "\r", ""]:
            add("sdp", sdp=f"v=0\r\na=candidate:1 1 UDP 00123 {host} 09999 typ host{separator}")
    for fields in ["1 +1 udp 1 8.8.8.8 9999 typ host", "1 2 udp 1 8.8.8.8 9999 typ host",
                   "1 1 tcp 1 8.8.8.8 9999 typ host", "1 1 udp 0 8.8.8.8 9999 typ host",
                   "1 1 udp +1 8.8.8.8 9999 typ host", "1 1 udp 1 8.8.8.8 +99 typ host",
                   "1 1 udp 1 8.8.8.8 65536 typ host", "1 1 udp 1 8.8.8.8 99 typ bad",
                   "1 1 udp 1 8.8.8.8 99 typ relay raddr 1.1.1.1 rport 1", "incomplete"]:
        add("sdp", sdp="a=candidate:"+fields)
    for ch in ["\t", "\v", "\f", "\u0085", "\u00a0", "\u1680", "\u2000", "\u200a", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000"]:
        add("sdp", sdp="a=candidate:"+ch.join("1 1 UDP 123 8.8.8.8 9999 typ host".split()))
    for text in ["x"*4097, "\n"*1024, "x"*65537, "m=x\n"*9,
                 "a=candidate:1 1 udp 1 8.8.8.8 9999 typ host\n"*65]:
        add("sdp", sdp=text)
    identity = dict(sessionId="s😀", testId="t", sessionHash=42, testHash=7)
    payload = json.dumps(dict(type="uploadComplete", **identity, planned=8,
                             scheduled=8, skipped=0, actualSent=8, sentSeqs=list(range(8))),
                         ensure_ascii=False, separators=(",", ":")).encode()
    parts = [dict(type="controlFragment", messageType="uploadComplete", fragmentId="f",
                  encoding="base64", count=3, index=i, **identity,
                  data=base64.b64encode(chunk).decode())
             for i, chunk in enumerate([payload[:17], payload[17:29], payload[29:]])]
    for order in [[0, 1, 2], [2, 1, 0], [0, 0, 1, 2], [2, 2, 1, 0], [0]]:
        add("fragments", parts=[parts[i] for i in order])
    for key, values in dict(type=[None, 1, "bad"], fragmentId=[None, "", 123],
                            count=[0, -1, 3.0, "3", 5000], index=[-1, 3, 0.0, "0"],
                            encoding=["bad", None], data=["A", "AB==", "AA=A", None]).items():
        for value in values:
            add("fragments", parts=[dict(parts[0], **{key: value})])
    add("fragments", parts=parts, bytes=10)
    add("fragments", parts=[parts[0], dict(parts[1], sessionHash=43)])
    add("fragments", parts=[dict(parts[0], fragmentId="f"+str(i)) for i in range(5)])
    add("fragments", parts=[dict(parts[0], fragmentId="f"*1000),
                           dict(parts[1], fragmentId="f"*1000), dict(parts[2], fragmentId="f"*1000)])
    for depth in list(range(115, 135)) + [300]:
        nested = 0
        for _ in range(depth):
            nested = [nested]
        value = dict(type="uploadComplete", **identity, extra=nested)
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        add("fragments", parts=[dict(parts[0], index=0, count=1,
                                     data=base64.b64encode(data).decode())])
    for value in [dict(type=3, **identity), [], "text", dict(type="bad", **identity)]:
        add("fragments", parts=[dict(parts[0], index=0, count=1,
                                     data=base64.b64encode(json.dumps(value).encode()).decode())])
    for legacy in [False, True]:
        for size in [0, 1, 900, 5000]:
            for chunk in [0, 256, 512]:
                add("serialize", value=dict(type="downResult", **identity, text="x"*size),
                    single=900, chunk=chunk, maximum=8, legacy=legacy)
    return cases


def equivalent(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    return a == b


def compare(rust_command, cpp_command, output):
    cases = vectors()
    payload = "".join(json.dumps(x, ensure_ascii=False)+"\n" for x in cases)
    outputs = []
    for command in [rust_command, cpp_command]:
        result = subprocess.run(command, input=payload, encoding="utf-8", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120)
        if result.returncode:
            raise RuntimeError(f"Test executable failed ({result.returncode}): {result.stderr[-2000:]}")
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(rows) == len(cases), (len(rows), len(cases))
        outputs.append(rows)
    failures = [dict(index=i, input=cases[i], rust=a, cpp=b)
                for i, (a, b) in enumerate(zip(*outputs)) if not equivalent(a, b)]
    report = dict(passed=not failures, vectors=len(cases), failed=len(failures),
                  float_relative_tolerance=1e-12, float_absolute_tolerance=1e-12,
                  discrete_comparison="exact", failures=failures)
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}))
    return not failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpp", required=True)
    parser.add_argument("--rust", required=True)
    parser.add_argument("--wsl-distro", help="Windows test host: run only the C++ executable in this local WSL")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cpp = [args.cpp, "--json-lines"]
    if args.wsl_distro:
        cpp = ["wsl", "-d", args.wsl_distro, "--exec", *cpp]
    if not compare([args.rust], cpp, args.output):
        raise SystemExit(1)
