# Goal: Lower math and time

## Goal Description

`import math` calls a fixed subset of `java.lang.Math`. `import time`
calls packaged `time.time` and `time.sleep`. `re` stays unaliased.
Each math call emits the Java method it resolved, including that
method's descriptor.

## Dependencies & Execution Order

- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 13
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch

- `Py2APK/compiler_j/module.py`
- `Py2APK/compiler_j/calls.py`
- `Py2APK/compiler_j/types.py`
- `Py2APK/stdlib/time.py` (new)
- `Py2APK/stdlib_cache.py`
- `tests/_compile_helpers.py`
- `tests/test_cli_compile_stdlib.py`
- `docs/covered.md`
- `docs/not-covered.md`
- `ROADMAP.md`

Touch the `Py2APK/stdlib` directory after the new module. Do not add
`Py2APK/stdlib/math.py`.

## References

- **Shared Understanding & Fact Sheet**: [`goals/math-time/facts.md`](facts.md)
- **Execution Plan**: [`goals/math-time/plan.md`](plan.md)

## Memory Plan

- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task
  start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before
  implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded
  to a file in this goal; use a `case` when the goal introduces a
  reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record
  the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session
  end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under
  `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition

1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block
   the `implemented` transition.
2. Implementation is committed with `(goals/math-time/goal.md)` or
   `[goal: math-time]` under a `feat:`, `fix:`, `refactor:`, `perf:`,
   or `chore:` prefix.
3. Goal is certified (`goals/math-time/certification.json` with
   `verified: true` or a signed `audit-receipt.json`) and any markdown
   evidence contains an explicit standalone `Verdict: PASS` line.
4. `sila goals --json` reports `status: "implemented"`,
   `verification_status: "verified"`, and `unverified_count: 0`, with
   a clean `git status`.
