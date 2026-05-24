# pseudoscope_demo

Minimal C project for trying [pseudoscope.py](../../pseudoscope.py).

## Design

| Function | File | Tested? | Expected sweep result |
|----------|------|---------|------------------------|
| `add` | `calc.c` | Yes | **fail** (tests assert correct sums) |
| `multiply` | `calc.c` | Yes | **fail** |
| `dead_code_transform` | `calc.c` | No | **pass** (never called → pseudo-tested) |
| `unused_scale` | `calc.c` | No | **pass** |

Expected **PI ≈ 50%** (2 of 4 functions pass: both mutants survive for the unused functions).

## Build and test

```bash
cd examples/pseudoscope_demo
make test
```

## Run PseudoScope (from `PesudoScope/`)

```bash
cd ../..   # PesudoScope root

python3 pseudoscope.py sweep \
  --workdir examples/pseudoscope_demo \
  --build-command "make -B" \
  --test-command "make test" \
  --out examples/pseudoscope_demo/.pseudoscope/results.csv
```

Use `make -B` so the test binary is relinked after every restore/mutation (plain `make` may reuse a stale `test_runner`).
```

`void` functions are not present here; each `int` function gets two mutants (`return 0` / `return 1`).
