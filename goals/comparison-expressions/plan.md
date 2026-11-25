# Plan: Store comparisons and boolean tests

## Execution Steps

### Phase 1 — One boolean wrapper

- [x] Add `_condition_as_bool(self, node: ast.expr) -> str` in
      `compare.py`. It calls `_condition_jump_false` on that node,
      then emits `iconst_1` / `goto` / `iconst_0`, and returns `Z`.
      This is the identity boolean pattern generalized.
- [x] Point `ast.Compare` at `_compare_value` / `_compare_type`.
      `_compare_value` is `_condition_as_bool`. `_compare_type`
      returns `Z` after a pure check of one link or a chain.
      Keep `_identity_jump`. `_compare_type` checks an `is` link.
- [x] Register `ast.BoolOp` as `_bool_value` / `_bool_type`.
      `_bool_value` is `_condition_as_bool`. `_bool_type` accepts
      each arm with the condition rules below and returns `Z`.
- [x] Implement `_unary_not` as `_condition_as_bool` and
      `_unary_not_type` as the same arm check. Do not copy
      `_truth_jump_false`.
- [x] Add `_condition_accepted` (name flexible) used only by type
      handlers. A compare, `BoolOp`, or `not` recurses. Otherwise
      `_type_of`: allow `I` / `Z` and `_supports_truth`; otherwise
      raise `if condition must be bool or int`. No `_expression`.

### Phase 2 — Chains only where the jump path rejects them

- [x] Leave the `len(ops) == 1` body in
      `_condition_jump_false` (`compare.py:128`).
      It still calls `_comparison_operands` (`compare.py:369`).
      Do not park single-link operands. F-string order
      `iconst_1` / `iload` / `if_icmpne` stays.
- [x] When `len(ops) > 1`, call a new walker and return. The walker
      parks `node.left`, then for each op emits that comparator,
      parks it, reloads the previous slot and the new slot, and
      jumps to the caller label on failure. The new slot becomes
      the next left. Equality and identity reloads apply today's
      constant-right swap; ordered reloads stay left then right.
- [x] Reuse `_equality_jump`, `_identity_jump`, and the ordered
      opcode map (`compare.py:37`). A failing link jumps to the
      condition label, so `if` / `while` / `and` emit no `iconst`.
      A stored chain gets `iconst` only from `_condition_as_bool`.
- [x] `_compare_type` on a chain type-checks every link with
      `_type_of` on each operand once. `==` / `!=` accept numeric,
      reference, and mixed ref/numeric pairs. Ordered accepts `I`/`Z`
      only. `is` uses the current reference rule. `in` / `not in`
      still raise `unsupported comparison ...`.
- [x] Confirm the condition path never calls `_expression` on a
      `Compare`, `BoolOp`, or `not` node, including a chain. The
      fall-through at `compare.py:185` is only for a bare `int` /
      `bool` or a truthy reference.

### Phase 3 — Tests, docs, evidence

- [x] `test_stores_equality_checks`: `same = left == right` for
      `str` has `equals`, `iconst_1`, and `iconst_0`; `int` `==`
      has `if_icmp` and no `equals`; `folded == count` has `pop`
      and `iconst_0` and no `equals`; `return left != right`;
      `width == 1.0` has `fcmpg` and `iconst`; `folded == None`
      has `ifnull` and `iconst`.
- [x] `test_stores_bool_ops`: stored `ready and count > 0` emits
      the right compare after the left failure jump (both present);
      stored `or` jumps to success before the right arm; `if` of
      `and` still has no boolean `iconst_0`.
- [x] `test_stores_not`: `not items` for `list[str]` has
      `arraylength` and `iconst` (`list[str]` is a `String[]`);
      `not flag` for `bool` has `ifne` and `iconst`. `not width`
      for `float` still raises `if condition must be bool or int`.
      `if not count` for `int` jumps with `ifne` and does not
      store a boolean.
- [x] `test_chained_comparisons`: `if left == mid == right` has
      two equality jumps and no `iconst`. `left == len(name) == right`
      contains one `String.length` invoke, and that invoke is
      before the second operand's load. `if lo < mid < hi` has
      two ordered `if_icmp*` and no `iconst`. A stored chain has
      `iconst_1` and `iconst_0`. `in` inside a chain still raises.
- [x] Keep, without assertion edits:
      `test_lowers_identity_checks`, `test_stores_identity_checks`,
      `test_and_short_circuits_the_right_comparison`, and
      `test_lowers_fstrings_builtins_and_constant_comparisons`.
- [x] Update `docs/covered.md` and `docs/not-covered.md` for stored
      `==` / `!=` / `and` / `or` / `not` and chains. State that
      `and` / `or` store `0` or `1`.
- [x] Leave green: `python3 -m unittest discover -s tests -v`,
      `just check`, `just lint`, and
      `ripwire . --doc-drift --legend=compact` with `drift="0"`.
- [x] Write `goals/comparison-expressions/evidence.md` with those
      commands, a `## Memory Writes` block, and a standalone
      `Verdict: PASS` line.

### Phase 4 — Attribution and registry closure

- [x] **Phase 4 — Attribution & Registry Closure**: (1) check all
      completed tasks in `goals/comparison-expressions/plan.md`;
      (2) commit with `(goals/comparison-expressions/goal.md)` or
      `[goal: comparison-expressions]` using a `feat:`, `fix:`,
      `refactor:`, `perf:`, or `chore:` subject (`docs:` and `test:`
      commits are ignored); (3) certify via `sila goals certify
      comparison-expressions
      --evidence=goals/comparison-expressions/evidence.md` or
      `sila goals audit comparison-expressions`; (4) reconcile with
      `sila goals` and commit `goals/README.md` plus certification
      receipts; `sila goals --json` reports `unverified_count: 0`
      for delivered goals.
