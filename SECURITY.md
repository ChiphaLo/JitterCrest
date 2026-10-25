# Security

Never commit `.env` files, SSH passwords/keys, access tokens, deployment snapshots,
full sensitive headers, packet captures or production logs. Keep credentials
outside the repository and pass only the configuration needed by the service.

The published source uses generic node identifiers and reserved documentation
addresses, with a same-origin local default. Keep real deployment configurations
private. An anonymized source tree is not a substitute for reviewing every Git
commit and CI log before publication. History rewriting cannot revoke previously
downloaded copies or guarantee removal from hosting caches and forks.

Use an unprivileged service, loopback HTTP behind a correctly configured reverse
proxy, normal TLS verification, bounded resource limits and a known-good rollback.
Trust proxy headers only from explicitly trusted proxy IPs. Never expose a
public listener while trusting arbitrary forwarded identity headers.

WebRTC uses direct UDP. Validate public address/NAT/firewall configuration and
SDP filtering. The private-RTC test flag is for isolated tests only.

For suspected vulnerabilities, use GitHub private vulnerability reporting when
available or contact the repository owner without posting secrets or exploitable
production details in a public issue. Run `cargo audit` against a fresh advisory
database. A clean scan is not a security guarantee; yanked-version warnings and
other dependency lifecycle changes should also be reviewed.

## Archived Node reference

The old Node/Werift A-B source lives only under `archive/node/`, outside the active
Rust build and CI. Installed npm dependencies are not committed; private recovery
ZIPs retain them. This baseline must not be exposed to untrusted production traffic. At its original
publication, `npm audit` reported three high-severity dependency entries (`ip`,
`werift-ice`, `werift`) originating from `ip`'s improper public-address
classification advisory. Archiving this baseline does not fix those dependencies;
use it only in isolated compatibility/recovery tests.

The Rust lockfile updates rustls to 0.23.46, fixing RUSTSEC-2026-0346 through
RUSTSEC-2026-0349. A `yoke-derive 0.8.3` yanked-version warning remains a dependency
lifecycle item, not a clean bill of security or an ignored vulnerability report.
