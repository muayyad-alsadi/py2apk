# Evidence: Use a slice as a value

A slice expression copies. Step `1` on `str` uses `substring`. Step
`1` on an array uses `Arrays.copyOfRange`. An `arraylist:` source
gathers into a new `ArrayList`. Any other step gathers. `for` still
peels the slice and walks in place.

Shared docs (`ROADMAP.md`, `docs/covered.md`, `docs/not-covered.md`)
were left untouched for a later sequential edit.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 121 tests, OK |
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `tests/test_cli_compile_loop.py` | 3 tests, OK |
| `tests/test_cli_compile_stdlib.py` | 2 tests, OK |

## Acceptance checks

- `text[1:]`, `text[:10]`, `text[1:10]`, `text[:]`, and `text[-2:]`
  contain `substring(II)`, `slice_start`, and `slice_stop`, with
  `if_icmpgt` before `substring`.
- `text[5:2]` still contains `substring` after `if_icmpgt`. Bounds
  are not folded, so the JDK call stays on the not-taken side.
- `text[::-1]` and `text[::2]` contain `charAt`, `newarray char`,
  and `String/<init>([C)V`, and do not contain `substring`. The
  reverse contains `if_icmple`.
- `text[::step]` for a local `step` contains `charAt` and
  `IllegalArgumentException`, not `substring`.
- `text[::0]` raises `slice step cannot be zero`.
- `text[::"a"]` raises `slice bound must be int`.
- `items[:2]` on an int array contains `copyOfRange` and
  `slice_start`, and does not contain `ArrayList`.
- `names[:2]` on a string array contains `copyOfRange` and
  `checkcast [Ljava/lang/String;`.
- After `append`, `items[:2]` contains `new java/util/ArrayList`
  and `add`, and does not contain `subList` or `copyOfRange`.
- `items[::-1]` on an int array contains `newarray int`, `iaload`,
  `iastore`, and `if_icmple`, and does not contain `copyOfRange`.
- `items[1:3] = xs` raises `a slice is only supported as a for iterable`.
- A `HashSet` slice raises `subscript needs an array, a list, or a string`.
- `for item in items[1:]` does not contain `copyOfRange`.

## Deviations from plan

- Docs in section 6 stay open. The parent will edit shared docs later.
- Certification stays open. This commit does not run `sila goals certify`.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "slice-values",
  "project": "py2apk",
  "prime": {
    "ran": true,
    "command": "prime_context",
    "at": "2026-11-25T14:32:00Z"
  },
  "recall": [
    {
      "query": "slice values slice lowering compiler_j",
      "matches": 0
    },
    {
      "query": "slice subscript or slice as a value",
      "matches": 11
    }
  ],
  "memos": [
    {
      "id": "fact_2eb85759",
      "directive": "Value slices copy; for slices still walk in place"
    }
  ],
  "lessons": [],
  "cases": [
    {
      "id": "fact_f049f954",
      "intent": "Lower a slice expression to a new sequence"
    }
  ],
  "facts_verify": {"exit_code": null, "note": "not run; certify is deferred"},
  "handoff": {"id": null, "note": "parent certifies after merge"}
}
```
