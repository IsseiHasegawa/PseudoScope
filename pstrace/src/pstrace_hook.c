/*
 * pstrace_hook.c
 *
 * -finstrument-functions backend. The compiler inserts a call to
 * __cyg_profile_func_enter(this_fn, call_site) at the entry of every
 * instrumented function. We attribute each entry to the "current test"
 * (set from Python via pstrace_set_test) and count (test, function) pairs.
 *
 * At process exit we resolve each function address to (image, offset) with
 * dladdr() and write a raw TSV. Turning offset -> function name + file:line
 * and filtering to the target's own sources happens in Python post-processing
 * (atos on macOS, addr2line/llvm-symbolizer on Linux), where it is easy to do
 * per platform.
 *
 * Thread-safe: each thread records into its own hash table (no lock on the
 * entry hook's hot path); the tables are linked in a global registry and merged
 * once, single-threaded, at dump. Calls made on threads a test spawns are
 * attributed to whatever test is current (a single shared g_current_test), which
 * matches serial pytest. Subprocesses are still a separate address space and are
 * not attributed.
 *
 * Every function here is no_instrument_function so the hook never instruments
 * itself (which would recurse infinitely).
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE /* dladdr on glibc; harmless elsewhere */
#endif
#include "pstrace.h"

#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NOINSTR __attribute__((no_instrument_function))

/* ------------------------------------------------------------------ */
/* Interned test ids: current span index -> id string.                */
/* Only the main thread appends (via pstrace_set_test); worker threads */
/* store the integer index and it is resolved to a string at dump.     */
/* ------------------------------------------------------------------ */

static char **g_tests = NULL;
static size_t g_test_count = 0;
static size_t g_test_cap = 0;
/* Shared across threads: set by the main thread, read on every entry. Plain
 * aligned int loads/stores are atomic on the targets we support; volatile keeps
 * worker threads from caching a stale value across a test boundary. */
static volatile int g_current_test = -1; /* -1 = before any test (startup) */

static pthread_once_t g_register_once = PTHREAD_ONCE_INIT;
static int g_dumped = 0;

NOINSTR static char *pstrace_strdup(const char *s) {
    if (s == NULL) {
        s = "";
    }
    size_t len = strlen(s);
    char *copy = (char *)malloc(len + 1);
    if (copy != NULL) {
        memcpy(copy, s, len + 1);
    }
    return copy;
}

/* ------------------------------------------------------------------ */
/* Open-addressing hash table keyed by (test_index, function address). */
/* One table per thread; a global registry links them for the merge.   */
/* ------------------------------------------------------------------ */

typedef struct {
    const void *addr; /* NULL marks an empty slot */
    int test_index;
    unsigned long long count;
} Slot;

typedef struct ThreadTable {
    Slot *slots;
    size_t cap; /* always a power of two */
    size_t used;
    struct ThreadTable *next; /* global registry chain */
} ThreadTable;

static __thread ThreadTable *tls_table = NULL;
static ThreadTable *g_tables = NULL; /* registry head; guarded by g_tables_lock */
static pthread_mutex_t g_tables_lock = PTHREAD_MUTEX_INITIALIZER;

NOINSTR static size_t pstrace_hash(int test_index, const void *addr) {
    size_t h = (size_t)(uintptr_t)addr;
    h ^= (size_t)test_index * (size_t)0x9e3779b97f4a7c15ULL;
    h ^= h >> 29;
    h *= (size_t)0xbf58476d1ce4e5b9ULL;
    h ^= h >> 32;
    return h;
}

/* Insert or accumulate ``count`` for (test_index, addr) into ``table``. */
NOINSTR static void table_put(ThreadTable *table, int test_index, const void *addr,
                              unsigned long long count) {
    if (table->cap == 0 || table->used * 2 >= table->cap) {
        size_t new_cap = (table->cap == 0) ? 1024 : table->cap * 2;
        Slot *new_slots = (Slot *)calloc(new_cap, sizeof(Slot));
        if (new_slots == NULL) {
            return; /* drop silently rather than crash the target process */
        }
        size_t mask = new_cap - 1;
        for (size_t i = 0; i < table->cap; i++) {
            if (table->slots[i].addr == NULL) {
                continue;
            }
            size_t h = pstrace_hash(table->slots[i].test_index, table->slots[i].addr) & mask;
            while (new_slots[h].addr != NULL) {
                h = (h + 1) & mask;
            }
            new_slots[h] = table->slots[i];
        }
        free(table->slots);
        table->slots = new_slots;
        table->cap = new_cap;
    }
    size_t mask = table->cap - 1;
    size_t h = pstrace_hash(test_index, addr) & mask;
    while (table->slots[h].addr != NULL) {
        if (table->slots[h].addr == addr && table->slots[h].test_index == test_index) {
            table->slots[h].count += count;
            return;
        }
        h = (h + 1) & mask;
    }
    table->slots[h].addr = addr;
    table->slots[h].test_index = test_index;
    table->slots[h].count = count;
    table->used++;
}

/* This thread's table, creating and registering it on first use. */
NOINSTR static ThreadTable *thread_table(void) {
    ThreadTable *table = tls_table;
    if (table == NULL) {
        table = (ThreadTable *)calloc(1, sizeof(ThreadTable));
        if (table == NULL) {
            return NULL;
        }
        tls_table = table;
        pthread_mutex_lock(&g_tables_lock);
        table->next = g_tables;
        g_tables = table;
        pthread_mutex_unlock(&g_tables_lock);
    }
    return table;
}

NOINSTR static void pstrace_record(int test_index, const void *addr) {
    if (addr == NULL) {
        return;
    }
    ThreadTable *table = thread_table();
    if (table != NULL) {
        table_put(table, test_index, addr, 1);
    }
}

/* ------------------------------------------------------------------ */
/* CSV/TSV field writer (quote and escape).                            */
/* ------------------------------------------------------------------ */

NOINSTR static void pstrace_write_field(FILE *fp, const char *s) {
    fputc('"', fp);
    if (s != NULL) {
        for (const char *p = s; *p != '\0'; p++) {
            if (*p == '"') {
                fputc('"', fp);
            }
            fputc(*p, fp);
        }
    }
    fputc('"', fp);
}

NOINSTR static void pstrace_write_slot(FILE *fp, const Slot *slot) {
    int ti = slot->test_index;
    const char *test_id =
        (ti >= 0 && (size_t)ti < g_test_count) ? g_tests[ti] : "(startup)";

    Dl_info info;
    const char *image = "?";
    long offset = 0;
    const char *sym = "?";
    if (dladdr(slot->addr, &info) && info.dli_fbase != NULL) {
        if (info.dli_fname != NULL) {
            image = info.dli_fname;
        }
        offset = (long)((const char *)slot->addr - (const char *)info.dli_fbase);
        if (info.dli_sname != NULL) {
            sym = info.dli_sname;
        }
    }

    pstrace_write_field(fp, test_id);
    fputc('\t', fp);
    pstrace_write_field(fp, image);
    fprintf(fp, "\t0x%lx\t", offset);
    pstrace_write_field(fp, sym);
    fprintf(fp, "\t%llu\n", slot->count);
}

/* ------------------------------------------------------------------ */
/* Public API.                                                         */
/* ------------------------------------------------------------------ */

NOINSTR void pstrace_dump(void) {
    if (g_dumped) {
        return;
    }
    g_dumped = 1;

    const char *out = getenv("PSTRACE_OUTPUT");
    if (out == NULL || out[0] == '\0') {
        out = "pstrace_raw.tsv";
    }
    FILE *fp = fopen(out, "w");
    if (fp == NULL) {
        return;
    }

    fprintf(fp, "test_id\timage\toffset\tdladdr_sym\tcount\n");

    /* Merge every thread's table so each (test, function) is a single summed
     * row. Single-threaded at process end; the lock only guards the traversal
     * against a still-running thread registering a new table. */
    ThreadTable merged = {NULL, 0, 0, NULL};
    pthread_mutex_lock(&g_tables_lock);
    for (ThreadTable *table = g_tables; table != NULL; table = table->next) {
        for (size_t i = 0; i < table->cap; i++) {
            if (table->slots[i].addr != NULL) {
                table_put(&merged, table->slots[i].test_index,
                          table->slots[i].addr, table->slots[i].count);
            }
        }
    }
    pthread_mutex_unlock(&g_tables_lock);

    for (size_t i = 0; i < merged.cap; i++) {
        if (merged.slots[i].addr != NULL) {
            pstrace_write_slot(fp, &merged.slots[i]);
        }
    }
    free(merged.slots);
    fclose(fp);
}

NOINSTR static void pstrace_atexit(void) {
    atexit(pstrace_dump);
}

NOINSTR static void pstrace_register(void) {
    pthread_once(&g_register_once, pstrace_atexit);
}

NOINSTR void pstrace_set_test(const char *test_id) {
    pstrace_register();

    if (test_id == NULL || test_id[0] == '\0') {
        g_current_test = -1;
        return;
    }
    if (g_test_count == g_test_cap) {
        size_t new_cap = (g_test_cap == 0) ? 256 : g_test_cap * 2;
        char **grown = (char **)realloc(g_tests, new_cap * sizeof(char *));
        if (grown == NULL) {
            g_current_test = -1;
            return;
        }
        g_tests = grown;
        g_test_cap = new_cap;
    }
    g_tests[g_test_count] = pstrace_strdup(test_id);
    g_current_test = (int)g_test_count;
    g_test_count++;
}

NOINSTR void __cyg_profile_func_enter(void *this_fn, void *call_site) {
    (void)call_site;
    pstrace_register();
    pstrace_record(g_current_test, this_fn);
}

NOINSTR void __cyg_profile_func_exit(void *this_fn, void *call_site) {
    (void)this_fn;
    (void)call_site;
}
