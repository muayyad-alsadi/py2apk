# Goal: Lower map, filter, and sorted with key

## Goal Description
Lower single-iterable `map(fn, xs)`, `filter(fn, xs)`, and
`sorted(xs, key=fn, reverse=...)` on arrays and `ArrayList`s, building
on `function-values` (`Callable` abstract base classes and function
references).

## Dependencies & Execution Order
- **Mode**: Dependent (requires `function-values`)
- **Depends On**: function-values
- **Sequence**: 12
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j/calls.py
- Py2APK/compiler_j/collections.py
- Py2APK/compiler_j/lambdas.py
- Py2APK/compiler_j/base.py
- tests/_compile_helpers.py
- tests/test_cli_compile_lambda.py
- docs/covered.md
- docs/not-covered.md
- ROADMAP.md

## References
- **Shared Understanding & Fact Sheet**: [`goals/higher-order-builtins/facts.md`](facts.md)
- **Execution Plan**: [`goals/higher-order-builtins/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/higher-order-builtins/goal.md)` or `[goal: higher-order-builtins]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/higher-order-builtins/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
