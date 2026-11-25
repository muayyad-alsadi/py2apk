# Goal: Allow keyword arguments on calls

## Goal Description
Support keyword arguments on Java/Android calls (`jars.db` overloads) and
project-local Python functions, methods, and constructors, unlocking
builder-style Android calls and aliases that require keywords.

Extract JVM parameter names in `Py2APK/classfile.py` (prefer `MethodParameters`
attribute; fall back to `Code` -> `LocalVariableTable` at `start_pc == 0`,
accounting for 2-slot `J`/`D` parameters, which is how `android.jar` stores
names like `title`, `message`, `context`, `attrs`). Persist `param_names` in
`Py2APK/jar_index.py` with automatic cache-schema invalidation for stale
`*.sqlite` files. Record Python AST parameter names on `_MethodSignature` and
`LocalType` in `Py2APK/compiler_j/types.py`, `indexer.py`, and `module.py`.

Resolve and reorder keyword arguments to formal parameter order in
`Py2APK/compiler_j/calls.py` across instance methods, static methods,
constructors, `super()` calls, type inference (`_type_of_call`), and
list-mutability preview (`_signature_for_call`). Reject `**kwargs`, duplicate
keywords, positional/keyword collisions, unknown names, and ambiguous or
nameless overloads with clear `JasminCompilerError` messages.

## Dependencies & Execution Order
- **Mode**: Dependent (requires prerequisite goals)
- **Depends On**: compiler-package
- **Sequence**: 3
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/classfile.py
- Py2APK/jar_index.py
- Py2APK/compiler_j/types.py
- Py2APK/compiler_j/indexer.py
- Py2APK/compiler_j/module.py
- Py2APK/compiler_j/calls.py
- tests/test_jars.py
- tests/test_cli_compile_calls.py
- docs/covered.md
- docs/not-covered.md

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
