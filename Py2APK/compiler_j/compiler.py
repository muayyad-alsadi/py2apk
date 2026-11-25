"""Composed Jasmin compiler and compile_py2j entry point."""

from __future__ import annotations

import ast
from pathlib import Path

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.calls import _CallsMixin
from Py2APK.compiler_j.collections import _CollectionsMixin
from Py2APK.compiler_j.compare import _CompareMixin
from Py2APK.compiler_j.exceptions import _ExceptionsMixin
from Py2APK.compiler_j.expr import _ExprMixin
from Py2APK.compiler_j.generators import _GeneratorsMixin
from Py2APK.compiler_j.lambdas import _LambdasMixin
from Py2APK.compiler_j.loops import _LoopsMixin
from Py2APK.compiler_j.module import _ModuleMixin
from Py2APK.compiler_j.types import JasminCompilerError, LocalType, _project_root
from Py2APK.jar_index import JarCatalog


class _Compiler(
    _ExceptionsMixin,
    _GeneratorsMixin,
    _LambdasMixin,
    _LoopsMixin,
    _CallsMixin,
    _CollectionsMixin,
    _CompareMixin,
    _ExprMixin,
    _ModuleMixin,
    _CompilerBase,
):
    """Lower one typed module through the family mixins."""


def compile_py2j(
    source: Path,
    output: Path,
    catalog: JarCatalog,
    project: dict[str, LocalType],
) -> list[Path]:
    """Compile one typed Python-like source file into a Jasmin ``.j`` file."""
    source = Path(source)
    output = Path(output)
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except SyntaxError as exc:
        location = f"{source}:{exc.lineno or '?'}"
        raise JasminCompilerError(f"{location}: invalid syntax: {exc.msg}") from exc

    compiler = _Compiler(source, output, tree, catalog, project)
    compiler.project_root = _project_root(source)
    jasmin = compiler.emit()
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(jasmin, encoding="utf-8")
    written = [output]
    for filename, text in compiler.extra_classes:
        extra = output.parent / filename
        _ = extra.write_text(text, encoding="utf-8")
        written.append(extra)
    return written
