# Post-boundary-fix SCTP queue diagnostic — unresolved

This finite local diagnostic tests the message-boundary correction at
`de82799d44d328302daab5b6fc4b77330efb58ae`. It is **not** frontend acceptance,
an Internet benchmark, a performance ranking, or permission to deploy.
The small-buffer experiment below was **rejected, not merged**.

**Subsequent evidence:** the [coupled reference-admission/recovery correction](reference-admission-recovery.md)
uses the reference RTO profile as well as bounded native admission. It passed
new controlled comparisons and the affected safety matrix. This does not turn
the small-buffer-only failure below into a pass or validate older packages.

## Fixed uninstrumented comparison

A fresh run of the corrected raw C++ executable reproduced the severe-outage
delay: approximately 30.07 s for a 20 s measurement, versus an incomplete Rust
result at 23.86 s. The preceding message-cap/read-boundary fixes did not solve
this separate issue. The unchanged frontend allows 25 s for this sequence; a
longer-waiting raw-wire client cannot turn a late result into a frontend pass.

The reference `webrtc-sctp` production pending queue has a 128 KiB semaphore
budget; its much larger `cfg(test)` constant is not the production setting.
libdatachannel defaults to a 1 MiB native send buffer. C++ separately bounds its
per-channel application queue at 64 KiB with a two-second wait. The native
`bufferedAmount()` does not include all bytes admitted into usrsctp. These are
different layers, not interchangeable measurements of one queue.

A private source copy varied **only** `SctpSettings.sendBufferSize = 65536`.
Its rationale was 64 KiB native plus the existing 64 KiB queue threshold, **not**
exact equivalence to Rust's shared pending budget. Canonical C++/Rust/frontend
sources and the native library were unchanged. Both C++ builds used matching
Clang Release, LTO **off**, the same patched native library, preserved frontend
and two I/O threads. This is not the ThinLTO benchmark snapshot elsewhere.
The experimental build passed eight CTests; no sanitizer approval is claimed.

Before traffic, the schedule was fixed: four policies, each in baseline-C++ /
candidate-C++ / Rust order, then the severe policy in candidate / baseline /
Rust order. No build or other test overlapped collection. All 15 cases, input
hashes, selected real candidate pairs, failures and cleanup were retained and
independently audited. Tests ran only on owned loopback traffic, in headless
Chromium with GPU disabled.

The relay carried real encrypted ICE/DTLS/SCTP with 60 ms one-way delay. Loss
policies were every 137th application datagram in each direction; application
datagrams 150–152 in each direction; and an upstream application-datagram
blackout at `6 <= elapsed < 20` seconds. Policies were matched, **not** packets.
Each case used the unchanged 100 pps × 20 s wire measurement and fragmented
result protocol.

| Policy / round | Current C++ arrival | 64 KiB experiment | Rust reference |
|---|---:|---:|---:|
| Delay only | 21.43 s | 21.54 s | 21.16 s |
| Periodic loss | 24.57 s | 24.21 s | 23.86 s |
| Burst loss | 23.23 s | 23.23 s | 21.90 s |
| Upstream blackout, first | 30.09 s | 30.81 s | 23.87 s |
| Upstream blackout, reverse order | 30.07 s | 30.81 s | 23.87 s |

Delay-only cases had all 2,000 upload and download receipts. Periodic/burst
cases returned server `complete:true`, **not** proof of frontend acceptance.
Periodic loss may fail the unchanged .99 density gate. Neither density,
receipt accounting, counts, result deadlines, zero-retransmission reliability,
RTOs nor association retry budgets were modified.

In both blackout rounds current C++ returned server `complete:true` **late**.
The experiment and Rust returned `complete:false` with `DOWNLOAD_SEND_FAILED`
and `DOWNLOAD_SCHEDULER_INCOMPLETE`. The experiment matched those reasons but
**still arrived late**, slightly later than current C++. Late ACK/receipt
discrepancies are not accepted as valid measurements. Two repeats establish
this observation, not statistical significance or universal equivalence.

## Separate native metadata check

A separate four-case diagnostic interposed `usrsctp_setsockopt` and
`usrsctp_sendv`, without changing arguments or return values. It recorded
monotonic time, size, stream/PPID, status/errno and actual
`getsockopt(SO_SNDBUF)` readback. It never dereferenced application payload or
destination bytes, recorded no ICE credentials and retained no packet bodies.
Logging can perturb timings; these are not formal performance samples.

Actual socket readback was **1,048,576 bytes** for current C++ and **65,536
bytes** for the experiment, in both delay-only and blackout cases. The negative
result was therefore not caused by an unapplied setting. The successful native
text sends matched exactly the initial ready message plus the result fragments
observed by the browser. No metadata truncation or cleanup failure occurred.

| Blackout case | First result attempt at native send, after ready | Last result fragment admitted, after ready | Raw client arrival |
|---|---:|---:|---:|
| Current C++, 1 MiB | 21.589 s | 21.629 s | 30.080 s |
| Experiment, 64 KiB | 30.049 s | 30.529 s | 30.819 s |

Native times share the first native ready-send origin; client elapsed time has
a different origin. Do **not** subtract them as an exact one-way latency.
Native send success means admission, not delivery. Current C++ admitted all 70
result fragments without native send errors. The experiment admitted 63 result
fragments, with seven native control `EAGAIN` returns before later successful
attempts. Its delay-only result also had 123 control `EAGAIN` returns versus
zero with the default buffer. Those internal retries are not retransmission of
an already accepted probe.

Source inspection explains why the small-buffer case can delay a new control
message's first native attempt: libdatachannel's `SctpTransport::send` flushes
its existing association-wide FIFO before trying the new message. Queued binary
probe/ACK messages can precede control text even when that channel's buffered
amount is initially small. The raw result also records application finalization
before delivery. This narrows the investigation to queue admission/drain and
native recovery; it does **not** establish a complete root cause or justify
changing control priority, discarding probes or tuning RTOs to pass a test.
Rust's pending-queue selection remains the behavioral reference, not an assumed
control-priority policy.

## Decision and remaining gate

Do not promote the 64 KiB setting as a fix. The source copy, binary, failures,
uninstrumented reports and metadata remain private diagnostic artifacts; only
this sanitized account is published. Normal application source is unchanged.
These experiments operated on no public host, production service, Caddy route,
release link, Rust source or rollback archive.

At this diagnostic milestone, timely truthful result handling remained
unresolved. The subsequent correction above addresses the recorded controlled
case without phantom send counts, relaxed density/deadline checks or a Rust
semantic change. New packages and public role checks of that newer source
remain required. Older public8/8 and benchmark artifacts retain their original
pre-correction identities.
