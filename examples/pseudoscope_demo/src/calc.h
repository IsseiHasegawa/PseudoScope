#ifndef CALC_H
#define CALC_H

/* Covered by tests — mutants should make tests fail (killed). */
int add(int a, int b);
int multiply(int a, int b);

/*
 * Not referenced from tests — mutants should survive (pseudo-tested).
 * These exist to raise PI in a pseudoscope trial run.
 */
int dead_code_transform(int x);
int unused_scale(int value);

#endif
