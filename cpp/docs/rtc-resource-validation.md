# Native RTC resource limits and remote-close regression

This is a scoped acceptance milestone, not permission to replace production.
Rust, the shared frontend, existing Caddy/systemd configuration and backups
remain unchanged. Public-NAT connectivity, real-network comparisons and final
release/cutover acceptance remain separate gates.

## Shared native resource contracts

`tests/rtc_resource_contract.py` uses the same actual Chromium ICE/DTLS/SCTP
client and unmodified executable interface for Rust and C++. It covers:

1. Three globally admitted peers across separate identities; fourth rejected;
   active cancellation frees capacity; the cancelled attempt stays tombstoned.
2. Three actual ten-second session expirations, remotely closed channels,
   tombstones and usable recovered capacity.
3. Unknown-cancel rate limit, duplicate exemption, foreign-identity isolation,
   active-cancel exemption and real ten-second rate-window recovery.
4. Offer-rate accounting for failed/conflicting attempts, rate-vs-tombstone
   precedence, independent identities and real rate-window recovery.
5. A full 128-entry FIFO: duplicate cancellation does not reorder the oldest
   entry, eviction admits validation again, reinsertion evicts the next oldest.
6. Actual thirty-second tombstone lifetime: duplicate at twenty seconds does
   not refresh it; retained at twenty-nine, expired after thirty seconds.
7. Three same-key reuse rounds after FIFO eviction, with old browser peers
   retained and replacement-generation probes still acknowledged. This does
   not claim deterministic pausing of a particular native callback instruction.
8. Control-message count violation.
9. Control-message size violation.
10. Aggregate inbound-byte violation after an acknowledged probe.
11. Native test-message count violation under bounded zero-retry frame traffic.
12. Down-start count violation.
13. Measurement duration clamping with the unchanged planned-packet/accounting
    semantics; missing upload completion cannot produce a successful result.

Every violation case requires browser-visible remote closure, replay rejection
and a usable replacement peer. The browser keeps the same five-second closure
deadline; tests do not close the peer locally to manufacture success. Real
thirty-second expiry is not replaced with a fake clock. Process logs and bounded
clean exits are checked, including sanitizer/executor/delayed-cleanup failures.

The initial Release and ASan/UBSan matrices passed all thirteen cases, but a
later TSan run exposed the closure failure below. Earlier passes are not used
to dismiss that reproducible failure. Final matrices must use the corrected
dependency, not just the unchanged application executable hash.

## Confirmed close-notify lifetime defect

On an aggregate inbound-byte violation, Chromium sometimes reported:

```text
ICE connected, DTLS connected, SCTP connected
control closed, test closing, both bufferedAmount values zero
```

The server had already released/tombstoned the session. This happened on
loopback, so it was not evidence of an Internet route or remote firewall fault.
Explicit channel closes and retaining the native peer until a native closed
state did not resolve it; those application-level candidates were discarded.

A private, synthetic-peer-only OpenSSL keylog/interposer and packet capture
allowed authenticated DTLS decryption. Direction was inferred from the actual
ClientHello, not from which endpoint runs the server application: the answering
WebRTC endpoint can itself be the DTLS client. The failed baseline had zero
authentication/decryption errors. Its SCTP SHUTDOWN / SHUTDOWN_ACK /
SHUTDOWN_COMPLETE exchange finished, but there was **no server DTLS
close_notify**. A final stream-reset exchange was still visible. These are
separate transport-layer observations, not a claim that the browser finished
closing every stream.

In pinned libdatachannel's OpenSSL implementation, `DtlsTransport::stop()`
stopped the input queue and called `enqueueRecv()`. That task captures only a
weak pointer. The destructor also calls `stop()`, then immediately frees SSL;
the lower ICE transport is subsequently stopped during base destruction.
Consequently, deferred `doRecv()` cannot be relied on to send the alert when
the last strong owner is being released.

`cmake/patches/libdatachannel-openssl-close-notify.patch` sends the first
`SSL_shutdown()` while SSL and its lower transport still exist. It uses the
existing SSL mutex, requires a finished handshake and avoids re-sending an
already sent shutdown notification. This first call sends the notification;
it does not wait for a peer reply. Existing queue shutdown and asynchronous
receive cleanup remain in place. No probe layout, reliability, pacing,
statistics, resource limit or timeout was relaxed. The application's
`src/rtc.cpp` retains its pre-investigation implementation.

In one hundred consecutive fully native-instrumented TSan capture rounds,
every browser peer closed within the original deadline and every capture
contained the server close_notify, with zero DTLS decryption failures. Keys,
packet bodies, raw traces and diagnostic interposer are **not published**.
`tests/rtc_close_repeat.py` separately repeats the same regression without
preload, TLS hooks or packet capture; it also checks tombstones and replacement
ACKs. Tracer-only success is not the final acceptance gate.

The bootstrap applies the reviewed patch explicitly, checks reverse applicability
for idempotence and records patch hashes. Its context uniquely identifies the
OpenSSL branch. The fix is validated for that backend only; it is not a claim
that the GnuTLS or MbedTLS implementations were tested or repaired. Comparing
fresh upstream archives plus all published patches against the actual tracked
native sources verifies that no undeclared source edit is needed.

## Reproduction

Build the matching instrumented native dependencies and C++ executable first,
following `../README.md`. The Rust executable is a development reference, never
part of the C++ runtime. Playwright Python with its Chromium installation is
required for these tests; both browser clients explicitly disable host GPU use.

```sh
python3 cpp/tests/rtc_resource_contract.py \
  --cpp /local/build/speedtest-cpp --rust /local/reference/rust-ui \
  --output /private/evidence/rtc-resources.json
python3 cpp/tests/rtc_close_repeat.py --binary /local/build/speedtest-cpp \
  --rounds 100 --output /private/evidence/rtc-close-repeat.json
```

Repeat with separate Release, ASan/UBSan and TSan builds. Continue to run the
22-case native wire/loss/actual-frontend suite, active-shutdown suite and CTests;
the resource suite does not replace them. Original failures remain preserved.

## Verified final-source results — 2026-10-26

The final exported native sources were reproduced from upstream plus the
published patches, rebuilt, then tested again. The earlier investigation binary
snapshots were not silently relabeled as this final-source run. Application/test
sources, executable and native shared-library hashes were held fixed during the
final tests. Discrete contracts compare with the immutable paired Rust reference
results; the original Rust/frontend/vendor baseline remains unchanged.

| Check | Release | ASan + UBSan | TSan |
|---|---:|---:|---:|
| Native resource/expiry contracts | 13 passed | 13 passed | 13 passed |
| Consecutive untraced remote-close + recovery rounds | 100 passed | 100 passed | 100 passed |
| Native wire/loss/actual-frontend contracts | 22 passed | 22 passed | 22 passed |
| Idle/active/offer-race bounded process shutdown | 3 passed | 3 passed | 3 passed |
| CTests | 8 passed | 8 passed | 8 passed |

Debug also rebuilt and passed eight CTests. The frozen Rust reference was
separately exercised in the paired thirteen-case and twenty-two-case runs for
all three profiles before the final C++-only repetitions; comparison is not to
an invented oracle. No sanitizer, executor or delayed-cleanup finding was
accepted as success. System OpenSSL/libcurl are still not claimed completely
source-instrumented merely because the native transport is instrumented.

A fresh public-source native bootstrap and idempotent rerun passed. Its independent
Release/ThinLTO UI build passed eight CTests and the paired thirteen-case resource
suite; the independent no-UI build passed eight CTests and 126 paired HTTP
contracts. The optional-LTO build exposed and fixed a separate build-system
issue: every consumer of the bitcode static core, including support tests, must
receive IPO link flags. No workaround linker flag was needed in the corrected
all-target public build.

Both release bundles passed extraction/checksum, `/proc` native-library
relocation, upload/download and both progress-mode tests. The UI completed the
actual paired frontend and Restart/RTT paths; the agent denied UI/icon routes.
Both passed native remote-close/replacement-ACK tests after relocation. See
`release-bundle.md`. These are local artifacts/tests, not public-NAT validation,
target-host service/rollback acceptance, a Git merge or a production deployment.
