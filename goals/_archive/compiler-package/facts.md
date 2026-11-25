# Facts: Split the Jasmin compiler into a package

## Architectural Invariants & Constraints
- Behavior stays strictly identical: pure structural split with verbatim method
  bodies where possible; no language semantics change.
- Packaging contract: `pyproject.toml` hardcodes `[tool.setuptools] packages =
  ["Py2APK", "Py2APK.stdlib", "Py2APK.stdlib.urllib"]`. Adding
  `"Py2APK.compiler_j"` is mandatory or wheel installs will omit the package.
- Public API stability: `Py2APK/compiler_j/__init__.py` MUST re-export all 4
  symbols imported across the repo (`__all__`):
  - `JasminCompilerError` (`cli_compile.py`, `cli_setup.py`, tests)
  - `LocalType` (`cli_compile.py:16`)
  - `compile_py2j` (`cli_compile.py`, `stdlib_cache.py`, tests)
  - `index_project_types` (`cli_compile.py`, `stdlib_cache.py`, tests)
  Because all 4 stay on `Py2APK.compiler_j`, `cli_compile.py`, `cli_setup.py`,
  and `stdlib_cache.py` need zero edits.
- Zero-conflict dispatch contract: `_expression` (cx ~19) and `_type_of`
  (cx ~18) are currently right under Ruff's `C901` cap (`max-complexity = 20`).
  Replacing monolithic `if isinstance(...)` chains with table-driven dispatch
  (`STMT_HANDLERS`, `EXPR_HANDLERS`, `TYPE_HANDLERS`, and operator-keyed
  `UNARY_HANDLERS` so `ast.USub` in `expr.py` and `ast.Not` in `compare.py`
  never collide) keeps dispatch complexity low and lets future goals register
  new AST nodes in their own family module without editing `base.py` or
  `compiler.py`.
- Deep-module anti-ravioli boundary: avoid sub-120 LOC slivers (`arithmetic`,
  `fstrings`, `builtins`, `subscripts` separately caused 84 cross-mixin edges
  and bidirectional `calls <-> builtins` / `loops -> subscripts` ping-pong).
  Keep shared low-level emitters (`_invoke`, `_static_invoke`,
  `_require_method`, `_constant`, `_keep`, `_cursor_name`, `_names_imported`,
  `_sequence_element`) in `base.py` and keep each family cohesive (150–530 LOC),
  cutting cross-mixin call edges roughly in half (~41 edges).
- Strict typing (`ty check Py2APK`): every family mixin inherits from
  `_CompilerBase` (in `base.py`), which declares all instance state, low-level
  emitters, and the small set of cross-family entry points (`_statement`,
  `_expression`, `_type_of`, `_condition_jump_false`, `_call`, `_assign`,
  `_store`, `_emit_yield`). C3 MRO resolves cleanly with zero `type: ignore`.
- Test discovery contract: shared test helpers live in
  `tests/_compile_helpers.py` (not prefixed with `test_`), so
  `python3 -m unittest discover -s tests` does not double-load or collect them.
  Deleting `tests/test_cli_compile.py` prevents duplicate test execution.
- Doc-drift contract: `AGENTS.md` (`CLAUDE.md` is a symlink), `docs/covered.md`,
  and `docs/interals.md` reference `compiler_j.py` and must be updated so
  `ripwire . --doc-drift --legend=compact` reports `drift="0"`.

## File & Interface Contracts
- Package layout (`Py2APK/compiler_j/`) and downstream goal ownership:
  - `__init__.py` — public re-exports (`JasminCompilerError`, `LocalType`,
    `compile_py2j`, `index_project_types`)
  - `types.py` — `JasminCompilerError`, `_MethodSignature`, `LocalType`, JVM
    descriptor/opcode constants & pure helpers
  - `indexer.py` — `index_project_types` and AST pre-indexing helpers
  - `base.py` — shared compiler base state, low-level emitters (`_invoke`,
    `_static_invoke`, `_require_method`, `_constant`, `_keep`, `_cursor_name`,
    `_names_imported`, `_sequence_element`), type resolution, and table-driven
    `_statement` / `_expression` / `_type_of` dispatch
  - `module.py` — module/class/method/import compilation, assignment & storage
  - `expr.py` — `BinOp`, `USub`, `IfExp`, `JoinedStr`, format specs, string
    method aliases (owns `true-division` & `string-split-join` in distinct fns)
  - `compare.py` — comparisons, truthiness, `BoolOp`, `Not`, `len`
    (owns future `comparison-expressions`)
  - `collections.py` — list/tuple/dict/set literals, list mutability analysis,
    subscript load/store/slicing (owns `hashset-literals` & `slice-values`)
  - `calls.py` — attribute/method/constructor resolution, builtins & stdlib
    bindings (owns `keyword-arguments`, `minsdk-api-check` hook, `math-time`)
  - `loops.py` — `if`, `while`, `for`, `break`/`continue`, `range`, slice iter
    (owns future `tuple-for`)
  - `lambdas.py` — listener lambdas & method references
    (owns future `function-values`)
  - `generators.py` — `yield` iterators & `@contextmanager` synthetic classes
  - `exceptions.py` — `try`/`except`/`finally`/`raise`/`with`/`suppress`
  - `compiler.py` — `_Compiler` composition + `compile_py2j` entry point
- Relevant non-package files:
  - `pyproject.toml`
  - `tests/_compile_helpers.py`
  - `tests/test_cli_compile_command.py`
  - `tests/test_cli_compile_<family>.py` (`collections`, `compare`, `fstring`,
    `generator`, `lambda`, `loop`, `stdlib`, `try`)
  - `AGENTS.md`, `docs/covered.md`, `docs/interals.md`
