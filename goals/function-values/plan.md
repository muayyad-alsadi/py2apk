# Plan: Use a function as a value

Goal: a module function or a stored `lambda` can be assigned to a local,
passed as an argument to a `Callable`-annotated parameter, returned from
a `Callable`-annotated function, and called later.

## Locked Decisions

All architectural decisions are locked before Phase 1 (verified against
`Py2APK/compiler_j` and `Py2APK/cli_build.py`):

- [x] **D1 Scope.**
  - **In scope**:
    1. Annotated stored lambda with parameters:
       `f: Callable[[int], int] = lambda x: x + 1`, then `f(2)`.
    2. Unannotated zero-arg stored lambda:
       `f = lambda: 42` or `f = lambda: self.ping()`, then `f()`.
    3. Module function reference (same module or imported non-`Math`
       static module function): `g = square`, then `g(3)`.
    4. Passing a lambda, local function value, or module function to a
       `Callable[[...], R]` parameter (same module or cross-module):
       `apply(lambda x: x + 1, 2)`, `apply(square, 3)`, `apply(f, 2)`.
    5. Returning a lambda, local function value, or module function from
       a function annotated with `-> Callable[[...], R]`.
    6. Void-returning function values (`Callable[[...], None]`) called
       as statements with no value left on the stack.
    7. Captures of effectively-final locals inside stored lambdas.
  - **Out of scope**:
    - Unannotated lambdas with >= 1 parameters (`f = lambda x: x + 1`),
      because Python `ast.Lambda` parameters cannot carry annotations
      and Py2APK is single-pass strictly typed.
    - Keyword arguments on function-value calls (`f(x=1)`).
    - Chained calls (`make_adder(1)(2)`), calling a `self.field`
      directly (`self.cb(2)`), overloaded `java.lang.Math` function
      references (`g = sqrt`), `map`, `filter`, `sorted(key=)`,
      `Thread(target=fn)`, decorators beyond `@contextmanager`,
      `*args`/`**kwargs`, `functools`, and `java.util.function.Function`
      boxing.
- [x] **D2 Value representation.**
  - Each unique JVM signature `(params, result)` maps to one canonical
    root-package abstract base class:
    `py2apk$fn$<encoded_params>_r_<encoded_result>` with `<init>()V` and
    `public abstract call(<params>)<result>`.
  - Encoding (provably injective and reversible, in `types.py`):
    - Primitives/void (`Z`, `I`, `F`, `D`, `J`, `V`): unchanged.
    - Array `[` -> `A`.
    - Object `L<internal>;` -> `L<escaped>_e` where `_`->`_u`, `$`->`_d`,
      `/`->`_s`.
    - Multiple parameters joined by `_p_`; params/result separated by
      `_r_`. Examples: `() -> V` is `py2apk$fn$_r_V`; `(I,) -> I` is
      `py2apk$fn$I_r_I`; `(Ljava/lang/String;) -> I` is
      `py2apk$fn$Ljava_slang_sString_e_r_I`.
  - Each stored lambda or function reference emits a concrete
    `{class_internal}$fn${lambda_serial}` extending that base class,
    implementing `public call(<params>)<result>`.
  - Calls emit `invokevirtual py2apk$fn$<mangled>/call(<params>)<result>`.
  - Abstract base `.j` uses a fixed `.source py2apk$fn` so multiple
    modules sharing a signature emit byte-identical `.class` files in
    `.py2apk/classes/` and D8 receives one class without collision.
- [x] **D3 Type spelling & annotation support.**
  - `Callable[[T1, T2], R]` and `Callable[[], R]` (`R` may be `None` ->
    `"V"`), importable from `collections.abc` or `typing`.
  - Both `indexer.py` (`_collect_imports`, `_type_from_expr`) and
    `module.py`/`base.py` (`_record_from_import`, `_annotation`) resolve
    `Callable[[...], R]` to `Lpy2apk$fn$<mangled>;`.
  - Reject `Callable[..., R]` and malformed `Callable` subscripts with a
    clear `JasminCompilerError`.
- [x] **D4 Abstract class, never `.interface`.**
  - Base classes use `.class public abstract <base>` and
    `.super java/lang/Object`.
  - Concrete function-value classes use `.super <base>` (no `.implements`)
    and call `invokespecial <base>/<init>()V` in `<init>`.
  - Listener lambdas keep `.super java/lang/Object` + `.implements <iface>`.
- [x] **D5 Capture semantics (Java effectively-final).**
  - A pure AST helper `_non_final_names(fn: ast.FunctionDef) -> set[str]`
    computes names that are: (a) parameters assigned anywhere in
    `fn.body`, (b) locals with > 1 assignment target (`Assign`,
    `AnnAssign`, `AugAssign`, `For.target`, `With.optional_vars`,
    `ExceptHandler.name`), or (c) assigned inside any `For`/`While` loop.
  - Function-value lambdas capturing any name in `self.non_final_locals`
    raise `JasminCompilerError`:
    `captured local '<name>' is reassigned; only effectively final locals can be captured`.
  - Existing listener lambdas remain unaffected.
- [x] **D6 File scope & complexity budget.**
  - `goal.md` and `facts.md` already list all 6 compiler files
    (`types.py`, `indexer.py`, `base.py`, `module.py`, `lambdas.py`,
    `calls.py`), tests, and docs.
  - Because `_builtin_call` (`cx=29`), `_builtin_result` (`cx=31`),
    `_record_from_import` (`cx=22`), `_type_from_expr` (`cx=19`), and
    `_lambda_class` (`cx=17`) are at/above the complexity cap, all new
    logic goes into small pure helpers so `just lint` and
    `ripwire . --quality-delta` stay green.

## Phase 1 — Contracts, Types & Red Tests

- [x] **1.1 Canonical function-type helpers in `Py2APK/compiler_j/types.py`.**
  Add `_PY2APK_CALLABLE = "py2apk/Callable"` and pure, PEP 484-typed
  helpers:
  - `_mangle_descriptor(descriptor: str) -> str` and
    `_unmangle_descriptor(encoded: str) -> str | None`
  - `_function_internal(parameters: tuple[str, ...], result: str) -> str`
    -> `py2apk$fn$<mangled>`
  - `_function_type(parameters: tuple[str, ...], result: str) -> str`
    -> `Lpy2apk$fn$<mangled>;`
  - `_parse_function_type(descriptor: str) -> tuple[str, tuple[str, ...], str] | None`
    -> `(internal, parameters, result)` or `None`
  - `_is_function_type(descriptor: str) -> bool`
  - `_callable_parts(node: ast.expr, imports: dict[str, str]) -> tuple[list[ast.expr], ast.expr] | None`
    validates `Callable[[...], R]`, rejects `Callable[..., R]` and
    non-list first arg with `JasminCompilerError`.
- [x] **1.2 Indexer support in `Py2APK/compiler_j/indexer.py`.**
  - Update `_collect_imports` to map `Callable` from `collections.abc`
    and `typing` to `_PY2APK_CALLABLE`.
  - Add helper `_callable_type_from_expr(...)` called from
    `_type_from_expr` (1 branch) so cross-module `Callable[[...], R]`
    parameters and return types index cleanly.
- [x] **1.3 Base state & annotation support in `Py2APK/compiler_j/base.py`.**
  - Initialize `self.emitted_function_bases: set[str] = set()` and
    `self.non_final_locals: set[str] = set()` in `_CompilerBase.__init__`.
  - In `_compatible` (line 293): for `actual == "lambda"`, return
    `self._is_interface_type(wanted) or _is_function_type(wanted)`.
  - Add helper `_callable_annotation(self, node: ast.expr) -> str | None`
    called from `_annotation` (1 branch) using `_callable_parts`.
  - Add stub declarations on `_CompilerBase` for cross-mixin methods
    called from `module.py` / `calls.py` (`_emit_function_lambda`,
    `_emit_function_ref`, `_function_ref_binding`), keeping `ty` strict
    typechecking green.
- [x] **1.4 Contract tests first (red) in `tests/test_cli_compile_lambda.py`.**
  Add `FunctionValueTests` covering:
  - `test_function_type_roundtrip` (unit test on `_function_type` /
    `_parse_function_type` for primitives, arrays, objects, void, `$`
    and `_` in class names).
  - `test_annotated_stored_lambda_emits_base_and_invokevirtual` (must
    fail before Phase 2 with unsupported `Callable` / `Lambda`).
  Run `python3 -m unittest tests.test_cli_compile_lambda -v` and confirm
  the new tests fail for the expected reason.

## Phase 2 — Core Implementation

- [x] **2.1 Abstract base & concrete emitters in `Py2APK/compiler_j/lambdas.py`.**
  - Register `EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {ast.Lambda: "_emit_lambda_value"}`.
  - Add `_function_base_class(internal, parameters, result) -> str`
    emitting `.source py2apk$fn`, `.class public abstract <internal>`,
    `.super java/lang/Object`, `<init>()V`, and
    `.method public abstract call(<params>)<result>`.
  - Add `_ensure_function_base(self, internal, parameters, result) -> None`
    (checks `self.emitted_function_bases` and appends `(f"{internal}.j", ...)`
    once per compilation unit).
  - Extract shared Jasmin class builder `_emit_synthetic_class(...)`
    from `_lambda_class` so both listener classes (`.super java/lang/Object`,
    `.implements <iface>`) and function-value classes (`.super <base>`,
    no `.implements`, `invokespecial <base>/<init>()V`) share one clean
    emitter without raising `cx`.
  - Add `_non_final_names(node: ast.FunctionDef) -> set[str]` and check
    captures against `self.non_final_locals` in `_emit_function_lambda`.
  - Add `_emit_function_lambda(self, node: ast.Lambda, expected: str) -> str`:
    validates plain positional args, arity match against
    `_parse_function_type(expected)`, effectively-final captures, ensures
    the base class, emits `Owner$fn$N`, returns `expected`.
  - Add `_emit_lambda_value(self, node: ast.Lambda) -> str`:
    if `node.args.args` (or vararg/kwarg/defaults/kwonlyargs) is non-empty,
    raise `JasminCompilerError("a lambda with parameters requires a Callable annotation or function-typed parameter")`.
    Otherwise infer result type from `self._type_of(node.body)` (void
    allowed), compute `expected = _function_type((), result)`, and delegate
    to `_emit_function_lambda`.
  - Add `_function_ref_binding(self, name: str, node: ast.AST) -> tuple[str, str, _MethodSignature] | None`
    and `_emit_function_ref(self, node: ast.Name) -> str`:
    resolves same-module `invokestatic` functions in `self.methods` and
    imported non-`Math` static functions in `self.imported_functions`;
    rejects overloaded `java/lang/Math` names with a clear error; ensures
    the base class and emits capture-free `Owner$fn$N` whose `call`
    invokes `invokestatic owner/method(<params>)<result>`.
- [x] **2.2 Imports, annotations, names & returns in `Py2APK/compiler_j/module.py`.**
  - Allow `Callable` in `from collections.abc import ...` and
    `from typing import ...` via a small helper called from
    `_record_from_import` (keep `cx` flat).
  - In function compilation and `_compile_method`, save/set/restore
    `self.non_final_locals = _non_final_names(node)`.
  - In `_ann_assign`: resolve `declared = self._annotation(node.annotation)`
    first; if `isinstance(node.value, ast.Lambda)` and
    `_is_function_type(declared)`, emit via `_emit_function_lambda` and
    store directly without calling `_assigned_value`.
  - In `_emit_return`: if `isinstance(node.value, ast.Lambda)` and
    `_is_function_type(self.method_result)`, emit via
    `_emit_function_lambda` and `areturn`.
  - In `_type_of_name` and `_load_name`: when `node.id` is not in
    `self.locals`, delegate to `_function_ref_binding` /
    `_emit_function_ref` before raising `unknown local`.
- [x] **2.3 Call dispatch & argument lowering in `Py2APK/compiler_j/calls.py`.**
  - Add helper `_local_function_signature(self, node: ast.Call) -> tuple[str, tuple[str, ...], str] | None`
    reading `self.locals.get(node.func.id)` and `_parse_function_type`.
  - Add helper `_emit_local_function_call(self, node: ast.Call, fn: tuple[str, tuple[str, ...], str]) -> str`:
    rejects `node.keywords`, emits `_load_local`, `_emit_arguments(node, params)`,
    `invokevirtual <base>/call(<params>)<result>`, returns `result`.
  - Call these at the top of `_builtin_call` and `_builtin_result` (each
    adds only a single guard, or extract a helper to keep `cx` <= current).
  - In `_emit_arguments` (lines 1088-1090): when `isinstance(argument, ast.Lambda)`
    and `_is_function_type(wanted)`, call `self._emit_function_lambda(argument, wanted)`
    instead of `self._emit_listener`. Keep `self.method` and interface
    lambdas on `_emit_listener`.

## Phase 3 — Verification & Evidence

- [x] **3.1 Positive tests** in `tests/test_cli_compile_lambda.py`:
  - `f: Callable[[int], int] = lambda x: x + 1` then `return f(2)` emits
    `py2apk$fn$I_r_I.j`, `Owner$fn$1.j` (`.super py2apk$fn$I_r_I`), and
    `invokevirtual py2apk$fn$I_r_I/call(I)I`.
  - Zero-arg `f = lambda: 42` then `return f()`, and void
    `f = lambda: self.ping()` then `f()` (statement call, no `pop`).
  - Module function ref `g = square` then `return g(3)` (same module and
    imported from another module).
  - Two stored lambdas/refs with the same signature share one base `.j`
    file in `extra_classes`.
  - Captures: stored lambda capturing an effectively-final local emits
    `.field` and passes it to `Owner$fn$N/<init>`.
  - Higher-order argument (D1c): `apply(lambda x: x + 1, 2)`,
    `apply(square, 3)`, and `apply(f, 2)` across both same-module and
    two-module (`index_project_types`) compilation.
  - Returning a lambda/function from `def make() -> Callable[[int], int]:`.
- [x] **3.2 Negative tests** in `tests/test_cli_compile_lambda.py`:
  - Unannotated parameterized lambda `f = lambda x: x + 1` -> clear
    `Callable annotation` error.
  - `Callable[..., int]` and `Callable[int, int]` -> clear error.
  - Unknown captured local `f = lambda: x` -> `unknown local 'x'`.
  - Non-final capture: reassigned local (`x = 1; f = lambda: x; x = 2`),
    reassigned parameter, and loop variable (`for i in range(3): f = lambda: i`)
    -> `effectively final` error.
  - Wrong arity `f(1, 2)` and wrong arg type `f("a")` on
    `Callable[[int], int]` -> `JasminCompilerError`.
  - Keyword argument on function value `f(x=1)` -> rejected.
  - Mismatched signature passed to `apply(lambda x, y: x + y, 2)` or
    `apply(other_sig_fn, 2)` -> rejected.
  - Result-type mismatch in lambda body -> rejected.
  - Overloaded `from math import sqrt; g = sqrt` -> rejected.
  - `map(f, xs)` and other out-of-scope forms still raise.
- [x] **3.3 Regression.** Run existing
  `test_lowers_listener_lambdas_and_method_references` unchanged (must
  pass without edits, including `self.ping` as a listener).
- [x] **3.4 Full suite & static checks.**
  - `python3 -m unittest discover -s tests -v` exits 0 (record before/after).
  - `just check` (`ty check Py2APK`) exits 0.
  - `just lint` (`ruff check .`) exits 0.
  - `ripwire . --quality-delta --legend=compact` exits 0 (no new debt).
- [x] **3.5 End-to-end build.**
  Build a sample project with `py2apk build` containing two modules
  sharing a `Callable[[int], int]` signature, a stored lambda, and a
  module function value. Verify `.py2apk/classes/py2apk$fn$I_r_I.class`
  and the signed APK are produced cleanly (inspect classes with `javap`
  or `apktool` if available). Record commands and exit codes in evidence.
- [x] **3.6 Docs & doc-drift.**
  Update `docs/covered.md` (add function values + runnable `Callable`
  example), `docs/not-covered.md` (narrow "Lambda" and remove "A function
  used as a value" while keeping unannotated parameterized lambdas,
  `map`, `filter`, `sorted`, decorators, `*args`, and non-final captures),
  and `ROADMAP.md` (`## A function is not a value`). Run
  `ripwire . --doc-drift --legend=compact` until `drift="0"`.
- [x] **3.7 Evidence.**
  Write `goals/function-values/evidence.md` with test counts, commands,
  exit codes, `## Memory Writes` (`sila/memory-writes/v1`), and a
  standalone `Verdict: PASS` line.

## Phase 4 — Attribution & Registry Closure

- [x] **4.1 Check tasks.** All boxes in Phases 1-3 are `- [x]`.
- [x] **4.2 Commit.** One or more `feat:` commits with
  `(goals/function-values/goal.md)` or `[goal: function-values]`. Local
  commit only; no remote push without user consent.
- [x] **4.3 Certify.** `sila goals certify function-values --evidence=goals/function-values/evidence.md`
  (or `sila goals audit function-values`).
- [x] **4.4 Reconcile.** Run `sila goals`, commit updated `goals/README.md`
  and certification receipts.
- [x] **4.5 Verify.** `sila goals --json` reports `status: "implemented"`,
  `verification_status: "verified"`, `unverified_count: 0`, and clean
  `git status`.

## Memory protocol (goal.md)

- Prime: `sila prime` at start (record timestamp).
- Recall before Phase 1: `sila recall "function value"` and
  `sila recall "lambda listener"` (record queries and match counts).
- Writes: `sila case` for the canonical abstract-base `py2apk$fn$`
  mechanism (D2/D3); `sila lesson` for any bug fixed in Phase 2.
- Facts: `sila facts verify` before 4.3 (record exit code and delta).
- Handoff: `sila handoff "<summary>" --open="..."` at session end.
- Evidence: `sila/memory-writes/v1` block under `## Memory Writes`.

## Risks

| Risk | Mitigation |
|---|---|
| `"lambda"` sentinel is shared with `self.method` listener references. | Keep `"lambda"` for interface + `Callable` dispatch; branch `_emit_arguments` on `_is_function_type(wanted)` vs `_is_interface_type(wanted)`; keep regression test (3.3). |
| Multi-module builds emit the same `py2apk$fn$...` base class from two `.j` files. | Fixed `.source py2apk$fn` produces byte-identical `.class` files in `.py2apk/classes/`; verified in E2E build (3.5). |
| Descriptor mangling collision (`a/b` vs `a_b`, or `$` inner classes). | Injective `_` escape scheme (`_u/_d/_s/_e`, `_p_`, `_r_`) with roundtrip unit test (1.4). |
| Complexity regression in `calls.py`/`module.py`/`indexer.py` (`cx=19..31`). | Place all new logic in small pure helpers; gate with `just lint` and `ripwire . --quality-delta` (3.4). |
| Late-binding capture expectations vs Java capture-by-value. | Enforce effectively-final via `_non_final_names` (D5); test and document in `docs/not-covered.md`. |

Next action: run `sila prime` and `sila recall`, then start Phase 1 (task 1.1).
