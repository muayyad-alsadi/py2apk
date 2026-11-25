# Plan: Compile true division and float arithmetic

Decisions are locked in [`facts.md`](facts.md). `float` is `F`. `/` is
`fdiv` after `i2f`. Float `//` is `Math.floor(double)`. Float `%` is
`a - (a // b) * b`. Int `//` and `%` stay `idiv` and `irem`. `double`,
`bool`, and unary minus on `F` stay rejected.

## Execution Steps

### Phase 1 — Shared kind, no new descriptors

- [x] Add `_numeric_kind(self, op: ast.operator, left: str, right: str)
      -> str` on `_ExprMixin` (`expr.py`). Return `"F"` for `ast.Div`
      when both descriptors are in `{"I", "F"}`. Return `"I"` for
      `Add`/`Sub`/`Mult`/`FloorDiv`/`Mod` when both are `"I"`. Return
      `"F"` for those five when both are in `{"I", "F"}` and either is
      `"F"`. Otherwise `self.error` with
      `arithmetic needs int or float`. `Z`, `D`, and `J` hit that
      error. Do not import `_WIDEN`.
- [x] Add `_emit_as_float(self, node: ast.expr) -> None`: call
      `_expression`; on `"I"` emit `i2f`; on `"F"` emit nothing; any
      other descriptor is the same error. Call it on the left operand
      before the right operand is evaluated.
- [x] Add `_emit_float_floor(self, node: ast.AST) -> None`. The stack
      holds two `F` values. Emit `fdiv`, `f2d`, resolve
      `java/lang/Math.floor(D)D` with `_require_method`
      (`base.py:220`), `invokestatic`, then `d2f`.

### Phase 2 — Type and emit stay on one kind

- [x] `_type_of_binop` (`expr.py:317`): keep `str` + `str` `Add`
      first. Then `left = _type_of(left)`, `right = _type_of(right)`,
      kind = numeric kind of the two sides, return `"I"` or `"F"`.
- [x] `_yield_or_add` (`expr.py:148`): keep the `yield` guard and the
      string `concat` branch. Then:
      - If the numeric kind of the two `_type_of` results is `"I"`,
        evaluate both sides and emit today's `iadd`/`isub`/`imul`/
        `idiv`/`irem` map. `ast.Div` never takes this branch.
      - If the kind is `"F"` and the op is `Add`, `Sub`, `Mult`, or
        `Div`: `_emit_as_float` left, `_emit_as_float` right, then
        `fadd`/`fsub`/`fmul`/`fdiv`. Return `"F"`.
      - If the kind is `"F"` and the op is `FloorDiv`: same two
        `_emit_as_float` calls, then `_emit_float_floor`. Return `"F"`.
      - If the kind is `"F"` and the op is `Mod`: `_emit_as_float`
        each side and `_park_float` each `F` (same slot step as
        `_park` at `compare.py:508`; ty cannot see that mixin method
        from `_ExprMixin`). Reload, `_emit_float_floor`,
        park the quotient, reload `a`, reload `q`, reload `b`, `fmul`,
        `fsub`. Return `"F"`. Do not emit `frem`.
- [x] Leave `_unary_minus` and `compare.py` unchanged.

### Phase 3 — Verification & evidence

- [x] In `tests/_compile_helpers.py`, add static
      `java/lang/Math.floor(D)D` with owner `java/lang/Math` on the
      catalog `_alias_catalog` already uses (`tests/_compile_helpers.py`
      `Math.abs` rows near line 228).
- [x] Add `tests/test_cli_compile_arithmetic.py`:
      - `1.5 + 1.0`, `1.5 - 1.0`, `1.5 * 2.0` emit `fadd` / `fsub` /
        `fmul` and do not emit `iadd`.
      - `count / 2` emits two `i2f` and `fdiv`, and does not emit
        `idiv`. `a / b` for `float` locals emits `fdiv`.
      - `1 + ratio` emits `i2f` then `fadd` (left widened before the
        right operand).
      - `a // 2.0` emits `fdiv`, `f2d`,
        `invokestatic java/lang/Math/floor(D)D`, and `d2f`.
      - `(0.0 - 5.0) % 3.0` emits `floor`, `fmul`, and `fsub`, and does
        not emit `frem`. `(-5.0)` is unary minus, which stays rejected.
      - `(-5) // 2` emits `idiv` and does not emit `Math/floor`.
        `(-5) % 2` emits `irem`.
      - `label + 1.5` and `latitude: double` `/ 2` raise
        `arithmetic needs int or float`.
- [x] Run `python3 -m unittest discover -s tests -v`.
- [x] Docs: `docs/covered.md` gains `/` and float `+ - * // %` with
      `half = count / 2`. `docs/not-covered.md` keeps a `double`
      division example and drops the claim that no `/` compiles.
      `ROADMAP.md` (the `sum` bullet near line 105) matches that.
- [x] Leave green: `just check`, `just lint`, and
      `ripwire . --doc-drift --legend=compact` with `drift="0"`.
- [x] Write `goals/true-division/evidence.md` with those commands, a
      `## Memory Writes` block, and a standalone `Verdict: PASS` line.

### Phase 4 — Attribution & Registry Closure

- [x] Check every completed task in this file. Commit with
      `(goals/true-division/goal.md)` or `[goal: true-division]` under
      `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` (`docs:` and
      `test:` subjects do not count). Certify with
      `sila goals certify true-division
      --evidence=goals/true-division/evidence.md` or
      `sila goals audit true-division`. Reconcile with `sila goals`
      and commit `goals/README.md` plus the receipts. `sila goals
      --json` reports `unverified_count: 0` for delivered goals.
