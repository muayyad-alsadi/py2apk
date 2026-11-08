# AGENTS.md

Operational guidance for humans and coding agents working in this repo: file
structure, commands, artifact policy, boundaries, current state.

For *why* the system is designed this way — see [README.md](README.md).

For *what comes next* — see [ROADMAP.md](ROADMAP.md).

## Repository layout

- `Py2APK/` — Python package. `cli.py` routes the command-line interface and is
  exposed as the `py2apk` console script. Command implementations use
  `cli_<COMMAND>.py`; compiler backends use `compiler_<target>.py`.
- `templates/` — non-code project assets installed under
  `share/py2apk/templates`; `setup.py` builds the recursive data-file mapping.
- `docs/` — internal implementation notes.
- `README.md` — user-facing purpose, commands, and high-level design.
- `ROADMAP.md` — planned compilation targets and build stages.
- `pyproject.toml` — package metadata and authoritative runtime dependency list.
- `requirements.txt` — convenience mirror of runtime dependencies for local setup.

## Tools and setup

This repository uses pure Python. The APK build pipeline also requires Java and
three build JARs: apktool, Jasmin, and R8 (whose CLI supplies D8). The setup
command additionally retrieves uber-apk-signer for development signing.

Use Python 3.10 or newer with the system interpreter and `pip`. Do not use a
virtual environment or `uv` in this repository. A typical local setup is:

```sh
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

The approved runtime dependencies are:

- `tqdm` for progress reporting.
- `PyYAML` (imported as `yaml`) for YAML parsing.

## Current state

`Py2APK/__init__.py` is currently empty scaffolding. `Py2APK/cli.py` implements
`setup`, `init`, `compile`, and `build` argument parsing. `setup` uses `cli_setup.py` to cache the toolchain under `~/.cache/py2apk`
and symlink it into the `jars/` sibling of a writable packaged `templates/`
directory, or into an explicit `--dst`. Jasmin and R8 come from Maven. Apktool
and uber-apk-signer come from GitHub releases because those versions are not
on Maven Central. Setup also creates `~/.local/share/py2apk/default.keystore`.
`init` uses `cli_init.py` to create a new directory from packaged template
assets. `compile` uses `cli_compile.py` for incremental discovery and
`compiler_j.py` to compile the currently supported typed Python-like subset to
Jasmin. API signatures come from the project's `jars.db`, written by
`fetch_jars` and `fetch_platform` (`cli_fetch_jars.py`, `cli_fetch_platform.py`).
`build` uses `cli_build.py` to run compile, Jasmin, D8, apktool, and
uber-apk-signer. The unsigned APK keeps the name in `target/apktool.yml`'s
`apkFileName`. The signed APK is that name with `.signed` before `.apk`.
Build output discovery must not glob `target/dist/`, because both files remain
there.

Naming conventions:

- `cli_<COMMAND>.py` owns the implementation of a non-trivial CLI subcommand;
  keep `cli.py` focused on argument parsing and dispatch.
- `compiler_<target>.py` owns source emission for one compiler target.
  `compiler_j.py` emits Jasmin; a future direct Smali backend would be
  `compiler_smali.py`.

The intended build flow is:

```text
Python AST -> Jasmin (.j) -> jasmin.jar -> .class -> d8.jar -> .dex -> apktool -> .apk
```

## Validation

Run the standard-library test suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

For packaging changes, also validate with:

```sh
python3 -m pip install --no-deps --no-build-isolation .
python3 -c "import Py2APK"
py2apk --help
```

## Artifact policy

Do not commit generated build output, downloaded JAR files, virtual
environments, Python caches, compiled classes, DEX files, or APK files. Add
appropriate ignore rules before tools begin producing these artifacts.

Run `./clean.sh` to remove the setuptools-generated `build/` and
`py2apk.egg-info/` directories and all `__pycache__/` directories.

## Do

- Keep `pyproject.toml` and `requirements.txt` synchronized for runtime
  dependencies.
- Use the standard library where practical and keep the build pipeline usable
  without Android Studio.
- Update `README.md`, `ROADMAP.md`, and internal documentation when behavior or
  architecture changes.
- Preserve the staged pipeline so intermediate output can be inspected and
  failures can be diagnosed.

## Don't

- Do not use or create virtual environments (`venv`, `.venv`, and similar).
- Do not use `uv`; use `python3 -m pip` for package operations.
- Do not add new dependencies without explicit confirmation.
- Do not claim planned CLI commands or build stages work before they are
  implemented and validated.
- Do not commit generated or downloaded build artifacts.
