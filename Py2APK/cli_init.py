"""Initialize a Py2APK project from a packaged template."""

from __future__ import annotations

import shutil
import site
import sys
import sysconfig
from pathlib import Path


TEMPLATE_SENTINEL = "_template.keep"
ROOT_BUILD_FILES = ("AndroidManifest.xml", "apktool.yml")


def _template_root() -> Path:
    package_parent = Path(__file__).resolve().parent.parent
    candidates = [
        package_parent / "templates",
        package_parent / "share/py2apk/templates",
        Path(sysconfig.get_path("data")) / "share/py2apk/templates",
    ]
    if site.USER_BASE:
        candidates.append(Path(site.USER_BASE) / "share/py2apk/templates")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    raise RuntimeError("the installed package does not contain template assets")


def _apply_placeholders(root: Path, package: str, name: str) -> None:
    package_path = Path(*package.split("."))
    placeholders = sorted(
        (path for path in root.rglob("__PACKAGE__") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in placeholders:
        target = directory.parent / package_path
        target.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(target)

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace("__PACKAGE__", package).replace("__NAME__", name)
        if updated != text:
            _ = path.write_text(updated, encoding="utf-8")


def _copy_template(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()

    try:
        for child in source.iterdir():
            if child.name == TEMPLATE_SENTINEL:
                continue

            target = destination / child.name
            if child.is_dir():
                _ = shutil.copytree(
                    child,
                    target,
                    ignore=shutil.ignore_patterns(
                        TEMPLATE_SENTINEL,
                        "__pycache__",
                        "*.py[co]",
                    ),
                )
            else:
                _ = shutil.copy2(child, target)

        for filename in ROOT_BUILD_FILES:
            target_file = destination / "target" / filename
            if target_file.is_file():
                _ = shutil.copy2(target_file, destination / filename)
    except Exception:
        shutil.rmtree(destination)
        raise


def init_project(
    template_name: str,
    icon: str | None,
    package: str,
    name: str,
    destination: Path,
) -> int:
    destination = destination.expanduser()
    if destination.exists():
        print(f"error: destination already exists: {destination}", file=sys.stderr)
        return 1

    if (
        not template_name
        or template_name in {".", ".."}
        or Path(template_name).name != template_name
        or "\\" in template_name
    ):
        print(f"error: invalid template name: {template_name!r}", file=sys.stderr)
        return 1

    try:
        template_root = _template_root()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    source = template_root / template_name
    if not source.is_dir():
        available = ", ".join(
            sorted(path.name for path in template_root.iterdir() if path.is_dir())
        )
        print(
            f"error: unknown template {template_name!r}; available: {available}",
            file=sys.stderr,
        )
        return 1

    try:
        _copy_template(source, destination)
        _apply_placeholders(destination, package, name)
    except OSError as error:
        print(f"error: could not create {destination}: {error}", file=sys.stderr)
        return 1

    print(f"initialized: {destination.resolve()}")
    print(f"  template: {template_name}")
    if icon:
        print(f"  icon: {icon}")
    print(f"  package: {package}")
    print(f"  name: {name}")
    return 0
