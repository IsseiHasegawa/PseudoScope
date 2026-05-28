#include <cstring>

extern "C" {

int cpp_add(int left, int right) { return left + right; }

int cpp_mystery(int left, int right) { return left - right + 42; }

int cpp_weak_not_zero(int value) { return value; }

int cpp_string_size(const char *text) {
    if (text == nullptr) {
        return 0;
    }
    return (int)std::strlen(text);
}

}
