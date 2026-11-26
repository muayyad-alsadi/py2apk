# Plan: Lower map, filter, and sorted with key

Goal: lower single-iterable `map(fn, xs)`, `filter(fn, xs)`, and
`sorted(xs, key=fn, reverse=...)` on arrays and `ArrayList`s, building
on `function-values` (implemented and verified at commit `64c065d`).

## Locked Decisions

- [x] **D1 Scope.**
  - **In scope**:
    - `map(fn, xs)` on one array or `ArrayList` (`[T` or `arraylist:T`),
      where `fn` is a `Callable[[T], R]` local, a module function ref
      (same module or imported), or an inline `lambda x: ...` (param
      type `T` inferred from `xs`; effectively-final captures allowed).
      Returns `arraylist:R` where `R` is not `V`, `null`, `lambda`, or
      `arraylist:...`.
    - `filter(fn, xs)` on one array or `ArrayList`, where `fn` returns
      `bool` (`Z`). Returns `arraylist:T`.
    - `sorted(xs)`, `sorted(xs, reverse=flag)`, `sorted(xs, key=fn)`,
      and `sorted(xs, key=fn, reverse=flag)` on `[T` or `arraylist:T`.
      Natural order (no `key` or literal `key=None`) supports `I`, `F`,
      `D`, `J`, and `Ljava/lang/String;`. Keyed sort supports any `T`
      when `key` returns `I`, `F`, `D`, `J`, or `Ljava/lang/String;`.
      `reverse` accepts any `bool` (`Z`) expression (constant or
      runtime). Returns a fresh `arraylist:T` without mutating `xs`.
  - **Out of scope**:
    - Multi-iterable `map(fn, xs, ys)` and lazy `Iterator` semantics.
    - Builtin references like `map(str, xs)` or `map(int, xs)` (users
      write `map(lambda x: str(x), xs)`).
    - `filter(None, xs)` truthiness filtering.
    - `sorted` on `bool` (`Z`), `B`/`S`/`C`, or custom objects without
      `key=`.
    - `reversed`, `enumerate`, `zip`, `any`, `all`, `sum`, `round`.
- [x] **D2 Comparator synthesis & sorting semantics.**
  - Plain `sorted(xs)` (no `key` or literal `key=None`, and no `reverse`
    or literal `reverse=False`) copies `xs` into a fresh `ArrayList` and
    calls `Collections.sort(Ljava/util/List;)V` (no synthetic class).
  - Otherwise, emit synthetic `Owner$cmp$N` (allocating `N` from
    `self.lambda_serial`, reusing `_emit_synthetic_class` with
    `super_name="java/lang/Object"` and
    `interface="java/util/Comparator"`) holding `key` (if present) and
    `reverse` (`Z` field when runtime or true), and call
    `Collections.sort(Ljava/util/List;Ljava/util/Comparator;)V`.
  - Inside `compare(Ljava/lang/Object;Ljava/lang/Object;)I`:
    - When `reverse` is true (statically or via `getfield` + `ifeq`),
      swap local slots `1` and `2` (`aload_1`, `aload_2`, `astore_1`,
      `astore_2`) before unboxing and comparing. This avoids `ineg`
      `Integer.MIN_VALUE` overflow, preserves TimSort stability on ties,
      and handles runtime `reverse=flag`.
    - Emit `checkcast <boxed_owner>` before primitive unboxing
      (`intValue`, `floatValue`, `doubleValue`, `longValue`,
      `booleanValue`) and `checkcast <internal>` for references so
      JVM/ART verification succeeds.
    - Compare keys with `Integer.compare(II)I` (`I`), `lcmp` (`J`),
      `Float.compare(FF)I` (`F`), `Double.compare(DD)I` (`D`), or
      `String.compareTo(Ljava/lang/String;)I` (`Ljava/lang/String;`).
  - Document in `docs/not-covered.md` that `key=fn` runs per comparison
    (`O(n log n)`) and `Float`/`Double` follow Java total ordering for
    `NaN` and `-0.0`.
- [x] **D3 Evaluation order, local slots & dispatch seam.**
  - Route `map`, `filter`, `sorted` via a new higher-order dispatch
    helper called from `_dispatch_builtin` (`calls.py`) right after
    `_dispatch_named_call` so local `Callable` shadowing keeps working
    and `_builtin_call_body`, `_builtin_result_body`, and
    `_guard_builtin_keywords` gain zero complexity.
  - Evaluate `fn` before `xs` in `map`/`filter` (left-to-right Python
    order) and hold both in temp locals.
  - In `filter`, store each iteration's element in a typed local slot
    once per iteration (handles 2-slot `D`/`J` values cleanly and avoids
    re-reading `seq[idx]`).

## Phase 1 — Contracts, Catalog & Red Tests

- [x] **1.1 Cross-mixin stubs in `Py2APK/compiler_j/base.py`.**
  Add stub declarations on `_CompilerBase` for the new cross-mixin
  helpers (`_dispatch_higher_order`, `_infer_lambda_result`,
  `_emit_comparator`) so `ty` stays strict and green.
- [x] **1.2 Lambda body return-type helper in `Py2APK/compiler_j/lambdas.py`.**
  Add `_infer_lambda_result(self, node: ast.Lambda, param_types: tuple[str, ...]) -> str`:
  - Call `self._require_plain_lambda(node, len(param_types))` and
    `self._function_captures(node, set(param_names))`.
  - Temporarily bind each `(name, (0, desc))` in a copy of `self.locals`
    and remove shadowed names from a copy of `self.list_elements` inside
    `try ... finally`.
  - Return `_inferred_lambda_result(self._type_of(node.body))` without
    emitting instructions.
- [x] **1.3 Test catalog support in `tests/_compile_helpers.py`.**
  - Add `java/util/Collections.sort(Ljava/util/List;)V`,
    `java/util/Collections.sort(Ljava/util/List;Ljava/util/Comparator;)V`,
    `java/util/ArrayList.get(I)Ljava/lang/Object;`, and `Float.valueOf`,
    `Long.valueOf`, `Boolean.valueOf` to `_collection_catalog`.
  - Add `collections: bool = False` option to `_compiled_outputs`.
- [x] **1.4 Contract tests first (red) in `tests/test_cli_compile_lambda.py`.**
  Add `HigherOrderBuiltinTests` with positive assertions on emitted
  Jasmin for `map`, `filter`, and `sorted(..., key=..., reverse=...)`,
  run `python3 -m unittest tests.test_cli_compile_lambda -v`, and confirm
  they fail with `unsupported builtin`.

## Phase 2 — Core Implementation

- [x] **2.1 Dispatch & argument validation (`calls.py`, `collections.py`).**
  - Call `self._dispatch_higher_order(node, emit=emit)` from
    `_dispatch_builtin` right after `_dispatch_named_call`.
  - Validate arity and keywords in dedicated helpers: reject all
    keywords on `map`/`filter`; allow only `key` and `reverse` (no
    duplicates, no `**kwargs`, exactly 1 positional arg) on `sorted`;
    treat literal `key=None` as no key; require `reverse` to type as
    `Z`.
- [x] **2.2 `map(fn, xs)` lowering (`collections.py`).**
  - Resolve `T = _sequence_element(xs_type)` (error if `None`).
  - Resolve `fn` signature `((T,), R)` accepting `Callable[[T], R]`
    local, module function ref (`_function_ref_binding`), or inline
    `lambda x: ...` (`_infer_lambda_result`); reject arity != 1, param
    mismatch, or `R` in `{"V", "null", "lambda"}` / `arraylist:...`.
  - Emit `fn` -> temp `fn_local`, `xs` -> temp `seq`, fresh `ArrayList`
    -> temp `out`, loop `0..len(seq)` via `_emit_sequence_length` and
    `_emit_element`, `invokevirtual <base>/call(<T>)<R>`, `_box(R)`,
    `ArrayList.add` + `pop`. Load `out`, return `arraylist:<R>`.
- [x] **2.3 `filter(fn, xs)` lowering (`collections.py`).**
  - Same sequence and `fn` resolution requiring `R == "Z"`.
  - Emit `fn` -> `fn_local`, `xs` -> `seq`, fresh `ArrayList` -> `out`.
  - Loop `0..len(seq)`: store `_emit_element` into typed local `elem`,
    call `fn_local.call(elem)`, `ifeq skip`, load `out` + `elem`,
    `_box(T)`, `ArrayList.add` + `pop`. Load `out`, return
    `arraylist:<T>`.
- [x] **2.4 `sorted(xs, key=..., reverse=...)` & comparator synthesis
  (`collections.py`, `lambdas.py`).**
  - Copy `xs` into a fresh `ArrayList` (preserving `xs` unchanged).
  - For plain ascending sort (no `key` / literal `key=None`, no
    `reverse` / literal `reverse=False`), require `T` in
    `{"I", "F", "D", "J", "Ljava/lang/String;"}` and invoke
    `Collections.sort(Ljava/util/List;)V`.
  - Otherwise, emit synthetic `Owner$cmp$N` in `lambdas.py` via
    `_emit_synthetic_class` (incrementing `self.lambda_serial`), with
    slot-swap `reverse` handling, `checkcast` before unboxing, key
    invocation (when `key` is provided), and `Integer.compare` / `lcmp`
    / `Float.compare` / `Double.compare` / `String.compareTo`; register
    `(f"{simple}$cmp${N}.j", text)` in `self.extra_classes` and invoke
    `Collections.sort(Ljava/util/List;Ljava/util/Comparator;)V`.
  - Return `arraylist:<T>`.

## Phase 3 — Verification, Runtime & Evidence

- [x] **3.1 Positive tests** in `tests/test_cli_compile_lambda.py`:
  - `map(square, xs)`, `map(fn_local, xs)`, `map(lambda x: x + k, xs)`
    (with captured effectively-final local `k`) on `int[]`, `double[]`,
    and `ArrayList` (`for y in map(...)`).
  - `filter(is_even, xs)`, `filter(lambda x: x > 0, xs)` (including `J`
    or `D` elements to verify 2-slot local handling).
  - `sorted(xs)`, `sorted(xs, key=None, reverse=False)` (no comparator
    emitted), `sorted(xs, reverse=True)`, `sorted(xs, reverse=flag)`
    (runtime `bool`), `sorted(words, key=len_fn)`, and
    `sorted(words, key=lambda s: len(s), reverse=True)` (verifying
    `checkcast`, slot-swap, and `Collections.sort`).
  - Local `Callable` shadowing `map`/`filter`/`sorted` still calls the
    local.
- [x] **3.2 Negative tests** in `tests/test_cli_compile_lambda.py`:
  - `map(fn, xs, ys)` (multi-iterable), `map(str, xs)` (builtin),
    `map(void_fn, xs)` (void result), `map(list_fn, xs)` (`arraylist:`
    result), `map(fn, xs, extra=1)` (keyword on `map`), non-final
    capture in `map(lambda x: x + k, xs)`.
  - `filter(None, xs)`, `filter(int_fn, xs)` (non-bool predicate),
    `filter(fn, xs, extra=1)`.
  - `sorted(xs, cmp=fn)` (unknown keyword), `sorted(xs, **kw)`,
    `sorted(xs, reverse=1)` (non-bool `reverse`), `sorted(objs)`
    (non-comparable element without `key`), `sorted(xs, key=bad_key)`
    (unsupported key return type), non-sequence `xs` (e.g. `int`).
- [x] **3.3 Static & unit gates.**
  - `python3 -m unittest discover -s tests -v` exits 0.
  - `just check` (`ty check Py2APK`) exits 0.
  - `just lint` (`ruff check .`) exits 0.
  - `ripwire . --quality-delta --legend=compact` exits 0 (no new debt).
- [x] **3.4 End-to-end build & JVM runtime verification.**
  - Build a sample project with `py2apk build` exercising `map`,
    `filter`, `sorted(xs)`, `sorted(xs, reverse=True)`,
    `sorted(xs, reverse=flag)`, and
    `sorted(words, key=lambda s: len(s), reverse=True)`.
  - Inspect `Owner$cmp$N.class` with `javap -v` and run a driver class
    on the host JVM (`java -cp .py2apk/classes ...`) verifying actual
    sorted/mapped/filtered outputs, non-mutation of input, and stability
    on ties (proves JVM bytecode verifier passes on `checkcast` +
    unboxing + `Comparator`).
- [x] **3.5 Docs & doc-drift.**
  Update `docs/covered.md`, `docs/not-covered.md` (document `key`
  per-comparison invocation and `Float`/`Double` `NaN`/`-0.0` order),
  and `ROADMAP.md`; verify `ripwire . --doc-drift --legend=compact`
  reports `drift="0"`.
- [x] **3.6 Evidence.**
  Write `goals/higher-order-builtins/evidence.md` with commands, exit
  codes, test counts, runtime output, `## Memory Writes`
  (`sila/memory-writes/v1`), and standalone `Verdict: PASS`.

## Phase 4 — Attribution & Registry Closure

- [x] **4.1 Check all tasks** in Phases 1-3 (`- [x]`).
- [ ] **4.2 Commit** locally with `(goals/higher-order-builtins/goal.md)`
  under `feat:`. No remote push without user consent.
- [ ] **4.3 Certify** via
  `sila goals certify higher-order-builtins --evidence=goals/higher-order-builtins/evidence.md`.
- [ ] **4.4 Reconcile** via `sila goals` and commit updated
  `goals/README.md` and certification receipts.
- [ ] **4.5 Verify** `sila goals --json` (`status: "implemented"`,
  `verification_status: "verified"`, `unverified_count: 0`, clean
  `git status`).

## Memory Protocol (`goal.md`)

- **Prime**: run `sila prime` at task start (record timestamp).
- **Recall**: run `sila recall "higher order builtin"` and
  `sila recall "Comparator"` before Phase 1 (record queries and counts).
- **Writes**: record `sila case` for comparator synthesis + slot-swap
  reverse handling (grounded to `Py2APK/compiler_j/lambdas.py` and
  `collections.py`), and `sila lesson` for any bug fixed in Phase 2.
- **Facts**: run `sila facts verify` before 4.3 (record exit code and
  violation delta).
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session
  end.
- **Evidence**: embed `sila/memory-writes/v1` under `## Memory Writes`.

## Risks

| Risk | Mitigation |
|---|---|
| Missing `checkcast` before unboxing `Object` in `Comparator.compare` fails JVM/ART verifier. | Emit `checkcast <boxed_owner>` before `intValue`/`floatValue`/`doubleValue`/`longValue`; prove via JVM runtime test (3.4). |
| `ineg` on `compareTo` overflows `Integer.MIN_VALUE` and risks tie-order bugs. | Swap local slots `1` and `2` when `reverse` is true (D2); handles runtime `reverse=flag` and preserves TimSort stability. |
| `map` returning `arraylist:` breaks `_box` / `_restore_yield_type`. | Reject `V`, `null`, `lambda`, and `arraylist:` results in `map` (2.2) with negative test (3.2). |
| Complexity regression in `_builtin_call_body` (`cx=29`) / `_builtin_result_body` (`cx=31`). | Route from `_dispatch_builtin` to a new higher-order helper in `collections.py`; gate with `just lint` and `--quality-delta` (3.3). |
| Test catalog missing `Collections.sort` / `ArrayList.get` / `valueOf`. | Add rows in `tests/_compile_helpers.py` (1.3) and list file in `goal.md`. |
| `key=fn` called `O(n log n)` times; `Float`/`Double` `NaN`/`-0.0` order differs from CPython. | Explicitly document both in D2 and `docs/not-covered.md` (3.5). |
