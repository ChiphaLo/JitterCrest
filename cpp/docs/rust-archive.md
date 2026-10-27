# Rust source archive and C++-only maintenance

The user requested that Rust become a frozen archive and that only C++ remain
maintained. `archive/rust/` preserves the original sanitized Rust source, Cargo
lockfile, vendored SCTP dependency, original frontend, tests, examples, deployment
templates, notices and original workflow. Its `SOURCE-MANIFEST.json` records
106 exact original/reference file SHA256s. The optional test-only Rust oracle
was moved there too, with explicit relative module-path adjustments.

The active root now has a CMake entry, C++ README/configuration and C++-only
GitHub Actions workflow. Neither CMake nor default CI runs the Rust or Node
archives. All Rust source files are under `archive/`; the active frontend and
C++ protocol/runtime source are unchanged. Optional historical comparisons still
accept an explicitly supplied archived Rust executable; they are not a runtime
or default-build dependency.

The earlier production-rollout and acceptance reports are historical evidence
for their dates. Their recommendation to retain Rust rollback was subsequently
overridden by the user's explicit request to remove server-side Rust services,
releases and backups **after** verifying the GitHub source archive. Server
retirement subsequently completed on all three hosts, with independent checks
and a fresh production-browser profile passing.
This source archive is not a retained production binary or an instant rollback.
Local private historical evidence is outside the public repository and is not
implicitly deleted by a server-side cleanup request. Historical Node archives
and unrelated services remain out of scope.

## Completed server-side retirement

The source archive was pushed to main and its remote commit verified before
any destructive server cleanup. Then, on all three hosts:

- Stopped/disabled and removed the old Rust unit and drop-ins; its load state
  is now `not-found`, with no listener on3010.
- Removed Rust release directories/current links/state, Rust reference binary
  copies from inactive acceptance stages, old Rust deployment rollback materials,
  and the Rust backup archives/scripts from the initial C++ rollout.
- Preserved the active C++ unit/release/environment, proxy process/live routes,
  configuration hashes and historical Node recovery packages. The parent holding
  the current C++ environment was **not** removed with its obsolete Rust backup.
- Used exact path/fingerprint manifests, canonical path checks and live-process
  use checks rather than broad name-based recursive deletion. Separate fresh SSH
  verification found no remaining scoped Rust retirement targets.

| Host role | Removed file bytes, MiB |
|---|---:|
| Central UI/data | 424.02 |
| Probe-only agent | 350.00 |
| Data agent | 351.64 |

These are summed removed file lengths, not free-space deltas or guaranteed
physical-block reclamation; unrelated disk activity is not credited to cleanup.

After deletion, the actual production three-node browser profile passed3/3:
autoRTT, genuinely new offer/session/direction IDs on Restart,100pps x20s stability
with the unchanged.99 gate and payload/progress checks on data-capable nodes.
Twenty-one public HTTP/TLS/CORS/payload/denial checks and five nonce-correlated
browser-wire probe denials also passed. Normal certificate verification and GPU
disabled headless mode were retained. C++ and proxy process identities did not
change; no rebuild deployment, route modification or reboot was necessary.

**There is no longer a server-side Rust rollback service/binary/backup.** Returning
to Rust would require an explicit historical rebuild and new deployment from the
archive, not running the now-removed rollback commands in the old rollout report.
Private local historical records/backups were not deleted by this server-only
request. The active maintained source/build/CI remains C++ only.

## Archive validation scope

Both root CMake Release builds (UI/agent) passed8 CTests each;27 Python tooling
tests passed. All106 archived reference file hashes matched both working files
and staged/published Git content. Cargo metadata and the relocated optional GNU
Rust oracle rebuild/three arithmetic smokes passed. The whole archived Rust
server was not rebuilt in this archival change. C++ runtime/frontend sources were
unchanged. Archived whitespace and original historical reports were preserved
rather than cosmetically rewriting the frozen source. Hosted GitHub Actions is
separate from these completed local checks; inspect the current run for status.
