# Reproducible local benchmarks

## Real-network client

`network_benchmark.py` is a bounded Python standard-library HTTPS load client
for two **authorized isolated endpoints on the same test host**, not a
deployment tool. The operator must establish matching hardware/OS/proxy,
Release optimization, server configuration and resource budgets independently.
Do not target production merely because credentials are available.

Run `python3 cpp/benchmarks/network_benchmark_test.py`, then invoke the client
with `--rust-url https://TEST_IP:RUST_TEST_TLS_PORT`,
`--cpp-url https://TEST_IP:CPP_TEST_TLS_PORT`, `--ca /private/test-ca.pem`,
optional `--token-file /private/acceptance-token`, and a fresh
`--output /private/evidence/network.json`. Defaults are three alternating rounds,
two-second admission windows, 0.5-second warmup and a 512 MiB attempted-payload
budget; bounded overrides are available through `--help`.

Ordinary supplied-CA/IP-hostname verification stays enabled. The client installs
no proxy/routing override. An optional temporary X-Speedtest-Acceptance header
stays out of metadata. Existing outputs are refused before traffic. Errors or
an exhausted budget retain partial evidence and exit nonzero, without retries.
Warmup/preconnect failures retain the worker cause rather than only a barrier
exception. Freeze workloads/artifacts during collection and retain failed runs.

The workload is /latency and 1 MiB upload/download at concurrency 1 and 4.
Workers preconnect, warm up independently, synchronize measurement admission,
then drain their last request. Goodput includes drain time. Errors, wrong
status, byte-count mismatch and oversized responses are not successful payload.
The attempted-byte budget includes warmup/failures, not exact TLS/wire bytes.

P50/P95/P99 are nearest-rank **observed benchmark request latencies**, not the
application's differently defined probe percentiles, corrected omission or an
open-loop SLA. Short windows can produce small sample counts and noisy tails.
Results include Python/client, network, TLS, proxy and co-tenant effects; they
cannot isolate language speed or prove NIC saturation. The CLI explicitly marks
remote CPU/RSS unavailable, never zero. A trusted private orchestrator can use
`run_workload(..., observe=...)` for independent remote process snapshots.
Distinguish current RSS from lifetime high-water RSS and document CPU-tick
quantization and resource-synchronization overhead.

## Native/local measurements

The benchmark runner is a **test client**, not an implementation of either
backend. It runs two independent local Linux executables in alternating AB/BA
order and records actual values. Rust production, its sources/configuration,
archives, services and routing are not changed. No benchmark result permits a
production cutover.

## Comparable builds

Install the documented build dependencies and bootstrap the patched native
transport first. Use Release/ThinLTO on both sides, without PGO, fast-math or
architecture-specific tuning available to only one side. Record the actual
compiler, dependency and executable hashes. Compatibility/sanitizer builds are
not performance builds.

```sh
bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S cpp -B cpp/build/bench -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld \
  -DCMAKE_PREFIX_PATH=/absolute/local/deps/install \
  -DSPEEDTEST_LTO=ON -DSPEEDTEST_BENCHMARKS=ON
cmake --build cpp/build/bench --parallel 4
ctest --test-dir cpp/build/bench --output-on-failure

# The existing reference Cargo release profile already enables thin LTO.
# Use an isolated target directory if preserving previous build artifacts.
cargo build --release --locked --manifest-path archive/rust/Cargo.toml
cargo build --release --locked --manifest-path archive/rust/oracle/Cargo.toml \
  --bin core_benchmark --bin speedtest-rust-oracle
```

The native-core Rust driver compiles the original `protocol.rs` and `stats.rs`
by path, without editing them; the C++ driver calls the independently implemented
C++ functions. Neither service invokes or links the other language's driver.

## Load generator and CPU placement

HTTP requires native `wrk` with Lua support plus GNU time. The implementation
used Ubuntu's wrk 4.1.0 package. Install it normally or extract distribution
packages to a private tool prefix without starting any services; a non-system
LuaJIT dependency path can be passed with `--wrk-library-path`.

The runner uses Linux `/proc`, CPU affinity, GNU time and Python's standard
library. The default masks fit a 24-logical-CPU test machine, **not every host**:
server `0,2`, generator `4,6,8,10`, orchestrator `12`. Choose disjoint masks
appropriate to the actual CPU/SMT/NUMA layout. WSL guest affinity does not imply
exclusive ownership of Windows host physical cores. Keep other compilation,
tests and heavy workloads stopped while collecting performance data.

Both servers are given the same two-CPU budget, `SPEEDTEST_IO_THREADS=2` and
`TOKIO_WORKER_THREADS=2`. The real total thread counts can differ and are recorded.
Listeners bind only to random loopback ports. Existing rate/quota code is not
disabled or patched: a matching loopback-only trusted-proxy profile simulates
multiple RFC 2544 benchmark-range clients. GET caches contain 1024 identities
per worker; upload caches are bounded to 16 MiB per worker. No live trusted-proxy
setting is copied from or written to production.

## Run

```sh
python3 cpp/benchmarks/benchmark.py --mode core \
  --rust /absolute/path/to/core_benchmark \
  --cpp cpp/build/bench/speedtest-core-benchmark \
  --rounds 5 --output /private/evidence/core.json

python3 cpp/benchmarks/benchmark.py --mode http \
  --rust /absolute/path/to/reference-rust-server \
  --cpp cpp/build/bench/speedtest-cpp --wrk /absolute/path/to/wrk \
  --rounds 3 --duration 5 --warmup 1 --concurrencies 1,16,64,128 \
  --output /private/evidence/http.json

python3 cpp/benchmarks/benchmark.py --mode soak \
  --rust /absolute/path/to/reference-rust-server \
  --cpp cpp/build/bench/speedtest-cpp --wrk /absolute/path/to/wrk \
  --rounds 1 --duration 300 --warmup 5 \
  --output /private/evidence/soak.json

# Run this separate accepted-socket observation only AFTER timed measurements.
python3 cpp/tests/http_capacity.py \
  --rust /absolute/path/to/reference-rust-server \
  --cpp cpp/build/bench/speedtest-cpp \
  --output /private/evidence/capacity.json

python3 cpp/benchmarks/summarize.py --core /private/evidence/core.json \
  --http /private/evidence/http.json --soak /private/evidence/soak.json \
  --capacity /private/evidence/capacity.json \
  --output /private/evidence/aggregate-report.md
```

Native core cases use seeded runtime inputs, matching checksums and iteration
counts, with 10% in-process warmup. Input parsing, hex conversion and setup are
outside the timed loop. Both drivers dispatch outside the loop, consume all
results and use equivalent standard compiler fences around timing—no custom
assembly or unequal optimizer barriers. Cases are 128-byte decoding (1/8 invalid
lengths), ASCII/Unicode ID hashes and P95 over 512 samples. Loop/index/checksum
overhead remains included. Whole-process GNU-time CPU/RSS also includes startup
and warmup and is not mislabeled hot-loop-only cost.

HTTP checks exact endpoint preflight status/bytes (`/latency` correctly returns
204), keeps persistent HTTP/1 connections and measures 10 KiB, 100 KiB and 1 MiB
GET/POST payloads. Any wrk socket, status or timeout error fails the workload;
failed/throttled bytes never count as successful payload throughput. wrk's
corrected closed-loop percentiles are not an open-loop SLO measurement.

Server CPU uses `/proc/<pid>/stat` over the measured window; idle/peak RSS and
thread counts come from kernel status. Peak RSS includes startup and warmup.
GNU time captures the load generator's actual CPU/peak RSS. CPU-at-saturation
alone does not measure efficiency: normalize by completed bytes/requests and
also inspect client utilization for generator ceilings. The soak records an RSS
timeline; five minutes cannot prove 24-hour stability or absence of all leaks.
The separate capacity helper verifies 1/16/64/128 actual backend-owned
ESTABLISHED keepalive sockets, checks release to zero, and observes RSS/threads.
It is not a claim that 128 is the maximum capacity or a concurrent slow-client
exhaustion test. Run it after benchmarks, not as competing load during them.

Every run hashes its binaries and workload sources and checks they did not
change mid-measurement. Progress is printed per case and partial JSON is saved
atomically after each completed case. Only a report with **both `complete=true`
and `passed=true`** is a completed run. A long soak can legitimately spend five
minutes within one case; inspect its live process/log instead of restarting it
because one observation timed out.

Performance mode rejects sanitizer-linked executables. For a correctness-only
smoke against an instrumented new core driver, use `--validation --core-scale
0.001`; that report is explicitly `validation_only`, not performance evidence.

Raw reports contain local paths and stay private. The summary exports aggregate
scientific values and binary hashes only. It does not publish raw process logs,
credentials or deployment data. Local core, loopback HTTP and real Internet
end-to-end measurements must remain separate; these scripts alone do not
provide a real Internet result or justify replacing Rust.
