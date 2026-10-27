# Archived implementations

- [`rust/`](rust/ARCHIVE.md): frozen Rust server, Cargo lockfile, vendored SCTP,
  original frontend/tests/templates and optional reference oracle.
- [`node/`](node/): older sanitized Node server and historical compatibility tools.

Only C++20 under `cpp/` is maintained. The root CMake build and GitHub Actions
workflow do not compile, test or run these archives. Historical builds require
explicit commands from inside the relevant archive and are not deployment advice.

No credentials, real infrastructure configuration, installed dependencies,
binaries or operational recovery backups belong here. Existing Git history is
retained without rewriting or force pushing.
