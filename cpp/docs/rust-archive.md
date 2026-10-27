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
retirement remains pending until the independent cleanup/production checks pass.
This source archive is not a retained production binary or an instant rollback.
Local private historical evidence is outside the public repository and is not
implicitly deleted by a server-side cleanup request. Historical Node archives
and unrelated services remain out of scope.
