# Third-party notices

## Active C++20 implementation

The backend uses Boost (BSL-1.0), nlohmann-json (MIT), OpenSSL (Apache-2.0),
libcurl (curl license), libdatachannel (MPL-2.0), libjuice (MPL-2.0), usrsctp
(BSD-3-Clause) and plog (MIT). Keep upstream copyright/license notices when
redistributing dependencies or bundled binaries. Python/CMake are build/test
utilities, not server runtime subprocesses.

`cpp/cmake/bootstrap-datachannel.sh` pins the native transport source and its
submodules and applies explicit versioned patches from `cpp/cmake/patches/`.
CMake creates a reviewed Boost header overlay without editing system headers.
Source modifications, dependency versions and distribution obligations are
explained in `cpp/README.md`; binary packaging retains dependency notices.

## Historical references

Rust's original notices remain in [`archive/rust/THIRD_PARTY.md`](archive/rust/THIRD_PARTY.md).
Its vendored `archive/rust/vendor/webrtc-sctp-0.17.1/` retains MIT/Apache-2.0
licenses and original copyright. The archived Cargo lockfile records the frozen
Rust dependency set. Optional comparison tooling is also under `archive/rust/`.

The old Node source remains under `archive/node/` with its original lockfile.
No npm installations, binary recovery archives or operational backups are
published. Archiving does not remove upstream licensing or security obligations.
