# Plan: Unpack a pair in `for`

Design is locked here. [`facts.md`](facts.md) is the code it
comes from. Implement only `loops.py`, the loop tests, and
the three docs named below.

## Behavior

Validate the target before the iterable, as `_for_statement`
does today. One helper on `_LoopsMixin`:

`_for_target_names(self, target: ast.expr) -> list[ast.Name]`

- `ast.Name`: return a one-element list of that name.
- Starred tuple: `starred assignment targets are unsupported`.
- Use that same text as `_unpack_assign`.
- An all-name tuple of length >= 1 returns those names.
- Length 1, 2, and 3+ share that path.
- Any other target: `for target must be a name or a tuple of names`.
- That includes `[a, b]`, nesting, attributes, and `()`.

Call it from `_for_statement`, `_for_index`, and
`_for_generator`. Delete the three
`for target must be a name` raises. A name still stores
with the existing `_store(node.target, element)` call and
no extra instructions. A tuple never calls `_store` with
the whole element.

Add one store helper:

```
def _store_for_unpack(
    self,
    names: list[ast.Name],
    element: str,
    node: ast.AST,
) -> None:
```

Call it only for an `ast.Tuple` target, from both
`_for_index` (after `_emit_element`) and `_for_generator`
(after `_restore_yield_type`). Keep it before
`loops.append`, where `_store` sits now.

If `element` does not start with `[`, raise
`for unpack requires an array element`. That covers
`(1, 2)`, `[1, 2, 3]`, `range`, a `list[int]` parameter,
and a `java.util.Iterator`. Do not try to read `arraylength`
on a scalar. Do not call `_unpack_assign`.

The slot type is `_sequence_element(element)`. For `[I`
that is `I`. For `[Ljava/lang/String;` it is
`Ljava/lang/String;`. Both names get that one type.
There is no per-slot type and no per-slot `checkcast`.
The generator `checkcast` of the whole array already
happened in `_restore_yield_type`.

Stash the array with `_cursor_name("pair")` and `_keep`.
Do not `dup` once per index: the last `_store` would leave
the array on the stack. Then emit the length check, copying
the shape in `_emit_slice_step`:

- load the temp, `arraylength`, `_emit_int(len(names))`
- `if_icmpeq` the ok label
- `new java/lang/IllegalArgumentException`
- `dup`
- `invokespecial java/lang/IllegalArgumentException/<init>()V`
- `athrow`
- ok label

This is not CPython's `ValueError`. The subset has no
`ValueError` bytecode. A non-constant zero slice step
already throws this empty `IllegalArgumentException`.
Use that, not a new `Py2APK/stdlib` function. Python has
no unpack helper to mirror, and a compiler-only helper
must not be invented.

Then, for each index, load the temp, `_emit_int(index)`,
emit `_ARRAY_LOAD.get(slot, "aaload")`, and
`_store(name, slot)`. `_store` already allocates `D` and
`J` as two locals. Int uses `iaload` / `istore`. A
reference uses `aaload` / `astore`.

Also reject a bad literal length at compile time, so the
unit tests can see it. They inspect Jasmin. They do not
run it. When `node` is an `ast.For`, `node.iter` is an
`ast.List` or `ast.Tuple`, it is not a slice subscript,
and every element is an `ast.List` or `ast.Tuple`, each
of those lengths must equal `len(names)`. Otherwise raise
`for unpack length does not match` on the bad element.
If any element is a name or another expression, skip this
check. The runtime `arraylength` check still runs.
`for a, b in [[1], [2, 3]]` fails here.
`for a, b in (1, 2)` does not: its elements are not
sequences, so it hits the scalar error instead.

Always emit the runtime check for a tuple target, including
literals whose lengths already matched. One path.

Do not add `_type_of_for_element`. `_store` records each
name's descriptor. The body type-checks through that, as a
normal local does.

Leave the index `continue` update, the generator
`goto` / `pop` / `orelse`, and the name-target store
untouched. Do not edit `module.py` or `collections.py`.
Do not touch `Py2APK/stdlib`, so there is no directory
mtime to update.

## In

- Int pairs: `[(1, 2), (3, 4)]` and `[[1, 2], [3, 4]]`.
- A local that holds that array.
- Parameter `pairs: list[list[int]]` (descriptor `[[I`).
- String pairs: `[("x", "y")]`.
- `def pairs() -> list[int]` then `yield (1, 2)`.
- A slice of an array of pairs, such as `pairs[:1]`.
- Arity 3: `for a, b, c in [(1, 2, 3)]`.
- `for else` on a tuple target.

## Out

- `enumerate`, `zip`, and any new builtin.
- Starred targets and starred elements.
- Nested targets: `for (a, b), c in ...`.
- List targets `for [a, b] in ...` (legal Python).
- Heterogeneous pairs. `(1, "a")` already fails.
- Strings, scalars, `arraylist:` elements, and `range`.
- Changing assignment unpack.
- Executing the APK. Tests assert `athrow` only.

## Tasks

### 1. Target helper

- [x] Add `_for_target_names` as specified above.
- [x] Replace the three `ast.Name` guards with it.
- [x] Keep a name on `_store(node.target, element)`.
- [x] Send a tuple to `_store_for_unpack`.

### 2. Unpack store

- [x] Add `_store_for_unpack` as specified above.
- [x] Use it in `_for_index` and `_for_generator` only.
- [x] Add the literal-length check before emit.
- [x] Type every new signature.
- [x] Return from each failing guard. Keep the success path flat.

### 3. Tests

Add `_assert_tuple_for` in `tests/test_cli_compile_loop.py`
and call it from a new `AliasLoweringTests` method.
Use `_compiled_class` / `_compiled_generator` like the
helpers above it. Keep every existing example.

- [x] Keep the `(1, 2)` raise. Assert the scalar message.
- [x] Int pairs: `aaload`, `arraylength`, `if_icmpeq`, `iaload`, `istore`.
- [x] Int pairs also emit `IllegalArgumentException`.
- [x] String pairs: `aaload` and `astore` for `("x", "y")`.
- [x] A `list[list[int]]` parameter loop emits `iaload`.
- [x] Arity 3 emits `iconst_2` for `(1, 2, 3)`.
- [x] `for a, *rest` raises the starred assignment message.
- [x] `for [a, b]` raises the tuple-of-names message.
- [x] `for (a, b), c` raises that same message.
- [x] `[[1], [2, 3]]` raises the length message.
- [x] `[1, 2, 3]` and `range(3)` raise the scalar message.
- [x] `-> list[int]` plus `yield (1, 2)` compiles.
- [x] That generator loop has `checkcast [I` and `iaload`.
- [x] A tuple-target `for` with `else` still emits the else body.
- [x] Existing loop helpers stay green, including fibonacci.

### 4. Docs

- [x] `docs/covered.md`: tuple-of-names `for` compiles on arrays.
- [x] Keep the sentence that `(1, 2, 3)` is an array.
- [x] Delete the "does not compile" line for `for a, b in pairs`.
- [x] `docs/not-covered.md`: list starred, nested, `[a, b]`, scalars.
- [x] Do not say every tuple target fails.
- [x] `ROADMAP.md`: same-type tuples are arrays and can be unpacked.
- [x] Say `enumerate` and `zip` are still not implemented.
- [x] Delete "A tuple value does not."

### 5. Gates

- [x] `python3 -m unittest discover -s tests -v`
- [x] `just check`
- [x] `just lint`
- [ ] `ripwire . --doc-drift --legend=compact` shows `drift="0"`.

`--affected` on `loops.py` stays an empty test list.
Do not treat that as coverage. Run the unittest command.

### 6. Close the goal

- [x] Write `evidence.md` with `## Memory Writes`.
- [x] Include a standalone `Verdict: PASS` line.
- [ ] Check every task in this file.
- [x] Commit under `feat:`/`fix:`/`refactor:`/`perf:`/`chore:`.
- [x] Put `(goals/tuple-for/goal.md)` or `[goal: tuple-for]` in that commit.
- [ ] Certify with `sila goals certify` or `sila goals audit`.
- [ ] Pass `evidence.md` to that certify command.
- [ ] `sila goals`, then `sila goals --json` is verified.
- [ ] `unverified_count` is 0 and `git status` is clean.
