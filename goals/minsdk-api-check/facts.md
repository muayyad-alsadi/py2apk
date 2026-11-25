# Facts: Reject APIs newer than minSdkVersion

## Data source (verified)

- `fetch_platform` (`Py2APK/cli_fetch_platform.py:46`) downloads
  `platform-<api>-...zip` and keeps it in
  `~/.cache/py2apk/platforms/<api>/` beside `android.jar`.
- That archive contains `android-<api>/data/api-versions.xml`.
  Verified against the cached API 34 archive
  (`platform-34-ext7_r03.zip`): 5,302,264 bytes, 6,222 `<class>`,
  46,916 `<method>`, 30,364 `<field>` entries.
- Shape:

  ```xml
  <api version="3">
    <class name="android/widget/TextView" since="1">
      <extends name="android/view/View"/>
      <method name="&lt;init>(Landroid/content/Context;)V"/>
      <method name="append(Ljava/lang/CharSequence;)V" since="21"/>
      <field name="ACCESS_NOTIFICATION_POLICY" since="23"/>
      <field name="ACCESS_MOCK_LOCATION" removed="23"/>
  ```

- A `<method>`'s `name` attribute is the full member signature
  `name(params)result` (with `&lt;init>` for constructors). A `<field>`'s
  `name` has no `(`, so methods and fields never collide in one
  `(owner, member)` index.
- 10,086 of 46,916 methods carry their own `since`; the rest inherit
  the class `since`, which every class entry has. Values in API 34 are
  `1`..`34`.
- `java/lang/*` classes are in `api-versions.xml` too (for example
  `java/lang/String` at API 1 and `java/lang/Record` at API 34), so
  `java.*` invokes are covered alongside `android.*`.
- `deprecated` and `removed` exist, but neither is needed to reject a
  newer-than-minSdk invoke: the catalog only holds members of the
  current `android.jar`, so removed members are never candidates.

## Architectural invariants & constraints

- `jars.db` is the only compile-time metadata source (`ROADMAP.md:14`).
  The API table must be merged into it, not opened as a second file by
  the compiler.
- The index has no API history today: `MEMBER_SCHEMA` (`Py2APK/jar_index.py:21`)
  and `PROJECT_SCHEMA` (`jar_index.py:48`) store descriptors and
  opcodes only, and `JarCatalog` has no `since` accessor.
- Layering: `cli_fetch_platform.py` already imports `index_jar` from
  `Py2APK/jar_index.py` (`cli_fetch_platform.py:15`). Keep XML parsing
  in `cli_fetch_platform.py` and pass parsed `(owner, member, since)`
  rows into `jar_index.py` so there is no circular import.
- `min_sdk` (`cli_fetch_platform.py:113`) already reads
  `sdkInfo.minSdkVersion` from `apktool.yml`; `cli_build.py:225` and
  `_dex_linked_jars` (`cli_fetch_jars.py:262`) already use it for D8.
- Fail open: a member with no table row is allowed. An unlisted member,
  a project-local method, or an old cache must never become a compile
  error.
- User-written invokes funnel through `_find_method`
  (`Py2APK/compiler_j/calls.py:728`) with `call=node`:
  - instance calls (`calls.py:552`)
  - static calls (`calls.py:505`)
  - imported static functions (`_static_binding`, `calls.py:546`)
  - constructors (`_bound_constructor`, `calls.py:145`)
  - `super` calls (`calls.py:526`)
- `_require_method` (`base.py:218`) and `_static_invoke`
  (`base.py:209`, used for `str`/`int`/`float`/`abs`/`min`/`max`) call
  `_find_method` with `call=None`; those remain unchecked.
- `_signature_for_call` (`calls.py:927`) probes with preview types
  during list classification and should pass `call=None` so it never
  raises a speculative API error before real call emission.
- `ensure_stdlib_jasmin` (`Py2APK/stdlib_cache.py:47`) compiles one
  shared cache for all projects and must keep `min_api=None`.
- `REQUIRED_PATHS` (`cli_compile.py:24`) is `AndroidManifest.xml` and
  `src` only: a project without `apktool.yml` must keep compiling with
  the check off. When `apktool.yml` exists, `_modified_sources`
  (`cli_compile.py:175`) must include its mtime so lowering
  `minSdkVersion` invalidates cached `.j` files.
- `index_jar` (`jar_index.py:124`) deletes and rewrites `android.sqlite`
  when `android.jar` is newer; API rows must live in a separate
  `android.api.sqlite` beside `android.jar`.

## File & interface contracts

- `Py2APK/jar_index.py`
  - `PROJECT_SCHEMA` gains table `api_levels(jar_id, owner, member,
    since)` (all `NOT NULL`) and index `idx_api_levels_owner_member` on
    `(owner, member)`.
  - `api_database_path(jar_path: Path) -> Path` returns
    `jar_path.resolve().with_suffix(".api.sqlite")`.
  - `write_api_table(rows: Sequence[tuple[str, str, int]],
    database_path: Path) -> None` writes atomically via `.part` ->
    `replace`.
  - `write_project_database` merges API rows only for
    `kind == "platform"`; a missing `.api.sqlite` is not an error.
  - `JarCatalog.api_since(owner: str, member: str) -> int | None`
    returns `None` when the table or row is absent.
- `Py2APK/cli_fetch_platform.py`
  - `api_levels_from_xml(payload: bytes) -> list[tuple[str, str, int]]`
    yields `(owner, member, since)` for `<method>` and `<field>`,
    inheriting class `since` when a member omits its own.
  - `fetch_platform` builds `android.api.sqlite` from a cached local
    `platform-*.zip` first before hitting the network, and skips
    gracefully if an archive lacks `data/api-versions.xml`.
- `Py2APK/compiler_j/base.py`
  - `_CompilerBase.__init__(..., min_api: int | None = None)`,
    storing `self.min_api: int | None`.
- `Py2APK/compiler_j/compiler.py`
  - `compile_py2j(source, output, catalog, project, min_api=None)`
    returning `list[Path]`.
- `Py2APK/compiler_j/calls.py`
  - `_reject_newer_api(node, name, signature)` called from
    `_find_method` before returning a match.
  - Message: `<dotted owner>.<name> requires API <since>, minSdkVersion
    is <min_api>` (e.g. `android.widget.TextView.setAutoSizeTextType...`).
- `Py2APK/cli_compile.py`
  - `_min_api(root: Path) -> int | None` returns `None` without
    `apktool.yml`, else `min_sdk(root)`.
  - `compile_project` prints `error: ...` and returns `1` on config
    errors, and `_modified_sources` includes `apktool.yml` mtime.

## Scope

In: method and constructor invokes written in project source, checked
against `minSdkVersion` from `apktool.yml`, using `data/api-versions.xml`
from the platform archive for `targetSdkVersion` (storing both methods
and fields in `android.api.sqlite` for forward compatibility).

Out: field reads (`getstatic`/`getfield`), class-only references that
never invoke a newer member (e.g. passing a parameter of an API 26 type
and calling only `Object.toString()`), extension SDK levels
(`<sdk id=... shortname=...-ext>`), `deprecated`/`removed` warnings,
`compileSdkVersion` as a separate knob, and `py2apk check`
(`cli_check.py`).

## Test seams

- `tests/test_cli_compile_calls.py:26` (`_compile_java`) builds the fake
  catalog; add optional API rows and `min_api` there.
- `tests/_compile_helpers.py:171` (`_alias_catalog`) is the shared base
  schema; `tests/_compile_helpers.py:344` (`_collection_catalog`) shows
  the extra-row pattern.
- `tests/test_jars.py:316` (`PlatformSelectionTests`) is where
  `select_platform_archive` tests live; add XML parser and offline
  cache-rebuild tests beside it.
- `tests/test_jars.py:762` (`DexSelectionTests`) already calls
  `write_project_database` with a `platform` row.
- `tests/test_cli_compile_command.py` writes an empty `jars.db`, so
  `JarCatalog` must tolerate a database without the API table.

## Risks

- 5.3 MB XML: parse once per platform fetch, not per compile, and index
  `(owner, member)` in SQLite so compile stays O(log N) per call.
- Old caches have `android.jar` and `platform-*.zip` without
  `android.api.sqlite`: rebuild offline from the cached zip before
  touching the network; re-download only when the zip is gone.
- Lowering `minSdkVersion` in `apktool.yml` without editing `.py` files
  would silently skip checks unless `_modified_sources` tracks
  `apktool.yml` mtime.
- Overloads differ per descriptor; keying on `name + descriptor` avoids
  attributing the level of one overload to another.
- Extension SDKs (`<sdk id=... shortname=...>`, 88 classes with `sdks`
  in API 34) are ignored in this goal; do not claim extension support.
