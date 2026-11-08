# Py2APK - A cli tool to create Android Apps APK from python source tree

I hate heavy bloated IDEs, I hate Android Studio even more.

This is a cli tool that let you write your app in python-like syntax
Then build your apk directly from that code.

```sh
py2apk setup
py2apk init --template weather --pkg com.example.myweather --name "My First Weather App" myweather
py2apk fetch_jars
py2apk compile
py2apk build
```

* `py2apk setup` - set up the stable toolchain JAR files.
* `py2apk init` - create a new project by copying a packaged template.
  `--icon` is optional. `__PACKAGE__` and `__NAME__` in a template are replaced
  with `--pkg` and `--name`.
* `py2apk fetch_jars` - read `-r` (default `deps.txt`) and download those Maven
  jars plus their runtime transitives. Google Maven and Central are always
  searched. A `deps.txt` line `--extra-index-url URL` adds another repository
  before those two, the same way `requirements.txt` adds an index. `-R URL`
  does that from the command line. The `chart` template uses JitPack that way
  for MPAndroidChart. An `.aar` dependency contributes its `classes.jar`. The
  command also downloads the SDK platform stub for `targetSdkVersion`.
* `py2apk fetch_platform` - download only that platform stub.
* `py2apk compile` - find missing or stale Jasmin outputs for Python-like sources.
* `py2apk build` - compile, assemble, and sign the current project.

`fetch_jars` caches Maven files under `~/.cache/py2apk/maven/` and the platform
jar under `~/.cache/py2apk/platforms/<api>/`. Each cached jar gets a sibling
SQLite file of its public and protected members, in the same directory as the
Maven `.pom` when the artifact has one. That scan is reused until the jar
changes. The project `jars/` directory receives dependency symlinks only.
The platform stub stays in the cache. One merged `jars.db` sits at the project
root. `jars.kind` is
`direct`, `transitive`, or `platform`. Compile reads every kind. D8 dexes
`direct` and `transitive` jars. The platform stub is passed to D8 with
`--lib` so type lookup succeeds, and it is not written into the dex. The
toolchain jars from `setup` are not dexed. Downloads use `aiohttp` with five concurrent
transfers when that package is already installed, and `urllib.request`
otherwise. `aiohttp` is not a required dependency.

`py2apk setup` caches Jasmin and R8 through the Maven cache at
`~/.cache/py2apk/maven/`. Apktool 3.0.3 and uber-apk-signer 1.3.0 are not
published to Maven Central, so those two are cached from their GitHub releases
under `~/.cache/py2apk/toolchain/`. The command symlinks all four into the
toolchain `jars/` directory. It also creates
`~/.local/share/py2apk/default.keystore` (alias `py2apk`, RSA 2048, PKCS12,
10000 days) and writes the password to `default.keystore.pass` beside it.
`XDG_DATA_HOME` overrides the directory. An existing keystore is left in place.

The command interface is available after installation as `py2apk`, or directly
from the repository root as `python3 -m Py2APK.cli`. `setup` retrieves
apktool, Jasmin, R8/D8, and uber-apk-signer. Without `--dst`, it finds a writable
Py2APK asset directory containing `templates/` and creates the sibling `jars/`
directory. Pass `-d JARS_DIR` or `--dst JARS_DIR` to choose it explicitly.
`compile` performs incremental source discovery and emits Jasmin for the
currently supported typed Python-like subset. `build` runs compilation, Jasmin,
D8, and apktool, then signs that APK with `~/.local/share/py2apk/default.keystore`.
The installable file is `target/dist/<name>.signed.apk`, for example
`weather.signed.apk`. If `jars.db` is missing, `build` runs `fetch_jars`
before compiling. `init` requires
a destination that does not exist and
recursively copies the selected template into it. Templates are package data, so
this also works after a `pip install --user` installation. For templates with
apktool build inputs, `init` also copies `target/AndroidManifest.xml` and
`target/apktool.yml` to the project root while preserving their modes and
modification times. Root files and `src/` are authored inputs; `target/` is
reproducible build output and may be deleted.

## How does it work

`Py2APK/cli.py` owns argument parsing and dispatch. Each non-trivial command is
implemented in `Py2APK/cli_<COMMAND>.py`, such as `cli_setup.py`,
`cli_init.py`, `cli_compile.py`, and `cli_build.py`.

The command `py2apk compile` scans `src/` for missing or stale Jasmin targets and
uses Python's built-in AST to emit `.j` files under `target/j/`. The current
compiler intentionally supports the small, fully typed language subset used by
the bundled hello-world template.

`py2apk build [DIRECTORY]` first runs that compiler, synchronizes newer authored
`AndroidManifest.xml` and `apktool.yml` files into `target/`, assembles the
Jasmin into JVM classes, converts those classes to `classes.dex` with D8, asks
apktool for the unsigned APK, and signs it with uber-apk-signer. The file to
install is `target/dist/<name>.signed.apk`. It selects the first existing JAR
directory from `$PY2APK_JARS`, the project's `jars/`, and the `jars/` sibling of
source or installed `templates/` assets. A selected directory is expected to
contain the complete toolchain, including `uber-apk-signer-*.jar`. If it does
not, the error prints the exact `py2apk setup -d JARS_DIR` recovery command. Set
`PY2APK_JARS=/path/to/jars` when the JARs live elsewhere.

See the [internal documentation](docs/interals.md) for the generated directory
layout and individual build stages.

## Signing development APKs

Apktool assembles an APK but does not sign it, and Android will not install an
unsigned APK. `py2apk build` runs [`uber-apk-signer`](https://github.com/patrickfav/uber-apk-signer)
on that APK with `~/.local/share/py2apk/default.keystore` and writes
`target/dist/<name>.signed.apk`. Install that file:

```sh
adb install -r target/dist/weather.signed.apk
```

The same signer invocation, if you need to run it by hand, is:

```sh
java -jar /path/to/uber-apk-signer-1.3.0.jar \
  -a target/dist/helloworld.apk \
  --ks ~/.local/share/py2apk/default.keystore \
  --ksAlias py2apk \
  --ksPass "$(cat ~/.local/share/py2apk/default.keystore.pass)" \
  --ksKeyPass "$(cat ~/.local/share/py2apk/default.keystore.pass)"
```

This keystore is a local release-style key, not a store you should publish
with. Do not commit the keystore or its password file.

Android Studio is not required for either building or signing Py2APK projects.
If a release process uses Google's official tools, install only the Android SDK
command-line tools and Build Tools. `zipalign` is a platform-specific native
executable, not a JAR; `apksigner` is Java-based but is normally distributed as
part of the SDK Build Tools. For lightweight development without an SDK install,
`uber-apk-signer` bundles the alignment and signing workflow behind one command.
