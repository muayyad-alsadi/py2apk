# Goal: Store is and is not

## Goal Description
Lower is and is not to a boolean so missing = body is None and saved = body is None compile. while body is not None already branches and does not keep the test. is between two numbers stays rejected.

## Dependencies & Execution Order
- **Mode**: Dependent (requires prerequisite goals)
- **Depends On**: is-comparison
- **Sequence**: 6
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j.py
- tests/test_cli_compile.py

## References
- **Shared Understanding & Fact Sheet**: [`goals/comparison-values/facts.md`](facts.md)
- **Execution Plan**: [`goals/comparison-values/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/comparison-values/goal.md)` or `[goal: comparison-values]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/comparison-values/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
