# Receive-fairness snapshot: native Windows Internet HTTP comparison

This distinct experiment used the official `cd514ed` preserved-UI bundle and unchanged Rust UI reference on the same isolated server. It is not a relabeling of the older HK-to-JP report, a native RTC performance test, or evidence for the later message-cap correction.

## Matched profile

- Native Windows Python standard-library HTTPS keepalive generator, concurrency 1/4, no browser/GPU. The existing OS routing/proxy policy was not altered.
- Same independent Debian13 x86_64/glibc2.41 server and same copied Caddy, two-CPU affinity and matched transient systemd resource/hardening limits for both Release/ThinLTO backends. Production/co-tenants stayed running; not an exclusive-host experiment.
- Three AB/BA/AB rounds; 1 MiB GET/POST plus bodyless latency; 0.5s warmup and 2s admission. Last admitted requests drain into goodput. Ordinary scoped-CA and IP verification remain enabled.
- All **36 cases / 5,466 requests** passed with zero workload errors. Attempted application payload including warmup was **1591 MiB** under a fixed 2GiB limit; not exact wire/TLS bytes.
- Source, workload and binary identities stayed frozen. A separate earlier 36-case collection completed requests but overlapped a short local frontend test: it is retained and excluded from formal timing, not selected or discarded based on performance outcomes. This declared replacement had no overlapping builds/browser tests.
- Remote process CPU/RSS came from independent SSH snapshots. CPU ticks are10ms; resource intervals include coordination gaps and are not the exact client window. Windows generator CPU used process_time, including orchestration, rather than pretending to read Linux process counters locally.

## Results

Medians of three per-run values, not pooled percentiles. Brackets are observed ranges, not statistical confidence intervals. Payload Mbps and request latencies include WAN/client/TLS/proxy effects.

| Workload | c | Backend | Payload Mbps [min,max] | req/s | P50 ms | P95 ms | P99 ms [min,max] | Requests |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| GET | 1 | cpp | 198.892 [179.368, 203.903] | 23.71 | 39.756 | 58.443 | 111.819 [78.646, 119.652] | 140 |
| GET | 1 | rust | 183.537 [169.453, 194.825] | 21.88 | 42.922 | 64.266 | 137.228 [88.670, 141.830] | 132 |
| GET | 4 | cpp | 297.280 [250.501, 332.176] | 35.44 | 87.450 | 241.608 | 321.604 [220.644, 431.787] | 219 |
| GET | 4 | rust | 309.738 [258.369, 318.976] | 36.92 | 94.261 | 198.168 | 258.830 [252.255, 553.446] | 221 |
| LATENCY | 1 | cpp | 0.000 [0.000, 0.000] | 68.61 | 13.834 | 17.709 | 24.808 [20.719, 27.148] | 431 |
| LATENCY | 1 | rust | 0.000 [0.000, 0.000] | 65.49 | 11.799 | 17.639 | 22.071 [17.525, 45.035] | 423 |
| LATENCY | 4 | cpp | 0.000 [0.000, 0.000] | 264.68 | 13.891 | 20.674 | 27.211 [21.797, 31.775] | 1642 |
| LATENCY | 4 | rust | 0.000 [0.000, 0.000] | 278.96 | 14.283 | 17.873 | 24.126 [23.891, 24.183] | 1706 |
| POST | 1 | cpp | 144.645 [138.528, 153.612] | 17.24 | 54.866 | 81.581 | 83.079 [77.107, 149.683] | 106 |
| POST | 1 | rust | 136.547 [114.197, 146.433] | 16.28 | 58.143 | 79.956 | 131.078 [80.447, 190.639] | 96 |
| POST | 4 | cpp | 209.199 [204.002, 247.716] | 24.94 | 86.402 | 348.738 | 489.599 [478.962, 548.217] | 176 |
| POST | 4 | rust | 229.278 [210.170, 242.320] | 27.33 | 138.337 | 228.125 | 259.541 [198.972, 385.135] | 174 |

| Backend | Idle RSS MiB | Lifetime peak MiB | Sum backend CPU s | Sum proxy CPU s | Initial threads |
|---|---:|---:|---:|---:|---:|
| rust | 7.637 | 11.684 | 1.01 | 3.04 | 3 |
| cpp | 15.848 | 16.711 | 1.10 | 3.14 | 12 |

## Limits and cleanup

The short closed-loop samples do not establish open-loop SLOs, a maximum connection capacity, a long network soak, or language-level statistical significance. Lifetime peak is not a per-case RSS peak. Background server activity and client/WAN variation remain factors; do not subtract RTT to invent backend CPU.
All owned stage units/listeners/ephemeral secrets were removed and production process/unit/configuration/release snapshots matched. A separate subsequent cleanup check is required and recorded privately. No production route or service was changed. The earlier failed/excluded experiments remain retained.
See [local tables](local-performance-current.md), [findings](current-performance-findings.md), [public functionality](public-staging-validation.md), and the generic [benchmark client](../benchmarks/README.md). Raw operational paths, hosts, credentials and diagnostic records are deliberately not published.
