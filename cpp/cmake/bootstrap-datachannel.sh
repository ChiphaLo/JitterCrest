#!/usr/bin/env bash
set -euo pipefail
# Explicit local dependency prefix. Never installs into or restarts production services.
prefix=${1:?usage: bootstrap-datachannel.sh /absolute/local/dependency-prefix}
case "$prefix" in /*) ;; *) echo 'An absolute dependency prefix is required' >&2; exit 2;; esac
source_dir="$prefix/src/libdatachannel"
expected=6b1e2e620f1e37f0eafeee702eaea0043cb305fd
if [[ ! -d "$source_dir" ]]; then
  mkdir -p "$prefix/src"
  git clone --depth 1 --branch v0.24.6 --recursive --shallow-submodules \
    https://github.com/paullouisageneau/libdatachannel.git "$source_dir"
fi
test "$(git -C "$source_dir" rev-parse HEAD)" = "$expected"
git -C "$source_dir" submodule status --recursive > "$prefix/submodule-versions.txt"
if grep -q '^[+-U]' "$prefix/submodule-versions.txt"; then
  echo 'Dependency submodules are not at the pinned revision' >&2; exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Explicit, reviewed fixes found by fully instrumenting the pinned transport.
# Never disable sanitizer checks to accommodate an upstream defect.
for entry in 'self:libdatachannel-stored-callback-lock.patch' 'libjuice:libjuice-picohash-callback-types.patch' 'usrsctp:usrsctp-linux-shared-state.patch'; do
  dependency=${entry%%:*}
  patch="$script_dir/patches/${entry#*:}"
  if [[ "$dependency" == self ]]; then
    checkout="$source_dir"
  else
    checkout="$source_dir/deps/$dependency"
  fi
  if git -C "$checkout" apply --reverse --check "$patch" 2>/dev/null; then
    echo "Already applied: ${entry#*:}"
  else
    git -C "$checkout" apply --check "$patch"
    git -C "$checkout" apply "$patch"
  fi
done
sha256sum "$script_dir"/patches/*.patch > "$prefix/native-patches.sha256"
build_type=Release
native_flags=()
case "${NATIVE_SANITIZER:-}" in
  '') ;;
  address-undefined) sanitizer=address,undefined ;;
  thread) sanitizer=thread ;;
  *) echo 'NATIVE_SANITIZER must be empty, address-undefined, or thread' >&2; exit 2 ;;
esac
if [[ -n "${sanitizer:-}" ]]; then
  build_type=RelWithDebInfo
  native_flags=("-DCMAKE_C_FLAGS=-fsanitize=$sanitizer -fno-omit-frame-pointer"
    "-DCMAKE_CXX_FLAGS=-fsanitize=$sanitizer -fno-omit-frame-pointer"
    "-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=$sanitizer")
fi
cmake -S "$source_dir" -B "$prefix/build/libdatachannel" -G Ninja \
  -DCMAKE_BUILD_TYPE="$build_type" -DCMAKE_INSTALL_PREFIX="$prefix/install" \
  -DNO_MEDIA=ON -DNO_WEBSOCKET=ON -DNO_TESTS=ON -DNO_EXAMPLES=ON "${native_flags[@]}"
cmake --build "$prefix/build/libdatachannel" --parallel "${BUILD_JOBS:-4}"
cmake --install "$prefix/build/libdatachannel"
