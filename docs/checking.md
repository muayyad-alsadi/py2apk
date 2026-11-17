# Type checking

`ty` is a soft dependency. `py2apk setup`, `init`, `fetch_jars`,
`compile`, and `build` run with no type checker. Install `ty` only when
you want `py2apk check` or editor diagnostics:

```sh
python3 -m pip install ty
```

Other installers are listed at
https://docs.astral.sh/ty/installation/. The current `ty` line is
0.0.x, not 1.0. This package does not depend on it.

## What `py2apk check` does

From the project directory, after `py2apk fetch_jars`:

```sh
py2apk check
```

`check` does not compile, dex, or write an APK. It writes `.pyi` stubs
for the Java packages your `src/` tree imports, then runs:

```sh
ty check -q --extra-search-path typings --project . src
```

A clean project exits 0 and prints nothing. Type errors still print.
`-qq` is not used, because that would hide them.

If `ty` is not on `PATH`, `check` exits non-zero and prints the pip
command above. `py2apk stubs` is the writer `check` calls. You do not
need to run it yourself.

## Editors

`ty` reads the generated stubs. `ruff` does not. Ruff lints and formats
the Python you wrote. It never looks up `TextView` in `typings/`. It
does lint `*.pyi` files by default, so leave `typings/` out or it will
rewrite the files `check` just wrote.

Put this in the app project's `pyproject.toml`. Both editors read it.

```toml
[tool.ty.environment]
extra-paths = ["typings"]

[tool.ruff]
extend-exclude = ["typings"]
```

Run `py2apk check` once so `typings/` exists. The editor does not
generate those files.

### VS Code and Cursor

Install two extensions. `astral-sh.ty` typechecks `.py` files when you
open them. It bundles `ty` 0.0.104 and sets `python.languageServer` to
`None`, so Pylance is not a second server. `charliermarsh.ruff` lints
and can format on save. It bundles `ruff` and picks up `extend-exclude`
from `pyproject.toml`. Install the `ruff` command only if you want it
in a terminal: `python3 -m pip install ruff`.

To keep Pylance for hover and use `ty` only as a checker:

```json
{
  "python.languageServer": "Pylance",
  "ty.disableLanguageServices": true
}
```

### Zed

Zed ships both tools. The default Python server is basedpyright, and
ruff is already on for lint and format. Enable `ty` and leave ruff on
by keeping `"..."` in `settings.json`:

```json
{
  "languages": {
    "Python": {
      "language_servers": ["ty", "!basedpyright", "..."]
    }
  }
}
```

A list that is only `["ty"]` turns ruff off. `lsp.ty.binary.path`
overrides the `ty` binary. Zed reads `extra-paths` and
`extend-exclude` from the project file.

Any other editor starts the servers with `ty server` and `ruff server`.
Editor guides: https://docs.astral.sh/ty/editors/ and
https://docs.astral.sh/ruff/editors/setup/.

## What the checker will tell you

Hover and errors for `setText` come from `ty`. A Java `String` is
`str`, and a Java `String[]` parameter is `list[str]`. `float` and
`double` are both `float`.

`py2apk compile` is what decides whether the app builds. `ty` does not
know a few things the compiler does: an expression lambda, `append` on
a list, and passing a list you changed into a method that wants a
string array.
