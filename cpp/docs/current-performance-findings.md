# Receive-fairness snapshot performance findings

This revalidation measured the current `cd514ed` runtime source, independently
built from the sanitized public checkout with Release/ThinLTO and the current
patched native dependency. It does not rename the earlier frozen benchmark
binary or results. Complete tables, ranges, toolchain, resources, errors and
executable identities are in [local-performance-current.md](local-performance-current.md).
This snapshot predates the native message-cap correction; it is not a claimed
measurement of that later executable. The separate same-snapshot Internet
experiment is in [network-performance-fairness.md](network-performance-fairness.md).

## Verified experiment

- Five alternating native-core rounds: 40 rows, matching input checksums,
  warmup checksums and iteration counts.
- Three alternating HTTP rounds: 168 rows / **15,821,064 requests**, zero errors.
  Both backends used the same 1 s warmup, 5 s measurement, payloads, concurrency,
  two-CPU server budget and separate native load generator.
- One 300 s mixed 1 MiB GET/POST run per backend: **2,313,060 requests** combined,
  zero errors and clean exits. This is not a day-long soak or a leak proof.
- Actual 1/16/64/128 backend-owned connections were observed and all released.
- The measured build passed eight CTests and all 126 paired HTTP contracts
  before timing. Sources, workload drivers, binaries and mapped native library
  hashes remained unchanged; all 86 protected baseline files still matched.
- Original historical reports/binaries remain preserved. The new measured
  application `.text` section matches the packaged UI executable. Dependency
  prefixes have distinct ELF identities, not an asserted byte-for-byte match;
  exact patched source reproduction and the compiled 64-datagram receive bound
  were independently checked. Raw operations, build paths and logs stay private.

## What the current measurements show

Rust had higher median throughput in all twelve download cases and eleven of
twelve upload cases. The exception was 10 KiB upload at concurrency 16, where
C++ was about 3.3% higher and observed ranges overlap. It is not a general win.

| Local 1 MiB payload, concurrency 64 | Rust Gbit/s | C++ Gbit/s |
|---|---:|---:|
| Download | 42.294 | 30.566 |
| Upload | 34.406 | 28.693 |

These are loopback application rates, not Internet or NIC bandwidth. At
concurrency 128, 1 MiB download cost about 0.413 versus 0.527 CPU-seconds/GiB
and upload about 0.487 versus 0.666, Rust versus C++. CPU percentage at saturation
alone would conceal that efficiency difference.

For the bodyless `/latency` endpoint, C++ had about 1.19–1.36 times the request
rate. At concurrency 128 its median P99 was 3.189 ms versus Rust's 5.856 ms.
Payload P99 results were mixed and measured at different completed throughputs;
there is no blanket high-concurrency latency winner or open-loop SLO claim.

Rust had lower idle RSS, about 7.8–8.2 MiB versus C++'s 15.3–15.6 MiB. C++ had
lower high-concurrency large-payload peak RSS: at concurrency 128, 1 MiB download
was 21.20 versus 86.71 MiB, and upload 27.77 versus 119.72 MiB, C++ versus Rust.
During the five-minute mixed run, C++ used a 21.50 MiB peak versus Rust's
100.33 MiB, but delivered only 22.512 versus 42.146 Gbit/s aggregate payload.
That is a throughput/memory tradeoff, not uniformly lower total resource cost.

Native frame decoding ranges overlapped (5.320 versus 5.335 ns/op, C++ versus
Rust). C++ hashing took 2.78 times the ASCII-ID time and 2.63 times the Unicode-ID
time; its 512-sample P95 operation took about 16% less time. These operations do
not form a whole-service language score.

## Replacement decision and limits

The results do **not** justify replacing Rust for overall throughput or CPU
efficiency. C++ offers some measured memory/lightweight-request advantages but
adds native dependency patching, callback/lifetime auditing and sanitizer upkeep.
Its existence as an independent deployable implementation is not evidence of
lower maintenance cost or universal superiority.

Three/five finite alternating rounds are descriptive, not a statistical
significance test. WSL guest affinity is not exclusive Windows physical-core
ownership; compiler/runtime, generator and host scheduling remain factors.
The older separate Internet report remains identified by its own artifact hashes
in `network-performance.md`. A distinct Windows-client Internet revalidation of
the receive-fairness snapshot is in `network-performance-fairness.md`; none of
these experiments may be silently relabeled as another artifact. Public native-Windows functionality passed eight node cases, but
retained adverse-path failures and the severe-outage timing boundary still belong
in the final compatibility decision. Performance evidence does not waive them
or independently authorize a production switch.
