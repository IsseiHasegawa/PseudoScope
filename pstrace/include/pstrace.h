#ifndef PSTRACE_H
#define PSTRACE_H

/*
 * pstrace public API.
 *
 * The target C/C++ sources are compiled with -finstrument-functions, so the
 * compiler calls __cyg_profile_func_enter() on entry to every instrumented
 * function. pstrace records which function ran and attributes it to the
 * "current test" set from Python via pstrace_set_test().
 */

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Mark the start of a test (or any logical span). Every instrumented function
 * entry after this call is attributed to test_id until the next call. Pass a
 * stable, unique string such as a pytest nodeid. A NULL or empty id is treated
 * as "no test".
 */
void pstrace_set_test(const char *test_id);

/*
 * Write the collected (test_id, function) table to $PSTRACE_OUTPUT (default
 * "pstrace_raw.tsv"). Runs automatically at process exit; calling it more than
 * once is a no-op after the first.
 */
void pstrace_dump(void);

#ifdef __cplusplus
}
#endif

#endif /* PSTRACE_H */
