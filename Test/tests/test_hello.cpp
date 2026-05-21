#include "hello.hpp"

#include <cassert>
#include <cmath>
#include <iostream>
#include <sstream>
#include <string>

namespace {

void expect_eq(int actual, int expected, const char* name) {
    if (actual != expected) {
        std::cerr << "FAIL: " << name << " expected " << expected
                  << ", got " << actual << std::endl;
        std::exit(1);
    }
}

void expect_near(double actual, double expected, const char* name) {
    if (std::fabs(actual - expected) > 1e-9) {
        std::cerr << "FAIL: " << name << " expected " << expected
                  << ", got " << actual << std::endl;
        std::exit(1);
    }
}

void expect_str(const std::string& actual, const std::string& expected,
                const char* name) {
    if (actual != expected) {
        std::cerr << "FAIL: " << name << " expected \"" << expected
                  << "\", got \"" << actual << "\"" << std::endl;
        std::exit(1);
    }
}

std::string capture_hello_output() {
    std::ostringstream buffer;
    std::streambuf* old = std::cout.rdbuf(buffer.rdbuf());
    hello();
    std::cout.rdbuf(old);
    return buffer.str();
}

}  // namespace

int main() {
    expect_str(capture_hello_output(), "Hello world\n", "hello() output");

    expect_eq(TwoSum(3, 4), 7, "TwoSum(3, 4)");
    expect_eq(TwoSum(-2, 5), 3, "TwoSum(-2, 5)");
    expect_eq(TwoSum(0, 0), 0, "TwoSum(0, 0)");

    expect_near(FTwoSum(1.5, 2.5), 4.0, "FTwoSum(1.5, 2.5)");
    expect_near(FTwoSum(-1.0, 0.5), -0.5, "FTwoSum(-1.0, 0.5)");

    expect_str(ReturnString("test"), "test", "ReturnString(\"test\")");
    expect_str(ReturnString(""), "", "ReturnString(\"\")");

    std::cout << "All tests passed." << std::endl;
    return 0;
}
