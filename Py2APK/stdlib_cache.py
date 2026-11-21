"""Cache Jasmin for the packaged fallback. Dex is a later, per-API step."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from Py2APK.cli_fetch_platform import cache_root
from Py2APK.compiler_j import compile_py2j, index_project_types
from Py2APK.jar_index import MEMBER_SCHEMA, JarCatalog
from Py2APK.module_resolve import stdlib_root


def cache_directory() -> Path:
    return cache_root() / "stdlib_j"


def cached_jasmin(source: Path, cache: Path | None = None) -> Path:
    """Return the cached ``.j`` that mirrors ``source`` under the fallback tree."""
    root = stdlib_root().resolve()
    relative = source.resolve().relative_to(root)
    return (cache_directory() if cache is None else cache) / relative.with_suffix(".j")


def ensure_stdlib_jasmin(
    cache: Path | None = None,
    source_root: Path | None = None,
) -> Path:
    """Rebuild the Jasmin cache when the fallback tree is newer."""
    sources = stdlib_root() if source_root is None else source_root
    destination = cache_directory() if cache is None else cache
    if not _stale(sources, destination):
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in sources.rglob("*.py") if path.is_file())
    database = destination / "jars.db"
    if database.exists():
        database.unlink()
    _runtime_catalog(database)
    project = index_project_types(destination / "absent", files)
    catalog = JarCatalog(database)
    try:
        for source in files:
            output = destination / source.relative_to(sources).with_suffix(".j")
            output.parent.mkdir(parents=True, exist_ok=True)
            written = compile_py2j(source, output, catalog, project)
            _ = output.with_suffix(".j.list").write_text(
                "\n".join(path.name for path in written) + "\n",
                encoding="utf-8",
            )
    finally:
        catalog.close()
    os.utime(destination, None)
    return destination


def _stale(source_root: Path, cache: Path) -> bool:
    if not cache.is_dir():
        return True
    cache_stamp = cache.stat().st_mtime_ns
    if source_root.stat().st_mtime_ns > cache_stamp:
        return True
    return any(
        path.stat().st_mtime_ns > cache_stamp
        for path in source_root.rglob("*.py")
        if path.is_file()
    )


def _runtime_catalog(database: Path) -> None:
    string = "Ljava/lang/String;"
    builder = "Ljava/lang/StringBuilder;"
    connection = sqlite3.connect(database)
    connection.executescript(MEMBER_SCHEMA)
    methods = (
        ("java/lang/String", "valueOf", "I", string, 1, "invokestatic"),
        ("java/lang/String", "format", string + "[Ljava/lang/Object;", string, 1, "invokestatic"),
        ("java/lang/String", "length", "", "I", 0, "invokevirtual"),
        ("java/lang/String", "indexOf", string, "I", 0, "invokevirtual"),
        ("java/lang/String", "replace", string + string, string, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "<init>", "", "V", 0, "invokespecial"),
        ("java/lang/StringBuilder", "append", string, builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "I", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "toString", "", string, 0, "invokevirtual"),
        ("java/lang/Integer", "valueOf", "I", "Ljava/lang/Integer;", 1, "invokestatic"),
        ("java/lang/Math", "abs", "I", "I", 1, "invokestatic"),
        ("java/util/ArrayList", "<init>", "", "V", 0, "invokespecial"),
        ("java/util/ArrayList", "add", "Ljava/lang/Object;", "Z", 0, "invokevirtual"),
        ("java/util/ArrayList", "size", "", "I", 0, "invokevirtual"),
        ("java/lang/AutoCloseable", "close", "", "V", 0, "invokeinterface"),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        methods,
    )
    connection.commit()
    connection.close()
