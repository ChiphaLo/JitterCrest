# Documentation index

[Back to README](../../README.md) · [C++ backend](../README.md)

## Current guides

- [Build, deployment and node management](deployment.md) · [中文](deployment.zh-CN.md)
- [Built-in TLS HTTP/2 listener](tls-edge.md)
- [Release bundles](release-bundle.md)
- [Rust archive and C++-only maintenance](rust-archive.md)
- [Coroutine analyzer findings](coroutine-analysis.md)

## Historical records

These reports were written during the October 2026 Rust-to-C++ migration. Each
describes the source commit, environment and policy of its own date; later
changes are not backfilled. Rust has since been archived, C++ is the only
maintained backend, and speed traffic now uses the built-in TLS listener with
the receiver-side measurement window described in the root README.

Validation and acceptance:

- [Objective acceptance audit](acceptance-audit.md)
- [Core milestone](core-validation.md) · [HTTP milestone](http-validation.md)
- [Native RTC milestone](rtc-validation.md) · [RTC resource limits](rtc-resource-validation.md)
- [Independent-agent TLS and resources](agent-resource-validation.md)
- [Boundary-corrected candidates](corrected-candidate-builds.md)
- [Configuration and native transport follow-up](transport-compatibility.md)
- [RTC admission and recovery](reference-admission-recovery.md)
- [OpenSSL receive fairness](receive-fairness.md) · [SCTP queue diagnostic](send-queue-diagnostic.md)
- [Recovery-source packages and roles](recovery-release-validation.md)
- [Guarded public staging](public-staging-validation.md) · [Staging template](staging.md)

Performance:

- Local: [first run](local-performance.md) · [receive-fairness snapshot](local-performance-current.md) · [recovery source](local-performance-recovery.md) · [analysis](performance-analysis.md)
- Internet: [first run](network-performance.md) · [receive-fairness snapshot](network-performance-fairness.md) · [recovery source](network-performance-recovery.md)
- Findings: [receive-fairness snapshot](current-performance-findings.md) · [recovery source](performance-findings-recovery.md)

Rollout:

- [Initial authorized production rollout](production-rollout.md)
