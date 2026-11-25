# Goal: Store comparisons and boolean tests

## Chain of Custody (Isnad)
- **Registered By**: Pending Registration
- **Spec Audited By**: Pending Spec Audit
- **Implemented By**: Pending Implementation
- **Mystery Audited By**: @mystery-auditor | Report: `receipt_comparison-expressions_1795611055`


## Goal Description
Lower ==, !=, and, or, and not to boolean values, including a chained comparison. is and is not already store a boolean. A comparison inside if and while stays a jump.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 7
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j/compare.py
- tests/test_cli_compile_compare.py
- docs/covered.md and docs/not-covered.md (document the expanded subset)

## Implementation summary
Stored compares and boolean tests are the existing condition jump plus
`iconst_1` / `iconst_0`. Single-link `if` / `while` bytecode stays.
Chains are a new walker: each comparator is emitted once, and a failed
link jumps past the rest. `and` / `or` store `0` or `1`.

## References
- **Shared Understanding & Fact Sheet**: [`goals/comparison-expressions/facts.md`](facts.md)
- **Execution Plan**: [`goals/comparison-expressions/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/comparison-expressions/goal.md)` or `[goal: comparison-expressions]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/comparison-expressions/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
