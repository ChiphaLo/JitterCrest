# Core milestone validation — 2026-10-25

Scope: independent C++ protocol/statistics/SDP/control library, **not** a complete
HTTP/WebRTC server. Production remains Rust. No production route/service was
modified. Original Rust sources and shared frontend were checked unchanged by
SHA-256 before and after this milestone.

## Actual results

| Check | Result | Scope |
|---|---|---|
| CMake Debug / CTest | Passed | Core self-test, strict base64, frame bounds, overflow, threading |
| CMake Release / CTest | Passed | Same tests, optimized native Linux x86_64 |
| Clang-Tidy | Passed | All current `src/*.cpp`, analyzer/bugprone/performance/modernize/concurrency categories |
| ASan + UBSan / CTest | Passed | Separate instrumented core build |
| TSan / CTest | Passed | Separate build including concurrent core calls |
| Rust/C++ differential | 3065/3065 passed | Debug, Release, ASan/UBSan and TSan executables |
| libFuzzer + ASan/UBSan | 20,000 runs passed | Frame/SDP/control/JSON/base64/UTF-8 parser harness, seed 20261025 |
| Reference HTTP baseline | 35 cases recorded | Rust only; C++ HTTP comparison pending |
| Full service compatibility | **Not yet tested** | HTTP/progress, native WebRTC, cancellation, resource/lifecycle behavior |
| CPU/RSS/throughput/latency benchmarks | **Not measured** | No language performance conclusion |
| Production replacement readiness | **Not achieved** | Rust remains the stable implementation |

The differential client compares discrete fields exactly and finite floating
values at `1e-12` relative/absolute tolerance. It normalizes only random result
fragment IDs. Arbitrary IEEE-754 frame timestamp bits compare exactly.

### Bugs found by comparison and fixed in C++, not in the reference

- SDP component `+1` is accepted/normalized by the actual Rust integer parser;
  priority/port fields still require decimal digits only.
- SDP tokenization uses Unicode White_Space, not locale-dependent ASCII splitting.
- Rust's default JSON recursion budget has a boundary that differs from the
  third-party C++ parser's unbounded default. The C++ input wrapper now rejects
  the same nested containers; the shared vectors exercise both sides of it.
- Malformed reassembled result types produce the matching identity/payload
  errors rather than escaping as an unrelated JSON type exception.

Clang-Tidy omits only conventional-vs-trailing return syntax and non-wire enum
storage sizing; these are cosmetic/micro-optimization preferences, not removed
safety gates. The unchecked optional access findings were fixed with explicit
checks, not suppressed. Warnings in third-party/system headers are outside this
source gate. The full service and dependency instrumentation still need their
own safety/lifecycle tests.

## Reproduction

See the root C++ README for package prerequisites, CMake presets and the shared
client invocation. This milestone used Clang 18.1.3 on Ubuntu 24.04 x86_64,
Boost 1.83, OpenSSL 3.0.13 and nlohmann-json 3.11.3. The reference oracle pins
serde_json 1.0.151, base64 0.22.1 and getrandom 0.3.4 to the current Rust lockfile.
No Rust binary, FFI or Rust service is part of the C++ library's runtime graph.

Fuzzing is finite evidence, not proof of absence of memory errors. Core TSan
does not prove a future network implementation race-free. All full-task
requirements remain tracked in the README until verified at their actual scope.
