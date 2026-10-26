# Independent staging layout (template, not a deployment)

**Subsequent evidence:** guarded temporary units on three actual VPS hosts have
now been exercised without changing the production Rust/Caddy units or routes.
See `public-staging-validation.md` for actual checks, staged backout, preserved
failures and unresolved network observations. The generic permanent unit below
was not installed as a production service; the earlier local-only statements
below describe the template milestone, not an absence of later staging tests.

Production stays on Rust. Neither the templates nor local tests install a unit,
change a production Caddy configuration, or switch traffic. The C++ HTTP binary
is an intermediate build: native WebRTC is implemented but its complete safety,
deployment and acceptance gates are still pending. Keep the RTC flag off in
staging templates until those gates pass; see `rtc-validation.md`.

| Resource | Independent C++ staging value |
|---|---|
| Account | `speedtest-cpp` |
| Release directories | `/opt/speedtest-cpp-staging/releases/<release-id>/` |
| Current link | `/opt/speedtest-cpp-staging/current` |
| Executable | `current/speedtest-cpp` |
| Environment | `/etc/speedtest-cpp-staging.env` |
| Service | `speedtest-cpp-staging.service` |
| HTTP listener | `127.0.0.1:32132` |
| Local reverse proxy | `http://127.0.0.1:32133`, separate Caddy instance |
| Logs | Journal identifier `speedtest-cpp-staging`; separate proxy stdout |

These names deliberately do not overlap the Rust service, release directory,
environment file or listener. Both implementations can run simultaneously.
The local proxy template does not request an ACME certificate or bind a public
interface. An HTTPS example is commented out and uses a reserved domain.

For a later authorized staging installation: build and test locally, record the
binary hash/toolchain/dependency manifest, put the executable and its native
runtime dependencies in a new immutable staging release directory, and verify
that account ownership permits reads but no writes to the release. Create the
separate account/environment/unit, validate the unit and Caddy configuration on
the destination OS, then run the same direct and proxy HTTP/browser tests.
Do not copy a sanitizer binary into a performance or deployment comparison.

Retain the previous C++ release and update only its own `current` link atomically.
Rollback changes that link back and restarts only `speedtest-cpp-staging`.
Stopping/disabling this test service and its separate proxy must leave Rust
running unchanged. Never alter existing Rust/Node backups to make a C++ release.

The systemd template preserves the existing hardening approach: non-root user,
read-only system/home protections, empty capabilities, bounded tasks/memory,
restricted address families, journal logging and a ten-second stop limit above
the five-second HTTP drain grace. Memory ceilings are initial containment, not
measured capacity claims. WebRTC UDP ports/candidate mapping must be
validated separately before any public staging session is offered.

Template syntax is reviewable source, not proof of effective hardening, TLS,
rollback, firewall or live service health. No production deployment or online
switch has been performed as part of the C++ work.

## Complete temporary-copy admission

Before uploading or creating a temporary stage, measure and add **all** of:
compressed candidate archive, expanded files, copied Rust reference, copied
proxy binary, metadata/filesystem-growth margin and retained free-space reserve.
An `expanded * 2` estimate is not sufficient: compression, reference and proxy
sizes are independent. Existing production storage is not part of the budget
available for these copies.

`deploy/staging_capacity.py` is a pure, immutable byte-accounting helper; it
does not connect, copy, remove, install or change services. Inputs must be
nonnegative integer counts, not booleans/floats/NaN. It refuses to lower the
16 MiB metadata margin or768 MiB retained reserve. Copy costs are added to that
reserve rather than paid out of it. For example70+104+13+50 MiB in copies needs
1021 MiB free with the default margin/reserve; the older estimate could admit
less than that. Recheck actual free space after copies, allowing for remaining
metadata, and after preparing a stage. Concurrent external disk growth can
still require aborting, not spending the reserve.

```sh
python3 cpp/tests/test_staging_capacity.py
```

Eight unit tests, including invalid inputs and exact admission boundaries,
passed on Windows and Linux. A separate **read-only** exercise of the actual
private staging admission method found approximately907 MiB free versus1020 MiB
required for the measured agent footprint and rejected admission. It created
no remote directory, upload, unit or listener; the production snapshot was
unchanged. The source archive used for this size calculation was an older
boundary candidate, not a claimed deployment of the newer RTC correction.
No reserve was lowered and no remote cleanup was performed by that exercise.

Subsequently, two exact obsolete non-live acceptance extracts were retired only
after full archive/file/mode and live-use verification, preserving their recovery
archives, logs and reference/proxy copies. The cache policy for those two obsolete
versions explicitly changed to archive-backed recovery rather than keeping a
resident extraction per hash. The newer three-host run then verified its complete
budgets and retained reserve after copying/preparing. See
[current release validation](recovery-release-validation.md); production files
and unrelated services were not removed or changed.

Local validation: `systemd-analyze verify --man=no` returned success for a scratch
copy with only executable/working-directory paths changed to the local build.
The source template was not installed. A local extracted Ubuntu Caddy 2.6.2
binary (no package installation or system service) successfully validated the
Caddyfile with its Caddyfile adapter. `tests/proxy_tls_contract.py` subsequently
passed shared Rust/C++ live HTTP and HTTPS contracts using this template on
isolated loopback ports: 10 KiB/100 KiB/1 MiB downloads, 1 MiB upload, exact CORS,
NDJSON received before upload starts and final server byte accounting. The same
unchanged frontend completed a paired RTC test and the real Restart/RTT rendering
path through HTTPS signaling. Python verifies certificate trust and hostname;
Chromium pins only the ephemeral certificate's public key. No certificate is
installed into the system trust store. Caddy config/data/certificates are confined
to the test temporary directory and all child services stop afterward.

This validates a local direct TLS/proxy UI profile, not a public deployment,
production firewall, rollback or effective systemd hardening. A subsequent local
central-UI-to-separate-agent **cross-site HTTPS IP** matrix passed all four
Rust/C++ UI/agent combinations in Release, ASan/UBSan and TSan profiles; see
`agent-resource-validation.md`. Successful publicly mapped probe-only
connectivity and real public deployment still require separate evidence.
