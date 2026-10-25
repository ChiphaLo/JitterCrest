# Independent-agent TLS and resource validation — 2026-10-26

This is a scoped acceptance milestone, not full objective completion or a
production cutover. Runtime sources, protocol, frontend and production remain
unchanged. The new work is shared actual-process/browser tests and evidence.

## Central UI to independent IP agents

`tests/agent_tls_contract.py` starts three independent backend processes (UI,
measurement-only agent, probe-only agent) and one isolated local Caddy instance.
The UI is served at an HTTPS domain origin; agents use HTTPS **IP origins** on
different ports. The test certificate includes both DNS and IP SANs. Python
verifies chain/hostname against that certificate and rejects it without the
explicit test trust anchor. Chromium pins only its ephemeral public key; no CA
is installed, no blanket TLS bypass, route interception or response mock is used.

The same unchanged frontend is exercised with all four combinations:

| UI | Measurement agent | Result in each C++ profile |
|---|---|---|
| Rust | Rust | Passed reference |
| C++ | C++ | Passed |
| Rust | C++ | Passed |
| C++ | Rust | Passed |

C++ profiles: **Release, ASan+UBSan, and TSan**, with separately built no-UI agent
executables using the matching patched native transport dependencies. Their two
instrumented agent builds each passed seven CTests. Rust remains its unchanged
Release reference; these are correctness/safety tests, not performance comparisons.
All twelve profile/combination runs passed, including clean child-process exits.

Verified behavior in every combination:

- Agents return 404 for `/` and `/favicon.svg`; the central UI supplies the page.
- The page's node selector actually selects the configured independent agent.
- CSP permits the configured agent/probe origins. Exact allowed-origin CORS is
  returned; an unapproved origin's upload preflight is rejected with 403 and no
  allow-origin header. Cross-site operation is explicitly enabled for the exact
  UI origin, not treated as an automatic consequence of an origin allowlist.
- Caddy access logs prove an actual Chromium upload preflight and a successful
  upload with `Sec-Fetch-Site: cross-site`. Playwright's page request events do
  not expose Chromium's internal preflights; they are not fabricated from a
  client concurrency/request intention.
- Browser fetch completes 10 KiB, 100 KiB and 1 MiB downloads, a 1 MiB upload and
  exact final upload accounting through the independent agent IP, not the UI.
- The browser receives the first NDJSON progress record **before** starting an
  upload; final `bytes == measuredBytes == 1 MiB`, complete and not failed/expired.
  This proves no whole-response proxy buffering; intermediate slow-upload sample
  continuity is separately covered by the existing HTTP lifecycle suite.
- The existing paired frontend hook completes; the real Restart button finishes
  20 pkt/s RTT testing and renders both directions' average/P95 with no incomplete
  badge or uncaught page error. Signaling goes to the selected agent, not the UI.
- Navigating to the probe-only node selects that role. Its seven tested payload
  routes/aliases return 404, its upload preflight returns 404 and is blocked by
  Chromium, and `/latency` remains 204.
- A real browser-generated WebRTC offer to the probe-only role **without a public
  mapping** fails closed with 500 `WebRTC offer failed`; cancellation is 204.

The original private Rust reference selects its special role using an operational
proxy marker, while sanitized editions/C++ offer generic role configuration.
The test accepts an explicit validated `--rust-probe-node-id` and applies the
appropriate proxy marker. This translation is deployment-role configuration,
not a claim that differently named environment settings are identical. C++ also
sets `SPEEDTEST_PROBE_ONLY=1`. Never publish a private runtime configuration to
make a generic test command look identical.

**Still unverified:** successful public-NAT probe-only ICE/data-plane connectivity,
public certificates/firewalls, real Internet performance and effective production
systemd hardening. The intentionally rejected missing-mapping case does not prove
any of those positive paths.

## Finite admission and exhaustion contracts

`tests/resource_contract.py` passed the same scenarios against the independent
Rust reference and C++ Release, ASan/UBSan and TSan builds:

| Scenario | Actual assertion |
|---|---|
| Global active throughput ceiling | Eight uploads on distinct trusted fixture identities acquire leases, confirmed by 100 Continue; ninth gets exact 429 reason while health remains available |
| Per-IP active ceiling | Eight admitted uploads on one identity, exact per-IP 429 for the ninth |
| Abrupt reset recovery | All admitted uploads are reset without completing their declared body; capacity becomes usable again; three rounds for each active-limit scenario |
| Progress registry capacity | Eight records across distinct fixture identities; ninth rejected with exact 429 capacity reason |
| Progress ownership and one-use lanes | Another identity cannot read the record; a completed lane cannot attach twice |
| Final retention and reclamation | Finished records still occupy capacity during the five-second result-retention window, then expire and permit new records; old ID returns 404 |
| Additional C++ socket hard ceiling | Eight actually established backend-owned keepalive sockets; ninth is closed; reset releases accepted sockets and service recovers |

The last row is explicitly C++ hardening coverage, **not** a shared Rust
equivalence claim: the original reference has no `MAX_HTTP_CONNECTIONS` setting.
These bounded scenarios do not prove that every queue, malformed-traffic shape
or long-running memory behavior is safe. Global native RTC admission, negotiation
expiry and wider cancellation/exhaustion cases remain separate acceptance work.

ASan leak detection and halt-on-error, UBSan halt-on-error and TSan halt-on-error
were enabled. All process logs are checked for sanitizer findings and bounded
clean exits. Instrumented native transport is distinct from system OpenSSL/curl,
which are not claimed completely source-instrumented.

## Reproduction

Use the documented native dependency bootstrap/builds first. Prepare a virtual
environment with Playwright (observed version 1.64.1), install its matching Chromium
and provide a local Caddy executable (observed Ubuntu 2.6.2). These test tools are
not runtime/backend dependencies. No Node server is involved.

```sh
# Preserve two independent Rust build outputs: web-ui default and no-default-features.
cargo build --release --locked --target-dir /absolute/local/rust-ui
cargo build --release --locked --no-default-features --target-dir /absolute/local/rust-agent

cmake -S cpp -B cpp/build/agent -DCMAKE_BUILD_TYPE=Release \
  -DSPEEDTEST_WEB_UI=OFF -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build cpp/build/agent --parallel 4

python3 cpp/tests/agent_tls_contract.py \
  --cpp-ui cpp/build/release/speedtest-cpp --cpp-agent cpp/build/agent/speedtest-cpp \
  --rust-ui /absolute/local/rust-ui/release/speedtest-rust \
  --rust-agent /absolute/local/rust-agent/release/speedtest-rust \
  --rust-probe-node-id probe-only --caddy /absolute/local/caddy \
  --output /private/evidence/agent-tls.json

python3 cpp/tests/resource_contract.py --cpp cpp/build/release/speedtest-cpp \
  --rust /absolute/local/rust-ui/release/speedtest-rust \
  --output /private/evidence/resources.json
```

The marker above is for a generic sanitized reference; choose the marker actually
recognized by the chosen Rust edition, rather than changing Rust for the test.
Repeat with separate matching sanitizer UI/agent builds and documented sanitizer
options. Reports include executable/test-source hashes and reject mid-run changes.
Raw paths, logs and operational configuration stay private; only this aggregate
report and generic test sources are published.

Earlier fixture failures were corrected from authoritative behavior, not by
weakening the server: the private Rust role requires its proxy marker, a browser
cannot read a failing preflight as a normal fetch response, and exact rate-limit
reason strings must come from current Rust. The failed preflight/error-text
fixture reports are retained privately; they are not relabeled passing runs.

## Remaining gates

Two unsuppressed Boost coroutine static-analysis diagnostics, broader native RTC
resource/timeout coverage, successful mapped probe-only connectivity, real-network
measurements, a reproducible complete release/dependency bundle and the final
requirement-by-requirement audit remain open. A requested later merge/production
deployment must wait for those gates; this milestone does not bypass them.
