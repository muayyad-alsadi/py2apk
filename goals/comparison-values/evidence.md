# Evidence: Store is and is not

`missing = body is None` and `saved = body is None` compile to a
boolean in `Py2APK/compiler_j.py`. The expression uses the same
identity jump as `if` and `while`, then `iconst_1` or `iconst_0`.
`while body is not None` still branches and does not keep the test.
`is` between two numbers stays rejected. Neither path calls
`Object.equals`.

`AliasLoweringTests.test_stores_identity_checks` covers both
assignments, the `while` jump, `left is not right`, and `count is
None`. `just check`, `just lint`, and `python3 -m unittest discover
-s tests -v` passed (64 tests).

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "comparison-values",
  "project": "py2apk",
  "prime": {"ran": true, "command": "sila prime"},
  "recall": [
    {
      "query": "is and is not boolean value missing = body is None",
      "matches": 2
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_81641e75",
      "intent": "Store is and is not as a boolean"
    }
  ],
  "facts_verify": {"exit_code": 0, "grounding": "2/2", "violations": 0},
  "handoff": {
    "id": "hoff_e63dbc98f17d",
    "summary": "is and is not store a boolean from the identity jump"
  }
}
```
