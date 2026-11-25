# Goal: Reject APIs newer than minSdkVersion

## Goal Description
Ship an API level table the jar index does not have, and reject invokes
newer than `minSdkVersion`.

The table comes from `data/api-versions.xml`, which already ships inside
the platform archive that `fetch_platform` downloads. It is parsed once
per platform fetch into `android.api.sqlite` beside `android.jar`,
merged into the project `jars.db` as an indexed `api_levels` table, and
read by the compiler through `JarCatalog.api_since`. `compile` then
compares the chosen signature's `since` with `sdkInfo.minSdkVersion`
from `apktool.yml` and fails with a located error. A member with no row
is allowed, and `min_api=None` keeps the check off for the shared stdlib
cache and for every existing test.

## Dependencies & Execution Order
- **Mode**: Independent (disjoint, parallelizable)
- **Depends On**: none
- **Sequence**: 4
- **Shape**: ship
- **Tier**: roadmap
- **Memory Protocol**: required

## Files to Touch
- Py2APK/jar_index.py
- Py2APK/cli_fetch_platform.py
- Py2APK/cli_compile.py
- Py2APK/compiler_j/base.py
- Py2APK/compiler_j/compiler.py
- Py2APK/compiler_j/calls.py
- tests/test_cli_compile_calls.py
- tests/test_cli_compile_command.py
- tests/test_jars.py
- ROADMAP.md
- docs/interals.md
- docs/covered.md
- docs/not-covered.md

## References
- **Shared Understanding & Fact Sheet**: [`goals/minsdk-api-check/facts.md`](facts.md)
- **Execution Plan**: [`goals/minsdk-api-check/plan.md`](plan.md)

## Memory Plan
- **Prime**: run `sila prime` (CLI) or `prime_context` (MCP) at task start and record the briefing timestamp.
- **Recall**: run `sila recall "<symptom or design question>"` before implementation; record each query and match count.
- **Writes**: record at least one `memo`, `lesson`, or `case` grounded to a file in this goal; use a `case` when the goal introduces a reusable mechanism.
- **Facts**: run `sila facts verify` before certification and record the exit code and violation delta.
- **Handoff**: run `sila handoff "<summary>" --open="..."` at session end and record the handoff ID.
- **Evidence**: embed a `sila/memory-writes/v1` block under `## Memory Writes`, per `memory-protocol-goal-contract`.

## Done Condition
1. All tasks in `plan.md` are checked (`- [x]`); unchecked tasks block the `implemented` transition.
2. Implementation is committed with `(goals/minsdk-api-check/goal.md)` or `[goal: minsdk-api-check]` attribution under a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` prefix.
3. Goal is certified (`goals/minsdk-api-check/certification.json` with `verified: true` or a signed `audit-receipt.json`) and any markdown evidence contains an explicit standalone `Verdict: PASS` declaration.
4. `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, and `unverified_count: 0` for delivered goals, with a clean `git status`.

## Scope

In:

- Method and constructor invokes written in project source (instance,
  static, imported static, constructor, and `super`).
- `since` inherited from the declaring class when a member omits it.
- Both `<method>` and `<field>` rows stored in `android.api.sqlite` and
  `jars.db` so a later field-check goal reuses the cache without a
  schema migration.
- The platform archive for `targetSdkVersion` as the table source.
- `minSdkVersion` from `apktool.yml` as the threshold, with incremental
  staleness invalidating `.j` outputs when `apktool.yml` changes.

Out (later goals; say so in `docs/not-covered.md`):

- Field reads (`getstatic`/`getfield`) in the compiler.
- Class-only references where no member on the newer class is invoked.
- Extension SDK levels (`<sdk id=... shortname=...-ext>`).
- `deprecated` and `removed` diagnostics.
- A separate `compileSdkVersion`.
- `py2apk check` / `ty` integration.

## Acceptance Checks

- `py2apk compile` on a project with `minSdkVersion: 21` and a call to
  an API 26 method fails with one located error and exit code `1`.
- The same source compiles when `minSdkVersion` is `26`, and lowering
  `minSdkVersion` back to `21` in `apktool.yml` triggers recompilation
  and fails even when `.py` mtimes are unchanged.
- A member absent from the API table never fails, and a project with no
  `apktool.yml` compiles exactly as before.
- `just check`, `just lint`, `ripwire . --doc-drift --legend=compact`
  (`drift="0"`), and `python3 -m unittest discover -s tests -v` pass.
