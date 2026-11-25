# Facts: Unpack a pair in `for`

Verified against the tree. Line numbers are the definitions
these symbols had when this file was written.

## For today

`ast.For` is dispatched by the string
`STMT_HANDLERS[ast.For] = "_for_statement"`
(`Py2APK/compiler_j/loops.py`, class `_LoopsMixin`).
Ripwire `--impact=_for_statement` reports `reaches=0`, and
`--affected=Py2APK/compiler_j/loops.py` reports `tests=0`,
because that edge is a method name in a dict, not a call.
The loop tests still compile through `tests/test_cli_compile_loop.py`.

`_for_statement` (`loops.py`, def line 81) rejects any target
that is not `ast.Name` with `for target must be a name`
(line 83), then types the iterable. The same guard is repeated
in `_for_generator` (def line 105, guard line 107) and
`_for_index` (def line 139, guard line 147). A tuple target
dies at the first guard. It never reaches a store.

After the guard, `_for_statement` does this:

- A subscript iterable must be a slice, applied later.
- Any other subscript is `subscript is unsupported`.
- `Ljava/util/Iterator;` is forced to element `I`.
- A generator type calls `_for_generator` and returns.
- A slice of a generator is unsupported.
- Otherwise `_sequence_element` must return a type.
- If it does not: `for requires an array, a list, or a generator`.
- That element type is passed to `_for_index`.

`_for_index` keeps the sequence, its length, and the slice
cursors. Each iteration calls `_emit_element` (`loops.py`,
def line 293), then `_store(node.target, element)`, then
pushes the loop labels and compiles the body. `continue`
still adds the step. `orelse` is compiled at the end label.
Do not move that store relative to `loops.append`.

`_emit_element` loads the sequence and the index. A `[`
descriptor uses `_ARRAY_LOAD.get(element, "aaload")`.
Anything else is `ArrayList.get` plus `_restore_yield_type`.
`_emit_sequence_length` (`loops.py`, def line 286) emits
`arraylength` for a `[` descriptor and `size()` otherwise.

`_for_generator` calls `next()Ljava/lang/Object;`, then
`_restore_yield_type(element)`, then `_store`. The catch is
`NoSuchElementException`, which is the lowering of
`StopIteration`. The body is outside that catch.

`_ARRAY_LOAD` (`Py2APK/compiler_j/types.py`) maps `Z` `I` `F`
`D` `J` to `baload` `iaload` `faload` `daload` `laload`.
Every other component, including another array, uses `aaload`.

`range` (`_emit_range` in `loops.py`) records element `I` on
`py2apk$gen$1`. A non-constant slice step of 0 is a runtime
`java/lang/IllegalArgumentException` with no message:
`new`, `dup`, `invokespecial <init>()V`, `athrow`
(`_emit_slice_step`, def line 212). A constant 0 is the
compile error `slice step cannot be zero`. There is no
`ValueError` class in the compiler's emitted bytecode.

## Assignment unpack today

`_unpack_assign` (`Py2APK/compiler_j/module.py`, def line 667)
is only for an `ast.Tuple` target and an `ast.Tuple` value.

- Starred target: `starred assignment targets are unsupported`.
- That raise is `module.py` line 669.
- Non-tuple value: `unpacking requires a tuple of the same length`.
- That raise is `module.py` line 671.
- Starred values: `starred assignment values are unsupported`.
- Different lengths: `assignment unpack length does not match`.

It evaluates each value element into a temp (`_hold_temp`),
then `_store`s each target. `_store` (`module.py`, def line 691)
accepts `ast.Name` and `self.attr` only. A tuple target there
would be `unsupported assignment target Tuple`. The loop must
not call `_unpack_assign`: the element is already on the
stack, and it is a Java array, not an `ast.Tuple`.

## What a tuple or list already is

`_tuple_literal` (`collections.py`, def line 112) always
calls `_java_array`. `_list_literal` (def line 115) calls
`_java_array` unless that list node is a field or a mutable
sink, in which case it calls `_array_list`.

`_element_type` (def line 128) requires one component type.
An empty literal has no element type. A starred element is
`tuple unpacking is unsupported` (or `list` / `set`). Two
different types are `a tuple needs one element type`.
`(1, "a")` already fails there. The loop test at
`tests/test_cli_compile_loop.py` line 200 covers that
literal, not a `for`.

`_array_descriptor` (`types.py`, def line 261) is
`"[" + element`. It does not store the length. `[I` is every
`int` array. `_java_array` pushes the count only as the
`newarray` operand. Consumers cannot read that count back
from the descriptor.

`_sequence_element` (`base.py`, def line 179) returns the
`arraylist:` payload from `_list_element` (`types.py`,
def line 248) when the descriptor has that prefix.
Otherwise a leading `[` strips one character. So:

- `(1, 2)` is `[I`. The `for` element is `I`.
- `[(1, 2), (3, 4)]` is `[[I` if the outer list is an array.
- That `for` element is `[I`. One more strip is `I`.
- A mutable list of those pairs is `arraylist:[I`.
- `_sequence_element` of that descriptor is still `[I`.
- `list[int]` annotates as `[I` (`_listed_element`, line 230).
- `list[list[int]]` annotates as `[[I`.
- Only the name `list` is special. `tuple[int, int]` is not.

`docs/covered.md` already says a same-type tuple such as
`(1, 2, 3)` is an array. `ROADMAP.md` lines 97–98 say
"A tuple value does not." Those two sentences disagree.
Same-type tuple literals do compile. Heterogeneous tuples
and the `tuple` builtin do not.

## Generators

`_bind_generators` (`module.py`, def line 439) sets
`generator_elements` from the function result. An
`Iterator[...]` annotation replaces that with the bracket
payload (`_iterator_slice`). `yield` (`_emit_yield`,
`generators.py`, def line 569) requires the value's
descriptor to equal that element, or a compatible reference
or array with a `checkcast`.

`def pairs() -> list[int]` therefore yields `[I`.
`yield (1, 2)` is also `[I`, so it matches.
`_restore_yield_type` (`generators.py`, def line 656)
`checkcast`s a `[` element, including `[I`. Primitive
results are unboxed. An array is not boxed on the way out
and not unboxed on the way back.

## Tests and docs that mention this hole

`_assert_loop_controls` starts at
`tests/test_cli_compile_loop.py` line 120.
`test_lowers_break_continue_else_range_and_tuples` (line 265)
calls it. At line 222 it `assertRaises(JasminCompilerError)`
for `for a, b in (1, 2):` (source line 228). It does not
assert the message. That program is a tuple of ints, so the
element type is `I`, not a pair.

`_assert_for_loops` starts at line 15, not 262.
Line 262 is `test_lowers_for_over_arrays_slices_and_generators`,
which calls that helper. It covers a name over a string
array, a reverse slice, a generator `for`, and a `for` that
yields. It expects `arraylength` and `aaload` for the name
loop. Those opcodes must remain.

`docs/covered.md` line 174 and `docs/not-covered.md` line 29
both say `for a, b in pairs` does not compile.
`ROADMAP.md` lines 97–100 say `enumerate` needs iteration
and tuple values, that same-length assignment unpack
compiles, and that a tuple value does not. `zip` is the
next bullet and is also unimplemented. `docs/not-covered.md`
lists `enumerate` and `zip` with the builtins that are not
aliases.

## What Python does, and what this subset cannot

CPython accepts `for a, b in [(1, 2), (3, 4)]` and
`for [a, b] in ...`. A short or long element raises
`ValueError` at that iteration. A non-iterable element,
including each `int` from `(1, 2)` or `range(3)`, raises
`TypeError` at that iteration. `for (a,) in ...` is legal.
`for () in ...` is a syntax error. Nested targets and
starred targets are legal Python.

This compiler has no string iterator, no heterogeneous
tuple, no `ValueError` bytecode, and no length in an array
descriptor. Those limits are facts. The plan states the
subset rules that follow from them.
