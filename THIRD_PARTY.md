# Third-party notices

The local `vendor/webrtc-sctp-0.17.1` crate derives from webrtc-rs and remains
dual-licensed under its upstream MIT OR Apache-2.0 terms. Original copyright
notices and both license files are retained in that directory.

Service-specific changes cover bundled FORWARD-TSN parsing and compatibility with
the preserved Node/Werift sender (zero-Rexmit handling, fast-recovery state,
T3 recovery reset and restartable miss indicators). These are not represented as
a general-purpose SCTP standards update. Most deterministic tests pass, but two
upstream vendor tests require external fuzz-artifact files not present in this
source snapshot; the normal workspace unit suite excludes that vendor crate.

Rust dependencies remain governed by their respective licenses. `Cargo.lock`
records exact versions. `werift` and its npm dependencies are used for the Node
reference/A-B clients and are not vendored here. Retain their notices when
redistributing installed dependencies or a bundled application.
