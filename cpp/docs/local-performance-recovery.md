# Recovery-source Rust/C++ local performance measurements

These are measured local profiles, not proof of universal language superiority or production readiness.

Runtime source: `019ff35821f974d80ab223e0fed4f19f7b01a6fb`. Fresh public-source Release/ThinLTO build passed CTest8 and paired HTTP126 before timing. Independent post-run checks verified unchanged source, binary, workload and native-library hashes, identical measured/packaged application text, and all86 protected baseline files. Earlier reports remain versioned, not relabeled.

Collection: 40 core rows, 168 HTTP rows / **15,292,678 requests**, two 300-second soak rows / **2,305,566 requests**, zero HTTP/soak workload errors.

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
| decode / 128-byte frames, 1/8 invalid length | 5.350 [5.306, 5.445] | 5.370 [5.308, 5.425] | 1.004 |
| hash / ASCII IDs | 31.169 [31.057, 32.288] | 87.351 [86.887, 88.627] | 2.803 |
| hash / Unicode IDs | 45.753 [42.127, 52.607] | 120.455 [118.158, 124.070] | 2.633 |
| percentile / P95, 512 samples | 6218.408 [6205.795, 6234.031] | 5121.033 [5031.315, 5249.195] | 0.824 |

## HTTP payload throughput / request rate

Payload Mbit/s counts successful application bytes (request payload for upload), not headers or compressed wire bytes. Latency rows use requests/s. A wrk client ceiling can limit a result; these values are not asserted to be absolute backend capacity.

| Workload | Metric | Rust median [min, max] | C++ median [min, max] | C++ / Rust |
|---|---|---:|---:|---:|
| GET 10 KiB / c=1 | Mbit/s | 569.079 [567.619, 572.982] | 405.397 [404.385, 408.265] | 0.712 |
| GET 10 KiB / c=16 | Mbit/s | 2588.823 [2551.269, 2608.080] | 2168.224 [2166.729, 2188.567] | 0.838 |
| GET 10 KiB / c=64 | Mbit/s | 2648.970 [2616.795, 2706.477] | 2270.922 [2262.316, 2272.512] | 0.857 |
| GET 10 KiB / c=128 | Mbit/s | 2661.923 [2633.665, 2670.912] | 2322.461 [2290.635, 2322.547] | 0.872 |
| GET 100 KiB / c=1 | Mbit/s | 4088.285 [4053.955, 4088.720] | 3188.502 [3188.142, 3192.390] | 0.780 |
| GET 100 KiB / c=16 | Mbit/s | 19509.080 [19216.056, 19614.111] | 14653.044 [14585.436, 14740.716] | 0.751 |
| GET 100 KiB / c=64 | Mbit/s | 19204.901 [18958.314, 19358.601] | 15757.840 [15635.284, 15890.713] | 0.821 |
| GET 100 KiB / c=128 | Mbit/s | 18864.775 [18795.769, 19235.378] | 15867.531 [15469.794, 15946.063] | 0.841 |
| GET 1024 KiB / c=1 | Mbit/s | 16202.832 [15864.403, 16283.873] | 11741.715 [11676.513, 11901.080] | 0.725 |
| GET 1024 KiB / c=16 | Mbit/s | 41280.583 [38617.876, 41505.577] | 25944.181 [25451.302, 26553.431] | 0.628 |
| GET 1024 KiB / c=64 | Mbit/s | 40892.190 [39290.939, 41184.266] | 31028.805 [29354.652, 32137.022] | 0.759 |
| GET 1024 KiB / c=128 | Mbit/s | 40963.561 [40783.752, 40968.388] | 33735.843 [33201.148, 34446.879] | 0.824 |
| POST 10 KiB / c=1 | Mbit/s | 527.717 [526.110, 531.298] | 476.967 [467.899, 481.847] | 0.904 |
| POST 10 KiB / c=16 | Mbit/s | 2445.149 [2395.995, 2477.783] | 2400.057 [2387.140, 2458.236] | 0.982 |
| POST 10 KiB / c=64 | Mbit/s | 2687.688 [2659.189, 2721.080] | 2475.676 [2459.115, 2506.534] | 0.921 |
| POST 10 KiB / c=128 | Mbit/s | 2658.016 [2525.613, 2702.450] | 2513.063 [2417.247, 2556.278] | 0.945 |
| POST 100 KiB / c=1 | Mbit/s | 4240.431 [4233.677, 4274.265] | 2541.840 [2507.923, 2551.694] | 0.599 |
| POST 100 KiB / c=16 | Mbit/s | 20588.723 [20434.447, 21135.184] | 14541.711 [14076.233, 14707.196] | 0.706 |
| POST 100 KiB / c=64 | Mbit/s | 22394.988 [22356.624, 24071.856] | 14612.170 [13984.618, 14735.561] | 0.652 |
| POST 100 KiB / c=128 | Mbit/s | 23226.891 [21132.716, 24304.162] | 14248.414 [14115.224, 14256.818] | 0.613 |
| POST 1024 KiB / c=1 | Mbit/s | 17972.648 [17804.733, 17973.140] | 5493.626 [5423.007, 5739.900] | 0.306 |
| POST 1024 KiB / c=16 | Mbit/s | 43584.282 [43511.323, 43628.661] | 24860.221 [24847.593, 25318.825] | 0.570 |
| POST 1024 KiB / c=64 | Mbit/s | 36223.397 [35666.185, 36713.252] | 26360.088 [26098.570, 26510.544] | 0.728 |
| POST 1024 KiB / c=128 | Mbit/s | 36023.382 [35698.548, 36061.580] | 23961.332 [23949.488, 24613.642] | 0.665 |
| Latency / c=1 | requests/s | 7723.997 [7648.844, 7724.882] | 9031.965 [8998.949, 9103.768] | 1.169 |
| Latency / c=16 | requests/s | 37125.360 [36826.409, 37376.170] | 48461.730 [47137.214, 48579.454] | 1.305 |
| Latency / c=64 | requests/s | 39487.523 [39398.210, 39794.610] | 49028.401 [48711.510, 50252.552] | 1.242 |
| Latency / c=128 | requests/s | 39359.797 [38550.571, 39586.796] | 51412.378 [51366.598, 51485.551] | 1.306 |

## HTTP latency (milliseconds)

| Workload | Rust P50 / P95 / P99 | C++ P50 / P95 / P99 |
|---|---:|---:|
| GET 10 KiB / c=1 | 0.133 / 0.180 / 0.218 | 0.196 / 0.239 / 0.285 |
| GET 10 KiB / c=16 | 0.513 / 0.771 / 0.869 | 0.590 / 0.729 / 0.824 |
| GET 10 KiB / c=64 | 2.005 / 3.258 / 3.652 | 2.280 / 2.684 / 2.993 |
| GET 10 KiB / c=128 | 3.869 / 8.555 / 12.920 | 4.502 / 5.304 / 5.880 |
| GET 100 KiB / c=1 | 0.194 / 0.245 / 0.296 | 0.253 / 0.305 / 0.349 |
| GET 100 KiB / c=16 | 0.681 / 1.019 / 1.160 | 0.877 / 1.052 / 1.188 |
| GET 100 KiB / c=64 | 2.756 / 4.512 / 5.120 | 3.305 / 3.810 / 4.188 |
| GET 100 KiB / c=128 | 5.528 / 10.040 / 13.112 | 6.658 / 7.599 / 8.446 |
| GET 1024 KiB / c=1 | 0.504 / 0.611 / 0.900 | 0.700 / 0.876 / 0.984 |
| GET 1024 KiB / c=16 | 3.280 / 4.876 / 5.444 | 5.123 / 5.974 / 7.268 |
| GET 1024 KiB / c=64 | 13.347 / 21.538 / 23.866 | 16.970 / 20.548 / 24.930 |
| GET 1024 KiB / c=128 | 26.498 / 41.664 / 49.584 | 31.331 / 35.070 / 37.775 |
| POST 10 KiB / c=1 | 0.149 / 0.185 / 0.225 | 0.169 / 0.219 / 0.266 |
| POST 10 KiB / c=16 | 0.520 / 0.851 / 1.009 | 0.534 / 0.693 / 0.789 |
| POST 10 KiB / c=64 | 1.953 / 2.756 / 3.125 | 2.075 / 2.577 / 2.803 |
| POST 10 KiB / c=128 | 3.980 / 5.654 / 6.210 | 4.095 / 5.003 / 5.349 |
| POST 100 KiB / c=1 | 0.176 / 0.216 / 0.263 | 0.308 / 0.372 / 0.432 |
| POST 100 KiB / c=16 | 0.609 / 1.037 / 1.227 | 0.859 / 1.144 / 1.263 |
| POST 100 KiB / c=64 | 2.289 / 3.413 / 4.101 | 3.398 / 4.459 / 4.760 |
| POST 100 KiB / c=128 | 4.486 / 6.293 / 7.365 | 7.161 / 8.987 / 9.445 |
| POST 1024 KiB / c=1 | 0.369 / 0.493 / 0.642 | 1.434 / 1.681 / 1.788 |
| POST 1024 KiB / c=16 | 2.592 / 3.911 / 4.588 | 5.142 / 6.458 / 6.845 |
| POST 1024 KiB / c=64 | 14.181 / 21.670 / 24.647 | 19.766 / 24.824 / 26.104 |
| POST 1024 KiB / c=128 | 28.987 / 43.056 / 48.013 | 43.610 / 53.370 / 55.819 |
| Latency / c=1 | 0.120 / 0.157 / 0.194 | 0.101 / 0.156 / 0.194 |
| Latency / c=16 | 0.429 / 0.660 / 0.744 | 0.319 / 0.414 / 0.475 |
| Latency / c=64 | 1.637 / 2.536 / 2.857 | 1.262 / 1.585 / 1.753 |
| Latency / c=128 | 3.291 / 5.286 / 6.047 | 2.448 / 3.029 / 3.403 |

## Server resources

Idle / peak columns are median MiB. CPU cost is seconds/GiB for payload cases or microseconds/request for latency. Total thread counts are the observed architectural cost, under the same CPU budget.

| Workload | Rust CPU % / cost | C++ CPU % / cost | Rust idle / peak MiB | C++ idle / peak MiB | Rust / C++ threads |
|---|---:|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 102.4 / 15.510 | 102.8 / 21.754 | 7.89 / 8.70 | 15.43 / 16.43 | 3 / 6 |
| GET 10 KiB / c=16 | 197.5 / 6.604 | 197.1 / 7.869 | 7.89 / 11.36 | 15.32 / 19.45 | 3 / 6 |
| GET 10 KiB / c=64 | 198.3 / 6.481 | 197.2 / 7.517 | 7.91 / 13.20 | 15.36 / 20.01 | 3 / 6 |
| GET 10 KiB / c=128 | 197.9 / 6.470 | 197.0 / 7.371 | 7.79 / 15.78 | 15.45 / 20.88 | 3 / 6 |
| GET 100 KiB / c=1 | 94.2 / 1.994 | 106.5 / 2.876 | 7.98 / 8.96 | 15.39 / 16.38 | 3 / 6 |
| GET 100 KiB / c=16 | 198.1 / 0.879 | 197.5 / 1.167 | 7.90 / 13.04 | 15.36 / 19.47 | 3 / 6 |
| GET 100 KiB / c=64 | 197.6 / 0.894 | 198.1 / 1.088 | 8.08 / 18.98 | 15.40 / 20.11 | 3 / 6 |
| GET 100 KiB / c=128 | 197.2 / 0.895 | 197.7 / 1.086 | 7.87 / 26.61 | 15.41 / 20.85 | 3 / 6 |
| GET 1024 KiB / c=1 | 84.4 / 0.453 | 102.4 / 0.753 | 7.62 / 10.24 | 15.45 / 16.43 | 3 / 6 |
| GET 1024 KiB / c=16 | 198.2 / 0.417 | 198.5 / 0.664 | 7.76 / 20.51 | 15.39 / 19.40 | 3 / 6 |
| GET 1024 KiB / c=64 | 197.1 / 0.418 | 198.7 / 0.555 | 7.84 / 46.29 | 15.38 / 20.34 | 3 / 6 |
| GET 1024 KiB / c=128 | 197.9 / 0.421 | 198.3 / 0.512 | 7.91 / 84.57 | 15.29 / 21.07 | 3 / 6 |
| POST 10 KiB / c=1 | 100.3 / 16.425 | 108.9 / 19.705 | 7.95 / 8.89 | 15.25 / 16.38 | 3 / 6 |
| POST 10 KiB / c=16 | 195.1 / 6.951 | 193.2 / 7.011 | 7.80 / 11.63 | 15.36 / 20.89 | 3 / 6 |
| POST 10 KiB / c=64 | 198.0 / 6.439 | 194.4 / 6.844 | 7.95 / 13.58 | 15.33 / 24.99 | 3 / 6 |
| POST 10 KiB / c=128 | 198.4 / 6.519 | 195.2 / 6.801 | 7.92 / 15.67 | 15.43 / 29.00 | 3 / 6 |
| POST 100 KiB / c=1 | 85.4 / 1.740 | 103.0 / 3.499 | 7.93 / 8.72 | 15.38 / 15.73 | 3 / 6 |
| POST 100 KiB / c=16 | 194.1 / 0.820 | 194.8 / 1.171 | 8.08 / 11.52 | 15.35 / 17.93 | 3 / 6 |
| POST 100 KiB / c=64 | 196.8 / 0.766 | 196.0 / 1.173 | 8.12 / 19.65 | 15.31 / 22.14 | 3 / 6 |
| POST 100 KiB / c=128 | 198.1 / 0.748 | 196.4 / 1.210 | 8.04 / 30.89 | 15.41 / 27.75 | 3 / 6 |
| POST 1024 KiB / c=1 | 81.0 / 0.391 | 108.6 / 1.707 | 8.18 / 11.04 | 15.40 / 15.64 | 3 / 6 |
| POST 1024 KiB / c=16 | 192.9 / 0.387 | 190.5 / 0.669 | 8.02 / 22.76 | 15.39 / 17.33 | 3 / 6 |
| POST 1024 KiB / c=64 | 195.1 / 0.471 | 195.4 / 0.648 | 7.99 / 62.56 | 15.37 / 22.38 | 3 / 6 |
| POST 1024 KiB / c=128 | 196.4 / 0.479 | 195.9 / 0.718 | 8.03 / 125.77 | 15.29 / 27.84 | 3 / 6 |
| Latency / c=1 | 100.6 / 130.741 | 110.2 / 122.236 | 7.74 / 7.86 | 15.38 / 15.43 | 3 / 6 |
| Latency / c=16 | 197.5 / 53.608 | 198.1 / 41.194 | 7.70 / 8.38 | 15.29 / 15.68 | 3 / 6 |
| Latency / c=64 | 198.1 / 50.374 | 197.9 / 40.648 | 7.73 / 9.72 | 15.32 / 16.35 | 3 / 6 |
| Latency / c=128 | 198.2 / 50.984 | 198.1 / 39.013 | 7.85 / 10.95 | 15.28 / 17.17 | 3 / 6 |

## Generator utilization / errors

Generator CPU below is user+system time / observed wall, as percent of one core. It has up to four CPUs (one thread for c=1). High generator utilization is a warning against interpreting a throughput ceiling as solely server capacity.

| Workload | Rust-run client CPU % | C++-run client CPU % | Total completed requests | Socket/status/timeout errors |
|---|---:|---:|---:|---:|
| GET 10 KiB / c=1 | 24.8 | 28.9 | 181761 | 0 |
| GET 10 KiB / c=16 | 92.2 | 100.2 | 887819 | 0 |
| GET 10 KiB / c=64 | 92.3 | 99.7 | 918785 | 0 |
| GET 10 KiB / c=128 | 92.0 | 98.6 | 926490 | 0 |
| GET 100 KiB / c=1 | 34.2 | 33.6 | 135323 | 0 |
| GET 100 KiB / c=16 | 123.7 | 122.4 | 636965 | 0 |
| GET 100 KiB / c=64 | 121.4 | 123.7 | 651505 | 0 |
| GET 100 KiB / c=128 | 118.9 | 123.0 | 647605 | 0 |
| GET 1024 KiB / c=1 | 61.9 | 53.9 | 50867 | 0 |
| GET 1024 KiB / c=16 | 157.6 | 113.3 | 121083 | 0 |
| GET 1024 KiB / c=64 | 155.5 | 118.7 | 129877 | 0 |
| GET 1024 KiB / c=128 | 154.9 | 122.4 | 136036 | 0 |
| POST 10 KiB / c=1 | 26.1 | 22.2 | 187494 | 0 |
| POST 10 KiB / c=16 | 86.2 | 80.1 | 905873 | 0 |
| POST 10 KiB / c=64 | 91.5 | 80.6 | 962781 | 0 |
| POST 10 KiB / c=128 | 91.7 | 80.1 | 954488 | 0 |
| POST 100 KiB / c=1 | 40.9 | 25.5 | 126376 | 0 |
| POST 100 KiB / c=16 | 129.7 | 90.6 | 656341 | 0 |
| POST 100 KiB / c=64 | 148.5 | 95.5 | 695850 | 0 |
| POST 100 KiB / c=128 | 154.0 | 94.5 | 690451 | 0 |
| POST 1024 KiB / c=1 | 70.4 | 21.1 | 42185 | 0 |
| POST 1024 KiB / c=16 | 327.5 | 99.5 | 124811 | 0 |
| POST 1024 KiB / c=64 | 300.6 | 108.2 | 113851 | 0 |
| POST 1024 KiB / c=128 | 293.5 | 100.6 | 109358 | 0 |
| Latency / c=1 | 26.4 | 27.9 | 253738 | 0 |
| Latency / c=16 | 92.2 | 103.3 | 1302733 | 0 |
| Latency / c=64 | 93.9 | 100.9 | 1358183 | 0 |
| Latency / c=128 | 93.3 | 102.9 | 1384049 | 0 |

## Sustained mixed upload/download

One 300-second run per backend, 5-second warmup, 64 connections, alternating cached 1 MiB GET/POST requests. Payload rate is their aggregate, not separately asserted upload and download rates. This is a five-minute local stability observation, not a 24-hour soak or a leak proof.

| Backend | Aggregate Mbit/s | CPU % | Peak RSS MiB | First / last 30 s median RSS MiB | P50 / P95 / P99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| rust | 40815.599 | 198.9 | 101.22 | 90.53 / 93.31 | 11.142 / 25.242 / 30.382 | 0 |
| cpp | 23633.080 | 198.9 | 21.57 | 20.80 / 20.74 | 23.189 / 42.138 / 43.961 | 0 |

## Observed simultaneous connections

Separate correctness/capacity observation after performance collection: actual backend-owned ESTABLISHED sockets counted from Linux /proc, not just a client concurrency target. Each connection completes a health request and remains open; all must release after client close. This demonstrates the tested counts, not a maximum connection capacity.

| Backend | Requested / observed connections | Idle / active / peak RSS MiB | Threads | All released |
|---|---:|---:|---:|---|
| rust | 1 / 1 | 7.82 / 7.89 / 7.89 | 3 | yes |
| rust | 16 / 16 | 7.69 / 8.07 / 8.07 | 3 | yes |
| rust | 64 / 64 | 7.67 / 9.02 / 9.02 | 3 | yes |
| rust | 128 / 128 | 7.73 / 10.45 / 10.45 | 3 | yes |
| cpp | 1 / 1 | 15.16 / 15.17 / 15.17 | 6 | yes |
| cpp | 16 / 16 | 15.25 / 15.38 / 15.38 | 6 | yes |
| cpp | 64 / 64 | 15.22 / 15.73 / 15.73 | 6 | yes |
| cpp | 128 / 128 | 15.21 / 16.24 / 16.24 | 6 | yes |

## Artifact identity

Private paths, process IDs and raw captures are deliberately not published. Executable SHA256 values identify the measured builds:

- HTTP rust: `17a34335dedc7adf142a295a22e73f75a8444732ebcf52d7fbb095d5b6c826e7`
- HTTP cpp: `2bfe7e565fcdeff843b7a1a7a0b58a0859a0fb2b5c974f812ffa22c0618a9aea`
- core rust: `fc1f473392f6b6d50249fa9f73e7af3f2a51171ac1fbf8c39174f0e3d2cd30a7`
- core cpp: `fe5a8c097ff0edf0509f7b17b06ac8cccd97ea36f350aafa562eae17fab814f0`

## Interpretation boundary

These tables compare these implementations, compiler/dependency versions, tested profiles and hardware conditions—not an intrinsic property of C++ versus Rust. CPU-at-saturation alone is not efficiency; use CPU cost per unit of completed work. Memory and tail latency can trade against throughput. Small sample counts and WSL/host scheduling prevent broad statistical-significance claims.

See [final objective audit](acceptance-audit.md), [current release validation](recovery-release-validation.md), and [current Internet comparison](network-performance-recovery.md). No measured local speed difference by itself authorizes or justifies a production replacement.
