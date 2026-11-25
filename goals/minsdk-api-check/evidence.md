# Evidence: Reject APIs newer than minSdkVersion

Implementation commit: `547b62b` (`feat: reject APIs newer than minSdkVersion`),
attributed with `[goal: minsdk-api-check]`.

Re-certified against HEAD after `456553b` (`hashset-literals`) changed the
shared docs `ROADMAP.md`, `docs/covered.md`, and `docs/not-covered.md`. Those
edits do not change this goal's behaviour; the checks below were re-run on HEAD.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 98 tests, OK (on HEAD) |
| `sila goals audit minsdk-api-check` | passed, CCDSI 0.83, 6/6 probes |
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `ripwire . --doc-drift --legend=compact` | `drift="0"`, `clean="63"` of 63 docs |
| `sila facts verify` | exit 0; 15/15 blocks verified; 3 pre-existing missing references (`data/api-versions.xml`, `Py2APK/compiler_j.py`, `tests/test_cli_compile.py`), none introduced by this change |
| `sila verify` | exit 0; newly stale 0 on re-run |
| `sila gate` | exit 0; all checks pass, including charter-sentry, rule-of-five, memory-write |

## Acceptance checks

- `minSdkVersion: 21` and a call to an API 26 method fails with one
  located error and exit `1`: `test_rejects_an_invoke_newer_than_min_sdk`
  in `tests/test_cli_compile_command.py`.
- `minSdkVersion: 26` compiles; lowering it to `21` recompiles and fails
  even with unchanged `.py` mtimes: `test_lowering_min_sdk_recompiles_and_fails`.
- A member absent from the table never fails, and a project without
  `apktool.yml` compiles with the check off: `test_project_without_apktool_yml_skips_the_check`
  and the `MinApiTests` class in `tests/test_cli_compile_calls.py`.

## Deviations from plan

- `_reject_newer_api` takes `(compiler, node, name, signature)`, not
  `(node, name, signature)`, matching the module's `compiler`-first helpers.
- `_min_api` also catches `ValueError`, so a non-integer `minSdkVersion`
  reports an error instead of a traceback.
- `make_project` in `tests/test_cli_build.py` now writes
  `sdkInfo.minSdkVersion`, because the compile step requires it.

## Limitation

Verification uses a mocked `jars.db` and a synthetic `api-versions.xml`
fixture. No run against a real platform archive or real `android.jar`
was performed, so the end-to-end path against Google's data is unproven.

## Memory Writes

```json
{"schema":"sila/memory-writes/v1","goal_slug":"minsdk-api-check","prime":{"ran":true},"recall":[{"query":"minSdkVersion API level since check","matches":0}],"memos":[{"id":"fact_15303888"}],"lessons":[{"id":"fact_73785691"}],"cases":[{"id":"fact_406ceba1"}],"facts_verify":{"exit_code":0},"handoff":{"id":"hoff_db6d94cb6e8a"}}
```

Verdict: PASS
