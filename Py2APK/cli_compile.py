# pyright: reportMissingModuleSource=false

"""Incremental source discovery for the Py2APK compiler pipeline."""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.cli_fetch_platform import min_sdk
from Py2APK.compiler_j import (
    JasminCompilerError,
    LocalType,
    compile_py2j,
    index_project_types,
)
from Py2APK.jar_index import JarCatalog
from Py2APK.module_resolve import resolve_module, stdlib_root
from Py2APK.stdlib_cache import cached_jasmin, ensure_stdlib_jasmin

REQUIRED_PATHS = ("AndroidManifest.xml", "src")


def _display_path(path: Path, project_root: Path) -> str:
    relative = path.relative_to(project_root).as_posix()
    return f"./{relative}"


def _jasmin_path(source: Path, source_root: Path, jasmin_root: Path) -> Path:
    relative = source.relative_to(source_root)
    return jasmin_root / relative.with_suffix(".j")


def _output_for(source: Path, project_root: Path, jasmin_root: Path) -> Path:
    from Py2APK.module_resolve import host_stdlib

    source_root = project_root / "src"
    if _is_under(source, source_root):
        return _jasmin_path(source, source_root, jasmin_root)
    for root in (stdlib_root(), host_stdlib()):
        if root is not None and _is_under(source, root):
            return _jasmin_path(source, root, jasmin_root)
    return jasmin_root / f"{source.stem}.j"


def _is_under(path: Path, root: Path) -> bool:
    try:
        _ = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _imported_modules(source: Path) -> list[str]:
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(
                alias.name for alias in node.names if alias.name not in {"typing", "collections.abc"}
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module not in {None, "__future__", "typing", "collections.abc"}
        ):
            names.append(node.module)
    return names


def _closure(sources: list[Path], project_root: Path) -> list[Path]:
    pending = list(sources)
    seen: set[Path] = set()
    ordered: list[Path] = []
    while pending:
        source = pending.pop(0).resolve()
        if source in seen or not source.is_file():
            continue
        seen.add(source)
        ordered.append(source)
        for name in _imported_modules(source):
            found = resolve_module(name, project_root)
            if found is not None:
                pending.append(found)
        if _needs_runtime(source):
            helper = stdlib_root() / "py2apk.py"
            if helper.is_file():
                pending.append(helper)
    return ordered


def _needs_runtime(source: Path) -> bool:
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"chr", "range"}:
            return True
    return False


def compile_project(project_root: Path | None = None) -> int:
    root = Path.cwd() if project_root is None else project_root
    required = [root / relative for relative in REQUIRED_PATHS]
    missing = [path for path in required if not path.exists()]
    if missing:
        for path in missing:
            print(
                f"error: required path does not exist: {_display_path(path, root)}",
                file=sys.stderr,
            )
        return 1

    manifest, source_root = required
    invalid: list[Path] = []
    if not manifest.is_file():
        invalid.append(manifest)
    if not source_root.is_dir():
        invalid.append(source_root)
    if invalid:
        for path in invalid:
            print(
                f"error: required path has the wrong type: {_display_path(path, root)}",
                file=sys.stderr,
            )
        return 1

    jasmin_root = root / ".py2apk" / "j"
    jasmin_root.mkdir(parents=True, exist_ok=True)
    try:
        ensure_stdlib_jasmin()
    except (JasminCompilerError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        min_api = _min_api(root)
    except (RuntimeError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    sources = _closure(sorted(source_root.rglob("*.py")), root)
    if sources and not (root / "jars.db").is_file():
        print("error: jars.db is missing. run: py2apk fetch_jars", file=sys.stderr)
        return 1
    catalog = JarCatalog(root / "jars.db") if (root / "jars.db").is_file() else None
    extras = [source for source in sources if not _is_under(source, source_root)]
    project_types = index_project_types(source_root, extras) if sources else {}
    modified = _modified_sources(sources, root, jasmin_root)
    compiled = 0
    with tqdm(modified, desc="compiling ...") as progress:
        try:
            for source in progress:
                progress.set_description(_display_path(source, root) if _is_under(source, root) else source.name)
                _compile_source(source, root, jasmin_root, catalog, project_types, min_api)
                compiled += 1
        except (JasminCompilerError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            print(f"scanned: {len(sources)}; modified: {len(modified)}; compiled: {compiled}")
            if catalog is not None:
                catalog.close()
            return 1

    print(
        f"scanned: {len(sources)}; modified: {len(modified)}; compiled: {compiled}"
    )
    if catalog is not None:
        catalog.close()
    return 0


def _modified_sources(sources: list[Path], project_root: Path, jasmin_root: Path) -> list[Path]:
    modified: list[Path] = []
    for source in tqdm(sources, desc="Scanning sources", unit="source"):
        output = _output_for(source, project_root, jasmin_root)
        stamp = source.stat().st_mtime_ns
        config = project_root / "apktool.yml"
        if config.is_file():
            stamp = max(stamp, config.stat().st_mtime_ns)
        if _is_under(source, stdlib_root()):
            stamp = max(stamp, cached_jasmin(source).stat().st_mtime_ns)
        if not output.is_file() or stamp > output.stat().st_mtime_ns:
            modified.append(source)
    return modified


def _min_api(root: Path) -> int | None:
    """Return ``minSdkVersion`` from ``apktool.yml``, or ``None`` when there is no config."""
    if not (root / "apktool.yml").is_file():
        return None
    return min_sdk(root)


def _compile_source(
    source: Path,
    project_root: Path,
    jasmin_root: Path,
    catalog: JarCatalog | None,
    project_types: dict[str, LocalType],
    min_api: int | None,
) -> None:
    output = _output_for(source, project_root, jasmin_root)
    if _is_under(source, stdlib_root()):
        _copy_cached(source, output)
        return
    if catalog is None:
        raise JasminCompilerError("jars.db is missing")
    compile_py2j(source, output, catalog, project_types, min_api)


def _copy_cached(source: Path, output: Path) -> None:
    cached = cached_jasmin(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = cached.with_suffix(".j.list")
    names = manifest.read_text(encoding="utf-8").splitlines() if manifest.is_file() else [cached.name]
    for name in names:
        shutil.copyfile(cached.parent / name, output.parent / name)
