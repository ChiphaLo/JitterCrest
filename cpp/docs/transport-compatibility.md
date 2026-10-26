# Current configuration and native transport follow-up

**Subsequent follow-up:** `receive-fairness.md` records the bounded native
OpenSSL receive-turn change and its new validation. The failed aggregate below
is retained for the earlier dependency snapshot, not silently converted to a pass.

These changes do not authorize a production switch. Rust, the frontend, probe
wire format, statistical definitions and the 0.99 completeness gate are unchanged.
Earlier reports describe their recorded source snapshots, not automatic safety
approval of later application or dependency changes.

## Configured probe visibility

Disabling the probe role previously removed the literal default `probe-only`
node, even when `SPEEDTEST_PROBE_ONLY_NODE_ID` selected a different node. The
configuration now resolves that ID before filtering and removes only the
configured role. Only the literal enable value `0` disables it; whitespace,
`false`, `00`, an empty value and an unset variable retain the reference behavior.
Unrelated nodes and non-object entries retain their original ordering/content.

`tests/config_fixture.cpp` covers custom/default IDs and nonzero flag values.
`tests/node_visibility_contract.py` additionally starts real UI executables and
reads their embedded node configuration. Its eight flag cases can compare with
an unmodified reference through an explicit environment-name/ID adapter. Public
defaults remain generic; no private deployment configuration is distributed.

## Association lifetime versus probe reliability

libdatachannel's optimized SCTP defaults abort an association after five
retransmission attempts. The reference Rust DATA T3 path does not impose that
retry-count abort; application/session and ICE lifetimes remain the cleanup
boundaries. C++ now sets the native association retry budget to the maximum
16-bit value **before native preload**, retaining its existing application
peer/session deadlines. This is not an unlimited application lifetime.

This does **not** change unordered probe messages' `maxRetransmits=0`, change the
probe schedule, extend frontend timeouts, or override the native RTO defaults.
An experimental full reference-RTO override was rejected after measured
periodic-loss result tails worsened. No such RTO override is in the source.

A controlled loopback encrypted-UDP relay, applying the same 60 ms one-way delay
and declared drop policies to both backends, demonstrated the old early-close
difference. With the retry-only change, the C++ association survived a 6–20 s
upload-direction blackout. However, its raw-wire result arrived around 30 s
after starting a 20 s measurement: too late for the unchanged frontend's result
deadline. Rust returned an incomplete result around 24 s. This severe-outage
case is **not** frontend acceptance or a timing-equivalence pass. These relay
observations are not Internet measurements, packet-identical traffic, or a
language-performance benchmark. Earlier public control/result-fragment failures
remain unproven; this change alone is not evidence that they have been cured.

## Shared mbuf cluster references

Fully instrumented TSan resource testing found a genuine usrsctp race between
`mb_dupcl`'s ordinary reference-count read/fast-path write and `mb_free_ext`'s
atomic decrement. It is unrelated to the two audited coroutine-analyzer model
findings. `usrsctp-mbuf-refcount.patch` removes the non-atomic increment fast path
and uses the dependency's atomic primitive for shared-counter observations in
the writable/logging macros and debug output. Counter initialization still
occurs before publication; the owning mbuf must remain alive while cloning.

`tests/native_mbuf_refcount.c` exercises **actual native** `m_copym`/`m_freem`,
with four threads making 400,000 total copies/releases of one retained immutable
cluster, and checks payload sharing, observation macros and final ownership.
The original instrumented archive failed with the same TSan race (exit 66).
The repaired archive passed Release, ASan/UBSan and five consecutive TSan runs
without a finding. These focused checks do not substitute for full RTC tests.

```sh
python3 cpp/tests/native_mbuf_refcount.py \
  --source-dir /local/deps/src/libdatachannel/deps/usrsctp/usrsctplib \
  --archive /local/deps/build/libdatachannel/deps/usrsctp/usrsctplib/libusrsctp.a \
  --sanitizer thread
```

Use a fixture/archive pair with the same sanitizer. The fixture's explicit
defines match the pinned Linux simple-allocator/process-lock build; it is not a
portable ABI test for arbitrary dependency builds.

## Receive-queue metadata ownership

After the mbuf repair, all thirteen TSan resource scenarios passed, but the
66th consecutive remote-close/recovery round exposed a **second genuine race**.
`sctp_sorecvmsg` observed a queued control's `do_not_ref_stcb` after releasing
the read-queue lock for `uiomove`; association teardown modified that flag while
holding the queue lock. The socket/transport receive mutex is a different lock.

`usrsctp-receive-queue-lock.patch` moves the existing lock reacquisition ahead
of that metadata observation, retaining the unlocked copy and the existing
release/error cleanup. It does not merely turn the flag atomic while leaving
the related queue metadata unsynchronized, add a new lock order, or suppress
TSan. The original failure and partial run are retained. Repaired-source
results below remain a **failed aggregate** because of a separate flood stall;
the earlier successful safety tables must not be relabeled as these results.

## Provider completion and executor teardown

A subsequent TSan CTest exposed an independent real lifecycle error in the
client-info fixture: it destroyed a short-lived `io_context` while a provider
thread was still returning from posting the already-executed completion. An
empty reactor or ready future does not prove that all external executor calls
have returned. The fix retains one context through all fixture lookups until
the provider pools have joined; it does not hide the race with sleeps or replace
the asynchronous provider with a synchronous call.

Production teardown now also explicitly shuts down/joins the provider pool in
`HttpServer` destruction, while its App and caller-owned context are still alive.
This ensures completion producers finish before executor destruction and avoids
letting the last pool-task owner destroy its own pool. Normal reactor workers
are joined before server destruction. Provider limits, timeouts, normalization
and wire responses are unchanged. The original failing CTest is retained.
The repaired client-info CTest passed 100 consecutive TSan executions, and all
three runtime profiles passed their independent shutdown scenarios below.

## Bounded test failure reporting

The resource client's backpressure loop used to wait forever if a server died
while the browser still considered its data channel open. A 60 s **failing**
watchdog now bounds the whole flood, including its backpressure waits, and
reports sends, ACK count, buffered bytes and channel state. The 40,000-send
ceiling, rate/yields, `sent > 24000` assertion and actual remote-close/recovery
requirements remain unchanged. No timeout is converted into success.

`tests/test_rtc_resource_watchdog.py` uses a harness-only fake stalled channel to
prove failure and a healthy control that closes on send 24,001. Native tests
still use real browser ICE/DTLS/SCTP. Both controls passed; no fake peer is used
by the service or protocol acceptance tests.

## Repaired-source results — incomplete acceptance

The following independent checks were completed on the repaired dependency and
application source. Native libraries, binaries and test sources were hash-guarded.
Unchanged Rust is the paired HTTP/configuration reference; the resource/wire
discrete results also compare to its retained immutable paired snapshots.

| Check | Release | ASan + UBSan | TSan |
|---|---:|---:|---:|
| CTests | 8 passed | 8 passed | 8 passed |
| Configuration differential cases | 521 passed | 521 passed | 521 passed |
| Actual paired UI visibility cases | 16 passed | 16 passed | 16 passed |
| Paired HTTP contracts | 126 passed | 126 passed | 126 passed |
| Native resource scenarios | 13 passed | 13 passed | 12 passed, **flood failed** |
| Consecutive untraced remote-close/recovery | 100 passed | 100 passed | 100 passed |
| Native wire/loss/actual-frontend contracts | 22 passed | 22 passed | 22 passed |
| Idle/active/offer-race process shutdown | 3 passed | 3 passed | 3 passed |

Debug also rebuilt and passed eight CTests. An independent build from the
sanitized public C++ checkout passed Release/ThinLTO compilation, eight CTests,
the watchdog controls and eight generic custom-node visibility cases. Comparing
that generic UI to a private Rust executable initially produced four HTML-body
mismatches: its separate sanitized frontend differs by 144 bytes. That report
and binary are retained, not labeled a server-protocol pass. Selecting the
**same preserved frontend explicitly via `SHARED_ASSETS`**, without editing
either frontend or the comparison, then passed all 126 paired HTTP contracts
and sixteen paired visibility cases. This is a distinct deployment-profile
check, not permission to publish private assets or deploy either binary.

The TSan flood failure was not retried until green or removed from the matrix.
Independent close/wire/shutdown checks were run separately after retaining that
failure; their success does not promote the failed resource aggregate. A
predeclared Release/TSan diagnostic repeated the same flood counts and pacing:
Release passed, while TSan again reached the failing 60 s watchdog with the
browser channel still open and approximately 256 KiB buffered. The server
remained alive and cleaned up normally, without a sanitizer finding in that run.

A single further diagnostic captured only the synthetic loopback socket and
authenticated its DTLS records locally. The capture was bounded and untruncated,
with zero authentication/decryption failures. SCTP SACKs showed an initially
exhausted advertised receive window that subsequently recovered near 2 MiB,
while hundreds of TSN gaps and very slow cumulative/FORWARD-TSN advancement
persisted. Thus the later stall is **not a permanently zero receive window**,
process crash, or evidence about the Internet proxy/VPS route. Kernel delivery,
native-stack overload recovery and browser feedback contributions still need
attribution. No speculative buffer tuning, packet-count reduction, timeout
extension or reliable retransmission of zero-retry probes was introduced.
Raw packets, keys and diagnostic tooling remain private, not in this repository.

Changed configuration/RTC/fixture Tidy checks produced no new diagnostic. The
full HTTP analysis still reports the same two audited Clang18/Boost coroutine
model findings in `coroutine-analysis.md`; it is not a clean full-service Tidy
pass. Sanitizer runtime success does not erase static findings or demonstrate
that every system dependency is source-instrumented. Public deployment-profile
acceptance, the high-density TSan stall and the final requirement audit remain
open. **Do not merge/promote a release or replace Rust on this evidence.**

## Late upload ACKs after the final receipt snapshot: shared baseline limitation

Current C++ and reference Rust both remove the measurement's active accounting
state when producing `downResult`, but still echo a valid kind-1 upload probe
as a kind-2 ACK when that state is absent. Consequently, a delayed upload can
be ACKed **after** the final upload-receipt snapshot. If the browser observes
those ACKs before finishing result processing, its ACK count can exceed the
snapshot receipt count. The unchanged frontend correctly rejects that mismatch
as `UPLOAD_RECEIPT_ACCOUNTING`; a server's `complete:true` alone is not enough.

`tests/rtc_late_receipt_contract.py` deterministically exercises this existing
boundary with actual browser/native ICE, DTLS and SCTP. It starts a two-probe
measurement, sends a structurally valid completion before delivering the delayed
upload frames, waits for the original final result, and only then delivers those
two uploads. Both backends return a snapshot of zero uploaded receipts and then
ACK both late frames. The original result grace remains at least 299 ms; no
deadline, reliability or frontend validation rule is relaxed. This deliberately
late test input represents a receipt-order boundary, not a valid completed
measurement or a recreation of every public failure.

The test passed three paired rounds in each of Release, ASan+UBSan and TSan
(three C++ and three reference sessions per profile), without a sanitizer or
shutdown finding. Its report explicitly marks `baseline_observation_only:true`
and `measurement_acceptance:false`. This is evidence of equal behavior and an
existing baseline limitation, **not** a fix or a newly passing stability test.
Public failures also had independently observed upstream record gaps, described
in `public-staging-validation.md`; those cannot all be attributed to this
snapshot mechanism. Any future protocol/accounting correction needs a separate
decision for the reference implementation rather than a C++-only semantic change.

```sh
python3 cpp/tests/rtc_late_receipt_contract.py \
  --cpp /local/cpp-release/speedtest-cpp \
  --rust /local/reference/speedtest-rust \
  --rounds 3 --output /private/evidence/late-receipt-baseline.json
```

## Native message-size negotiation and the reference read boundary

Real browser negotiation found a separate compatibility defect: reference Rust
omits `a=max-message-size`, so the browser applies the RFC8841 default of
65,536 bytes. C++ had incorrectly advertised the **2 MiB aggregate control
reassembly budget** as a single native message capability; Chromium exposed a
262,144-byte send limit. The original official bundle failed the new negotiated
cap regression while the unchanged Rust reference passed. That failure and
bundle remain retained, not renamed as a corrected release.

C++ now configures the native message capability as 65,536 bytes, independently
of the aggregate application fragment budget. Both native answers now omit the
default attribute and both browsers expose the same 65,536-byte limit. The
application's normal 16 KiB per-control-message policy, 128-byte probe layout,
fragment format/reassembly limits, reliability and deadlines remain unchanged.
libdatachannel's existing internal buffer minimum follows its configured native
message size; this is not a change to probe scheduling or an RTO override.

A positive-edge test then exposed an existing reference limitation: its native
DataChannel read buffer is `u16::MAX` bytes, **65,535**, not 65,536. With BOTH
fixtures explicitly configured to permit 64 KiB application control messages,
a valid 65,535-byte JSON message is delivered and a subsequent real measurement
succeeds; a 65,536-byte message is accepted by the browser but closes the reference
peer before application dispatch. A 65,537-byte send is rejected by the browser
as `TypeError`, before transmission. The normal 16 KiB application policy must
not be mistaken for this transport-boundary test configuration.

The first positive test mistakenly expected 65,536 to survive the normal 16 KiB
application policy; that fixture failure is retained. Repeating with the correct
explicit legal application ceiling demonstrated the distinct reference read
boundary above. A cap-only C++ build still failed that close-boundary comparison.
C++ therefore also enforces the reference 65,535-byte native dispatch boundary
before application byte/message accounting, using its existing peer cleanup and
tombstone path. Rust and its dependency sources were not edited. This preserves
the current baseline limitation rather than silently fixing Rust in a migration.

Verification scopes are intentionally separated:

- The negotiated-cap correction rebuilt Debug/Release/ASan+UBSan/TSan and passed
  eight CTests plus paired real negotiation in each. Release and both sanitizer
  profiles also passed resource13, untraced close/recovery100, native wire22 and
  shutdown3 suites. Those reports precede the subsequent read-boundary guard.
- After the additional read-boundary guard, all four profiles rebuilt and passed
  eight CTests plus the full paired boundary fixture: valid65,535, close65,536,
  reject65,537, unchanged real20pps×2s measurement and closed-attempt409 behavior.
  This is targeted validation of the final guard, not a relabeling of the earlier
  full runtime matrix as having been rerun on it.
- `tests/frontend_statistics_contract.py` separately checked 510 golden inputs
  against the actual statistic helpers served by both preserved-UI backends.
  Percentiles, scaled MAD, adjacent-difference mean, population standard deviation,
  timing thresholds and both trimmed stable-rate functions matched at `1e-12`
  relative/absolute tolerance; the served helper source was identical. This is
  offline arithmetic evidence, not a fake RTC or Internet measurement.

```sh
python3 cpp/tests/native_message_cap_contract.py \
  --cpp /local/cpp/speedtest-cpp --rust /local/reference/speedtest-rust \
  --output /private/evidence/native-message-cap.json
python3 cpp/tests/frontend_statistics_contract.py \
  --cpp /local/cpp-ui/speedtest-cpp --rust /local/reference/rust-ui \
  --output /private/evidence/frontend-statistics.json
```

The previous fairness bundles, public8/8 profile and performance measurements are
still identified by their original hashes. They do not include this correction.
New independent bundles/relocation and their deployment-profile checks are still
required. The severe-outage/result-delay investigation is separate and is not
claimed fixed by the message-cap correction.

The [post-correction queue diagnostic](send-queue-diagnostic.md) now reproduces
that delay on the corrected raw executable. A fixed 15-case comparison and a
separate actual-socket metadata check reject a private 64 KiB send-buffer-only
experiment: matching Rust's failure reasons did not make the result timely.
No small-buffer setting was promoted into application source.
