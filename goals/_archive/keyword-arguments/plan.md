# Plan: Allow keyword arguments on calls

## Execution Steps
- [x] **Phase 1 — Classfile & `jars.db` Parameter-Name Extraction**:
  1. Add `param_names: tuple[str, ...] = ()` to `ClassMember` in
     `Py2APK/classfile.py`. Parse `MethodParameters` first; when absent, parse
     `Code` -> `LocalVariableTable` (`start_pc == 0`) using exact JVM slot
     offsets (`0` = `this` for instance methods; `J`/`D` advance by 2 slots).
  2. Add `param_names TEXT NOT NULL DEFAULT ''` to `methods` in
     `MEMBER_SCHEMA` and `PROJECT_SCHEMA` in `Py2APK/jar_index.py`, copy it in
     `_copy_members`, return it from `JarCatalog.methods`, and rebuild cached
     `*.sqlite` files automatically when `param_names` is missing.
  3. Add unit tests in `tests/test_jars.py` covering `MethodParameters`,
     `LocalVariableTable` (including `J`/`D` 2-slot parameters and static vs
     instance slot 0), and automatic rebuild of a legacy `jar.sqlite`.
- [x] **Phase 2 — Project & Local Signature Parameter Names**:
  1. Add `parameter_names: tuple[str, ...] = ()` to `_MethodSignature` and
     `constructor_names: tuple[str, ...] = ()` to `LocalType` in
     `Py2APK/compiler_j/types.py`.
  2. Populate parameter names from Python AST in `Py2APK/compiler_j/indexer.py`
     (`_index_functions`, `_index_class`) and `Py2APK/compiler_j/module.py`
     (`_method_signature`, `_function_signature`, `_constructor_signature`).
- [x] **Phase 3 — Keyword Argument Resolution & Reordering in `calls.py`**:
  1. Add dedicated helpers in `Py2APK/compiler_j/calls.py` (keeping `_call` and
     `_search_method` under Ruff C901 max-complexity 20) to validate keywords
     (reject `**kwargs`, duplicate names, positional overlaps, unknown names),
     map positional + keyword args onto candidate parameter names, and reorder
     expressions to formal parameter order `0..N-1`.
  2. Wire keyword resolution through `_call`, `_emit_constructor`,
     `_bound_constructor`, `super().method(...)`, `_type_of_call`, and
     `_signature_for_call`, reusing `_best_signature` type scoring after
     reordering.
  3. Keep `suppress(...)`, `list.append(...)`, and `super(...)` rejecting
     unsupported keywords.
- [x] **Phase 4 — Compiler Tests, Docs & Quality Gates**:
  1. Create `tests/test_cli_compile_calls.py` (using `tests/_compile_helpers.py`)
     covering keyword arguments on instance methods, static methods,
     constructors, `super()` calls, project-local functions/methods, overload
     disambiguation by parameter name, and all rejection cases (`**kwargs`,
     duplicates, positional collision, unknown keyword, nameless overload).
  2. Update `docs/covered.md` and `docs/not-covered.md` to reflect keyword
     argument support.
  3. Run and leave green: `just check`, `just lint`,
     `python3 -m unittest discover -s tests -v`,
     `ripwire . --doc-drift --legend=compact` (`drift="0"`), and
     `ripwire . --quality-delta --legend=compact`.
  4. Write `goals/keyword-arguments/evidence.md` with command outputs,
     `## Memory Writes`, and a standalone `Verdict: PASS` line.
- [x] **Phase 5 — Attribution & Registry Closure**:
  1. Check all completed tasks `- [x]` in `goals/keyword-arguments/plan.md`.
  2. Commit with `(goals/keyword-arguments/goal.md)` under `feat:`.
  3. Certify via `sila goals certify keyword-arguments --evidence=goals/keyword-arguments/evidence.md`.
  4. Run `sila goals`, commit updated `goals/README.md` and certification
     receipts, and verify `sila goals --json` reports `unverified_count: 0`
     with a clean `git status`.
