# Central UI and measurement-only Rust agents

## Connection model

There is no master-to-agent tunnel, fleet registration or bulk-traffic relay.
The browser loads one UI, reads its `SPEEDTEST_NODES` manifest, and connects
straight to the selected node:

```text
browser -- page + node manifest --> UI host
browser -- HTTP upload/download --> selected agent's Caddy --> Rust agent
browser -- HTTPS offer/cancel ---> selected agent's Caddy --> Rust agent
browser ===== ICE/DTLS/SCTP UDP =================================> agent
```

A UI host may also be a measurement node. No database, Redis, message bus,
server-to-server heartbeat or Node runtime is required. Selecting another node
cancels the prior browser session. Ordinary node switching stays on the UI host.
Special legacy probe-only/direct-page behavior remains unchanged.

## Two builds, one protocol

```sh
# Default: standalone site (UI plus all measurement APIs).
cargo build --release --locked --target-dir target/site

# Remote agent: no embedded HTML/favicon, no page generation or node manifest.
cargo build --release --locked --no-default-features --target-dir target/agent
```

Both artifacts are named `speedtest-rust`; use separate output directories to
avoid accidentally replacing the site artifact with the agent build. Retain
versioned releases and the existing systemd/Caddy hardening and rollback model.
The agent returns **404** for `/`, `/speed`, `/speed/` and favicon routes; health,
latency, client-info, upload/download and WebRTC endpoints remain. Copying the
agent binary does not require a `public/` directory. The default build remains
backwards-compatible. Startup logs report `web_ui` to identify the role.

This split primarily eliminates repeated UI deployment/configuration and version
drift. The former HTML was only about 0.3 MB, compared with an approximately
18 MB Linux service binary: do not expect a large throughput/CPU improvement.
The WebRTC stack and measurement code still belong on every agent.

## Configuration

On the UI host, configure the list once (restart the service after changes):

```sh
SPEEDTEST_NODES='[{"id":"jp","label":"Japan","base":"https://jp.example.net/","rtcTargetMs":25},{"id":"hk","label":"Hong Kong","base":"https://hk.example.net/","rtcTargetMs":25}]'
```

These are examples, not functioning endpoints. Use ordinary node IDs for new
agents. A central page derives additional CSP `connect-src` origins from parsed
HTTP(S) node URLs; adding a node no longer needs a CSP source-code edit/rebuild.
The small legacy static CSP allowlist remains for backwards compatibility.
Relative bases stay same-origin. URL paths/credentials cannot inject CSP directives.
The manifest contains public routing information only, never credentials.
Agents built without `web-ui` ignore `SPEEDTEST_NODES`.

An HTTPS `base` on the legacy probe-only node keeps the page on the central UI;
its old direct-page navigation is used only when no secure agent is configured.
The node's RTT/stability-only measurement restriction remains unchanged.

### Direct public IP HTTPS

The node `base` may be an IP URL, for example `https://192.0.2.10/`, provided
the browser trusts a certificate whose IP SAN matches that address. A domain
certificate is not valid for an IP URL. Keep domain and IP listeners on the same
agent, and use independent certificate policies. See
[the agent Caddy example](Caddyfile.agent.example).

Let's Encrypt supports IP certificates through the `shortlived` ACME profile
(160-hour lifetime); automatic renewal must be monitored. IP validation uses
HTTP-01/TLS-ALPN-01, not DNS-01. Caddy must support the profile configuration;
do not assume an old packaged Caddy can manage it. IP-literal clients normally
omit SNI, so verify the no-SNI/default-certificate path as well as certificate
issuance. A successful configuration parse alone does not prove usable TLS.

Adding an IP endpoint does not require restoring a node's retired DNS name or
putting any proxy/CDN in the measurement path. A usable HTTPS IP agent allows
the old HTTP direct-page exception to be retired without a mixed-content bypass.

On **each** agent, allow only the actual UI origin:

```sh
ALLOWED_ORIGINS=https://speed.example.com
# Necessary only when frontend and agent are cross-site, not merely cross-origin.
SPEEDTEST_ALLOW_CROSS_SITE=1
```

Cross-site access is **off by default**. Enabling it additionally requires an
exact nonempty, non-opaque Origin in `ALLOWED_ORIGINS`; `null`, missing and
unlisted origins remain rejected on cross-site measurement requests. CORS uses
an exact echoed Origin and `Vary: Origin`, not `*`. `PUBLIC_CORS=1` is not a
replacement for these settings. Request/peer/byte/time limits are unchanged.
CORS/fetch metadata is browser defense, not authentication against arbitrary
HTTP clients; do not expose an unbounded measurement endpoint.

Preserve the deployment's public UDP address, experimental RTC setting, resource
limits and trusted reverse-proxy configuration. A standalone UI origin that also
measures locally must be in that server's own `ALLOWED_ORIGINS`.

The legacy `primary` node ID selects the dedicated H1 single-download path. It
now uses the selected agent's host, not the central page's host. For a custom H1
listener set `singleDownloadBase`, e.g. `https://hk.example.net:8443/`; configure
TLS, CORS and H1 on that listener as well. Other node IDs retain their existing
HTTP/2 path. H1 upload restrictions and measurement accounting are unchanged.

## Migration boundaries

1. Stage a measurement-only agent without replacing the live standalone site.
2. Verify public TLS/DNS, CORS/preflight, direct HTTP throughput, actual UDP
   RTT/stability and frontend/backend protocol compatibility.
3. Add its URL to the central manifest, then cut over only that agent with a
   rollback-ready old release. UI-only releases need not restart remote agents.
4. Do not reverse-proxy upload/download through the UI host: that measures a
   different path and consumes the main host's bandwidth.

HTTPS pages cannot directly fetch an HTTP-only agent (mixed content). Such nodes
must keep an approved direct-page exception or obtain a genuinely usable secure
direct endpoint. Do not bypass browser security or add a cross-border relay just
to remove one copy of the UI. Provider-specific domain interception and existing
network boundaries take precedence over a uniform deployment diagram. Missing
DNS/TLS is not fixed by the role split. This change does not modify production
routes, DNS, certificates or running services automatically.

## Checks

```sh
cargo fmt --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-features --locked
cargo clippy --workspace --all-targets --no-default-features --locked -- -D warnings
cargo test --workspace --no-default-features --locked
python tests/browser_central_ui.py --ui-binary target/site/release/speedtest-rust \
  --agent-binary target/agent/release/speedtest-rust --output target/central-ui-check
```

The optional browser regression needs Python/Playwright and Caddy. It runs one UI
and two agents on disposable loopback TLS listeners, exercises same-site and
cross-site direct HTTP, real WebRTC RTT, live upload/download charts and node
switching. The UI proxy rejects measurement routes so it cannot silently act as
a relay. Its loopback TLS exception must never be copied into production checks.
