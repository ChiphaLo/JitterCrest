# Recovery-source performance findings

> **Historical record.** This report describes the source, environment and policy of its own date during the October 2026 migration. Rust has since been archived and C++ is the only maintained backend; see the [documentation index](README.md) for current guides.

Measured runtime source: `019ff35821f974d80ab223e0fed4f19f7b01a6fb`.
This supersedes earlier snapshots only for the current implementation's measured
performance, not their historical evidence. See [complete local tables](local-performance-recovery.md)
and [independent Internet profile](network-performance-recovery.md).

## Local observations

Five core rounds, three HTTP rounds, and one five-minute mixed run per backend
were collected serially without overlapping builds or browser tests. Both used
Release/ThinLTO, the same Linux OS, matched two-CPU server budget and the same
native generator. HTTP completed 15,292,678 requests; mixed load 2,305,566; no
workload errors. Actual 1/16/64/128 open backend connections were observed and
released. This is finite stability evidence, not an assertion of maximum capacity
or a24-hour soak. Full toolchain, client CPU, latency ranges and CPU accounting
are in the tables.

Rust had higher median throughput in **all 12 download and all 12 upload cases**.
Small-payload upload c16 ranges overlapped; do not treat every individual
comparison as statistically significant.

| Local 1 MiB payload / c64 | Rust Gbit/s | C++ Gbit/s |
|---|---:|---:|
| Download | 40.892 | 31.029 |
| Upload | 36.223 | 26.360 |

These are loopback application rates, not NIC/Internet rates. At c128, download
cost 0.421 versus 0.512 CPU-seconds/GiB, upload 0.479 versus 0.718, Rust versus C++.
At saturation both consume about two logical cores; raw CPU percentage alone
would hide the completed-work efficiency difference.

C++ had 1.17–1.31 times the bodyless latency request rate and lower c128 P99
(3.403ms versus Rust 6.047ms). Payload P99 is mixed: at c128 C++ download was
37.775ms versus 49.584ms, but upload 55.819ms versus 48.013ms. Different completed
rates and closed-loop histograms do not establish equal-arrival-rate SLOs.

Rust used less idle RSS (about 7.6–8.2MiB versus 15.2–15.5MiB). C++ used less
large-payload high-load peak RSS: at c128 download 21.07 versus 84.57MiB,
upload 27.84 versus 125.77MiB, C++ versus Rust. During mixed load C++ peaked at
21.57MiB versus 101.22MiB, but delivered 23.633 versus 40.816Gbit/s and had
43.961 versus 30.382ms P99. This is a throughput/memory tradeoff, not uniform
resource superiority.

Core frame decoding was close with overlapping ranges (5.370 versus 5.350ns/op,
C++ versus Rust); C++ ASCII/Unicode ID hashing took 2.80/2.63 times as long;
512-sample P95 took about 18% less time. The matched checksums do not make these
four operations a whole-service language score.

## Interpretation and replacement decision

No population-level significance claim is made from three/five alternating
rounds. WSL guest affinity does not provide exclusive physical-core ownership.
Compiler/runtime, generator, host scheduling, proxy and WAN remain factors.
The separately bounded Internet samples must not be used to infer a
language-only throughput advantage or benchmark native RTC performance.

**There is no measured throughput/CPU/maintenance justification for replacing
Rust as the default implementation.** C++ has selective lightweight-request and
high-load RSS advantages, but adds native dependency patches, callback/lifetime
reviews, independent sanitizer builds and ongoing upstream-patch maintenance.
An explicitly authorized deployment is an operational choice, not a claim that
C++ is faster. Preserve the independent Rust source, release and rollback path.

Compatibility, safety, historical failures and finite acceptance boundaries are
in the [objective audit](acceptance-audit.md). No metrics/deadlines/gates were
weakened to produce favorable results.
