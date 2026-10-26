"""Linux-only focused test against an explicitly selected native static archive.

Use the same sanitizer for the archive and fixture. This supplements, rather
than substitutes for, the browser/native transport resource and wire suites.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True,
                        help='Pinned usrsctp/usrsctplib directory')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--compiler', default='clang-18')
    parser.add_argument('--sanitizer', choices=['none', 'thread', 'address,undefined'],
                        default='none')
    args = parser.parse_args()
    flags = ['-std=c11', '-O2', '-g', '-pthread', '-fno-omit-frame-pointer']
    if args.sanitizer != 'none':
        flags.append('-fsanitize=' + args.sanitizer)
    definitions = ['_GNU_SOURCE', '__Userspace__', 'SCTP_SIMPLE_ALLOCATOR',
                   'SCTP_PROCESS_LEVEL_LOCKS', 'SCTP_DEBUG', 'HAVE_STDATOMIC_H',
                   'HAVE_SYS_QUEUE_H', 'HAVE_LINUX_IF_ADDR_H',
                   'HAVE_LINUX_RTNETLINK_H', 'HAVE_NETINET_IP_ICMP_H',
                   'HAVE_NET_ROUTE_H']
    with tempfile.TemporaryDirectory(prefix='native-mbuf-refcount-') as tmp:
        binary = str(Path(tmp) / 'refcount-test')
        subprocess.run([args.compiler, *flags, *['-D' + x for x in definitions],
                        '-I' + str(args.source_dir),
                        str(Path(__file__).with_suffix('.c')), str(args.archive),
                        '-o', binary], check=True, timeout=60)
        env = dict(os.environ)
        env.setdefault('TSAN_OPTIONS', 'halt_on_error=1')
        env.setdefault('ASAN_OPTIONS', 'detect_leaks=1:halt_on_error=1')
        subprocess.run([binary], check=True, env=env, timeout=60)


if __name__ == '__main__':
    main()
