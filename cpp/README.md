# Independent C++20 backend — implementation in progress

**This is not yet a complete Speedtest server and must not replace the Rust
production service.** The full target includes HTTP streaming, upload progress,
WebRTC probes/control, cancellation/accounting, safety checks, shared-client
integration tests and measured Rust/C++ benchmarks. A green core test is not a
claim that those end-to-end requirements are complete.

The native HTTP milestone is implemented: `speedtest-cpp` now listens on a
separate port, serves UI or measurement-only builds, and independently handles
download/upload streaming, upload progress, limits, client info and connection
lifecycle. Native WebRTC is now implemented behind the default-off experimental
flag: native signaling/ICE/DTLS/SCTP, cancellation/tombstones, separate reliable
control and zero-retry probes, scheduler/accounting and fragmented results.
**Complete safety and acceptance gates remain pending.** With the flag off,
valid RTC requests still fail closed with 503. Do not enable this binary
as a production backend. See `docs/rtc-validation.md` for current RTC evidence,
`docs/http-validation.md` for the earlier HTTP snapshot,
and outstanding safety gates; `docs/core-validation.md` is the earlier core
snapshot, not an all-current-source safety claim.

Local Release/ThinLTO performance measurements are now available in
`docs/local-performance.md`, with interpretation in `docs/performance-analysis.md`
and reproducible commands in `benchmarks/README.md`. They cover five native-core
rounds, three HTTP rounds and five minutes of mixed traffic per backend. Rust
leads most measured payload throughput cases; C++ has some lightweight-request
and high-concurrency RSS advantages. These are local results, not Internet
measurements or evidence authorizing a production replacement.

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
  it remains unchanged and is exercised by the same browser against both
  independent backends. Server pacing/RTT/accounting is independently implemented
  in C++; broader acceptance and benchmark work is still required.

## Remaining acceptance work (not yet achieved)

1. Complete the remaining HTTP security/edge-case and resource-exhaustion matrix
   beyond the tested milestone; complete probe-only profile and live TLS/proxy
   verification. External provider behavior was tested with injected deterministic
   fixtures, not declared validated against every third-party response.
2. Extend and complete acceptance of the implemented native WebRTC session
   limits, cancellation/tombstones, zero-retry probes, scheduler/grace accounting,
   result fragmentation and actual UI/agent/probe-only browser integration.
3. Debug/Release plus all required sanitizer/static-analysis gates for the full
   service; malicious/slow/half-close/disconnect/reconnect/concurrency cases.
4. Shared Rust/C++ offline and HTTP/browser contracts, including UI/agent and
   probe-only deployment behavior with explicit environment configuration.
5. Local multi-round Release/ThinLTO computation and HTTP benchmarks, CPU/RSS,
   P50/P95/P99, accepted connections and five-minute stability are measured in
   the reports above. Real-network end-to-end performance and longer-running
   stability remain unmeasured; local observations do not establish either.
6. Independent systemd/Caddy templates are provided in `deploy/`, with their
   isolation/rollback design in `docs/staging.md`. Live staging, Caddy validation,
   reproducible complete deployment bundle, measured reports and final verdict
   remain pending. No template has been installed into production.

Current core validation is a development milestone only. Full functionality,
performance superiority and readiness to replace Rust remain unproven.
