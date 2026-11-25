# Facts: Lower set literals to HashSet

## Current state (verified)

- `{1, 2}` parses as `ast.Set`. No mixin registers it: `EXPR_HANDLERS`
  (`Py2APK/compiler_j/collections.py:25`) lists `ast.Subscript`,
  `ast.List`, `ast.Tuple`, `ast.Dict`, and `TYPE_HANDLERS`
  (`collections.py:31`) lists `ast.Subscript`, `ast.List`, `ast.Dict`.
  `_expression` (`base.py:68`) and `_type_of` (`base.py:80`) therefore
  raise `unsupported expression Set`.
- Python has no empty set literal (`{}` parses as `ast.Dict`), so the
  empty branch of `_element_type` (`collections.py:120`) is unreachable
  for sets. `set()` goes to `_builtin_call` (`calls.py:352`) and raises
  `unsupported builtin set`.
- `docs/covered.md:111`, `docs/not-covered.md:70`, and `ROADMAP.md:117`
  all state that `set` is not implemented yet.

## Rules to mirror (list and dict literals)

- Homogeneous elements: `_element_type` (`collections.py:120`) rejects
  mixed types (`a list needs one element type`) and rejects `null` or
  `lambda` elements. Checking `ast.Starred` inside `_element_type`
  before calling `_type_of` guarantees `{*items}` raises
  `set unpacking is unsupported` in both type inference and emission.
- Type inference validates: `_type_of_dict` (`collections.py:40`) calls
  `_dict_types` (`collections.py:210`) before returning
  `_LINKED_HASH_MAP`. The set type handler must likewise call
  `_element_type` before returning the HashSet descriptor.
- Emission shape: `_array_list` (`collections.py:165`) emits
  `new`/`dup`/`invokespecial <init>()V`, then per element `dup`,
  expression, type check, `_box`, `add`, `pop`. `_dict_literal`
  (`collections.py:208`) does the same with `put`. A set literal follows
  the `_array_list` shape with `java/util/HashSet`.
- `_box` (`base.py:185`) is a no-op for references (`L...;`, `[...`)
  and boxes `I`/`Z`/`F`/`D`/`J` via `_BOX_METHODS` (`types.py:93`).
- `_require_method` (`base.py:218`) resolves every emitted call through
  `jars.db`; no signatures are hard-coded.
- `_invoke` (`base.py:235`) prints `signature.owner` and falls back to
  `_STRING_BUILDER` (`java/lang/StringBuilder`) if `owner` is empty.
  All test catalog rows for `java/util/HashSet` must set `owner`.

## Architectural invariants & constraints

- A set is always `java.util.HashSet`. There is no array form, so no
  pseudo descriptor is needed; `_list_element` (`types.py:244`) and
  `_jvm_descriptor` (`types.py:251`) stay untouched, and
  `list_elements`/`mutable_lists` (`base.py:123`,
  `collections.py:293`) are not involved.
- Because the descriptor is a standard reference descriptor, `_store`
  (`module.py:691`) stores it in a local and the field branch
  (`module.py:722`) checks compatibility against the declared Java field
  type (`self.items: HashSet` with `from java.util import HashSet`).
- `_annotation` (`base.py:320`) supports `list[X]` only; `set[X]`
  annotations remain an error in this goal.
- `len` and truthiness share one seam: `_len_accepted`
  (`compare.py:240`) decides, `_emit_len` (`compare.py:251`) emits, and
  `_truth_jump_false` (`compare.py:213`) emits `ifne` for `if not s:`
  and `ifeq` for `if s:`. Both positive (`if s:`) and negated
  (`if not s:`) conditions work automatically. Value `not` is stored
  by `_unary_not` (`compare.py:50`).
- `_sequence_element` (`base.py:177`) is the `for` seam (sets need
  `iterator()`, a separate goal). `_subscript_load` (`collections.py:44`)
  requires an array, list, or string; sets are not indexable.
- Set membership (`in`), `add`, `remove`, and `for x in s` are not
  implemented yet; document that clearly.

## Design note: Java 17 `sealed interface` / `record` sum types

- A Java 17 ADT such as `sealed interface JsonValue permits ...` with
  `record` variants is **not** used to allow heterogeneous sets/lists/
  dicts or `json.loads()` in Py2APK:
  1. Py2APK emits Jasmin (Java 5 bytecode, no `PermittedSubclasses` or
     `Record` classfile attributes, no `invokedynamic` `ObjectMethods`,
     and no `.interface` emission yet).
  2. `java.lang.Record` is Android API 34+, colliding with lower
     `minSdkVersion` targets.
  3. Wrapping literal elements in union records would break direct Java
     collection interop (`HashSet`/`ArrayList`/`LinkedHashMap` passed
     directly to Android APIs).
- Where the pattern *does* apply later: if Py2APK adds `Union` types or
  `dataclasses`, it can lower them to desugared `.interface` + plain
  `.class` value types with generated `equals`/`hashCode`/`toString`.
  Until then, user-defined Py2APK classes inherit `Object.equals` /
  `Object.hashCode` (identity-based), not value hashing.

## File & interface contracts

- `Py2APK/compiler_j/types.py`
  - Add descriptor constant `"Ljava/util/HashSet;"` and owner constant
    `"java/util/HashSet"`.
- `Py2APK/compiler_j/collections.py`
  - Register `ast.Set` in `EXPR_HANDLERS` and `TYPE_HANDLERS`.
  - `_element_type(node: ast.List | ast.Tuple | ast.Set) -> str`: reject
    `ast.Starred` before `_type_of`, and use `a set needs one element
    type` for mixed elements.
  - Set type handler validates via `_element_type` and returns the
    HashSet descriptor.
  - Set emission handler emits `HashSet` construction + boxed `add`
    calls and returns the HashSet descriptor.
  - `_preview_type` returns the HashSet descriptor for `ast.Set`.
- `Py2APK/compiler_j/compare.py`
  - `_len_accepted` accepts the HashSet descriptor.
  - `_emit_len` invokes `java/util/HashSet.size()I`.
- `tests/_compile_helpers.py`
  - `_collection_catalog` adds `classes` row for `java/util/HashSet`,
    `methods` rows (`<init>()V`, `add(Ljava/lang/Object;)Z`, `size()I`),
    and static `java/util/Objects.requireNonNull`.

## Scope

In: non-empty homogeneous set literal, list-equivalent element typing
plus `ast.Starred` rejection, primitive boxing, local storage, Java-typed
field storage, `len(s)`, `if s:` / `if not s:` truthiness, and passing as
`HashSet`/`Set`/`Object` when the catalog has the hierarchy.

Out: `in` membership, `for x in s`, `add`/`remove`, `set()` builtin,
`frozenset`, empty literal, `set[X]` annotations, heterogeneous union
wrappers, iteration order guarantees, and hashability diagnostics.

## Test seams

- `tests/test_cli_compile_collections.py:17` (`test_lowers_lists_and_maps`)
  is the existing lowering test; add focused sibling test methods.
- `tests/_compile_helpers.py:344` (`_collection_catalog`) is the shared
  fake catalog; `tests/_compile_helpers.py:171` (`_alias_catalog`)
  already includes `Integer.valueOf` boxing rows.
- Assert on emitted `.j`: `new java/util/HashSet`,
  `invokevirtual java/util/HashSet/add(Ljava/lang/Object;)Z`,
  `invokevirtual java/util/HashSet/size()I`, `ifne` for `if not s:`,
  `ifeq` for `if s:`, and absence of `anewarray` and `ArrayList`.

## Risks

- `_element_type` is shared with lists and tuples; keep existing error
  wording byte-identical (`test_cli_compile_collections.py:106`).
- Checking `ast.Starred` after `_element_type` would raise
  `unsupported expression Starred` instead of `set unpacking is
  unsupported`; always guard before `_type_of`.
- `_invoke` falls back to `java/lang/StringBuilder` on empty `owner`;
  every HashSet catalog row must set `owner = "java/util/HashSet"`.
- A `HashSet` of mutable or non-value-hashable objects compiles and uses
  `Object.hashCode`/`equals` at run time; document this limit.
