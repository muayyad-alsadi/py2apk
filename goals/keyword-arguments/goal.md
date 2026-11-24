# Goal: Allow keyword arguments on calls

## Goal Description
Match keyword call arguments to jars.db overloads. Unlocks builder-style Android calls and aliases that need keywords.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 3
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j.py
- tests/test_cli_compile.py

## References
- **Shared Understanding & Fact Sheet**: [`goals/keyword-arguments/facts.md`](facts.md)
- **Execution Plan**: [`goals/keyword-arguments/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/keyword-arguments/goal.md)` or `[goal: keyword-arguments]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/keyword-arguments/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
