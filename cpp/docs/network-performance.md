# Real-network HTTP comparison — 2026-10-26

This is a bounded Internet experiment between separate authorized client and
server hosts, not loopback, a frontend screenshot, or a language-only benchmark.
It completes the real-network HTTP measurement milestone, **not** full release
acceptance. Probe-agent control failures remain in
[public-staging-validation.md](public-staging-validation.md).

## Matched setup

- One Debian 13 x86-64 server guest, glibc 2.41, one logical vCPU presenting an
  Intel Xeon Gold 6133. Both independent backends and the same copied Caddy ran
  under matching temporary systemd hardening, CPU affinity, memory/task budgets
  and loopback HTTP listener profiles. Existing production services remained
  running: this was not exclusive bare metal or an idle-co-tenant guarantee.
- A separate Debian 13 client guest presenting an AMD EPYC 7K62 ran a dedicated
  Python stdlib HTTPS generator in a read-only-root, capability-dropped,
  no-new-privileges Docker container, with a two-CPU quota and 256 MiB memory cap.
  No browser/GPU or Rust service/FFI wrapper was used by this generator.
- Both servers used generic x86-64 Release/ThinLTO, without PGO, fast-math or
  one-sided architecture tuning. The unchanged Rust reference was built with
  rustc 1.98.1/LLVM 22.1.8. C++ used Clang 18.1.3/LLD 18, Boost 1.83 and the
  explicitly patched libdatachannel dependency documented elsewhere. Bundled
  dependency libraries remained identical across all C++ cases. This is a new
  transport-fixed artifact, not a relabeling of the frozen local benchmark binary.
- Each comparison used the same public IP-SAN HTTPS test endpoint and proxy
  executable/configuration. The isolated proxy selected one independent backend
  at a time; production Caddy was never changed. A private ephemeral CA and
  ordinary hostname/IP verification stayed enabled; a temporary authorization
  header was stripped by the test proxy. No routing or proxy-policy repair was
  applied to either client machine.
- Three AB/BA/AB rounds; `/latency` plus 1 MiB GET/POST; concurrency 1 and 4;
  two-second measurement-admission windows, 0.5-second per-worker warmup and
  preconnected keepalive clients. Last admitted requests were drained and their
  drain time included in goodput. Limits/quota enforcement stayed present under
  the same explicitly provisioned benchmark configuration for both servers.
- Aggregate attempted application payload, including warmup, was capped at
  2 GiB. Actual attempted payload was **1,542 MiB**, not an exact TLS/wire-byte
  accounting. All **36 cases / 2,465 measured requests** succeeded with no workload
  status, byte-count, timeout or socket errors in this completed experiment.
  Sources and artifact SHA-256 values stayed unchanged throughout; all 86
  protected Rust/frontend/vendor baseline files still matched their manifests.

## Measurements

Each value is the median of three runs for that exact backend/workload, not a
pooled percentile. Brackets are the observed min–max across the three runs.
Payload rates are application Mbit/s; zero-payload latency tests instead report
request rate. Percentiles are nearest-rank benchmark **request** latencies,
not the application's probe percentile algorithm.

| Workload | c | Backend | Payload Mbit/s [min–max] | req/s | P50 ms | P95 ms | P99 ms [min–max] | Measured requests, 3 runs |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| Latency 204 | 1 | rust | — | 17.60 | 56.767 | 57.250 | 59.570 [57.400–61.350] | 106 |
| Latency 204 | 1 | cpp | — | 18.13 | 55.088 | 55.347 | 57.223 [55.233–61.501] | 107 |
| Latency 204 | 4 | rust | — | 68.30 | 56.583 | 61.401 | 61.883 [61.786–63.788] | 417 |
| Latency 204 | 4 | cpp | — | 68.18 | 57.051 | 61.280 | 62.738 [61.682–63.633] | 416 |
| Download 1 MiB | 1 | rust | 91.242 [90.442–100.085] | 10.88 | 91.851 | 92.776 | 95.551 [87.030–96.224] | 68 |
| Download 1 MiB | 1 | cpp | 100.337 [97.749–100.371] | 11.96 | 83.343 | 85.250 | 87.311 [87.005–89.285] | 72 |
| Download 1 MiB | 4 | rust | 385.803 [371.339–390.710] | 45.99 | 83.836 | 92.778 | 102.260 [93.083–102.997] | 283 |
| Download 1 MiB | 4 | cpp | 383.280 [376.546–388.045] | 45.69 | 85.384 | 94.584 | 98.976 [96.057–101.570] | 282 |
| Upload 1 MiB | 1 | rust | 91.455 [91.445–97.899] | 10.90 | 91.387 | 92.503 | 95.331 [88.992–95.972] | 68 |
| Upload 1 MiB | 1 | cpp | 100.764 [97.263–100.978] | 12.01 | 83.076 | 84.136 | 86.613 [84.488–93.358] | 74 |
| Upload 1 MiB | 4 | rust | 387.888 [381.951–389.866] | 46.24 | 85.386 | 91.655 | 95.214 [95.171–95.431] | 286 |
| Upload 1 MiB | 4 | cpp | 383.010 [381.626–391.200] | 45.66 | 85.651 | 92.138 | 95.488 [94.963–95.602] | 286 |

### Process resources

Remote `/proc` snapshots identify stable PIDs/start ticks, CPU time, current RSS
and lifetime high-water RSS. CPU ticks have **10 ms** resolution. The independent
snapshot interval includes two SSH/synchronization gaps around the client
measurement; it is not exactly the client admission window. Low absolute CPU
values, background runtime activity and shared-host scheduling limit rankings.
Caddy CPU is reported separately, not charged only to one backend.

| Backend | Idle RSS MiB | Maximum process-lifetime HWM MiB | Sum of backend CPU seconds, 18 measured intervals | Sum of proxy CPU seconds, same intervals | Observed initial threads |
|---|---:|---:|---:|---:|---:|
| rust | 7.371 | 11.641 | 1.26 | 2.67 | 3 |
| cpp | 15.719 | 16.484 | 1.58 | 2.74 | 12 |

Lifetime HWM is not a per-case peak or a leak test. Both backend thread sets had
the same single-guest-CPU affinity. Different native runtime thread counts are
recorded, not silently normalized away. Client CPU and lifetime high-water RSS
were also retained privately; none of these observations proves NIC saturation.

## Limits and earlier failed attempts

Earlier WSL-client experiments were retained separately. Two aborted during
preconnect/warmup; instrumentation preserved an `SSLEOFError` instead of masking
it as only a barrier error. A separate 36-case WSL experiment produced responses
but failed its source-freeze guard when CLI-only source was edited during the
run; it is **excluded** from the accepted comparison, not promoted after the fact.
The independent-client experiment above froze inputs from start to finish.
Changing client vantage does not fix or erase the original WSL path failures.

A later attempt was refused before upload/start because accumulated duplicate
staging extractions had reduced available disk space. Every redundant file was
verified against its retained recovery archive, active-process maps were checked,
one complete extraction and all diagnostics were preserved, and duplicate
extractions were reclaimed. Identical compressed archives were deduplicated
without changing their paths/content. Free space recovered from about 128 MiB
to 1,347 MiB before the final run; a larger staging reserve is now enforced.
No production artifact, backup or unrelated service was cleaned. Temporary test
units/listeners, the generator container and test secrets were removed; both
hosts' production process/unit/configuration/release snapshots still matched.

These two-second closed-loop cases have small per-run sample counts: P99 is
often near the maximum observed request. They are not corrected for coordinated
omission, an open-loop overload test, a long-running network soak or evidence of
population-level statistical significance. Client, WAN, TLS and proxy effects
remain inseparable here. Do not subtract RTT and call the remainder language CPU.

## Interpretation and reproducibility

At c=4 the two implementations overlap around 380–390 Mbit/s for these payloads.
C++ had higher single-connection medians in this particular path, but the small
sample, client/path limits and different native stacks preclude attributing the
difference to language. Rust had lower observed idle/lifetime RSS and lower
summed backend CPU in this bounded low-concurrency experiment; this does not
contradict C++'s high-concurrency RSS advantage in the separate local saturation
measurements. No general language winner or production replacement justification
follows. See [local-performance.md](local-performance.md) and
[performance-analysis.md](performance-analysis.md) for the independent local data.

The public client and unit tests are
`benchmarks/network_benchmark.py` and `benchmarks/network_benchmark_test.py`;
[benchmark instructions](../benchmarks/README.md) explain the HTTPS CLI and
resource-observation hook. Target service administration, credentials, real node
configuration and raw diagnostics remain private. The standalone client does not
pretend to measure remote CPU/RSS without an independent trusted collector.
