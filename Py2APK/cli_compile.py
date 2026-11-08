# pyright: reportMissingModuleSource=false

"""Incremental source discovery for the Py2APK compiler pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.compiler_j import JasminCompilerError, compile_py2j, index_project_types
from Py2APK.jar_index import JarCatalog


REQUIRED_PATHS = ("AndroidManifest.xml", "src", "target")


def _display_path(path: Path, project_root: Path) -> str:
    relative = path.relative_to(project_root).as_posix()
    return f"./{relative}"


def _jasmin_path(source: Path, source_root: Path, jasmin_root: Path) -> Path:
    relative = source.relative_to(source_root)
    return jasmin_root / relative.with_suffix(".j")


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

    manifest, source_root, target_root = required
    invalid: list[Path] = []
    if not manifest.is_file():
        invalid.append(manifest)
    if not source_root.is_dir():
        invalid.append(source_root)
    if not target_root.is_dir():
        invalid.append(target_root)
    if invalid:
        for path in invalid:
            print(
                f"error: required path has the wrong type: {_display_path(path, root)}",
                file=sys.stderr,
            )
        return 1

    jasmin_root = target_root / "j"
    jasmin_root.mkdir(parents=True, exist_ok=True)

    sources = sorted(source_root.rglob("*.py"))
    if sources and not (root / "jars.db").is_file():
        print("error: jars.db is missing. run: py2apk fetch_jars", file=sys.stderr)
        return 1
    catalog = JarCatalog(root / "jars.db") if (root / "jars.db").is_file() else None
    project_types = index_project_types(source_root) if sources else {}
    modified: list[Path] = []
    for source in tqdm(sources, desc="Scanning sources", unit="source"):
        output = _jasmin_path(source, source_root, jasmin_root)
        if not output.is_file() or source.stat().st_mtime_ns > output.stat().st_mtime_ns:
            modified.append(source)
    compiled = 0
    with tqdm(modified, desc="compiling ...") as progress:
        try:
            for source in progress:
                progress.set_description(_display_path(source, root))
                output = _jasmin_path(source, source_root, jasmin_root)
                if catalog is None:
                    raise JasminCompilerError("jars.db is missing")
                compile_py2j(source, output, catalog, project_types)
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
