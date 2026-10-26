# Boundary-corrected candidates — local validation

**Version scope:** these hashes predate the later
[RTC admission/recovery correction](reference-admission-recovery.md).
They must not be deployed or described as packages of that newer source.

Three independent Linux x86_64 candidates were built from public source commit
`400078fd3bf3d0ceab5da4816eae57a9d0d5b8e6`, not by renaming older archives.
That commit's runtime code includes the negotiated64KiB / native65535-byte
read-boundary correction. It does **not** include the rejected64KiB send-buffer
experiment. The [severe-outage result delay](send-queue-diagnostic.md) remains
unresolved; these are **not production-ready releases**.

## Artifact identities

Each role had its own fresh CMake/Ninja build, package directory and archive.
Builds used Clang18, Release, comparable ThinLTO, the existing pinned/patched
libdatachannel0.24.6 and its source/license material. Rust and frontend sources
were unchanged. The minimum glibc required by each bundled dependency set is
**2.38**; the OS loader, resolver/NSS, CA store and kernel remain prerequisites.
No aarch64 build/run is claimed.

| Role / archive | SHA256 |
|---|---|
| Preserved UI: `speedtest-cpp-ui-boundary-candidate.tar.gz` | `5d58588fc1de7de3349241559b426f47b0a73c6f882df22912560559dee4c3da` |
| Generic UI: `speedtest-cpp-generic-ui-boundary-candidate.tar.gz` | `4d51383d89239c6200ebb453f13df13fb59c7b31b988f5dbc0b40e886a66304f` |
| No-UI agent: `speedtest-cpp-agent-boundary-candidate.tar.gz` | `fdff773d6041390dc2f677a17731f7de569468410d33bc7e2e89b31ef26fad00` |

The preserved UI uses the existing deployment frontend; the generic UI uses
the public generic frontend; the agent embeds no webpage. Archives are retained
locally, not committed to GitHub. Operational configuration, raw diagnostics
and backups are not part of the public source update. Archive sizes are about
70 MiB compressed and104–105 MiB expanded each, excluding staging copies of a
Rust reference, proxy, additional metadata and the required free-space reserve.

## Verified scope

- **All three builds:** eight CTests each. Preserved UI and agent additionally
  passed126 actual-process paired HTTP contracts against the corresponding
  unchanged Rust UI/agent reference. Generic UI had eight actual configuration
  visibility cases; the preserved UI had sixteen paired visibility cases.
  A full generic-vs-private-UI HTML differential is not claimed.
- **All three relocated archives:** extract into unrelated temporary paths,
  verify every manifest/SHA256SUMS entry and original executable identity,
  launch the packaged wrapper, and inspect actual process mappings. Every
  bundled library loaded from that relocated directory, not a development
  prefix. Fixed10KiB/100KiB/1MiB download/upload and both upload-progress modes
  passed there. Root HTML is200 for UI roles and404 for the no-UI agent.
- **All three relocated native transports:** paired real browser tests against
  the matching Rust reference verified negotiated65536, valid65535 followed by
  a successful20pps×2s measurement, close65536/tombstone409, and browser
  rejection of65537 before transmission. Both fixtures explicitly permit the
  legal65536-byte application-control ceiling for this boundary test; the
  normal16384-byte application policy is unchanged.
- **Both normal relocated webpages:** autoRTT and Restart completed without
  frontend test hooks, route mocks or test-mode query parameters. A separate
  strict check correlated a **new offer/attempt and200 response after Restart**,
  observed the running phase, then waited for a new RTT completion. An old
  completed phase alone was not sufficient. Browser page errors were absent;
  Chromium ran headless with GPU disabled.
- Runtime files and protected source/reference hashes remained unchanged.
  Temporary extractions, server and browser processes were cleaned up.

These are local build/relocation/normal-UI results, not a rerun of every earlier
sanitizer suite, fair benchmark or public multi-host role profile. The old
fairness bundles and their public8/8 results retain their own hashes. A current
corrected-source public role matrix, the adverse-path decision and final
objective audit remain pending. No main merge, remote deployment, production
route/service change or Rust rollback modification was made by this work.
