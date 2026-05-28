"""Tests for pseudoscope_bench — intentionally uneven coverage for PseudoScope."""

import pseudoscope_bench as bench


def test_bench_add() -> None:
    assert bench.bench_add(1, 2) == 5


def test_bench_is_even() -> None:
    assert bench.bench_is_even(4) == 1
    assert bench.bench_is_even(3) == 0


def test_bench_weak_not_zero() -> None:
    assert bench.bench_weak_not_zero(5) != 0


def test_bench_alloc_id() -> None:
    assert bench.bench_alloc_id(3) == 1


def test_bench_mean_two_doubles() -> None:
    assert bench.bench_mean_two_doubles(1.0, 2.0) == 1.5


def test_bench_void_smoke() -> None:
    bench.bench_void_smoke()


def test_cpp_add() -> None:
    assert bench.cpp_add(2, 3) == 5


def test_cpp_weak_not_zero() -> None:
    assert bench.cpp_weak_not_zero(7) != 0


def test_cpp_string_size() -> None:
    assert bench.cpp_string_size("abc") == 3
