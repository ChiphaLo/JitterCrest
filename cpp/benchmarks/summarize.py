"""Publish aggregate scientific results, never private paths or process logs."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def load(path):
    report = json.loads(path.read_text())
    assert report["complete"] and report["passed"] and report["purpose"] == "performance", path
    return report


def grouped(report):
    groups = defaultdict(lambda: defaultdict(list))
    for row in report["rows"]:
        groups[row["case"]][row["backend"]].append(row)
    for pair in groups.values():
        assert pair["rust"] and pair["cpp"]
        assert {r["round"] for r in pair["rust"]} == {r["round"] for r in pair["cpp"]}
    return groups


def median(rows, key):
    return statistics.median(row[key] for row in rows)


def spread(rows, key, scale=1):
    values = [row[key]/scale for row in rows]
    return f"{statistics.median(values):.3f} [{min(values):.3f}, {max(values):.3f}]"


def label(row):
    if row["path"] == "/latency":
        return f"Latency / c={row['concurrency']}"
    return f"{row['method']} {row['payload_bytes']//1024} KiB / c={row['concurrency']}"


def summary(core, http, soak, capacity=None):
    lines = ["# Local Rust/C++ performance measurements", "",
             "These are measured local profiles, not proof of universal language superiority or production readiness.", "",
             "## Method and scope", "",
             f"- Runtime: Linux {http['environment']['kernel']}, {http['environment']['machine']}; Python {http['environment']['python']} orchestrator, native wrk client.",
             "- Both backend executables use Release optimization and ThinLTO, generic x86-64 targets, no PGO, fast-math or `-march=native`.",
             "- Reference Rust: rustc 1.98.1 / LLVM 22.1.8, cross-built with cargo-zigbuild 0.23.4 and Zig 0.16.0 for Linux GNU 2.36; runs on the same Linux OS as C++.",
             "- C++: Clang/LLD 18.1.3; Boost 1.83, OpenSSL 3.0.13, nlohmann-json 3.11.3, libcurl 8.5.0 and explicitly patched libdatachannel 0.24.6.",
             f"- Server CPU mask: `{http['configuration']['server_cpus']}`; client CPU mask: `{http['configuration']['client_cpus']}`; orchestrator CPU: `{http['configuration']['driver_cpu']}`. These are WSL guest CPU IDs; host scheduling is not physically isolated.",
             "- Both servers have the same two-CPU budget. C++ IO threads=2 and Tokio workers=2; actual total OS thread counts can differ by architecture and are measured, not claimed identical.",
             f"- HTTP: {http['configuration']['rounds']} alternating AB/BA rounds per matched case; {http['configuration']['warmup']} s warmup then {http['configuration']['duration']} s measurement, fresh process per case. Persistent HTTP/1 connections, up to 1000 requests per socket on both.",
             "- Original limiter/quota code remains enabled. Both use the same loopback-only trusted-proxy test profile and benchmark-range synthetic client identities; no live configuration or public listener is changed.",
             "- GET request caches use 1024 identities/worker; upload caches are bounded to 16 MiB/worker with at least 16 identities. Every preflight checks the real endpoint status/byte count; any socket/status/timeout error fails a workload rather than inflating throughput.",
             "- Native RTC is off in the HTTP throughput profile. It is separately tested for protocol/safety; these HTTP numbers do not benchmark ICE/SCTP or browser rendering.",
             "- CPU is server-process user+system time from Linux /proc, normalized to one logical core (two-CPU budget can approach 200%). Peak RSS is the kernel high-water mark including startup/warmup; idle RSS is before warmup. CPU cost per GiB is needed because a slower saturated server can use less CPU simply by doing less work.",
             "- wrk latency percentiles are its corrected closed-loop histogram values, not an open-loop arrival-SLO test. Tables show medians of per-run percentiles, NOT pooled percentiles.",
             "- Brackets show the observed min/max across rounds. This small sample is not a statistical significance test; overlapping/non-overlapping ranges are descriptive, not general population confidence intervals.",
             "- No real Internet end-to-end performance result is supplied by this local run. Browser/native RTC correctness over loopback/TLS is a separate result, not Internet throughput evidence.", "",
             "## Native core (no JSON/network I/O inside the timed loop)", "",
             f"{core['configuration']['rounds']} alternating rounds, identical seeded inputs/iteration counts and 10% in-process warmup. Both drivers consume matching checksums, dispatch outside the hot loop and use equivalent standard compiler fences around timing. Frame decoding consumes all decoded fields; it does not use a removable encode/decode inverse loop. Loop/index/checksum overhead is included. GNU-time whole-process resources additionally include input preparation/warmup, so they are not presented as isolated hot-loop CPU cost.", "",
             "| Operation / dataset | Rust ns/op median [min, max] | C++ ns/op median [min, max] | C++ / Rust |", "|---|---:|---:|---:|"]
    for pair in grouped(core).values():
        rust, cpp = pair["rust"], pair["cpp"]
        assert {row["checksum"] for row in rust} == {row["checksum"] for row in cpp}
        lines.append(f"| {rust[0]['operation']} / {rust[0]['dataset']} | {spread(rust,'ns_per_operation')} | {spread(cpp,'ns_per_operation')} | {median(cpp,'ns_per_operation')/median(rust,'ns_per_operation'):.3f} |")
    lines += ["", "## HTTP payload throughput / request rate", "",
              "Payload Mbit/s counts successful application bytes (request payload for upload), not headers or compressed wire bytes. Latency rows use requests/s. A wrk client ceiling can limit a result; these values are not asserted to be absolute backend capacity.", "",
              "| Workload | Metric | Rust median [min, max] | C++ median [min, max] | C++ / Rust |",
              "|---|---|---:|---:|---:|"]
    groups = grouped(http)
    for pair in groups.values():
        rust, cpp = pair["rust"], pair["cpp"]
        key = "payload_mbps" if rust[0]["payload_bytes"] else "requests_per_second"
        lines.append(f"| {label(rust[0])} | {'Mbit/s' if key=='payload_mbps' else 'requests/s'} | {spread(rust,key)} | {spread(cpp,key)} | {median(cpp,key)/median(rust,key):.3f} |")
    lines += ["", "## HTTP latency (milliseconds)", "",
              "| Workload | Rust P50 / P95 / P99 | C++ P50 / P95 / P99 |",
              "|---|---:|---:|"]
    for pair in groups.values():
        rust, cpp = pair["rust"], pair["cpp"]
        text = lambda rows: " / ".join(f"{median(rows,key):.3f}" for key in ["p50_ms","p95_ms","p99_ms"])
        lines.append(f"| {label(rust[0])} | {text(rust)} | {text(cpp)} |")
    lines += ["", "## Server resources", "",
              "Idle / peak columns are median MiB. CPU cost is seconds/GiB for payload cases or microseconds/request for latency. Total thread counts are the observed architectural cost, under the same CPU budget.", "",
              "| Workload | Rust CPU % / cost | C++ CPU % / cost | Rust idle / peak MiB | C++ idle / peak MiB | Rust / C++ threads |",
              "|---|---:|---:|---:|---:|---:|"]
    def cost(rows):
        return statistics.median(r["server_cpu_seconds"]/(r["requests"]*r["payload_bytes"]/(1<<30)) if r["payload_bytes"] else
                                 r["server_cpu_seconds"]*1e6/r["requests"] for r in rows)
    for pair in groups.values():
        rust, cpp = pair["rust"], pair["cpp"]
        lines.append(f"| {label(rust[0])} | {median(rust,'server_cpu_percent_one_core'):.1f} / {cost(rust):.3f} | {median(cpp,'server_cpu_percent_one_core'):.1f} / {cost(cpp):.3f} | {median(rust,'idle_rss_bytes')/(1<<20):.2f} / {median(rust,'peak_rss_bytes')/(1<<20):.2f} | {median(cpp,'idle_rss_bytes')/(1<<20):.2f} / {median(cpp,'peak_rss_bytes')/(1<<20):.2f} | {median(rust,'server_threads'):.0f} / {median(cpp,'server_threads'):.0f} |")
    lines += ["", "## Generator utilization / errors", "",
              "Generator CPU below is user+system time / observed wall, as percent of one core. It has up to four CPUs (one thread for c=1). High generator utilization is a warning against interpreting a throughput ceiling as solely server capacity.", "",
              "| Workload | Rust-run client CPU % | C++-run client CPU % | Total completed requests | Socket/status/timeout errors |",
              "|---|---:|---:|---:|---:|"]
    for pair in groups.values():
        rust, cpp = pair["rust"], pair["cpp"]
        client = lambda rows: statistics.median(r["client_resources"]["cpu_seconds"]*100/r["observed_wall_seconds"] for r in rows)
        errors = sum(sum(r["errors"].values()) for r in rust+cpp)
        assert errors == 0
        lines.append(f"| {label(rust[0])} | {client(rust):.1f} | {client(cpp):.1f} | {sum(r['requests'] for r in rust+cpp)} | {errors} |")
    lines += ["", "## Sustained mixed upload/download", ""]
    if soak:
        lines += [f"One {soak['configuration']['duration']}-second run per backend, {soak['configuration']['warmup']}-second warmup, 64 connections, alternating cached 1 MiB GET/POST requests. Payload rate is their aggregate, not separately asserted upload and download rates. This is a five-minute local stability observation, not a 24-hour soak or a leak proof.", "",
                  "| Backend | Aggregate Mbit/s | CPU % | Peak RSS MiB | First / last 30 s median RSS MiB | P50 / P95 / P99 ms | Errors |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in soak["rows"]:
            timeline = r["rss_timeline"]
            first = statistics.median(p["rss_bytes"] for p in timeline if p["seconds"] <= 30)/(1<<20)
            last = statistics.median(p["rss_bytes"] for p in timeline if p["seconds"] >= r["observed_wall_seconds"]-30)/(1<<20)
            errors = sum(r["errors"].values())
            assert errors == 0
            lines.append(f"| {r['backend']} | {r['payload_mbps']:.3f} | {r['server_cpu_percent_one_core']:.1f} | {r['peak_rss_bytes']/(1<<20):.2f} | {first:.2f} / {last:.2f} | {r['p50_ms']:.3f} / {r['p95_ms']:.3f} / {r['p99_ms']:.3f} | {errors} |")
    else:
        lines += ["Not yet measured; no sustained-stability claim is made."]
    lines += ["", "## Observed simultaneous connections", ""]
    if capacity:
        assert capacity["passed"] and capacity["processes_exited"]
        lines += ["Separate correctness/capacity observation after performance collection: actual backend-owned ESTABLISHED sockets counted from Linux /proc, not just a client concurrency target. Each connection completes a health request and remains open; all must release after client close. This demonstrates the tested counts, not a maximum connection capacity.", "",
                  "| Backend | Requested / observed connections | Idle / active / peak RSS MiB | Threads | All released |",
                  "|---|---:|---:|---:|---|"]
        for backend in ["rust", "cpp"]:
            rows = capacity["results"][backend]
            assert [r["requested"] for r in rows] == [1, 16, 64, 128]
            for r in rows:
                assert r["requested"] == r["accepted_established"] and r["released_connections"]
                lines.append(f"| {backend} | {r['requested']} / {r['accepted_established']} | {r['idle_rss_bytes']/(1<<20):.2f} / {r['active_rss_bytes']/(1<<20):.2f} / {r['peak_rss_bytes']/(1<<20):.2f} | {r['threads']} | yes |")
    else:
        lines += ["Not separately observed; load-generator targets alone do not prove accepted socket counts."]
    lines += ["", "## Artifact identity", "", "Private paths, process IDs and raw captures are deliberately not published. Executable SHA256 values identify the measured builds:", ""]
    for kind, report in [("HTTP",http),("core",core)]:
        for backend, value in report["binaries"].items():
            lines.append(f"- {kind} {backend}: `{value['sha256']}`")
    lines += ["", "## Interpretation boundary", "",
              "These tables compare these implementations, compiler/dependency versions, tested profiles and hardware conditions—not an intrinsic property of C++ versus Rust. CPU-at-saturation alone is not efficiency; use CPU cost per unit of completed work. Memory and tail latency can trade against throughput. Small sample counts and WSL/host scheduling prevent broad statistical-significance claims.", "",
              "Production remains Rust. Remaining protocol/resource/static-analysis/agent/probe-only/deployment acceptance must be satisfied separately. No measured local speed difference, by itself, authorizes a production replacement.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--core", type=Path, required=True)
    p.add_argument("--http", type=Path, required=True)
    p.add_argument("--soak", type=Path)
    p.add_argument("--capacity", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.write_text(summary(load(args.core), load(args.http), load(args.soak) if args.soak else None,
                                   json.loads(args.capacity.read_text()) if args.capacity else None))
