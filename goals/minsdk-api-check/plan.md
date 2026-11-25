# Plan: Reject APIs newer than minSdkVersion

## Execution Steps

### Phase 1 — API table in the index

- [ ] Add `api_levels (jar_id, owner, member, since)` and
      `CREATE INDEX idx_api_levels_owner_member ON api_levels (owner,
      member)` to `PROJECT_SCHEMA` (`Py2APK/jar_index.py:48`). `member`
      is the `api-versions.xml` `name` attribute (`name + descriptor`
      for methods, field name for fields; the `(` in method signatures
      prevents collisions). Leave `MEMBER_SCHEMA` (`jar_index.py:21`)
      alone so dependency jars do not grow an empty table.
- [ ] Add `api_database_path(jar_path: Path) -> Path` and
      `write_api_table(rows: Sequence[tuple[str, str, int]],
      database_path: Path) -> None` to `Py2APK/jar_index.py` next to
      `index_jar` (`jar_index.py:124`). Keep `jar_index.py` pure SQLite
      (no XML import, avoiding a circular import with
      `cli_fetch_platform.py`). Write `android.api.sqlite` atomically
      through a `.part` file then `replace`, matching
      `write_project_database` (`jar_index.py:169`). Do not reuse
      `android.sqlite`, which `index_jar` (`jar_index.py:124`) deletes
      and rewrites when the jar changes.
- [ ] Add `api_levels_from_xml(payload: bytes) -> list[tuple[str, str,
      int]]` to `Py2APK/cli_fetch_platform.py`. Parse with
      `defusedxml.ElementTree`, strip namespaces like
      `_repository_document` (`cli_fetch_platform.py:137`), read each
      `<class name=... since=...>`, and emit `(owner, member, since)`
      for every `<method>` and `<field>` child using the member's own
      `since` when present, else the class `since`. Skip entries with
      neither. Let the XML parser unescape `&lt;init>`.
- [ ] Update `fetch_platform` (`cli_fetch_platform.py:46`) to populate
      `android.api.sqlite` beside `android.jar`: when `android.jar`
      exists but `android.api.sqlite` is missing, first check for a
      cached `platform-*.zip` in `destination` to extract
      `android-<api>/data/api-versions.xml` offline; only query
      `_repository_document` and download if no local archive exists or
      `android.jar` is missing. If an archive lacks
      `data/api-versions.xml`, skip writing the API table instead of
      raising `KeyError`.
- [ ] In `write_project_database` (`jar_index.py:169`), when
      `kind == "platform"`, merge rows from `android.api.sqlite` via a
      new helper beside `_copy_members` (`jar_index.py:394`). A missing
      `android.api.sqlite` writes no rows and is not an error so old
      caches keep working.
- [ ] Add `JarCatalog.api_since(owner: str, member: str) -> int | None`
      in `Py2APK/jar_index.py`, using a table-exists probe like
      `_has_param_names` (`jar_index.py:170`). Query
      `SELECT MIN(since) FROM api_levels WHERE owner = ? AND member = ?`
      and return `None` when the table or row is absent.

### Phase 2 — Reject newer invokes in the compiler

- [ ] Add `min_api: int | None = None` to `_CompilerBase.__init__`
      (`Py2APK/compiler_j/base.py:92`) and store it as `self.min_api`.
- [ ] Add `min_api: int | None = None` to `compile_py2j` (`Py2APK/compiler_j/compiler.py:37`)
      and forward it to `_Compiler` (`compiler.py:22`). Default `None`
      disables the check, preserving the shared stdlib cache and every
      existing test.
- [ ] Add `_reject_newer_api(node: ast.Call, name: str,
      signature: _MethodSignature) -> None` to
      `Py2APK/compiler_j/calls.py` and call it from `_find_method`
      (`calls.py:728`) immediately after a match is found. Return early
      when `self.min_api is None`, `call is None`, `signature.owner` is
      empty, or `signature.owner` is a project-local class
      (`signature.owner == self.class_internal` or in `self.project`).
      Look up `self.catalog.api_since(signature.owner, name +
      signature.descriptor)` and raise `self.error(call, ...)` when
      `since > self.min_api`. This covers instance calls
      (`calls.py:552`), static calls (`calls.py:505`), imported static
      functions (`_static_binding`, `calls.py:370`), constructors
      (`_bound_constructor`, `calls.py:145`), and `super` calls
      (`calls.py:526`).
- [ ] In `_signature_for_call` (`calls.py:680`), pass `call=None` to
      `_find_method` so list-classification preview probes do not raise
      speculative API errors before actual call emission.
- [ ] Format the error as
      `f"{signature.owner.replace('/', '.')}.{name} requires API {since}, minSdkVersion is {self.min_api}"`
      through `self.error(call, ...)`, preserving source path and line.
- [ ] Leave `call=None` lookups unchecked — `_require_method` (`base.py:218`)
      and `_static_invoke` (`base.py:209`, used for builtins): those
      target compiler-internal helpers and API-1 `java.lang` methods.
- [ ] Leave `getstatic`/`getfield` (`_attribute`, `calls.py:484`)
      unchecked in this goal; document that in `docs/not-covered.md`.
- [ ] In `Py2APK/cli_compile.py`, add `_min_api(root: Path) -> int |
      None`: return `None` when `apktool.yml` is absent
      (`REQUIRED_PATHS` at `cli_compile.py:24` is `AndroidManifest.xml`
      and `src` only), else `min_sdk(root)`. Read it once in
      `compile_project` (`cli_compile.py:111`), turn a `RuntimeError`
      or `TypeError` from a malformed config into a stderr message and
      `return 1`, and pass `min_api` through `_compile_source`
      (`cli_compile.py:187`) to `compile_py2j` (`cli_compile.py:200`).
- [ ] Update `_modified_sources` (`cli_compile.py:175`) so `stamp` also
      includes `(project_root / "apktool.yml").stat().st_mtime_ns` when
      `apktool.yml` exists; lowering `minSdkVersion` then triggers
      recompilation instead of silently keeping stale `.j` files.
- [ ] Keep `ensure_stdlib_jasmin` (`Py2APK/stdlib_cache.py:47`) at
      `min_api=None` because that cache is shared across all projects.

### Phase 3 — Verification & evidence

- [ ] Extend `_compile_java` (`tests/test_cli_compile_calls.py:26`)
      with optional `api_levels` rows and `min_api`, creating the
      table and inserting rows in the temporary `jars.db`.
- [ ] Add tests in `tests/test_cli_compile_calls.py`: a method with
      `since=26` is rejected at `min_api=21` and accepted at
      `min_api=26`; an unlisted member is accepted at `min_api=1`; no
      check runs when `min_api=None`; a constructor, a static method,
      and a `super()` call are all checked; a project-local method is
      never checked; the message names the dotted owner, member name,
      required API level, and `minSdkVersion`.
- [ ] Add tests in `tests/test_jars.py` beside `PlatformSelectionTests`
      (`test_jars.py:316`): `api_levels_from_xml` on a small inline XML
      fixture covers an own `since`, an inherited class `since`, a
      `<field>` entry, `&lt;init>`, a class-only entry, and a
      removed/deprecated member; `JarCatalog.api_since` returns `None`
      when the table or row is absent; and `fetch_platform` rebuilds
      `android.api.sqlite` offline from a cached platform zip without
      calling `_repository_document`.
- [ ] Add a `write_project_database` test beside
      `test_platform_jar_outside_the_project_keeps_its_cache_path`
      (`test_jars.py:762`) proving a `platform` row merges the API
      table and a `direct` row does not.
- [ ] Add `tests/test_cli_compile_command.py` cases: a project with no
      `apktool.yml` compiles with the check off; a project whose
      `apktool.yml` has `minSdkVersion: 21` reports a rejected invoke
      as `error: ...` on stderr with exit `1`; and lowering
      `minSdkVersion` in `apktool.yml` after a passing compile triggers
      recompilation and fails.
- [ ] Update `ROADMAP.md`, `docs/interals.md`, `docs/covered.md`, and
      `docs/not-covered.md` to state what is checked and that fields,
      class-only references, and extension SDK levels are not.
- [ ] Run and leave green:
      `python3 -m unittest discover -s tests -v`, `just check`,
      `just lint`, and `ripwire . --doc-drift --legend=compact` with
      `drift="0"`.
- [ ] Write `goals/minsdk-api-check/evidence.md` with the commands,
      their results, a `## Memory Writes` block, and a standalone
      `Verdict: PASS` line.

### Phase 4 — Attribution & Registry Closure

- [ ] **Phase 4 — Attribution & Registry Closure**: (1) check all completed tasks in `goals/minsdk-api-check/plan.md`; (2) commit with `(goals/minsdk-api-check/goal.md)` or `[goal: minsdk-api-check]` using a `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` subject (docs/test commits are ignored); (3) certify via `sila goals certify minsdk-api-check --evidence=goals/minsdk-api-check/evidence.md` (with `Verdict: PASS`) or `sila goals audit minsdk-api-check`; (4) reconcile via `sila goals` and commit the updated `goals/README.md` and certification receipts; verify `sila goals --json` reports `unverified_count: 0` for delivered goals.
