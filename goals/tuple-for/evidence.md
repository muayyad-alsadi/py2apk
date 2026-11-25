# Evidence: Unpack a pair in for

`for a, b in [(1, 2), (3, 4)]` lowers to an array load, a length
check, and per-slot stores. A one-name `for` still uses `_store`
on the whole element. Shared docs were left for a later pass.

## Commands and results

| Command | Result |
|---|---|
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `python3 -m unittest discover -s tests -v` | 110 tests, OK |

Run from `/tmp/py2apk-tuple-for` with `PYTHONPATH` set to that
worktree. The first unittest pass in the sandbox failed on
`~/.cache/py2apk/stdlib_j/jars.db` (permission denied). The
unrestricted rerun exited 0.

Verdict: PASS

## Deviations

- `docs/covered.md`, `docs/not-covered.md`, and `ROADMAP.md` were
  not edited. Those plan tasks stay open.
- `sila goals certify` was not run.
- `sila goals claim tuple-for` failed: file conflict with active
  goal `slice-values` on `loops.py` and the shared docs. Claim
  was not forced.
- Lists of tuples had no tuple type handler. `_type_of_tuple`
  calls the existing `_element_type`. A base stub makes that
  call visible to `ty`.

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "tuple-for",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime"},
  "recall": [
    {
      "query": "tuple for unpacking a pair in for",
      "matches": 0
    },
    {
      "query": "for-loop tuple unpack",
      "matches": 2
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_adc83217",
      "intent": "Unpack a same-type pair in for"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_8a70c75546b2",
    "summary": "for unpacks a same-type pair into its own locals"
  }
}
```
