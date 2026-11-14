# Security

Only the C++20 backend under `cpp/` is maintained. Implementations under
`archive/rust/` and `archive/node/` are frozen historical references, excluded
from the active build and CI. Do not expose archived services to untrusted
traffic merely because their source is available.

Never commit credentials, `.env` secrets, SSH keys, tokens, full sensitive
headers, deployment snapshots, packet captures, production logs or backups.
Published defaults use generic identifiers/reserved addresses; review each Git
commit and CI log. History cannot revoke previously downloaded secrets.

Use a non-root service, loopback HTTP behind verified TLS, explicit allowed
origins/trusted proxy IPs, bounded peers/requests/bytes/queues and resource limits.
Never trust arbitrary forwarded identities on a public listener. WebRTC requires
validated public UDP/NAT/firewall settings and remote SDP filtering. Private-RTC
flags are for isolated tests only.

Per-client rate, concurrency and byte limits apply to an IPv4 address or to an
IPv6 /64, since one IPv6 client normally controls the whole prefix. Streaming
downloads stop writing at their deadline plus five seconds even when the client
stops reading, which releases their active slot. Remove origins of retired nodes
from every node's `ALLOWED_ORIGINS`.

Keep the pinned C/C++ dependencies and explicit native patches under review.
Repeat the separate ASan+UBSan and TSan suites after affected changes; do not mix
incompatible sanitizers or suppress findings to obtain a green report. Finite
safety evidence and two explicitly triaged Clang 18/Boost coroutine-model
findings are recorded in `cpp/docs/acceptance-audit.md` and
`cpp/docs/coroutine-analysis.md`, not represented as proof of zero risk.

## Node management and enrollment

Management requires an explicitly configured high-entropy administrator token,
exact public HTTPS origin and writable private registry directory. Keep the main
listener on loopback behind trusted TLS. Browser admin calls use a bearer token
held only in page memory; they do not use cookies or persistent browser storage.
Cross-origin administrator writes are rejected. Protect the service environment,
registry directory and backups with operator-only permissions; rotating the admin
token requires a service restart and logging in again.

Enrollment keys contain 256 random bits, expire after one hour and can be claimed
once. Only hashes of enrollment keys and agent credentials are persisted on the
controller. Keep copied commands private: they contain an enrollment key. Agent
credentials reside in mode-0600 installation files on the VPS and authorize only
that node's activation and heartbeat. Reset/removal revokes those credentials.
Disabling a node hides it from the picker while preserving its installation.
Removing a node does not stop containers or prevent access to its public endpoint;
retiring the VPS service also requires stopping its containers.

The generated installer requires root and working external network access. It
builds only the embedded allowlisted agent source, uses strict HTTPS certificate
verification, and refuses occupied TLS ports or unrelated container names. It
does not modify the firewall. For Docker installation it uses the distribution's
apt package on Debian/Ubuntu. Review the command and downloaded installer before
running them on a VPS; the controller is trusted to distribute executable source.
The Docker build context excludes environment files, archives, VCS and operator
records. The C++ runtime runs non-root with a read-only filesystem and no added
capabilities; Caddy manages certificates in its separate data directory. A root
systemd timer copies the issued certificate and key, mode 0400, into a directory
owned by the container user and mounted read-only for the built-in TLS listener.

For vulnerabilities use GitHub private vulnerability reporting where available,
or contact the owner without publicly posting secrets/exploitable operational
information. Review fresh advisories for active dependencies and every archived
component before any deliberate historical rebuild. Original Rust/Node advisory
notes remain in their archives; those notes are not current clean-scan claims.
