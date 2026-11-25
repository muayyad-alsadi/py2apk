# Facts: Lower math and time

Grounded in the compiler as of this writing. Line numbers are anchors;
symbols are the contract.

## Current state (verified)

- `_bind_module` (`Py2APK/compiler_j/module.py`, `_bind_module`)
  returns true only when `module.replace(".", "/")` is a key in
  `self.project`. It then sets `self.imports[local_name]`. It does
  not consult the jar catalog.
- `_java_class` in the same file has no callers. Do not revive it
  for this goal.
- `import Name` goes through `_record_import`. `local_name` is
  `alias.asname`, or the last dotted piece when there is no alias.
  A failed `_bind_module` raises `_missing_import`.
- `from module import name` goes through `_record_from_import`, then
  `_bind_module_names`. A project static method is stored in
  `self.imported_functions[local] = (internal, method_name)`.
  Anything else in that project module must be a class already in
  `self.project`. If the module is not in the project, the fallback
  writes `self.imports[local] = "{module}/{name}"` with no catalog
  check. That is how `from java.lang import Math` becomes the type
  `java/lang/Math`.
- `resolve_module` (`Py2APK/module_resolve.py`) looks for a `.py`
  under project `src`, then `Py2APK/stdlib`, then the host stdlib.
  A C extension is a miss. On this machine `math.__file__` is a
  `.so` and `time` is a builtin (`__file__` is `None`). `_closure`
  in `Py2APK/cli_compile.py` will not pull a host `math.py`.
- There is no `Py2APK/stdlib/math.py` and no `Py2APK/stdlib/time.py`.
  `import math` and `import time` therefore fail in `_bind_module`.
- A packaged fallback's internal name is its path under
  `stdlib_root()` (`module_internal`). `Py2APK/stdlib/time.py` indexes
  as project key `time`. Module functions become `invokestatic`
  methods on that name (`_index_functions`).
- `self.imports` makes a name a type reference
  (`_is_type_reference` in `calls.py`). `math.sqrt(...)` then takes
  the static branch of `_call`. That branch looks up `node.func.attr`
  and, on a miss, calls `_emit_constructor` on `{owner}${attr}`.
  It does not call `_java_method_name`.
- `_java_method_name` (`types.py`) renames only `java/lang/String`
  methods, via `_STRING_METHOD_ALIASES`. Instance calls use it.
  Static calls do not.
- `_compatible` (`base.py`) is true for primitives only when the
  descriptors are equal. `F` is not compatible with `D`.
- `_best_signature` prefers an exact parameter tuple. Its only
  primitive widening is `I` to `J`. `_emit_arguments` inserts `i2l`
  for that pair and otherwise rejects a primitive mismatch.
- `_WIDEN` in `types.py` is `I→J i2l`, `I→F i2f`, `I→D i2d`,
  `J→D l2d`, `F→D f2d`. Compare code reads it. Call selection does
  not. Adding those pairs to every `_best_signature` search would
  make `I→J` and `I→D` both compatible and return no match.
- `_BUILTIN_TYPES`: `int` is `I`, `float` is `F`, `double` is `D`.
  A float literal types as `F` (`_type_of_constant`). There is no
  `long` annotation.
- `float(expr)` / `double(expr)` on a non-string use `_numeric_cast`
  and `_NUMERIC_CASTS`. `J` to `F` is `l2f`. `D` to `F` is `d2f`.
- Bare `abs` / `min` / `max` lower in `_builtin_call` to
  `java/lang/Math` with the argument descriptors unchanged. They
  require an exact catalog row (`I`, `F`, or `D`). They are not
  `math.abs`.
- Float `/` is `fdiv` after `i2f` (`_emit_float_binop`,
  `_emit_as_float`). That helper rejects `D` and `J`. Int `*` stays
  `I`. `D` and `J` binops are out of scope for the arithmetic work
  (`goals/true-division/facts.md`).
- `ensure_stdlib_jasmin` compiles every `Py2APK/stdlib/**/*.py`
  against `_runtime_catalog` only, with `min_api` left unset.
  Unit helpers `_compiled_class`, `_compiled_named`, and
  `_compiled_with_stdlib` use `_alias_catalog` instead.
- `_alias_catalog` already has `Math.abs(I)I`, `min(II)I`,
  `max(II)I`, and `floor(D)D`. It has no `sqrt`, no `System`, and
  no `Thread.sleep`. `_runtime_catalog` has `Math.abs(I)I` only.
- `docs/not-covered.md` (the paragraph under "Calls and types that
  are not aliases") and `ROADMAP.md` ("Names that stay Java") list
  `math`, `time`, `base64`, and `re.compile` as not lowered yet.
- `docs/covered.md` ("Imports of Python modules") documents the
  src / packaged fallback / host search and the current fallback
  list. `math` and `time` are absent.
- `tests/test_cli_compile_stdlib.py` imports CPython `time` at
  module level for the cache mtime check. That import is the test
  process, not a fixture to delete.
- The compiler emits no `.throws` clause. Nothing in `compiler_j`
  mentions `InterruptedException`.

## Two bindings

`math` is a type alias to the Java class. `time` is a real fallback
module because no single Java class has CPython's `time.time` /
`time.sleep` signatures.

- `import math`: `imports[local] = "java/lang/Math"` when
  the project has no `math` key.
- `from math import sqrt`: `imported_functions[local] =
  ("java/lang/Math", java_name)`.
- `import time`: project key `time` from
  `Py2APK/stdlib/time.py`.
- `from time import time`: existing `_bind_module_names`
  static-method path.

`src/math.py` stays a normal project module. The alias runs only
when `"math" not in self.project`. Do not add `stdlib/math.py`.
That file would win over the alias in `resolve_module` and would
be indexed as project `math`.

## Math names

Public CPython `math` names are the allow-list. Java-only names
are not Python functions. Map at the binding and at the static
call. Store and invoke the Java name.

One `double` argument, result `D`, same Java name: `sqrt`, `sin`,
`cos`, `tan`, `asin`, `acos`, `atan`, `exp`, `log`, `log10`,
`floor`, `ceil`.

Two `double` arguments, result `D`, same Java name: `atan2`,
`hypot`, `pow`. `atan2(y, x)` keeps Python's argument order, which
matches `Math.atan2`.

Renames:

| Python | Java | Arity |
| --- | --- | --- |
| `radians` | `toRadians` | 1 |
| `degrees` | `toDegrees` | 1 |
| `fabs` | `abs` | 1 |

`fabs` always selects `Math.abs(D)D`. Exact `abs(I)I` would steal
an int argument. Builtin `abs` keeps the exact-type path.

`log` is natural log, one argument. `hypot` and `pow` are exactly
two arguments. A different arity is a failed catalog match.

## Math call rules

- Exact catalog match wins, except `fabs`.
- If the exact `Math` lookup misses, retry with every `I` and `F`
  argument replaced by `D`. Emit `_WIDEN` (`i2d` or `f2d`) in
  `_emit_arguments` for that selected signature.
- The invoke descriptor is the Java one. These methods return `D`.
  Do not append `d2f`. A `float` (`F`) local is not compatible
  with `D`. Callers who need `F` write `float(...)`, which already
  emits `d2f`.
- `floor` and `ceil` therefore return `D`. CPython returns an int.
  This goal follows the Java method.
- A miss on `java/lang/Math` raises
  `unknown method signature java/lang/Math.{java_name}`.
  Do not fall through to `_emit_constructor`.
- `from math import` of a name outside the table raises
  `unsupported import math.{name}` from `_record_from_import`,
  before the class-path fallback.
- `import math as m` and `from math import sqrt as root` use
  `alias.asname or alias.name` as the local, and `alias.name` as
  the Python method key.
- Bare `abs` / `min` / `max` stay in `_builtin_call`.

## Time module

`Py2APK/stdlib/time.py` is typed fallback source, same shape as
`calendar.py` (module-level functions) and the same Java import
shape as `contextlib.py`.

Signatures, matching CPython's names and a subset of its types:

- `time() -> float`
- `sleep(seconds: int) -> None`

Body that the subset can already compile:

- `time`: `float(System.currentTimeMillis()) / 1000.0`.
  The Java method is `()J`. `float` emits `l2f`. The division is
  `fdiv` by the float literal `1000.0`. Result annotation is `F`.
  `l2f` keeps about 24 bits of mantissa. Epoch milliseconds do
  not fit exactly. Document that. Do not use `i2f` on a `J`.
  Do not divide a `D`; `_emit_as_float` rejects `D`.
- `sleep`: `Thread.sleep(seconds * 1000)`. The product is `I`.
  The catalog method is `sleep(J)V`. Existing `I` to `J` emits
  `i2l`. Int overflow follows the other int multiplies.

`Thread.sleep` declares a checked `InterruptedException`. The
compiler has no throws clause. Ship the bare `invokestatic`.
Unit tests stop at Jasmin text.

After adding the file, `touch Py2APK/stdlib`. Linux does not bump
a directory mtime on a content change. `ensure_stdlib_jasmin`
rebuilds `~/.cache/py2apk/stdlib_j/` from that mtime or a newer
`.py`.

`_runtime_catalog` must gain the two methods or the cache compile
of `time.py` fails:

- `java/lang/System` `currentTimeMillis` `""` `J` static
  `invokestatic`
- `java/lang/Thread` `sleep` `J` `V` static `invokestatic`

`_alias_catalog` needs the same two rows so `_compiled_named` on
`time.py` can see them. Math rows belong in `_alias_catalog`
because user tests compile against that database. They are not
required in `_runtime_catalog` unless `time.py` calls `Math`.

## Tests already in place

- `_compiled_with_stdlib` indexes every `Py2APK/stdlib/**/*.py`
  and compiles only the host snippet. A host `time.time()` assert
  sees `invokestatic time/time()F`. It does not see the body.
- `_compiled_named(Path("Py2APK/stdlib/time.py"))` compiles the
  body. That is where `currentTimeMillis` and `Thread/sleep` are
  asserted.
- `_compiled_class` is enough for `import math` because math is
  not a project file. The catalog must contain each `Math` row
  the assert invokes.
- `tests/test_cli_compile_arithmetic.py` already depends on
  `Math.floor(D)D` inside float floor-division. Leave that path
  on `_emit_float_floor`.

## Scope

In:

- `import math`, `import math as m`, `math.sqrt`, `math.pow`,
  `from math import hypot`, `from math import radians as rad`
- `import time`, `time.time()`, `time.sleep(1)`,
  `from time import time`, `from time import sleep`

Out:

- `Py2APK/stdlib/math.py`, `base64`, `re`, `re.compile`
- constants `pi`, `e`, `tau`, `inf`, `nan`
- Java-only names: `toRadians`, `toDegrees`, `rint`, `signum`,
  `IEEEremainder`, `random`, `round`, `floorDiv`, `copySign`
- the rest of CPython `math` (`factorial`, `gcd`, `dist`,
  `isclose`, `log` with a base, `hypot` with a count other than
  2, hyperbolic functions, `fma`, `remainder`, ...)
- promoting every Java call through `_WIDEN`
- `d2f` on math results, `D` or `J` arithmetic, float `sleep`,
  `struct_time`, `monotonic`, `perf_counter`, `time_ns`
- a `.throws` clause or a new `InterruptedException` handler
- changing builtin `abs` / `min` / `max`

## Deferred check

Jasmin tests cannot show whether ART rejects `Thread.sleep`
without a catch. If a later `py2apk build` of a sample fails
verification on that call, handle it then. Do not block the
Jasmin work on that run.
