# Windows / MSVC support (design)

pstrace targets Clang/GCC on Linux/macOS. Windows is not supported yet. This
document is the implementation plan so it can be picked up on a Windows host.
Nothing here is validated: there is no Windows toolchain in the current
environment, so the compile-flag mapping (below) is unit-tested at the argv level
only, and everything else is design.

## Two toolchains, two stories

| Toolchain | Instrumentation | Hook needed |
|---|---|---|
| **clang-cl** (LLVM's MSVC-compatible driver) | `-finstrument-functions` works (it is Clang) | the existing `__cyg_profile_func_enter/exit` hook |
| **MSVC `cl.exe`** | no `-finstrument-functions`; `/Gh` and `/GH` instead | a new `_penter` / `_pexit` hook (naked asm) |
| mingw gcc | `-finstrument-functions` works | the existing hook |

So Windows is not blocked in principle. clang-cl (and mingw) reuse the current
hook; only real `cl.exe` needs the `_penter`/`_pexit` variant.

## Status

- **Done (unit-tested):** `pstrace/wrapper.py` detects `cl` / `clang-cl` and maps
  the instrumentation flags: `cl` -> `/Gh /GH /Od /Zi`; `clang-cl` ->
  `/clang:-finstrument-functions /clang:-fno-omit-frame-pointer /Od /Zi`. Include
  dirs become `/I<dir>`; a DLL link (`/LD`, `/DLL`, or a `.dll`/`.pyd` output)
  gets the hook object; build-system probes are passed through. See
  `tests/test_wrapper.py` (the MSVC / clang-cl cases).
- **Not done:** the `_penter` hook, Windows hook delivery/linking, Windows
  symbolization, the driver's Windows build, and any end-to-end run.

## Pieces to implement

### 1. Hook

- **clang-cl / mingw:** the current `src/pstrace_hook.c` works, except its
  `dladdr` call. Windows has no `dladdr`; guard it with `#ifdef _WIN32` and use
  `GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | ..._UNCHANGED_REFCOUNT,
  addr, &hmod)` + `GetModuleFileNameA(hmod, ...)` for the image path, with the
  offset = `addr - (module base)`. The rest (per-thread tables, TSV) is portable.
- **MSVC `cl.exe`:** `/Gh` inserts `call _penter` as the first instruction of
  each instrumented function; `/GH` inserts `call _pexit` at exit. Unlike
  `__cyg_profile_func_enter(this_fn, call_site)`, no arguments are passed: on
  entry the return address on the stack points just past the `call _penter`,
  i.e. inside the current function, which identifies it. `_penter` must be a
  **naked** function that saves every register it could clobber, reads the return
  address, records it (route into the existing per-thread `table_put`), restores
  registers, and returns. This is per-architecture assembly (x64 / x86 / arm64)
  and is the single most crash-prone part; get register preservation exactly
  right. A minimal x64 sketch:

  ```c
  // Read [rsp] (return addr) -> record; preserve all volatile regs + xmm.
  __declspec(naked) void _penter(void) { /* push volatiles; mov rcx,[rsp+..];
      call pstrace_record_penter; pop volatiles; ret */ }
  ```

### 2. Hook delivery / linking

No `LD_PRELOAD` / `DYLD_INSERT_LIBRARIES` on Windows, so the preload mode does not
apply; use **link injection** (put the hook `.obj` in the `.pyd`). Complication:
Windows separates compile (`cl`) and link (`link.exe`); setuptools' MSVCCompiler
calls `link.exe` directly, which the `CC` wrapper does not intercept. Options:

- Wrap the linker too (point the build's linker at a `pstrace-link` shim that
  appends the hook `.obj`), or
- Ship the hook as a static `.lib` and add it via the extension's link inputs, or
- Use clang-cl as the driver for both compile and link (it can link), so the
  single `CC` wrapper covers both.

`pstrace_set_test` / `pstrace_dump` must be exported from the `.pyd`
(`__declspec(dllexport)` or a `.def`) so the plugin's `ctypes` can find them.

### 3. Symbolization

`atos` / `addr2line` do not exist on Windows; debug info is in **PDB** files. Add
a Windows branch to `pstrace/symbolize.py` that resolves `(image, offset)` to
`function` + `file:line` via **`llvm-symbolizer`** (reads PDB and is already an
LLVM tool) or DbgHelp `SymFromAddr` / `SymGetLineFromAddr64`. Build with `/Zi`
(done in the flag mapping) and keep the PDB next to the `.pyd`.

### 4. Driver

`pstrace/driver.py`: build the hook with `cl` / `clang-cl` (`.obj`, and a `.lib`
if using the static-lib delivery), drop `-pthread` / `-ldl` (Windows), force
`--hook-mode link` (no preload), and derive `real_cc` from the target
interpreter's MSVC config. `LDSHARED` handling is POSIX-only and is skipped.

### 5. Plugin

`pstrace/plugin.py` already uses `ctypes.CDLL`, which works on Windows; verify
`WinDLL` is not required and that `PSTRACE_MODULE` -> `.pyd` -> exported
`pstrace_set_test` resolves.

## Validation plan (needs a Windows host)

1. Windows + Python + MSVC Build Tools (and separately LLVM for clang-cl).
2. A minimal C extension (the `mini-ext` shape).
3. clang-cl first (reuses the existing hook): driver-built, traced pytest,
   llvm-symbolizer -> coverage map. Confirms flags + delivery + symbolize.
4. Then `cl.exe` with the `_penter` hook: same, plus a multi-thread stress like
   the Linux `leaf` test to prove the naked `_penter` preserves registers.
5. Compare the coverage map to a known-good so selection stays correct.

## Risks

- `_penter` register preservation (a single missed volatile register crashes the
  target); per-arch asm; `__declspec(naked)` is unavailable on arm64 MSVC (use a
  separate `.asm`).
- Linker interception on Windows (compile and link are separate programs).
- Exporting the hook symbols from the `.pyd`.
- PDB availability and `llvm-symbolizer` presence.
