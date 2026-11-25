# Evidence: Use a function as a value

A module function or a stored lambda lowers to an object whose method
is `call`. Each JVM signature has one abstract `py2apk$fn$` base.
`Callable` comes from `typing` or `collections.abc`.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -q` | 151 tests, OK, exit 0 |
| `just check` | exit 0 |
| `just lint` | exit 0 |
| `ripwire . --quality-delta --legend=compact` | exit 0, gating=0 |
| `ripwire . --doc-drift --legend=compact` | drift=0 |
| `PYTHONPATH=. python3 -m Py2APK.cli build /tmp/fnvalue` | exit 0 |

The suite needs write access to `~/.cache/py2apk`. A sandboxed run
fails with `Permission denied` on `stdlib_j/jars.db` before compile.

## Acceptance checks

- `Callable[[int], int]` emits `py2apk$fn$I_r_I`, `Host$fn$1`, and
  `invokevirtual py2apk$fn$I_r_I/call(I)I`.
- `lambda: 42` emits `call()I`. `lambda: self.ping()` then `f()`
  emits `call()V` and does not `pop`.
- `g = square` emits `invokestatic` inside `call`, same module and
  imported.
- Two values of one signature share one base `.j`.
- An effectively final capture is a `.field` passed to `<init>`.
- `apply(lambda ...)`, `apply(square)`, and `apply(f)` compile in one
  module and across two indexed modules.
- `return lambda x: x + 1` from `-> Callable[[int], int]` uses `areturn`.
- Unannotated `lambda x: ...`, `Callable[..., R]`, `Callable[int, int]`,
  unknown captures, reassigned captures, bad arity, bad argument type,
  keywords, mismatched signatures, result mismatch, `math.sqrt`, and
  `map` are rejected.
- `test_lowers_listener_lambdas_and_method_references` passes with no
  edits to that test.
- `/tmp/fnvalue` builds `dist/blank.signed.apk`. `javap` shows
  `public abstract int call(int)` on one
  `.py2apk/classes/py2apk$fn$I_r_I.class`.

## Deviations from plan

- `non_final_locals` is set at the start of `_compile_method`. Methods
  are not nested, so the previous set is not restored.
- Quality delta acked 4 preexisting findings: call-class churn,
  `_emit_arguments` churn, lambda-mixin verbosity, and a test-source
  clone. New-symbol rows remain unacked and do not gate.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "function-values",
  "project": "py2apk",
  "prime": {
    "ran": true,
    "command": "sila prime",
    "at": "2026-11-25T21:01:10Z"
  },
  "recall": [
    {"query": "function value", "matches": 3},
    {"query": "lambda listener", "matches": 1}
  ],
  "memos": [],
  "lessons": [],
  "cases": [
    {
      "id": "fact_197cbb03",
      "intent": "Canonical py2apk$fn$ function values"
    }
  ],
  "facts_verify": {"exit_code": 0, "violation_delta": 0}
}
```
