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
- **Total Goals**: 14
- 🟡 **Ready to Execute (Pending)**: 9 (0 independent ⚡, 9 unblocked 🔗)
- 🟢 **Implemented**: 5 (1 verified 📜, 4 stale ⚠️, 0 unverified)
- 🎯 **By Tier**: 0 immediate (Tier 1), 14 roadmap (Tier 2), 0 nice-to-have (Tier 3), 0 wont-fix (Tier 4)
- 📋 **Execution Tasks Progress**: 22/58 completed (37%)

---

## 🟡 Ready to Execute (Pending Goals)

| Goal Package | Tier | Mode & Sequence | Dependencies | Focus & Description | Launch Command |
| :--- | :---: | :---: | :--- | :--- | :--- |
| [`minsdk-api-check`](minsdk-api-check/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#4)` | Deps met: compiler-package | Reject APIs newer than minSdkVersion — Ship an API level table the jar index does not have, and reject invokes newer th... | `/goal goals/minsdk-api-check/goal.md` |
| [`hashset-literals`](hashset-literals/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#5)` | Deps met: compiler-package | Lower set literals to HashSet — Compile a homogeneous set literal to java.util.HashSet, using the same rules as ... | `/goal goals/hashset-literals/goal.md` |
| [`comparison-expressions`](comparison-expressions/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#7)` | Deps met: compiler-package | Store comparisons and boolean tests — Lower ==, !=, and, or, and not to boolean values, including a chained comparison... | `/goal goals/comparison-expressions/goal.md` |
| [`true-division`](true-division/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#8)` | Deps met: compiler-package | Compile true division and float arithmetic — Lower / to true division and allow + - * // % on float. int + - * // % already c... | `/goal goals/true-division/goal.md` |
| [`slice-values`](slice-values/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#9)` | Deps met: compiler-package | Use a slice as a value — Compile label[1:] and items[:10] as values. An index already compiles. A slice i... | `/goal goals/slice-values/goal.md` |
| [`tuple-for`](tuple-for/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#10)` | Deps met: compiler-package | Unpack a pair in for — Compile for a, b in pairs. Same-length unpack assignment already compiles. A sta... | `/goal goals/tuple-for/goal.md` |
| [`function-values`](function-values/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#11)` | Deps met: compiler-package | Use a function as a value — Allow a function or a stored lambda to be passed and called. A module function s... | `/goal goals/function-values/goal.md` |
| [`string-split-join`](string-split-join/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#12)` | Deps met: compiler-package | Lower literal split and join — Lower a literal str.split to a non-regex split, and flip sep.join(parts) to Stri... | `/goal goals/string-split-join/goal.md` |
| [`math-time`](math-time/goal.md) | `🗺️ Roadmap` | `🚢 SHIP 🔗 Ready (#13)` | Deps met: compiler-package | Lower math and time — Lower math and time, the later aliases named in docs/not-covered.md. re stays un... | `/goal goals/math-time/goal.md` |

---

## 🟢 Implemented

| Goal Package | Mode | Shape | Verification | Task / Ref | Commit |
| :--- | :---: | :--- | :---: | :--- | :--- |
| [`compiler-package`](compiler-package/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `867d226` |
| [`is-comparison`](is-comparison/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `e877355` |
| [`except-tuples`](except-tuples/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `41a9f67` |
| [`keyword-arguments`](keyword-arguments/goal.md) | `🔗 Dep` | 🚢 SHIP | verified 📜 | Git Commit | `41283b7` |
| [`comparison-values`](comparison-values/goal.md) | `🔗 Dep` | 🚢 SHIP | stale ⚠️ | Git Commit | `e18f983` |
