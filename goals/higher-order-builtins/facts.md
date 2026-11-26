# Facts: Lower map, filter, and sorted with key

## Prerequisite (`function-values` — implemented & verified)

`goals/function-values/goal.md` is implemented and verified (commit
`64c065d`, `sila goals --json` reports `status: "implemented"`,
`verification_status: "verified"`), providing:
- Canonical abstract base classes `py2apk$fn$<mangled>` with
  `public abstract call(<params>)<result>`.
- `_function_type`, `_parse_function_type`, `_is_function_type` in
  `Py2APK/compiler_j/types.py`.
- `_emit_function_lambda`, `_emit_function_ref`, `_function_ref_binding`,
  `_emit_synthetic_class`, `_synthetic_init`, `_require_plain_lambda`,
  `_function_captures`, and `_inferred_lambda_result` in
  `Py2APK/compiler_j/lambdas.py`.
- `_dispatch_builtin` and `_dispatch_named_call` in
  `Py2APK/compiler_j/calls.py`.

## Current state (verified in code)

- **Non-emitting expression typing (`Py2APK/compiler_j/base.py`)**:
  - `_type_of` dispatches through `TYPE_HANDLERS` / `UNARY_HANDLERS`
    without emitting instructions. `_emit_lambda_value` in `lambdas.py`
    already uses `_inferred_lambda_result(self._type_of(node.body))` for
    zero-arg lambdas.
- **Sequence primitives (`Py2APK/compiler_j/base.py`)**:
  - `_sequence_element` extracts the element descriptor from both JVM
    arrays (`[T`) and `arraylist:T`.
  - `_emit_sequence_length` and `_emit_element` read length and indexed
    elements uniformly for arrays and `ArrayList`s.
- **Boxing & `ArrayList` (`Py2APK/compiler_j/types.py`, `base.py`,
  `collections.py`, `generators.py`)**:
  - `_BOX_METHODS` and `_UNBOX_METHODS` in `types.py` cover `I`, `Z`,
    `F`, `D`, `J`. `_box` in `base.py` boxes primitives via
    `_require_method`.
  - `_emit_list_slice` and `_emit_list_unit` in `collections.py` build
    `ArrayList` results typed as `arraylist:<element>`.
  - `_restore_yield_type` in `generators.py` unboxes primitives without
    a preceding `checkcast`. Inside `Comparator.compare(Object, Object)I`,
    operands have static type `Ljava/lang/Object;`, so the comparator
    emitter MUST emit `checkcast <boxed_owner>` before unboxing (and
    `checkcast <internal>` for references) to pass JVM/ART verification.
- **Call dispatch (`Py2APK/compiler_j/calls.py`)**:
  - `_builtin_call` and `_builtin_result` both delegate to
    `_dispatch_builtin(node, emit=...)`, which checks
    `_dispatch_named_call` (local `Callable` values) before falling
    through to `_builtin_call_body` (`cx=29`) or `_builtin_result_body`
    (`cx=31`).
  - Routing `map`, `filter`, and `sorted` in a helper called from
    `_dispatch_builtin` (after `_dispatch_named_call`) preserves local
    variable shadowing, validates keywords per builtin, and adds zero
    complexity to `_builtin_call_body`, `_builtin_result_body`, or
    `_guard_builtin_keywords`.
- **Synthetic class emission (`Py2APK/compiler_j/lambdas.py`)**:
  - `self.lambda_serial` numbers synthetic classes (`$lambda$N`,
    `$fn$N`). Reusing it for `Owner$cmp$N` prevents collisions.
  - `_emit_synthetic_class` already accepts `super_name`, `interface`,
    `captures` (fields + `<init>`), and `methods`.
- **Test catalog (`tests/_compile_helpers.py`)**:
  - `_alias_catalog` has `Integer.valueOf` and `Double.valueOf`, but
    lacks `Float.valueOf`, `Long.valueOf`, `Boolean.valueOf`,
    `ArrayList`, and `Collections.sort`.
  - `_collection_catalog` has `ArrayList.<init>`, `add`, `size`,
    `toArray`, but lacks `ArrayList.get` and `Collections.sort`.
  - `_compiled_outputs` currently only calls `_alias_catalog` and needs
    an optional `collections: bool = False` flag.
- **API levels (`templates/*/apktool.yml`)**:
  - All project templates set `minSdkVersion >= 21`.
  - `Integer.compare(II)I` is API 19; `Float.compare(FF)I`,
    `Double.compare(DD)I`, `String.compareTo(Ljava/lang/String;)I`,
    `Collections.sort`, and the `lcmp` opcode are API 1.

## Architectural Invariants & Constraints

- **Eager `ArrayList` return type (`arraylist:<R>`)**:
  - `map(fn, xs)`, `filter(fn, xs)`, and `sorted(xs, ...)` return
    `arraylist:<element>`, which interoperates with `for`, indexing,
    slicing, `len()`, and Java `ArrayList` parameters.
  - Nested `arraylist:arraylist:T` is unsupported (`_box` and
    `_restore_yield_type` only handle primitives, arrays, and `L...;`),
    so `map` rejects functions returning `V`, `null`, `lambda`, or
    `arraylist:...`.
  - Returning a fresh `ArrayList` preserves Python's non-mutating
    `sorted(xs)` contract.
- **Inline lambda type propagation**:
  - In `map(lambda x: ..., xs)`, `filter(lambda x: ..., xs)`, and
    `sorted(xs, key=lambda x: ...)`, `x`'s type is `T = _sequence_element(xs_type)`.
  - `_infer_lambda_result` validates plain arity and captures
    (`_require_plain_lambda`, `_function_captures`), temporarily binds
    `x: T` in a copy of `self.locals` (clearing any shadowed entry in
    `self.list_elements`), and runs `_inferred_lambda_result(self._type_of(node.body))`.
- **Sorting via `java/util/Collections.sort` + synthetic `Comparator`**:
  - Plain `sorted(xs)` (no `key` or literal `key=None`, and no `reverse`
    or literal `reverse=False`) copies `xs` into a fresh `ArrayList` and
    calls `Collections.sort(Ljava/util/List;)V` with no comparator class.
  - Otherwise, emit `Owner$cmp$N` (`implements java/util/Comparator`)
    holding `key` (if present) and `reverse` (`Z`, when non-constant or
    true). Inside `compare(Object, Object)I`, when `reverse` is true,
    swap local slots `1` and `2` before unboxing and comparing. Swapping
    avoids `ineg` `Integer.MIN_VALUE` overflow, preserves TimSort
    stability for ties, and supports both constant and runtime `bool`
    `reverse=` values.
  - Known differences from CPython (documented in `docs/not-covered.md`):
    `key=fn` is invoked per comparison (`O(n log n)`) rather than once
    per element; `Float.compare`/`Double.compare` follow Java total
    ordering for `NaN` and `-0.0`.
- **Complexity cap**: All `map`/`filter`/`sorted` typing and lowering
  live in dedicated helpers so `just lint` and
  `ripwire . --quality-delta` stay green.

## File & Interface Contracts
- `Py2APK/compiler_j/calls.py` — route `map`, `filter`, `sorted` from
  `_dispatch_builtin` (after `_dispatch_named_call`) to higher-order
  helper; keep `_builtin_call_body`, `_builtin_result_body`, and
  `_guard_builtin_keywords` unchanged.
- `Py2APK/compiler_j/collections.py` — `_dispatch_higher_order`,
  `_lower_map_filter`, `_lower_sorted`, and argument/keyword
  validators.
- `Py2APK/compiler_j/lambdas.py` — `_infer_lambda_result` and synthetic
  `Comparator` class emitter reusing `_emit_synthetic_class` and
  `self.lambda_serial`.
- `Py2APK/compiler_j/base.py` — cross-mixin method stubs on
  `_CompilerBase` for `ty`.
- `tests/_compile_helpers.py` — add `Collections.sort`, `ArrayList.get`,
  and missing primitive `valueOf` rows to `_collection_catalog`; add
  `collections` parameter to `_compiled_outputs`.
- `tests/test_cli_compile_lambda.py` — positive, negative, capture, and
  regression tests.
- `docs/covered.md`, `docs/not-covered.md`, `ROADMAP.md`
