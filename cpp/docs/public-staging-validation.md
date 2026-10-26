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
- The original run did not separate network/route loss from server transport
  behavior. The later scoped dual-ended investigation below narrows the ICE
  failure, but does not explain every control-message timeout.
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

## Follow-up: dual-ended headers and an independent client vantage

A bounded investigation observed only the staging processes' owned UDP sockets.
The AF_PACKET filter initially rejected everything, then admitted only those
exact socket endpoints. It read at most 50 bytes (Ethernet, IPv4, UDP and eight
protocol-header bytes), storing lengths/directions and STUN/DTLS kinds, never
payloads, ICE credentials, transaction IDs or encrypted bodies. Filters were
disarmed before releasing sockets, preventing capture of a later port owner.
Six private collector tests included live positive/negative kernel filters,
process socket ownership, dual-stack discovery and an egress-tap control.

An initial `ETH_P_IP` collector observed ingress only. Its absence of egress is
**not evidence of a missing server response**. The corrected collector used an
`ETH_P_ALL` packet tap with the same narrow IPv4 BPF filter. The completed
corrected run had zero kernel drops, overwritten records or read errors:

| Header observation | C++ | Rust |
|---|---:|---:|
| WSL outbound STUN requests | 769 | 768 |
| Server inbound STUN requests | 710 | 668 |
| Server outbound STUN-success responses | 144 | 668 |
| Server-originated outbound STUN requests | 407 | 3,764 |
| Scoped inbound packets at WSL | 0 | 0 |

All twelve connections failed ICE. Each attempt kept one local UDP source port,
while the server saw 15 distinct source ports per C++ attempt and 68 per Rust
attempt. Counts are not packet-by-packet identities: header-only capture omits
transaction IDs, and network duplication/retransmission is possible. The trace
does establish that requests reached the server and responses left its NIC,
but no scoped return packet reached WSL. This implicates the intervening return
path/NAT/proxy mapping; it does **not** identify the failing hop or prove every
server request received a response. Backend behavior under rotating mappings
may differ and was not patched merely to force a passing diagnostic.

An independent headless Chromium client then ran in a temporary, bounded,
capability-dropped, read-only-root Docker container on the authorized UI host.
GPU use was disabled. The client used real public IP HTTPS origins and scoped
test-certificate pins, without SDP rewrites, routing changes, response mocks or
frontend test hooks. The locally built deployment-frontend UI artifact (not
generic public node aliases) served the unchanged page; the two remote agents
contained no UI assets. Exact deployment identifiers/configuration remained
private. Both native backends were still independent staging processes.

Four combinations were exercised: C++ UI/C++ agents, Rust UI/Rust agents,
Rust UI/C++ agents and C++ UI/Rust agents. In **all four**, the public data agent
passed automatic RTT rendering, the real Restart action, a real 100 packets/s
× 20 seconds Stability action with the existing .99 validation gate, all three
payload sizes, upload and live progress accounting. Browser candidate evidence
identified the actual remote public IP over UDP. Proxy access records confirmed
real cross-site browser OPTIONS/POST traffic and exact central-origin CORS
responses. This is actual central-UI-to-agent Internet traffic, not the earlier
local TLS fixture. It further separates the WSL/data-agent ICE failure from an
inability of that agent to operate with either implementation.

The probe-only agent remains unresolved: the first C++-agent combination had a
`measurementReady timeout`; the Rust/Rust combination lacked upload completion
and failed frontend stability validation. The two mixed combinations completed
stability, but their subsequent payload-denial checks encountered browser CORS
rejection: the test Authorization header triggered preflight, and the backend
correctly returned 404 to that preflight. A rejected fetch is not an HTTP 404
response object. Private proxy logs independently confirm those 404 preflight
denials. This test-expectation issue must not erase the earlier genuine control
failures or promote the entire transaction to a pass. These failures are not
classified as exclusively C++ or exclusively the WSL route.

All four combination results remain recorded as failed; the passing data-agent
subcases are reported separately. Temporary browser containers, unit files,
listeners, authorization configs and TLS private keys were removed. Before/after
production process/unit/configuration/release snapshots still matched on all
three hosts. The deployment-frontend UI package separately passed eight CTests,
126 paired HTTP contracts, relocated library/checksum checks, actual frontend
RTT and a native close/recovery regression. It is a candidate, not a promoted
production release. No branch merge or production switch has occurred.

## Reproduction and remaining boundary

### Controlled probe-agent metadata follow-up

A subsequent bounded diagnostic used a throwaway, locally compiled C++ source
clone with control-message metadata logging, plus a browser observer. The
official C++ source, Rust reference and frontend remained unchanged. The clone
passed eight CTests; a real local DTLS/SCTP smoke test verified the observer,
native send exceptions, ordinary construction and `instanceof`. Neither observer changed probe
scheduling, deadlines, completion accounting or retry policy. Logging can still
perturb timing: this diagnostic artifact is **not** a release/performance build
or a substitute for uninstrumented acceptance.

The metadata observers recorded only message type, direction, opaque numeric measurement hashes, fragment
index/count, queue counters and timestamps were recorded; no control bodies,
probe payloads, SDP or ICE credentials were logged by those observers. Ordinary
frontend measurement results were retained separately as private evidence. The fixed schedule for each
backend was two RTT-only pages and two pages with automatic RTT followed by
100 packets/s × 20 seconds stability, irrespective of earlier outcomes.

- All four C++ diagnostic pages completed. Six measurement admissions had
  matching browser/server `downStart` and ready records; received upload
  completions and result-fragment counts also matched. Server ready-handler
  acceptance took 0.120–0.241 ms, and the observed native control buffered amount
  was zero. These are diagnostic handler observations, not end-to-end latency
  or proof that a prior transport stall was repaired.
- Three of four unchanged Rust pages completed. The remaining page completed
  its automatic RTT, then failed the subsequent stability connection's
  **application probe**. Three candidate peers had successful ICE/DTLS/SCTP
  state but no returned probe frames before the original application-probe
  deadline. The observer saw successful JavaScript send calls; those alone do
  not prove wire delivery, as some native channel counters were still zero.
  No stability `downStart` was sent for those failed peers. This observation
  must not be mislabeled a missing ready reply or upload completion in that run.
- Both implementations' five probe-only payload endpoints were independently
  checked over HTTPS and denied. All **40 browser denial observations** were
  additionally corroborated by exact nonce-matched proxy OPTIONS404 records,
  the central Origin and real cross-site provenance, with no subsequent payload
  method observed for the same nonce. Six verifier tests include negative
  controls for missing HTTP proof, wrong status/origin, an unexpected actual
  payload request, missing nonce and duplicate observations.

`tests/probe_denial_contract.py` now distinguishes a returned HTTP404 from a
browser fetch rejection. A TypeError alone remains insufficient: TLS, network
failure and CORS can all produce it. The helper requires independent exact
request evidence rather than synthesizing a successful response object. Run
`python3 cpp/tests/probe_denial_contract_test.py` for its deterministic tests.

The earlier C++ ready timeout was not reproduced; its cause remains unproven.
The earlier Rust missing-completion result is likewise preserved, not explained
away by the different application-probe failure above. No Rust repair was mixed
into the migration. Diagnostic success does not promote the earlier failed
transactions to passes. Both hosts' production snapshots still matched after
temporary units, listeners, browser containers and authorization/key files were
removed; no production deployment or branch merge occurred.

### Corrected, uninstrumented four-combination follow-up

The corrected denial verifier was subsequently exercised against the original
uninstrumented UI/agent release bundles, not the control-logging clone. The
same four central-UI/agent combinations and both public agents were retained.
Rust/Rust, Rust UI/C++ agents and C++ UI/Rust agents each passed **both** remote
nodes' automatic RTT, real Restart and 100 packets/s × 20 seconds stability.
Their probe-agent payload denials were corroborated by all fifteen nonce-matched
preflight records; the data agent also passed all payload/progress checks.

The C++/C++ data-agent subcase passed again. Its probe-agent subcase passed
automatic RTT, but failed during Restart before reaching stability. The original
test assertion did not preserve that failed page's detailed phase/evidence;
an empty assertion message is not enough to classify it as the earlier ready
timeout, a missing completion, or a proven network defect. That evidence gap is
explicit, and future failure capture now saves phase and frontend evidence.
Thus **three of four complete combinations passed**, not a clean aggregate.
The original failed subcase and earlier failures remain retained. All three
hosts' production snapshots still matched after cleanup. This confirms the
verifier repair on real traffic but does not resolve the remaining intermittent
setup/transport investigation or authorize a production switch by itself.

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

These are functionality/portability checks, **not** a fair real-network
throughput/CPU/RSS/tail-latency benchmark. The original direct-target client and
the earlier local cross-site matrix remain separate from the actual public
central-UI/multiple-agent follow-up above. That follow-up passed the data-agent
subcases but still leaves probe-agent control failures, final deployment-profile
acceptance and the final requirements audit open.

Public builds intentionally use generic node identifiers/configuration. The
deployment UI build selected the preserved frontend using
`SHARED_ASSETS` and validated the actual node list/role configuration; do not blindly
substitute a generic frontend for private deployment aliases. Neither the
preserved frontend nor Rust source was edited in this milestone.
