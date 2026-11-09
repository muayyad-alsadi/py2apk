"""Fetch Maven dependencies and the Android platform jar for a project."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.cli_fetch_platform import cache_root, fetch_platform, min_sdk
from Py2APK.downloader import DownloadError, download_files
from Py2APK.jar_index import write_project_database
from Py2APK.maven_resolve import (
    CENTRAL,
    Artifact,
    MavenError,
    parse_dependency_file,
    repository_list,
    resolve_artifacts,
)


def fetch_jars(
    project_root: Path,
    dependency_file: Path | None = None,
    repositories: list[str] | None = None,
    cache: Path | None = None,
) -> int:
    root = project_root.resolve()
    deps = dependency_file if dependency_file is not None else root / "deps.txt"
    try:
        if deps.is_file():
            parsed = parse_dependency_file(deps.read_text(encoding="utf-8"))
            coordinates = parsed.coordinates
            declared = parsed.repositories
        else:
            coordinates = []
            declared = []
        repos = repository_list([*declared, *(repositories or [])])
        store = cache if cache is not None else cache_root()
        platform = fetch_platform(root, store)
        artifacts = resolve_artifacts(coordinates, lambda relative: _fetch(repos, relative, store)) if coordinates else []
        jars = _link_maven_jars(root, store, repos, artifacts)
        _remove_platform_symlink(root)
        if jars:
            _dex_linked_jars(root, [link for link, _kind, _coordinate in jars], platform)
        jars.append((platform, "platform", f"platform:android:{_api_label(platform)}"))
        write_project_database(root / "jars.db", jars)
    except (OSError, MavenError, DownloadError, RuntimeError, LookupError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"fetched {len(jars) - 1} jars into {root / 'jars'}")
    print(f"platform: {platform}")
    print(f"index: {root / 'jars.db'}")
    return 0


def _fetch(repos: list[str], relative: str, cache: Path) -> bytes | None:
    destination = cache / "maven" / relative
    if destination.is_file():
        return destination.read_bytes()
    for repo in repos:
        missing = download_files([(f"{repo}/{relative}", destination)])
        if not missing and destination.is_file():
            return destination.read_bytes()
    return None


def _link_maven_jars(
    project_root: Path,
    cache: Path,
    repos: list[str],
    artifacts: list[Artifact],
) -> list[tuple[Path, str, str]]:
    jars_dir = project_root / "jars"
    jars_dir.mkdir(parents=True, exist_ok=True)
    selected = [artifact for artifact in artifacts if artifact.packaging in ("jar", "aar")]
    jobs: list[tuple[str, Path]] = []
    located: list[tuple[Artifact, Path, Path]] = []
    for artifact in selected:
        jar_path = cache / "maven" / artifact.path
        archive_relative = artifact.path
        if artifact.packaging == "aar":
            archive_relative = artifact.path.removesuffix(".jar") + ".aar"
        archive_path = cache / "maven" / archive_relative
        located.append((artifact, jar_path, archive_path))
        if jar_path.is_file() and (artifact.packaging == "jar" or archive_path.is_file()):
            continue
        if not archive_path.is_file():
            jobs.append((archive_relative, archive_path))
    if jobs:
        progress = tqdm(total=len(jobs), desc="Downloading JARs", unit="jar")
        try:
            remaining = {relative: destination for relative, destination in jobs}
            for repo in repos:
                batch = [
                    (f"{repo}/{relative}", destination)
                    for relative, destination in remaining.items()
                    if not destination.is_file()
                ]
                if not batch:
                    break
                missing = set(download_files(batch, on_complete=progress.update))
                still: dict[str, Path] = {}
                for relative, destination in remaining.items():
                    url = f"{repo}/{relative}"
                    if url in missing or not destination.is_file():
                        still[relative] = destination
                remaining = still
            if remaining:
                names = ", ".join(remaining)
                raise MavenError(f"jar not found in {', '.join(repos)}: {names}")
        finally:
            progress.close()
    linked: list[tuple[Path, str, str]] = []
    for artifact, jar_path, archive_path in located:
        if artifact.packaging == "aar":
            if not archive_path.is_file():
                raise MavenError(f"aar not found: {artifact.coordinate}")
            if not jar_path.is_file() or archive_path.stat().st_mtime_ns > jar_path.stat().st_mtime_ns:
                if not _extract_classes_jar(archive_path, jar_path):
                    continue
        if not jar_path.is_file():
            raise MavenError(f"jar not found: {artifact.coordinate}")
        link = jars_dir / artifact.jar_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(jar_path.resolve())
        linked.append((link, artifact.kind, artifact.coordinate))
    return linked


def _extract_classes_jar(aar_path: Path, jar_path: Path) -> bool:
    """Write ``classes.jar`` from an Android archive next to the cached POM.

    A resource-only archive has no ``classes.jar``. That is not a fetch error.
    """
    with zipfile.ZipFile(aar_path) as archive:
        try:
            payload = archive.read("classes.jar")
        except KeyError:
            return False
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    partial = jar_path.with_name(jar_path.name + ".part")
    _ = partial.write_bytes(payload)
    partial.replace(jar_path)
    return True


def link_jar_dex(
    jar_link: Path,
    *,
    min_api: int,
    platform_jar: Path,
    r8_jar: Path,
) -> list[Path]:
    """Dex ``jar_link`` once into the cache and symlink ``jars/<stem>.dex``."""
    cached = ensure_jar_dex(
        jar_link,
        min_api=min_api,
        platform_jar=platform_jar,
        r8_jar=r8_jar,
    )
    _symlink_dex(jar_link, cached)
    return cached


def ensure_jar_dex(
    jar_path: Path,
    *,
    min_api: int,
    platform_jar: Path,
    r8_jar: Path,
) -> list[Path]:
    """Return cached dex files for ``jar_path``, building them when stale.

    The cache file sits beside the real jar and is named ``{stem}.min{api}.dex``.
    A second dex from D8 is ``{stem}.min{api}-2.dex``.
    """
    real_jar = jar_path.resolve()
    if not _contains_classes(real_jar):
        return []
    cached = _cached_dex_set(real_jar, min_api)
    if cached and _is_fresh(cached[0], real_jar, r8_jar):
        return cached
    with tempfile.TemporaryDirectory(prefix="py2apk-dex-") as temporary:
        output = Path(temporary)
        command = [
            "java",
            "-cp",
            str(r8_jar),
            "com.android.tools.r8.D8",
            "--intermediate",
            "--min-api",
            str(min_api),
            "--lib",
            str(platform_jar),
            "--output",
            str(output),
            str(real_jar),
        ]
        print(f"+ {' '.join(command)}")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"D8 failed with exit code {completed.returncode}: {real_jar.name}"
            )
        produced = _produced_dexes(output)
        if not produced:
            raise RuntimeError(f"D8 produced no classes.dex for {real_jar.name}")
        written: list[Path] = []
        for index, path in produced:
            destination = real_jar.with_name(_cache_dex_name(real_jar.stem, min_api, index))
            destination.unlink(missing_ok=True)
            _ = shutil.move(path, destination)
            written.append(destination)
        extra = len(written) + 1
        while True:
            stale = real_jar.with_name(_cache_dex_name(real_jar.stem, min_api, extra))
            if not stale.is_file():
                break
            stale.unlink()
            extra += 1
    return written


def jar_needs_dex(jar_path: Path) -> bool:
    """True when ``jar_path`` should have a dex sibling and does not yet."""
    if not jar_path.is_file():
        return True
    if jar_path.with_suffix(".dex").is_file():
        return False
    return _contains_classes(jar_path)


def project_dex_files(jar_path: Path) -> list[Path]:
    """Return ``jars/<stem>.dex`` and any ``<stem>-N.dex`` siblings."""
    primary = jar_path.with_suffix(".dex")
    if not primary.is_file():
        if jar_path.is_file() and not _contains_classes(jar_path):
            return []
        raise FileNotFoundError(primary)
    found = [primary]
    index = 2
    while True:
        extra = jar_path.with_name(f"{jar_path.stem}-{index}.dex")
        if not extra.is_file():
            return found
        found.append(extra)
        index += 1


def _dex_linked_jars(project_root: Path, jars: list[Path], platform_jar: Path) -> None:
    api = min_sdk(project_root)
    r8_jar = _r8_jar(project_root)
    progress = tqdm(jars, desc="Dexing dependencies", unit="jar")
    try:
        for jar_link in progress:
            progress.set_description(f"Dexing {jar_link.name}")
            _ = link_jar_dex(
                jar_link,
                min_api=api,
                platform_jar=platform_jar,
                r8_jar=r8_jar,
            )
    finally:
        progress.close()


def _r8_jar(project_root: Path) -> Path:
    from Py2APK.cli_setup import asset_directories

    directories: list[Path] = []
    configured = os.environ.get("PY2APK_JARS")
    if configured:
        directories.append(Path(configured).expanduser())
    directories.append(project_root / "jars")
    directories.extend(assets / "jars" for assets in asset_directories())
    seen: list[Path] = []
    for directory in directories:
        if directory in seen or not directory.is_dir():
            continue
        seen.append(directory)
        matches = sorted(directory.glob("r8-*.jar"))
        if len(matches) == 1:
            return matches[0].resolve()
    raise RuntimeError("r8 jar is missing. run: py2apk setup")


def _symlink_dex(jar_link: Path, cached: list[Path]) -> None:
    directory = jar_link.parent
    stem = jar_link.stem
    pattern = re.compile(rf"{re.escape(stem)}(?:-\d+)?\.dex\Z")
    for path in directory.glob(f"{stem}*.dex"):
        if pattern.fullmatch(path.name):
            path.unlink()
    for index, source in enumerate(cached, start=1):
        name = f"{stem}.dex" if index == 1 else f"{stem}-{index}.dex"
        link = directory / name
        link.symlink_to(source.resolve())


def _contains_classes(jar_path: Path) -> bool:
    """False when the jar has no dexable classes.

    Kotlin common jars and ``module-info`` / ``package-info`` entries have
    nothing for D8 to emit.
    """
    try:
        with zipfile.ZipFile(jar_path) as archive:
            return any(_dexable_class(info.filename) for info in archive.infolist())
    except zipfile.BadZipFile:
        return True


def _dexable_class(filename: str) -> bool:
    if not filename.endswith(".class"):
        return False
    name = filename.rsplit("/", 1)[-1]
    return name not in {"module-info.class", "package-info.class"}


def _cached_dex_set(real_jar: Path, min_api: int) -> list[Path]:
    found: list[Path] = []
    index = 1
    while True:
        path = real_jar.with_name(_cache_dex_name(real_jar.stem, min_api, index))
        if not path.is_file():
            return found
        found.append(path)
        index += 1


def _cache_dex_name(stem: str, min_api: int, index: int) -> str:
    if index == 1:
        return f"{stem}.min{min_api}.dex"
    return f"{stem}.min{min_api}-{index}.dex"


def _is_fresh(dex: Path, *sources: Path) -> bool:
    stamp = dex.stat().st_mtime_ns
    return all(stamp >= source.stat().st_mtime_ns for source in sources)


def _produced_dexes(output: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in output.glob("classes*.dex"):
        if path.name == "classes.dex":
            found.append((1, path))
            continue
        number = path.name.removeprefix("classes").removesuffix(".dex")
        if number.isdecimal():
            found.append((int(number), path))
    found.sort(key=lambda item: item[0])
    return found


def _remove_platform_symlink(project_root: Path) -> None:
    """Drop a platform symlink left in ``jars/`` by an older fetch."""
    link = project_root / "jars" / "android.jar"
    if link.is_symlink():
        link.unlink()


def _api_label(jar_path: Path) -> str:
    return jar_path.parent.name


def describe_repositories(extra: list[str] | None) -> list[str]:
    repos = repository_list(extra)
    assert repos[-1] == CENTRAL
    return repos
