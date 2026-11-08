# Internal build layout

A generated project separates authored input from disposable build output:

- `AndroidManifest.xml` is the authored manifest and source of truth.
- `src/` contains authored Python-like source and is the source of truth.
- `apktool.yml` contains the authored apktool configuration.
- `target/` contains generated and copied build inputs. It may be deleted and
  reproduced at any time.

`py2apk init` copies the selected template recursively, then seeds root-level
`AndroidManifest.xml` and `apktool.yml` from the corresponding files in
`target/`. These copies use preserved file modes and timestamps so a freshly
initialized project does not immediately look stale.

The `py2apk compile` discovery pass validates `AndroidManifest.xml`, `src/`, and
`target/`; creates `target/j/`; recursively scans `src/**/*.py`; and selects a
source when its corresponding `.j` file is missing or older. `cli_compile.py`
shows each current source through a `tqdm` progress description and calls
`compiler_j.compile_py2j(source, output)`. The backend uses Python's built-in
`ast` module and assumes fully typed input. It implements the language subset
used by the bundled templates. Android and library signatures come from
`jars.db`, not from tables in the compiler.

Command implementations follow `Py2APK/cli_<COMMAND>.py`; `Py2APK/cli.py`
contains only argument parsing and dispatch. The current modules are
`cli_setup.py`, `cli_init.py`, `cli_fetch_jars.py`, `cli_fetch_platform.py`,
`cli_compile.py`, and `cli_build.py`. Compiler backends follow
`compiler_<target>.py`; the Jasmin backend is `compiler_j.py`, while a future
direct Smali backend would be `compiler_smali.py`.

`py2apk build [DIRECTORY]` is the full pipeline. When `jars.db` is missing it
calls `fetch_jars` for that project, which also fetches the platform stub.
It then invokes `compile_project()` before running external tools, copies a
newer root manifest or apktool config into `target/`, preserving metadata,
and performs these stages in order:

1. Jasmin assembles all `target/j/**/*.j` files into `target/classes/**/*.class`.
2. D8 converts those classes into `target/dex/classes.dex`. Platform rows in
   `jars.db` are `--lib` inputs. Direct and transitive rows are program inputs
   and are included in the dex.
3. The DEX file is copied to `target/classes.dex`, where apktool expects it.
4. Apktool builds the decoded project in `target/` and writes the unsigned APK
   named by `target/apktool.yml`'s `apkFileName` under `target/dist/`.
5. uber-apk-signer signs that APK with `~/.local/share/py2apk/default.keystore`
   and the build moves the result to `target/dist/<name>.signed.apk`.

`apkFileName` is still the apktool output. The installable file is the
`.signed.apk` beside it. Do not discover the result with `target/dist/*.apk`:
both the unsigned and signed files remain there.

The build recreates `target/classes/` and `target/dex/` each time so stale JVM
or DEX artifacts cannot leak into an APK. It leaves `.j`, `.class`, `.dex`, and
APK stages available for inspection when diagnosing a failure. Commands stop at
the first non-zero subprocess status.

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

The incremental compile pass compares source and target modification times.
A source is compiled when `target/j/.../Name.j` is missing or older than
`src/.../Name.py`. A newer root `AndroidManifest.xml` or `apktool.yml` is
copied into `target/` the same way. `py2apk compile` scans the whole tree; it
does not take a single input and output path.

Timestamp checks are only a rebuild optimization. Missing target files must
always be regenerated, and no unique authored state may live only under
`target/`.
