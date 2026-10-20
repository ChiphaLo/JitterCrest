# Browser Network Quality Speedtest — Rust

A self-hosted browser speedtest and network-quality service. The Rust backend
provides bounded HTTP streaming and real WebRTC DataChannel probes; the existing
browser application supplies RTT, jitter, percentile, loss, ordering and stability
reports. English UI; Chinese deployment notes below.

## What is included

- `src/`: Tokio/Axum/Hyper HTTP service, limits, timeouts, signaling, frame codecs,
  statistics and session accounting.
- `server.js` and `webrtc_contracts.js`: preserved Node reference and embedded
  browser application. `build.rs` reads these files at **build time**; the Rust
  executable does **not** launch Node at runtime.
- `vendor/webrtc-sctp-0.17.1/`: service-specific SCTP compatibility fork, with its
  upstream MIT/Apache-2.0 notices retained. See [third-party notes](THIRD_PARTY.md).
- `test/`: local protocol, HTTP and Node/Rust comparison clients. Some browser,
  Caddy and Linux-specific probes need additional tools and are not the unit suite.
- `deploy/`: generic examples, not live infrastructure configuration.

No credentials, SSH host tables, production snapshots, internal operational logs,
or binaries are included in this repository. Deployment-specific IPs, domains and
node labels have been replaced with generic identifiers and reserved examples.
The default node list contains only the same-origin local server; it does not
connect to the original operator's servers. Configure `SPEEDTEST_NODES` for your
deployment. This sanitized public edition is separate from private production
configuration and is not a drop-in configuration for the original installation.

## Build and test

Rust **1.96 or newer** is required. Keep the lockfile and the local SCTP fork.

```sh
cargo build --release --locked
cargo fmt --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-features --locked
```

Minimal HTTP smoke (WebRTC remains disabled):

```sh
SPEEDTEST_HOST=127.0.0.1 PORT=3010 \
  SPEEDTEST_NODES='[{"id":"local","label":"Local server","base":""}]' \
  ./target/release/speedtest-rust
curl -fsS http://127.0.0.1:3010/health
```

Node 24 and npm are only needed for Node-reference/A-B tests:

```sh
npm ci
node --test test/*.test.js
node test/http-contract.mjs
```

Use isolated loopback instances for tests, not live production services. Do not
publish port 3010 directly while trusting reverse-proxy headers.

## Production setup

1. Build a release for the target platform.
2. Configure a dedicated unprivileged service account and adapt
   [`deploy/speedtest-rust.service`](deploy/speedtest-rust.service).
3. Adapt [the environment example](.env.example) and the
   [Caddy example](deploy/Caddyfile.example). Replace example names with domains
   you own. Caddy terminates TLS and proxies to loopback.
4. For WebRTC, explicitly set your server's **real public IPv4** in
   `SPEEDTEST_WEBRTC_UDP4` and enable `SPEEDTEST_RUST_RTC_EXPERIMENTAL=1`.
   DataChannel traffic is direct UDP, not carried by Caddy. NAT/cloud firewall
   rules must permit the relevant UDP path. Do not enable the private-RTC test
   switch on a production service.
5. Preserve a known-good release and rollback configuration; validate HTTP,
   TLS, real browser RTT and stability before accepting a deployment.

The WebRTC flag still uses its historical experimental name. This code is not a
claim of universal network/browser compatibility or a standards-certified SCTP stack.

### Example identifiers and deployment-specific modes

`192.0.2.10` is a reserved documentation address; `speed.example.com` is an example
domain. They are not functioning demo services. The optional `primary` identifier
selects the legacy dual-port HTTP/1.1 path on port 8443; `probe-only` selects the
legacy RTT/stability-only path. Neither is in the default node list.
`ENABLE_PROBE_ONLY_NODE=0` filters that optional node from an explicit node list.
If using those specialized modes, adapt the example direct-page/return-page
constants and the CSP in both `server.js` and `src/web.rs` to your own topology,
then validate Node/Rust compatibility. Ordinary same-origin deployment needs none
of those specialized modes. Wire formats, statistics and resource limits have not
been changed by anonymizing the deployment defaults.

### Legacy version-1 clients

`SPEEDTEST_RTC_LEGACY_CONTROL=1` sends a single, size-bounded JSON `downResult` for
older clients that cannot reassemble `controlFragment` messages. The default is
**off**, preserving current fragmented transport. Result fields, sample precision,
reason codes and density thresholds are not changed. Size/transport limits may
prevent very large legacy reports; upgrade the client instead of weakening the
validity checks. Existing historical Node pages can be embedded by building with
their corresponding `server.js` / contract pair; do not silently mix versions.

## Architecture and limitations

- HTTP: fixed 10 KiB/100 KiB/1 MiB payloads, bounded streams, upload accounting,
  CORS, active/request/byte budgets, slow-client cleanup and graceful shutdown.
- Probe frame: 128-byte binary frames with identity/sequence/timestamp fields.
- Statistics: original RTT/jitter/percentile/loss/ordering and 0.99 density semantics.
- HTTP error header capitalization/order may differ from Node while preserving
  status and framing semantics. This is not byte-exact HTTP emulation.
- Address policy rejects unsafe remote SDP candidates; public UDP reachability
  and valid DNS/certificates are deployment prerequisites.
- Resource limits in examples are starting points, not capacity guarantees.
- See [SECURITY.md](SECURITY.md) for sensitive-data handling and reporting.

## 中文说明

这是 Node 测速服务的 Rust 服务端迁移。前端模板与契约在构建期嵌入，运行
Rust 服务不依赖 Node 进程。请保留 `server.js`、`webrtc_contracts.js` 与 vendor
目录，它们是可复现构建/A-B 的组成部分，而不是应删除的临时文件。

生产部署请使用自己的域名、节点配置、公网 IP 与凭据。仓库中的地址和节点名称
已改为通用示例，默认只有同源本地节点；保留的特殊模式需按实际拓扑配置，
示例地址不是可用的演示服务。先验证 HTTPS、HTTP 测速和真实公网 DataChannel，再切换；
保留回滚版本。旧前端单消息兼容选项默认关闭，不改变统计定义或密度要求。

## License

Project code is licensed under [MIT](LICENSE). Vendored/upstream components retain
their own licenses and notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
