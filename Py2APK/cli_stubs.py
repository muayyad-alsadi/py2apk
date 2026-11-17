"""Write ty stubs for Java packages imported by a project."""

from __future__ import annotations

import ast
import keyword
import sys
from pathlib import Path

from Py2APK.classfile import parameter_descriptors
from Py2APK.jar_index import JarCatalog


_PRIMITIVES = {
    "Z": "bool",
    "B": "int",
    "S": "int",
    "I": "int",
    "J": "int",
    "F": "float",
    "D": "float",
    "C": "str",
}


def write_stubs(project_root: Path) -> int:
    """Refresh ``typings/<package>.pyi`` for Java packages imported from ``src``."""
    database = project_root / "jars.db"
    if not database.is_file():
        print("jars.db is missing. Run py2apk fetch_jars.", file=sys.stderr)
        return 1
    catalog = JarCatalog(database)
    try:
        packages = sorted(
            module.replace(".", "/")
            for module in _imported_modules(project_root / "src")
            if catalog.has_package(module.replace(".", "/"))
        )
        classes: dict[str, list[tuple[str, str | None, bool]]] = {}
        generated: set[str] = set()
        for package in packages:
            rows = catalog.classes_in_package(package)
            classes[package] = rows
            generated.update(internal for internal, _super_name, _interface in rows)
        rendered = {
            package: _render_package(package, rows, generated, catalog)
            for package, rows in classes.items()
            if rows
        }
    finally:
        catalog.close()
    database_mtime = database.stat().st_mtime_ns
    for package, text in rendered.items():
        destination = project_root / "typings" / Path(package + ".pyi")
        if destination.is_file() and destination.stat().st_mtime_ns >= database_mtime:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return 0


def _imported_modules(source_root: Path) -> set[str]:
    modules: set[str] = set()
    if not source_root.is_dir():
        return modules
    for path in source_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module)
    return modules


def _render_package(
    package: str,
    rows: list[tuple[str, str | None, bool]],
    generated: set[str],
    catalog: JarCatalog,
) -> str:
    local_names = {internal.rsplit("/", 1)[-1] for internal, _super_name, _interface in rows}
    types = _Types(generated, package, local_names)
    blocks: list[str] = []
    needs_overload = False
    for internal, super_name, _interface in rows:
        fields = catalog.fields_of(internal)
        methods = catalog.methods_of(internal)
        body, overloaded = _class_body(internal, super_name, fields, methods, types)
        needs_overload = needs_overload or overloaded
        blocks.append(body)
    header: list[str] = []
    if needs_overload:
        header.append("from typing import overload")
    for internal, name in sorted(types.imports.items(), key=lambda item: item[1]):
        module = internal.rsplit("/", 1)[0].replace("/", ".")
        simple = internal.rsplit("/", 1)[-1]
        if name == simple:
            header.append(f"from {module} import {simple}")
        else:
            header.append(f"from {module} import {simple} as {name}")
    parts: list[str] = []
    if header:
        parts.append("\n".join(header))
    parts.extend(blocks)
    return "\n\n".join(parts) + "\n"


class _Types:
    """Map JVM descriptors onto names this stub can mention."""

    def __init__(self, generated: set[str], package: str, local_names: set[str]) -> None:
        self.generated = generated
        self.package = package
        self.imports: dict[str, str] = {}
        self._used = set(local_names)

    def format(self, descriptor: str) -> str | None:
        try:
            parts = parameter_descriptors(descriptor)
        except (IndexError, ValueError):
            return None
        if len(parts) != 1:
            return None
        return self._one(parts[0])

    def _one(self, descriptor: str) -> str | None:
        if descriptor.startswith("["):
            inner = self._one(descriptor[1:])
            if inner is None:
                return None
            return f"list[{inner}]"
        if descriptor in _PRIMITIVES:
            return _PRIMITIVES[descriptor]
        if descriptor == "V":
            return "None"
        if not descriptor.startswith("L") or not descriptor.endswith(";"):
            return None
        internal = descriptor[1:-1]
        if internal == "java/lang/String":
            return "str"
        if internal == "java/lang/Object":
            return "object"
        if internal not in self.generated or "$" in internal:
            return "object"
        simple = internal.rsplit("/", 1)[-1]
        owner = internal.rsplit("/", 1)[0]
        if owner == self.package:
            return simple
        imported = self.imports.get(internal)
        if imported is not None:
            return imported
        name = simple
        if name in self._used:
            name = owner.replace("/", "_") + "_" + simple
        self._used.add(name)
        self.imports[internal] = name
        return name


def _class_body(
    internal: str,
    super_name: str | None,
    fields: list[tuple[str, str, bool]],
    methods: list[tuple[str, str, str, bool]],
    types: _Types,
) -> tuple[str, bool]:
    simple = internal.rsplit("/", 1)[-1]
    base = _base_name(super_name, types)
    lines = [f"class {simple}({base}):"]
    members: list[str] = []
    for name, descriptor, _is_static in fields:
        if not _python_name(name):
            continue
        annotation = types.format(descriptor)
        if annotation is None:
            continue
        members.append(f"    {name}: {annotation}")
    grouped: dict[str, list[_Signature]] = {}
    order: list[str] = []
    for name, params, result, is_static in methods:
        python_name = "__init__" if name == "<init>" else name
        if not _python_name(python_name):
            continue
        signature = _signature(params, result, is_static, types)
        if signature is None:
            continue
        if python_name not in grouped:
            grouped[python_name] = []
            order.append(python_name)
        grouped[python_name].append(signature)
    overloaded = any(len(items) > 1 for items in grouped.values())
    if "__init__" in grouped:
        order.remove("__init__")
        members.extend(_method_lines("__init__", grouped["__init__"]))
    for name in order:
        members.extend(_method_lines(name, grouped[name]))
    if not members:
        lines.append("    ...")
    else:
        lines.extend(members)
    return "\n".join(lines), overloaded


class _Signature:
    def __init__(self, annotations: list[str], result: str, is_static: bool) -> None:
        self.annotations = annotations
        self.result = result
        self.is_static = is_static


def _signature(params: str, result: str, is_static: bool, types: _Types) -> _Signature | None:
    try:
        descriptors = parameter_descriptors(params)
    except (IndexError, ValueError):
        return None
    annotations: list[str] = []
    for descriptor in descriptors:
        annotation = types.format(descriptor)
        if annotation is None:
            return None
        annotations.append(annotation)
    if result == "V":
        result_name = "None"
    else:
        formatted = types.format(result)
        if formatted is None:
            return None
        result_name = formatted
    return _Signature(annotations, result_name, is_static)


def _method_lines(name: str, signatures: list[_Signature]) -> list[str]:
    lines: list[str] = []
    many = len(signatures) > 1
    for signature in signatures:
        if many:
            lines.append("    @overload")
        if signature.is_static:
            lines.append("    @staticmethod")
        arguments = [] if signature.is_static else ["self"]
        arguments.extend(
            f"arg{index}: {annotation}"
            for index, annotation in enumerate(signature.annotations)
        )
        lines.append(f"    def {name}({', '.join(arguments)}) -> {signature.result}: ...")
    return lines


def _base_name(super_name: str | None, types: _Types) -> str:
    if super_name is None:
        return "object"
    named = types.format(f"L{super_name};")
    if named is None or named == "object":
        return "object"
    return named


def _python_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)
