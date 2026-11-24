# Plan: Compare with is and is not

## Execution Steps
- [x] **Phase 1 — Contracts & Schemas**: Define models, data structures, and signatures.
- [x] **Phase 2 — Core Implementation**: Implement business logic, routes, and error handling.
- [x] **Phase 3 — Verification & Evidence**: Write unit tests, run end-to-end verification, and write markdown evidence with an explicit standalone `Verdict: PASS` declaration (or `## Verdict` followed by `PASS`) to satisfy `EVIDENCE_VERDICT_REQUIRED`.
- [x] **Phase 4 — Attribution & Registry Closure**: (1) check all completed tasks in `goals/is-comparison/plan.md`; (2) commit with `(goals/is-comparison/goal.md)` or `[goal: is-comparison]` using a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` subject (docs/test commits are ignored); (3) certify via `sila goals certify is-comparison --evidence=goals/is-comparison/evidence.md` (with `Verdict: PASS`) or `sila goals audit is-comparison`; (4) reconcile via `sila goals` and commit the updated `goals/README.md` and certification receipts; verify `sila goals --json` reports `unverified_count: 0` for delivered goals.
