# Goal: Unpack a pair in for

## Goal Description

Compile `for a, b in pairs` when each element is a homogeneous
Java array, the shape a same-type tuple or list literal already
has. A one-name `for` stays as it is. A starred target stays
rejected. `enumerate` and `zip` stay unimplemented. This goal
only closes the loop-target hole in front of them.

## Dependencies & Execution Order

- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 10
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch

- `Py2APK/compiler_j/loops.py`
- `tests/test_cli_compile_loop.py`
- `docs/covered.md`
- `docs/not-covered.md`
- `ROADMAP.md`

Do not change `module.py`, `collections.py`, or `Py2APK/stdlib`.

## References

- **Shared Understanding & Fact Sheet**: [`facts.md`](facts.md)
- **Execution Plan**: [`plan.md`](plan.md)

## Memory Plan

**Prime.** Run `sila prime` or `prime_context` at task start.
Record the briefing timestamp.

**Recall.** Run `sila recall` on the design question before
implementation. Record each query and match count.

**Writes.** Record at least one `memo`, `lesson`, or `case`
grounded to a file in this goal. Use a `case` for the
unpack helper.

**Facts.** Run `sila facts verify` before certification.
Record the exit code and violation delta.

**Handoff.** Run `sila handoff` at session end with the
open items. Record the handoff ID.

**Evidence.** Embed a `sila/memory-writes/v1` block under
`## Memory Writes`.

## Done Condition

1. Every task in `plan.md` is checked (`- [x]`).
2. The commit message cites this goal file.
3. The prefix is `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:`.
4. Certification is verified, or a signed audit receipt exists.
5. Evidence has a standalone `Verdict: PASS` line.
6. `sila goals --json` reports `implemented` and `verified`.
7. `unverified_count` is 0 and `git status` is clean.

## Outcome

`for a, b in [(1, 2), (3, 4)]` compiles. Each iteration loads
one `int[]` and stores `a` and `b` as `int`. `for a, b in (1, 2)`
and `for a, *rest in pairs` still fail at compile time. A wrong
array length still fails at run time. Name-target loops, slices,
`break`, `continue`, and `for else` stay as they are.
