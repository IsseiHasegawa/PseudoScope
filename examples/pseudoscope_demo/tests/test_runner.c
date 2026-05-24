#include "calc.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    assert(add(2, 3) == 5);
    assert(add(-1, 1) == 0);
    assert(multiply(4, 5) == 20);
    assert(multiply(0, 100) == 0);

    printf("All tests passed.\n");
    return 0;
}
