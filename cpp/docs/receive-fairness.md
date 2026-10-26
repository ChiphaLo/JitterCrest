# Bounded OpenSSL receive executor turns

This follow-up changes native transport scheduling, not the probe protocol,
measurement/statistics, test requirements or production routing. Earlier failed
runs in `transport-compatibility.md` remain failed evidence for their original
source snapshots. A passing local follow-up is not public deployment acceptance.

## Evidence and attribution boundary

The instrumented high-density resource test repeatedly stalled with its browser
data channel open and roughly 256 KiB buffered, eventually hitting the existing
60 s **failure** watchdog. The native process remained alive and shut down
normally. Release passed the same test; TSan's slower execution made the case
reproducible. This was distinct from the previously repaired native data races.

Further diagnostics kept the server, browser and protocol workload unchanged:

- Only synthetic loopback sockets were observed. The server's UDP file
  descriptors/inodes and the browser's exact ICE ports were selected; unrelated
  services and Internet traffic were not retained.
- Approximately 100 ms `/proc` observations showed monotonically zero UDP
  socket drop counters on both sides. Instantaneous queue peaks can be missed,
  and this does not prove that every kernel discard path was exercised, but it
  supplied no evidence for increasing socket buffers.
- Authenticated DTLS decoding of bounded, untruncated captures showed large SCTP
  TSN gaps and slow cumulative/FORWARD-TSN advancement. The receive window
  initially approached zero, then recovered near 2 MiB while progress stayed slow.
  This was not a permanently zero advertised receive window.
- Numeric statistics/association fields were read from the owned process using
  exact offsets compiled from the pinned native headers. No function was
  injected into the process and no racy in-process `usrsctp_get_stat` struct copy
  was added. These are approximate observations, not a transactional snapshot.
  Reassembly remained nonempty even with an empty application socket byte count;
  partial unordered DATA fragments were also visible on the wire. Native
  rwnd/chunk-limit drop counters stayed zero in these observations, so those
  counters alone do not locate the first missing fragment.

One initial diagnostic sampler logged its mapping-retirement exception after
the test process ended; its preceding observations remain diagnostic evidence,
not an acceptance pass. Later samplers handle that lifecycle explicitly. Raw
packets, session keys, pointer/offset tooling and operational records stay private.
Neither this reproduction nor its improvement identifies an Internet proxy/VPS
route as the cause of the earlier public failures.

## Scheduling change

The pinned OpenSSL `DtlsTransport::doRecv` previously drained its input queue
until empty while holding the transport receive mutex. Under continuous input,
that is an unbounded executor turn. SCTP receive/flush work uses the same native
thread pool; a second DTLS receive task can also wait on the same mutex. Slow
instrumented processing can therefore defer the work needed to drain/acknowledge
input. This is a scheduling risk visible in the code, not proof that every
discarded datagram was lost at that exact queue.

`libdatachannel-openssl-recv-fairness.patch` processes at most **64 datagrams per
executor turn**, schedules further receive work through the existing mechanism,
then returns. The existing mutex still serializes DTLS processing. Its stopped
queue/close-notify path and peer lifetime ownership remain intact.

Unchanged boundaries:

- native queue size and socket/association buffer limits;
- 128 B probes and unordered `maxRetransmits=0`;
- SCTP association retry budget and application/session deadlines;
- control fragmentation, payloads, admission accounting and the 0.99 gate;
- flood ceiling 40,000 sends, original pacing/yields and `sent > 24000` assertion;
- 60 s failing flood watchdog and actual close/tombstone/replacement-ACK checks.

No extra worker threads, unbounded buffers, custom allocator, priority queue or
lock-free mechanism were introduced. This patch is scoped to the selected
OpenSSL build; GnuTLS/MbedTLS scheduling is not claimed tested or repaired.

## Isolated experiment

Before changing canonical dependencies, an independent checkout was built from
the same pinned upstream/submodules and published repairs, with only this batch
limit added. The same TSan application executable loaded that isolated native
library, verified from its actual process mappings. No TLS hooks or packet tracer
were used in the three predeclared validation rounds.

All three rounds passed without reducing any test requirement. Each sent 40,000
probes, received 24,000 ACKs, triggered the server's test-message limit, observed
both channels close, verified the attempt tombstone and used a replacement peer.
Observed whole-case times were **51.08, 37.67 and 40.76 seconds**. These are finite
resource-regression observations, not normal 20 packets/s RTT measurement times,
a throughput benchmark, or evidence that arbitrary overload/loss always recovers.
The original failures were not rerun and relabeled as passes.

The exact tested patch was then added to the reproducible bootstrap and applied
to the two canonical local native checkouts. Prior source and all three native
library profiles were copied and hash-verified for rollback. Frozen benchmark
dependencies were not changed. Pristine upstream plus every published patch
reproduces the actual tracked native sources, with strict patch-whitespace checks.

## Current validation boundary

Full repaired-source Release, ASan/UBSan and TSan revalidation completed on
unchanged application/test sources, binaries and matching native libraries:

| Check | Release | ASan + UBSan | TSan |
|---|---:|---:|---:|
| CTests | 8 passed | 8 passed | 8 passed |
| Configuration differential | 521 passed | 521 passed | 521 passed |
| Paired UI-configuration visibility | 16 passed | 16 passed | 16 passed |
| Paired HTTP contracts | 126 passed | 126 passed | 126 passed |
| Native resource scenarios | 13 passed | 13 passed | 13 passed |
| Consecutive untraced close/recovery rounds | 100 passed | 100 passed | 100 passed |
| Native wire/loss/actual-frontend cases | 22 passed | 22 passed | 22 passed |
| Idle/active/offer-race shutdown cases | 3 passed | 3 passed | 3 passed |

Debug rebuilt and passed eight CTests. All three rebuilt native profiles also
passed the actual four-thread, 400,000-copy mbuf ownership fixture. The discrete
resource/wire contracts compare with retained immutable paired Rust results;
configuration, visibility and HTTP were paired again in this current run. No
sanitizer, executor or delayed-cleanup finding was accepted as a pass. Earlier
source reports, including the failed flood matrix, are not relabeled current.
Independent public-checkout builds were checked separately: preserved-frontend
UI and no-UI agent each passed eight CTests and 126 paired HTTP contracts;
the UI also passed sixteen paired configuration-visibility cases. A fresh
default generic-frontend UI build passed eight CTests and eight custom-node
visibility cases, and the stalled/healthy watchdog controls passed again.

Three new Release/ThinLTO bundles were produced: generic UI, preserved-frontend
UI and measurement-only agent. Each includes the new native patch, 33 relocated
non-glibc runtime libraries and a computed minimum GLIBC version of 2.38. All
three passed fresh extraction/checksums, actual process-library relocation,
10 KiB/100 KiB/1 MiB upload/download, both progress modes and native remote-close
plus replacement-ACK checks. Both UI editions completed their actual paired
measurement and Restart/RTT paths; the agent denied UI/icon endpoints. These are
new local artifacts, not renamed older bundles or public-host acceptance.

Do not promote an old artifact that lacks the new dependency patch. Actual
public central/multiple-agent acceptance, final requirement audit and the
conditionally authorized merge/production deployment remain outstanding. Rust,
the frontend, existing archives and production services have not been modified.
The subsequent current-artifact public transaction is recorded separately in
`public-staging-validation.md`: all four data-node subcases passed, but the probe
node retained two C++ failures and one Rust failure. The local fairness repair
therefore is not presented as a cure for those public transport observations.
