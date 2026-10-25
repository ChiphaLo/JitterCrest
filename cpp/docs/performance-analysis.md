# Local performance findings — 2026-10-26

Full measured tables, min/max ranges, CPU cost, client utilization, RSS and binary
hashes are in [local-performance.md](local-performance.md). Reproduction commands
and caveats are in [../benchmarks/README.md](../benchmarks/README.md).

## Experiment and checks

The same Ubuntu 24.04 WSL2 instance ran both independent executables on an
AMD Ryzen 9 3900 (12 cores / 24 logical CPUs), with two guest CPUs per backend,
separate load-generator CPUs and no competing build/test load. WSL GPU/GUI
support was disabled. Guest affinity is not exclusive physical-core isolation
from Windows host scheduling; this is not a bare-metal laboratory result.

Both builds use Release/ThinLTO, generic x86-64, no PGO or fast-math. The original
Rust service and runtime sources are unchanged. Compiler/dependency differences
remain part of this implementation comparison, not controlled language-only
variables. Exact observed distribution package revisions:

- Boost 1.83.0-2.1ubuntu3.2
- OpenSSL 3.0.13-0ubuntu3.16
- libcurl 8.5.0-2ubuntu10.16
- nlohmann-json 3.11.3-1
- glibc 2.39-0ubuntu8.9
- wrk 4.1.0-4build2 (native C/LuaJIT generator)

Formal collection completed:

- Native core: five alternating rounds, four matched workloads, **40 rows**;
  matching checksums, warmup checksums and iteration counts.
- HTTP: three alternating rounds, 28 matched workloads, **168 rows** and
  **16,763,653 completed requests**; zero socket/status/timeout errors.
- Sustained mixed traffic: one **300-second** measurement per backend,
  **2,428,837 completed requests** combined; zero errors, clean process exits.
- Separate accepted-connection check: both backends really held 1/16/64/128
  backend-owned ESTABLISHED sockets and released them to zero after client close.
  This does not establish maximum capacity or exhaustion safety.
- Workload and executable hashes stayed unchanged throughout collection. All
  86 protected Rust/source/frontend/vendor baseline files stayed unchanged.
- The measured Release/LTO profile passed seven CTests and the earlier shared
  3,065-vector core / 126-case HTTP contracts. Five deterministic reporting tests
  passed, including invalid/incomplete report rejection and redaction checks.

## Findings, without cherry-picking

### Native computation

Frame decoding is close: Rust 5.280 ns/op versus C++ 5.318 ns/op; observed ranges
overlap. No meaningful superiority is established by that difference.

ID hashing favors the reference Rust implementation: C++ takes 2.788 times the
ASCII-ID time and 2.639 times the Unicode-ID time. The 512-sample P95 calculation
favors C++ in this dataset: 5,018 ns versus Rust 6,162 ns (about 19% less time).
These are specific operations, not a whole-service speed score.

### HTTP throughput and CPU efficiency

Rust's median payload throughput is higher in **all 12 download cases** and
**11 of 12 upload cases**. The exception is the 10 KiB / c=16 upload, where C++
is about 4% higher; that isolated result does not overturn the overall pattern.

Representative 1 MiB / c=64 payload rates:

| Direction | Rust Gbit/s | C++ Gbit/s |
|---|---:|---:|
| Download | 44.568 | 36.552 |
| Upload | 36.493 | 30.114 |

These are **loopback application-payload rates**, not NIC or Internet bandwidth.
The 1 MiB / c=1 upload has a particularly large gap (Rust 19.812 versus C++
5.655 Gbit/s). Its cause is not yet profiled; no network, buffering or language
root cause is asserted. The baseline is retained instead of tuning only C++
and replacing unfavorable measurements.

CPU percentage at saturation alone is misleading. For example, 1 MiB / c=128
download costs approximately 0.39 CPU-seconds/GiB in Rust versus 0.45 in C++;
upload approximately 0.47 versus 0.62. The complete report gives every case,
including generator CPU: a busy client can also limit observed throughput.

### Latency and memory tradeoffs

The bodyless `/latency` 204-response workload favors C++ at all four concurrency
levels: about 1.19–1.33 times Rust's request rate, with lower measured P99. At
c=128, medians are 56,483 versus 43,166 requests/s and 3.04 versus 5.40 ms P99.
This does not imply C++ has better tails for all upload/download workloads.
Both better and worse payload P99 cases appear in the complete table, under
different completed throughput. Corrected closed-loop histograms do not prove
open-loop overload/SLO behavior.

Rust uses less idle RSS (roughly 8 MiB versus C++ roughly 15–16 MiB). With 1 MiB
payloads and c=128, C++ has a much smaller measured peak RSS: download 21.24
versus Rust 85.19 MiB; upload 27.91 versus Rust 118.86 MiB. This is a specific
throughput/memory tradeoff, not a blanket claim of lower C++ memory. Measured
total threads were Rust 3 / C++ 6 under the same two-CPU execution budget.

### Five-minute observation

| Mixed 1 MiB GET/POST, c=64 | Rust | C++ |
|---|---:|---:|
| Aggregate payload Gbit/s | 41.847 | 26.049 |
| Server CPU % of one logical core | 198.9 | 199.2 |
| Peak RSS MiB | 98.46 | 21.77 |
| First / last 30 s median RSS MiB | 90.16 / 93.64 | 21.39 / 21.34 |
| P99 ms | 29.454 | 39.210 |
| Errors | 0 | 0 |

This single local run per backend supplies actual sustained-load evidence, not
a leak proof or a 24-hour stability claim. The small Rust RSS change is not
diagnosed as a leak. Aggregate mixed payload rate is not mislabeled separately
as upload and download rate.

## Decision and outstanding work

There is **no current performance basis to replace production Rust with C++**.
Rust currently leads the tested payload throughput/CPU-efficiency cases;
C++ has useful lightweight-request and high-concurrency RSS advantages, but
also higher idle/thread overhead and additional C/C++ dependency safety and
maintenance burden. Local results are descriptive; three/five rounds and WSL
noise do not justify a population-level significance claim.

Real-network end-to-end benchmarking, remaining native RTC resource/timeouts,
successful publicly mapped probe-only acceptance, two unresolved coroutine
static-analysis diagnostics, a complete staging bundle and final acceptance
audit remain open. Production routes, services and historical archives are
untouched. This is a completed **local benchmark milestone**, not completion of
the full C++ refactor or authorization for a cutover.

Subsequent local central-UI-to-separate-agent HTTPS IP and finite HTTP resource
acceptance is recorded in `agent-resource-validation.md`; it does not alter the
measurements or the performance conclusion above.
