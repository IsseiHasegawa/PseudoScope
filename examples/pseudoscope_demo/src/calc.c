#include "calc.h"

int add(int a, int b)
{
    return a + b;
}

int multiply(int a, int b)
{
    return a * b;
}

int dead_code_transform(int x)
{
    return x + 100;
}

int unused_scale(int value)
{
    return value * 3;
}
