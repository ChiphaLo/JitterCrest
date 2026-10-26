# Recovery-source snapshot: native Windows Internet HTTP comparison

This distinct experiment used the source `019ff35` preserved-UI bundle and unchanged Rust UI reference on the same isolated server. It is not a relabeling of the older HK-to-JP report, a native RTC performance test, or a measurement of older artifacts.

## Matched profile

- Native Windows Python standard-library HTTPS keepalive generator, concurrency 1/4, no browser/GPU. The existing OS routing/proxy policy was not altered.
- Same independent Debian13 x86_64/glibc2.41 server and same copied Caddy, two-CPU affinity and matched transient systemd resource/hardening limits for both Release/ThinLTO backends. Production/co-tenants stayed running; not an exclusive-host experiment.
- Three AB/BA/AB rounds; 1 MiB GET/POST plus bodyless latency; 0.5s warmup and 2s admission. Last admitted requests drain into goodput. Ordinary scoped-CA and IP verification remain enabled.
- All **36 cases / 5,914 requests** passed with zero workload errors. Attempted application payload including warmup was **1616 MiB** under a fixed 2GiB limit; not exact wire/TLS bytes.
- Source, workload and binary identities stayed frozen. Earlier source snapshots, including the separately excluded overlap run, remain retained. This new source019ff collection began only after the local timed benchmark job ended and had no overlapping builds/browser tests/local benchmarks.
- Remote process CPU/RSS came from independent SSH snapshots. CPU ticks are10ms; resource intervals include coordination gaps and are not the exact client window. Windows generator CPU used process_time, including orchestration, rather than pretending to read Linux process counters locally.

## Results

Medians of three per-run values, not pooled percentiles. Brackets are observed ranges, not statistical confidence intervals. Payload Mbps and request latencies include WAN/client/TLS/proxy effects.

| Workload | c | Backend | Payload Mbps [min,max] | req/s | P50 ms | P95 ms | P99 ms [min,max] | Requests |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| GET | 1 | cpp | 205.338 [199.109, 216.450] | 24.48 | 37.712 | 47.140 | 112.836 [109.533, 113.937] | 149 |
| GET | 1 | rust | 193.416 [186.945, 198.070] | 23.06 | 41.605 | 50.226 | 110.587 [50.600, 132.264] | 140 |
| GET | 4 | cpp | 319.961 [295.105, 345.676] | 38.14 | 76.434 | 230.395 | 358.395 [204.547, 526.212] | 241 |
| GET | 4 | rust | 310.765 [295.083, 336.366] | 37.05 | 91.690 | 172.107 | 455.452 [180.264, 510.256] | 234 |
| LATENCY | 1 | cpp | 0.000 [0.000, 0.000] | 75.80 | 13.033 | 14.097 | 17.385 [16.866, 18.915] | 455 |
| LATENCY | 1 | rust | 0.000 [0.000, 0.000] | 62.73 | 15.790 | 16.784 | 19.633 [17.222, 30.819] | 376 |
| LATENCY | 4 | cpp | 0.000 [0.000, 0.000] | 306.10 | 13.207 | 15.672 | 16.994 [16.694, 18.992] | 1838 |
| LATENCY | 4 | rust | 0.000 [0.000, 0.000] | 334.51 | 11.538 | 14.000 | 15.713 [15.568, 17.897] | 1962 |
| POST | 1 | cpp | 129.211 [120.507, 136.703] | 15.40 | 62.750 | 74.423 | 105.855 [75.973, 271.176] | 93 |
| POST | 1 | rust | 148.981 [146.415, 151.407] | 17.76 | 55.447 | 69.991 | 75.739 [73.762, 82.701] | 109 |
| POST | 4 | cpp | 207.657 [204.988, 208.885] | 24.75 | 121.487 | 362.385 | 395.876 [197.674, 580.159] | 159 |
| POST | 4 | rust | 202.758 [199.925, 221.837] | 24.17 | 154.358 | 201.200 | 210.903 [208.682, 330.244] | 158 |

| Backend | Idle RSS MiB | Lifetime peak MiB | Sum backend CPU s | Sum proxy CPU s | Initial threads |
|---|---:|---:|---:|---:|---:|
| rust | 7.516 | 12.129 | 1.05 | 2.94 | 3 |
| cpp | 15.738 | 16.418 | 0.95 | 2.84 | 12 |

## Limits and cleanup

The short closed-loop samples do not establish open-loop SLOs, a maximum connection capacity, a long network soak, or language-level statistical significance. Lifetime peak is not a per-case RSS peak. Background server activity and client/WAN variation remain factors; do not subtract RTT to invent backend CPU.
All owned stage units/listeners/ephemeral secrets were removed and production process/unit/configuration/release snapshots matched. A separate subsequent exact-run SSH cleanup check passed and is recorded privately. No production route or service was changed. The earlier failed/excluded experiments remain retained.
See [local tables](local-performance-recovery.md), [findings](performance-findings-recovery.md), [public functionality](recovery-release-validation.md), and the generic [benchmark client](../benchmarks/README.md). Raw operational paths, hosts, credentials and diagnostic records are deliberately not published.
