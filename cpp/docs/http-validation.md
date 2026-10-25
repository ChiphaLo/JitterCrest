# Native HTTP milestone — 2026-10-25

Historical HTTP-only snapshot. Native RTC has since been implemented; see
`rtc-validation.md` for subsequent evidence and still-open acceptance gates.
Statements below about RTC not being implemented describe this earlier snapshot.

This is partial progress toward the complete independent C++ backend, **not**
full acceptance. Rust production/source/frontend/archives remain unchanged.
Valid WebRTC negotiation still fails closed with 503; no Rust executable,
Rust FFI, service proxy or Node runtime implements any C++ route.

## Implemented independently

- Environment configuration with exact common Rust numeric ranges/dependencies,
  flags, startup errors, public candidate mapping and loopback-only test guard.
- Standard/speed rate tables, active connection leases, rolling byte budgets and
  refund-on-destruction fixed-download reservations.
- Native HTTP/1 connection handling on Boost.Asio/Beast C++20 coroutines; separate
  connection strands, bounded input/upload buffers and one reader/one writer.
- Root/icon, aliases, health, latency, client info, fixed/streaming downloads,
  uploads, stream uploads, upload progress creation/finish/NDJSON, and RTC input
  validation. UI and measurement-only executables are separate build profiles.
- Ignored request-body draining while an early response is delivered; explicit
  Content-Length/chunked framing, partial writes, quota-aborted streams, upload
  deadlines, wall-clock fixed-download cancellation and bounded shutdown.
- Owner-scoped random progress IDs, one-use/reusable lanes, live 100 ms snapshots,
  first-nonempty-byte warmup clocks and authoritative final request accounting.
- Bounded libcurl HTTPS provider workers, no redirects, response size/time limits,
  asynchronous-DNS requirement, cache, normalization and fallback. Deterministic
  injected-provider tests avoid treating Internet/provider variance as parity.

## Tested scope and reproduction

The shared client starts **two real, independent local processes**, sets the
same effective environment, runs the same requests, and stops both in `finally`.
It does not compare a C++ wrapper to a Rust process behind that wrapper.

```sh
python3 cpp/tests/http_contract.py \
  --rust /absolute/path/to/matching-rust-release \
  --cpp cpp/build/debug/speedtest-cpp --output /local/evidence/http.json
python3 cpp/tests/http_lifecycle.py \
  --rust /absolute/path/to/matching-rust-release \
  --cpp cpp/build/debug/speedtest-cpp --output /local/evidence/lifecycle.json
python3 cpp/tests/config_differential.py \
  --rust cpp/tests/rust-oracle/target/release/speedtest-rust-oracle \
  --cpp cpp/build/debug/speedtest-config-fixture --output /local/evidence/config.json
```

Run the HTTP contract with both Rust and C++ agent builds to validate the no-UI
profile. Tests explicitly disable RTC on **both** processes; that is a declared
scope boundary, not proof of RTC compatibility. Native RTC/browser/loss testing
is still required before the full goal can pass.

The ordinary shared suite has 126 comparisons: statuses, header values,
10 KiB/100 KiB/1 MiB lengths, upload bytes/warmup fields, aliases, CORS/origin
denials, progress lifecycle, invalid offers, malformed framing, ignored-body
reuse, half-close recovery, 100-continue, pipelined socket limits, quotas,
admission, trusted proxy behavior, CSP escaping and 128 requests at concurrency16.

The slow suite verifies eight lifecycle contracts: partial/silent initial header
timeouts, incomplete offer-body timeout, keep-alive expiration, intermediate
NDJSON samples, ignored-body activity spanning an idle window, fixed-download
deadline under backpressure, and forced shutdown within the five-second drain
grace. It waits for real 30-second global check ticks, not a shortened fake timer.

Exact JSON fields/byte counts/discrete states are compared. Runtime duration
values are checked nonnegative and relationships/measurement semantics asserted,
then normalized because two independent requests cannot have identical wall
times. Header-name case, Date, random IDs, CSP nonces and random download contents
are not compared as deterministic values. Content lengths are independently
checked against actual bodies; bodies/CSP remain compared after nonce replacement.
No quota, reason code, payload size or successful-vs-aborted EOF is normalized away.

## Reference behaviors learned by tests

- HEAD retains an explicit icon length; unknown HEAD/root/latency responses use
  the actual Rust empty-body behavior, not Beast's default serialization.
- An empty string-body chunked 503 can serialize two terminal chunks in Beast.
  The C++ rejection response uses an empty-body serializer and matches Rust's
  single terminal chunk, including already-pipelined requests after socket limits.
- Rust/Hyper accepts Transfer-Encoding together with Content-Length and uses
  transfer framing. C++ pre-parses one bounded header and removes the ignored
  length before Beast parses it. This records/preserves the reference behavior;
  it is not an attempted Rust security repair. Body framing is interpreted once.
- A silent newly accepted connection uses the header deadline, **not** keep-alive.
  After a response, keep-alive has the inherited extra 1000 ms buffer. The next
  first byte cancels idle expiry. Header/body expiry is checked at the next
  server-wide 30-second tick strictly after its configured deadline.
- A fixed-download deadline must close the socket even when a slow peer prevents
  the body generator from being polled. A stream quota failure is an aborted body,
  not a successful early EOF. Live progress finalizes before the final upload
  response is delivered; display snapshots do not replace final byte accounting.

Private/public Rust editions have different operational node/CSP defaults. Tests
set explicit generic nodes and reproduce the reference's legacy CSP origin list
via the C++ optional `SPEEDTEST_CSP_COMPAT_ORIGINS` setting. Private values and raw
reports are not embedded in public source. This is an explicit effective profile,
not an ignored CSP mismatch or a change to the shared frontend.

The public checkout also contains a previously sanitized frontend variant
(generic probe-node identifiers/addresses). A first public-checkout comparison
against the private production Rust artifact correctly failed four HTML cases:
the two executables had embedded different input assets. For that comparison,
configure `-DSHARED_ASSETS=/absolute/path/to/the/reference/frontend` so both embed
the exact same unchanged HTML/icon. Do not normalize away HTML differences or
copy private frontend/deployment data into the public repository. A default
public build uses its own sanitized shared frontend, as the public Rust build does.

## Safety gate status

Supporting config/limits/progress/client-info/main sources passed the configured
Clang-Tidy categories. The getenv warning has one narrow, documented annotation:
environment loading occurs before workers start, and the service never mutates
the environment. Other findings were addressed with explicit ownership, checks,
named bounds/credits, observable cleanup errors and non-empty error handling.

**The full network Clang-Tidy/analyzer gate has NOT passed.** Clang18 reports
four diagnostics inside installed Boost1.83 Asio/Beast coroutine/parser internals:
`awaitable.hpp:171`, `awaitable.hpp:508`, `basic_parser.ipp:187` and `:319`.
They require further investigation of analyzer modeling versus a real defect;
they are not silently suppressed, and no safety checker was disabled to obtain
a green full-service result. Passing runtime sanitizers would not, by itself,
prove those static diagnostics false.

The measured build/test profile results are recorded in the milestone table
below. System Boost/libcurl/OpenSSL dependencies are not claimed to be completely
sanitizer-instrumented merely because the C++ service is instrumented.

| Check | Actual result | Scope |
|---|---|---|
| Debug / Release CTest | 5/5 each passed | Core, config, RAII limits, progress, bounded provider workers |
| ASan + UBSan CTest | 5/5 passed | Separate instrumented build; leak detection and halt-on-error enabled |
| TSan CTest | 5/5 passed | Separate build, concurrent leases/receipts/provider callbacks |
| HTTP shared process contracts | 126/126 each passed | Debug, Release, ASan/UBSan and TSan UI builds |
| Slow/lifecycle process contracts | 8/8 each passed | Debug, Release, ASan/UBSan and TSan UI builds |
| Agent HTTP shared contracts | 126/126 passed | Release C++ no-UI versus matching Rust no-UI executable |
| Original Rust/config differential | 521/521 passed | Exact common startup errors, 42 numeric settings and common flags |
| Supporting-source Clang-Tidy | Passed | Config, limits, progress, client info, main |
| Network Clang-Tidy | **Not passed** | Four upstream-header analyzer diagnostics described above |
| systemd template parser | Passed with staged executable/working-directory paths | Local scratch copy only, no installed unit or running-service claim |
| Caddy template engine validation | Not run | Caddy unavailable locally; no proxy/TLS deployment claim |
| Full native WebRTC and browser parity | **Not achieved** | Required native transport milestone still pending |
| Fair CPU/RSS/throughput/tail benchmarks | **Not measured** | No language-performance verdict |

The five CTest targets do not include every shared-process contract automatically;
run both Python suites explicitly. Actual reports are kept outside the public
checkout. The first core milestone's 3065 vectors and 20,000 fuzz runs remain
separate evidence and are not mislabeled as full-network fuzz coverage.

## Not achieved

Native libdatachannel signaling/session management, zero-retry probe transport,
cancel/tombstones, scheduler/grace results and full browser equivalence remain
required. So do complete service/resource-exhaustion gates, complete dependency
coverage, fair alternating Release performance measurements, and a measured
replacement recommendation. No throughput/CPU/RSS superiority claim is made.
The independent staging templates have not been installed; production stays Rust.
