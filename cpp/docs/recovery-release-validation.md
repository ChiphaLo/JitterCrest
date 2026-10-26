# Current recovery-source packages and public role validation

This is **new** artifact and public-network evidence for runtime source commit
`019ff35821f974d80ab223e0fed4f19f7b01a6fb` (`src/rtc.cpp` SHA256
`2db73f7be25ac03e42ef3ffb442c4d2616608286f2e7c491d1f095fee499c67a`). It includes both the native message boundaries and
the [reference RTO/admission correction](reference-admission-recovery.md).
Older fairness/boundary hashes and their successes are not relabeled as this run.
It is still not a production cutover or the final objective completion audit.

## Independent artifacts

| Role | Local candidate archive | SHA256 |
|---|---|---|
| ui | `speedtest-cpp-ui-recovery-candidate.tar.gz` | `adedf954e3651a210e4251ec959c38a385309e04d9e4bc2d26788444e7b26bb5` |
| generic-ui | `speedtest-cpp-generic-ui-recovery-candidate.tar.gz` | `425418627c238d3cbfd25864502d58c7829b416cb10c157f817e1d704d2a283b` |
| agent | `speedtest-cpp-agent-recovery-candidate.tar.gz` | `3e4b5bc07c72da27c9f3480d06458ae0fe31d07406779f3e578425734b53aed4` |

Each role was built independently from public CMake source, Clang18 Release /
ThinLTO and the pinned/patched libdatachannel0.24.6. The minimum bundled glibc
requirement is2.38. Preserved UI uses the unchanged deployment frontend; generic
UI uses public generic assets; the agent embeds no webpage. Archives and raw
operational records remain local/private, not in this Git source update.

All three passed CTest8, relocated manifest/SHA256SUMS/executable identity and
actual library-map checks, fixed10KiB/100KiB/1MiB uploads/downloads and both
upload-progress modes. UI and agent each passed126 paired HTTP contracts;
generic UI passed8 visibility cases and preserved UI16 paired visibility cases.
Both normal webpages completed autoRTT and a new-offer-correlated Restart.

Each relocated artifact additionally passed paired native65535/65536/65537
message boundaries and four paired recovery cases against its corresponding
unchanged Rust UI/agent reference. Recovery checks retain the original25-second
result window and require a truthful incomplete result under the declared
6–20s upstream outage, not a healthy completed stability measurement. Neither
probe reliability, application deadlines nor .99 gates were weakened.

## Fresh public role profile

One owned host served only the webpage/control entry, a second was probe-only,
and a third was a no-UI data agent. The native Windows client used real public
IP HTTPS and UDP, existing routing policy, headless Chromium and GPU disabled.
Temporary origins used scoped bearer authentication and freshly generated test
certificates trusted only by explicit SPKI pins, not a global certificate bypass.
This does not claim new production-domain certificates or provider DNS validation.

| UI backend / agent backend | Browser version | Node cases | Exact probe denial proofs |
|---|---|---:|---:|
| cpp-cpp | 151.0.7922.34 | 2/2 | 5 |
| rust-rust | 151.0.7922.34 | 2/2 | 5 |
| rust-cpp | 151.0.7922.34 | 2/2 | 5 |
| cpp-rust | 151.0.7922.34 | 2/2 | 5 |

All8 cases passed autoRTT, genuine Restart and100pps×20s stability with the
unchanged .99 gate and2000 planned samples per direction. Restart had to produce
a **new offer, session ID and both direction test IDs** before the new completed
result was accepted. A stale completed phase was insufficient. The frontend and
its validation logic were not patched; an observer recorded native connection,
channel/count and selected UDP-pair metadata without changing their operations.

The data-agent cases passed actual payload/progress requests. Probe-only cases
each had five nonce-correlated browser/proxy denial proofs,20 total, including
failed-payload paths rather than merely absent traffic. Both agent roots remained
404: backend hosts did not serve the central webpage. All selected transport
pairs observed by the metadata recorder were UDP; no recorder overflow occurred.

Before/after process/binary/unit/Caddy-config/release-link snapshots matched on
all three production hosts. A separate post-cleanup SSH audit confirmed closed
test listeners, absent temporary unit files and ephemeral key/config files, and
the exact run's unchanged production snapshots. No Rust unit, production route,
release link, source, archive or rollback was changed by this staging profile.

## Temporary storage boundary

The full copy budget included archive, expansion, Rust reference, proxy and
metadata in addition to the768MiB retained reserve. Actual archive and C++
executable hashes matched the local candidate on every remote host, and native
libraries mapped from the candidate release directory. The smallest target
needed about1020.51MiB before staging and retained about879.17MiB after prepare.

To obtain space, exactly two old **non-live acceptance extracts** were retired
only after their files/modes matched retained archives and no process map, open
file or working directory used them. Their compressed recovery archives, logs,
Rust references and proxy copies were preserved and hashes rechecked. Available
space rose from about907 to1115MiB. This explicitly changed the cache policy for
those two obsolete versions to archive-backed recovery; it did not pretend that
the older keep-a-full-extraction-per-hash invariant still held. No production
or unrelated file was removed, and the reserve was never lowered.

## Remaining scope

This finite profile establishes the current source's normal public role path,
not universal Internet behavior. Earlier loss/late-ACK observations remain
versioned; shared Rust baseline limitations are not silently repaired. Current
source performance revalidation and the final original-requirement audit remain
pending before the user's conditional branch merge/production deployment.
Rust production continues unchanged; no main merge or cutover has occurred.
