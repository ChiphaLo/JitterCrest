# C++20 objective acceptance — completed with explicit limits

Runtime source: `019ff35821f974d80ab223e0fed4f19f7b01a6fb`.
This is the final development/deliverable audit, not a universal equivalence or
zero-risk certification. It supersedes earlier incomplete status summaries;
historical reports retain their dates, artifact identities and failed runs.
The subsequent user-authorized merge/deployment is a separate operational gate.

## Original fifteen-section requirement map

| Section | Delivered evidence | Scope / limitation |
|---|---|---|
| 1 Independent backend | Complete C++20 executable: pure core, Beast HTTP/streaming/progress, native libdatachannel ICE/DTLS/SCTP, UI and no-UI agents | No Rust binary, Rust FFI or Rust service in runtime; test-only oracle is separate |
| 2 Modern C++ quality | RAII leases/resources, typed views with owning awaited buffers, serialized executors, bounded worker pool, exception boundaries; applied Core Guidelines in README | Finite review/static/runtime evidence, not proof that all bugs are impossible |
| 3 Rust baseline | Rust routes/config/protocol/statistics/runtime/deployment inspected; current Rust is the paired reference | Node archive is retained historical data, not authority; private deployment aliases stay private |
| 4 HTTP/wire/statistics | HTTP126 paired cases;10KiB/100KiB/1MiB; progress/CORS/cache/status/limits;128B little-endian probes;20pps×2s and100pps×20s; core3065/config521/served-frontend510 vectors | Discrete values exact, floating1e-12 absolute/relative; random fragment identifiers normalized only. Client-side metrics remain client-side |
| 5 Architecture | Mature Beast/Asio, OpenSSL, libcurl, JSON and pinned patched libdatachannel; pure protocol/statistics separated from network ownership | No speculative allocators, SIMD, lock-free structures or Rust wrapper |
| 6 Concurrency/resources | Partial I/O, backpressure, half-close/reset/slow client/deadline/drain/cancellation; bounded peers, queues, bytes, rates, buffers, provider jobs | Resource13, wire22, repeated-close100 and shutdown3 suites per affected release/sanitizer profile; configured capacity is not maximum performance capacity |
| 7 Timing/performance design | Monotonic planned/actual/receive accounting; measured CPU cost per completed work; source-backed native admission/RTO correction | Enqueue is not wire delivery; original25s result window and.99 completeness gate preserved |
| 8 Rust preservation |86 protected source/frontend/vendor baseline files checked unchanged; historical archives/backups preserved; independent bundles/services | Development and acceptance left production unchanged. A later explicitly authorized cutover must retain Rust rollback |
| 9 Shared compatibility | Deterministic paired clients for frames, JSON/base64/SDP, HTTP, resources, actual browser transport/roles, invalid/truncated/duplicate/reordered and close/recovery cases | Final current-source public four combinations/eight node cases passed; earlier transport-path failures remain recorded rather than erased |
| 10 Fair benchmarks | Five core rounds; three HTTP rounds/168 rows/15,292,678 requests;300s mixed load per backend/2,305,566 requests; real observed1/16/64/128 connections; separate36-case/5,914-request Internet profile | Matched Release/ThinLTO, hardware/OS/client/payload/warmup/concurrency; zero workload errors. WAN/client/WSL noise and finite durations remain explicit |
| 11 Build/toolchain | CMake, pinned native bootstrap/patches, Debug/Release, independent ASan+UBSan/TSan presets, format/tidy configs; three independent relocated packages | Linux x86_64 validated. aarch64 was conditional and is not claimed built/run; bundles require glibc>=2.38 |
| 12 Safety checks | Four-profile CTest8; ASan+UBSan and TSan affected runtime matrices, real native-race negative controls and repairs;20,000 finite core fuzz runs; full static analysis executed | Full raw HTTP Tidy is NOT clean: two Clang18/Boost coroutine-model findings have exact repro/CFG/negative-control evidence, no suppression. System OpenSSL/libcurl not wholly source-instrumented |
| 13 Independent deployment | Separate binary/workdir/port/unit/log/release templates, bundled libraries/licenses/manifests; actual isolated three-host IP-HTTPS/UDP checks, staged backout, complete storage budget and cleanup | UI/generic-UI/agent each CTest8 plus relocation/message/recovery; no live config/secret/ops scripts in public repository |
| 14 Actual execution | Implemented, compiled, tested, compared, sanitized, benchmarked and documented; regressions repaired with retained before/after evidence | No test deletion, weakened metric or relaxed deadline to manufacture acceptance |
| 15 Final deliverables | Complete server, shared tests, safety reports, actual performance tables, architecture/build/ownership/deployment documentation and eight explicit answers below | Deliverables accepted for the documented finite profiles, not universal network identity or a language-superiority claim |

## Current-source safety and release gate

All four current-source profiles passed CTest8, config521, visibility16 and
paired native message boundaries. Release/ASan+UBSan/TSan additionally passed
HTTP126, resource13, untraced close100, wire22, shutdown3 and respectively8/4/4
paired recovery cases. Core/parser code was unchanged from its3065-vector and
20,000-run fuzz validation; that scope is not expanded to arbitrary network code.

The recovery regression now returns truthful incomplete reasons within the
unchanged frontend window under the declared6–20s blackout. The genuine older
executable fails that test. See [recovery correction](reference-admission-recovery.md).
The independent three role packages and fresh current-source Windows-browser
public8/8 profile, fresh Restart IDs and20 exact probe denials are documented in
[release validation](recovery-release-validation.md). Final local/WAN timing was
collected serially after those validations; all relevant inputs remained frozen.

## Compatibility differences and retained limitations

- The native C++ and Rust stacks are not packet-identical. Native65536 negotiation,
  the reference65535 read/close boundary, zero-retry128B probes, reference RTOs and
  application deadlines are explicitly covered. C++ has per-channel64KiB admission
  plus native65536 buffering, not an asserted byte-for-byte copy of Rust's shared
  128KiB pending queue. Severe-loss finite outcomes/timely incomplete reasons
  match the recorded reference profile; universal adverse-path equivalence is
  not claimed.
- Shared late-ACK/receipt-snapshot behavior is retained rather than modifying
  Rust during migration. Earlier source-specific control stalls, sanitizer races
  and close-alert failures remain versioned with their fixes/tests. Historical
  Internet failures are not retrospectively converted to passes by a later run.
- Public defaults/configuration are generic; deployment-specific probe IDs,
  host aliases and CSP origins require explicit operational mapping. No real
  infrastructure values are embedded in this public edition.
- The two unsuppressed full-HTTP static-analysis diagnostics remain visible and
  triaged as scoped analyzer coroutine-model false positives, not ignored
  lifetime defects. See [analysis evidence](coroutine-analysis.md). No current
  untriaged implementation/sanitizer failure is represented as a pass.
- Five-minute local stability, short Internet HTTP samples and finite browser
  profiles do not establish multi-day uptime, exhaustive third-party provider
  compatibility, aarch64 support or absence of every race/UB.

## Required final answers

1. **Functionally equivalent?** Compatible in the tested HTTP/API/wire/statistical,
   resource and real-browser profiles, including mixed Rust/C++ nodes; not a claim
   of identical native transport behavior under every possible network condition.
2. **Higher throughput?** Rust led all12 local download and all12 upload median
   cases. For1MiB/c64: Rust40.892/36.223Gbit/s versus C++31.029/26.360 download/upload.
3. **Lower CPU?** Rust had lower bulk CPU-seconds/GiB; C++ lower cost for bodyless
   latency requests. Saturation CPU percentages alone are not efficiency.
4. **Lower memory?** Rust lower idle RSS; C++ lower large-payload high-load peak
   RSS, at lower completed throughput. See complete matched resource tables.
5. **More stable high-concurrency latency?** Mixed. C++ c128 bodyless P99 was
   3.403ms versus Rust6.047ms; upload/mixed tails were worse. No open-loop SLO claim.
6. **Significant difference?** Descriptive finite ranges only, no population-level
   significance test or universal language score. WAN samples include link/client/TLS/proxy effects.
7. **Additional complexity?** Yes: native dependency patches, callback/lifetime
   ownership, sanitizer rebuilds and upstream maintenance are real C++ costs.
8. **Reason to replace Rust?** Not for overall throughput, CPU efficiency or lower
   maintenance based on these measurements. An authorized operational deployment
   is separate from performance recommendation; retain Rust source and rollback.

## Reports

- [Architecture, dependencies, builds and ownership](../README.md)
- [Current complete local tables](local-performance-recovery.md)
- [Current interpretation](performance-findings-recovery.md)
- [Current real-network tables](network-performance-recovery.md)
- [Current safety/recovery matrix](reference-admission-recovery.md)
- [Current independent packages/public roles](recovery-release-validation.md)
- [Static-analysis classification](coroutine-analysis.md)
- [Historical transport diagnoses and boundaries](transport-compatibility.md)

Raw logs, hosts, credentials, recovery archives and private operational scripts
remain outside the public source repository. Production merge/cutover status is
recorded separately, never inferred from a development acceptance table.
