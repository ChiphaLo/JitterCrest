# JitterCrest

[简体中文](README.zh-CN.md) | English

JitterCrest is a self-hosted network quality tool. Its maintained backend is C++20: bounded asynchronous HTTP streaming, upload
progress and native WebRTC DataChannel probes. The browser renders download /
upload charts and RTT, jitter, percentile, loss, ordering and stability reports.
Building and running the active service requires neither Rust nor Node/npm.

## Layout

- `cpp/`: active server, protocol/statistics logic, tests, build presets, benchmarks
  and generic deployment templates.
- `public/`: active shared browser application, embedded in the UI executable.
- `archive/rust/`: frozen Rust source, lockfile, vendored SCTP dependency, original
  tests/templates/frontend and optional historical comparison oracle.
- `archive/node/`: older sanitized Node reference.

Archives are excluded from the default CMake build and CI. They are historical
references, not maintained alternative production backends. Legacy configuration names do not imply a Rust runtime dependency.

## Build and test

Linux x86_64 is validated. Install CMake >=3.22, Ninja, a C++20 compiler (validated
with Clang18), Boost.System1.83, OpenSSL3, libcurl>=7.85 with asynchronous DNS,
nlohmann-json>=3.11, Python3 and Git. The native transport bootstrap pins
libdatachannel0.24.6 and applies the versioned dependency patches.

```sh
CC=clang-18 CXX=clang++-18 \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=clang++-18 \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure

# Local HTTP smoke. The executable embeds the UI; there is no JS build step.
SPEEDTEST_HOST=127.0.0.1 PORT=32132 build/cpp/speedtest-cpp
# In another terminal:
curl -fsS http://127.0.0.1:32132/health
```

Add `-DSPEEDTEST_WEB_UI=OFF` for an independent measurement-only agent. It does
not embed/serve the webpage. Existing `cmake -S cpp ...` builds and the separate
Debug, Release, ASan+UBSan and TSan presets in `cpp/` remain supported.

Native RTC is explicitly enabled with `SPEEDTEST_RUST_RTC_EXPERIMENTAL=1` after
configuring a real public IPv4 and validating UDP reachability. The legacy name
is retained as a configuration key only; no Rust process is called. Without it,
RTC requests fail closed503. `.env.example` is an environment-variable reference;
the executable does not automatically load dotenv files.

## Configuration and deployment

Published defaults are generic and use only a same-origin local node. Configure
your own `SPEEDTEST_NODES`, allowed origins, public UDP mapping and TLS proxy.
Never expose a listener trusting arbitrary forwarded identity headers. Use a
non-root service and bounded resources. UI, probe-only and data-agent roles are
described in the [C++ documentation](cpp/README.md); generic service/proxy examples
and packaging tools are in [`cpp/deploy/`](cpp/deploy/).

No credentials, real deployment configuration, binary releases, backups or raw
operational captures belong in this repository. See [security](SECURITY.md) and
[third-party notices](THIRD_PARTY.md).

## Validation and history

- [Compatibility and safety acceptance](cpp/docs/acceptance-audit.md)
- [Measured Rust/C++ performance comparison](cpp/docs/performance-findings-recovery.md)
- [Initial authorized production rollout](cpp/docs/production-rollout.md)
- [Archive structure and Rust retirement](cpp/docs/rust-archive.md)

Historical benchmarks do not imply C++ is universally faster. Rust led the
recorded local bulk-throughput cases; C++ had selective lightweight-request and
high-load-memory advantages. Reports retain their source identities, finite
test scopes and unsuppressed analyzer-model findings.

## 中文说明

后续仅维护 C++20 版本。Rust 完整源码与依赖已移到 `archive/rust/`，Node 历史
版本保留在 `archive/node/`；默认构建与 CI 不执行任何归档实现。浏览器端
JavaScript 仍负责图表及 WebRTC，这不代表后端使用 JavaScript 或 Node。

## License

Project code is MIT licensed. Dependencies retain their original licenses and
notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
