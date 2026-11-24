# Evidence: Catch more than one exception type

An `except` tuple in `Py2APK/compiler_j.py` emits one `.catch` per
distinct type and one shared handler. One type, including a
one-element tuple, stores that class. Different types store
`java/lang/Throwable`. An empty tuple and a nested entry are rejected.
`try`, `raise`, and `with` are unchanged.

`AliasLoweringTests.test_lowers_try_raise_and_with` covers
`(Error, Exception, Error)`, `(Error,)`, a following `except`,
`finally`, a mixed `as` name, an empty tuple, and a nested tuple.
`just check`, `just lint`, and `python3 -m unittest discover -s tests
-v` passed (64 tests). `sila facts verify` exited 0 with 3/3 facts
grounded and no violations. `sila prime` ran at task start.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "except-tuples",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime", "at": "2026-11-25T02:07+03:00"},
  "recall": [
    {
      "query": "except tuple catch multiple exception types jasmin",
      "matches": 0
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_2359efa1",
      "intent": "Share one except handler across a tuple"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_a6447574a993",
    "summary": "except tuples emit one .catch per type and share one handler"
  }
}
```
