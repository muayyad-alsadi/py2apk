# Goals Status & Pragmatic Execution Roadmap

This index tracks all goal packages under `goals/`, their execution mode (Independent ⚡ vs Dependent 🔗), dependencies, and progress status.

## 🤖 Agent Guide: How to Create, Claim & Execute Goals

1. **Create a Goal**: Scaffold a new goal package conforming to standards:
   ```bash
   sila goals create <slug> --title="..." --files="path/to/file.go" [--depends-on="..."] [--independent]
   ```
2. **Claim a Goal**: Lock the goal so no other agent duplicates work:
   ```bash
   sila goals claim <slug> --agent=<name> [--note="evaluating..."]
   ```
3. **Launch & Implement**: Follow `facts.md` and `plan.md` until all tests pass:
   ```bash
   /goal goals/<slug>/goal.md
   ```
4. **Progress Updates**: Report status updates during execution:
   ```bash
   sila goals report <slug> "Running e2e test suite"
   ```
5. **Commit with Goal Reference**: Include the goal path in your commit message:
   ```bash
   git commit -m "feat(scope): implement description (goals/<slug>/goal.md)"
   ```
6. **Auto-Reconciliation**: Run `sila goals` to scan commits, mark 🟢 **Implemented**, and clear the claim lock.

## Summary
- **Total Goals**: 10
- 🟡 **Ready to Execute (Pending)**: 1 (0 independent ⚡, 1 unblocked 🔗)
- 🟢 **Implemented**: 9 (3 verified 📜, 4 stale ⚠️, 2 unverified)
- 🎯 **By Tier**: 0 immediate (Tier 1), 10 roadmap (Tier 2), 0 nice-to-have (Tier 3), 0 wont-fix (Tier 4)
- 📋 **Execution Tasks Progress**: 258/275 completed (93%)

---

## 🟡 Ready to Execute (Pending Goals)

| Goal Package | Tier | Mode & Sequence | Dependencies | Focus & Description | Launch Command |
| :--- | :---: | :---: | :--- | :--- | :--- |
| [`higher-order-builtins`](higher-order-builtins/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#12)` | Deps met: function-values | Lower map, filter, and sorted with key — Lower single-iterable `map(fn, xs)`, `filter(fn, xs)`, and `sorted(xs, key=fn, r... | `/goal goals/higher-order-builtins/goal.md` |

---

## 🟢 Implemented

| Goal Package | Mode | Shape | Verification | Task / Ref | Commit |
| :--- | :---: | :--- | :---: | :--- | :--- |
| [`minsdk-api-check`](minsdk-api-check/goal.md) | `⚡ Indep` | 🚢 SHIP | unverified | Git Commit | `8eabd0c` |
| [`hashset-literals`](hashset-literals/goal.md) | `⚡ Indep` | 🚢 SHIP | unverified | Git Commit | `4815f8b` |
| [`comparison-expressions`](comparison-expressions/goal.md) | `⚡ Indep` | 🚢 SHIP | verified 📜 | Git Commit | `b05a695` |
| [`true-division`](true-division/goal.md) | `⚡ Indep` | 🚢 SHIP | verified 📜 | Git Commit | `b04dc94` |
| [`slice-values`](slice-values/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `0aa3f09` |
| [`tuple-for`](tuple-for/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `b94758f` |
| [`function-values`](function-values/goal.md) | `⚡ Indep` | 🚢 SHIP | verified 📜 | Git Commit | `937ff84` |
| [`string-split-join`](string-split-join/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `cac0495` |
| [`math-time`](math-time/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `bb91a35` |
