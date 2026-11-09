"""Download the Android SDK platform stub jar for a project's target SDK."""

from __future__ import annotations

import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import yaml

from Py2APK.downloader import download_files
from Py2APK.jar_index import index_jar


REPOSITORY = "https://dl.google.com/android/repository/"
REPOSITORY_INDEX = REPOSITORY + "repository2-3.xml"


def cache_root() -> Path:
    return Path.home() / ".cache" / "py2apk"


def select_platform_archive(document: ET.Element, api: int) -> str:
    """Return the platform zip filename for ``api`` at the highest revision."""
    wanted = f"platforms;android-{api}"
    chosen: str | None = None
    revision = -1
    for package in document:
        if _local(package.tag) != "remotePackage" or package.get("path") != wanted:
            continue
        major = _first_text(package, "major")
        url = _first_text(package, "url")
        if major is None or url is None:
            continue
        package_revision = int(major)
        if package_revision >= revision:
            chosen = url
            revision = package_revision
    if chosen is None:
        raise LookupError(f"no SDK platform archive for API {api}")
    return chosen


def fetch_platform(project_root: Path, cache: Path | None = None) -> Path:
    """Download and index ``android.jar`` for the project's ``targetSdkVersion``."""
    api = target_sdk(project_root)
    root = cache if cache is not None else cache_root()
    destination = root / "platforms" / str(api)
    jar_path = destination / "android.jar"
    if not jar_path.is_file():
        archive_name = select_platform_archive(_repository_document(), api)
        archive_path = destination / archive_name
        print(f"downloading Android SDK platform {api}")
        missing = download_files([(REPOSITORY + archive_name, archive_path)])
        if missing or not archive_path.is_file():
            raise RuntimeError(f"could not download {archive_name}")
        with zipfile.ZipFile(archive_path) as archive:
            payload = archive.read(f"android-{api}/android.jar")
        partial = jar_path.with_name("android.jar.part")
        _ = partial.write_bytes(payload)
        partial.replace(jar_path)
    _ = index_jar(jar_path)
    return jar_path


def target_sdk(project_root: Path) -> int:
    loaded, config_path = _apktool_config(project_root)
    return _sdk_version(loaded, config_path, "targetSdkVersion")


def min_sdk(project_root: Path) -> int:
    """Return ``sdkInfo.minSdkVersion`` from the project apktool config."""
    loaded, config_path = _apktool_config(project_root)
    return _sdk_version(loaded, config_path, "minSdkVersion")


def _apktool_config(project_root: Path) -> tuple[dict[str, object], Path]:
    config_path = project_root / "apktool.yml"
    if not config_path.is_file():
        config_path = project_root / "target" / "apktool.yml"
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError(f"could not read {config_path}: {error}") from error
    if not isinstance(loaded, dict):
        raise RuntimeError(f"invalid apktool configuration: {config_path}")
    return loaded, config_path


def _sdk_version(loaded: dict[str, object], config_path: Path, key: str) -> int:
    sdk_info = loaded.get("sdkInfo")
    if not isinstance(sdk_info, dict) or key not in sdk_info:
        raise RuntimeError(f"{key} is missing from {config_path}")
    return int(sdk_info[key])


def _repository_document() -> ET.Element:
    request = urllib.request.Request(REPOSITORY_INDEX, headers={"User-Agent": "Py2APK"})
    with urllib.request.urlopen(request) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1]


def _first_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def main(project_root: Path) -> int:
    try:
        jar_path = fetch_platform(project_root)
    except (OSError, RuntimeError, LookupError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"platform: {jar_path}")
    return 0
