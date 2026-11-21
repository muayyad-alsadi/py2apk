"""Resolve a Python import across the project, the fallback, and the host."""

from __future__ import annotations

import sysconfig
from pathlib import Path


def stdlib_root() -> Path:
    return Path(__file__).resolve().parent / "stdlib"


def host_stdlib() -> Path | None:
    found = sysconfig.get_path("stdlib")
    if not found:
        return None
    path = Path(found)
    return path if path.is_dir() else None


def resolve_module(
    name: str,
    project_root: Path,
    *,
    fallback: Path | None = None,
    host: Path | None = None,
) -> Path | None:
    """Return the first ``.py`` for ``name``.

    The order is the project ``src`` tree, then the packaged fallback,
    then a host CPython ``.py``. A missing host file, including a C
    extension such as ``itertools``, is a miss.
    """
    relative = Path(*name.split("."))
    roots = (
        project_root / "src",
        stdlib_root() if fallback is None else fallback,
        host_stdlib() if host is None else host,
    )
    for root in roots:
        if root is None:
            continue
        module = root / relative.with_suffix(".py")
        package = root / relative / "__init__.py"
        if module.is_file():
            return module
        if package.is_file():
            return package
    return None


def describe_search(name: str, project_root: Path) -> str:
    host = host_stdlib()
    host_text = str(host) if host is not None else "the host stdlib"
    return f"{project_root / 'src' / name}.py, {stdlib_root() / name}.py, and {host_text}"


def module_internal(source: Path) -> str:
    """Internal name of a module file, relative to src, the fallback, or the host."""
    source = source.resolve()
    for root in (stdlib_root(), host_stdlib()):
        if root is not None and _is_under(source, root.resolve()):
            relative = source.relative_to(root.resolve())
            if relative.name == "__init__.py":
                return relative.parent.as_posix()
            return relative.with_suffix("").as_posix()
    return ""


def _is_under(path: Path, root: Path) -> bool:
    try:
        _ = path.relative_to(root)
    except ValueError:
        return False
    return True
