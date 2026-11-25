# Facts: Allow keyword arguments on calls

## Architectural Invariants & Constraints
- JVM parameter-name reality (`android.jar` verified): Android SDK `android.jar`
  classfiles have `MethodParameters = False` and `LocalVariableTable = True`
  inside the `Code` attribute (e.g. `AlertDialog$Builder.setTitle` has
  `['this', 'titleId']` and `['this', 'title']`). `Py2APK/classfile.py` MUST
  read `MethodParameters` first (for Java 8+ `-parameters` jars and interfaces
  with no `Code` attribute) and fall back to `Code` -> `LocalVariableTable`
  entries with `start_pc == 0`.
- JVM local-slot width rule: in `LocalVariableTable`, instance methods reserve
  slot `0` for `this` (static methods start at slot `0`), and `J` (`long`) /
  `D` (`double`) parameters consume 2 slots each. Parameter names must be
  mapped by exact slot index, never by naive list slicing.
- SQLite schema & cache invalidation: `MEMBER_SCHEMA` and `PROJECT_SCHEMA` in
  `Py2APK/jar_index.py` add `param_names TEXT NOT NULL DEFAULT ''` to `methods`
  (comma-separated names, or `""` when absent). Because cached `*.sqlite` files
  already exist in `~/.cache/py2apk/`, `index_jar` MUST detect missing
  `param_names` column (or bumped `PRAGMA user_version`) and rebuild stale
  cache databases automatically.
- Backward-compatible schema default: `DEFAULT ''` preserves existing test
  fixtures in `tests/_compile_helpers.py` and `tests/test_jars.py` that insert
  into `methods` without specifying `param_names`.
- Single caller of `JarCatalog.methods`: `Py2APK/compiler_j/calls.py` is the
  only caller of `JarCatalog.methods` in the repo, so extending its return
  tuple with `param_names` has minimal blast radius.
- Complexity cap (Ruff C901, `max-complexity = 20`): `_CallsMixin._search_method`
  is already at cx=20 and `_CallsMixin._call` is at cx=19. Keyword matching,
  validation, and argument reordering MUST live in dedicated helpers in
  `calls.py` rather than adding branches inline.
- Call-site semantics:
  - Reject `**kwargs` (`kw.arg is None`), duplicate keyword names, keyword
    colliding with a positional slot, unknown keyword names, and missing
    required parameters with `JasminCompilerError`.
  - Reorder call expressions to formal parameter order `0..N-1` before
    `_emit_arguments` pushes them onto the JVM stack.
  - Apply the same resolution across instance methods, static methods,
    constructors, `super().method(...)`, `_type_of_call`, and
    `_signature_for_call`.
  - Keep `suppress(...)`, `list.append(...)`, and `super(...)` rejecting
    unsupported keywords.
- Doc-drift contract: update `docs/covered.md` and `docs/not-covered.md` if
  they state keyword arguments are unsupported, and leave
  `ripwire . --doc-drift --legend=compact` at `drift="0"`.

## File & Interface Contracts
- Relevant files:
  - `Py2APK/classfile.py` — parameter-name tuple on `ClassMember`,
    `MethodParameters` + `Code`/`LocalVariableTable` parsing
  - `Py2APK/jar_index.py` — parameter-names column in `MEMBER_SCHEMA` and
    `PROJECT_SCHEMA`, stale-cache rebuild in `index_jar`, `JarCatalog.methods`
  - `Py2APK/compiler_j/types.py` — `_MethodSignature.parameter_names` and
    `LocalType.constructor_names`
  - `Py2APK/compiler_j/indexer.py` — record AST parameter names in
    `_index_functions` and `_index_class`
  - `Py2APK/compiler_j/module.py` — record AST parameter names in
    `_method_signature`, `_function_signature`, and `_constructor_signature`
  - `Py2APK/compiler_j/calls.py` — keyword validation, overload matching, and
    argument reordering helpers
  - `tests/test_jars.py` — classfile `MethodParameters`/`LocalVariableTable`
    (including `J`/`D` 2-slot offsets) and stale-`jar.sqlite` rebuild tests
  - `tests/test_cli_compile_calls.py` — keyword lowering tests (instance,
    static, constructor, `super()`, project-local defs, overload disambiguation
    by name, and rejection of `**kwargs`/duplicates/collisions/unknown names)
  - `docs/covered.md`, `docs/not-covered.md`
