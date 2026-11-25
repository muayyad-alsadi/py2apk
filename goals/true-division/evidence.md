# Evidence: Compile true division and float arithmetic

`/` lowers to `fdiv` and returns `F`, including `count / 2`. Float
`+ - *` use `fadd` / `fsub` / `fmul`. Float `//` is
`Math.floor(double)` then `d2f`. Float `%` is `a - (a // b) * b`.
Int `//` and `%` stay `idiv` and `irem`. `double` division stays
rejected.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 109 tests, OK |
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `ripwire . --doc-drift --legend=compact` | `drift="0"` |
| `sila facts verify` | exit 0; 25/25 blocks; 3 pre-existing missing files |

The three missing references are `data/api-versions.xml`,
`Py2APK/compiler_j.py`, and `tests/test_cli_compile.py`. This change
did not add them.

## Acceptance checks

- `1.5 + 1.0`, `1.5 - 1.0`, and `1.5 * 2.0` emit `fadd`, `fsub`, and
  `fmul`, and do not emit `iadd`.
- `count / 2` emits two `i2f` and `fdiv`, and does not emit `idiv`.
  `a / b` for `float` locals emits `fdiv`.
- `1 + ratio` emits `i2f` before `fload` and then `fadd`.
- `a // 2.0` emits `fdiv`, `f2d`,
  `invokestatic java/lang/Math/floor(D)D`, and `d2f`.
- `(0.0 - 5.0) % 3.0` emits `floor`, `fmul`, and `fsub`, and does not
  emit `frem`.
- `(-5) // 2` emits `idiv`. `(-5) % 2` emits `irem`. Neither emits
  `Math/floor`.
- `label + 1.5` and `latitude: double` `/ 2` raise
  `arithmetic needs int or float`.

## Deviations from plan

- Float `%` parks with `_park_float` on `_ExprMixin`. `ty` does not
  see `_park` from `_CompareMixin`.
- The negative float modulo sample is `(0.0 - 5.0) % 3.0`. `(-5.0)`
  is unary minus, which stays rejected.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "true-division",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime"},
  "recall": [
    {
      "query": "true division float binop frem floor",
      "matches": 0
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_c76ded8f",
      "intent": "Lower Python true division and float arithmetic"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_b105f7dab11a",
    "summary": "True division and float arithmetic lower through _numeric_kind"
  }
}
```
