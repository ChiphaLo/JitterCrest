# Independent C++20 backend — acceptance in progress

**The independent HTTP/WebRTC implementation is present, but final compatibility
and release acceptance are incomplete. It must not replace the Rust production
service yet.** The full target includes HTTP streaming, upload progress,
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

The requirement-by-requirement status and remaining gates are in
`docs/acceptance-audit.md`; it explicitly does not declare the full goal complete.

Release/ThinLTO measurements of the receive-fairness snapshot are available in
`docs/local-performance-current.md`, with interpretation in
`docs/current-performance-findings.md`. The original frozen measurements remain
in `docs/local-performance.md` and `docs/performance-analysis.md`.
The distinct same-snapshot Internet revalidation is in
`docs/network-performance-fairness.md`. All reports pin their measured artifacts;
they must not be relabeled as a later corrected binary.
Reproducible commands are in `benchmarks/README.md`. Both runs cover five native-core
rounds, three HTTP rounds and five minutes of mixed traffic per backend. Rust
leads most measured payload throughput cases; C++ has some lightweight-request
and high-concurrency RSS advantages. These are local results, not Internet
measurements or evidence authorizing a production replacement.

The central-UI/independent-agent HTTPS IP and finite resource-exhaustion matrix
is documented in `docs/agent-resource-validation.md`. All four Rust/C++ UI/agent
combinations passed in Release, ASan/UBSan and TSan profiles. Subsequent guarded
VPS tests demonstrated actual public-NAT probe-only connectivity and independent
runtime portability, but also retained unresolved real-network transport
failures; see `docs/public-staging-validation.md`. Full acceptance remains open.

The two remaining Clang18/Boost coroutine diagnostics now have an exact reduced
fixture, AST/CFG evidence, known-bad controls and a four-profile runtime lifetime
test; see `docs/coroutine-analysis.md`. The raw full-service Tidy command still
reports those two findings and is **not** mislabeled a clean pass. No checker or
warning was suppressed to obtain this classification.

The additional native admission/expiry/FIFO/resource suite and the diagnosed
OpenSSL DTLS close-notify lifetime regression are documented in
`docs/rtc-resource-validation.md`. The dependency patch sends the close alert
before last-owner teardown; it does not weaken the browser closure deadline or
modify the Rust/reference protocol. Resource, repeated-close and wire suites
remain distinct acceptance checks.

The current configured-node visibility and SCTP retry-budget changes, newly
reproduced native mbuf/receive-queue races, and bounded test-failure watchdog are
described in `docs/transport-compatibility.md`. Repaired-source safety validation
and public deployment-profile acceptance remain required; older green snapshots
must not be interpreted as approval of a later dependency build.

The subsequent bounded OpenSSL receive-turn follow-up is documented in
`docs/receive-fairness.md`, including the unchanged flood requirements, retained
failures and limits of the local transport diagnosis. Public release/production
acceptance remains separate from that local safety revalidation.

The subsequent native message-cap/read-boundary correction and its explicit
before/after verification scopes are in `docs/transport-compatibility.md`.
The older fairness bundles and their public eight-case result must not be
relabeled as this corrected binary. The actual-served shared frontend arithmetic
fixture is `tests/frontend_statistics_contract.py` (offline, not a mock RTC pass).

Independent binary/runtime bundling and deterministic archives are described in
`docs/release-bundle.md`. Packaging never installs a service or authorizes a
production switch; relocation and target-host tests remain required.

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

1. Include the remaining adverse-path/control observations in final acceptance
   without relaxing protocol deadlines or the .99 scheduler-density gate.
   The current native-Windows public central/multiple-agent profile passed all
   eight node cases and four mixed Rust/C++ combinations, including probe-only
   and data roles. This does not erase failures on the earlier Hong Kong-browser
   path or the controlled severe-outage timing difference. See
   [public staging](docs/public-staging-validation.md) for the exact positive
   profile and retained failures, including dual-ended return-path evidence.
   A newer unchanged-bundle header diagnostic established upstream record gaps
   between the browser-host egress and probe-host ingress for both backends,
   while only two of eight pages completed. It also closed the failed-page
   payload-denial evidence gap. See the same report for the limits of that
   attribution, and `docs/transport-compatibility.md` for the separately
   reproduced shared late-ACK/receipt-snapshot limitation. Neither observation
   is a full acceptance pass or permission to change the Rust baseline.
2. Existing local HTTP/resource, native RTC wire/loss/resource/close/shutdown,
   Debug/Release and separate ASan+UBSan/TSan matrices are recorded in the linked
   validation reports; they do not prove every Internet transport condition.
   The two raw full-service Clang analyzer findings remain nonzero, with scoped
   model-false-positive evidence in [coroutine analysis](docs/coroutine-analysis.md),
   not a falsely clean Tidy claim. External providers used deterministic fixtures,
   not a claim of validation against every third-party response.
3. Shared Rust/C++ offline and HTTP/browser contracts include independently
   configured UI, no-UI agents, and probe-only roles. Complete final production
   profile acceptance and the original requirement-by-requirement audit rather
   than treating these finite suites as full goal completion.
4. Local multi-round Release/ThinLTO computation and HTTP benchmarks, CPU/RSS,
   P50/P95/P99, accepted connections and five-minute stability are measured in
   the reports above. A separate three-round, 36-case bounded Internet HTTP
   comparison with remote CPU/RSS is now in
   [network performance](docs/network-performance.md). Longer-running Internet
   stability remains unmeasured; these short cases do not establish it.
5. Independent systemd/Caddy templates are provided in `deploy/`, with their
   isolation/rollback design in `docs/staging.md`. Reproducible UI/agent bundles,
   relocation tests, temporary hardened VPS units and staged C++/Rust/C++ proxy
   backout have been exercised. The preserved deployment frontend also passed
   its separate build/HTTP/relocation checks. Final acceptance, conditional branch
   merge and backed-up production deployment remain pending. No production route,
   release link or service has been switched.

Current validation is a development milestone only. Full functionality,
performance superiority and readiness to replace Rust remain unproven.

The latest [post-boundary-fix queue diagnostic](docs/send-queue-diagnostic.md)
retains a fixed15-case comparison and a separate actual-native metadata check.
A private64KiB send-buffer-only experiment was rejected: its failure reasons
matched Rust, but severe-outage result delivery still missed the original
frontend deadline. Application source and production remain unchanged by it.

New corrected public-source preserved-UI, generic-UI and no-UI-agent candidates
have now passed their own [local build/relocation checks](docs/corrected-candidate-builds.md),
including real paired message boundaries and fresh-offer-correlated normal UI
Restart. Their public multi-host role acceptance and the adverse-path gate are
still pending; no main merge or production cutover has occurred.

The latest [reference-scale RTC admission/recovery correction](docs/reference-admission-recovery.md)
combines reference RTO values with bounded native admission. Fixed comparisons,
an old-executable negative control and new four-profile/affected sanitizer
suites verify the recorded timely-incomplete-result regression without changing
probe reliability, deadlines, counts or density gates. Earlier boundary-package
hashes predate this change; new packages and public-role acceptance are pending.
The [staging capacity guard](docs/staging.md#complete-temporary-copy-admission)
also prevents spending the protected reserve on omitted temporary copies.

New source019ff packages and their **new** native-Windows public4-combination /
8-case profile have now passed, with fresh Restart IDs,20 exact probe denials,
actual remote artifact/library identities, full copy budgets and independent
post-cleanup production checks. See [current release validation](docs/recovery-release-validation.md).
Current performance revalidation and the final objective audit remain pending;
Rust production has not been switched and no main merge has occurred.
