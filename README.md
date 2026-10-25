# Browser Network Quality Speedtest — Rust

A self-hosted browser speedtest and network-quality service. The Rust backend
provides bounded HTTP streaming and real WebRTC DataChannel probes; the existing
browser application supplies RTT, jitter, percentile, loss, ordering and stability
reports. English UI; Chinese deployment notes below.

## What is included

- `src/`: Tokio/Axum/Hyper HTTP service, limits, timeouts, signaling, frame codecs,
  statistics and session accounting.
- `public/index.html`: standalone browser application and inline browser
  contracts, embedded directly by Rust's `include_str!`. Building and running
  the service requires neither Node.js nor npm nor a JavaScript build step.
- `vendor/webrtc-sctp-0.17.1/`: service-specific SCTP compatibility fork, with its
  upstream MIT/Apache-2.0 notices retained. See [third-party notes](THIRD_PARTY.md).
- `tests/`: Rust frontend/HTTP integration checks and an optional headless browser
  check against the actual Rust service over local Caddy HTTP/2.
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

The default Cargo suite needs no Node installation. An optional real-browser
check requires Python, Playwright/Chromium and Caddy (all test listeners are
loopback-only; the test's self-signed TLS exception applies only to those listeners):

```sh
python -m pip install playwright
python -m playwright install chromium
python tests/throughput_worker.py
python tests/browser_speed_charts.py --caddy /path/to/caddy
```

The sanitized old Node source and its compatibility clients are available under
[`archive/node/`](archive/node/). Installed npm dependencies and full operational
recovery ZIPs remain private. The active source, Cargo build and Rust CI do not
read or execute the archive.

### Live speed charts

Download and upload each have a live Mbps-over-time chart. Raw worker samples
update a smoothed curve throughout the test. A horizontal dashed cumulative
estimate is visible while measurement runs; final accounting replaces it with
the exact result. Updated agents supply server-received upload counters; a legacy
agent's explicit 404 falls back to a labeled browser-send estimate.
Restarting a test clears old chart data. Mobile and dark-mode layouts are supported.

### High-speed transfer behavior

The 256 KiB upload buffer and 512 KiB download chunk are reused throughout timed
streams; they are not total test-file sizes. Single-stream upload runs for about
12 seconds, including a 2-second warm-up; its final rate still uses the server's
post-warm-up byte count and duration. Multi-stream final accounting is unchanged.
The non-streaming multi-upload fallback reuses one immutable 4 MiB Blob across
lanes and uses the same aggregated server-receipt telemetry as streaming uploads.
Older agents instead use labeled browser upload-progress estimates, not just
completed-chunk ACKs. This avoids repeated BufferSource copies and sparse ACK bins.
Upload receipt snapshots run at approximately 100 ms intervals. Genuine receipt
stalls remain zero; missing telemetry or response finalization is not converted
into a fabricated zero-speed sample. Legacy browser-send sampling stops when
production ends, before waiting for the server response.
The UI explicitly shows this final accounting phase. Producers close at their
deadline even without another pull; aborts/timeouts release their timers.

Missing, malformed, zero or mismatched upload acknowledgements fail explicitly
instead of becoming a successful zero-speed result. Download budget exhaustion
fails the body rather than silently returning a shorter successful stream.
Resource limits remain enabled: the default stream-upload cap is 2 GiB and each
direction has a 4 GiB/IP byte budget over 60 seconds. Configure limits according
to capacity; do not confuse the ordinary fixed-upload limit with the stream limit.

For a local high-bandwidth regression, the browser test accepts
`--download-mbps 400 --upload-mbps 400 --latency-ms 50 --streams 1` (or `4`).
Add `--force-chunked` with multiple streams to exercise the compatibility path.
These are browser-emulation limits, not a guarantee of achieved WAN bandwidth.

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
constants and the CSP in `public/index.html` and `src/web.rs` to your own topology,
then validate browser/HTTP behavior. Ordinary same-origin deployment needs none
of those specialized modes. Wire formats, statistics and resource limits have not
been changed by anonymizing the deployment defaults.

### Legacy version-1 clients

`SPEEDTEST_RTC_LEGACY_CONTROL=1` sends a single, size-bounded JSON `downResult` for
older clients that cannot reassemble `controlFragment` messages. The default is
**off**, preserving current fragmented transport. Result fields, sample precision,
reason codes and density thresholds are not changed. Size/transport limits may
prevent very large legacy reports; upgrade the client instead of weakening the
validity checks. For a historical client, use its matching page/contracts from
the recovery archive; do not silently mix protocol versions.

## Central UI / lightweight remote agents

The default build remains a standalone site. Use
`cargo build --release --no-default-features --locked` for remote measurement
agents **without embedded frontend assets**. The browser connects directly to
each selected agent; the UI host is not a bandwidth relay. Centralize the node
list in the UI host's `SPEEDTEST_NODES`. Cross-site measurement is opt-in and
requires exact allowed origins; default security behavior is unchanged.
See [multi-node configuration and migration boundaries](deploy/multi-node.md).

## Smooth speed charts and upload receipt telemetry

Speed samples are collected at approximately **100 ms** intervals, independently
of a capped **60 fps** Canvas renderer. Numeric text is updated at up to 30 Hz.
Axes/SVG nodes are reused; a cached monotone cubic path and a 500 ms rolling
visual estimate smooth the trace without changing raw samples or final results.
The dashed cumulative estimate is visible during measurement (`~` means
provisional); final accounting replaces it with the exact final result.

Streaming and chunked uploads can use the optional `/upload-progress` API:

- `POST ?lanes=1&warmupMs=2000` creates an opaque, peer-IP-owned session.
  Chunked fallback adds `chunked=1`; allowed lane counts are 1/2/4/8.
- `GET ?id=...` streams bounded newline-delimited receipt counters at 10 Hz.
- Upload requests attach `progressId=...&lane=...`. Chunked lanes cannot overlap;
  `POST ?id=...&finish=1` closes their telemetry after their final byte ACKs.
- Limits apply to session count, active observers, per-IP usage, input frames,
  lifetime and slow-client cleanup. Cancellation releases the observer's lease.

One telemetry stream aggregates all lanes. It measures bytes received by Rust,
not bytes queued inside the browser. Genuine receipt stalls remain zero; missing
telemetry is not manufactured into zero throughput. Final upload ACK validation,
warmup rules, byte budgets and final time denominators are unchanged. A legacy
backend's 404 uses an explicitly labeled browser send estimate; upgrade the site
and agents together to enable server receipts. Measurement traffic stays direct
to the selected agent, with the existing exact-origin CORS policy.

The RTT plot computes P10/P50/P95/P99 independently for the up and down samples.
Gray dashed percentile guides and numeric labels stay within their own row;
both directions retain a shared RTT scale and the existing percentile definition.

Short zero-rate windows can reflect real TCP delivery stalls rather than an
exhausted upload file. A receiver may wait for a missing segment before delivering
later bytes to the application. Diagnose with independent clients, receiver
counters and transport evidence; do not hide such intervals by inventing samples.

Optional checks: `python tests/throughput_worker.py`,
`python tests/chart_render_math.py`, `python tests/rtt_percentile_plot.py`, and
`python tests/browser_speed_charts.py`.

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

这是 Node 测速服务的 Rust 服务端迁移。前端页面和浏览器契约现在位于
`public/index.html`，由 Rust 直接嵌入；构建、运行以及 Cargo 测试均不依赖
Node/npm。脱敏的旧 Node 源码位于 `archive/node/`，完整依赖恢复包另行私下保存，
不参与当前构建。浏览器端
JavaScript 仍负责实时图表和 WebRTC，这不意味着服务端运行 JavaScript。

生产部署请使用自己的域名、节点配置、公网 IP 与凭据。仓库中的地址和节点名称
已改为通用示例，默认只有同源本地节点；保留的特殊模式需按实际拓扑配置，
示例地址不是可用的演示服务。先验证 HTTPS、HTTP 测速和真实公网 DataChannel，再切换；
保留回滚版本。旧前端单消息兼容选项默认关闭，不改变统计定义或密度要求。

## License

Project code is licensed under [MIT](LICENSE). Vendored/upstream components retain
their own licenses and notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
