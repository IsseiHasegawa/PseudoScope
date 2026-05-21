CXX = c++
CXXFLAGS = -std=c++17 -Wall -Wextra -I src

HELLO_SRC = src/hello.cpp

.PHONY: all hello test clean

all: hello test

hello: src/main.cpp $(HELLO_SRC)
	$(CXX) $(CXXFLAGS) -o $@ src/main.cpp $(HELLO_SRC)

test: test_hello
	./test_hello

test_hello: tests/test_hello.cpp $(HELLO_SRC)
	$(CXX) $(CXXFLAGS) -o $@ tests/test_hello.cpp $(HELLO_SRC)

clean:
	rm -f hello test_hello
