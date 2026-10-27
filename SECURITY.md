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

Keep the pinned C/C++ dependencies and explicit native patches under review.
Repeat the separate ASan+UBSan and TSan suites after affected changes; do not mix
incompatible sanitizers or suppress findings to obtain a green report. Current
finite safety evidence and two explicitly triaged Clang18/Boost coroutine-model
findings are recorded in `cpp/docs/acceptance-audit.md` and
`cpp/docs/coroutine-analysis.md`, not represented as proof of zero risk.

For vulnerabilities use GitHub private vulnerability reporting where available,
or contact the owner without publicly posting secrets/exploitable operational
information. Review fresh advisories for active dependencies and every archived
component before any deliberate historical rebuild. Original Rust/Node advisory
notes remain in their archives; those notes are not current clean-scan claims.
