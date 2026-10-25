# Legacy Node reference — archived

This source-only snapshot predates the Rust-only source cleanup and the latest
high-speed upload fixes. It includes the live-chart feature but is not the current
production implementation. Project source remains under the repository's [MIT
license](../../LICENSE); npm dependencies retain their own licenses.

Deployment identifiers have been replaced by generic names, `example.com` domains
and reserved documentation addresses. Default node selection is same-origin local.
These examples are not live demo services. `SOURCE-MANIFEST.json` records SHA-256
hashes of the preserved source files.

For isolated recovery/reference work only, from **this directory**, using Node 24:

```sh
npm ci
SPEEDTEST_HOST=127.0.0.1 PORT=3019 node server.js
npm test
```

Installed `node_modules` are intentionally not committed. Historical A/B clients
may require separately supplying the Rust binary and additional test tools; they
are preserved as evidence, not part of the active Cargo suite.

**Security warning:** the pinned Node/Werift dependency tree has known high-severity
`ip` public-address-classification advisory entries, propagated via `werift-ice`
and `werift`. The archive also lacks subsequent throughput/lifecycle fixes. Do not
expose it to untrusted production traffic. Archiving does not remediate old flaws.
