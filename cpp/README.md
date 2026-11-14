# JitterCrest C++20 backend

This directory holds the only maintained JitterCrest backend. The UI build embeds
and serves the page from [`../public/`](../public/); the agent build
(`-DSPEEDTEST_WEB_UI=OFF`) serves measurement endpoints only. The running
service never invokes Python, Node or Rust. The frozen Rust implementation is kept
in [`archive/rust/`](../archive/rust/ARCHIVE.md) for reference.

Measurement methods and formulas are documented in the [root README](../README.md);
build, deployment and node management in [docs/deployment.md](docs/deployment.md).

## Components

| Source | Responsibility |
|---|---|
| `core.cpp` | 128-byte little-endian probe frames, ID hashing, percentiles (`floor((n-1)p)`), density and byte accounting, strict JSON parsing |
| `http.cpp` | Boost.Asio/Beast coroutine HTTP server: page, downloads, uploads, upload receipts, client info, WebRTC signalling and management API |
| `edge.cpp` | Built-in TLS + HTTP/2 listener with large receive windows that relays to the local HTTP listener ([details](docs/tls-edge.md)) |
| `progress.cpp` | Upload receipts: a 10 s window that opens 1 s after the first received byte, reported as NDJSON every 100 ms |
| `limits.cpp` | Per-client rate, concurrency and byte budgets (IPv4 address or IPv6 /64) with RAII leases |
| `rtc.cpp`, `sdp.cpp`, `control.cpp`, `rtc_validation.cpp` | libdatachannel peers, SDP/candidate filtering, control-message fragmentation and result validation |
| `admin.cpp` | Node registry, `/admin` API, single-use enrollment keys and heartbeats |
| `client_info.cpp` | Bounded libcurl worker pool for client network lookups, isolated from the event loop |
| `config.cpp` | Environment configuration with explicit bounds |

## Design

- Each HTTP connection owns its parser, bounded buffers, timers and socket on a
  serialized executor; there is no thread per connection. Cancellation
  generations stop stale timers from closing a later request.
- WebRTC uses pinned **libdatachannel 0.24.6** (ICE/libjuice, DTLS/OpenSSL,
  SCTP/usrsctp). A reliable `control` channel carries signalling and results;
  the `test` channel is unordered with `maxRetransmits=0`.
- Native RTC is off unless `SPEEDTEST_RUST_RTC_EXPERIMENTAL=1` is set after the public
  UDP mapping is verified; otherwise RTC requests fail closed with 503. The key
  keeps its historical name and does not start any Rust process.
- Beast gives incremental reads and writes, body limits and explicit connection
  lifetimes; libdatachannel supplies a browser-compatible WebRTC stack without
  reimplementing ICE, DTLS or SCTP; ordered nlohmann JSON keeps control field
  order. No custom allocator, lock-free queue, SIMD or fast-math is used.

## Build and test

Requirements: Linux x86_64, CMake >= 3.22, Ninja, a C++20 compiler (validated with
Clang 18), Boost.System 1.83, OpenSSL >= 3, nlohmann-json >= 3.11,
libcurl >= 7.85 with asynchronous DNS, libnghttp2 >= 1.59 (pkg-config) and
Python 3 for asset generation and tests.

```sh
bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S cpp -B cpp/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build cpp/build --parallel
ctest --test-dir cpp/build --output-on-failure

# Agent without the embedded page
cmake -S cpp -B cpp/build-agent -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DSPEEDTEST_WEB_UI=OFF -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build cpp/build-agent --parallel
```

CTest runs ten suites: protocol statistics and SDP, configuration bounds, limits,
upload receipts, RTC upload completion, executor and coroutine lifetimes, client
info, node enrollment and the TLS HTTP/2 relay. GitHub Actions also runs the
Python tooling tests (quickstart, enrollment installer, release packaging,
staging capacity and benchmark reports).

Debug, Release, ASan+UBSan and TSan presets live in `CMakePresets.json`.
Sanitizer builds need separately instrumented native dependencies; never mix TSan
with ASan, and never compare sanitizer builds with Release binaries.

```sh
NATIVE_SANITIZER=address-undefined CC=clang CXX=clang++ \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps-asan
NATIVE_SANITIZER=thread CC=clang CXX=clang++ \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps-tsan
cd cpp
cmake --preset asan-ubsan -DCMAKE_PREFIX_PATH=/absolute/local/deps-asan/install
cmake --build --preset asan-ubsan && ctest --preset asan-ubsan
cmake --preset tsan -DCMAKE_PREFIX_PATH=/absolute/local/deps-tsan/install
cmake --build --preset tsan && ctest --preset tsan
```

Release LTO is opt-in with `-DSPEEDTEST_LTO=ON`. Clang core fuzzing:

```sh
cmake -S cpp -B cpp/build/fuzz -DCMAKE_CXX_COMPILER=clang++ \
  -DSPEEDTEST_SANITIZER=address-undefined -DSPEEDTEST_FUZZ=ON
cmake --build cpp/build/fuzz --parallel
cpp/build/fuzz/speedtest-core-fuzz -runs=10000 -max_len=65536
```

## Native dependencies

The bootstrap script pins libdatachannel commit
`6b1e2e620f1e37f0eafeee702eaea0043cb305fd`, verifies its submodules and applies the
reviewed patches in `cmake/patches/`, recording their SHA256 values. CMake
generates a hash-pinned Boost 1.83 atomic-count header overlay with an
acquire-release decrement; installed Boost headers are never edited and
unreviewed revisions fail configuration. CMake does not fetch sources.

Licenses: libdatachannel and libjuice MPL-2.0, usrsctp BSD-3-Clause, plog MIT,
Boost BSL-1.0, nlohmann-json MIT, OpenSSL Apache-2.0, libcurl curl, nghttp2 MIT.
Keep these notices when distributing binaries; see [THIRD_PARTY.md](../THIRD_PARTY.md)
and [release bundles](docs/release-bundle.md).

## Comparison with the archived Rust implementation

Differential and paired contract runners (for example `tests/differential.py`,
`tests/http_contract.py`) compare this backend with an explicitly built archived
Rust binary. They are optional, never part of CTest or CI, and the C++ service never
links or invokes the oracle:

```sh
cargo build --release --locked --manifest-path archive/rust/oracle/Cargo.toml
python3 cpp/tests/differential.py \
  --rust archive/rust/oracle/target/release/speedtest-rust-oracle \
  --cpp cpp/build/speedtest-core-fixture --output core-differential.json
```

## Validation history

The [documentation index](docs/README.md) separates current guides from dated
validation, performance and rollout records. Those records describe the source
commit and environment named in each report; later changes are not backfilled.
