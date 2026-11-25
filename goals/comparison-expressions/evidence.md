# Evidence: Store comparisons and boolean tests

Stored `==`, `!=`, `and`, `or`, and `not` leave `0` or `1`.
`_condition_as_bool` in `Py2APK/compiler_j/compare.py` is
`_condition_jump_false` plus `iconst_1` / `iconst_0`. `if` and `while`
still jump. A chain emits each comparator once and skips the rest after
a failed link. `is` between two numbers stays rejected.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 102 tests, OK |
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `ripwire . --doc-drift --legend=compact` | `drift="0"` |
| `sila facts verify` | exit 0; 24/24 blocks; 3 pre-existing missing files |

The three missing references are `data/api-versions.xml`,
`Py2APK/compiler_j.py`, and `tests/test_cli_compile.py`. This change
did not add them.

## Acceptance checks

- `same = left == right` for `str` emits `equals`, `iconst_1`, and
  `iconst_0`. `int` `!=` uses `if_icmpeq` and no `equals`. Mixed
  `str == int` pops and stores `iconst_0`. `width == 1.0` uses
  `fcmpg`. `folded == None` uses `ifnull`.
- Stored `and` emits the right compare after the left `ifeq`. Stored
  `or` jumps before `iload 2`. `if left and right` has no `iconst`.
- `not items` for `list[str]` uses `arraylength`. `not flag` uses
  `ifne`. `not width` raises `if condition must be bool or int`.
  `if not count` uses `ifne` and no `iconst`.
- `if left == mid == right` has two `if_icmpne` and no `iconst`.
  `left == len(name) == right` has one `length()I` before `iload 2`.
  `if lo < mid < hi` has two `if_icmpge`. A stored chain has
  `iconst_1` and `iconst_0`. `in` still raises
  `unsupported comparison In`.

## Deviations from plan

- `list[str]` is a `String[]`, so `not items` uses `arraylength`
  rather than `size()`.
- `if not count` now jumps. The old f-string test expected that form
  to fail.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "comparison-expressions",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime"},
  "recall": [
    {
      "query": "store comparison as boolean without duplicating jump lowering",
      "matches": 5
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_bf94ff71",
      "intent": "Store comparisons and boolean tests as Z"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_619328b5a2f7",
    "summary": "Comparisons and boolean tests store Z from the condition jump"
  }
}
```
