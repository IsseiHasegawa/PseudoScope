#include <stdlib.h>

static int static_doubled(int value) { return value * 2; }

static int static_orphan(int value) { return value + 100; }

int bench_add(int left, int right) { return left + static_doubled(right); }

int bench_mystery_add(int left, int right) { return left + right + 99; }

int bench_is_even(int value) { return value % 2 == 0; }

int bench_bool_unused(int value) { return value > 0; }

void bench_noop_void(void) {}

void bench_void_smoke(void) {}

int bench_weak_not_zero(int value) { return value; }

int *bench_alloc_id(int size) {
    int *buffer = (int *)malloc((size_t)size * sizeof(int));
    if (buffer == NULL) {
        return NULL;
    }
    for (int index = 0; index < size; index++) {
        buffer[index] = index + 1;
    }
    return buffer;
}

void bench_free_id(int *buffer) { free(buffer); }

double bench_mean_two_doubles(double left, double right) {
    return (left + right) / 2.0;
}

double bench_double_secret(double value) { return value * 3.14159; }
