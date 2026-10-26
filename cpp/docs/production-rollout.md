# Authorized production rollout and Rust rollback

The original C++20 [development acceptance](acceptance-audit.md) was completed
before fulfilling the user's subsequent conditional merge/production request.
The feature branch was merged to main in `63cd522e6afada0f0ca3bd7b45e9d21c0ced4e45`.
Deployed runtime source remains `019ff35821f974d80ab223e0fed4f19f7b01a6fb`;
intervening commits contain documentation, not a different executable.

## Deployment boundary

- Three independently enabled C++ services: one central UI, one probe-only agent,
  one measurement-data agent. Agents embed/serve no webpage. Central frontend
  bytes and CSP match the retained Rust UI after normalizing only random nonces.
- C++ runs on its own loopback port32132, release directory, journal identifier
  and hardened non-root systemd service. The existing Rust service remains active
  on3010; its PID/start time/executable/unit hashes and release link are unchanged.
- Only the speedtest proxy upstream dials changed. Adapted before/after Caddy JSON
  was compared structurally; unrelated routes/configuration files, proxy process,
  Rust units and historical archives were preserved. The actual live admin config
  matches the validated C++ configuration, not merely a file on disk.
- Existing certificates, public domain/IP/HTTP/HTTPS/8443 entry behavior and
  routing policy were preserved; no production trust-store change or certificate
  bypass was used. Private probe-ID/host/CSP aliases are explicit operational
  environment mappings, not embedded public infrastructure values.
- Previously verified, inactive acceptance extracts were promoted by same-filesystem
  rename, avoiding an extra full copy on the smallest host. All manifest files,
  executable hashes and actual bundled-library maps were checked again. Original
  candidate/recovery archives remain retained; free space stayed above768MiB.

## Actual post-cutover verification

Native Windows headless Chromium, GPU disabled, ordinary certificate validation:

| Role | autoRTT | Fresh-offer/session/direction-ID Restart | 100pps x20s stability | Upload/download density | Network loss up/down |
|---|---|---|---|---|---|
| Central data/UI | passed | passed | passed | 1.0000 / 1.0000 | 0 / 0 |
| Probe-only agent | passed | passed | passed | 0.9990 / 1.0000 | 0 / 0 |
| Data agent | passed | passed | passed | 0.9995 / 1.0000 | 0 / 0 |

The unchanged.99 completeness gate applied to2000 planned samples per direction;
missing scheduled sends are not mislabeled network loss. Both data-capable nodes
also passed10KiB/100KiB/1MiB downloads,1MiB upload and live upload-progress final
byte accounting. Five nonce-correlated **browser wire**404/CORS-preflight denial
proofs verified the probe-only role; a fetch TypeError alone was not accepted as
denial evidence. A separate21-case public HTTP/TLS/CORS/payload check covered the
existing domain/IP/8443 and retained HTTP entry points.

Independent SSH verification checked actual runtime executable/library hashes,
non-root UID, empty capabilities, NoNewPrivileges, seccomp and configured service
resource/hardening limits. The persistent services are enabled and active,
with no automatic restarts during acceptance. A reboot was not performed.

## Backup, rehearsal and completion

Each host actually switched C++ to Rust and back during rollout. The Rust
rollback restored the exact original process/unit/link/configuration snapshot.
Before switching, a local and remote hash-verified archive preserved the exact
Rust executable, release link, original unit/drop-ins and original proxy file.
Archive contents were independently compared with the original binary/unit/config
hashes; byte hashes, including original line endings, are used for drift guards.

A30-minute rollback watchdog protected each uncommitted switch. Only after the
three-node production browser, policy and runtime checks passed were persistent
C++ services enabled and committed markers written. All watchdog timers were
then stopped and independently confirmed inactive. Manual guarded Rust rollback
remains available in the operator's private deployment record; it refuses to
overwrite an externally changed proxy configuration.

Operational records, raw captures, real addresses, environment values, backup
archives and deployment scripts are deliberately **not** in the public repository.
This rollout does not change the [performance recommendation](performance-findings-recovery.md):
Rust remains the better measured bulk-throughput/CPU/maintenance choice; C++ is
not claimed universally faster or safer. Finite production acceptance is not a
multi-day uptime or exhaustive Internet-path guarantee.
