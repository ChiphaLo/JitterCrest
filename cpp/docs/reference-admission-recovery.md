# Reference-scale RTC admission and recovery correction

The application now initializes native SCTP with the reference's RTO values:
initial3000ms, minimum1000ms and maximum60000ms. Its native send buffer is64KiB,
alongside the existing64KiB per-channel application-queue threshold and bounded
two-second admission wait. These settings are applied before native preload.

The reference production pending queue has a128KiB semaphore budget. This is
**reference-scale admission**, not a claim that native in-flight, per-channel
and reference shared-queue byte accounting are identical. In particular, the
two channel queues are distinct. The new buffer applies to **WebRTC SCTP only**,
not the HTTP upload/download buffers or maximum throughput payloads.

No128-byte field, probe schedule, maxRetransmits0, association retry budget,
application/session/result deadline, statistics definition or .99 gate changed.
The20-second frontend result sequence still has its original25-second window.
Native retransmission timers are not an extension of that application window.
Rust, its dependencies, frontend and production routes/services were not edited.

## Evidence and rejected alternatives

Native header-only diagnostics on the prior configuration observed outgoing
FORWARD-TSN attempts at about6.190,6.594,7.401,9.016,12.245,18.704 and28.800s
after the first native ready-send. No incoming SACK was observed between20 and28s;
the next was near28.923s. The result's first DATA output was near28.800s despite
earlier native admission. No binary DATA TSN was retransmitted. These are one
controlled trace's header metadata, not decrypted application bodies or a
universal network diagnosis. The trace and raw diagnostics stay private.

Readback confirmed that the prior native RTO was1000/200/10000ms. Separate
actual socket readback of both new variants confirmed3000/1000/60000ms and,
respectively,1048576 or65536 send-buffer bytes. Real20pps×2s traffic completed
in both readback fixtures. Settings were not merely assumed applied.

An older, pre-message-boundary full-RTO experiment had worsened a periodic-loss
tail and was rejected. A corrected-source64KiB-only experiment also failed to
fix the deadline. Those failures remain retained. A new fixed20-case schedule
tested the corrected source's default, reference-RTO-only, combined settings
and unchanged Rust. This was declared before traffic, without builds or other
tests overlapping collection; no values were tuned after observing results.

Raw result arrival seconds (two severe rounds reverse backend order):

| Policy | Prior default | RTO only | Combined correction | Rust |
|---|---:|---:|---:|---:|
| delay | 21.419 | 21.413 | 21.526 | 21.147 |
| periodic | 24.129 | 23.821 | 23.641 | 23.495 |
| burst | 23.256 | 22.989 | 22.980 | 21.873 |
| up-blackout | 30.087 / 30.072 | 22.513 / 22.523 | 23.148 / 23.134 | 23.865 / 23.871 |

RTO-only recovery was timely but still claimed a complete scheduler where Rust
reported failure. The combined correction returned the same incomplete reason
codes, `DOWNLOAD_SEND_FAILED` and `DOWNLOAD_SCHEDULER_INCOMPLETE`, in both
blackout rounds, within the unchanged result window. Delay-only cases retained
all2000 upload/download receipts. Eight additional fixed AB/BA periodic/burst
cases also completed collection; all four combined-candidate results arrived
within25s. Periodic loss is **not** a valid .99-density frontend measurement,
and no loss case is relabeled healthy merely because its server returned.

These matched policies are not packet-identical traffic, Internet benchmarks
or proof of universal timing equivalence. The public recovery regression uses
real encrypted loopback transport with60ms one-way delay and a6–20s upstream
application-datagram blackout. It requires a timely **incomplete** result, not
a valid completed stability test. Its healthy20pps×2s branch requires all40
receipts. The original executable failed exactly its C++ blackout deadline
check; the unchanged Rust counterpart passed. Both observations are retained.

```sh
python3 cpp/tests/rtc_recovery_contract.py \
  --cpp /local/cpp/speedtest-cpp --rust /local/reference/speedtest-rust \
  --rounds 2 --output /private/evidence/rtc-recovery.json
```

## Current-source safety scope

The exact experimentally validated source was selected only after verifying a
source backup and the negative control. Current `src/rtc.cpp` SHA256 is
`2db73f7be25ac03e42ef3ffb442c4d2616608286f2e7c491d1f095fee499c67a`. The following is a **new** matrix including the final message
read guard and combined settings, not a relabeling of the older cap-only runs.

| Profile | CTest | Config vectors | Paired visibility | Paired message boundary | Paired HTTP | Resources | Untraced closes | Wire | Shutdown | Paired recovery cases |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| release | 8 | 521 | 16 | yes | 126 | 13 | 100 | 22 | 3 | 8 |
| asan-ubsan | 8 | 521 | 16 | yes | 126 | 13 | 100 | 22 | 3 | 4 |
| tsan | 8 | 521 | 16 | yes | 126 | 13 | 100 | 22 | 3 | 4 |
| debug | 8 | 521 | 16 | yes | — | — | — | — | — | — |

The message fixture covers negotiated65536, valid65535 plus real measurement,
close65536/tombstone409 and browser rejection of65537, with an explicit legal
65536 application-control ceiling in both fixtures. Default control policy
remains16384. Sanitizer suites used their separate instrumented C++ and native
builds with halt-on-error; ASan also checked leaks. These finite results do not
prove absence of every race/UB, and system OpenSSL/libcurl are not claimed fully
source-instrumented. Scoped RTC Clang-Tidy and clang-format passed; the two
previous full-HTTP Clang18/Boost model findings remain explicitly nonzero and
audited, not silently suppressed or declared clean. Core/parser sources were
unchanged; the earlier finite20000-run core fuzz scope is not expanded here.

The source is not yet a current deployed release. Earlier boundary-candidate
archive hashes predate these settings. New independent packages, current public
multi-host role acceptance, final objective audit and conditional production
deployment with Rust rollback remain pending. No main merge or production
cutover was performed by this correction.
