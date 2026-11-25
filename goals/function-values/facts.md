# Facts: Use a function as a value

## Current state (verified in code, 2026-11-25)

- **Listener lambdas (`Py2APK/compiler_j/lambdas.py`)**:
  - `TYPE_HANDLERS` maps `ast.Lambda` to `_type_of_lambda`, returning `"lambda"`.
  - `ast.Lambda` is not in `EXPR_HANDLERS`; using a `lambda` outside a call
    argument currently raises `unsupported expression Lambda`.
  - `_emit_lambda` (lines 164-209) and `_lambda_class` (lines 211-327) emit
    `{class_internal}$lambda${serial}` extending `java/lang/Object` and
    implementing the target Java interface, with captured locals as `.field`s.
  - `_is_method_reference` (lines 26-33) and `_type_of_attribute`
    (`calls.py:876-878`) also return `"lambda"` for `self.method` references.
- **Call dispatch (`Py2APK/compiler_j/calls.py`)**:
  - `_call` (line 783) routes non-type `ast.Name` calls to `_builtin_call`
    (lines 488-544); `_type_of_call` (line 888) routes them to `_builtin_result`
    (lines 635-689).
  - `_emit_arguments` (lines 1088-1090) routes every `ast.Lambda` or
    `self.method` argument unconditionally to `_emit_listener`, which requires
    `self.catalog.is_interface(...)`.
- **Name lookup & assignment (`Py2APK/compiler_j/module.py`)**:
  - `_load_name` (lines 65-73) and `_type_of_name` (lines 75-82) look up only
    `self.locals` and raise `unknown local` for module functions.
  - `_ann_assign` (lines 48-63) evaluates `node.value` before reading
    `node.annotation`.
  - `_record_from_import` (lines 210-222) allows only `typing.{TYPE_CHECKING,
    cast}` and `collections.abc.Iterator`.
- **Indexing (`Py2APK/compiler_j/indexer.py`)**:
  - `_collect_imports` (lines 242-247) recognizes only `collections.abc.Iterator`.
  - `_type_from_expr` (lines 261-305) raises `unsupported annotation Subscript`
    for `Callable[[...], R]`.
- **Type compatibility & annotations (`Py2APK/compiler_j/base.py`)**:
  - `_compatible` (lines 292-306) accepts `actual == "lambda"` only when
    `_is_interface_type(wanted)` is true.
  - `_annotation` (lines 326-348) handles primitives, `list[T]`, and `Iterator`.
- **Build pipeline (`Py2APK/compiler_j/compiler.py`, `Py2APK/cli_build.py`)**:
  - `compile_py2j` writes `extra_classes` next to `output`.
  - `build_project` passes all `.py2apk/j/**/*.j` to `jasmin -d .py2apk/classes`,
    then passes `sorted(classes_root.rglob("*.class"))` to D8.

## Architectural Invariants & Constraints

- **No `.interface` emission**: `ROADMAP.md` specifies `.class` and `.super`,
  never `.interface`. Function base types use `.class public abstract`.
- **Single-pass strict typing**: Python `ast.Lambda` parameters cannot carry
  annotations. A lambda with >= 1 parameters therefore requires an expected
  `Callable[[...], R]` type from its context (`ast.AnnAssign`, `ast.Call`
  argument, or `ast.Return`). Zero-argument lambdas (`lambda: expr`) and
  module function references (`g = square`) have known types without annotation.
- **Canonical, injective, reversible base-class names**: Function base types
  must resolve identically across modules, `indexer.py`, and `base.py`, with no
  `/` or `;` in the filename or class name:
  - Internal name: `py2apk$fn$<params>_r_<result>` (root package).
  - Descriptor encoding: primitives/void unchanged; `[` -> `A`; `L<name>;` ->
    `L<escaped>_e` (`_`->`_u`, `$`->`_d`, `/`->`_s`); params joined by `_p_`.
  - Abstract base `.j` uses a fixed `.source py2apk$fn` so multiple modules
    emitting the same signature produce byte-identical `.class` files in
    `.py2apk/classes/`, giving D8 a single `.class` with zero duplicate errors.
- **Complexity cap (Ruff C901, `max-complexity = 20` & `ripwire --quality-delta`)**:
  Existing functions are already near or above the cap:
  - `_CallsMixin._builtin_result` (`calls.py:635`): `cx=31`
  - `_CallsMixin._builtin_call` (`calls.py:488`): `cx=29`
  - `_ModuleMixin._record_from_import` (`module.py:205`): `cx=22`
  - `_type_from_expr` (`indexer.py:261`): `cx=19`
  - `_LambdasMixin._lambda_class` (`lambdas.py:211`): `cx=17`
  All new dispatch, import, annotation, and emission logic MUST live in small,
  pure helpers rather than adding inline branches to these functions.
- **Preserve listener lambdas & `self.method`**: `"lambda"` sentinel and
  `_emit_listener` stay intact for Java interface parameters.
- **Doc-drift contract**: Run `ripwire . --doc-drift --legend=compact` after
  updating `docs/covered.md`, `docs/not-covered.md`, and `ROADMAP.md`, and leave
  `drift="0"`.

## File & Interface Contracts
- `Py2APK/compiler_j/types.py` — `_PY2APK_CALLABLE`, `_function_type`,
  `_parse_function_type`, `_is_function_type`, `_callable_parts`
- `Py2APK/compiler_j/indexer.py` — `Callable` import + `_type_from_expr` support
- `Py2APK/compiler_j/base.py` — `_compatible`, `_annotation`, compiler state
  (`emitted_function_bases`, `non_final_locals`)
- `Py2APK/compiler_j/module.py` — `Callable` imports, `_ann_assign`,
  `_emit_return`, `_load_name`, `_type_of_name`, non-final analysis
- `Py2APK/compiler_j/lambdas.py` — `EXPR_HANDLERS[ast.Lambda]`, abstract base
  emitter, function-lambda & function-ref emitters, `_non_final_names`
- `Py2APK/compiler_j/calls.py` — local function-value call dispatch and
  `_emit_arguments` branching between interface and `Callable`
- `tests/test_cli_compile_lambda.py` — contract, positive, negative,
  multi-module, and listener-regression tests
- `docs/covered.md`, `docs/not-covered.md`, `ROADMAP.md`
