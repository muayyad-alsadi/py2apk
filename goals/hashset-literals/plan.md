# Plan: Lower set literals to HashSet

## Execution Steps

### Phase 1 — Contracts & schema

- [x] Add two constants to `Py2APK/compiler_j/types.py` beside
      `_ARRAY_LIST` (`types.py:57`) and `_LINKED_HASH_MAP_OWNER`
      (`types.py:63`): the descriptor `"Ljava/util/HashSet;"` and the
      owner `"java/util/HashSet"`.
- [x] Register `ast.Set` in `EXPR_HANDLERS` (`collections.py:25`) and
      `TYPE_HANDLERS` (`collections.py:31`) on `_CollectionsMixin`.
      Without this, `{1, 2}` raises `unsupported expression Set` from
      `_expression` (`base.py:68`).
- [x] Widen `_element_type` (`collections.py:120`) to accept
      `ast.List | ast.Tuple | ast.Set`, choosing `kind` as `"list"`,
      `"tuple"`, or `"set"`. Check `isinstance(item, ast.Starred)`
      before calling `_type_of` (`base.py:80`) and raise
      `f"{kind} unpacking is unsupported"` so `{*items}` fails with
      `set unpacking is unsupported` instead of
      `unsupported expression Starred`. Keep all existing list and
      tuple error messages byte-identical.

### Phase 2 — Core implementation

- [x] Add the set type-inference handler next to `_type_of_dict`
      (`collections.py:40`). Call `self._element_type(node)` first
      (matching `_type_of_dict` calling `_dict_types`), then return the
      HashSet descriptor. Do not invent a pseudo descriptor: a set is
      never an array, so `_list_element` (`types.py:244`) and
      `_jvm_descriptor` (`types.py:251`) stay unchanged and
      `list_elements` tracking is not involved.
- [x] Add the set emission handler next to `_list_literal` (`collections.py:107`),
      mirroring `_array_list` (`collections.py:165`): resolve `<init>()V`
      and `add(Ljava/lang/Object;)Z` via `_require_method` (`base.py:218`),
      emit `new java/util/HashSet`, `dup`, `invokespecial`, then for
      each element emit `dup`, `_expression`, verify `produced ==
      element`, `_box` (`base.py:185`), `_invoke` (`base.py:235`), and
      `pop`. Return the HashSet descriptor.
- [x] Add the HashSet branch to `_len_accepted` (`compare.py:240`) and
      `_emit_len` (`compare.py:251`), invoking
      `java/util/HashSet.size()I` through `_require_method` and
      `_invoke`. Because `_supports_truth` (`compare.py:208`) and
      `_truth_jump_false` (`compare.py:213`) delegate to `_len_accepted`
      and `_emit_len`, this enables `len(s)`, `if s:`, `if not s:`, and
      `while`/`and`/`or` truth tests in one place.
- [x] Handle `ast.Set` in `_preview_type` (`collections.py:488`) by
      returning the HashSet descriptor, matching `ast.Dict` so call
      argument previews do not return `None`.
- [x] Keep out of scope: `in`, `for` iteration, `add`/`remove`, the
      `set()` builtin, an empty set literal (`{}` is a dict in Python),
      `frozenset`, and `set[T]` annotations (`_annotation` at
      `base.py:320` supports `list[X]` only; a set field uses
      `from java.util import HashSet`).

### Phase 3 — Verification & evidence

- [x] Extend `_collection_catalog` (`tests/_compile_helpers.py:344`)
      with: (1) a `classes` row mapping `java/util/HashSet` to
      `java/lang/Object`; (2) `methods` rows on `java/util/HashSet` for
      `<init>()V`, `add(Ljava/lang/Object;)Z`, and `size()I` (with
      owner set so `_invoke` at `base.py:235` never falls back to
      `StringBuilder`); and (3) a static
      `java/util/Objects.requireNonNull(Ljava/lang/Object;)Ljava/lang/Object;`
      row so `Object`-typed parameter passing is verified.
- [x] Add focused tests in `tests/test_cli_compile_collections.py`
      beside `test_lowers_lists_and_maps` (`test_cli_compile_collections.py:17`):
      `values = {"a", "b"}` emits `new java/util/HashSet` and two
      `add(Ljava/lang/Object;)Z` calls with no `anewarray` and no
      `ArrayList`; an `int` set boxes via `Integer.valueOf`; `{1, "a"}`
      fails with `a set needs one element type`; `{None}` fails with
      `a set element needs a value type`; `{*items}` fails with
      `set unpacking is unsupported`; `len(values)` emits
      `HashSet.size()I`; `if not values:` emits `size()` plus `ifne`
      and `if values:` emits `size()` plus `ifeq`; `set()` still fails
      with `unsupported builtin set`; a field annotated `HashSet`
      accepts the literal; and `Objects.requireNonNull(values)` accepts
      it as `Object`.
- [x] Update `docs/covered.md:111`, `docs/not-covered.md:70`,
      `ROADMAP.md:117`, and `docs/README.md` if it lists the subset.
- [x] Run and leave green:
      `python3 -m unittest discover -s tests -v`, `just check`,
      `just lint`, and `ripwire . --doc-drift --legend=compact` with
      `drift="0"`.
- [x] Write `goals/hashset-literals/evidence.md` with the commands,
      their results, a `## Memory Writes` block, and a standalone
      `Verdict: PASS` line.

### Phase 4 — Attribution & Registry Closure

- [x] **Phase 4 — Attribution & Registry Closure**: (1) check all completed tasks in `goals/hashset-literals/plan.md`; (2) commit with `(goals/hashset-literals/goal.md)` or `[goal: hashset-literals]` using a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` subject (docs/test commits are ignored); (3) certify via `sila goals certify hashset-literals --evidence=goals/hashset-literals/evidence.md` (with `Verdict: PASS`) or `sila goals audit hashset-literals`; (4) reconcile via `sila goals` and commit the updated `goals/README.md` and certification receipts; verify `sila goals --json` reports `unverified_count: 0` for delivered goals.
