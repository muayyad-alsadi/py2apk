# Goal: Lower literal split and join

## Goal Description
Lower `text.split("sep")` when the separator is a non-empty string
literal to a quoted Java split that keeps trailing empty fields.
Lower `sep.join(parts)` for a homogeneous `str` sequence to
`py2apk.str_join`. A non-literal separator stays Java's regex
`String.split`. Do not call `String.join`.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 12
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- `Py2APK/compiler_j/calls.py`
- `Py2APK/stdlib/py2apk.py` (then `touch Py2APK/stdlib`)
- `tests/_compile_helpers.py`
- `tests/test_cli_compile_fstring.py`
- `tests/test_cli_compile_stdlib.py`
- `docs/covered.md`, `docs/not-covered.md`, `ROADMAP.md`, `README.md`

## References
- **Shared Understanding & Fact Sheet**:
  [`goals/string-split-join/facts.md`](facts.md)
- **Execution Plan**: [`goals/string-split-join/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at
  task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before
  implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case`
  grounded to a file in this goal. Use a `case` when the goal
  introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record
  the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session
  end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under
  `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`). Unchecked tasks
   block the `implemented` transition.
2. Implementation is committed with
   `(goals/string-split-join/goal.md)` or
   `[goal: string-split-join]` under a `feat:`, `fix:`, `refactor:`,
   `perf:`, or `chore:` prefix.
3. Goal is certified
   (`goals/string-split-join/certification.json` with
   `verified: true`, or a signed `audit-receipt.json`). Evidence
   contains a standalone `Verdict: PASS` line.
4. `sila goals --json` reports `status: "implemented"`,
   `verification_status: "verified"`, and `unverified_count: 0`,
   with a clean `git status`.
