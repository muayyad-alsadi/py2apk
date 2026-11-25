# Evidence: Split the Jasmin compiler into a package

`Py2APK/compiler_j.py` is now the package `Py2APK/compiler_j`.
`__init__.py` re-exports `compile_py2j`, `JasminCompilerError`,
`LocalType`, and `index_project_types`. `cli_compile.py`,
`cli_setup.py`, and `stdlib_cache.py` are unchanged.
Family mixins register `STMT_HANDLERS`, `EXPR_HANDLERS`,
`TYPE_HANDLERS`, and operator-keyed `UNARY_HANDLERS`.
`_CompilerBase` merges those tables and dispatches by node type.
`python3 -m unittest discover -s tests -v` passed (64 tests, same
count as before the split). `just check` and `just lint` passed.
Packaging install imported the four public names, and `py2apk --help`
printed the command list. `ripwire` doc-drift reported `drift="0"`.
`quality_delta` reported `gating=0`. `sila facts verify` exited 0
with every violation counter at 0. `sila prime` (`prime_context`)
ran at 2026-11-25T02:55+03:00. Recall for the split query returned
1 unrelated match.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "compiler-package",
  "project": "py2apk",
  "prime": {"ran": true, "command": "prime_context"},
  "recall": [
    {
      "query": "compiler package split table-driven dispatch mixins",
      "matches": 1
    }
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_6c610f6e",
      "intent": "Split Jasmin lowering into family modules with merged handler tables"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_a6786a410bd0",
    "summary": "Jasmin compiler is the Py2APK/compiler_j package with merged handler tables"
  }
}
```
