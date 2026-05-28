from setuptools import Extension, setup

bench_extension = Extension(
    "pseudoscope_bench",
    sources=[
        "src/bench_module.c",
        "src/benchmark_ops.c",
        "src/benchmark_cpp.cpp",
    ],
    language="c++",
)

setup(
    name="pseudoscope-benchmark",
    version="0.1.0",
    ext_modules=[bench_extension],
    python_requires=">=3.10",
)
