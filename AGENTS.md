# AGENTS.md

Operational guidance for humans and coding agents working in this repo: file
structure, commands, artifact policy, boundaries, current state.

For *why* the system is designed this way — see [README.md](README.md).

For *what comes next* — see [ROADMAP.md](ROADMAP.md).

## Repository layout

- `Py2APK/` — Python package. `cli.py` routes the command-line interface and is
  exposed as the `py2apk` console script. Command implementations use
  `cli_<COMMAND>.py`; compiler backends use `compiler_<target>.py`.
  `Py2APK/stdlib/` holds the packaged fallback for `import` and the
  helpers the Jasmin compiler calls (`py2apk.slice_start`, `slice_stop`,
  `chr`, `range`). A fallback that uses a Python name keeps that
  Python signature. Do not invent a function Python does not have.
  A helper only the compiler calls lives in `Py2APK/stdlib/py2apk.py`.
  A fallback that mirrors a Python module lives at that module's path.
  After editing anything under that directory, touch it
  (`touch Py2APK/stdlib`). Linux updates a directory mtime when a name
  is added or removed, not when a file's bytes change. Setup and
  compile rebuild `~/.cache/py2apk/stdlib_j/` from that mtime, or from
  a newer `.py`, then touch the cache directory.
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
`Py2APK/compiler_j` to compile the currently supported typed Python-like subset to
Jasmin, including f-strings and a small set of `java.lang` aliases. API
signatures come from the project's `jars.db`, written by
`fetch_jars` and `fetch_platform` (`cli_fetch_jars.py`, `cli_fetch_platform.py`).
`build` uses `cli_build.py` to run compile, Jasmin, D8, apktool, and
uber-apk-signer. The unsigned APK keeps the name in `apktool.yml`'s
`apkFileName`. The signed APK is that name with `.signed` before `.apk`.
Build output discovery must not glob `dist/`, because both files remain
there. Apktool builds the project directory. Intermediates live in
`.py2apk/`. `classes.dex` is written at the project root.

Naming conventions:

- `cli_<COMMAND>.py` owns the implementation of a non-trivial CLI subcommand;
  keep `cli.py` focused on argument parsing and dispatch.
- `compiler_<target>.py` owns source emission for one compiler target.
  The Jasmin backend is the package `Py2APK/compiler_j`; a future direct
  Smali backend would be `compiler_smali.py`.

The intended build flow is:

```text
Python AST -> Jasmin (.j) -> jasmin.jar -> .class -> d8.jar -> .dex -> apktool -> .apk
```

## Validation

After each build, run both of these from the repository root and leave
them green:

```sh
just check
just lint
```

`just check` typechecks `Py2APK` with ty. `just lint` runs Ruff on the
repository, including `Py2APK/stdlib` and `templates`.

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

- After each build, run `just check` and `just lint` and leave both green.
- Keep `pyproject.toml` and `requirements.txt` synchronized for runtime
  dependencies.
- Use the standard library where practical and keep the build pipeline usable
  without Android Studio.
- Update `README.md`, `ROADMAP.md`, and internal documentation when behavior or
  architecture changes. Before trusting or editing those files, follow
  ripwire-fresh-eyes and run `ripwire . --doc-drift --legend=compact`.
  Correct each flagged anchor until the report shows `drift="0"`.
  That pass checks file:line refs, backticked symbols, and numeric
  anchors. Check prose claims about what compiles against `Py2APK/compiler_j`.
- Preserve the staged pipeline so intermediate output can be inspected and
  failures can be diagnosed.
- Keep the success path flat. A failing check returns early (never-nester).

## Don't

- Do not use or create virtual environments (`venv`, `.venv`, and similar).
- Do not use `uv`; use `python3 -m pip` for package operations.
- Do not add new dependencies without explicit confirmation.
- Do not claim planned CLI commands or build stages work before they are
  implemented and validated.
- Do not commit generated or downloaded build artifacts.
- Do not nest the success path under a passing `if`. Return from the
  failing guard instead.

<!-- sila:begin (managed by sila; content inside is regenerated by `sila install` / `sila agents sync`) -->
<!-- sila:meta rev=1 scope=core hash=59cb217132170414 -->
## 0. Safety Rails (NEVER Do This)
- No unprompted remote push: Local commits are autonomous; remote push requires user consent.
- No unauthorized repo creation or re-homing: NEVER run 'gh repo create' or push external specs to personal namespaces; fail closed on auth hurdles.

## 1. Working Relationship & Discipline
- Concise and direct.
- Terminal Markdown: Keep visible line and table width under 80 chars; no hard breaks inside bullets or links.
- Decision Brief & `/adoo` Receipts: Lead with 1-line verdict ("X happens because Y, confirmed by Z"), compact receipt/risk table, file links, and a 1-line next action at the end (never bury actions or questions in prose). No 1-5 scores.
- Verified Repo Runbook Primacy: Never guess commands; verify exit code 0 locally.
- Closed-Loop Goal Attribution (6 Steps) for `goals/<slug>/`:
  1. Check every completed task `- [x]` in `goals/<slug>/plan.md`.
  2. Write markdown evidence with a standalone `Verdict: PASS` line.
  3. Commit with `(goals/<slug>/goal.md)` or `[goal: <slug>]` under `feat:`, `fix:`, `refactor:`, `perf:`, or `chore:` (`docs:`/`test:` ignored).
  4. Certify via `sila goals certify <slug> --evidence=goals/<slug>/evidence.md` or `sila goals audit <slug>`.
  5. Run `sila goals` to reconcile `goals/README.md` and commit receipts.
  6. Verify `sila goals --json` reports `status: "implemented"`, `verification_status: "verified"`, `unverified_count: 0`, and clean `git status`.

## Sila Trigger Contract

Memory writes are required by the goal contract and enforced by `sila gate`.
- `sila prime` (start) → briefing + goals.
- `sila recall "<symptom>"` (stuck) → prior resolutions.
- `sila memo "<directive>" --files=...` (decision) → grounded directive.
- `sila lesson "<symptom>" --cause=... --fix=...` (solved) → grounded lesson.
- `sila case "<intent>" --approach=... --invariant=...` (pattern) → case record.
- `sila facts verify` (grounding) → file/ledger evidence.
- `sila gate` (before commit) → deterministic quality receipt.
- `sila handoff "<summary>" --open=...` (end) → handoff record.
- `sila sync` (after changes) → cross-harness continuity.
- `sila salam`, `sila guide`, `docs/reference.md` → detail.
- Prior research/specs (BEFORE grep/find): `sila search "<q>" --omni` (`omni_search`), `sila search expand <id>` (`get_message_context`), `sila doc search "<q>"` (`search_docs`).
- Activity/domains/review/skills: `sila standup last 24h`, `sila digest --since=24h`, `sila recap`; `sila guide` (`get_operational_guidebook`), `sila learn`, `sila persona`, `sila hire --for-goal=<slug>`, `sila audit` (`audit_goal`), `sila skill` (`skill_search`, `skill_load`).
- Symbols/decisions: `sila edit-check --symbol=<sym>`, `sila safe-delete --symbol=<sym>`, `sila note --symbol=<sym>`, `sila decision` (`create_decision`), `sila vision triage`.

---

## Stack-Specific Standards (Python)

- Type Hinting & Mypy: Full PEP 484 type annotations on all function signatures; enforce strict typing.
- Pure Functions: Prefer dataclasses, pydantic frozen models, and pure transformations over deep mutable class hierarchies.
- Resource Management: Always use context managers ('with' statements) for file and network handles.

---
<!-- sila:end -->
