# Facts: Compile true division and float arithmetic

## Current state (verified)

- `ast.BinOp` lowers in `_yield_or_add` and types in `_type_of_binop`
  (`Py2APK/compiler_j/expr.py:50`, `expr.py:228`). Both sides `I`:
  `+ - * // %` emit `iadd` `isub` `imul` `idiv` `irem`. `ast.Div` is
  absent from that map, so `/` raises
  `only string concatenation and integer arithmetic are supported`
  (`expr.py:72`). That sentence is only in `expr.py`; no test locks it.
- `float` is descriptor `F` (`types.py:45`). A float literal is `ldc`
  plus `F` (`base.py:263`). `fstore` already exists (`base.py:382`).
  `double` is a separate descriptor `D` (`types.py:46`). No `fadd` or
  `fdiv` is emitted anywhere.
- Integer ops require `I` on both sides. `Z` does not count as `I`
  (`expr.py:70`). Bool arithmetic stays rejected.
- `docs/not-covered.md:41` shows `half = latitude / 2`. The weather
  template types `latitude` as `double`. This goal covers `int` and
  `float` (`F`) only, so that line stays rejected when `latitude` is
  `double`. The covered example is `half = count / 2`.
- Ordered compares stay `I`/`Z` (`compare.py:54`). `_unary_minus`
  stays `I` (`expr.py:35`). `-ratio` stays rejected; `0.0 - ratio`
  is the binary form this goal accepts.

## Semantics the lowering has to match

Python 3 `/` is true division and its result is a float even for two
ints (`7 / 2` is `3.5`). In this compiler that result is `F`, the same
32-bit descriptor as a float literal. Two ints are `i2f` then `fdiv`.
Values above `2**24` are not exact in `F`. That matches every float
this compiler already stores.

`+ - *` on two `F` values, or one `I` and one `F`, promote to `F` and
emit `fadd` / `fsub` / `fmul`.

Float `//` is floor toward -inf. `(-7.0) // 2` is `-4.0`. JVM `fdiv`
truncates toward zero, so the lowering is `fdiv`, `f2d`,
`java/lang/Math.floor(D)D`, `d2f`. `Math.floor(float)` is Java 14;
`floor(double)` is the method `minSdkVersion` already allows.
`f2d` of an `F` is exact.

Float `%` is `a - (a // b) * b`. JVM `frem` keeps the sign of the
dividend: `(-5.0) % 3.0` is `-2.0` in Java and `1.0` in Python. `%`
reuses the floor quotient. It does not emit `frem`.

Int `//` and `%` stay `idiv` and `irem` (truncation toward zero,
sign of the dividend). `(-5) // 2` keeps `idiv` and does not call
`Math.floor`.

A zero divisor follows the JVM. `idiv`/`irem` throw
`ArithmeticException`. `fdiv` yields Infinity or NaN. This goal does
not insert a `ZeroDivisionError` check.

## Design

String `+` stays the first branch. One helper,
`_numeric_kind(op, left, right) -> "I" | "F"`, is what both the type
handler and the emitter call, so the two cannot drift.

```text
str + str                         concat, String
I/I, + - * // %                   iadd isub imul idiv irem, I
I or F, /                         i2f where needed, fdiv, F
I/F or F/F, + - *                 i2f where needed, fadd fsub fmul, F
I/F or F/F, //                    same, then floor(double), d2f, F
I/F or F/F, %                     park both F, q = floor, a - q*b, F
D, J, Z, references               arithmetic needs int or float
```

Widen on the top of the stack immediately after the operand that
needs it, and before the other operand is pushed. Types are known
from the descriptor `_expression` just returned, so `fdiv` sees
`[left F, right F]`.

`%` is the only op that parks. The quotient consumes both values, and
the formula still needs `a` and `b`. Reuse `_park` / reload the way
`compare.py:365` already does for a pair of descriptors. `+ - * / //`
do not park.

`_emit_float_floor` assumes two `F` on the stack and replaces them
with the floor quotient `F`. `//` calls it directly. `%` parks, calls
it, then `fmul` and `fsub`.

## File & interface contracts

- `Py2APK/compiler_j/expr.py`
  - `_numeric_kind(self, op, left: str, right: str) -> str`
  - `_emit_as_float(self, node: ast.expr) -> None` — `_expression`,
    then `i2f` when the descriptor is `I`.
  - `_emit_float_floor(self, node: ast.AST) -> None` — `fdiv`, `f2d`,
    `Math.floor(D)D` through `_require_method` (`base.py:218`), `d2f`.
  - `_emit_float_mod(self, node: ast.BinOp) -> None` — park both,
    floor, `a - q * b`.
  - `_type_of_binop` and `_yield_or_add` call the numeric-kind helper.
- `Py2APK/compiler_j/types.py` — unchanged. `i2f` / `f2d` / `d2f` are
  written at the emit site. `_WIDEN` stays a compare helper.
- `tests/_compile_helpers.py` — static
  `java/lang/Math.floor(D)D` with owner `java/lang/Math`.
- `tests/test_cli_compile_arithmetic.py` — new. Bytecode asserts, same
  style as `tests/test_cli_compile_collections.py`.

## Scope

In: `F` and mixed `I`/`F` for `+ - * / // %`, result `F` except
int/int `+ - * // %` which stay `I`. Locals and `return`. A float
already accepted by an f-string format spec.

Out: `D` and `J` binops, `Z` arithmetic, unary minus on `F`, `**`,
float ordered compares, `math.*` as Python builtins, Python
`ZeroDivisionError`, and changing int `idiv` / `irem`.

## Test seams

- No test asserts `idiv` or `irem`. Loop and generator tests only
  mention `iadd` (`tests/test_cli_compile_loop.py:115`).
- `tests/test_cli_compile_fstring.py` uses `double` parameters. It is
  a non-regression run after `expr.py` changes, not the home of the
  new asserts.
- Bytecode locks:
  - `count / 2` has `i2f` and `fdiv`, and no `idiv`.
  - `1 + ratio` has `i2f` and `fadd`.
  - `a // 2.0` has `Math/floor(D)D` and `d2f`.
  - `(-5.0) % 3.0` has `floor`, `fmul`, and `fsub`, and no `frem`.
    Python's result is `1.0`; `frem` would be `-2.0`.
  - `(-5) // 2` has `idiv` and no `Math/floor`.
  - `latitude: double` divided by `2` raises
    `arithmetic needs int or float`.

## Risks

- Forgetting `d2f` after `floor` leaves `D` in a `float` local.
- Emitting `frem` matches the JVM and fails `(-5.0) % 3.0`.
- Parking `+` and `/` as well as `%` copies `compare.py` into a path
  that does not need it and grows `_yield_or_add` (`ccx=6`).
- Promoting `/` to `D` would disagree with float literals, which are
  `F`.
- Updating `docs/not-covered.md:41` by deleting the `/` line would
  claim `double` division works. Keep a `double` example there.
