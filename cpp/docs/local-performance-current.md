# Receive-fairness snapshot: local Rust/C++ performance measurements

Measured on 2026-10-26 from current runtime source cd514ed (source checkout 7965744). These are fresh reports, not renamed historical measurements. The independent measured ELF and packaged UI ELF have identical application .text bytes; complete ELF hashes differ with link/runtime paths. Native library prefixes have separate recorded identities and the same reproduced patches; their raw bytes are not claimed identical.

This pinned snapshot precedes the later native message-cap correction. RTC was
disabled in the timed HTTP profile; these executable hashes must not be labeled
as those of a later binary. The before/after correction and its separate native
acceptance checks are recorded in `transport-compatibility.md`.

These are measured local profiles, not proof of universal language superiority or production readiness.

## Method and scope

- Runtime: Linux 6.18.33.2-microsoft-standard-WSL2, x86_64; Python 3.12.3 orchestrator, native wrk client.
- Both backend executables use Release optimization and ThinLTO, generic x86-64 targets, no PGO, fast-math or `-march=native`.
- Reference Rust: rustc 1.98.1 / LLVM 22.1.8, cross-built with cargo-zigbuild 0.23.4 and Zig 0.16.0 for Linux GNU 2.36; runs on the same Linux OS as C++.
- C++: Clang/LLD 18.1.3; Boost 1.83, OpenSSL 3.0.13, nlohmann-json 3.11.3, libcurl 8.5.0 and explicitly patched libdatachannel 0.24.6.
- Server CPU mask: `0,2`; client CPU mask: `4,6,8,10`; orchestrator CPU: `12`. These are WSL guest CPU IDs; host scheduling is not physically isolated.
- Both servers have the same two-CPU budget. C++ IO threads=2 and Tokio workers=2; actual total OS thread counts can differ by architecture and are measured, not claimed identical.
- HTTP: 3 alternating AB/BA rounds per matched case; 1 s warmup then 5 s measurement, fresh process per case. Persistent HTTP/1 connections, up to 1000 requests per socket on both.
- Original limiter/quota code remains enabled. Both use the same loopback-only trusted-proxy test profile and benchmark-range synthetic client identities; no live configuration or public listener is changed.
- GET request caches use 1024 identities/worker; upload caches are bounded to 16 MiB/worker with at least 16 identities. Every preflight checks the real endpoint status/byte count; any socket/status/timeout error fails a workload rather than inflating throughput.
- Native RTC is off in the HTTP throughput profile. It is separately tested for protocol/safety; these HTTP numbers do not benchmark ICE/SCTP or browser rendering.
- CPU is server-process user+system time from Linux /proc, normalized to one logical core (two-CPU budget can approach 200%). Peak RSS is the kernel high-water mark including startup/warmup; idle RSS is before warmup. CPU cost per GiB is needed because a slower saturated server can use less CPU simply by doing less work.
- wrk latency percentiles are its corrected closed-loop histogram values, not an open-loop arrival-SLO test. Tables show medians of per-run percentiles, NOT pooled percentiles.
- Brackets show the observed min/max across rounds. This small sample is not a statistical significance test; overlapping/non-overlapping ranges are descriptive, not general population confidence intervals.
- No real Internet end-to-end performance result is supplied by this local run. Browser/native RTC correctness over loopback/TLS is a separate result, not Internet throughput evidence.

## Native core (no JSON/network I/O inside the timed loop)

5 alternating rounds, identical seeded inputs/iteration counts and 10% in-process warmup. Both drivers consume matching checksums, dispatch outside the hot loop and use equivalent standard compiler fences around timing. Frame decoding consumes all decoded fields; it does not use a removable encode/decode inverse loop. Loop/index/checksum overhead is included. GNU-time whole-process resources additionally include input preparation/warmup, so they are not presented as isolated hot-loop CPU cost.

| Operation / dataset | Rust ns/op median [min, max] | C++ ns/op median [min, max] | C++ / Rust |
|---|---:|---:|---:|
| decode / 128-byte frames, 1/8 invalid length | 5.335 [5.313, 5.360] | 5.320 [5.315, 5.355] | 0.997 |
| hash / ASCII IDs | 31.351 [31.109, 33.523] | 87.232 [86.783, 91.024] | 2.782 |
| hash / Unicode IDs | 46.239 [44.559, 50.381] | 121.782 [119.641, 123.289] | 2.634 |
| percentile / P95, 512 samples | 6262.370 [6190.560, 6332.645] | 5234.973 [4971.362, 5339.030] | 0.836 |

## HTTP payload throughput / request rate

Payload Mbit/s counts successful application bytes (request payload for upload), not headers or compressed wire bytes. Latency rows use requests/s. A wrk client ceiling can limit a result; these values are not asserted to be absolute backend capacity.

| Workload | Metric | Rust median [min, max] | C++ median [min, max] | C++ / Rust |
|---|---|---:|---:|---:|
| GET 10 KiB / c=1 | Mbit/s | 596.536 [586.818, 597.247] | 416.471 [407.901, 418.526] | 0.698 |
| GET 10 KiB / c=16 | Mbit/s | 2573.302 [2508.510, 2728.049] | 2251.711 [2228.973, 2257.870] | 0.875 |
| GET 10 KiB / c=64 | Mbit/s | 2684.882 [2549.062, 2702.790] | 2215.431 [2203.224, 2332.215] | 0.825 |
| GET 10 KiB / c=128 | Mbit/s | 2728.453 [2664.017, 2747.523] | 2339.554 [2291.850, 2445.165] | 0.857 |
| GET 100 KiB / c=1 | Mbit/s | 4204.207 [4198.343, 4268.039] | 3183.610 [3173.955, 3229.231] | 0.757 |
| GET 100 KiB / c=16 | Mbit/s | 19302.024 [19268.873, 19704.551] | 14972.516 [14747.424, 15815.884] | 0.776 |
| GET 100 KiB / c=64 | Mbit/s | 20385.915 [19511.204, 20472.934] | 17026.388 [16476.040, 17036.082] | 0.835 |
| GET 100 KiB / c=128 | Mbit/s | 19798.666 [19459.070, 20078.231] | 17415.110 [16212.462, 17444.014] | 0.880 |
| GET 1024 KiB / c=1 | Mbit/s | 17689.993 [16270.361, 17779.782] | 11531.375 [11338.980, 11904.895] | 0.652 |
| GET 1024 KiB / c=16 | Mbit/s | 43207.409 [42022.179, 43609.655] | 28315.801 [27190.744, 29205.285] | 0.655 |
| GET 1024 KiB / c=64 | Mbit/s | 42293.749 [41321.342, 43421.740] | 30565.739 [30114.470, 35666.287] | 0.723 |
| GET 1024 KiB / c=128 | Mbit/s | 41637.305 [38296.905, 43543.007] | 32756.402 [31230.113, 36653.716] | 0.787 |
| POST 10 KiB / c=1 | Mbit/s | 542.340 [539.784, 566.777] | 499.315 [498.003, 499.904] | 0.921 |
| POST 10 KiB / c=16 | Mbit/s | 2525.885 [2448.626, 2634.971] | 2609.289 [2490.586, 2675.080] | 1.033 |
| POST 10 KiB / c=64 | Mbit/s | 2810.951 [2688.435, 2884.839] | 2632.525 [2580.500, 2747.033] | 0.937 |
| POST 10 KiB / c=128 | Mbit/s | 2701.598 [2575.109, 2747.092] | 2544.991 [2482.941, 2691.709] | 0.942 |
| POST 100 KiB / c=1 | Mbit/s | 4405.733 [4346.867, 4408.901] | 2620.130 [2574.233, 2627.860] | 0.595 |
| POST 100 KiB / c=16 | Mbit/s | 21157.428 [20798.235, 21716.020] | 15353.221 [14539.107, 15744.349] | 0.726 |
| POST 100 KiB / c=64 | Mbit/s | 23894.839 [23520.936, 24451.662] | 15109.862 [15071.954, 15418.724] | 0.632 |
| POST 100 KiB / c=128 | Mbit/s | 22418.050 [21865.812, 23124.654] | 14479.906 [14464.250, 15276.507] | 0.646 |
| POST 1024 KiB / c=1 | Mbit/s | 18523.117 [17129.813, 18545.850] | 5767.594 [5638.045, 5892.419] | 0.311 |
| POST 1024 KiB / c=16 | Mbit/s | 40901.646 [39471.022, 46177.827] | 26999.308 [25634.870, 27308.485] | 0.660 |
| POST 1024 KiB / c=64 | Mbit/s | 34406.443 [32634.574, 36200.119] | 28693.290 [26575.064, 28807.220] | 0.834 |
| POST 1024 KiB / c=128 | Mbit/s | 35357.764 [32744.112, 37109.230] | 25855.627 [23297.834, 26686.683] | 0.731 |
| Latency / c=1 | requests/s | 7963.062 [7681.720, 7988.126] | 9513.783 [9181.995, 9524.625] | 1.195 |
| Latency / c=16 | requests/s | 38431.011 [37345.077, 38997.978] | 51263.237 [48640.779, 51908.805] | 1.334 |
| Latency / c=64 | requests/s | 40470.448 [39089.112, 41571.913] | 52607.790 [50285.766, 54112.506] | 1.300 |
| Latency / c=128 | requests/s | 40438.113 [39249.986, 40918.880] | 54883.802 [52638.162, 56449.038] | 1.357 |

## HTTP latency (milliseconds)

| Workload | Rust P50 / P95 / P99 | C++ P50 / P95 / P99 |
|---|---:|---:|
| GET 10 KiB / c=1 | 0.128 / 0.177 / 0.238 | 0.190 / 0.244 / 0.305 |
| GET 10 KiB / c=16 | 0.514 / 0.795 / 0.936 | 0.565 / 0.727 / 0.842 |
| GET 10 KiB / c=64 | 1.968 / 3.275 / 3.814 | 2.322 / 2.861 / 3.245 |
| GET 10 KiB / c=128 | 3.771 / 8.668 / 12.686 | 4.473 / 5.339 / 6.137 |
| GET 100 KiB / c=1 | 0.187 / 0.245 / 0.317 | 0.248 / 0.321 / 0.407 |
| GET 100 KiB / c=16 | 0.686 / 1.063 / 1.250 | 0.859 / 1.025 / 1.140 |
| GET 100 KiB / c=64 | 2.609 / 4.227 / 4.753 | 3.039 / 3.638 / 4.011 |
| GET 100 KiB / c=128 | 5.267 / 9.700 / 12.806 | 6.034 / 6.908 / 7.533 |
| GET 1024 KiB / c=1 | 0.459 / 0.586 / 0.737 | 0.716 / 0.891 / 1.005 |
| GET 1024 KiB / c=16 | 3.134 / 4.664 / 5.164 | 4.638 / 5.551 / 5.916 |
| GET 1024 KiB / c=64 | 12.954 / 20.666 / 22.724 | 16.767 / 20.065 / 23.047 |
| GET 1024 KiB / c=128 | 25.950 / 41.396 / 48.941 | 32.726 / 36.942 / 41.798 |
| POST 10 KiB / c=1 | 0.143 / 0.178 / 0.231 | 0.157 / 0.213 / 0.269 |
| POST 10 KiB / c=16 | 0.501 / 0.831 / 0.979 | 0.487 / 0.639 / 0.735 |
| POST 10 KiB / c=64 | 1.861 / 2.665 / 3.153 | 1.948 / 2.420 / 2.632 |
| POST 10 KiB / c=128 | 3.876 / 5.617 / 6.877 | 4.017 / 4.917 / 5.358 |
| POST 100 KiB / c=1 | 0.171 / 0.216 / 0.276 | 0.299 / 0.366 / 0.435 |
| POST 100 KiB / c=16 | 0.590 / 1.020 / 1.211 | 0.808 / 1.091 / 1.243 |
| POST 100 KiB / c=64 | 2.156 / 3.207 / 4.073 | 3.291 / 4.480 / 4.919 |
| POST 100 KiB / c=128 | 4.595 / 6.574 / 7.304 | 6.956 / 9.288 / 9.969 |
| POST 1024 KiB / c=1 | 0.352 / 0.513 / 0.639 | 1.390 / 1.627 / 1.741 |
| POST 1024 KiB / c=16 | 2.783 / 4.167 / 4.856 | 4.684 / 6.027 / 6.678 |
| POST 1024 KiB / c=64 | 14.884 / 23.288 / 26.966 | 18.134 / 22.171 / 24.413 |
| POST 1024 KiB / c=128 | 29.488 / 44.179 / 50.045 | 40.405 / 48.849 / 53.865 |
| Latency / c=1 | 0.117 / 0.157 / 0.223 | 0.097 / 0.152 / 0.215 |
| Latency / c=16 | 0.414 / 0.640 / 0.729 | 0.300 / 0.393 / 0.457 |
| Latency / c=64 | 1.598 / 2.478 / 2.834 | 1.183 / 1.525 / 1.735 |
| Latency / c=128 | 3.220 / 5.164 / 5.856 | 2.297 / 2.862 / 3.189 |

## Server resources

Idle / peak columns are median MiB. CPU cost is seconds/GiB for payload cases or microseconds/request for latency. Total thread counts are the observed architectural cost, under the same CPU budget.

| Workload | Rust CPU % / cost | C++ CPU % / cost | Rust idle / peak MiB | C++ idle / peak MiB | Rust / C++ threads |
|---|---:|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 102.8 / 14.908 | 104.0 / 21.557 | 7.83 / 8.65 | 15.47 / 16.46 | 3 / 6 |
| GET 10 KiB / c=16 | 196.9 / 6.624 | 196.9 / 7.578 | 7.77 / 11.29 | 15.57 / 19.70 | 3 / 6 |
| GET 10 KiB / c=64 | 197.4 / 6.334 | 197.4 / 7.720 | 7.91 / 13.20 | 15.38 / 20.11 | 3 / 6 |
| GET 10 KiB / c=128 | 197.4 / 6.287 | 196.6 / 7.324 | 7.95 / 16.03 | 15.38 / 20.95 | 3 / 6 |
| GET 100 KiB / c=1 | 95.0 / 1.947 | 107.1 / 2.903 | 7.98 / 8.96 | 15.39 / 16.36 | 3 / 6 |
| GET 100 KiB / c=16 | 198.2 / 0.888 | 198.2 / 1.146 | 7.92 / 13.07 | 15.56 / 19.67 | 3 / 6 |
| GET 100 KiB / c=64 | 198.4 / 0.843 | 198.1 / 1.008 | 7.89 / 18.77 | 15.48 / 20.22 | 3 / 6 |
| GET 100 KiB / c=128 | 197.5 / 0.868 | 197.8 / 0.987 | 7.99 / 27.84 | 15.45 / 20.93 | 3 / 6 |
| GET 1024 KiB / c=1 | 90.5 / 0.443 | 103.0 / 0.770 | 7.83 / 9.98 | 15.43 / 16.41 | 3 / 6 |
| GET 1024 KiB / c=16 | 197.8 / 0.397 | 198.4 / 0.607 | 7.89 / 20.51 | 15.43 / 19.46 | 3 / 6 |
| GET 1024 KiB / c=64 | 198.0 / 0.409 | 198.5 / 0.563 | 7.85 / 49.46 | 15.52 / 20.53 | 3 / 6 |
| GET 1024 KiB / c=128 | 197.5 / 0.413 | 198.4 / 0.527 | 7.84 / 86.71 | 15.38 / 21.20 | 3 / 6 |
| POST 10 KiB / c=1 | 100.5 / 15.840 | 110.5 / 19.071 | 7.94 / 8.83 | 15.44 / 16.59 | 3 / 6 |
| POST 10 KiB / c=16 | 194.5 / 6.662 | 193.8 / 6.469 | 7.95 / 11.74 | 15.45 / 20.94 | 3 / 6 |
| POST 10 KiB / c=64 | 197.8 / 6.138 | 194.9 / 6.448 | 8.00 / 13.74 | 15.39 / 24.32 | 3 / 6 |
| POST 10 KiB / c=128 | 198.5 / 6.440 | 195.3 / 6.710 | 7.89 / 15.82 | 15.44 / 28.88 | 3 / 6 |
| POST 100 KiB / c=1 | 85.0 / 1.666 | 102.6 / 3.394 | 7.95 / 8.75 | 15.37 / 15.70 | 3 / 6 |
| POST 100 KiB / c=16 | 194.0 / 0.798 | 194.5 / 1.112 | 8.01 / 11.51 | 15.34 / 18.03 | 3 / 6 |
| POST 100 KiB / c=64 | 197.8 / 0.721 | 195.9 / 1.132 | 7.94 / 19.64 | 15.44 / 22.59 | 3 / 6 |
| POST 100 KiB / c=128 | 197.0 / 0.768 | 196.1 / 1.188 | 8.05 / 32.82 | 15.41 / 27.61 | 3 / 6 |
| POST 1024 KiB / c=1 | 79.8 / 0.380 | 108.2 / 1.626 | 8.12 / 10.76 | 15.36 / 15.56 | 3 / 6 |
| POST 1024 KiB / c=16 | 193.9 / 0.414 | 190.6 / 0.616 | 7.91 / 22.69 | 15.30 / 17.18 | 3 / 6 |
| POST 1024 KiB / c=64 | 195.8 / 0.499 | 195.8 / 0.597 | 7.97 / 61.75 | 15.48 / 22.40 | 3 / 6 |
| POST 1024 KiB / c=128 | 195.3 / 0.487 | 195.9 / 0.666 | 8.17 / 119.72 | 15.32 / 27.77 | 3 / 6 |
| Latency / c=1 | 100.0 / 127.801 | 111.8 / 118.100 | 7.76 / 7.88 | 15.31 / 15.38 | 3 / 6 |
| Latency / c=16 | 197.7 / 51.785 | 198.1 / 38.977 | 7.78 / 8.43 | 15.41 / 15.80 | 3 / 6 |
| Latency / c=64 | 198.1 / 49.343 | 197.7 / 37.883 | 7.84 / 9.77 | 15.30 / 16.32 | 3 / 6 |
| Latency / c=128 | 198.3 / 49.868 | 198.6 / 36.635 | 7.75 / 10.99 | 15.34 / 17.11 | 3 / 6 |

## Generator utilization / errors

Generator CPU below is user+system time / observed wall, as percent of one core. It has up to four CPUs (one thread for c=1). High generator utilization is a warning against interpreting a throughput ceiling as solely server capacity.

| Workload | Rust-run client CPU % | C++-run client CPU % | Total completed requests | Socket/status/timeout errors |
|---|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 24.8 | 28.5 | 188223 | 0 |
| GET 10 KiB / c=16 | 93.0 | 104.2 | 905433 | 0 |
| GET 10 KiB / c=64 | 92.7 | 99.9 | 913062 | 0 |
| GET 10 KiB / c=128 | 95.1 | 98.8 | 945982 | 0 |
| GET 100 KiB / c=1 | 33.6 | 33.4 | 138174 | 0 |
| GET 100 KiB / c=16 | 123.6 | 123.8 | 646127 | 0 |
| GET 100 KiB / c=64 | 125.0 | 130.3 | 689487 | 0 |
| GET 100 KiB / c=128 | 122.7 | 131.3 | 686275 | 0 |
| GET 1024 KiB / c=1 | 61.8 | 49.2 | 52384 | 0 |
| GET 1024 KiB / c=16 | 160.2 | 117.4 | 129679 | 0 |
| GET 1024 KiB / c=64 | 158.7 | 118.6 | 135597 | 0 |
| GET 1024 KiB / c=128 | 158.6 | 121.4 | 136069 | 0 |
| POST 10 KiB / c=1 | 26.5 | 21.7 | 195854 | 0 |
| POST 10 KiB / c=16 | 90.1 | 83.4 | 955974 | 0 |
| POST 10 KiB / c=64 | 96.1 | 83.0 | 1014585 | 0 |
| POST 10 KiB / c=128 | 92.2 | 81.0 | 977443 | 0 |
| POST 100 KiB / c=1 | 41.9 | 25.2 | 130632 | 0 |
| POST 100 KiB / c=16 | 136.2 | 93.6 | 679527 | 0 |
| POST 100 KiB / c=64 | 156.5 | 100.2 | 728684 | 0 |
| POST 100 KiB / c=128 | 150.3 | 97.7 | 692595 | 0 |
| POST 1024 KiB / c=1 | 70.4 | 21.9 | 43043 | 0 |
| POST 1024 KiB / c=16 | 325.9 | 107.1 | 125212 | 0 |
| POST 1024 KiB / c=64 | 296.6 | 122.9 | 113701 | 0 |
| POST 1024 KiB / c=128 | 292.5 | 109.2 | 109604 | 0 |
| Latency / c=1 | 26.2 | 27.8 | 262699 | 0 |
| Latency / c=16 | 94.3 | 108.2 | 1359292 | 0 |
| Latency / c=64 | 96.5 | 107.0 | 1416518 | 0 |
| Latency / c=128 | 95.9 | 108.4 | 1449209 | 0 |

## Sustained mixed upload/download

One 300-second run per backend, 5-second warmup, 64 connections, alternating cached 1 MiB GET/POST requests. Payload rate is their aggregate, not separately asserted upload and download rates. This is a five-minute local stability observation, not a 24-hour soak or a leak proof.

| Backend | Aggregate Mbit/s | CPU % | Peak RSS MiB | First / last 30 s median RSS MiB | P50 / P95 / P99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| rust | 42146.190 | 198.9 | 100.33 | 87.41 / 97.86 | 10.765 / 24.454 / 29.617 | 0 |
| cpp | 22512.230 | 198.7 | 21.50 | 20.65 / 20.81 | 26.255 / 45.478 / 50.756 | 0 |

## Observed simultaneous connections

Separate correctness/capacity observation after performance collection: actual backend-owned ESTABLISHED sockets counted from Linux /proc, not just a client concurrency target. Each connection completes a health request and remains open; all must release after client close. This demonstrates the tested counts, not a maximum connection capacity.

| Backend | Requested / observed connections | Idle / active / peak RSS MiB | Threads | All released |
|---|---:|---:|---:|---|
| rust | 1 / 1 | 7.68 / 7.74 / 7.74 | 3 | yes |
| rust | 16 / 16 | 7.66 / 7.98 / 7.98 | 3 | yes |
| rust | 64 / 64 | 7.65 / 9.02 / 9.02 | 3 | yes |
| rust | 128 / 128 | 7.61 / 10.33 / 10.33 | 3 | yes |
| cpp | 1 / 1 | 15.23 / 15.23 / 15.23 | 6 | yes |
| cpp | 16 / 16 | 15.32 / 15.45 / 15.45 | 6 | yes |
| cpp | 64 / 64 | 15.30 / 15.80 / 15.80 | 6 | yes |
| cpp | 128 / 128 | 15.31 / 16.34 / 16.34 | 6 | yes |

## Artifact identity

Private paths, process IDs and raw captures are deliberately not published. Executable SHA256 values identify the measured builds:

- HTTP rust: `17a34335dedc7adf142a295a22e73f75a8444732ebcf52d7fbb095d5b6c826e7`
- HTTP cpp: `84567220c58f6bf0765464427e1ac51476090b01d783ce2ffe109629c7291159`
- core rust: `fc1f473392f6b6d50249fa9f73e7af3f2a51171ac1fbf8c39174f0e3d2cd30a7`
- core cpp: `fe5a8c097ff0edf0509f7b17b06ac8cccd97ea36f350aafa562eae17fab814f0`

## Interpretation boundary

These tables compare these implementations, compiler/dependency versions, tested profiles and hardware conditions—not an intrinsic property of C++ versus Rust. CPU-at-saturation alone is not efficiency; use CPU cost per unit of completed work. Memory and tail latency can trade against throughput. Small sample counts and WSL/host scheduling prevent broad statistical-significance claims.

Production remains Rust. Remaining protocol/resource/static-analysis/agent/probe-only/deployment acceptance must be satisfied separately. No measured local speed difference, by itself, authorizes a production replacement.
