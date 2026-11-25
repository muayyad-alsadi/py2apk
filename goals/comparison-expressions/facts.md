# Facts: Store comparisons and boolean tests

## Current state (verified)

- Every `if`, `while`, `and`/`or` arm, and conditional expression
  (`expr.py:210`, `loops.py:55`, `loops.py:69`) enters through
  `_condition_jump_false` (`compare.py:128`). A single `==`, `!=`,
  `is`, `is not`, or ordered `int`/`bool` compare emits a branch and
  leaves no `Z`.
- Stored compares, `and` / `or`, and `not` go through
  `_condition_as_bool`: the same jump, then `iconst_1` / `iconst_0`.
  `_compare_value` (`compare.py:44`) and `_unary_not` (`compare.py:50`)
  are that wrapper. `_bool_value` is the same wrapper.
- Jump lowering for `and` / `or` stays in `_bool_jump_false`
  (`compare.py:190`) and short-circuits.
- `len(ops) > 1` uses `_chained_compare_jump`. A single link still
  uses `_comparison_operands` (`compare.py:369`), which reads only
  `ops[0]` and
  `comparators[0]`. For `==` / `!=` / `is` it pushes a right-hand
  constant before the left operand. Ordered compares do not swap.
  `test_cli_compile_fstring.py:172` locks that order:
  `iconst_1` then `iload` then `if_icmpne`.
- `docs/covered.md` documents jump compares and stored boolean tests.
  `docs/not-covered.md` no longer rejects stored `==`, `and`, or chains.

## Decision: one materializer, two compare shapes

Stored `==`, `!=`, `and`, `or`, `not`, and `is` are conditions that
leave `Z`. They are not a second compare implementation.

```text
false = label
done = label
_condition_jump_false(node, false)
iconst_1
goto done
false:
iconst_0
done:
```

`_compare_value`, `_bool_value`, and `_unary_not` are that wrapper.
The old identity helper folds into it. Existing `is` tests stay the bytecode
gate: `ifnonnull` / `iconst_1` / `iconst_0`, no `equals`, and a `while`
test still has no `iconst`.

Do not reimplement `_equality_jump`, ordered `if_icmp*`,
`_bool_jump_false`, or `_truth_jump_false` in the value handlers.
`_condition_jump_false` is already `ccx=24`; growing a copy of it is
the defect this plan avoids.

### Recursion trap

The wrapper calls `_condition_jump_false` on the same node the value
handler just received. That is safe only while the condition path
returns before the fall-through `_expression` at `compare.py:185`.
Today it returns for `BoolOp`, single `Compare`, and `not`. A chain
branch must return too. If a value handler calls `_expression` on its
own node, the handler calls itself.

### What stays a jump

`if`, `while`, `and`/`or` arms, and `if`/`else` expressions keep
calling `_condition_jump_false` directly. A single compare there must
keep today's opcodes and operand order. The f-string compare test is
the lock. The wrapper's `iconst_1` / `iconst_0` appear only when the
compare or boolean test is the value of a store, `return`, or call.

## Boolean result, not Python's operand

CPython `and` / `or` return an operand. This goal returns `Z`, matching
the goal statement and the type system (one descriptor per expression).
`ready and count > 0` stores `0` or `1`. It does not store `ready` or
the comparison's operands. `not` is already a boolean in Python.

Short-circuit is control flow, not omitted bytecode. Both arms are
emitted. A false `and` left arm jumps past the right arm, which is
what `test_and_short_circuits_the_right_comparison` already checks
with instruction order. A stored `and` / `or` uses that same jump,
then the wrapper stores `Z`.

## Chains

`a op0 b op1 c` means `(a op0 b) and (b op1 c)`, `b` runs once, and
`c` does not run when the first link is false.

`_comparison_operands` cannot lower a chain. It always emits
`comparators[0]` and it changes evaluation order for a constant right
operand. Using it per link would evaluate the middle twice or would
rewrite single-link bytecode.

Chain rule:

- `len(ops) == 1` stays in the current body, via a extracted
  single-link function if that keeps the branch flat. Bytecode for
  `left == 1` stays `iconst` then `iload` then `if_icmp*`.
- `len(ops) > 1` is a new walker. Emit the left operand and park it.
  For each link, emit the next comparator only after the previous
  link's false jump is in place, park it, reload the two slots in the
  order that link's op wants, then use the existing jump helper
  (`_equality_jump`, `_identity_jump`, or the ordered opcode). The
  parked right slot is the next left slot. Do not emit it again.
- The walker's false label is the label `_condition_jump_false`
  received, so a chain inside `if`, `while`, or `and` jumps out
  without `iconst`.
- Links may mix `==`, `!=`, `is`, `is not`, and ordered `int`/`bool`
  compares. `in` / `not in` still raise
  `unsupported comparison In` / `NotIn`.
- Reload order for one equality or identity link follows today's
  constant-on-the-right swap. Evaluation order stays left to right,
  which is the chain rule; the swap applies only to the reload.

## Types

Value handlers that emit must have type handlers that do not emit.
`_type_of` on each operand, then the same acceptance the jump uses:

| Link | Accept | Reject |
| --- | --- | --- |
| `==` `!=` | `I`/`Z` pair, any numeric pair, any reference pair, mixed ref/numeric | anything else (`only integer/boolean comparisons are supported`) |
| `<` `<=` `>` `>=` | both `I` or `Z` | `only integer/boolean comparisons are supported` |
| `is` `is not` | both references, or mixed ref/numeric | `is compares references` |
| `and` `or` `not` arm | a compare, boolean op, `not`, `I`/`Z`, or `_supports_truth` | `if condition must be bool or int` |

Mixed ref/numeric `==` is false with `pop` and no `equals`. `is`
between two numbers stays rejected. `float` equality is in. Ordered
`float` / `long` stays out. A `float` used as `and` / `not` / `if`
stays out.

A chain's type is `Z` only when every link type-checks. Type checking
walks every operand with `_type_of` and does not need to model the
runtime skip.

## File contracts

### `Py2APK/compiler_j/compare.py`

- `EXPR_HANDLERS` / `TYPE_HANDLERS`: `ast.Compare` → `_compare_value`
  / `_compare_type`; `ast.BoolOp` → `_bool_value` / `_bool_type`.
- `_compare_value`, `_bool_value`, and `_unary_not` call one
  `_condition_as_bool` wrapper and return `Z`.
- Type methods return `Z` or raise, and call no `_expression`.
- Single-link jump bytecode stays on the current helpers.
- New chain walker is used only when `len(ops) > 1`.

### Tests

- `tests/test_cli_compile_compare.py` owns the new cases.
- `tests/test_cli_compile_fstring.py` (`compare`, `match`, `widths`,
  `missing`) and the three existing methods in
  `test_cli_compile_compare.py` stay green without edited assertions.

### Docs

- `docs/covered.md` and `docs/not-covered.md` follow the subset.
- Claim lock lists `compare.py` and `test_cli_compile_compare.py`.
  Doc edits are the repo rule when the subset changes; they are not
  new compiler surface.

## Scope

In: stored `==`, `!=`, `and`, `or`, `not`; chains of the compares
already legal in `if`; those chains in `if` / `while`; `is` / `is not`
routed through the same wrapper.

Out: `in` / `not in`; ordered `long` / `float`; `and` / `or` returning
an operand; new jump opcodes for single compares; `module.py` store
changes (a `Z` local already stores).

## Risks

- Routing a condition through `_expression` materializes `iconst` in
  `if` / `while` and fails the identity and f-string tests.
- Emitting every comparator before the first link test evaluates a
  later operand that Python skips, and evaluates the middle twice if
  the next link emits it again.
- Rewriting the single-link path to park-then-reload breaks
  `iconst_1` / `iload` / `if_icmpne` order.
- A type method that emits, or a value method that skips `_type_of`,
  disagrees with the jump errors.
- Treating "short-circuit" as "the right arm is absent from the
  Jasmin" will fail: both arms are present, and a jump separates them.
