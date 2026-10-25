"""Compile the pinned/patched libjuice hash header; compare hashlib/HMAC.

This is a dependency regression, not a replacement speedtest implementation.
All callback branches and repeated HMAC resets run with ASan+UBSan enabled.
"""
import argparse
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import tempfile

SOURCE = r'''
#include "picohash.h"
#include <stdio.h>
static void emit(const char *name, picohash_ctx_t *ctx) {
    unsigned char digest[PICOHASH_MAX_DIGEST_LENGTH];
    picohash_final(ctx,digest);
    printf("%s ",name);
    for(size_t i=0;i<ctx->digest_length;++i)printf("%02x",digest[i]);
    puts("");
}
int main(void) {
    void (*initializers[])(picohash_ctx_t *)={picohash_init_md5,picohash_init_sha1,picohash_init_sha224,picohash_init_sha256};
    const char *names[]={"md5","sha1","sha224","sha256"};
    for(unsigned i=0;i<4;++i) {
        picohash_ctx_t ctx;initializers[i](&ctx);picohash_update(&ctx,"abc",3);emit(names[i],&ctx);
        picohash_reset(&ctx);picohash_update(&ctx,"abc",3);emit(names[i],&ctx);
        for(unsigned length=20;length<=100;length+=80) {
            unsigned char key[100];memset(key,0x0b,length);
            picohash_init_hmac(&ctx,initializers[i],key,length);
            picohash_update(&ctx,"Hi There",8);emit(names[i],&ctx);
            picohash_reset(&ctx);picohash_update(&ctx,"Hi There",8);emit(names[i],&ctx);
        }
    }
    return 0;
}
'''

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--libjuice-source", required=True, type=Path)
    p.add_argument("--cc", default="clang")
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="native-hash-vectors-") as temp:
        directory = Path(temp)
        source, executable = directory/"hash.c", directory/"hash-vectors"
        source.write_text(SOURCE)
        subprocess.run([args.cc, "-std=c11", "-g", "-O1", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer", "-I", str(args.libjuice_source/"src"), str(source),
                        "-o", str(executable)], check=True)
        result = subprocess.run([str(executable)], check=True, text=True, capture_output=True,
                                env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                                         UBSAN_OPTIONS="halt_on_error=1"))
        rows = result.stdout.splitlines()
        expected = []
        for algorithm in ["md5", "sha1", "sha224", "sha256"]:
            expected += [f"{algorithm} {hashlib.new(algorithm,b'abc').hexdigest()}"]*2
            for length in [20, 100]:
                digest = hmac.new(bytes([0x0b])*length,b"Hi There",algorithm).hexdigest()
                expected += [f"{algorithm} {digest}"]*2
        assert rows == expected, (rows, expected)
        assert not result.stderr, result.stderr
        print("24 native hash/HMAC/reset vectors passed ASan+UBSan")
