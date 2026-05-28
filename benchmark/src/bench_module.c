#define PY_SSIZE_T_CLEAN
#include <Python.h>

extern int bench_add(int left, int right);
extern int bench_mystery_add(int left, int right);
extern int bench_is_even(int value);
extern int bench_bool_unused(int value);
extern void bench_noop_void(void);
extern void bench_void_smoke(void);
extern int bench_weak_not_zero(int value);
extern int *bench_alloc_id(int size);
extern void bench_free_id(int *buffer);
extern double bench_mean_two_doubles(double left, double right);
extern double bench_double_secret(double value);

extern int cpp_add(int left, int right);
extern int cpp_mystery(int left, int right);
extern int cpp_weak_not_zero(int value);
extern int cpp_string_size(const char *text);

static PyObject *py_bench_add(PyObject *self, PyObject *args) {
    int left = 0;
    int right = 0;
    if (!PyArg_ParseTuple(args, "ii", &left, &right)) {
        return NULL;
    }
    return PyLong_FromLong(bench_add(left, right));
}

static PyObject *py_bench_mystery_add(PyObject *self, PyObject *args) {
    int left = 0;
    int right = 0;
    if (!PyArg_ParseTuple(args, "ii", &left, &right)) {
        return NULL;
    }
    return PyLong_FromLong(bench_mystery_add(left, right));
}

static PyObject *py_bench_is_even(PyObject *self, PyObject *args) {
    int value = 0;
    if (!PyArg_ParseTuple(args, "i", &value)) {
        return NULL;
    }
    return PyLong_FromLong(bench_is_even(value));
}

static PyObject *py_bench_bool_unused(PyObject *self, PyObject *args) {
    int value = 0;
    if (!PyArg_ParseTuple(args, "i", &value)) {
        return NULL;
    }
    return PyLong_FromLong(bench_bool_unused(value));
}

static PyObject *py_bench_noop_void(PyObject *self, PyObject *args) {
    if (!PyArg_ParseTuple(args, "")) {
        return NULL;
    }
    bench_noop_void();
    Py_RETURN_NONE;
}

static PyObject *py_bench_void_smoke(PyObject *self, PyObject *args) {
    if (!PyArg_ParseTuple(args, "")) {
        return NULL;
    }
    bench_void_smoke();
    Py_RETURN_NONE;
}

static PyObject *py_bench_weak_not_zero(PyObject *self, PyObject *args) {
    int value = 0;
    if (!PyArg_ParseTuple(args, "i", &value)) {
        return NULL;
    }
    return PyLong_FromLong(bench_weak_not_zero(value));
}

static PyObject *py_bench_alloc_id(PyObject *self, PyObject *args) {
    int size = 0;
    if (!PyArg_ParseTuple(args, "i", &size)) {
        return NULL;
    }
    int *buffer = bench_alloc_id(size);
    if (buffer == NULL) {
        return PyLong_FromLong(0);
    }
    long first = (long)buffer[0];
    bench_free_id(buffer);
    return PyLong_FromLong(first);
}

static PyObject *py_bench_mean_two_doubles(PyObject *self, PyObject *args) {
    double left = 0.0;
    double right = 0.0;
    if (!PyArg_ParseTuple(args, "dd", &left, &right)) {
        return NULL;
    }
    return PyFloat_FromDouble(bench_mean_two_doubles(left, right));
}

static PyObject *py_bench_double_secret(PyObject *self, PyObject *args) {
    double value = 0.0;
    if (!PyArg_ParseTuple(args, "d", &value)) {
        return NULL;
    }
    return PyFloat_FromDouble(bench_double_secret(value));
}

static PyObject *py_cpp_add(PyObject *self, PyObject *args) {
    int left = 0;
    int right = 0;
    if (!PyArg_ParseTuple(args, "ii", &left, &right)) {
        return NULL;
    }
    return PyLong_FromLong(cpp_add(left, right));
}

static PyObject *py_cpp_mystery(PyObject *self, PyObject *args) {
    int left = 0;
    int right = 0;
    if (!PyArg_ParseTuple(args, "ii", &left, &right)) {
        return NULL;
    }
    return PyLong_FromLong(cpp_mystery(left, right));
}

static PyObject *py_cpp_weak_not_zero(PyObject *self, PyObject *args) {
    int value = 0;
    if (!PyArg_ParseTuple(args, "i", &value)) {
        return NULL;
    }
    return PyLong_FromLong(cpp_weak_not_zero(value));
}

static PyObject *py_cpp_string_size(PyObject *self, PyObject *args) {
    const char *text = NULL;
    if (!PyArg_ParseTuple(args, "s", &text)) {
        return NULL;
    }
    return PyLong_FromLong(cpp_string_size(text));
}

static PyMethodDef BenchMethods[] = {
    {"bench_add", py_bench_add, METH_VARARGS, "Add with static helper"},
    {"bench_mystery_add", py_bench_mystery_add, METH_VARARGS, "Unused in tests"},
    {"bench_is_even", py_bench_is_even, METH_VARARGS, "Parity check"},
    {"bench_bool_unused", py_bench_bool_unused, METH_VARARGS, "Unused in tests"},
    {"bench_noop_void", py_bench_noop_void, METH_VARARGS, "Unused void"},
    {"bench_void_smoke", py_bench_void_smoke, METH_VARARGS, "Called without assertion"},
    {"bench_weak_not_zero", py_bench_weak_not_zero, METH_VARARGS, "Weak inequality test"},
    {"bench_alloc_id", py_bench_alloc_id, METH_VARARGS, "Alloc helper"},
    {"bench_mean_two_doubles", py_bench_mean_two_doubles, METH_VARARGS, "Mean of two doubles"},
    {"bench_double_secret", py_bench_double_secret, METH_VARARGS, "Unused double"},
    {"cpp_add", py_cpp_add, METH_VARARGS, "C++ add"},
    {"cpp_mystery", py_cpp_mystery, METH_VARARGS, "Unused C++"},
    {"cpp_weak_not_zero", py_cpp_weak_not_zero, METH_VARARGS, "Weak C++ check"},
    {"cpp_string_size", py_cpp_string_size, METH_VARARGS, "C++ string length"},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef benchmodule = {
    PyModuleDef_HEAD_INIT,
    "pseudoscope_bench",
    "PseudoScope benchmark extension",
    -1,
    BenchMethods,
};

PyMODINIT_FUNC PyInit_pseudoscope_bench(void) {
    return PyModule_Create(&benchmodule);
}
