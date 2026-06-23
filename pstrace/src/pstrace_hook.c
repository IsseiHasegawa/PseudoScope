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
 * Every function here is no_instrument_function so the hook never instruments
 * itself (which would recurse infinitely).
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE /* dladdr on glibc; harmless elsewhere */
#endif
#include "pstrace.h"

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NOINSTR __attribute__((no_instrument_function))

/* ------------------------------------------------------------------ */
/* Interned test ids: current span index -> id string.                */
/* ------------------------------------------------------------------ */

static char **g_tests = NULL;
static size_t g_test_count = 0;
static size_t g_test_cap = 0;
static int g_current_test = -1; /* -1 = before any test (startup/collection) */

static int g_registered = 0;
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
/* ------------------------------------------------------------------ */

typedef struct {
    const void *addr; /* NULL marks an empty slot */
    int test_index;
    unsigned long long count;
} Slot;

static Slot *g_slots = NULL;
static size_t g_cap = 0; /* always a power of two */
static size_t g_used = 0;

NOINSTR static size_t pstrace_hash(int test_index, const void *addr) {
    size_t h = (size_t)(uintptr_t)addr;
    h ^= (size_t)test_index * (size_t)0x9e3779b97f4a7c15ULL;
    h ^= h >> 29;
    h *= (size_t)0xbf58476d1ce4e5b9ULL;
    h ^= h >> 32;
    return h;
}

NOINSTR static void pstrace_grow(void) {
    size_t new_cap = (g_cap == 0) ? 1024 : g_cap * 2;
    Slot *new_slots = (Slot *)calloc(new_cap, sizeof(Slot));
    if (new_slots == NULL) {
        return; /* drop silently rather than crash the target process */
    }
    size_t mask = new_cap - 1;
    for (size_t i = 0; i < g_cap; i++) {
        if (g_slots[i].addr == NULL) {
            continue;
        }
        size_t h = pstrace_hash(g_slots[i].test_index, g_slots[i].addr) & mask;
        while (new_slots[h].addr != NULL) {
            h = (h + 1) & mask;
        }
        new_slots[h] = g_slots[i];
    }
    free(g_slots);
    g_slots = new_slots;
    g_cap = new_cap;
}

NOINSTR static void pstrace_record(int test_index, const void *addr) {
    if (addr == NULL) {
        return;
    }
    if (g_cap == 0 || g_used * 2 >= g_cap) {
        pstrace_grow();
        if (g_cap == 0) {
            return; /* allocation failed */
        }
    }
    size_t mask = g_cap - 1;
    size_t h = pstrace_hash(test_index, addr) & mask;
    while (g_slots[h].addr != NULL) {
        if (g_slots[h].addr == addr && g_slots[h].test_index == test_index) {
            g_slots[h].count++;
            return;
        }
        h = (h + 1) & mask;
    }
    g_slots[h].addr = addr;
    g_slots[h].test_index = test_index;
    g_slots[h].count = 1;
    g_used++;
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
    for (size_t i = 0; i < g_cap; i++) {
        if (g_slots[i].addr == NULL) {
            continue;
        }
        int ti = g_slots[i].test_index;
        const char *test_id =
            (ti >= 0 && (size_t)ti < g_test_count) ? g_tests[ti] : "(startup)";

        Dl_info info;
        const char *image = "?";
        long offset = 0;
        const char *sym = "?";
        if (dladdr(g_slots[i].addr, &info) && info.dli_fbase != NULL) {
            if (info.dli_fname != NULL) {
                image = info.dli_fname;
            }
            offset = (long)((const char *)g_slots[i].addr -
                            (const char *)info.dli_fbase);
            if (info.dli_sname != NULL) {
                sym = info.dli_sname;
            }
        }

        pstrace_write_field(fp, test_id);
        fputc('\t', fp);
        pstrace_write_field(fp, image);
        fprintf(fp, "\t0x%lx\t", offset);
        pstrace_write_field(fp, sym);
        fprintf(fp, "\t%llu\n", g_slots[i].count);
    }
    fclose(fp);
}

NOINSTR static void pstrace_register(void) {
    if (!g_registered) {
        g_registered = 1;
        atexit(pstrace_dump);
    }
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
