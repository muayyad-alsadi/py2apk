# ROADMAP.md

The build pipeline is Python AST to Jasmin (`.j`), then `jasmin.jar`,
`.class`, `d8.jar`, `.dex`, apktool, and uber-apk-signer. A later backend
may emit Smali and hand that straight to apktool. Facebook ReDex is a
possible optimizer for that Smali, not part of the current pipeline.

## Done

* `py2apk init`, `fetch_jars`, `fetch_platform`, `compile`, `build`, and
  `setup` run. `build` signs with `~/.local/share/py2apk/default.keystore`
  and writes `target/dist/<name>.signed.apk`. If `jars.db` is missing,
  `build` fetches before it compiles.
* Compile signatures come from the project's `jars.db`. The compiler does
  not keep hard-coded method, constructor, field, or interface tables.
* `fetch_jars` reads `deps.txt`. `--extra-index-url` adds a Maven
  repository. Google Maven and Maven Central are always searched after
  that. An `.aar` contributes its `classes.jar`.
* `fetch_platform` downloads the SDK stub for `targetSdkVersion` into
  `~/.cache/py2apk/platforms/<api>/`. That jar is a D8 `--lib` input. It
  is not dexed. Direct and transitive dependency jars are dexed.
* Member indexes are SQLite files beside the cached jars. The project
  `jars/` directory holds dependency symlinks only.

## Not done

* A Smali backend, and any ReDex pass over that Smali.
* API availability (`since`, deprecated, removed) and a packaged API
  database separate from the per-project `jars.db`. The jar index has
  descriptors and invoke opcodes, not that history.
* A distinct `compileSdkVersion`. Resolution follows the platform jar
  for `targetSdkVersion`. `minSdkVersion` does not yet reject newer APIs.
* A separate system or hidden API surface. The stub jar is the public
  SDK surface.
* The typed language subset is still the one the templates use. `try`,
  keyword arguments, and lambdas are not implemented.
