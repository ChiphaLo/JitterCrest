# Third-party notices

The local `vendor/webrtc-sctp-0.17.1` crate derives from webrtc-rs and remains
dual-licensed under its upstream MIT OR Apache-2.0 terms. Original copyright
notices and both license files are retained in that directory.

Service-specific changes cover bundled FORWARD-TSN parsing and compatibility with
the sender (fast-recovery state, T3 recovery reset and restartable miss indicators). Zero-retry DATA now follows
the negotiated budget: the old reliable-zero compatibility behavior was removed.
These are not represented as a general-purpose SCTP standards update. Most deterministic tests pass, but two
upstream vendor tests require external fuzz-artifact files not present in this
source snapshot; the normal workspace unit suite excludes that vendor crate.

Rust dependencies remain governed by their respective licenses. `Cargo.lock`
records exact versions. The old Node/Werift reference and its npm dependencies
are no longer part of this active source distribution. Any separately retained
historical recovery archive must keep its original third-party notices.
