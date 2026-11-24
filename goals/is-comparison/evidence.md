# Evidence: Compare with is and is not

`is` and `is not` lower to identity jumps in `Py2APK/compiler_j.py`.
`None` beside a reference emits `ifnull` or `ifnonnull`. Two references
emit `if_acmpeq` or `if_acmpne`. Neither path calls `Object.equals`.

`AliasLoweringTests.test_lowers_identity_checks` covers `response is
None`, `response is not None`, `None is response`, `left is right`,
`left is not right`, and `count is None`. A number `is` a number and
`missing = response is None` still fail. `just check`, `just lint`, and
`python3 -m unittest discover -s tests -v` passed (63 tests).

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "is-comparison",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime"},
  "recall": [
    {
      "query": "is not None identity comparison ifnull ifnonnull",
      "matches": 0
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_e8bdfdec",
      "intent": "Lower is and is not to identity jumps"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_94c288ab154d",
    "summary": "is and is not lower to identity jumps"
  }
}
```
