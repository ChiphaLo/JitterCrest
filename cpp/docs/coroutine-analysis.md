# Coroutine analyzer investigation — Clang 18 / Boost 1.83

## Result and reporting boundary

The two remaining full-service findings have now been reproduced together by a
small, independently linked **real Asio/Beast coroutine fixture**:

| Boost site | Checker | Reported field |
|---|---|---|
| `asio/impl/awaitable.hpp:171` | `clang-analyzer-core.CallAndMessage` | `attached_thread_` treated as uninitialized |
| `asio/impl/awaitable.hpp:508` | `clang-analyzer-core.NullDereference` | `resume_context_` treated as undefined |

The AST/CFG comparison and lifetime audit below identify missing coroutine
construction/resumption modeling, rather than an uninitialized pointer in the
audited Asio execution paths. This is a **scoped modeling-false-positive
classification**, not a blanket exemption for coroutine code or Boost.

**The raw full-service Clang-Tidy command still exits nonzero with these two
diagnostics. It is not reported as a clean/zero-warning PASS.** No checker,
`WarningsAsErrors`, source warning or sanitizer was disabled to change that
result. No `NOLINT`, analyzer-only source branch or silent null-pointer fallback
was added. All new/changed diagnostics still require investigation.

## Exact reduced fixture

- `tests/coroutine_analysis_fixture.hpp`: owned socket, owned response values and
  explicit coroutine interfaces.
- `tests/coroutine_analysis_fixture.cpp`: the owned timeout response/Date header
  and nested awaitable operation which reproduce **both** original findings.
- `tests/coroutine_lifetime_test.cpp`: actual `co_spawn` entry and completion owner,
  separately compiled awaited operation, executor workers and runtime assertions.

The split matters: the analyzer examines the coroutine bodies as standalone
entries rather than relying on an already-inlined caller path. Earlier simpler
single-TU programs did **not** reproduce the two findings and were not sufficient
evidence. The current fixture does; it is not a simulation of a coroutine or a
Rust/Node wrapper.

The socket is intentionally unopened. Both nested paths must resume, perform
their asynchronous operation, propagate **exactly `bad_descriptor`**, and run the
owned completion handler. A random exception or early unstarted task is not a
passing result. Half of 10,000 sessions use the timeout response path and half
use the separately compiled nested response path. Three executor threads run
the operations, and all workers join before completion fields are read.

The fixture passed in **Debug, Release, ASan+UBSan and TSan**. Each profile also
passed all **eight CTests**, including the fixture. Leak detection/halt-on-error
were enabled for ASan, and halt-on-error for UBSan/TSan. These runtime results
support the audit; absence of a sanitizer finding alone would not establish that
an analyzer warning is false. Real connected HTTP timeout/upload and RTC paths
are covered by the earlier shared-process suites, not claimed by this unopened-
socket fixture.

The independently exported public-source checkout also built and ran the
fixture, passed eight CTests and reproduced the AST/CFG/negative-control result.
Full configured Tidy checks of the two fixture translation units leave only the
same audited Boost sites (two findings in the analysis fixture and the repeated
resume-context finding in the runtime helper); ordinary local findings about
exception boundaries and an unnecessary parameter copy were fixed, not waived.

## Direct compiler-model evidence

`tests/coroutine_analyzer_regression.py` preserves the expected raw failure and
separate AST/CFG dumps. In the observed Clang 18.1.3 build:

1. The AST contains `CoroutineBodyStmt`, the implicit `__promise` declaration
   and its `CXXConstructExpr`, plus the implicit call to `initial_suspend`.
2. The analyzer's as-written-body CFG accesses `__promise.await_transform` but
   does **not** contain that promise declaration/construction or initial-suspend
   entry. It starts in the user-written body, outside the executor's attach/pump
   and resume-context initialization which actually precede body execution.
3. The reduced valid fixture reproduces the exact two checkers/fields above.
4. Two separately generated, genuinely defective ordinary functions still
   trigger the same checkers: an uninitialized object pointer call and a null
   pointer dereference. They are analyzed only and are **never executed**.

The negative controls guard against accidentally obtaining a clean result by
filtering out these checkers. The regression's own success means **expected
diagnostics/model mismatch reproduced and controls detected**; its report
explicitly sets `full_service_tidy_clean=false` and does not claim runtime safety.

## Source/lifetime audit of the two sites

The relevant installed Boost header was read directly; reference version is 1.83.
These are library invariants, not speculative initialization added to our code:

### Attached thread at line 171

- The promise base default-initializes `attached_thread_` to null. The actual
  C++ promise is constructed before the coroutine body can execute.
- `initial_suspend()` returns `suspend_always`: merely obtaining an awaitable
  does not run its body.
- `awaitable_thread::launch()` calls `attach_thread(this)` before `pump()`.
  `pump()` then invokes the top frame's `resume()`.
- Nested awaitables transfer the active thread through `push_frame` and restore
  it through `pop_frame`. The parent's `await_transform` is evaluated while its
  thread is still attached, before `await_suspend` transfers to the child.
- The service starts these chains with Asio `co_spawn`, not a manual raw-handle
  resume. Current service sources do not use `coroutine_handle`, `.promise()` or
  `.resume()` to bypass this entry protocol. Completion handlers retain the
  `Session` owner until its coroutine finishes; response arguments own their data.

Thus an analyzer path beginning directly at the user-written `co_await`, without
promise construction or the attach/pump entry, does not represent a possible
entry to these audited coroutine bodies.

### Resume context at line 508

- `awaitable_frame_base::resume()` creates a local `resume_context`, assigns
  `resume_context_ = &context`, then calls `coro_.resume()`.
- During that call, the language coroutine machinery invokes `await_suspend`.
  The awaiter registers its continuation through `after_suspend` **while this
  local context is alive**. It does not access a context from a completed resume.
- Only after `coro_.resume()` returns does the library invoke the registered
  initializer. This deliberately avoids initiating an asynchronous operation
  before suspension is complete. A subsequent resume installs a new context
  before the coroutine executes again.

The missing caller/resumption model accounts for an undefined context at this
site. Adding a null check that silently skipped registration would break the
operation protocol and was **not** used as a repair.

## Upstream evidence, not an installed fix

[LLVM PR 208569](https://github.com/llvm/llvm-project/pull/208569), linked to issue
208531, was retrieved with its actual patches and checked **open/unmerged** on
2026-10-26. It describes omission of promise construction from the analyzer CFG
and adds opt-in modeling plus initialized/uninitialized promise controls.

That corroborates the observed AST/CFG discrepancy; it is not claimed merged,
backported or installed here. The proposed patch also explicitly leaves
initial/final suspension unmodeled, so it is not claimed to automatically solve
every Asio executor/context diagnostic. The audited compiler/header versions and
reproducer, rather than an unverified search summary, are the local evidence.

## Reproduce

Use the documented dependency bootstrap and build presets first:

```sh
cmake --build cpp/build/debug --target speedtest-coroutine-lifetime-test --parallel 4
cpp/build/debug/speedtest-coroutine-lifetime-test
ctest --test-dir cpp/build/debug --output-on-failure

# Repeat the runtime fixture/CTests in Release, ASan/UBSan and TSan profiles.
# Clang18 expected-diagnostic proof, not a clean full-service Tidy check:
python3 cpp/tests/coroutine_analyzer_regression.py --build-dir cpp/build/debug \
  --output /private/evidence/coroutine-analysis.json

# Keep running/reporting the real full-service check separately:
clang-tidy-18 cpp/src/http.cpp -p cpp/build/debug
```

The regression keeps all `clang-analyzer-*` checks enabled, with warnings treated
as errors; style checks are outside this dedicated analyzer-model experiment.
The full production-source command still uses the unchanged complete `.clang-tidy`
configuration. The expected-diagnostic regression is invoked explicitly, not
silently substituted for the full-service gate. Raw paths/logs remain private.

This investigation does not finish native resource/timeout, public-NAT probe-only,
real-network benchmark, release-bundle or production acceptance requirements.
