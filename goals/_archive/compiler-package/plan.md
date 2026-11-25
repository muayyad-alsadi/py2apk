# Plan: Split the Jasmin compiler into a package

## Execution Steps
- [x] **Phase 1 — Package Skeleton, Base State & Table-Driven Dispatch**:
  1. Create `Py2APK/compiler_j/` and add `"Py2APK.compiler_j"` to
     `[tool.setuptools] packages` in `pyproject.toml`.
  2. Extract `types.py` (`JasminCompilerError`, `_MethodSignature`, `LocalType`,
     descriptor/opcode tables, and pure helpers) and `indexer.py`
     (`index_project_types` and `_index_*` / `_collect_imports` /
     `_type_from_expr`).
  3. Create `base.py` with a shared compiler base class: `__init__` state,
     `error`, shared low-level emitters (`_instruction`, `_label`,
     `_load_local`, `_emit_int`, `_box`, `_cast_descriptor`, `_constant`,
     `_invoke`, `_static_invoke`, `_require_method`, `_keep`, `_cursor_name`,
     `_names_imported`, `_sequence_element`, `_annotation`,
     `_resolve_type_name`, `_compatible`, `_related`), cross-family entry stubs,
     and table-driven dispatch (`STMT_HANDLERS`, `EXPR_HANDLERS`,
     `TYPE_HANDLERS`, `UNARY_HANDLERS` merged across subclasses).
- [x] **Phase 2 — Split Lowering Families & Public Re-exports**:
  1. Move `_Compiler` methods verbatim into deep, cohesive family mixins
     inheriting from the shared base class: `module.py`, `expr.py`,
     `compare.py`, `collections.py`, `calls.py`, `loops.py`, `lambdas.py`,
     `generators.py`, `exceptions.py`.
  2. Each mixin registers its AST node handlers in its own `STMT_HANDLERS` /
     `EXPR_HANDLERS` / `TYPE_HANDLERS` / `UNARY_HANDLERS` dict so adding a new
     AST construct never requires editing `base.py` or `compiler.py`.
  3. Create `compiler.py` composing `_Compiler` and `compile_py2j`, create
     `__init__.py` re-exporting `JasminCompilerError`, `LocalType`,
     `compile_py2j`, and `index_project_types`, and delete
     `Py2APK/compiler_j.py`. Verify `cli_compile.py`, `cli_setup.py`, and
     `stdlib_cache.py` remain untouched.
- [x] **Phase 3 — Split Compiler Tests by Family**:
  1. Extract shared test fixtures (`FakeProgress`, `_compiled_*`, `_*_catalog`)
     into `tests/_compile_helpers.py`.
  2. Move `CompileCommandTests` to `tests/test_cli_compile_command.py` and split
     `AliasLoweringTests` into `tests/test_cli_compile_<family>.py` (`fstring`,
     `compare`, `collections`, `lambda`, `loop`, `generator`, `try`, `stdlib`),
     then delete `tests/test_cli_compile.py`.
  3. Confirm total discovered test count in `python3 -m unittest discover -s tests -v`
     matches or exceeds pre-split count with zero skipped/duplicate tests.
- [x] **Phase 4 — Docs, Static Gates & Packaging Verification**:
  1. Update `compiler_j.py` references in `AGENTS.md`, `docs/covered.md`, and
     `docs/interals.md`.
  2. Run and leave green: `just check` (`ty check Py2APK`), `just lint`
     (`ruff check .`), and `python3 -m unittest discover -s tests -v`.
  3. Validate packaging: `python3 -m pip install --no-deps --no-build-isolation .`,
     `python3 -c "import Py2APK; from Py2APK.compiler_j import JasminCompilerError, LocalType, compile_py2j, index_project_types"`,
     and `py2apk --help`.
  4. Run `ripwire . --doc-drift --legend=compact` (require `drift="0"`) and
     `ripwire . --quality-delta --legend=compact` (no regressions).
  5. Write `goals/compiler-package/evidence.md` with command outputs,
     `## Memory Writes`, and a standalone `Verdict: PASS` line.
- [x] **Phase 5 — Attribution & Registry Closure**:
  1. Check all completed tasks `- [x]` in `goals/compiler-package/plan.md`.
  2. Commit with `(goals/compiler-package/goal.md)` under `refactor:`.
  3. Certify via `sila goals certify compiler-package --evidence=goals/compiler-package/evidence.md`.
  4. Run `sila goals`, commit updated `goals/README.md` and certification
     receipts, and verify `sila goals --json` reports `unverified_count: 0`
     with a clean `git status`.
