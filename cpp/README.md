# JitterCrest C++20 后端

The sole maintained backend is C++20 HTTP/WebRTC. The frozen Rust reference is
in [`archive/rust/`](../archive/rust/ARCHIVE.md), outside active build and CI. Runtime never invokes Rust, Node or Python; Rust is used only as
the development compatibility oracle. UI and no-UI agents compile separately.

The [final objective audit](docs/acceptance-audit.md) maps all15 original sections
to delivered evidence and explicit limits. Current runtime source is `019ff35`.
Finite HTTP/statistics/resource/browser compatibility and release safety gates
passed; this is not a universal equivalence or zero-bug certification.

## Current results

- Debug/Release/ASan+UBSan/TSan: CTest8, config521, paired visibility and native
  message boundaries. Affected release/sanitizer matrices include HTTP126,
  resources13, close100, wire22, shutdown3 and recovery checks.
- Independent preserved-UI/generic-UI/agent bundles, actual library relocation,
  fresh Restart, public four-backend-combination/eight-case profile and probe
  payload denials: [current release validation](docs/recovery-release-validation.md).
- Two unsuppressed full-HTTP Clang18/Boost analyzer-model findings are explicitly
  audited, not called a clean full-service Tidy pass:
  [coroutine analysis](docs/coroutine-analysis.md).
- Latest fair Release/ThinLTO results: [local tables](docs/local-performance-recovery.md),
  [Internet tables](docs/network-performance-recovery.md),
  [interpretation](docs/performance-findings-recovery.md). Rust leads local bulk
  throughput/CPU efficiency; C++ has selective lightweight-request/high-load-RSS
  advantages. No overall performance/maintenance justification to replace Rust.

RTC remains default-off behind `SPEEDTEST_RUST_RTC_EXPERIMENTAL=1` for explicit
operational enablement; without it, valid RTC requests fail closed503. Do not
infer permission to replace an existing service from source availability.
Independent deployment templates and storage admission are in
[staging](docs/staging.md). The initial authorized three-host rollout is recorded in
[production history](docs/production-rollout.md). The subsequent user-requested
[Rust archive and server-side retirement](docs/rust-archive.md) is the current
policy: only C++ is maintained and no server-side Rust rollback remains.

Earlier reports are historical snapshots; later passing evidence does not erase
older failures or relabel their binaries. Start with the final audit/current
reports above instead of treating an early milestone's pending checklist as
current release status. Linux x86_64 was validated; aarch64 is not claimed.

## Architecture and boundaries

- C++20 pure core: owned 128-byte little-endian frames, UTF-16-compatible ID hash,
  nearest-index percentiles, density/accounting, pacing/executor summaries,
  bounded control fragmentation/reassembly and SDP/candidate filtering.
- Asynchronous HTTP uses Boost.Asio/Beast coroutines, not a thread per connection.
  Each connection owns its parser, bounded buffers, timers and socket on a
  serialized executor; response payload views are retained through awaited writes.
  Cancellation generations prevent stale timer callbacks closing a later request.
- RAII leases reserve/release active capacity and refund unproduced fixed-download
  bytes. Upload receipts own per-lane completion/failure. A bounded four-worker
  provider pool isolates blocking HTTPS lookup I/O from the event loop; completion
  work guards keep the executor alive until owned lookups finish.
- WebRTC uses pinned **libdatachannel 0.24.6**, ICE/libjuice, DTLS/OpenSSL and
  SCTP/usrsctp. This is a native C/C++ transport, not a Rust wrapper or FFI.
  Reliable control and unordered zero-retransmission probes remain distinct.
- Existing frontend and wire protocol are the compatibility contract. Rust
  sources/units, existing archives and rollback material are retained. C++ uses
  a different service, directory and port; authorized production routing changes
  are scoped and independently verified in the rollout report.

### Why these dependencies

Beast exposes incremental reads/writes, body limits and explicit connection
lifetimes atop a mature event loop; it avoids adapting an opaque buffered HTTP
framework to high-throughput streams. libdatachannel provides an established
browser-compatible native WebRTC stack rather than implementing cryptography,
ICE and SCTP anew. nlohmann ordered JSON preserves control field order; OpenSSL
provides entropy and the transport dependency. No custom allocator, lock-free
queue, architecture-specific SIMD or fast-math is introduced.

Core Guidelines applied: I.4/I.11 (typed interfaces/ownership), R.1/R.20/R.21 and
E.6 (RAII and minimal shared ownership), F.53 (no escaping reference captures),
CP.2/CP.20/CP.23 (no races, scoped locks, joining threads), Per.1/Per.2 (measure
before optimizing). Expected network callbacks must catch exceptions at their
boundary and schedule state changes through the owning executor.

## Current core build and tests

On Linux, install CMake >=3.22, a C++20 compiler, Ninja, Boost.System 1.83
(the reviewed atomic-count header revision; other revisions need review),
OpenSSL >=3, nlohmann-json >=3.11, Python 3 for asset generation/tests, and
libcurl >=7.85 with asynchronous DNS support. Runtime never invokes Python,
Rust or Node. The development environment used Ubuntu 24.04, Clang 18.1.3,
Boost 1.83, OpenSSL 3.0.13, libcurl 8.5.0 and nlohmann-json 3.11.3.
Distribution package revisions should be recorded with benchmark results.

```sh
bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build cpp/build --parallel
ctest --test-dir cpp/build --output-on-failure

# Isolated local HTTP milestone, no production listener or RTC replacement:
PORT=32132 SPEEDTEST_HOST=127.0.0.1 cpp/build/speedtest-cpp

# Measurement-only executable; it does not embed/serve the frontend:
cmake -S cpp -B cpp/build-agent -DCMAKE_BUILD_TYPE=Release -DSPEEDTEST_WEB_UI=OFF \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build cpp/build-agent --parallel

cd cpp
cmake --preset debug -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build --preset debug && ctest --preset debug
cmake --preset asan-ubsan -DCMAKE_PREFIX_PATH=/absolute/local/deps-asan/install
cmake --build --preset asan-ubsan && ctest --preset asan-ubsan
cmake --preset tsan -DCMAKE_PREFIX_PATH=/absolute/local/deps-tsan/install
cmake --build --preset tsan && ctest --preset tsan
```

Sanitizers use separate builds; TSan is never mixed with ASan. Release LTO is
explicitly opt-in (`SPEEDTEST_LTO`); final benchmarks must give Rust comparable
optimization flags and never compare sanitizer builds with Release binaries.

Native transport is a required build dependency, even with RTC disabled at runtime:

```sh
bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
# Configure all profiles with -DCMAKE_PREFIX_PATH pointing to the matching install.
# Instrument the C AND C++ native dependencies, using separate prefixes:
NATIVE_SANITIZER=address-undefined CC=clang CXX=clang++ \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps-asan
NATIVE_SANITIZER=thread CC=clang CXX=clang++ \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps-tsan
```

Pinned upstream commit is `6b1e2e620f1e37f0eafeee702eaea0043cb305fd`.
Submodules are the revisions pinned by that commit; the script verifies them.
libdatachannel is MPL-2.0, usrsctp BSD-3-Clause, libjuice MPL-2.0, plog MIT,
Boost BSL-1.0, nlohmann-json MIT and OpenSSL Apache-2.0. Retain appropriate
notices when distributing binaries/dependencies. The bootstrap explicitly applies
the reviewed patches in `cmake/patches/` and records their SHA256 values; these
are not silently modified system dependencies. CMake also generates a hash-pinned
Boost 1.83 atomic-count header overlay with an acquire-release decrement in every
profile. This strengthens synchronization without disabling TSan. Installed
Boost headers are never edited; unreviewed header revisions fail configuration.
See the RTC validation report for scope and reasons. CMake does not fetch sources.

### Original Rust differential oracle

The archived optional test-only oracle compiles the archived Rust pure modules **by path**;
it does not copy their implementation or alter them. The C++ library/service
never invokes or links the oracle. Its JSON/base64/entropy versions are pinned
to the reference Cargo.lock rather than upgraded opportunistically.

```sh
cargo build --release --locked --manifest-path archive/rust/oracle/Cargo.toml
python3 cpp/tests/differential.py \
  --rust archive/rust/oracle/target/release/speedtest-rust-oracle \
  --cpp cpp/build/speedtest-core-fixture --output core-differential.json
```

The shared deterministic client covers random frame bits and lengths, Unicode
IDs, percentile edges, overflow/density/accounting, public/reserved IPs, SDP
limits and Unicode whitespace, fragmented/reordered/duplicate controls, strict
base64, JSON nesting bounds and legacy/result-fragment transport. Discrete
values compare exactly; declared floating tolerance is `1e-12` absolute and
relative. Fragment nonce values are normalized, never the payload/statistics.

Clang core fuzzing:

```sh
cmake -S cpp -B cpp/build/fuzz -DCMAKE_CXX_COMPILER=clang++ \
  -DSPEEDTEST_SANITIZER=address-undefined -DSPEEDTEST_FUZZ=ON
cmake --build cpp/build/fuzz --parallel
cpp/build/fuzz/speedtest-core-fuzz -runs=10000 -max_len=65536
```

## Compatibility facts established from current Rust

- HTTP/1 is served behind Caddy; the real-time protocol is **WebRTC DataChannel**,
  not WebSocket. No fictitious WebSocket migration is required.
- Downloads reuse an immutable random 512 KiB chunk; fixed 10 KiB, 100 KiB and
  1 MiB responses have explicit lengths. Streaming and quota failure must not
  silently look like normal successful EOF.
- Upload warmup begins at the first nonempty received body chunk. Display
  progress and final authoritative upload accounting have separate lifetimes.
- The current backend honors unordered `maxRetransmits=0`; the old Node
  reliable-zero behavior is **not** the reference.
- Core Rust percentile index is `floor((n-1)*p)` after finite filtering, not the
  interpolated percentile or the differently defined nearest-rank formula.
- Current Rust keeps historical HTTP behavior including a service-wide 30 s
  head/body expiry-check cadence, keep-alive timeout plus a 1 s close buffer,
  ignored request-body draining and max-requests-per-socket handling. These
  transport cases need explicit C++ tests, not assumed framework equivalence.
- Many Node-era statements remain in historical architecture/migration prose
  (Node template build, RTC disabled, old node topology). The latest actual Rust
  source and matching production artifact are the baseline, not those records.
- Most chart/MAD/jitter/stable-throughput analysis is in the shared frontend;
  it remains unchanged and is exercised by the same browser against both
  independent backends. Server pacing/RTT/accounting is independently implemented
  in C++; broader acceptance and benchmark work is still required.


## Validation history

Core, HTTP, native-resource, transport and static-analysis milestone reports
remain in `docs/` with their original scopes. The [final audit](docs/acceptance-audit.md)
identifies current-source evidence and retained limitations.
