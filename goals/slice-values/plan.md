# Plan: Use a slice as a value

Decisions are in [`facts.md`](facts.md). Reuse `slice_start` /
`slice_stop` and the `for` step rules. Materialize a new sequence.
Never return `subList` or the original object.

`for item in items[1:]` must keep peeling the slice in
`_for_statement` and must not call the new value lowering.

## Behavior rules

A value slice is an `ast.Subscript` whose `slice` is `ast.Slice`.

Step is `1` when `slice.step` is `None` or `_literal_int` is `1`
(`1`, `+1`, `True`). Only that case may call `substring` or
`copyOfRange`. Any other step, including a non-literal, uses the
gather loop. A non-literal that happens to be `1` at run time still
gathers. The elements match; the bytecode does not have to.

Call the moved emitters in `_for_index`'s order: length, step, reject
zero, start, stop. Fast path and gather both use those locals. Pass
the adjusted locals to Java, never the raw bound expressions.

Before `substring` or `copyOfRange`, emit `if_icmpgt` from start to
stop. The taken branch pushes an empty result (`ldc ""`, or
`iconst_0` plus `newarray` / `anewarray`) and skips the JDK call.
`start == stop` may call the JDK method. `start > stop` must not.

Gather uses the same direction test as `_for_index`: `ifle` on the
step, then `if_icmpge` (positive) or `if_icmple` (negative). The
source index starts at `start` and adds `step`. The destination index
starts at `0` and adds `1`. Do not store at the source index.

Slice length, when a pre-sized array or `char[]` is required
(CPython, `idiv` because the numerator is non-negative here):

- step > 0 and start >= stop → `0`
- step > 0 → `(stop - start - 1) / step + 1`
- step < 0 and start <= stop → `0`
- step < 0 → `(start - stop - 1) / (-step) + 1`

`ArrayList` gather does not need that length. `add` grows it.

Result type:

- `_STRING` → `_STRING`. Index load stays `I`. This goal does not
  change `label[i]`.
- descriptor starts with `[`, and the component is a `_NEWARRAY`
  key or a reference (`L` or `[`) → the same descriptor.
  `byte`, `short`, and `char` arrays are unsupported: the load/store
  tables have no opcode for them. Raise the subscript error. Do not
  emit `aaload`.
- `arraylist:{element}` → the same pseudo descriptor.
- anything else, including `HashSet`, dict, and a generator → raise
  the existing `subscript needs an array, a list, or a string`.
  Do not keep `a slice is only supported as a for iterable` on loads.
  Store keeps that store error.

String indexing stays UTF-16 code units, matching `charAt`. Do not
add code-point indexing.

Copies are shallow: primitive slots are values; reference slots and
`ArrayList` elements are the same references. Do not promise
`s[:] is s` or `items[:] is items`.

An immutable list literal such as `items = [1, 2, 3]; taken =
items[:2]` is an array slice (`copyOfRange` or gather), not an
`ArrayList`. An `ArrayList` slice happens only when the source is
already `arraylist:` (for example after `append`). A homogeneous
tuple is already an array (`_tuple_literal`). Slicing it returns
`[T`. Do not rebuild a tuple.

## Out of scope

- Slice assignment and `del` of a slice. `_subscript_store` stays.
- `slice()` as a value, and `items[slice(1, 2)]` (the slice field is
  a `Call`, not `ast.Slice`).
- A comma slice (`items[1:2, 3:4]`), which is an `ast.Tuple`.
- `HashSet`, dict, generator, and any descriptor
  `_sequence_element` rejects. No new error string for those.
- Changing `for` over a slice into a copy.
- `for` over a bare `str`. `_sequence_element` still returns `None`
  for `String`.
- Deep copy, object identity, and Unicode code points.
- A new `py2apk.py` helper. Do not touch `Py2APK/stdlib`.

## Execution steps

### 1. Catalog rows

- [x] In `tests/_compile_helpers.py` `_alias_catalog`, add
      `("java/lang/String", "substring", "II", string, 0,
      "invokevirtual")`. `_compiled_class` always loads this catalog
      (`_compile_helpers.py:132`). Do not rely on
      `_collection_catalog`; it runs only when `collections=True`.
- [x] In the same function, add static `java/util/Arrays`
      `copyOfRange` rows, `is_static=1`, opcode `invokestatic`.
      Primitive rows, params then result: `[ZII`/`[Z`, `[III`/`[I`,
      `[FII`/`[F`, `[DII`/`[D`, `[JII`/`[J`. One reference row:
      params `[Ljava/lang/Object;II`, result `[Ljava/lang/Object;`.
      That covers every array `_NEWARRAY` plus `anewarray` can build.
      Skip `byte`, `short`, and `char` arrays; list literals never
      use those components.
- [x] Do not add `String.<init>([C)V` or `charAt`. Emit them as raw
      instructions, as `_emit_char_string` and `_subscript_load` do.
- [x] Do not edit `Py2APK/stdlib_cache.py`. Nothing in
      `Py2APK/stdlib` calls these methods.

### 2. Move the emitters, bytecode unchanged

- [x] Move these methods from `_LoopsMixin` onto `_CompilerBase`
      with the same bodies and signatures:
      `_emit_slice_step`, `_emit_int_expr`, `_reject_zero_step`,
      `_emit_slice_bound`, `_emit_sequence_length`, `_emit_element`.
- [x] Leave `_for_index` in `loops.py`. It still calls them in the
      same order and still allocates the six `_cursor_name`s first.
- [x] Do not move `_literal_int`. Import it from `types` in `base.py`
      if the moved methods need it. `loops.py` keeps its import for
      `_emit_range_step`.
- [x] Do not give `_emit_sequence_length` a `String` arm.
- [x] Run `tests/test_cli_compile_loop.py` and
      `tests/test_cli_compile_stdlib.py` before any value-slice
      code. `for` Jasmin must still contain `slice_start`,
      `slice_stop`, and `if_icmple`, and the stdlib `for` sample
      must still lack `ifge`.

### 3. Bind locals once

- [x] Add `_bind_slice_locals(self, origin: ast.AST, descriptor:
      str, bounds: ast.Slice) -> tuple[str, str, str, str, str]`
      on `_CompilerBase`. Always return five names: sequence, length,
      step, start, stop. The sequence value is already on the stack.
      `_keep` it, then emit length, step, the zero check, start, and
      stop, using the moved helpers.
- [x] Length: `arraylist:` and `[` go through `_emit_sequence_length`.
      `_STRING` is `aload` plus `_require_method` /
      `_invoke` of `java/lang/String.length`, same as `_emit_len`.
- [x] Type hints on the signature. Return from the failing guard
      inside the helpers you already moved; do not wrap the success
      path in `else`.

### 4. Type and load

- [x] Add `_type_of_subscript_slice(self, node: ast.Subscript) -> str`
      on `_CollectionsMixin`. No bytecode. Return `_STRING`, an array
      descriptor whose component is a `_NEWARRAY` key or a reference
      (`L` or `[`), or the same `arraylist:` descriptor. Otherwise
      raise `subscript needs an array, a list, or a string`.
- [x] Add `_subscript_slice_load(self, node: ast.Subscript) -> str`.
      Evaluate the sequence once. If the type helper would reject it,
      raise before emitting bounds. Bind locals. Then lower.
- [x] Wire `_type_of_subscript` (`collections.py:101`) and
      `_subscript_load` (`collections.py:52`) to those two methods
      when `isinstance(node.slice, ast.Slice)`. Leave integer indexes
      alone, including string `charAt` returning `I`.
- [x] Leave `_subscript_store` (`collections.py:71`) rejecting
      `ast.Slice` with `a slice is only supported as a for iterable`.
- [x] Leave `_for_statement` (`loops.py:95`) unchanged.

String, step folded to `1`:

- [x] Guard `start > stop` with an empty `ldc ""`.
- [x] Otherwise `aload` the string, `iload` start, `iload` stop,
      `_require_method(..., "substring", ("I", "I"), static=False)`
      and `_invoke`. Return `_STRING`.

String, any other step:

- [x] Emit the slice length. `newarray char`. Walk with source and
      destination indexes. `charAt(I)C`, `i2c`, `castore`.
      Then `new java/lang/String` / `dup` / `invokespecial
      java/lang/String/<init>([C)V`, copying `_emit_char_string`.
      Do not call `py2apk.chr` and do not `append(I)`.
- [x] Return `_STRING`.

Array, step folded to `1`:

- [x] Guard `start > stop`. Empty path: `iconst_0`, then
      `newarray {_NEWARRAY[element]}` or `anewarray` as in
      `_java_array` (`collections.py:163`).
- [x] Otherwise `aload` array, `iload` start, `iload` stop. If
      `descriptor[1:]` is a `_NEWARRAY` key, `_require_method` the
      matching primitive `copyOfRange` (`static=True`). Otherwise
      require the `Object[]` overload and `checkcast` to
      `descriptor`, as `_emit_to_array` does.
- [x] Return `descriptor`.

Array, any other step:

- [x] Emit the slice length, allocate with the same `newarray` /
      `anewarray` choice, and walk. Load with `_emit_element`. Store
      with `_ARRAY_STORE` (default `aastore`). Destination index
      increments by `1`. Return `descriptor`.

`arraylist:` (every step, including `1`):

- [x] `new` / `dup` / `<init>` via `_require_method`, as
      `_fill_collection` (`collections.py:184`).
- [x] Walk the source index. `_emit_element`, then `_box` (no-op for
      `L` and `[`), then `add(Ljava/lang/Object;)Z` and `pop`.
- [x] Do not emit `subList`. Return the same `arraylist:` descriptor.

### 5. Tests

Add cases to `tests/test_cli_compile_collections.py`. Assert Jasmin
text. This suite does not execute the slice.

- [x] `tail = text[1:]`, `text[:10]`, `text[1:10]`, and `text[:]`
      contain `substring(II)Ljava/lang/String;`, `slice_start`, and
      `slice_stop`. `if_icmpgt` occurs before `substring`.
- [x] `text[-2:]` still uses `substring` and `slice_start`.
- [x] `text[5:2]` still contains `substring` after `if_icmpgt`.
      Bounds are not folded against a runtime length, so the JDK
      call stays on the not-taken side of the guard. Assert the
      guard. Do not assert that `substring` is absent.
- [x] `text[::-1]` and `text[::2]` contain `charAt`, `newarray char`,
      and `String/<init>([C)V`. They do not contain `substring`.
      The reverse case contains `if_icmple`.
- [x] `step = 2` then `text[::step]` contains `charAt` and
      `IllegalArgumentException`, not `substring`.
- [x] `text[::0]` raises `JasminCompilerError` with
      `slice step cannot be zero`.
- [x] `text[::"a"]` raises `slice bound must be int`.
- [x] Immutable `items = [1, 2, 3, 4]; taken = items[:2]` contains
      `copyOfRange` and `slice_start`, and does not contain
      `ArrayList`.
- [x] `names = ["a", "b", "c"]; head = names[:2]` contains
      `copyOfRange` and `checkcast [Ljava/lang/String;`.
- [x] `items = [1, 2, 3]` then `items.append(4)` then
      `taken = items[:2]` contains `new java/util/ArrayList`, `add`,
      and not `subList` or `copyOfRange`.
- [x] `rev = items[::-1]` on an `int` array contains `newarray int`,
      `iaload`, `iastore`, and `if_icmple`, and not `copyOfRange`.
- [x] `items[1:3] = xs` still raises
      `a slice is only supported as a for iterable`.
- [x] A `HashSet` slice raises
      `subscript needs an array, a list, or a string`.
- [x] `for item in items[1:]` on an array does not contain
      `copyOfRange`. Existing loop and stdlib slice tests stay green.

### 6. Docs

- [x] `docs/covered.md` around `:130` and `:135`: a slice expression
      copies. Name `substring` for step `1` on `str`,
      `copyOfRange` for step `1` on an array, and a new `ArrayList`
      for `arraylist:`. State that `for` over a slice still walks in
      place. Keep the `slice_start` / `slice_stop` sentences.
- [x] `docs/not-covered.md:44` and `:55`: remove `tail = label[1:]`
      from the rejected list. Say slice assignment, `slice()`, and a
      comma slice stay rejected.
- [x] `ROADMAP.md:216`: `slice_start` / `slice_stop` normalize both
      `for` slices and value slices.
- [ ] `ripwire . --doc-drift --legend=compact` reports `drift="0"`.

### 7. Close the goal

- [x] `python3 -m unittest discover -s tests -v`
- [x] `just check`
- [x] `just lint`
- [x] Write `goals/slice-values/evidence.md` with those commands, a
      `## Memory Writes` block, and a standalone `Verdict: PASS` line.
- [x] Check every finished box in this file. Commit with
      `(goals/slice-values/goal.md)` or `[goal: slice-values]` under
      `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:`. Do not push.
- [ ] `sila goals certify slice-values
      --evidence=goals/slice-values/evidence.md` (or
      `sila goals audit slice-values`). Then `sila goals`, and commit
      the `goals/README.md` receipt the same way. Do not push.
- [ ] `sila goals --json` shows `status: "implemented"`,
      `verification_status: "verified"`, `unverified_count: 0`, and
      `git status` is clean.

## Acceptance

- `label[1:]` and `items[:10]` compile to a new sequence of the
  source kind described above.
- Omitted, negative, and inverted bounds go through `slice_start` /
  `slice_stop`. Inverted bounds do not reach `substring` or
  `copyOfRange`.
- `items[::-1]` and a non-literal step compile, with step `0`
  rejected the way `for` rejects it.
- `for` slice tests are unchanged in the strings they already lock.
- Slice store, generator slice, and non-sequences still fail.
