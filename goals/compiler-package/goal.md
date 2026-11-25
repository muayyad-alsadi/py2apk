# Goal: Split the Jasmin compiler into a package

## Goal Description
Replace `Py2APK/compiler_j.py` with the package `Py2APK/compiler_j/` so parallel
language goals edit disjoint family modules and test files with minimal merge
conflicts or locks.

`Py2APK/compiler_j/__init__.py` re-exports `compile_py2j`, `JasminCompilerError`,
`LocalType`, and `index_project_types` via explicit export list, so
`cli_compile.py`, `cli_setup.py`, and `stdlib_cache.py` require no edits.

Split `_Compiler` into a shared compiler base (`base.py`) with table-driven
AST dispatch (`STMT_HANDLERS`, `EXPR_HANDLERS`, `TYPE_HANDLERS`,
`UNARY_HANDLERS`) and deep, cohesive lowering-family mixins: `types.py`,
`indexer.py`, `module.py`, `expr.py`, `compare.py`, `collections.py`,
`calls.py`, `loops.py`, `lambdas.py`, `generators.py`, `exceptions.py`, and
`compiler.py`. Register `Py2APK.compiler_j` in `pyproject.toml`.

Extract shared test fixtures to `tests/_compile_helpers.py`, move
`CompileCommandTests` to `tests/test_cli_compile_command.py`, split
`AliasLoweringTests` into `tests/test_cli_compile_<family>.py` (`fstring`,
`compare`, `collections`, `lambda`, `loop`, `generator`, `try`, `stdlib`), and
delete `tests/test_cli_compile.py`. `python3 -m unittest discover -s tests`
runs the full suite with identical behavior.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 1
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/compiler_j.py
- Py2APK/compiler_j/
- pyproject.toml
- tests/test_cli_compile.py
- tests/_compile_helpers.py
- tests/test_cli_compile_command.py
- AGENTS.md
- docs/covered.md
- docs/interals.md

## References
- **Shared Understanding & Fact Sheet**: [`goals/compiler-package/facts.md`](facts.md)
- **Execution Plan**: [`goals/compiler-package/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/compiler-package/goal.md)` or `[goal: compiler-package]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/compiler-package/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.
