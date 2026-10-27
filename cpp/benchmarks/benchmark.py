"""Linux-only, same-client alternating native Rust/C++ benchmarks.

Servers keep their real rate/quota code. A loopback-only trusted-proxy profile
models multiple synthetic benchmark-range clients instead of disabling limits.
Raw reports/logs belong in a private evidence directory, not the source tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from http_contract import Server, request


def snapshot(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    status = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, value = line.partition(":")
        status[key] = value.strip()
    return dict(cpu_seconds=(int(stat[11])+int(stat[12]))/os.sysconf("SC_CLK_TCK"),
                rss_bytes=int(status["VmRSS"].split()[0])*1024,
                peak_rss_bytes=int(status["VmHWM"].split()[0])*1024,
                threads=int(status["Threads"]), start_ticks=int(stat[19]))


def pin_threads(pid, cpus):
    for task in Path(f"/proc/{pid}/task").iterdir():
        os.sched_setaffinity(int(task.name), cpus)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timed(command, folder, env, input_bytes=None):
    resource = folder / "resource.json"
    result = subprocess.run(["/usr/bin/time", "-f",
        '{"user_seconds":%U,"system_seconds":%S,"peak_rss_kib":%M,"exit":%x}',
        "-o", str(resource), *command], input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=dict(env, LC_ALL="C"), timeout=1900)
    if result.returncode:
        raise RuntimeError(f"benchmark child exited {result.returncode}: {result.stderr.decode(errors='replace')[-3000:]}")
    usage = json.loads(resource.read_text())
    usage["cpu_seconds"] = usage["user_seconds"] + usage["system_seconds"]
    usage["peak_rss_bytes"] = usage.pop("peak_rss_kib")*1024
    return result.stdout, usage


def wrk_run(args, port, case, duration, folder):
    method, path, size, concurrency = case
    threads = min(args.client_threads, concurrency)
    # POST cache is bounded to 16 MiB per worker; GET requests have no body.
    variants = max(16, min(1024, (16 << 20)//max(size, 1))) if method in ["POST", "MIX"] else 1024
    command = ["taskset", "-c", args.client_cpus, str(args.wrk),
               "-t", str(threads), "-c", str(concurrency), "-d", f"{duration}s",
               "--timeout", "2s", "--latency", "-s", str(Path(__file__).with_name("workload.lua")),
               f"http://127.0.0.1:{port}", "--", method, path, str(size), str(variants)]
    env = dict(os.environ)
    if args.wrk_library_path:
        env["LD_LIBRARY_PATH"] = str(args.wrk_library_path)
    stdout, resources = timed(command, folder, env)
    text = stdout.decode()
    (folder / "wrk.txt").write_text(text)
    line = next((x for x in text.splitlines() if x.startswith("BENCHMARK_JSON:")), None)
    if line is None:
        raise AssertionError("wrk Lua summary missing")
    value = json.loads(line.split(":", 1)[1])
    if value["requests"] <= 0 or sum(value["errors"].values()) != 0:
        raise AssertionError(f"non-successful workload; never count throttled/error bytes as throughput: {value}")
    value["client_resources"] = resources
    value["synthetic_client_identities"] = threads*variants
    return value


def http_case(args, binary, case):
    method, path, size, concurrency = case
    profile = {"TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1,::1",
               "RATE_MAX": "1000", "SPEED_RATE_MAX": "2000", "MAX_ACTIVE_GLOBAL": "1024",
               "MAX_ACTIVE_PER_IP": "128", "DOWNLOAD_BYTE_BUDGET_PER_IP": str(32 << 30),
               "UPLOAD_BYTE_BUDGET_PER_IP": str(32 << 30), "BYTE_BUDGET_WINDOW_MS": "10000",
               "SPEEDTEST_IO_THREADS": "2", "TOKIO_WORKER_THREADS": "2",
               "HTTP_MAX_REQUESTS_PER_SOCKET": "1000"}
    server = Server(binary, profile)
    rows, failures = [], []
    stop = threading.Event()
    def sample():
        while not stop.wait(.25):
            try:
                rows.append(dict(at=time.monotonic(), **snapshot(server.process.pid)))
            except (OSError, KeyError, ValueError) as error:
                failures.append(str(error))
                return
    sampler = None
    try:
        pin_threads(server.process.pid, args.server_cpu_set)
        headers = {"X-Forwarded-For": "198.19.255.254", "X-Real-IP": "198.19.255.254"}
        if method in ["GET", "MIX"]:
            checked = request(server.port, "GET", path if method == "GET" else f"/download?size={size}", headers=headers)
            expected_status = 204 if path == "/latency" else 200
            assert checked["status"] == expected_status and (path == "/latency" or checked["size"] == size), checked
        if method in ["POST", "MIX"]:
            checked = request(server.port, "POST", "/upload", b"x"*size, headers=headers)
            assert checked["status"] == 200 and checked["json"]["bytes"] == size, checked
        time.sleep(.25)
        idle = snapshot(server.process.pid)
        with tempfile.TemporaryDirectory(prefix="speedtest-benchmark-") as tmp:
            folder = Path(tmp)
            wrk_run(args, server.port, case, args.warmup, folder)
            before = snapshot(server.process.pid)
            started = time.monotonic()
            sampler = threading.Thread(target=sample)
            sampler.start()
            measured = wrk_run(args, server.port, case, args.duration, folder)
            ended = time.monotonic()
            after = snapshot(server.process.pid)
        stop.set()
        sampler.join()
        sampler = None
        assert not failures and before["start_ticks"] == after["start_ticks"], failures
        affinities = [sorted(os.sched_getaffinity(int(task.name))) for task in Path(f"/proc/{server.process.pid}/task").iterdir()]
        assert all(set(cpus) == args.server_cpu_set for cpus in affinities), affinities
        seconds = measured["duration_us"]/1e6
        measured.update(method=method, path=path, payload_bytes=size, concurrency=concurrency,
            server_cpu_seconds=after["cpu_seconds"]-before["cpu_seconds"],
            observed_wall_seconds=ended-started, server_cpu_percent_one_core=100*(after["cpu_seconds"]-before["cpu_seconds"])/(ended-started),
            payload_mbps=size*measured["requests"]*8/seconds/1e6,
            requests_per_second=measured["requests"]/seconds,
            idle_rss_bytes=idle["rss_bytes"], post_warmup_rss_bytes=before["rss_bytes"],
            end_rss_bytes=after["rss_bytes"], peak_rss_bytes=after["peak_rss_bytes"],
            server_threads=after["threads"], rss_timeline=[dict(seconds=r["at"]-started,
                rss_bytes=r["rss_bytes"], cpu_seconds=r["cpu_seconds"]-before["cpu_seconds"]) for r in rows])
        return measured
    finally:
        stop.set()
        if sampler:
            sampler.join(timeout=2)
        server.close()


def core_inputs(scale):
    rng = random.Random(20261026)
    frames = []
    for index in range(256):
        frame = bytearray(rng.randbytes(128))
        struct.pack_into("<d", frame, 16, rng.randrange(100000)/64.0)
        if index % 8 == 0:
            frame.pop()
        frames.append(frame.hex())
    ascii_ids = ["id-"+"".join(rng.choice("0123456789abcdef-") for _ in range(32)) for _ in range(256)]
    mixed_ids = ["id-"+"".join(rng.choice("abXYZ019测速😀") for _ in range(32)) for _ in range(256)]
    return [dict(operation="decode", dataset="128-byte frames, 1/8 invalid length", iterations=max(19, int(100000019*scale)), frames=frames),
            dict(operation="hash", dataset="ASCII IDs", iterations=max(19, int(5000033*scale)), texts=ascii_ids),
            dict(operation="hash", dataset="Unicode IDs", iterations=max(19, int(5000033*scale)), texts=mixed_ids),
            dict(operation="percentile", dataset="P95, 512 samples", iterations=max(19, int(100009*scale)), values=[rng.randrange(65536)/64.0 for _ in range(512)])]


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(report, indent=2)+"\n")
    temporary.replace(path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["http", "core", "soak"], required=True)
    p.add_argument("--cpp", type=Path, required=True)
    p.add_argument("--rust", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--wrk", type=Path)
    p.add_argument("--wrk-library-path", type=Path)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--concurrencies", default="1,16,64")
    p.add_argument("--server-cpus", default="0,2")
    p.add_argument("--client-cpus", default="4,6,8,10")
    p.add_argument("--driver-cpu", type=int, default=12)
    p.add_argument("--client-threads", type=int, default=4)
    p.add_argument("--core-scale", type=float, default=1)
    p.add_argument("--validation", action="store_true", help="Allow sanitizer binaries; never label this run performance data")
    args = p.parse_args()
    if platform.system() != "Linux":
        p.error("Linux /proc and CPU affinity are required")
    args.server_cpu_set = {int(n) for n in args.server_cpus.split(",")}
    client_set = {int(n) for n in args.client_cpus.split(",")}
    available = os.sched_getaffinity(0)
    assert args.server_cpu_set and client_set and not args.server_cpu_set & client_set
    assert args.server_cpu_set | client_set | {args.driver_cpu} <= available
    assert args.driver_cpu not in args.server_cpu_set | client_set
    assert 1 <= args.rounds <= 10 and 1 <= args.duration <= 1800 and 1 <= args.warmup <= 60
    assert 1 <= args.client_threads <= len(client_set)
    if not args.validation:
        for binary in [args.rust, args.cpp]:
            symbols = subprocess.check_output(["nm", "-D", str(binary)], stderr=subprocess.DEVNULL)
            assert not any(marker in symbols for marker in [b"__asan_", b"__ubsan_", b"__tsan_"]), "Performance runs reject sanitizer binaries"
    os.sched_setaffinity(0, {args.driver_cpu})
    report = dict(complete=False, passed=False, purpose="validation_only" if args.validation else "performance", mode=args.mode, configuration={k: str(v) if isinstance(v, Path) else
        sorted(v) if isinstance(v, set) else v for k,v in vars(args).items()},
        environment=dict(kernel=platform.release(), machine=platform.machine(), python=platform.python_version()),
        binaries={label: dict(path=str(binary.resolve()), sha256=sha256(binary)) for label,binary in [("rust",args.rust),("cpp",args.cpp)]}, rows=[])
    inputs = [Path(__file__).resolve(), Path(__file__).with_name("workload.lua"),
              Path(__file__).with_name("core_benchmark.cpp"),
              Path(__file__).resolve().parents[2] / "archive/rust/oracle/src/bin/core_benchmark.rs"]
    report["driver_source_sha256"] = {str(path): sha256(path) for path in inputs}
    try:
        if args.mode == "core":
            cases = core_inputs(args.core_scale)
        elif args.mode == "soak":
            cases = [("MIX", "/download?size=1048576", 1048576, 64)]
        else:
            assert args.wrk and args.wrk.is_file()
            cases = [(method, f"/download?size={size}" if method == "GET" else "/upload", size, int(c))
                     for method in ["GET", "POST"] for size in [10240,102400,1048576] for c in args.concurrencies.split(",")]
            cases += [("GET", "/latency", 0, int(c)) for c in args.concurrencies.split(",")]
        checksums = {}
        for round_index in range(args.rounds):
            for case_index, case in enumerate(cases):
                order = [("rust",args.rust),("cpp",args.cpp)]
                if (round_index+case_index) % 2:
                    order.reverse()
                for label, binary in order:
                    name = case["operation"] if args.mode == "core" else str(case)
                    print(f"RUN {args.mode} round={round_index+1} {label} {name}", flush=True)
                    if args.mode == "core":
                        with tempfile.TemporaryDirectory(prefix="speedtest-core-benchmark-") as tmp:
                            stdout, usage = timed(["taskset", "-c", str(min(args.server_cpu_set)), str(binary.resolve())],
                                Path(tmp), dict(os.environ), json.dumps(case, ensure_ascii=False).encode())
                        measured = json.loads(stdout)
                        expected = (measured["checksum"], measured["warmup_checksum"])
                        if case_index in checksums:
                            assert checksums[case_index] == expected, (name, checksums[case_index], expected)
                        checksums[case_index] = expected
                        measured.update(operation=name, dataset=case["dataset"], ns_per_operation=measured["elapsed_ns"]/measured["iterations"], total_process_resources=usage)
                    else:
                        measured = http_case(args, binary, case)
                    report["rows"].append(dict(backend=label, round=round_index+1, case=case_index, **measured))
                    save(args.output, report)
                    print(f"PASS {label} {name}", flush=True)
        assert all(sha256(binary) == report["binaries"][label]["sha256"] for label,binary in [("rust",args.rust),("cpp",args.cpp)]), "Benchmark binary changed during measurement"
        assert all(sha256(Path(path)) == digest for path,digest in report["driver_source_sha256"].items()), "Benchmark source changed during measurement"
        report.update(complete=True, passed=True)
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        save(args.output, report)
