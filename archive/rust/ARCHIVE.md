# Frozen Rust reference

Archived from source commit `b8e7ae25c94abf12b6584d9b276c4c0192083b32`.
This is no longer an actively maintained production backend. The original
README, source, Cargo lockfile, vendored dependency, frontend, tests, examples,
deployment templates and license/security notices are retained. The old workflow
is nested here for history and is not discovered by GitHub Actions.

`SOURCE-MANIFEST.json` records exact original/archive paths and SHA256s for the
preserved reference files. `public/` is a frozen copy: later changes to the
active root frontend must not silently change the historical Rust reference.
The independent optional `oracle/` was moved from `cpp/tests/rust-oracle/`; only
its module path adapters changed so it still reads the archived pure Rust source.

Explicit historical builds (not default build or CI):

```sh
cd archive/rust
cargo build --release --locked
cargo test --locked
# Optional protocol/statistics comparison tool:
cargo build --release --locked --manifest-path oracle/Cargo.toml
```

Rust1.96+ is required for that optional historical build. The active C++ server
does not invoke or link it. Old dependencies may acquire new advisories; archive
status is not a security update or permission to expose an old service publicly.
Secrets, installed dependencies, binaries and production recovery backups are
not included. Server-side Rust rollback removal is a separate authorized action;
this source archive is not a retained server binary or instant rollback path.
