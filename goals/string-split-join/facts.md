# Facts: Lower literal split and join

## Current state (verified)

- `label.split(",")` in
  `tests/test_cli_compile_fstring.py` (source near line 51, assert
  line 169) emits
  `invokevirtual java/lang/String/split(Ljava/lang/String;)[Ljava/lang/String;`.
  That Java method treats the argument as a regex and drops trailing
  empty fields. `_alias_catalog`
  (`tests/_compile_helpers.py:178`) registers only that one-arg
  overload. The result descriptor is `[Ljava/lang/String;`
  (`_STRING_ARRAY`, `Py2APK/compiler_j/types.py:55`).
- `examples/weather/jars.db` (platform `android.jar`) also has
  `String.split(Ljava/lang/String;I)[Ljava/lang/String;`,
  `Pattern.quote(Ljava/lang/String;)Ljava/lang/String;` (`invokestatic`),
  and both `String.join` overloads (`invokestatic`). Those rows are
  absent from `_alias_catalog`. Checked-in `jars.db` files have no
  `api_levels` table, so `JarCatalog.api_since`
  (`Py2APK/jar_index.py:215`) returns `None` for them today.
- There is no flip of `sep.join(parts)`. `String` has no instance
  `join`. Both catalog `join` methods are static and take the
  separator first.
- `_java_method_name` (`Py2APK/compiler_j/types.py:138`) renames only
  the keys in `_STRING_METHOD_ALIASES` (`types.py:87`): `upper`,
  `lower`, `strip`, `startswith`, `endswith`, `find`, `replace`.
  `split` and `join` are not in that map. Leave the map unchanged.
- Instance calls go through `_CallsMixin._call`
  (`Py2APK/compiler_j/calls.py:516`). It evaluates the receiver with
  `_expression` first, then `_find_method` (`calls.py:728`).
  `_type_of_call` (`calls.py:645`) uses `_type_of`, not `_expression`.
- `_require_method` (`Py2APK/compiler_j/base.py:220`) calls
  `_find_method` without the `call` argument, so it does not run
  `_reject_newer_api` (`calls.py:153`). The minSdk gate runs only when
  `_find_method` is passed `call=`.
- `_list_append` is `Py2APK/compiler_j/collections.py:274`. The
  `base.py:476` method is a stub. Do not add helpers beside a
  `calls.py` `_list_append`; that symbol is not there.
- A list literal is an array unless it is mutable
  (`_list_literal`, `collections.py:115`). `list[str]` annotations are
  arrays (`_listed_element`, `types.py:230`; `docs/checking.md:102`).
  `arraylist:{element}` is the mutable form (`_list_element`,
  `types.py:248`). Tuples are arrays (`_tuple_literal`,
  `collections.py:112`). Subscript, `len`, and `for` accept both
  arrays and `ArrayList` (`collections.py:52`, `compare.py:251`,
  `loops.py:81`). `append` rejects an array.
- A stored list passed to a call stays an array only when
  `_accepts_list_array` (`collections.py:431`) finds a signature whose
  parameter is that array. It calls `_signature_for_call`
  (`calls.py:680`). `String` has no instance `join`, so a list passed
  only to `join` is marked mutable and becomes an `ArrayList`.
- Compiler helpers are module functions in `Py2APK/stdlib/py2apk.py`
  (`slice_start`, `slice_stop`, `chr`, `range`). The compiler emits
  a raw `invokestatic py2apk/...` (`loops.py:284`, `calls.py:352`).
  They are not `jars.db` rows. `shlex.split`
  (`Py2APK/stdlib/shlex.py`) returns one `str` and is unrelated.
- `str + str` is `String.concat` (`expr.py:155`). The stdlib cache
  catalog (`Py2APK/stdlib_cache.py:71`) has `StringBuilder` `<init>`,
  `append(String)`, and `toString`. It does not have `String.concat`.
- Templates set `minSdkVersion` to 21 (`templates/blank`,
  `templates/helloworld`), 23 (`templates/chart`,
  `templates/weather`), or 33 (`templates/kitchensink`).
- Docs that still say split/join are not lowered:
  `docs/not-covered.md:118`, `ROADMAP.md:152`, `README.md:100`
  (string aliases stop at `replace`), `docs/covered.md:456`.

## Python behavior this subset must match

`str.split(sep=None, maxsplit=-1)` and `str.join(iterable)` below are
CPython. The subset is only the rows marked in.

- `text.split("sep")` with a non-empty `str` constant: in.
  Literal separator. `"a,".split(",")` is `["a", ""]`. `"."` and
  `"|"` are not regex.
- `text.split(sep)` when `sep` is not a `str` constant: in only as
  today's regex `String.split(String)`. Not Python. Drops trailing
  empties.
- `text.split()` or `text.split(None)`: out. Python splits on
  whitespace. Do not emit regex split.
- `text.split("")`: out. Python raises `ValueError: empty separator`.
  Compile error `empty separator`.
- `text.split(sep, maxsplit)` or a `maxsplit` keyword: out. Not
  Java's limit. Compile error.
- Any keyword on `split` or `join`: out. Compile error.
- `sep.join(parts)` when every element is `str`: in. Empty string
  between parts, nothing before the first or after the last. An
  empty runtime sequence is `""`.
- `sep.join([])` or `sep.join(())`: out. An empty list or tuple has
  no element type (`collections.py:128`).
- `join` of a non-`str` element, a set, a dict, a generator, or
  `bytes`: out. Compile error.
- `bytes.split` / `bytes.join`: out. No `bytes` type
  (`docs/covered.md:116`).

## Locked lowering

**Literal split.** After `_call` has pushed the receiver, when
`func.attr == "split"`, the receiver descriptor is `_STRING`
(`types.py:40`), there is one positional argument, no keywords, and
that argument is `ast.Constant` of a non-empty `str`:

1. `ldc` the raw separator (not a hand-quoted pattern).
2. `invokestatic` `Pattern/quote(Ljava/lang/String;)Ljava/lang/String;`
   via `_require_method` and `_invoke` (`base.py:220`, `base.py:237`).
3. `_emit_int(-1)` (`base.py:271`), which is `iconst_m1`.
4. `invokevirtual` `String/split(Ljava/lang/String;I)[Ljava/lang/String;`.
5. Return `_STRING_ARRAY`.

Then call `_reject_newer_api` with that `call`, because
`_require_method` skips it. Both methods are in the weather platform
index. Do not add a `py2apk` split helper. Do not wrap the array in
`ArrayList`. `parts.append` on a split result keeps today's array
error.

**Regex split.** One positional argument that is not a `str` constant
stays the one-arg `invokevirtual` already emitted. Do not quote it.

**Join.** Do not emit `String.join`. Android added both overloads at
API 26. Blank and weather templates are minSdk 21 and 23. A direct
`String.join` is a `NoSuchMethodError` there, and `_require_method`
would not consult minSdk. `String[]` is also not an `Iterable`, so the
`Iterable` overload does not match this compiler's `list[str]`.

Emit `invokestatic py2apk/str_join` with descriptor
`(Ljava/lang/String;[Ljava/lang/String;)Ljava/lang/String;`,
the same way `chr` is emitted (`calls.py:352`): a literal instruction,
not a `jars.db` lookup. Receiver (the separator) is already on the
stack. Then evaluate `parts`. If the descriptor is
`arraylist:Ljava/lang/String;`, call `_emit_to_array`
(`collections.py:302`) so the top becomes `_STRING_ARRAY`. Then
`invokestatic`. Return `_STRING`.

JVM order is separator, then parts. Evaluating parts first and the
separator second would swap the arguments.

`str_join` lives in `Py2APK/stdlib/py2apk.py`. It is a compiler helper,
not a Python builtin. Implement it with `StringBuilder`, which the
stdlib cache catalog already has. Do not use `+` (`String.concat` is
missing from that catalog). Shape:

```python
def str_join(sep: str, parts: list[str]) -> str:
    built = StringBuilder()
    index = 0
    size = len(parts)
    while index < size:
        if index > 0:
            built.append(sep)
        built.append(parts[index])
        index = index + 1
    return built.toString()
```

`list[str]` on that parameter is a `String[]`, so `len` is
`arraylength` and the subscript is `aaload`. An empty array skips the
loop and returns `""`.

So that a list used only by `join` stays an array, `_signature_for_call`
must report a parameter of `_STRING_ARRAY` for this call.
`_accepts_list_array` is the only caller. Do not emit that synthetic
signature.

## Corrections to the previous plan

- Helper names were `_literal_str_arg` / `_is_str_receiver` in the
  plan and `_is_literal_str` / `_joinable_parts` in the old facts.
  Use the names in `plan.md` only.
- The old plan's join stack order (parts, then separator) is wrong.
- The old plan returned a split array and also talked about an
  `ArrayList` join as if both were `Iterable`. Arrays are not
  `Iterable`.
- `Pattern.quote` plus `split(String, int)` does not need a minSdk
  fallback. `String.join` does. The fork is closed: quote-and-limit
  for literal split, `str_join` for join.
- `split()` does not "keep today's regex path". Java has no
  zero-arg `split`. The test catalog's one-arg row does not match.
- Phase 5 was labeled "Phase 4".
- `docs/not-covered.md:118` and `ROADMAP.md:152` are real, but each
  claim spans the following lines. `expr.py` does not own this
  dispatch; `calls.py` does. Archived
  `goals/_archive/compiler-package/facts.md` is historical.
