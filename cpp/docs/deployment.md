# Build, deployment and node management

[Back to README](../../README.md)

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
libnghttp2>=1.59 with pkg-config, nlohmann-json>=3.11, Python3 and Git. The native transport bootstrap pins
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

## Node management and command-based enrollment

The server picker supports search, capability labels and a remembered selection.
Open **Manage nodes** in the picker to access `/admin` on the main host.

1. Create a **main** setup with `python3 cpp/deploy/quickstart.py`, build/run it and
   configure its trusted HTTPS entry. The generated `runtime.env` contains a random
   `SPEEDTEST_ADMIN_TOKEN`, the public origin and a writable registry directory.
   Read the administrator token locally with `grep '^SPEEDTEST_ADMIN_TOKEN=' runtime.env`.
2. Log in to `/admin`. Choose **接入节点**, enter an ID, display name, the agent's
   HTTPS origin, public UDP IPv4 and capability, then generate the install command.
   An origin without a port uses the built-in TLS listener port 8444.
   The single-use enrollment key expires after **one hour**.
3. Copy the command to the corresponding Linux VPS. It claims the enrollment,
   builds/tests a UI-free C++ Docker image from the main server's allowlisted
   source bundle, lets Caddy obtain the certificate on 80/443, installs a systemd
   timer that copies it every 10 minutes, starts the agent with its built-in TLS
   listener, verifies HTTPS and HTTP/2 and activates the node.
   The node then appears in the main page's picker without a main-server restart.

Use a dedicated Linux VPS with Python 3, curl, sudo/root access, free TCP ports
80/443 and the listener port (default 8444), and reachable public UDP. If Docker
is missing, the installer installs
`docker.io` with apt on Debian/Ubuntu. Other distributions require an existing
working Docker installation. Domain names must resolve to the VPS. The installer
uses Caddy 2.11.9, including an explicit ACME `shortlived` profile for IP addresses;
certificate issuance, provider HTTPS policy and NAT/firewall access must succeed.
Existing listeners on the required ports are left untouched and setup stops.
The first build takes several minutes; native build layers are cached for reuse.

Management supports rename, enable/disable, credential reset and removal. Claimed
nodes report heartbeats every 30 seconds; missing heartbeats for 90 seconds show
as offline. Existing configured nodes show as configured until enrolled. Browsers
exchange measurements directly with agents. Heartbeats only report presence.
The registry takes its initial node list from `SPEEDTEST_NODES`; after its first
start, `/admin` is authoritative and updates are saved atomically with mode 0600.
`--add-node` is available before the registry's first start; use `/admin` afterward.

For an existing main service, set `SPEEDTEST_ADMIN_TOKEN` to a random value of at
least 32 characters, `SPEEDTEST_PUBLIC_ORIGIN` to its exact HTTPS origin, and
`SPEEDTEST_ADMIN_STATE` to an absolute writable private directory. A hardened
systemd unit needs `ReadWritePaths=` for that directory. Without these settings,
management endpoints are unavailable. See [security](../../SECURITY.md).

Installer state lives in `/opt/jittercrest-agents/<id>/`; rerun the same command
to resume an interrupted installation. The backend runs as a non-root user in a
read-only container. Removing a node revokes its controller credential and removes
it from the picker; when retiring the service, stop the containers
`jittercrest-<id>` and `jittercrest-<id>-tls` and disable
`jittercrest-<id>-tls-sync.timer` on the VPS.

## Manual guided setup (Linux)

The interactive wizard also supports manual main, data-agent or probe-only setups. It
requires Python 3 for setup and the build tools and native libraries listed above;
the running C++ service does not require Python. On Windows use Linux/WSL.

1. On each agent host, obtain this repository and run `python3 cpp/deploy/quickstart.py`.
   Choose **2** for a data agent or **3** for a probe-only agent; enter its HTTPS
   origin, the main page HTTPS origin, an ID, the built-in TLS listener port
   (default 8444) and the requested local settings.
   Probe-only agents require their public UDP IPv4. The output directory must be new.
   Choose a dependency prefix writable by the build user (the default `/opt` path
   may require administrator setup).
2. In the generated directory, run `./build.sh --bootstrap` to build the pinned
   transport and service, or `./build.sh` to reuse the configured dependency prefix.
   Once Caddy serves `Caddyfile.example` and has issued the certificate, run
   `sudo ./sync-cert.sh` to copy it into `tls/`, then run `./run.sh` in the
   foreground. Transfer the generated `node.json` securely to
   the main host; it contains public node addressing, not a password.
3. On the main host, run the same wizard, choose **1**, and import the agent
   `node.json` files when prompted. Build and run the generated main directory in
   the same way. Before starting its registry for the first time, import another node:

   ```sh
   python3 cpp/deploy/quickstart.py --existing /path/to/main --add-node /path/to/node.json
   ```

   A backup of `runtime.env` is created before an atomic update; the wizard
   refuses non-canonical hand-edited configurations. Once the main registry has
   started, manage nodes in `/admin`.

In generated setups Caddy obtains the certificate and serves the page, `/admin`
and HTTP/1.1 endpoints; every node address in the picker, including the main
server's own entry, is its built-in TLS listener, for example
`https://main.example.com:8444`. Open that TCP port in the firewall.
Review `Caddyfile.example` and configure a trusted HTTPS reverse proxy and firewall
separately. The wizard does not install or edit Caddy, obtain certificates, open
ports, or deploy remotely. IP-based HTTPS requires a suitable trusted IP
certificate. Verify UDP and ICE connectivity through any NAT. The browser talks
directly to the agents. Manually configured agents have no enrollment heartbeat.

An optional `service.example` and `install-service.sh` are generated. Before
running `sudo ./install-service.sh`, review both, create the selected non-root user,
and ensure it can traverse and read the generated directory and execute its
scripts and binary. It copies the certificate once, then installs the service and
`jittercrest-<id>-tls-sync.timer`, which refreshes the copy every 10 minutes. The
installer refuses to replace an existing unit. Use
`python3 cpp/deploy/quickstart.py --help` for non-interactive JSON options. For
example, save the following as `agent-config.json` on the agent host, then run
`python3 cpp/deploy/quickstart.py --config agent-config.json --output /path/to/new-agent`:

```json
{"role":"data", "id":"node-1", "label":"Node 1",
 "public_origin":"https://node.example.com", "main_origin":"https://main.example.com",
 "port":32132, "tls_port":8444, "native_prefix":"/opt/jittercrest-deps", "service_user":"jittercrest"}
```

Replace the example addresses and paths. `service_user` must already exist if
installing a systemd service; the output parent directory must also exist.

## Configuration and deployment

Published defaults are generic and use only a same-origin local node. Configure
your own `SPEEDTEST_NODES`, allowed origins, public UDP mapping and TLS proxy.
Never expose a listener trusting arbitrary forwarded identity headers. Use a
non-root service and bounded resources. UI, probe-only and data-agent roles are
described in the [C++ documentation](../README.md); generic service/proxy examples
and packaging tools are in [`cpp/deploy/`](../deploy/).

Browsers stream uploads only over HTTP/2, where the server's receive window
bounds one stream to window/RTT. Reverse proxies with a fixed 1 MiB window (such as
Caddy) therefore cap a 54 ms path near 155 Mbit/s. For speed-test traffic, enable
the built-in TLS listener (`SPEEDTEST_TLS_PORT`, `SPEEDTEST_TLS_CERT`,
`SPEEDTEST_TLS_KEY`), which advertises 32 MiB stream and 64 MiB connection
windows, and use that origin as the node `base`. See the
[TLS HTTP/2 listener notes](tls-edge.md). The quickstart wizard and
command-based enrollment configure it by default.

No credentials, real deployment configuration, binary releases, backups or raw
operational captures belong in this repository. See [security](../../SECURITY.md) and
[third-party notices](../../THIRD_PARTY.md).

## Validation and history

- [Compatibility and safety acceptance](acceptance-audit.md)
- [Measured Rust/C++ performance comparison](performance-findings-recovery.md)
- [Initial authorized production rollout](production-rollout.md)
- [Archive structure and Rust retirement](rust-archive.md)

Historical benchmarks do not imply C++ is universally faster. Rust led the
recorded local bulk-throughput cases; C++ had selective lightweight-request and
high-load-memory advantages. Reports retain their source identities, finite
test scopes and unsuppressed analyzer-model findings.
