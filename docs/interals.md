# Internal build layout

A generated project separates authored input from disposable build output:

- `AndroidManifest.xml` is the authored manifest and source of truth.
- `src/` contains authored Python-like source and is the source of truth.
- `apktool.yml` contains the authored apktool configuration.
- `res/`, `assets/`, `AndroidManifest.xml`, and `apktool.yml` are authored.
  Apktool reads them from the project root. It ignores `src/`, `jars/`, and
  `.py2apk/`.
- `.py2apk/` holds Jasmin, class files, and the dex scratch. It may be
  deleted. `classes.dex` is written at the project root because apktool only
  packs root files whose names end in `.dex`. Apktool writes `build/` and
  `dist/` beside it.

`py2apk init` copies the selected template recursively.

The `py2apk compile` discovery pass validates `AndroidManifest.xml` and
`src/`; creates `.py2apk/j/`; recursively scans `src/**/*.py`; and selects a
source when its corresponding `.j` file is missing or older. `cli_compile.py`
shows each current source through a `tqdm` progress description and calls
`compiler_j.compile_py2j(source, output)`. The backend uses Python's built-in
`ast` module and assumes fully typed input. It implements the language subset
used by the bundled templates. Android and library signatures come from
`jars.db`, not from tables in the compiler.

API levels live in `android.api.sqlite` beside the cached `android.jar`,
built by `fetch_platform` from `data/api-versions.xml` in the platform zip.
`write_project_database` merges that table into `jars.db` as `api_levels`
for platform rows only. `compile_project` reads `minSdkVersion` once from
`apktool.yml` and passes it to `compile_py2j`, where `_find_method` rejects
a catalog member whose `since` exceeds it. `ensure_stdlib_jasmin` compiles
the shared stdlib with the check off. Staleness includes `apktool.yml`'s
mtime, so lowering `minSdkVersion` rebuilds the `.j` outputs.

Command implementations follow `Py2APK/cli_<COMMAND>.py`; `Py2APK/cli.py`
contains only argument parsing and dispatch. The current modules are
`cli_setup.py`, `cli_init.py`, `cli_fetch_jars.py`, `cli_fetch_platform.py`,
`cli_compile.py`, `cli_build.py`, `cli_stubs.py`, and `cli_check.py`.
Compiler backends follow
`compiler_<target>.py`; the Jasmin backend is `Py2APK/compiler_j`, while a future
direct Smali backend would be `compiler_smali.py`.

`py2apk build [DIRECTORY]` is the full pipeline. It calls `fetch_jars` when
`jars.db` is missing, and also when a recorded platform jar or dependency
symlink is missing or broken. `fetch_jars` fetches the platform stub first,
and skips that download when `android.jar` is already cached. `build` calls
`fetch_platform` again before D8 if that file is still absent. It then
invokes `compile_project()` before running external tools, and performs
these stages in order:

1. Jasmin assembles all `.py2apk/j/**/*.j` files into
   `.py2apk/classes/**/*.class`.
2. D8 merges those classes with `jars/*.dex` into `.py2apk/dex/classes.dex`.
   `fetch_jars` already dexed each direct and transitive jar once, as
   `{stem}.min<api>.dex` beside the cached jar, using `--intermediate`,
   `--min-api` from `minSdkVersion`, and the platform jar as `--lib`. The
   project symlink is `jars/<stem>.dex`. Build passes those dex files and the
   app classes, with the same `--min-api`. It does not pass the dependency
   jars. Platform rows in `jars.db` stay `--lib` inputs. A missing dependency
   dex stops the build and tells you to run `py2apk fetch_jars`.
3. The DEX file is copied to `classes.dex`, where apktool expects it.
4. Apktool builds the project directory and writes the unsigned APK named
   by `apktool.yml`'s `apkFileName` under `dist/`.
5. uber-apk-signer signs that APK with `~/.local/share/py2apk/default.keystore`
   and the build moves the result to `dist/<name>.signed.apk`.

`apkFileName` is still the apktool output. The installable file is the
`.signed.apk` beside it. Do not discover the result with `dist/*.apk`:
both the unsigned and signed files remain there.

The build recreates `.py2apk/classes/` and `.py2apk/dex/` each time so stale
JVM or DEX artifacts cannot leak into an APK. It leaves `.j`, `.class`,
`.dex`, and APK stages available for inspection when diagnosing a failure.
Commands stop at the first non-zero subprocess status.

`py2apk setup` retrieves apktool, Jasmin, R8/D8, and uber-apk-signer into
`~/.cache/py2apk` and symlinks them into the toolchain directory. Jasmin and
R8 use the Maven cache. Apktool 3.0.3 and uber-apk-signer 1.3.0 are not on
Maven Central, so those two are cached from their GitHub release URLs. The
default destination is `jars/` beside the first writable asset `templates/`
directory; `--dst` overrides that path. Setup also creates
`~/.local/share/py2apk/default.keystore` when that file is absent.

Build selects the first existing JAR directory from `PY2APK_JARS`, the project,
and source/installed asset locations. That directory is treated as one complete
unit rather than combining partial toolchains from several locations. If the
three build JARs or uber-apk-signer are unavailable, the error names the
selected directory and prints the matching `py2apk setup -d ...` command.
`build` then signs with the setup keystore.

The incremental compile pass compares source and Jasmin modification times.
A source is compiled when `.py2apk/j/.../Name.j` is missing or older than
`src/.../Name.py`. `py2apk compile` scans the whole tree; it does not take
a single input and output path.

Timestamp checks are only a rebuild optimization. Missing `.py2apk` files
must always be regenerated. Authored files stay at the project root.

## Typecheck stubs

`py2apk check` calls `cli_stubs.write_stubs`, then `ty check` when `ty`
is on `PATH`. The writer reads `JarCatalog` and emits one
`typings/<package>.pyi` per Java package named by an import under
`src/`. A relative import is skipped. A name that is not a package
prefix in `classes.internal` is skipped. Inner classes (`$`) and
classes in a nested package are left out of that file. The rest of
`android.jar` is not written.

A stub is replaced when it is missing or older than `jars.db`. A newer
file is left alone. A missing index exits with a `fetch_jars` hint.
`ty` is not a project dependency. `shutil.which` finds the binary.
Without it, `check` still writes the stubs, then prints
`python3 -m pip install ty`.

Descriptors in the stub: `java.lang.String` is `str`,
`java.lang.Object` is `object`, `Z` is `bool`, `B`/`S`/`I`/`J` are
`int`, `F`/`D` are `float`, `C` is `str`, arrays are `list[...]`. A
referenced class that this run did not generate becomes `object`, so
the stub stays valid. `<init>` is `__init__`. Overloads are separate
`def` lines marked `@overload`. The superclass is named when that class
is in the same generated set. Otherwise the base is `object`.

mypy was not picked. A plugin cannot create `android.widget` before a
file is found. A launcher can patch `FindModuleCache` and
`FileSystemCache.read`, serve class shells from sqlite, and fill
signatures with `get_attribute_hook`. That patch is private, it has to
invent constructors and base classes, and the incremental cache has to
stay off. The same `TextView.setText` answers come from the stub.
`android.widget` at 3,068 methods is about 134 KB and checked in 0.09 s.
The whole platform in one imported module was 2.4 MB and 0.75 s.
