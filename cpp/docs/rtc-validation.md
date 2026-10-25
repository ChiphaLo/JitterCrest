# Native RTC milestone — in progress, not production acceptance

The C++ executable now independently negotiates and serves browser WebRTC over
libdatachannel 0.24.6, pinned commit
`6b1e2e620f1e37f0eafeee702eaea0043cb305fd`. It does not invoke a Rust process,
FFI, Rust library, Node backend or an existing Rust service. Rust is test-only
reference behavior. Production remains Rust and no deployment/cutover is made.

## Concurrency and ownership

One serialized Asio strand owns peer admission, bounded tombstones, timers,
measurement state and cleanup. Two bounded preparation workers perform native
negotiation; active plus still-preparing counts prevent a cancelled-offer flood
from growing an unbounded native-work queue. Callbacks keep weak peer references,
copy messages into owned storage, enforce cumulative byte/message admission
before posting, and transfer changes to the owning strand. Cleanup callbacks
check the exact admitted peer generation, not just a potentially reused key.

Reliable control and unordered `maxRetransmits=0` probes are independent native
channels. A false native `send()` return means buffered, not rejected; accepted
unreliable packets are never re-sent. Sends use a bounded high-water mark, async
backpressure and deadline. Owned scheduler/sampler/finalizer timers are cancelled
on close. Native cleanup drains late callbacks while the executor still exists.
Complete shutdown/resource-exhaustion auditing is still required.

The unchanged shared frontend is embedded. Core protocol/statistics tests are
not substitutes for genuine browser ICE/DTLS/SCTP and actual loss tests.

## Shared real-browser contracts

`tests/rtc_contract.py` launches the same headless CPU-only Chromium client
against Rust and C++. It exercises the existing frontend measurement hook plus
an identical browser wire client, with 128-byte little-endian probes. Browser
JavaScript here is a wire/test client, not a Node server implementation.

Coverage includes 20 packets/s for 2 seconds (40 planned packets), 100 packets/s
for 20 seconds, reordered/duplicate probes, malformed completion types/counts,
exact reason lists, reverse/duplicate completion fragments, fragmented native
results, result grace, missing/duplicate completions, cancellation/tombstones,
malformed controls/frames, invalid SDP cleanup, concurrent peers and per-IP
admission. The harness checks exact discrete accounting/sequence identities and
the strict 0.99 delivery gate; no percentile/density requirement is relaxed.
Tests print progress and save a failure report rather than silently ending.

`tests/rtc_udp_proxy.py` is a bounded loopback encrypted-UDP relay, not a protocol
replacement. It drops one DTLS application datagram in each direction and
verifies that later probes on the same path arrive but the missing sequences do
not reappear after a further three-second observation. This is an actual
loss/zero-retry check, not merely inspecting a data-channel configuration getter.
The NAT fixture checks advertised public IPv4 candidates without applying that
answer, so it never sends ICE traffic to the public fixture address.

The expanded 22-case wire suite passed against Rust/C++ in Debug, Release,
native-instrumented ASan/UBSan and TSan profiles, including real Restart/RTT
rendering, actual loss, the 20-second test and candidate mapping. Each profile
also passed seven CTests, 126 HTTP contracts and eight HTTP lifecycle contracts;
the Release agent passed 126 HTTP contracts. An independent public-source build
with its own sanitized frontend passed seven CTests and the paired 22-case RTC
suite. These are scoped snapshots, **not** an all-current-source safety verdict:
the additional active-shutdown tests subsequently found the callback race below.
The initial matrix's final source-freeze check correctly rejected a changed
bootstrap script while that new fix was being developed. It is not labeled a
successful unchanged-source aggregate. Subsequent dependency/fix tests must be
distinguished from the preceding 22-case compatibility snapshot.

```sh
# Install Playwright + its Chromium/OS dependencies in a local test environment.
python3 cpp/tests/rtc_contract.py \
  --rust /absolute/path/to/reference-rust-binary \
  --cpp cpp/build/release/speedtest-cpp --output /private/reports/rtc-release.json
python3 cpp/tests/rtc_lifecycle.py \
  --rust /absolute/path/to/reference-rust-binary \
  --cpp cpp/build/tsan/speedtest-cpp --output /private/reports/rtc-stop.json

# Header-only native callback regression, using the patched transport headers.
# Repeat separately with -fsanitize=address,undefined (never combined with TSan).
clang++ -std=c++20 -O1 -g -fsanitize=thread \
  -I/absolute/local/deps-tsan/install/include \
  cpp/tests/native_callback_test.cpp -pthread -o /tmp/native-callback-test
TSAN_OPTIONS=halt_on_error=1 /tmp/native-callback-test
```

## Explicit dependency safety corrections

Fully instrumenting the transport's C and C++ sources found findings that a
wrapper-only sanitizer build missed. No checks are disabled and these findings
must not be presented as an originally clean upstream dependency.

1. `libjuice-picohash-callback-types.patch`: replace incompatible function-pointer
   casts with exact-signature callback adapters. Hash/HMAC/reset behavior is
   verified with 24 ASan/UBSan vectors against Python hashlib/hmac.
2. `usrsctp-linux-shared-state.patch`: make socket/association counter snapshots
   match existing atomic writers; publish every cumulative-TSN update atomically
   to the unlocked receive-info snapshot; protect the first receive-queue lookup
   with the queue's existing lock and release before waiting; synchronize receive
   credit transfer/reset and the last advertised window snapshot. Linux endpoint
   flags use C11 atomic storage so all 500-plus existing flag reads/compound
   updates match the lock-free compare/exchange writers. Timer active/pending
   flags are atomic across timer-queue and endpoint locks. Global cached-chunk
   and diagnostic resource counters use atomic snapshots. Association state is
   atomic across TCB-locked transitions and read-queue-locked receive/shutdown
   snapshots; repeated shutdown tests exposed this additional race after an
   initially successful run. Native usrsctp C
   sources are consequently built as C11; its internal initialization function
   is renamed to avoid colliding with C11 `atomic_init`. Flag size/alignment
   assertions preserve the internal layout. No reliability,
   packet-loss, retransmission, pacing or wire/statistics algorithm is changed.
   Additional shared-state helpers target Linux userspace; other OS/kernel paths
   retain their original behavior and are not claimed validated by Linux tests.
3. `cmake/prepare_boost.py`: an explicit build-local, SHA256-pinned Boost 1.83
   overlay replaces release decrement plus acquire fence with acquire-release
   decrement, in **every** build profile. LLVM 18's TSan runtime
   `AtomicFence` is explicitly unimplemented (see the upstream source below).
   `tests/executor_refcount_test.cpp` reproduces the delete/refcount report with
   the original header and passes with the strengthened equivalent synchronization.
   Installed headers are untouched; no sanitizer suppression/annotation or thread
   count reduction is used. A changed upstream header requires review.
4. `libdatachannel-stored-callback-lock.patch`: explicitly synchronize the stored
   callback's complete copy/move/assignment, including its optional missed-event
   tuple. Its former implicit derived assignment copied that tuple outside the
   base-class lock while the native worker updated it. Actual active-measurement
   shutdown exposed this genuine race even after the 22 wire cases passed.
   `tests/native_callback_test.cpp` independently reproduces the original TSan
   finding and passes with the patch, including concurrent invoke/reset/copy and
   missed-event replay. A fully native-instrumented TSan build with this patch
   passed open-peer, acknowledged active-measurement and offer/shutdown-race
   process-stop contracts against the same Rust client. The offer race starts an
   unawaited fetch; it is not claimed to deterministically pause the backend at a
   particular native preparation instruction. After the association-state fix,
   five consecutive fully instrumented TSan rounds passed all three shutdown
   scenarios for both Rust and C++ (15 C++ native shutdown scenarios), without
   disabling checks, reducing native concurrency or weakening timing/protocol
   invariants. This still does not prove every resource-exhaustion path safe.

LLVM 18 reference:
<https://github.com/llvm/llvm-project/blob/llvmorg-18.1.3/compiler-rt/lib/tsan/rtl/tsan_interface_atomic.cpp>

The bootstrap was exercised from a fresh pinned upstream/submodule checkout.
It applies and records explicit patch hashes; the resulting usrsctp diff matches
the published patch byte-for-byte. Re-running the script must remain idempotent.

The active-shutdown tests deliberately signal the backend before closing its
browser peer. The active measurement must already have returned a probe ACK,
so a test cannot pass merely because no scheduler/native work ever started.
`tests/rtc_lifecycle.py` saves complete per-scenario backend logs, rejects delayed
cleanup/executor failures and requires clean bounded process exits.

## Open acceptance gates

- Final broad acceptance beyond the passing snapshots below; earlier failures
  are retained, not overwritten as successful results.
- Two remaining unsuppressed network Clang-Tidy diagnostics in Boost 1.83
  coroutine internals (`awaitable.hpp:171` and `:508`), and
  complete resource/timeouts/cancellation/agent/probe-only coverage. Local Caddy
  HTTP/TLS and actual UI Restart/RTT tests pass (see `staging.md`). The separate
  cross-site central-UI-to-agent HTTPS IP matrix now passes in three C++ profiles
  (see `agent-resource-validation.md`); successful mapped probe-only connectivity
  and public deployment remain unverified.
- Reproducible staging bundle and complete dependency/toolchain manifest.
- Local alternating Release/ThinLTO core and HTTP benchmarks, CPU/RSS/tails,
  accepted connections and five-minute mixed traffic are now measured in
  `local-performance.md` / `performance-analysis.md`. Real-network and longer
  stability measurements remain open; no production replacement is justified.

Do not enable the experimental RTC service as a production replacement merely
because a subset of compatibility tests passes.

The earlier HTTP snapshot's two Beast parser diagnostics were eliminated by
selecting its contiguous `const_buffer` overload for the owned normalized header.
This avoids the generic mutable-buffer-sequence stack copy; parsing is still
synchronous and the string remains alive. No safety warning is suppressed. The
two coroutine diagnostics still require a documented modeling/lifetime audit.

## Latest patched-transport verification

After both the stored-callback and association-state corrections:

| Check | Verified result | Scope |
|---|---|---|
| Shared Rust/C++ native RTC | 22 wire cases each passed in Release, ASan/UBSan and TSan | Includes actual frontend hook + Restart rendering, loss/zero-retry proof, strict accounting, 20-second stability and candidate mapping |
| Native RTC active shutdown | All three scenarios passed in Release and ASan/UBSan | Browser remains open when process stop begins |
| Repeated native TSan shutdown | Five consecutive rounds passed, three scenarios each | ACK-confirmed active traffic, idle peer and offer/stop race; 15 C++ plus 15 reference exits |
| Callback regression | Passed separate TSan and ASan/UBSan runs | Concurrent invocation/reset/copy plus owned string-event replay |
| Independent public-source build | Seven CTests and paired 22-case RTC suite passed | Uses its own sanitized frontend and freshly bootstrapped patched native dependencies |
| Public-source local Caddy TLS | Shared Rust/C++ HTTP, HTTPS, live NDJSON and actual UI RTT passed | Ephemeral pinned certificate; no production/CA installation |
| Fresh pinned dependency bootstrap | Passed; idempotent rerun passed | Both the complete usrsctp and stored-callback diffs match their source patches |

These results establish scoped functional and safety progress. They do **not**
replace the remaining resource/agent/probe-only/static-analysis matrix or
real-network benchmarks, and do not authorize a production cutover. System OpenSSL/libcurl
are not claimed fully source-instrumented by the transport sanitizer builds.
