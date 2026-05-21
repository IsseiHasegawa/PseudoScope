#include "hello.hpp"

#include <iostream>

int main() {
    hello();

    int sum = TwoSum(3, 4);
    std::cout << "TwoSum(3, 4) = " << sum << std::endl;

    double fsum = FTwoSum(1.5, 2.5);
    std::cout << "FTwoSum(1.5, 2.5) = " << fsum << std::endl;

    std::string result = ReturnString("test");
    std::cout << "ReturnString(\"test\") = " << result << std::endl;

    return 0;
}
