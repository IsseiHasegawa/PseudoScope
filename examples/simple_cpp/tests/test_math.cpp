#include "math.hpp"

#include <cassert>
#include <cmath>
#include <iostream>

int main() {
    assert(add(2, 3) == 5);
    assert(subtract(10, 4) == 6);
    assert(is_positive(1));
    assert(!is_positive(-1));
    assert(std::fabs(average(2.0, 4.0) - 3.0) < 1e-9);

    std::cout << "All tests passed." << std::endl;
    return 0;
}
