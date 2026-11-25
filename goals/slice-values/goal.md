# Goal: Use a slice as a value

## Goal Description

Compile `label[1:]` and `items[:10]` as values. An index already
compiles. A slice is legal today only as the iterable of `for`, and
that `for` path stays a walk, not a copy. A value slice is a new
`String`, a new array of the same descriptor, or a new `ArrayList`
with the same `arraylist:` element type.

## Dependencies & Execution Order

- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 9
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch

- `Py2APK/compiler_j/base.py`
- `Py2APK/compiler_j/loops.py`
- `Py2APK/compiler_j/collections.py`
- `tests/_compile_helpers.py`
- `tests/test_cli_compile_collections.py`
- `docs/covered.md`
- `docs/not-covered.md`
- `ROADMAP.md`

Do not edit `Py2APK/stdlib/py2apk.py`. `slice_start` and `slice_stop`
already normalize bounds. Do not add a Python-visible helper.

## References

- **Shared Understanding & Fact Sheet**:
  [`goals/slice-values/facts.md`](facts.md)
- **Execution Plan**: [`goals/slice-values/plan.md`](plan.md)

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
2. Implementation is committed with `(goals/slice-values/goal.md)` or
   `[goal: slice-values]` attribution under a `feat:`, `fix:`,
   `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/slice-values/certification.json` with
   `verified: true` or a signed `audit-receipt.json`) and any markdown
   evidence contains an explicit standalone `Verdict: PASS`
   declaration.
4. `sila goals --json` reports `status: "implemented"`,
   `verification_status: "verified"`, and `unverified_count: 0` for
   delivered goals, with a clean `git status`.
