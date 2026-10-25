"""Apply one explicit, hash-pinned Boost 1.83 header fix into the build tree.

LLVM 18 TSan does not model atomic_thread_fence. Asio's valid release-decrement
plus acquire-fence reference count therefore reports delete/refcount races.
Use an acquire-release decrement in ALL profiles, not a sanitizer suppression.
No installed header is edited and no checker is disabled.
"""
import argparse
import hashlib
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--include", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
relative = Path("boost/asio/detail/atomic_count.hpp")
source = (args.include / relative).read_bytes()
expected = "3114991b4fa76d8b5db92fa3ab604cea453a556826aae278501d27e5548e6b69"
if hashlib.sha256(source).hexdigest() != expected:
    raise SystemExit("Unreviewed Boost atomic_count.hpp revision; review before building")
original = b"a.fetch_sub(1, std::memory_order_release) == 1"
assert source.count(original) == 1
patched = source.replace(original, b"a.fetch_sub(1, std::memory_order_acq_rel) == 1")
fence = b"    std::atomic_thread_fence(std::memory_order_acquire);\n"
assert patched.count(fence) == 1
patched = patched.replace(fence, b"")
target = args.output / relative
target.parent.mkdir(parents=True, exist_ok=True)
if not target.exists() or target.read_bytes() != patched:
    target.write_bytes(patched)
print("Boost 1.83 explicit acquire-release refcount overlay:", expected)
