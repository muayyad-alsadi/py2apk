"""Resolve Maven coordinates from an ordered repository list.

Google Maven and Central are always searched after any extra repositories.
The first repository that contains a path wins.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field


CENTRAL = "https://repo.maven.apache.org/maven2"
GOOGLE = "https://dl.google.com/dl/android/maven2"

_QUALIFIERS = {
    "alpha": 1,
    "a": 1,
    "beta": 2,
    "b": 2,
    "milestone": 3,
    "m": 3,
    "rc": 4,
    "cr": 4,
    "snapshot": 5,
    "": 6,
    "final": 6,
    "ga": 6,
    "release": 6,
    "sp": 7,
}


class MavenError(RuntimeError):
    """Raised when a coordinate cannot be resolved."""


def repository_list(extra: list[str] | None) -> list[str]:
    """Return ``extra`` repositories, then Google Maven, then Central."""
    ordered: list[str] = []
    for repo in [*(extra or []), GOOGLE, CENTRAL]:
        cleaned = repo.rstrip("/")
        if cleaned in ordered:
            continue
        ordered.append(cleaned)
    return ordered


@dataclass
class Artifact:
    group: str
    artifact: str
    version: str
    classifier: str = ""
    packaging: str = "jar"
    kind: str = "transitive"

    @property
    def coordinate(self) -> str:
        value = f"{self.group}:{self.artifact}:{self.version}"
        if self.classifier:
            value += f":{self.classifier}"
        return value

    @property
    def jar_name(self) -> str:
        suffix = f"-{self.classifier}" if self.classifier else ""
        return f"{self.artifact}-{self.version}{suffix}.jar"

    @property
    def path(self) -> str:
        base = f"{self.group.replace('.', '/')}/{self.artifact}/{self.version}"
        return f"{base}/{self.jar_name}"


Fetch = Callable[[str], bytes | None]


@dataclass
class _Pom:
    group: str
    artifact: str
    version: str
    packaging: str
    properties: dict[str, str] = field(default_factory=dict)
    management: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    relocation: tuple[str, str, str] | None = None
    deps: list[dict[str, object]] = field(default_factory=list)


def resolve_artifacts(roots: list[tuple[str, str, str]], fetch: Fetch) -> list[Artifact]:
    """Resolve exact ``group, artifact, version`` roots and their runtime jars."""
    direct = {(group, artifact) for group, artifact, _version in roots}
    chosen: dict[tuple[str, str], str] = {}
    result: list[Artifact] = []
    level: list[tuple[str, str, str, str, list[tuple[str, str]]]] = [
        (group, artifact, version, "compile", []) for group, artifact, version in roots
    ]
    cache: dict[tuple[str, str, str], _Pom] = {}
    while level:
        nxt: list[tuple[str, str, str, str, list[tuple[str, str]]]] = []
        for group, artifact, version, scope, exclusions in level:
            if (group, artifact) in chosen:
                continue
            if not version:
                raise MavenError(f"no version for {group}:{artifact}")
            pom = _load_pom(group, artifact, version, fetch, cache)
            hops = 0
            while pom.relocation and hops < 5:
                group, artifact, version = pom.relocation
                pom = _load_pom(group, artifact, version, fetch, cache)
                hops += 1
            chosen[(group, artifact)] = version
            kind = "direct" if (group, artifact) in direct else "transitive"
            result.append(Artifact(group, artifact, version, "", pom.packaging, kind))
            for dep in pom.deps:
                dep_scope = str(dep["scope"])
                if dep_scope not in {"compile", "runtime"} or dep["optional"]:
                    continue
                if _excluded(str(dep["group"]), str(dep["artifact"]), exclusions):
                    continue
                effective = "runtime" if "runtime" in {dep_scope, scope} else "compile"
                nxt.append(
                    (
                        str(dep["group"]),
                        str(dep["artifact"]),
                        str(dep["version"]),
                        effective,
                        exclusions + list(dep["exclusions"]),  # type: ignore[arg-type]
                    )
                )
        level = nxt
    return result


@dataclass(frozen=True)
class DependencyFile:
    coordinates: list[tuple[str, str, str]]
    repositories: list[str]


def parse_dependency_file(text: str) -> DependencyFile:
    """Read coordinates and ``--extra-index-url`` lines from ``deps.txt``."""
    coordinates: list[tuple[str, str, str]] = []
    repositories: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            option, _, value = line.partition(" ")
            if option != "--extra-index-url" or not value.strip():
                raise MavenError(f"unsupported deps.txt option: {line}")
            repositories.append(value.strip())
            continue
        parts = line.split(":")
        if len(parts) < 3 or not all(parts[:3]):
            raise MavenError(
                f"bad coordinate (want group:artifact:version): {line}"
            )
        coordinates.append((parts[0], parts[1], parts[2]))
    if not coordinates:
        raise MavenError("no coordinates given")
    return DependencyFile(coordinates, repositories)


def _load_pom(
    group: str,
    artifact: str,
    version: str,
    fetch: Fetch,
    cache: dict[tuple[str, str, str], _Pom],
) -> _Pom:
    key = (group, artifact, version)
    cached = cache.get(key)
    if cached is not None:
        return cached
    relative = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"
    payload = fetch(relative)
    if payload is None:
        raise MavenError(f"POM not found: {group}:{artifact}:{version}")
    root = ET.fromstring(payload)
    _strip_ns(root)
    pom = _Pom(group, artifact, version, _text(root, "packaging") or "jar")
    relocation = root.find("distributionManagement/relocation")
    if relocation is not None:
        pom.relocation = (
            _text(relocation, "groupId") or group,
            _text(relocation, "artifactId") or artifact,
            _text(relocation, "version") or version,
        )
    properties: dict[str, str] = {}
    management: dict[tuple[str, str], dict[str, object]] = {}
    parent = root.find("parent")
    if parent is not None:
        parent_pom = _load_pom(
            _text(parent, "groupId") or "",
            _text(parent, "artifactId") or "",
            _text(parent, "version") or "",
            fetch,
            cache,
        )
        properties.update(parent_pom.properties)
        management.update(parent_pom.management)
    properties.update(_properties(root))
    pom.properties = dict(properties)
    context = dict(properties)
    context.update(
        {
            "project.groupId": group,
            "project.artifactId": artifact,
            "project.version": version,
            "pom.groupId": group,
            "pom.artifactId": artifact,
            "pom.version": version,
            "version": version,
        }
    )
    if parent is not None:
        context["project.parent.version"] = _text(parent, "version") or ""
        context["parent.version"] = context["project.parent.version"]
    for child in root.findall("dependencyManagement/dependencies/dependency"):
        dep = _finish(_dependency(child), context)
        if dep["scope"] == "import" and dep["type"] == "pom":
            bom = _load_pom(str(dep["group"]), str(dep["artifact"]), str(dep["version"]), fetch, cache)
            for managed_key, managed in bom.management.items():
                management.setdefault(managed_key, managed)
        else:
            management[(str(dep["group"]), str(dep["artifact"]))] = dep
    pom.management = management
    for child in root.findall("dependencies/dependency"):
        dep = _finish(_dependency(child), context)
        managed = management.get((str(dep["group"]), str(dep["artifact"])))
        if managed:
            if not dep["version"]:
                dep["version"] = managed["version"]
            if not dep["scope"]:
                dep["scope"] = managed["scope"]
        if not dep["scope"]:
            dep["scope"] = "compile"
        pom.deps.append(dep)
    cache[key] = pom
    return pom


def _finish(dep: dict[str, object], context: dict[str, str]) -> dict[str, object]:
    resolved = dict(dep)
    for name in ("group", "artifact", "version", "scope", "type"):
        resolved[name] = _interpolate(str(resolved[name] or ""), context)
    return resolved


def _properties(root: ET.Element) -> dict[str, str]:
    found: dict[str, str] = {}
    element = root.find("properties")
    if element is None:
        return found
    for child in element:
        if isinstance(child.tag, str):
            found[child.tag] = (child.text or "").strip()
    return found


def _dependency(element: ET.Element) -> dict[str, object]:
    exclusions = [
        (_text(item, "groupId") or "*", _text(item, "artifactId") or "*")
        for item in element.findall("exclusions/exclusion")
    ]
    return {
        "group": _text(element, "groupId") or "",
        "artifact": _text(element, "artifactId") or "",
        "version": _text(element, "version") or "",
        "scope": _text(element, "scope") or "",
        "optional": (_text(element, "optional") or "false").lower() == "true",
        "type": _text(element, "type") or "jar",
        "exclusions": exclusions,
    }


def _interpolate(value: str, properties: dict[str, str]) -> str:
    current = value
    for _ in range(10):
        if "${" not in current:
            return current
        updated = re.sub(
            r"\$\{([^}]+)\}",
            lambda match: properties.get(match.group(1), match.group(0)),
            current,
        )
        if updated == current:
            return current
        current = updated
    return current


def _excluded(group: str, artifact: str, exclusions: list[tuple[str, str]]) -> bool:
    return any(
        (expected_group in {"*", group} and expected_artifact in {"*", artifact})
        for expected_group, expected_artifact in exclusions
    )


def _strip_ns(root: ET.Element) -> None:
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None
