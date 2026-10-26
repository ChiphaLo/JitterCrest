/* Exercise the real pinned usrsctp cluster copy/free code, not a mock counter.
 * The original mbuf stays immutable and alive until every worker has joined. */
#include "user_atomic.h"
#include "user_mbuf.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { workers = 4, iterations = 100000 };

static void require(int condition) {
    if (!condition) {
        fputs("native mbuf ownership invariant failed\n", stderr);
        abort();
    }
}

static void *copy_and_release(void *arg) {
    struct mbuf *source = arg;
    for (int i = 0; i < iterations; ++i) {
        struct mbuf *copy = m_copym(source, 0, 128, M_NOWAIT);
        require(copy != NULL);
        require(copy->m_ext.ext_buf == source->m_ext.ext_buf);
        require(memcmp(copy->m_data, source->m_data, 128) == 0);
        /* Also exercise the two shared-reference observation macros. */
        require(!M_WRITABLE(source));
        require(SCTP_BUF_EXTEND_REFCNT(source) >= 2);
        m_freem(copy);
    }
    return NULL;
}

int main(void) {
    pthread_t threads[workers];
    mbuf_initialize(NULL);
    struct mbuf *source = m_get(M_NOWAIT, MT_DATA);
    require(source != NULL);
    m_clget(source, M_NOWAIT);
    require((source->m_flags & M_EXT) != 0);
    source->m_len = 128;
    memset(source->m_data, 0xa5, 128);
    for (int i = 0; i < workers; ++i)
        require(pthread_create(&threads[i], NULL, copy_and_release, source) == 0);
    for (int i = 0; i < workers; ++i)
        require(pthread_join(threads[i], NULL) == 0);
    require(SCTP_BUF_EXTEND_REFCNT(source) == 1);
    require(M_WRITABLE(source));
    m_freem(source);
    puts("PASS actual usrsctp: 400000 concurrent cluster copies/releases; final owner=1");
    return 0;
}
