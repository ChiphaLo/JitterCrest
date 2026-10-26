# Objective acceptance audit — incomplete

This maps the original C++20 migration objective to finite evidence and remaining
work. It is **not** a declaration that every requirement is complete. Rust source,
frontend, historical archives, production services/routes and rollback material
remain protected. A source commit or a passing subset does not authorize cutover.

## Requirement map

| Objective section | Implemented / inspected evidence | Acceptance boundary |
|---|---|---|
| 1 — independent equivalent backend | Independent C++ core, Beast HTTP and libdatachannel native transport; separate UI/agent CMake outputs and actual processes | No Rust binary/FFI/service implements a C++ route. Universal adverse-path equivalence is not established |
| 2 — C++20 and engineering rules | CMake C++20, RAII connection/limit/progress ownership, serialized executors, bounded provider pool, explicit dependency patches; README lists applied Core Guidelines | Finite static/runtime evidence is not proof of absence of every UB/race |
| 3 — authoritative Rust baseline | Current Rust HTTP/WebRTC/configuration implementation is the paired reference; documented HTTP cadence, zero-retry probes, percentile and Node-era documentation differences | Historical Node archives are retained, not the implementation reference |
| 4 — HTTP/API/wire/statistics | 126 paired HTTP contracts including10KiB/100KiB/1MiB, streaming/progress/status/headers/CORS;128-byte native frames;20pps×2s and100pps×20s; core3065 differential vectors and configuration521 | Native message negotiation/read-boundary defects were newly corrected. New bundles/public checks must carry that source, not old artifacts |
| 4 — client-computed metrics | Actual-served helper arithmetic510 vectors: percentiles, scaled MAD, adjacent-difference mean, population standard deviation, strict timing thresholds and trimmed stable throughput | Shared unchanged frontend, not invented C++/Rust server calculations or a mocked RTC pass. Numeric tolerance is1e-12 relative/absolute |
| 5 — framework/module design | README documents Beast incremental I/O/lifetimes, native WebRTC selection, pure protocol/statistics separation and mature dependencies | No speculative custom allocator/assembly/SIMD or claim that C++ must be faster |
| 6 — I/O/concurrency/resources | HTTP/lifecycle/resource contracts; native admission/expiry/FIFO/control/byte limits; separate close/replacement and shutdown checks; bounded send waits and callback cleanup | Native pending-queue/backpressure and severe-loss recovery differences still require the final transport decision |
| 7 — timing/performance design | Monotonic scheduler/executor samples, exact sequence/ACK identity, real partial-reliability loss tests, measured throughput/CPU/RSS | Do not treat enqueue acceptance as proof of wire delivery; no timeout or density gate was relaxed |
| 8 — preserve Rust | Protected-source/vendor/frontend manifests; independent VPS before/after process, binary, unit, Caddy and release snapshots | No production switch or main merge has occurred |
| 9 — shared compatibility clients | Shared deterministic core/HTTP/native browser clients; invalid/truncated/duplicate/reordered/half-close/cancellation/resource scenarios; original failures retained | Shared post-result late-ACK limitation and adverse-path failures are documented, not silently repaired in Rust |
| 10 — fair benchmarks | Five alternating core rounds, three HTTP rounds, per-backend300s mixed load and actual1/16/64/128 connections; separate36-case Internet experiment with independent remote CPU/RSS | Reports identify pre-message-cap `cd514ed` artifacts. They are not measurements of later executables or statistical proof of language superiority |
| 11 — builds/toolchain | Debug/Release/separate ASan+UBSan/TSan presets, pinned native bootstrap, standalone source builds and relocation archives, clang-format/tidy configuration | Linux x86_64 verified. Linux aarch64 is not claimed built/run; conditional hardware/toolchain coverage remains explicit |
| 12 — safety | Separate sanitizer runtime suites, actual native mbuf regression,20,000 finite core fuzz runs; reduced coroutine fixture and negative controls | Full raw HTTP Tidy still has two audited Clang18/Boost model findings; it is not a clean full-service result. System OpenSSL/libcurl are not claimed fully source-instrumented |
| 13 — deployment | Separate systemd/Caddy/env templates; UI/agent/generic roles; bundle manifests/licenses, source/patch identity and relocated runtime checks; guarded staging/backout | Corrected independent bundles now pass local relocation/native/normal-UI checks. Their current public role matrix remains pending. Preserve Rust rollback and unrelated services |
| 14 — actual implementation flow | Sources, real builds, tests, comparison, sanitizers, benchmark data and technical reports exist rather than a demo/design-only handoff | Finishing documentation must not replace resolving or explicitly auditing remaining objective gates |
| 15 — delivery/final decision | README and linked architecture/build/compatibility/safety/resource/performance/deployment reports; generic public source and test clients | Final overall acceptance and conditional merge/production deployment are **not yet achieved** |

## Versioned verification, not a merged green table

- The receive-fairness snapshot passed its current-source Release and separate
  sanitizer resource/wire/close/shutdown matrix. Its native-Windows public profile
  passed eight node cases and four mixed backend combinations. Those hashes are
  preserved and do not identify a later correction.
- The native message-cap correction passed its own four-profile builds/CTest8
  and paired negotiation. Its Release/ASan+UBSan/TSan resource13, close100, wire22
  and shutdown3 results precede the additional native read-boundary guard.
- The final read-boundary guard then rebuilt all four profiles and passed CTest8
  plus paired real65535/65536/65537-byte boundary,20pps×2s, close and409 checks.
  `transport-compatibility.md` distinguishes these scopes and retained failures.
  It is not claimed that the earlier full matrix was rerun after the last guard.
- The last official-bundle severe-loss diagnostic still returned the C++ result
  at about30.1s for a20s test, beyond the unchanged frontend25s result window;
  Rust returned an incomplete result at about23.9s. The C++ native message-cap
  correction is separate and **does not prove that recovery issue fixed**.
  The corrected raw executable has now reproduced that delay independently.
  A fixed15-case comparison rejected a private64KiB send-buffer experiment:
  it matched Rust's failure reasons but still arrived at30.81s in both severe
  rounds, versus23.87s for Rust. A separate four-case metadata check verified
  the actual socket settings and native result-admission timing. No setting
  was promoted and neither diagnostic is frontend acceptance. See
  [queue diagnostic](send-queue-diagnostic.md).
- The last clean Internet HTTP collection had36 cases /5,466 requests and zero
  workload errors. A complete earlier collection overlapping a short frontend
  test is retained but excluded from formal timing. All temporary units/secrets
  were removed and independent production checks matched on all three hosts.
- Subsequent corrected public-source UI/generic-UI/agent ThinLTO builds each
  passed CTest8 and archive relocation/checksums/actual-library mapping, fixed
  upload/download/progress and paired native boundary checks. The two normal
  webpages also passed autoRTT and a fresh-offer-correlated Restart. These are
  new local artifact results, not a corrected public8/8 profile or a new
  sanitizer/performance matrix. See [candidate builds](corrected-candidate-builds.md).

## Answers supported by the measured scope

1. **Equivalent?** Normal/offline/HTTP and recorded native role profiles have
   substantial paired evidence. Do not claim complete adverse-path equivalence
   or apply an older package's success to newer source.
2. **Throughput?** Rust led all twelve local download cases and eleven of twelve
   upload cases in the pinned revalidation.
3. **CPU?** Rust had lower bulk-transfer CPU cost per GiB; C++ had lower cost per
   lightweight latency request in the tested cases. Saturation CPU percent alone
   is not efficiency.
4. **Memory?** Rust used less idle memory; C++ used less large-payload,
   high-concurrency peak RSS, at lower completed throughput.
5. **High-concurrency latency?** Mixed results. C++ had lower lightweight-request
   P99, but not uniformly lower payload or mixed-load tails.
6. **Significance?** Finite descriptive ranges, no population-level significance
   or universal language ranking. Internet results include WAN/client/proxy.
7. **Safety/maintenance cost?** C++ adds real native-library patching, callback,
   lifetime and sanitizer obligations; the diagnosed races are not concealed.
8. **Reason to replace Rust?** Current results do not justify replacement on
   overall throughput, CPU efficiency or lower maintenance cost. A user's
   conditional deployment request still requires full objective acceptance.

Read [transport compatibility](transport-compatibility.md),
[public profiles](public-staging-validation.md),
[current pinned local findings](current-performance-findings.md),
[Internet revalidation](network-performance-fairness.md) and
[coroutine analysis](coroutine-analysis.md) for exact evidence/limits. Raw
operational diagnostics, credentials and deployment configurations stay private.
