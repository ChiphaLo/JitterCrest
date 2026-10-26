# Guarded public staging — 2026-10-26, acceptance still incomplete

This records actual isolated VPS runs, not loopback traffic or a serialized
candidate-address fixture. **It is not a production cutover or full acceptance.**
Original successes and failures are retained privately; later successes do not
turn an earlier failed transaction into a pass.

## Isolation and authoritative checks

Three existing x86_64 hosts were tested: a Debian 13 UI host, an Ubuntu 24.04
probe-only agent and a Debian 13 data agent. The final public-source Release/
ThinLTO UI and no-UI artifacts were built locally, SHA-256 checked before upload,
verified again on the target and extracted into newly owned directories.
Servers were not compiled on the VPS.

Each transaction used its own temporary systemd units and release directory:

- C++ and the immutable Rust reference ran independently on distinct loopback
  HTTP ports. Neither implementation called the other.
- A copied Caddy executable ran as a separate test process on a separate public
  HTTPS port, with its production admin endpoint inaccessible/unchanged.
- A short-lived test certificate had the actual target IP in its SAN. Python
  validated the supplied test CA and IP hostname; the same handshake failed
  under ordinary system trust. Chromium pinned only that certificate's SPKI.
  No system trust installation or blanket certificate-error bypass was used.
- The public test proxy required a random temporary acceptance header; an
  unauthenticated health request returned 403. The proxy stripped the header
  before forwarding. Test keys/configuration were delivered with systemd
  credentials, never published, and removed after stopping the test units.
- CPU affinity, memory/task ceilings, a thirty-minute runtime ceiling,
  read-only system/home protections and separate temporary storage applied to
  the test services. Kernel `/proc` checks confirmed non-root UIDs,
  `NoNewPrivs=1`, zero effective capabilities and active seccomp filtering.
- All 33 bundled C++ shared libraries were observed loaded from the extracted
  release, and the running native executable hash matched its manifest.

Production Rust and Caddy PIDs/start times, executable hashes, effective unit
files, Caddy configuration hashes and the Rust release link matched before and
after every transaction. Test listeners/units were stopped and removed; normal
public HTTPS health subsequently returned 200 on all three production hosts.
Source archives/releases were retained as audit artifacts, not made active.
These tests do not establish every conceivable hardening property.

## What actually passed

The same client exercised both implementations. `tests/remote_rtc_contract.py`
checks actual 10 KiB/100 KiB/1 MiB uploads/downloads, live NDJSON received before
uploading, final received-byte accounting, IP-SAN TLS trust, and real browser
ICE/DTLS/SCTP. The probe-only profile instead requires payload endpoints to
return 404 while allowing latency and native RTC.

Every completed profile uses three separate `20 packets/s × 2 seconds`
connections plus one `100 packets/s × 20 seconds` connection. Candidate-pair
statistics must identify the **actual public target IP over UDP**, with connected
DTLS/SCTP and bidirectional bytes. No SDP address rewrite, relay fixture,
request-route mock or fake clock is used. The UI profile also exercises the
unchanged page's real automatic RTT flow, without enabling its loopback test
hooks.

| Isolated target role | Observed C++ result | Rust reference observations |
|---|---|---|
| Central UI | A complete four-connection profile passed; real page RTT rendered | A later complete matching profile passed; an earlier reconnect failed |
| Probe-only agent | All four connections passed; payloads denied | All four matching connections passed; payloads denied |
| No-UI data agent | A complete four-connection profile passed; HTTP/progress and no-UI checks passed | First profile stopped on a reconnect failure |

On **each** target, an isolated proxy was actually switched C++ → Rust → C++;
health responses were checked against the selected independent backend. This
validates a staging backout/re-entry path, not a performed production rollback.
The production proxy was never reloaded or pointed at a test listener.

## Failures that remain open

A bounded repeat on the data-agent path subsequently produced a C++ ICE-connect
timeout as well. The Rust repeat reached the long measurement but returned
`UPLOAD_COMPLETE_MISSING`. At least one failed connection had valid public
answer candidates while the browser remained in ICE checking. Consequently:

- The failures are **not** classified as exclusively Rust defects.
- Correct candidate addresses alone do not prove that UDP requests/replies
  reached either endpoint.
- Network/route loss versus server transport behavior has **not** been causally
  separated. The next diagnostic needs synchronized, own-test-socket-scoped
  STUN/DTLS header counters, not another round of blind retries or relaxed
  connection deadlines.
- No merge or production switch is justified by selecting only the passing
  profiles. These public-network failures remain an acceptance investigation.

Thirty-five measurement records were captured across the bounded transactions:
19 C++ records finalized their protocol, and 15 of 16 Rust records did so; the
remaining Rust record explicitly lacked upload completion. There were also
connection failures before measurement, which are not counted as packets sent
or silently discarded. Populations and times differ: these counts are **not**
a statistically fair language-performance or reliability comparison.

Both packet/ACK deficits and protocol-incomplete results remain in the evidence.
Server `complete`/`final` describe protocol finalization, not loss-free delivery.
The original .99 density criterion concerns scheduler `actualSent/planned`,
not `received/actualSent`. The verifier initially confused those concepts;
comparison with the actual Rust/frontend code corrected the verifier, not either
backend. Null upload accounting in `UPLOAD_COMPLETE_MISSING` is now explicitly
unknown, not fabricated as zero or promoted to a success. Fifteen verifier unit
tests and replay of the captured server/accounting/candidate metadata passed;
replay is not a rerun or repair of a failed network connection. The CLI retains
partial observations, prints progress/completion, and refuses to overwrite a
previous evidence file before starting any network request.

The first test proxy also exposed a Caddyfile syntax error. An IP endpoint behind
one-to-one NAT additionally needed Caddy's `default_sni` set to that test IP;
its SAN/chain checks were kept enabled. These were isolated test-configuration
repairs, not changes to production Caddy or TLS verification.

## Reproduction and remaining boundary

Use only an explicitly authorized, isolated endpoint and private evidence paths:

```sh
python3 cpp/tests/remote_contract_test.py
python3 cpp/tests/remote_rtc_contract.py \
  --url https://PUBLIC_TEST_IP:TEST_PORT --ca /private/test-ca.crt \
  --pin TEST_CERTIFICATE_SPKI --token-file /private/acceptance-token \
  --role probe --backend cpp --rounds 3 \
  --output /private/evidence/public-probe.json
```

The token, CA/key generation, target selection and service administration belong
in private operations tooling, not the published source or reports. A public-IP
test does not bypass the workstation's ordinary network/routing policy.

These are functionality/portability checks, **not** the required fair real-network
throughput/CPU/RSS/tail-latency benchmark. The earlier local cross-site central-
UI/agent matrix remains separate; this staging client navigates directly to each
target, so it does not by itself prove the full public central-UI-to-multiple-agent
cross-origin flow. That flow, the unresolved transport observations, final
deployment-profile acceptance and the final requirements audit remain open.

Public builds intentionally use generic node identifiers/configuration. A later
production UI build must select the preserved deployment frontend using
`SHARED_ASSETS` and validate the actual node list/role configuration; do not blindly
substitute a generic frontend for private deployment aliases. Neither the
preserved frontend nor Rust source was edited in this milestone.
