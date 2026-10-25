# Independent C++20 backend — implementation in progress

**This is not yet a complete Speedtest server and must not replace the Rust
production service.** The full target includes HTTP streaming, upload progress,
WebRTC probes/control, cancellation/accounting, safety checks, shared-client
integration tests and measured Rust/C++ benchmarks. A green core test is not a
claim that those end-to-end requirements are complete.

## Architecture and boundaries

- C++20 pure core: owned 128-byte little-endian frames, UTF-16-compatible ID hash,
  nearest-index percentiles, density/accounting, pacing/executor summaries,
  bounded control fragmentation/reassembly and SDP/candidate filtering.
- Asynchronous HTTP will use Boost.Asio/Beast, not a thread per connection.
  Planned per-connection ownership covers socket, buffers, timers and in-flight
  response; no borrowed span/string_view may outlive its owner in callbacks.
- WebRTC will use pinned **libdatachannel 0.24.6**, ICE/libjuice, DTLS/OpenSSL and
  SCTP/usrsctp. This is a native C/C++ transport, not a Rust wrapper or FFI.
  Reliable control and unordered zero-retransmission probes remain distinct.
- Existing frontend and wire protocol are the compatibility contract. Rust
  sources, production Caddy/systemd, existing archives and rollback material are
  not changed. Staging must use a different service, directory and port.

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

On Linux, install CMake >=3.22, a C++20 compiler, Ninja, Boost.System >=1.74,
OpenSSL >=3 and nlohmann-json >=3.11. The development environment used Ubuntu
24.04, Clang 18.1.3, Boost 1.83, OpenSSL 3.0.13 and nlohmann-json 3.11.3.
Distribution package revisions should be recorded with benchmark results.

```sh
cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/build --parallel
ctest --test-dir cpp/build --output-on-failure

cd cpp
cmake --preset debug && cmake --build --preset debug && ctest --preset debug
cmake --preset asan-ubsan && cmake --build --preset asan-ubsan && ctest --preset asan-ubsan
cmake --preset tsan && cmake --build --preset tsan && ctest --preset tsan
```

Sanitizers use separate builds; TSan is never mixed with ASan. Release LTO is
explicitly opt-in (`SPEEDTEST_LTO`); final benchmarks must give Rust comparable
optimization flags and never compare sanitizer builds with Release binaries.

Optional WebRTC dependency bootstrap (needed when the server module is added):

```sh
bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
# Later configure with -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
```

Pinned upstream commit is `6b1e2e620f1e37f0eafeee702eaea0043cb305fd`.
Submodules are the revisions pinned by that commit; the script verifies them.
libdatachannel is MPL-2.0, usrsctp BSD-3-Clause, libjuice MPL-2.0, plog MIT,
Boost BSL-1.0, nlohmann-json MIT and OpenSSL Apache-2.0. Retain appropriate
notices when distributing binaries/dependencies. No dependency source is
silently patched or fetched by CMake at configure time.

### Original Rust differential oracle

The optional test-only oracle compiles the original Rust pure modules **by path**;
it does not copy their implementation or alter them. The C++ library/service
never invokes or links the oracle. Its JSON/base64/entropy versions are pinned
to the reference Cargo.lock rather than upgraded opportunistically.

```sh
cargo build --release --locked --manifest-path cpp/tests/rust-oracle/Cargo.toml
python3 cpp/tests/differential.py \
  --rust cpp/tests/rust-oracle/target/release/speedtest-rust-oracle \
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
  it must remain unchanged and be exercised by the same browser against both
  independent backends. Server pacing/RTT/accounting still requires independent
  C++ implementation and comparison.

## Remaining acceptance work (not yet achieved)

1. HTTP/config/security/resource limits, full routes, client-info cache/providers,
   request drain/timeouts/partial I/O, upload progress and graceful shutdown.
2. Native WebRTC signaling/session limits, cancellation/tombstones, probes,
   scheduler/grace accounting, result fragmentation and browser integration.
3. Debug/Release plus all required sanitizer/static-analysis gates for the full
   service; malicious/slow/half-close/disconnect/reconnect/concurrency cases.
4. Shared Rust/C++ offline and HTTP/browser contracts, including UI/agent and
   probe-only deployment behavior with explicit environment configuration.
5. Multi-round alternating Release benchmarks: throughput, CPU, idle/peak RSS,
   P50/P95/P99, concurrency, sustained stability and errors; separate computation,
   loopback HTTP and network results. No performance conclusion exists yet.
6. Hardened independent systemd/Caddy staging templates, reproducible dependency
   and deployment bundle, measured safety/performance reports and final verdict.

Current core validation is a development milestone only. Full functionality,
performance superiority and readiness to replace Rust remain unproven.
