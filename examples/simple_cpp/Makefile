CXX = c++
CXXFLAGS = -std=c++17 -Wall -Wextra -I src

.PHONY: all test clean

all: test

test_math: tests/test_math.cpp src/math.cpp
	$(CXX) $(CXXFLAGS) -o $@ tests/test_math.cpp src/math.cpp

test: test_math
	./test_math

clean:
	rm -f test_math
