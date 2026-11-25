# Goal: Lower set literals to HashSet

## Chain of Custody (Isnad)
- **Registered By**: Pending Registration
- **Spec Audited By**: Pending Spec Audit
- **Implemented By**: Pending Implementation
- **Mystery Audited By**: @mystery-auditor | Report: `receipt_hashset-literals_1795600284`


## Goal Description
Compile a homogeneous set literal to `java.util.HashSet`, using the same
rules as list and dict literals.

`{1, 2}` becomes `new java/util/HashSet` plus one `add` per element,
with primitives boxed. Element typing follows the list rule: at least
one element, one element type, no `null` or `lambda` element, no `*`
unpacking, and a clear error for a mix. `len(s)`, `if s:`, and
`if not s:` use `HashSet.size()`. A set is never an array, so no
pseudo descriptor or list-classification state is added. `in`,
`for x in s`, `add`, `remove`, `set()`, `frozenset`, and `set[T]`
annotations stay out of scope and are documented as such.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 5
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j/types.py
- Py2APK/compiler_j/collections.py
- Py2APK/compiler_j/compare.py
- tests/_compile_helpers.py
- tests/test_cli_compile_collections.py
- ROADMAP.md
- docs/covered.md
- docs/not-covered.md

## References
- **Shared Understanding & Fact Sheet**: [`goals/hashset-literals/facts.md`](facts.md)
- **Execution Plan**: [`goals/hashset-literals/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/hashset-literals/goal.md)` or `[goal: hashset-literals]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/hashset-literals/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.

## Scope

In:

- A non-empty, homogeneous `{...}` literal lowered to `java.util.HashSet`.
- Primitive elements boxed with `_box`.
- The same element-type errors as a list literal, plus `ast.Starred`
  rejected with `set unpacking is unsupported`.
- Storage in a local, and in a field annotated with the Java type
  (`from java.util import HashSet`).
- `len(s)`, `if s:`, and `if not s:` through `HashSet.size()`.

Out (later goals; state each in `docs/not-covered.md`):

- `in` membership, `for x in s`, `add`, and `remove`.
- The `set()` builtin, `frozenset`, and an empty set literal.
- `set[T]` annotations; `_annotation` still supports `list[X]` only.
- Heterogeneous sets via tagged unions or `Object` erasure.
- Iteration order, and non-hashable element diagnostics.

## Acceptance Checks

- `values = {"a", "b"}` emits `new java/util/HashSet`, two
  `add(Ljava/lang/Object;)Z` calls, no `anewarray`, and no `ArrayList`.
- `{1, "a"}` fails with `a set needs one element type`, `{*items}` fails
  with `set unpacking is unsupported`, and list error text is unchanged.
- `len(values)` emits `HashSet.size()I`; `if not values:` emits
  `size()` followed by `ifne`; `if values:` emits `size()` followed by
  `ifeq`.
- `just check`, `just lint`, `ripwire . --doc-drift --legend=compact`
  (`drift="0"`), and `python3 -m unittest discover -s tests -v` pass.
