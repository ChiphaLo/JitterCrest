<!-- JitterCrest contributor and repository guidance. -->

# Repository updates

- After a completed code or documentation update, run the relevant checks, create a Git commit, and push it to the configured GitHub remote. Read-only reviews do not require empty commits.
- Append this trailer to commits:

  `Co-authored-by: Codex <codex@openai.com>`

- Verify the remote commit after pushing. Report blocked checks or failed pushes instead of claiming completion. Do not force-push or overwrite unrelated work.
- Keep this public edition generic: never commit credentials, live infrastructure configuration, raw operational captures, recovery backups, or private deployment scripts.
- Publishing source does not authorize a production deployment.

## Maintained implementation

- Only the C++20 backend in `cpp/` and the active `public/` frontend are maintained.
  Root CMake/CI must not require Rust or Node. Rust/Node implementations are frozen
  under `archive/`; historical comparisons require explicit invocation.
- Rust production rollback services/releases/backups were explicitly retired.
  Do not recreate or deploy them without a new user request. Older rollout reports
  are historical; use `cpp/docs/rust-archive.md` for the current policy.
