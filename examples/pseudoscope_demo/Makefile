CC ?= gcc
CFLAGS ?= -Wall -Wextra -std=c11
CPPFLAGS += -Isrc

TEST_BIN := test_runner

.PHONY: all test clean

all: $(TEST_BIN)

# Re-link whenever calc.c changes (important for pseudoscope restore/mutate cycles).
$(TEST_BIN): tests/test_runner.c src/calc.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $^

test: $(TEST_BIN)
	./$(TEST_BIN)

clean:
	rm -f $(TEST_BIN)
