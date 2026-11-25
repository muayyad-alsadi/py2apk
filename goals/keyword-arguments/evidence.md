# Evidence: Allow keyword arguments on calls

Keyword arguments on a Java or project call are reordered to formal
parameter order before the arguments are pushed. `MethodParameters`
wins. Otherwise names come from `LocalVariableTable` entries at
`start_pc == 0`, and `J`/`D` take two slots. A stale `jar.sqlite`
without `param_names` is rebuilt. Project functions, methods, and
constructors keep the Python parameter names.

`ClassFileTests` covers `MethodParameters` ahead of the local table,
instance `(IJD)V` slots, and static `(DJ)V` slots.
`StaleJarIndexTests` covers a legacy sqlite rebuild and a current
sqlite that stays put. `KeywordCallTests` covers instance methods,
static methods, constructors, `super()`, project functions, overload
selection by name, and rejection of `**kwargs`, duplicates, positional
collisions, unknown names, missing parameters, nameless overloads,
ambiguous calls, `append`, `suppress`, and `super()`.

`just check` and `just lint` passed. `python3 -m unittest discover -s
tests -v` passed (73 tests). `ripwire . --doc-drift --legend=compact`
reported `drift="0"`. `ripwire . --quality-delta --legend=compact`
exited 0 with `gating="0"`. `sila prime` ran at
2026-11-25T03:38+03:00. `sila facts verify` exited 0. The ledger
summary has no broken links, digest mismatches, or order gaps.
Three older facts still name `Py2APK/compiler_j.py`, which this goal
did not revive.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "keyword-arguments",
  "project": "py2apk",
  "prime": {
    "ran": true,
    "command": "sila prime",
    "at": "2026-11-25T03:38+03:00"
  },
  "recall": [
    {
      "query": "JVM parameter names MethodParameters LocalVariableTable keyword arguments jars.db",
      "matches": 0
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_ed039e5d",
      "intent": "Reorder keyword arguments to formal JVM parameter order"
    }
  ],
  "facts_verify": {
    "exit_code": 0,
    "violations": {
      "broken_prev_link": 0,
      "digest_mismatch": 0,
      "missing_hash": 0,
      "genesis_mismatch": 0,
      "duplicate_hash": 0,
      "order_gap": 0
    }
  },
  "handoff": {
    "id": "hoff_09e8d8109112",
    "summary": "Keyword arguments reorder to formal parameter order"
  }
}
```
