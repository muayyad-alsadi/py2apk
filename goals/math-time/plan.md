# Plan: Lower math and time

Design is fixed in [`facts.md`](facts.md). Execute in order. Each
step names the symbol to change. Return early from a failing guard.
Type every new function.

`_bind_module` sits under `emit` and `compile_py2j`, so the compile
suite is the regression net. Run it whole at the end.

## Phase 1 — Math name table

- [x] In `Py2APK/compiler_j/types.py`, add `_MATH_METHOD_ALIASES`
      beside `_STRING_METHOD_ALIASES`. Values are
      `(java_name, arity, force_double)`.
      Same Java name, arity 1, `force_double` false: `sqrt`, `sin`,
      `cos`, `tan`, `asin`, `acos`, `atan`, `exp`, `log`, `log10`,
      `floor`, `ceil`.
      Same Java name, arity 2, `force_double` false: `atan2`,
      `hypot`, `pow`.
      Renames: `radians` → `toRadians` arity 1;
      `degrees` → `toDegrees` arity 1;
      `fabs` → `abs` arity 1 with `force_double` true.
- [x] Extend `_java_method_name(owner, name)`. For
      `owner == "java/lang/Math"`, return the Java name when `name`
      is in the table, otherwise return `name`. Leave the String
      branch as it is.
- [x] Add `math_method(name: str) -> tuple[str, int, bool] | None`
      in `types.py` returning the table row. Callers use this
      instead of reading the dict ad hoc.

## Phase 2 — Widen only a chosen Math signature

- [x] In `_emit_arguments` (`calls.py`), replace the dedicated
      `actual == "I" and wanted == "J"` block with a `_WIDEN`
      lookup. If `(actual, wanted)` is in `_WIDEN`, emit that
      opcode and set `actual = wanted`, then run `_compatible`.
      `i2l` still fires for `I` to `J`. `i2d`, `f2d`, `l2d`, and
      `i2f` fire only when the selected signature asks for them.
- [x] Do not change `_best_signature` or `_types_match`. A global
      `I` to `D` candidate collides with the existing `I` to `J`
      candidate.
- [x] Add `_math_signature(self, owner, java_name, types, call,
      force_double: bool) -> _MethodSignature | None` on
      `_CallsMixin`. Owner other than `java/lang/Math` returns
      `None` immediately. `force_double` true skips the exact
      lookup. Otherwise call `_find_method` with `types`. On a
      miss, retry with each `I` and `F` replaced by `D` when that
      tuple differs. `static=True`.
- [x] In `_call`'s static type-reference branch, set
      `java_name = _java_method_name(owner, node.func.attr)`.
      When `owner == "java/lang/Math"`, require a table row; a
      missing row raises
      `unknown method signature java/lang/Math.{attr}` before
      `_emit_constructor`. Look up with `_math_signature` using
      that row's `force_double`. Invoke
      `invokestatic {signature.owner}/{java_name}{descriptor}`.
      Pass the signature to `_emit_arguments` so `f2d` / `i2d`
      land in front of `(D)` parameters. Return `signature.result`
      (`D` for this table).
- [x] In `_static_binding`, when `imported_functions` has the name
      and the owner is `java/lang/Math`, call `_math_signature`
      with `force_double` true iff that local name is in
      `self.math_force_double` (phase 3). The stored method name
      is already the Java name (`abs` for `fabs`). A miss stays
      `None`.
- [x] In `_builtin_call`, when the name is in `imported_functions`
      and the owner is `java/lang/Math` and `_static_binding`
      returned `None`, raise
      `unknown method signature java/lang/Math.{method}`.
      Do not continue into `unsupported builtin`.
- [x] Leave the `abs` / `min` / `max` blocks in `_builtin_call`
      and `_builtin_result` on their exact-type path.

## Phase 3 — Bind `math` in the module

- [x] In `_bind_module`, after computing `internal`, when
      `module == "math"` and `"math" not in self.project`: set
      `self.imports[local_name] = "java/lang/Math"` and return
      `True`. A project `math` key falls through to today's
      project bind. `local_name` is already the alias (`m` in
      `import math as m`).
- [x] In `_record_from_import`, before `_bind_module_names`, when
      `node.module == "math"`: if `"math" in self.project`, skip
      this special case. Otherwise, for each alias, `local =
      alias.asname or alias.name`. Look up `math_method`. A miss
      raises `unsupported import math.{alias.name}`. A hit stores
      `imported_functions[local] = ("java/lang/Math", java_name)`.
      For `fabs`, also record that calls force `abs(D)D`. A small
      `self.math_force_double: set[str]` of local names is enough;
      `_static_binding` passes `local in self.math_force_double`.
      Initialize the set in `_CompilerBase.__init__`
      (`base.py`). Then `return`, so the class-path fallback
      cannot bind `math/sqrt` as a class.
- [x] Do not edit `_collect_imports`. It only feeds annotations.
      Calls re-read imports in `emit` / `_record_import`.
- [x] Do not add `Py2APK/stdlib/math.py`. Do not special-case
      `_closure`. Host `math` is not a `.py`.

## Phase 4 — `time` fallback

- [x] Add `Py2APK/stdlib/time.py` with full annotations:

      `from java.lang import System, Thread`

      `def time() -> float` returns
      `float(System.currentTimeMillis()) / 1000.0`.

      `def sleep(seconds: int) -> None` calls
      `Thread.sleep(seconds * 1000)`.

      No other functions. No `try`. Module docstring states
      millisecond resolution and that `time()` is `l2f` of
      `currentTimeMillis`, then `fdiv` by `1000.0`.
- [x] `touch Py2APK/stdlib`.
- [x] In `_runtime_catalog` (`stdlib_cache.py`) append
      `System.currentTimeMillis ()J` and `Thread.sleep (J)V`,
      both `is_static=1`, opcode `invokestatic`. Match the tuple
      shape of the existing `Math.abs` row.
- [x] No compiler branch for the name `time`. Once the file is
      indexed, `_bind_module` and `_bind_module_names` already
      bind `import time` and `from time import time`.

## Phase 5 — Catalog rows and tests

- [x] In `_alias_catalog` (`tests/_compile_helpers.py`) add
      `invokestatic` rows, owner `java/lang/Math`, result `D`,
      params `D` or `DD` as the table says: every shipped Java
      name including `toRadians`, `toDegrees`, and `abs` with
      params `D` (keep the existing `abs(I)I` row). Add the
      `System` and `Thread` rows from phase 4.
- [x] If a fetched project `jars.db` lacks one of those `Math`
      methods, drop that Python name from the table, the test
      row, and `docs/covered.md` together. Do not invent a
      Python body for it.
- [x] In `tests/test_cli_compile_stdlib.py`, keep the module-level
      CPython `import time`. Add methods on `AliasLoweringTests`:
      - `import math` / `x: double = math.sqrt(4.0)` via
        `_compiled_class`. Jasmin contains `f2d` and
        `invokestatic java/lang/Math/sqrt(D)D`. No `d2f`.
      - `math.sqrt` of an `int` contains `i2d` and the same
        invoke.
      - `import math as m` / `m.pow(2.0, 3.0)` contains
        `Math/pow(DD)D`.
      - `from math import radians as rad` /
        `rad` of a `double` contains `Math/toRadians(D)D`
        and no extra conversion opcode.
      - `from math import fabs` of an `int` contains
        `Math/abs(D)D` and `i2d`, and does not contain
        `Math/abs(I)I`.
      - bare `abs` of an `int` still contains `Math/abs(I)I`.
      - `from math import factorial` raises
        `JasminCompilerError` with
        `unsupported import math.factorial`.
      - `math.factorial(1.0)` raises
        `unknown method signature java/lang/Math.factorial`.
      - `import time` / `t: float = time.time()` via
        `_compiled_with_stdlib` contains
        `invokestatic time/time()F`.
      - `from time import sleep` / `sleep(1)` contains
        `invokestatic time/sleep(I)V`.
      - `_compiled_named` on `Py2APK/stdlib/time.py` contains
        `System/currentTimeMillis()J`, `l2f`, `fdiv`,
        `Thread/sleep(J)V`, and `i2l`.
- [x] A project `src/math.py` still binds as that file. One
      `resolve_module("math", project)` assert beside the
      existing `bisect` assert in `_assert_stdlib_fallback` is
      enough: a `src/math.py` wins, and an empty project does
      not resolve `math` to a fallback `.py`.

## Phase 6 — Docs

- [x] `docs/not-covered.md`, the sentence that lists `math`,
      `time`, `base64`, and `re.compile`: drop `math` and `time`.
      Leave `base64` and `re.compile`.
- [x] `ROADMAP.md` "Names that stay Java", the sentence that
      calls `math`, `time`, `base64`, and `re.compile` later
      aliases: same edit.
- [x] `docs/covered.md` "Imports of Python modules": `math` is
      `java/lang/Math` for the allow-list, results are `double`,
      `radians` / `degrees` / `fabs` are the renames, and a
      project `src/math.py` wins. `time.time` and `time.sleep`
      are the packaged fallback. State the `l2f` limit and that
      `sleep` takes `int` seconds.
- [ ] Run `ripwire . --doc-drift --legend=compact` from the repo
      root. Fix anchors until `drift="0"`.

## Phase 7 — Gates, evidence, registry

- [x] From the repo root, exit code 0:
      `python3 -m unittest discover -s tests -v`
      `just check`
      `just lint`
- [x] Arithmetic tests still pass. They cover `Math.floor` inside
      float `//`, which this goal must not retarget.
- [x] Write `goals/math-time/evidence.md` with a standalone
      `Verdict: PASS` line and the sila memory-writes block from
      `goal.md`.
- [ ] Check every box in this file. Commit with
      `(goals/math-time/goal.md)` or `[goal: math-time]` under
      `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:`.
      Certify, run `sila goals`, and confirm
      `unverified_count: 0` with a clean `git status`.

## Acceptance

- Shipped math invokes use the Java name and a `(D)D` or `(DD)D`
  descriptor. `I` and `F` arguments show `i2d` or `f2d`.
  `double` arguments do not.
- `fabs` is `abs(D)D` even for an int. Builtin `abs(int)` is
  still `abs(I)I`.
- Unknown math imports fail at the import. Unknown math calls
  fail at the call and do not emit a constructor.
- `time.time` is `()F` on the caller and `currentTimeMillis` plus
  `l2f` plus `fdiv` in `time.py`. `time.sleep` is `(I)V` on the
  caller and `i2l` plus `Thread/sleep(J)V` in `time.py`.
- `re`, `base64`, math constants, and `stdlib/math.py` are
  unchanged.
