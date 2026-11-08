"""Fetch Maven dependencies and the Android platform jar for a project."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.cli_fetch_platform import cache_root, fetch_platform
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
        artifacts = resolve_artifacts(coordinates, lambda relative: _fetch(repos, relative, store)) if coordinates else []
        jars = _link_maven_jars(root, store, repos, artifacts)
        platform = fetch_platform(root, store)
        _remove_platform_symlink(root)
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
                _extract_classes_jar(archive_path, jar_path)
        if not jar_path.is_file():
            raise MavenError(f"jar not found: {artifact.coordinate}")
        link = jars_dir / artifact.jar_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(jar_path.resolve())
        linked.append((link, artifact.kind, artifact.coordinate))
    return linked


def _extract_classes_jar(aar_path: Path, jar_path: Path) -> None:
    """Write ``classes.jar`` from an Android archive next to the cached POM."""
    with zipfile.ZipFile(aar_path) as archive:
        try:
            payload = archive.read("classes.jar")
        except KeyError as error:
            raise MavenError(f"{aar_path.name} has no classes.jar") from error
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    partial = jar_path.with_name(jar_path.name + ".part")
    _ = partial.write_bytes(payload)
    partial.replace(jar_path)


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
