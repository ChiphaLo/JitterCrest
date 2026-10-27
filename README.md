# JitterCrest

[简体中文](README.zh-CN.md) | English

JitterCrest is a self-hosted network quality tool. Its maintained C++20 backend
provides bounded HTTP streaming, upload progress and native WebRTC DataChannel probes. The browser renders download /
upload charts and RTT, jitter, percentile, loss, ordering and stability reports.
Building and running the active service requires neither Rust nor Node/npm.

## Layout

- `cpp/`: active server, protocol/statistics logic, tests, build presets, benchmarks
  and generic deployment templates.
- `public/`: active shared browser application, embedded in the UI executable.
- `archive/rust/`: frozen Rust source, lockfile, vendored SCTP dependency, original
  tests/templates/frontend and optional historical comparison oracle.
- `archive/node/`: older sanitized Node reference.

Archives are excluded from the default CMake build and CI. They are historical
references, not maintained alternative production backends. Legacy configuration
names do not imply a Rust runtime dependency.

## Build and test

Linux x86_64 is validated. Install CMake >=3.22, Ninja, a C++20 compiler (validated
with Clang18), Boost.System1.83, OpenSSL3, libcurl>=7.85 with asynchronous DNS,
nlohmann-json>=3.11, Python3 and Git. The native transport bootstrap pins
libdatachannel0.24.6 and applies the versioned dependency patches.

```sh
CC=clang-18 CXX=clang++-18 \
  bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=clang++-18 \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure

# Local HTTP smoke. The executable embeds the UI; there is no JS build step.
SPEEDTEST_HOST=127.0.0.1 PORT=32132 build/cpp/speedtest-cpp
# In another terminal:
curl -fsS http://127.0.0.1:32132/health
```

Add `-DSPEEDTEST_WEB_UI=OFF` for an independent measurement-only agent. It does
not embed/serve the webpage. Existing `cmake -S cpp ...` builds and the separate
Debug, Release, ASan+UBSan and TSan presets in `cpp/` remain supported.

Native RTC is explicitly enabled with `SPEEDTEST_RUST_RTC_EXPERIMENTAL=1` after
configuring a real public IPv4 and validating UDP reachability. The legacy name
is retained as a configuration key only; no Rust process is called. Without it,
RTC requests fail closed503. `.env.example` is an environment-variable reference;
the executable does not automatically load dotenv files.

## Guided setup (Linux)

The interactive wizard generates a new main, data-agent or probe-only setup. It
requires Python 3 for setup and the build tools and native libraries listed above;
the running C++ service does not require Python. On Windows use Linux/WSL.

1. On each agent host, obtain this repository and run `python3 cpp/deploy/quickstart.py`.
   Choose **2** for a data agent or **3** for a probe-only agent; enter its HTTPS
   origin, the main page HTTPS origin, an ID, and the requested local settings.
   Probe-only agents require their public UDP IPv4. The output directory must be new.
   Choose a dependency prefix writable by the build user (the default `/opt` path
   may require administrator setup).
2. In the generated directory, run `./build.sh --bootstrap` to build the pinned
   transport and service, or `./build.sh` to reuse the configured dependency prefix.
   Run `./run.sh` in the foreground. Transfer the generated `node.json` securely to
   the main host; it contains public node addressing, not a password.
3. On the main host, run the same wizard, choose **1**, and import the agent
   `node.json` files when prompted. Build and run the generated main directory in
   the same way. To add a node to an existing generated main configuration:

   ```sh
   python3 cpp/deploy/quickstart.py --existing /path/to/main --add-node /path/to/node.json
   ```

   A backup of `runtime.env` is created before an atomic update; the wizard
   refuses non-canonical hand-edited configurations. Restart the main process or
   service manually to apply the node list; rebuilding is not needed.

Review `Caddyfile.example` and configure a trusted HTTPS reverse proxy and firewall
separately. The wizard does not install or edit Caddy, obtain certificates, open
ports, or deploy remotely. IP-based HTTPS requires a suitable trusted IP
certificate. Verify UDP and ICE connectivity through any NAT. The browser talks
directly to the agents; there is no persistent main-to-agent control channel.

An optional `service.example` and `install-service.sh` are generated. Before
running `sudo ./install-service.sh`, review both, create the selected non-root user,
and ensure it can traverse and read the generated directory and execute its
scripts and binary. The installer refuses to replace an existing unit. Use
`python3 cpp/deploy/quickstart.py --help` for non-interactive JSON options. For
example, save the following as `agent-config.json` on the agent host, then run
`python3 cpp/deploy/quickstart.py --config agent-config.json --output /path/to/new-agent`:

```json
{"role":"data", "id":"node-1", "label":"Node 1",
 "public_origin":"https://node.example.com", "main_origin":"https://main.example.com",
 "port":32132, "native_prefix":"/opt/jittercrest-deps", "service_user":"jittercrest"}
```

Replace the example addresses and paths. `service_user` must already exist if
installing a systemd service; the output parent directory must also exist.

## Configuration and deployment

Published defaults are generic and use only a same-origin local node. Configure
your own `SPEEDTEST_NODES`, allowed origins, public UDP mapping and TLS proxy.
Never expose a listener trusting arbitrary forwarded identity headers. Use a
non-root service and bounded resources. UI, probe-only and data-agent roles are
described in the [C++ documentation](cpp/README.md); generic service/proxy examples
and packaging tools are in [`cpp/deploy/`](cpp/deploy/).

No credentials, real deployment configuration, binary releases, backups or raw
operational captures belong in this repository. See [security](SECURITY.md) and
[third-party notices](THIRD_PARTY.md).

## Validation and history

- [Compatibility and safety acceptance](cpp/docs/acceptance-audit.md)
- [Measured Rust/C++ performance comparison](cpp/docs/performance-findings-recovery.md)
- [Initial authorized production rollout](cpp/docs/production-rollout.md)
- [Archive structure and Rust retirement](cpp/docs/rust-archive.md)

Historical benchmarks do not imply C++ is universally faster. Rust led the
recorded local bulk-throughput cases; C++ had selective lightweight-request and
high-load-memory advantages. Reports retain their source identities, finite
test scopes and unsuppressed analyzer-model findings.

## License

Project code is MIT licensed. Dependencies retain their original licenses and
notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
