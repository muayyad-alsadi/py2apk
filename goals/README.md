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
- **Total Goals**: 6
- 🟡 **Ready to Execute (Pending)**: 4 (4 independent ⚡, 0 unblocked 🔗)
- 🟢 **Implemented**: 2 (1 verified 📜, 1 stale ⚠️, 0 unverified)
- 🎯 **By Tier**: 0 immediate (Tier 1), 6 roadmap (Tier 2), 0 nice-to-have (Tier 3), 0 wont-fix (Tier 4)
- 📋 **Execution Tasks Progress**: 8/24 completed (33%)

---

## 🟡 Ready to Execute (Pending Goals)

| Goal Package | Tier | Mode & Sequence | Dependencies | Focus & Description | Launch Command |
| :--- | :---: | :---: | :--- | :--- | :--- |
| [`except-tuples`](except-tuples/goal.md) | `🗺️ Roadmap` | `🚢 SHIP ⚡ Independent (#2)` | - | Catch more than one exception type — Emit one .catch per type in an except tuple and share one handler. try, raise, a... | `/goal goals/except-tuples/goal.md` |
| [`keyword-arguments`](keyword-arguments/goal.md) | `🗺️ Roadmap` | `🚢 SHIP ⚡ Independent (#3)` | - | Allow keyword arguments on calls — Match keyword call arguments to jars.db overloads. Unlocks builder-style Android... | `/goal goals/keyword-arguments/goal.md` |
| [`minsdk-api-check`](minsdk-api-check/goal.md) | `🗺️ Roadmap` | `🚢 SHIP ⚡ Independent (#4)` | - | Reject APIs newer than minSdkVersion — Ship an API level table the jar index does not have, and reject invokes newer th... | `/goal goals/minsdk-api-check/goal.md` |
| [`hashset-literals`](hashset-literals/goal.md) | `🗺️ Roadmap` | `🚢 SHIP ⚡ Independent (#5)` | - | Lower set literals to HashSet — Compile a homogeneous set literal to java.util.HashSet, using the same rules as ... | `/goal goals/hashset-literals/goal.md` |

---

## 🟢 Implemented

| Goal Package | Mode | Shape | Verification | Task / Ref | Commit |
| :--- | :---: | :--- | :---: | :--- | :--- |
| [`is-comparison`](is-comparison/goal.md) | `⚡ Indep` | 🚢 SHIP | stale ⚠️ | Git Commit | `e877355` |
| [`comparison-values`](comparison-values/goal.md) | `🔗 Dep` | 🚢 SHIP | verified 📜 | Git Commit | `0d0d9b1` |
