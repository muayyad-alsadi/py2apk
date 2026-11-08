# pyright: reportMissingModuleSource=false

"""Build an initialized Py2APK project into a signed APK."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from Py2APK.cli_compile import compile_project
from Py2APK.cli_fetch_jars import fetch_jars
from Py2APK.cli_setup import KEY_ALIAS, asset_directories, ensure_keystore
from Py2APK.jar_index import dex_jars, library_jars


ROOT_BUILD_FILES = ("AndroidManifest.xml", "apktool.yml")


@dataclass(frozen=True)
class _Toolchain:
    apktool: Path
    jasmin: Path
    r8: Path
    signer: Path


def _jar_directories(project_root: Path) -> list[Path]:
    directories: list[Path] = []
    configured = os.environ.get("PY2APK_JARS")
    if configured:
        directories.append(Path(configured).expanduser())
    directories.append(project_root / "jars")
    directories.extend(assets / "jars" for assets in asset_directories())

    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return unique


def _toolchain(project_root: Path) -> tuple[_Toolchain | None, Path]:
    directories = _jar_directories(project_root)
    fallback = next((path for path in directories if path.is_dir()), directories[0])
    for directory in directories:
        if not directory.is_dir():
            continue
        found = _find_toolchain(directory)
        if found is not None:
            return found, directory
    return None, fallback


def _single_jar(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    return None


def _find_toolchain(jars_dir: Path) -> _Toolchain | None:
    if not jars_dir.is_dir():
        return None
    apktool = _single_jar(jars_dir, "apktool_*.jar")
    jasmin = _single_jar(jars_dir, "jasmin-*-jar-with-dependencies.jar")
    r8 = _single_jar(jars_dir, "r8-*.jar")
    signer = _single_jar(jars_dir, "uber-apk-signer-*.jar")
    if apktool and jasmin and r8 and signer:
        return _Toolchain(apktool.resolve(), jasmin.resolve(), r8.resolve(), signer.resolve())
    return None


def _sync_build_inputs(project_root: Path) -> None:
    target_root = project_root / "target"
    for filename in ROOT_BUILD_FILES:
        source = project_root / filename
        target = target_root / filename
        if source.is_file() and (
            not target.is_file() or source.stat().st_mtime_ns > target.stat().st_mtime_ns
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(source, target)


def _apk_output(target_root: Path) -> Path:
    config_path = target_root / "apktool.yml"
    try:
        config = cast(object, yaml.safe_load(config_path.read_text()))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"could not read {config_path}: {error}") from error

    if not isinstance(config, dict):
        raise ValueError(f"invalid apktool configuration: {config_path}")
    values = cast(dict[str, object], config)
    filename = values.get("apkFileName")
    if not isinstance(filename, str) or not filename:
        raise ValueError(f"apkFileName is missing from {config_path}")
    return target_root / "dist" / filename


def _run_command(
    command: Sequence[str],
    project_root: Path,
    visible: Sequence[str] | None = None,
) -> int:
    shown = command if visible is None else visible
    print(f"+ {' '.join(shown)}")
    try:
        completed = subprocess.run(command, cwd=project_root, check=False)
    except OSError as error:
        print(f"error: could not run {command[0]!r}: {error}", file=sys.stderr)
        return 1
    if completed.returncode != 0:
        message = (
            f"error: command failed with exit code {completed.returncode}: "
            f"{' '.join(command)}"
        )
        print(message, file=sys.stderr)
    return completed.returncode


def build_project(project_root: Path | None = None) -> int:
    root = (Path.cwd() if project_root is None else project_root).resolve()

    if not (root / "jars.db").is_file() and fetch_jars(root) != 0:
        return 1

    if compile_project(root) != 0:
        return 1

    _sync_build_inputs(root)
    target_root = root / "target"
    try:
        apk = _apk_output(target_root)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    toolchain, jars_dir = _toolchain(root)
    if toolchain is None:
        print(f"error: required JARs are missing from {jars_dir}", file=sys.stderr)
        print(f"run: py2apk setup -d {jars_dir}", file=sys.stderr)
        return 1

    jasmin_files = sorted((target_root / "j").rglob("*.j"))
    if not jasmin_files:
        print("error: compile produced no Jasmin files", file=sys.stderr)
        return 1

    classes_root = target_root / "classes"
    dex_root = target_root / "dex"
    shutil.rmtree(classes_root, ignore_errors=True)
    shutil.rmtree(dex_root, ignore_errors=True)
    classes_root.mkdir(parents=True)
    dex_root.mkdir(parents=True)

    jasmin_command = [
        "java",
        "-jar",
        str(toolchain.jasmin),
        "-d",
        str(classes_root),
        *(str(path) for path in jasmin_files),
    ]
    if _run_command(jasmin_command, root) != 0:
        return 1

    class_files = sorted(classes_root.rglob("*.class"))
    if not class_files:
        print("error: Jasmin produced no class files", file=sys.stderr)
        return 1

    d8_command = [
        "java",
        "-cp",
        str(toolchain.r8),
        "com.android.tools.r8.D8",
        "--output",
        str(dex_root),
    ]
    for library in library_jars(root / "jars.db"):
        d8_command.extend(("--lib", str(library)))
    d8_command.extend(str(path) for path in class_files)
    d8_command.extend(str(path) for path in dex_jars(root / "jars.db"))
    if _run_command(d8_command, root) != 0:
        return 1

    dex_file = dex_root / "classes.dex"
    if not dex_file.is_file():
        print("error: D8 produced no classes.dex", file=sys.stderr)
        return 1
    _ = shutil.copy2(dex_file, target_root / "classes.dex")

    apktool_command = [
        "java",
        "-jar",
        str(toolchain.apktool),
        "b",
        str(target_root),
    ]
    if _run_command(apktool_command, root) != 0:
        return 1

    if not apk.is_file():
        print(f"error: apktool produced no APK at {apk}", file=sys.stderr)
        return 1
    try:
        signed = _sign_apk(toolchain, apk, root)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if signed is None:
        return 1
    print(f"built: {apk.relative_to(root)}")
    print(f"signed: {signed.relative_to(root)}")
    return 0


def _sign_apk(toolchain: _Toolchain, apk: Path, project_root: Path) -> Path | None:
    """Sign ``apk`` with the setup keystore and return ``name.signed.apk``."""
    keystore = ensure_keystore()
    password = keystore.with_name("default.keystore.pass").read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError(f"keystore password file is empty: {keystore.with_name('default.keystore.pass')}")
    out_dir = apk.parent.parent / "sign"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    command = [
        "java",
        "-jar",
        str(toolchain.signer),
        "-a",
        str(apk),
        "--ks",
        str(keystore),
        "--ksAlias",
        KEY_ALIAS,
        "--ksPass",
        password,
        "--ksKeyPass",
        password,
        "--out",
        str(out_dir),
        "--allowResign",
    ]
    visible: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            visible.append("***")
            skip_next = False
            continue
        visible.append(part)
        if part in ("--ksPass", "--ksKeyPass"):
            skip_next = True
    if _run_command(command, project_root, visible) != 0:
        return None
    produced = sorted(out_dir.glob("*.apk"))
    if len(produced) != 1:
        print(f"error: uber-apk-signer produced no single APK in {out_dir}", file=sys.stderr)
        return None
    signed = apk.with_name(f"{apk.stem}.signed.apk")
    signed.unlink(missing_ok=True)
    _ = shutil.move(produced[0], signed)
    return signed
