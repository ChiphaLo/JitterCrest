# Local Rust/C++ performance measurements

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
| decode / 128-byte frames, 1/8 invalid length | 5.280 [5.277, 5.427] | 5.318 [5.282, 5.371] | 1.007 |
| hash / ASCII IDs | 31.234 [30.916, 31.665] | 87.095 [86.492, 87.698] | 2.788 |
| hash / Unicode IDs | 44.998 [41.672, 46.326] | 118.734 [118.240, 120.221] | 2.639 |
| percentile / P95, 512 samples | 6161.874 [6152.171, 6297.257] | 5018.033 [4942.067, 5212.322] | 0.814 |

## HTTP payload throughput / request rate

Payload Mbit/s counts successful application bytes (request payload for upload), not headers or compressed wire bytes. Latency rows use requests/s. A wrk client ceiling can limit a result; these values are not asserted to be absolute backend capacity.

| Workload | Metric | Rust median [min, max] | C++ median [min, max] | C++ / Rust |
|---|---|---:|---:|---:|
| GET 10 KiB / c=1 | Mbit/s | 608.064 [604.524, 616.907] | 436.620 [434.703, 442.226] | 0.718 |
| GET 10 KiB / c=16 | Mbit/s | 2786.820 [2773.504, 2827.542] | 2370.933 [2365.121, 2379.776] | 0.851 |
| GET 10 KiB / c=64 | Mbit/s | 2913.970 [2832.934, 2915.127] | 2539.450 [2489.327, 2550.174] | 0.871 |
| GET 10 KiB / c=128 | Mbit/s | 2955.681 [2936.667, 2959.486] | 2646.647 [2613.483, 2649.971] | 0.895 |
| GET 100 KiB / c=1 | Mbit/s | 4392.256 [4323.698, 4495.442] | 3322.812 [3299.639, 3340.363] | 0.757 |
| GET 100 KiB / c=16 | Mbit/s | 21424.077 [21225.175, 21456.952] | 15917.784 [15858.718, 16014.852] | 0.743 |
| GET 100 KiB / c=64 | Mbit/s | 21502.983 [21279.766, 21850.978] | 17478.263 [17392.946, 17749.225] | 0.813 |
| GET 100 KiB / c=128 | Mbit/s | 20982.037 [20920.831, 21143.783] | 17911.014 [17606.179, 17957.444] | 0.854 |
| GET 1024 KiB / c=1 | Mbit/s | 17676.253 [17514.421, 18713.777] | 12171.032 [11908.382, 12201.345] | 0.689 |
| GET 1024 KiB / c=16 | Mbit/s | 44484.379 [44466.108, 44783.089] | 29481.694 [28794.045, 30449.110] | 0.663 |
| GET 1024 KiB / c=64 | Mbit/s | 44567.918 [44286.418, 44724.695] | 36552.056 [36024.872, 37108.261] | 0.820 |
| GET 1024 KiB / c=128 | Mbit/s | 43597.049 [43149.711, 43755.313] | 38657.931 [36834.988, 39180.783] | 0.887 |
| POST 10 KiB / c=1 | Mbit/s | 569.199 [568.355, 570.630] | 517.632 [511.530, 519.082] | 0.909 |
| POST 10 KiB / c=16 | Mbit/s | 2608.941 [2607.955, 2673.794] | 2710.917 [2620.715, 2861.256] | 1.039 |
| POST 10 KiB / c=64 | Mbit/s | 2857.929 [2734.034, 2933.486] | 2779.508 [2648.211, 2789.000] | 0.973 |
| POST 10 KiB / c=128 | Mbit/s | 2873.220 [2719.760, 2909.028] | 2738.913 [2714.556, 2739.882] | 0.953 |
| POST 100 KiB / c=1 | Mbit/s | 4443.109 [4283.939, 4506.056] | 2697.478 [2664.549, 2697.848] | 0.607 |
| POST 100 KiB / c=16 | Mbit/s | 23118.378 [22776.888, 23312.897] | 16081.655 [15517.393, 16288.515] | 0.696 |
| POST 100 KiB / c=64 | Mbit/s | 24695.522 [24558.145, 24868.077] | 15873.817 [15823.704, 15889.172] | 0.643 |
| POST 100 KiB / c=128 | Mbit/s | 23882.676 [23081.341, 24339.081] | 15913.097 [15884.070, 15918.452] | 0.666 |
| POST 1024 KiB / c=1 | Mbit/s | 19812.252 [19680.899, 19862.814] | 5655.033 [5405.635, 6131.508] | 0.285 |
| POST 1024 KiB / c=16 | Mbit/s | 43074.272 [41791.912, 43619.063] | 28186.102 [27868.522, 28302.448] | 0.654 |
| POST 1024 KiB / c=64 | Mbit/s | 36493.256 [36343.994, 36724.339] | 30114.380 [29360.487, 30380.674] | 0.825 |
| POST 1024 KiB / c=128 | Mbit/s | 36212.790 [35304.883, 36563.530] | 28276.505 [27372.441, 28528.075] | 0.781 |
| Latency / c=1 | requests/s | 8191.756 [8173.273, 8220.301] | 9775.811 [9520.321, 10165.473] | 1.193 |
| Latency / c=16 | requests/s | 39968.648 [39762.678, 40017.908] | 53183.089 [52905.704, 53579.574] | 1.331 |
| Latency / c=64 | requests/s | 42793.651 [41959.347, 43341.053] | 55256.943 [54581.384, 55637.781] | 1.291 |
| Latency / c=128 | requests/s | 43166.003 [43067.122, 43313.014] | 56482.746 [56163.683, 57404.955] | 1.309 |

## HTTP latency (milliseconds)

| Workload | Rust P50 / P95 / P99 | C++ P50 / P95 / P99 |
|---|---:|---:|
| GET 10 KiB / c=1 | 0.125 / 0.171 / 0.226 | 0.182 / 0.223 / 0.265 |
| GET 10 KiB / c=16 | 0.477 / 0.708 / 0.789 | 0.541 / 0.656 / 0.734 |
| GET 10 KiB / c=64 | 1.826 / 2.949 / 3.321 | 2.044 / 2.396 / 2.627 |
| GET 10 KiB / c=128 | 3.464 / 8.553 / 12.843 | 3.996 / 4.602 / 5.125 |
| GET 100 KiB / c=1 | 0.180 / 0.227 / 0.280 | 0.240 / 0.291 / 0.343 |
| GET 100 KiB / c=16 | 0.621 / 0.916 / 1.017 | 0.809 / 0.945 / 1.035 |
| GET 100 KiB / c=64 | 2.473 / 3.998 / 4.463 | 2.978 / 3.387 / 3.675 |
| GET 100 KiB / c=128 | 4.981 / 9.124 / 12.883 | 5.865 / 6.547 / 7.054 |
| GET 1024 KiB / c=1 | 0.457 / 0.569 / 0.742 | 0.678 / 0.848 / 0.956 |
| GET 1024 KiB / c=16 | 3.038 / 4.506 / 4.893 | 4.536 / 5.130 / 5.451 |
| GET 1024 KiB / c=64 | 12.268 / 19.641 / 21.723 | 14.629 / 16.197 / 17.467 |
| GET 1024 KiB / c=128 | 24.754 / 39.895 / 47.588 | 27.313 / 32.138 / 36.957 |
| POST 10 KiB / c=1 | 0.136 / 0.178 / 0.229 | 0.153 / 0.203 / 0.244 |
| POST 10 KiB / c=16 | 0.484 / 0.782 / 0.901 | 0.470 / 0.608 / 0.709 |
| POST 10 KiB / c=64 | 1.856 / 2.561 / 2.887 | 1.862 / 2.255 / 2.500 |
| POST 10 KiB / c=128 | 3.693 / 5.476 / 5.879 | 3.813 / 4.440 / 4.973 |
| POST 100 KiB / c=1 | 0.169 / 0.211 / 0.289 | 0.291 / 0.350 / 0.399 |
| POST 100 KiB / c=16 | 0.540 / 0.893 / 1.094 | 0.787 / 1.009 / 1.149 |
| POST 100 KiB / c=64 | 2.089 / 2.985 / 3.826 | 3.247 / 3.850 / 4.369 |
| POST 100 KiB / c=128 | 4.407 / 6.615 / 7.172 | 6.540 / 7.387 / 8.385 |
| POST 1024 KiB / c=1 | 0.317 / 0.465 / 0.592 | 1.421 / 1.599 / 1.689 |
| POST 1024 KiB / c=16 | 2.683 / 3.883 / 4.419 | 4.533 / 5.565 / 6.163 |
| POST 1024 KiB / c=64 | 14.168 / 21.268 / 24.483 | 17.370 / 19.708 / 21.211 |
| POST 1024 KiB / c=128 | 28.851 / 41.281 / 46.526 | 37.844 / 40.626 / 43.838 |
| Latency / c=1 | 0.113 / 0.152 / 0.197 | 0.094 / 0.147 / 0.179 |
| Latency / c=16 | 0.399 / 0.605 / 0.688 | 0.292 / 0.364 / 0.422 |
| Latency / c=64 | 1.515 / 2.309 / 2.598 | 1.139 / 1.347 / 1.580 |
| Latency / c=128 | 2.998 / 4.807 / 5.399 | 2.269 / 2.583 / 3.041 |

## Server resources

Idle / peak columns are median MiB. CPU cost is seconds/GiB for payload cases or microseconds/request for latency. Total thread counts are the observed architectural cost, under the same CPU budget.

| Workload | Rust CPU % / cost | C++ CPU % / cost | Rust idle / peak MiB | C++ idle / peak MiB | Rust / C++ threads |
|---|---:|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 103.6 / 14.627 | 105.8 / 20.932 | 7.74 / 8.54 | 15.46 / 16.45 | 3 / 6 |
| GET 10 KiB / c=16 | 197.2 / 6.062 | 198.4 / 7.232 | 7.88 / 11.41 | 15.39 / 19.49 | 3 / 6 |
| GET 10 KiB / c=64 | 196.8 / 5.897 | 198.2 / 6.746 | 7.80 / 13.07 | 15.39 / 20.10 | 3 / 6 |
| GET 10 KiB / c=128 | 199.5 / 5.853 | 198.6 / 6.521 | 7.83 / 15.80 | 15.46 / 20.90 | 3 / 6 |
| GET 100 KiB / c=1 | 95.4 / 1.870 | 108.5 / 2.809 | 7.96 / 8.89 | 15.34 / 16.33 | 3 / 6 |
| GET 100 KiB / c=16 | 199.4 / 0.803 | 198.2 / 1.074 | 7.90 / 12.79 | 15.37 / 19.50 | 3 / 6 |
| GET 100 KiB / c=64 | 198.6 / 0.799 | 196.6 / 0.972 | 7.99 / 19.00 | 15.38 / 20.09 | 3 / 6 |
| GET 100 KiB / c=128 | 199.2 / 0.824 | 197.9 / 0.960 | 7.96 / 27.59 | 15.43 / 20.87 | 3 / 6 |
| GET 1024 KiB / c=1 | 87.4 / 0.424 | 103.2 / 0.733 | 7.90 / 10.08 | 15.34 / 16.31 | 3 / 6 |
| GET 1024 KiB / c=16 | 198.2 / 0.386 | 196.8 / 0.578 | 7.88 / 20.98 | 15.37 / 19.42 | 3 / 6 |
| GET 1024 KiB / c=64 | 198.8 / 0.386 | 199.6 / 0.472 | 7.72 / 48.52 | 15.34 / 20.27 | 3 / 6 |
| GET 1024 KiB / c=128 | 196.1 / 0.394 | 198.9 / 0.449 | 7.75 / 85.19 | 15.43 / 21.24 | 3 / 6 |
| POST 10 KiB / c=1 | 100.8 / 15.269 | 111.3 / 18.579 | 7.89 / 8.82 | 15.33 / 16.47 | 3 / 6 |
| POST 10 KiB / c=16 | 195.2 / 6.498 | 194.7 / 6.245 | 7.94 / 11.69 | 15.33 / 20.85 | 3 / 6 |
| POST 10 KiB / c=64 | 197.2 / 6.002 | 195.1 / 6.126 | 8.05 / 13.59 | 15.36 / 24.59 | 3 / 6 |
| POST 10 KiB / c=128 | 198.2 / 5.990 | 195.9 / 6.242 | 7.87 / 15.70 | 15.34 / 30.63 | 3 / 6 |
| POST 100 KiB / c=1 | 84.9 / 1.649 | 103.0 / 3.303 | 8.05 / 8.86 | 15.30 / 15.64 | 3 / 6 |
| POST 100 KiB / c=16 | 195.9 / 0.740 | 194.5 / 1.058 | 8.06 / 11.66 | 15.35 / 17.75 | 3 / 6 |
| POST 100 KiB / c=64 | 198.1 / 0.700 | 195.7 / 1.075 | 8.07 / 20.08 | 15.36 / 22.45 | 3 / 6 |
| POST 100 KiB / c=128 | 197.8 / 0.724 | 196.7 / 1.080 | 8.10 / 31.60 | 15.34 / 27.52 | 3 / 6 |
| POST 1024 KiB / c=1 | 81.0 / 0.353 | 109.5 / 1.673 | 8.01 / 11.05 | 15.46 / 15.68 | 3 / 6 |
| POST 1024 KiB / c=16 | 194.4 / 0.393 | 190.5 / 0.595 | 8.08 / 21.95 | 15.31 / 17.30 | 3 / 6 |
| POST 1024 KiB / c=64 | 196.2 / 0.468 | 196.9 / 0.570 | 8.17 / 61.88 | 15.34 / 21.74 | 3 / 6 |
| POST 1024 KiB / c=128 | 194.5 / 0.473 | 197.4 / 0.615 | 8.10 / 118.86 | 15.30 / 27.91 | 3 / 6 |
| Latency / c=1 | 100.3 / 122.798 | 111.9 / 114.769 | 7.84 / 7.95 | 15.34 / 15.39 | 3 / 6 |
| Latency / c=16 | 198.6 / 50.093 | 199.2 / 37.571 | 7.74 / 8.39 | 15.18 / 15.58 | 3 / 6 |
| Latency / c=64 | 196.8 / 46.238 | 198.2 / 36.092 | 7.75 / 9.64 | 15.25 / 16.28 | 3 / 6 |
| Latency / c=128 | 199.7 / 46.585 | 198.7 / 35.547 | 7.74 / 11.05 | 15.36 / 17.21 | 3 / 6 |

## Generator utilization / errors

Generator CPU below is user+system time / observed wall, as percent of one core. It has up to four CPUs (one thread for c=1). High generator utilization is a warning against interpreting a throughput ceiling as solely server capacity.

| Workload | Rust-run client CPU % | C++-run client CPU % | Total completed requests | Socket/status/timeout errors |
|---|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 24.3 | 27.9 | 195134 | 0 |
| GET 10 KiB / c=16 | 97.3 | 107.4 | 964989 | 0 |
| GET 10 KiB / c=64 | 98.7 | 107.5 | 1009956 | 0 |
| GET 10 KiB / c=128 | 99.8 | 107.3 | 1042439 | 0 |
| GET 100 KiB / c=1 | 33.6 | 32.5 | 144265 | 0 |
| GET 100 KiB / c=16 | 131.7 | 128.4 | 696605 | 0 |
| GET 100 KiB / c=64 | 130.9 | 130.5 | 729157 | 0 |
| GET 100 KiB / c=128 | 129.3 | 132.1 | 724657 | 0 |
| GET 1024 KiB / c=1 | 61.8 | 50.6 | 54606 | 0 |
| GET 1024 KiB / c=16 | 163.4 | 119.0 | 135171 | 0 |
| GET 1024 KiB / c=64 | 162.6 | 129.4 | 147760 | 0 |
| GET 1024 KiB / c=128 | 158.4 | 129.9 | 148892 | 0 |
| POST 10 KiB / c=1 | 26.6 | 21.7 | 202726 | 0 |
| POST 10 KiB / c=16 | 90.8 | 86.6 | 1000830 | 0 |
| POST 10 KiB / c=64 | 96.7 | 87.0 | 1039731 | 0 |
| POST 10 KiB / c=128 | 94.6 | 85.9 | 1036929 | 0 |
| POST 100 KiB / c=1 | 42.0 | 25.4 | 132555 | 0 |
| POST 100 KiB / c=16 | 142.7 | 98.4 | 728625 | 0 |
| POST 100 KiB / c=64 | 158.3 | 101.7 | 755283 | 0 |
| POST 100 KiB / c=128 | 158.8 | 104.5 | 738752 | 0 |
| POST 1024 KiB / c=1 | 72.8 | 19.9 | 46338 | 0 |
| POST 1024 KiB / c=16 | 328.6 | 108.3 | 129074 | 0 |
| POST 1024 KiB / c=64 | 309.2 | 123.2 | 121041 | 0 |
| POST 1024 KiB / c=128 | 298.0 | 120.3 | 116592 | 0 |
| Latency / c=1 | 26.6 | 27.9 | 274655 | 0 |
| Latency / c=16 | 96.9 | 109.9 | 1424880 | 0 |
| Latency / c=64 | 98.7 | 109.3 | 1495586 | 0 |
| Latency / c=128 | 99.5 | 109.6 | 1526425 | 0 |

## Sustained mixed upload/download

One 300-second run per backend, 5-second warmup, 64 connections, alternating cached 1 MiB GET/POST requests. Payload rate is their aggregate, not separately asserted upload and download rates. This is a five-minute local stability observation, not a 24-hour soak or a leak proof.

| Backend | Aggregate Mbit/s | CPU % | Peak RSS MiB | First / last 30 s median RSS MiB | P50 / P95 / P99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| rust | 41846.970 | 198.9 | 98.46 | 90.16 / 93.64 | 10.834 / 24.464 / 29.454 | 0 |
| cpp | 26048.953 | 199.2 | 21.77 | 21.39 / 21.34 | 22.521 / 37.070 / 39.210 | 0 |

## Observed simultaneous connections

Separate correctness/capacity observation after performance collection: actual backend-owned ESTABLISHED sockets counted from Linux /proc, not just a client concurrency target. Each connection completes a health request and remains open; all must release after client close. This demonstrates the tested counts, not a maximum connection capacity.

| Backend | Requested / observed connections | Idle / active / peak RSS MiB | Threads | All released |
|---|---:|---:|---:|---|
| rust | 1 / 1 | 7.71 / 7.78 / 7.78 | 3 | yes |
| rust | 16 / 16 | 7.75 / 8.12 / 8.12 | 3 | yes |
| rust | 64 / 64 | 7.74 / 9.10 / 9.10 | 3 | yes |
| rust | 128 / 128 | 7.95 / 10.66 / 10.66 | 3 | yes |
| cpp | 1 / 1 | 15.34 / 15.36 / 15.36 | 6 | yes |
| cpp | 16 / 16 | 15.15 / 15.27 / 15.27 | 6 | yes |
| cpp | 64 / 64 | 15.26 / 15.78 / 15.78 | 6 | yes |
| cpp | 128 / 128 | 15.32 / 16.35 / 16.35 | 6 | yes |

## Artifact identity

Private paths, process IDs and raw captures are deliberately not published. Executable SHA256 values identify the measured builds:

- HTTP rust: `17a34335dedc7adf142a295a22e73f75a8444732ebcf52d7fbb095d5b6c826e7`
- HTTP cpp: `6b7c0d98dabf0f9c7a2a07af1d9375947b8ccd32fb01034c5ba8ca25dd0dd314`
- core rust: `fc1f473392f6b6d50249fa9f73e7af3f2a51171ac1fbf8c39174f0e3d2cd30a7`
- core cpp: `fe5a8c097ff0edf0509f7b17b06ac8cccd97ea36f350aafa562eae17fab814f0`

## Interpretation boundary

These tables compare these implementations, compiler/dependency versions, tested profiles and hardware conditions—not an intrinsic property of C++ versus Rust. CPU-at-saturation alone is not efficiency; use CPU cost per unit of completed work. Memory and tail latency can trade against throughput. Small sample counts and WSL/host scheduling prevent broad statistical-significance claims.

Production remains Rust. Remaining protocol/resource/static-analysis/agent/probe-only/deployment acceptance must be satisfied separately. No measured local speed difference, by itself, authorizes a production replacement.
