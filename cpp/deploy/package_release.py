"""Package a trusted local Release build, never install it or mutate a service.

Linux/glibc + Debian package metadata are required. Run ldd only on your own
built executable, not an untrusted downloaded binary. Host glibc/loader, NSS,
CA certificates and kernel remain OS prerequisites; all other linked libraries
are copied by SONAME and checked after relocation. No development path is
needed by the launcher. Raw build directories/configuration are not exported.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile


HOST_LIBS = {'libc.so.6', 'libm.so.6', 'libpthread.so.0', 'libdl.so.2',
             'librt.so.1', 'libresolv.so.2', 'libutil.so.1', 'libanl.so.1'}
PINNED_NATIVE = '6b1e2e620f1e37f0eafeee702eaea0043cb305fd'


def command(*args, env=None):
    return subprocess.check_output(args, text=True, env=env, stderr=subprocess.STDOUT)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def libraries(binary, env=None):
    rows = {}
    for line in command('ldd', str(binary), env=env).splitlines():
        if 'not found' in line:
            raise ValueError('unresolved runtime dependency')
        match = re.fullmatch(r'\s*(\S+) => (/\S+) \(0x[0-9a-f]+\)\s*', line)
        if match:
            name, source = match.groups()
            if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name):
                raise ValueError('unexpected dependency SONAME')
            rows[name] = Path(source).resolve(strict=True)
    if 'libc.so.6' not in rows or 'libdatachannel.so.0.24' not in rows:
        raise ValueError('expected a native Linux/glibc Speedtest build')
    return rows


def glibc_required(files):
    versions = set()
    for file in files:
        for version in re.findall(r'\bGLIBC_(\d+\.\d+(?:\.\d+)?)\b',
                                  command('readelf', '--version-info', str(file))):
            versions.add(tuple(map(int, version.split('.'))))
    return '.'.join(map(str, max(versions)))


def reject_instrumented(file):
    symbols = command('readelf', '--symbols', '--wide', str(file))
    if re.search(r'\b__(?:asan_|tsan_|ubsan_)', symbols):
        raise ValueError('sanitizer runtime/symbols are not a Release artifact')


def package_copyright(source):
    # Ubuntu merged-/usr packages may register either spelling.
    candidates = [str(source)]
    if str(source).startswith('/usr/lib/'):
        candidates.append(str(source)[4:])
    for candidate in candidates:
        try:
            rows = command('dpkg-query', '--search', candidate).splitlines()
        except subprocess.CalledProcessError:
            continue
        for row in rows:
            owner, _, filename = row.partition(': ')
            if filename != candidate:
                continue
            name = owner.split(':')[0]
            notice = Path('/usr/share/doc') / name / 'copyright'
            if notice.is_file():
                return name, notice
    raise ValueError('runtime library has no identifiable distribution copyright')


def write_archive(bundle, archive):
    if archive.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('archive must be outside the release directory')
    # Stable ordering/metadata and gzip timestamp. Never overwrite an archive.
    with archive.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as tar:
                for path in sorted(bundle.rglob('*')):
                    if path.is_symlink():
                        raise ValueError('release symlinks are not supported')
                    info = tar.gettarinfo(str(path), arcname=str(path.relative_to(bundle)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    if path.is_file():
                        with path.open('rb') as contents:
                            tar.addfile(info, contents)
                    else:
                        tar.addfile(info)


def build_release(build, native, output, license_file):
    if platform.system() != 'Linux' or platform.libc_ver()[0] != 'glibc':
        raise ValueError('packaging requires Linux/glibc')
    cache = {}
    for row in (build / 'CMakeCache.txt').read_text().splitlines():
        if row and not row.startswith(('#', '//')) and '=' in row and ':' in row.split('=')[0]:
            key, value = row.split('=', 1)
            cache[key.split(':')[0]] = value
    if cache.get('CMAKE_BUILD_TYPE') != 'Release' or cache.get('SPEEDTEST_SANITIZER', ''):
        raise ValueError('use an unsanitized CMake Release build')
    binary = (build / 'speedtest-cpp').resolve(strict=True)
    reject_instrumented(binary)
    if command('git', '-C', str(native), 'rev-parse', 'HEAD').strip() != PINNED_NATIVE:
        raise ValueError('unexpected native dependency revision')
    submodule_status = command('git', '-C', str(native), 'submodule', 'status', '--recursive')
    if any(row[0] != ' ' for row in submodule_status.splitlines() if row):
        raise ValueError('native submodules differ from the pinned revision')
    native_submodules = {row.split()[1]: row.split()[0] for row in submodule_status.splitlines()}
    inputs = libraries(binary)
    bundled = {name: source for name, source in inputs.items() if name not in HOST_LIBS}
    notices = {}
    for name, source in bundled.items():
        reject_instrumented(source)
        if not name.startswith('libdatachannel.'):
            owner, notice = package_copyright(source)
            notices[owner] = notice
    required = glibc_required([binary, *bundled.values()])
    # Refuse overwrite, before copying anything. A failed bundle is kept for
    # diagnosis; it never replaces a previous known-good release.
    output.mkdir(parents=True, exist_ok=False)
    (output / 'lib').mkdir()
    (output / 'licenses').mkdir()
    shutil.copy2(binary, output / 'speedtest-cpp.bin')
    for name, source in bundled.items():
        shutil.copyfile(source, output / 'lib' / name)
        (output / 'lib' / name).chmod(0o644)
    for name, source in notices.items():
        shutil.copyfile(source, output / 'licenses' / (name + '.copyright'))
    native_notices = ['LICENSE', 'deps/libjuice/LICENSE', 'deps/usrsctp/LICENSE.md',
                      'deps/plog/LICENSE', 'deps/json/LICENSE.MIT']
    for name in native_notices:
        shutil.copyfile(native / name, output / 'licenses' / ('native-' + name.replace('/', '-')))
    shutil.copyfile(license_file, output / 'LICENSE')
    source = Path(__file__).resolve().parents[1]
    # MPL-covered dependency sources/patches accompany the binary rather than
    # relying on a mutable download URL. Tracked files only, no build products.
    archive = subprocess.check_output(['git', '-C', str(native), 'archive', '--format=tar', 'HEAD'])
    (output / 'libdatachannel-upstream.tar').write_bytes(archive)
    for dependency in ['libjuice', 'usrsctp', 'plog', 'json']:
        archive = subprocess.check_output(['git', '-C', str(native / 'deps' / dependency),
                                           'archive', '--format=tar', 'HEAD'])
        (output / (dependency + '-upstream.tar')).write_bytes(archive)
    (output / 'native-patches').mkdir()
    for patch in sorted((source / 'cmake/patches').glob('*.patch')):
        shutil.copyfile(patch, output / 'native-patches' / patch.name)
    (output / 'speedtest-cpp').write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$root/lib"
exec "$root/speedtest-cpp.bin" "$@"
''')
    (output / 'speedtest-cpp').chmod(0o755)
    (output / 'templates').mkdir()
    for name in ['speedtest-cpp-staging.service', 'staging.env.example', 'Caddyfile.staging.example']:
        shutil.copyfile(source / 'deploy' / name, output / 'templates' / name)
    env = dict(os.environ, LD_LIBRARY_PATH=str((output / 'lib').resolve()))
    resolved = libraries(output / 'speedtest-cpp.bin', env=env)
    for name in bundled:
        if resolved.get(name) != (output / 'lib' / name).resolve():
            raise ValueError('relocated executable still resolves a developer library')
    compiler = cache['CMAKE_CXX_COMPILER']
    manifest = dict(format=1, architecture=platform.machine(), minimum_glibc=required,
                    build_type='Release', web_ui=cache.get('SPEEDTEST_WEB_UI'),
                    lto=cache.get('SPEEDTEST_LTO'),
                    compiler=command(compiler, '--version').splitlines()[0],
                    cxx_flags=cache.get('CMAKE_CXX_FLAGS', ''),
                    release_flags=cache.get('CMAKE_CXX_FLAGS_RELEASE', ''),
                    native_upstream=PINNED_NATIVE,
                    native_submodules=native_submodules,
                    host_prerequisites=sorted(set(inputs) - set(bundled)),
                    os_prerequisites=['Linux/glibc and loader', 'NSS/resolver configuration',
                                      'system CA certificates', 'separate service environment'],
                    files={str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(
        sha256(path) + '  ' + path.relative_to(output).as_posix() + '\n'
        for path in sorted(output.rglob('*')) if path.is_file()))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['build', 'native-source', 'output', 'license']:
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    manifest = build_release(args.build.resolve(), args.native_source.resolve(),
                             args.output.resolve(), args.license.resolve())
    if args.archive:
        write_archive(args.output.resolve(), args.archive.resolve())
    print(json.dumps(dict(files=len(manifest['files']), minimum_glibc=manifest['minimum_glibc'],
                          architecture=manifest['architecture'], installed=False)))
