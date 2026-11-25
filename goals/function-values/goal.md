# Goal: Use a function as a value

## Chain of Custody (Isnad)
- **Registered By**: Pending Registration
- **Spec Audited By**: Pending Spec Audit
- **Implemented By**: Pending Implementation
- **Mystery Audited By**: @mystery-auditor | Report: `receipt_function-values_1795641854`
## Goal Description
Allow a module function or a stored `lambda` to be assigned to a local,
passed as an argument to a `Callable`-annotated parameter, returned from
a `Callable`-annotated function, and called later. Today a `lambda`
compiles only as an argument of a one-method Java interface, and a
module function is `invokestatic` only.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 11
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j/types.py
- Py2APK/compiler_j/indexer.py
- Py2APK/compiler_j/base.py
- Py2APK/compiler_j/module.py
- Py2APK/compiler_j/lambdas.py
- Py2APK/compiler_j/calls.py
- tests/test_cli_compile_lambda.py
- docs/covered.md
- docs/not-covered.md
- ROADMAP.md

## References
- **Shared Understanding & Fact Sheet**: [`goals/function-values/facts.md`](facts.md)
- **Execution Plan**: [`goals/function-values/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/function-values/goal.md)` or `[goal: function-values]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/function-values/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
