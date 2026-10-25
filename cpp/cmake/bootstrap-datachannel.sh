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
cmake -S "$source_dir" -B "$prefix/build/libdatachannel" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix/install" \
  -DNO_MEDIA=ON -DNO_WEBSOCKET=ON -DNO_TESTS=ON -DNO_EXAMPLES=ON
cmake --build "$prefix/build/libdatachannel" --parallel "${BUILD_JOBS:-4}"
cmake --install "$prefix/build/libdatachannel"
