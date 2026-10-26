# Independent Linux release bundles

`deploy/package_release.py` packages a **trusted local build**; it does not
install, upload or switch a service. Final portability/deployment/rollback
acceptance remains separate. Build host requirements are Linux/glibc, Python
3.9+, `ldd`, `readelf`, Git and Debian/Ubuntu package copyright metadata.
Use `ldd` only on executables you built and trust. The running backend itself
does **not** invoke Python, Node, Rust or build/package tools.

```sh
python3 cpp/deploy/package_release.py \
  --build /local/build-release \
  --native-source /local/deps/src/libdatachannel --license LICENSE \
  --output /local/artifacts/speedtest-cpp-ui \
  --archive /local/artifacts/speedtest-cpp-ui.tar.gz
```

Build the no-UI agent separately (`SPEEDTEST_WEB_UI=OFF`) and package it into a
different new output. Debug/sanitizer CMake profiles and sanitizer symbols in
the executable/libraries, including static runtimes, are rejected. Native
revision/submodules must match the pin. Existing directories/archives are not
overwritten. A failed incomplete artifact is retained for diagnosis, never
silently installed as a current release.

## Contents and runtime boundary

- `speedtest-cpp`: executable shell launcher, sets a **process-local** library
  path to the release's `lib/` and execs `speedtest-cpp.bin`. It does not implement
  protocols or wrap a Rust/Node service.
- `lib/`: exact non-glibc linked libraries, including native transport, copied
  under the required SONAMEs. A relocation check rejects dependencies still
  resolved from the developer's prefix.
- `manifest.json`: file hashes, architecture, minimum required GLIBC symbol
  version, compiler/Release flags, LTO/UI flags, native/submodule pins and OS
  prerequisites. No raw CMake cache, real configuration or credentials.
- `SHA256SUMS`: all other file checksums, including the manifest.
- `licenses/`, pinned native upstream archives and `native-patches/`: notices
  and dependency source changes. Submodule archives are separate because a
  parent Git archive does not expand submodules.
- `templates/`: generic staging examples, not installed/active configuration.

glibc/loader, NSS/resolver configuration, CA certificates and the kernel remain
host dependencies. Minimum glibc is computed from the executable **and** all
bundled libraries' required symbols. A version/architecture match is not proof
of startup, DNS/HTTPS lookup or protocol portability. Updating a bundled library
requires a new tested release, not modification of an immutable directory.

Native sources/patches and distribution notices accompany the binary. Before
public binary redistribution, separately review distribution libraries' license
and source-provision obligations; this self-hosting/test artifact is not a claim
that unrestricted public binary redistribution has been legally audited.

Archives use sorted entries, zero uid/gid/mtime and zero gzip timestamp/filename.
The **same input files** produce the same archive; this is deterministic
packaging, not cross-toolchain bit-for-bit compiler reproducibility. The fast
`tests/release_package_test.py` guards cover unresolved/non-native libraries,
static sanitizer detection, numeric GLIBC versions, Debug/sanitizer refusal,
archive containment, deterministic packaging and no-overwrite safety.

## Verification before installation

Extract into a **new isolated directory**, never over an existing release:

```sh
cd /isolated/extracted-release
sha256sum -c SHA256SUMS
SPEEDTEST_HOST=127.0.0.1 PORT=32132 ./speedtest-cpp
```

Set test-only origins/node configuration explicitly. Verify `/proc/<pid>/maps`
loads native libraries from the extracted release, then run shared HTTP,
browser RTC/resource and shutdown contracts against the launcher. Verify the
agent separately; do not set a developer library prefix to hide a broken bundle.

Keep the **whole previous release directory** for rollback, not just its binary.
Only update the independently owned current link and restart its own unit after
validation. Rust stays production/rollback until conditional full acceptance,
merge and production deployment gates pass.

## Measured packaging verification — 2026-10-26

Fresh public-source Release/ThinLTO UI and no-UI builds both passed eight CTests.
Both generated bundles were extracted into unrelated temporary directories;
checksums passed and all 33 bundled shared libraries were observed loaded from
the extracted release, not a developer prefix. The measured minimum glibc is
2.38 on x86_64. The UI passed actual paired frontend/Restart RTT, and the agent
returned 404 for root/query-root/icon. Both passed 10 KiB/100 KiB/1 MiB
upload/download, both upload-progress modes and a real native limit-close with
replacement ACK. The packaged agent additionally passed 126 paired HTTP cases.

Eight packaging unit tests passed. Two independently generated UI archives from
the same inputs had identical SHA-256 values. This is local relocation and
deterministic packaging evidence, not target-VPS portability, effective systemd
hardening, Internet performance or production/rollback acceptance.
