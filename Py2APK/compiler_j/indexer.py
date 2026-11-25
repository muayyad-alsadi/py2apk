"""Project type index built before lowering."""

from __future__ import annotations

import ast
from pathlib import Path

from Py2APK.compiler_j.generators import (
    _context_manager_type,
    _has_yield,
    _indexed_generator,
    _is_contextmanager_def,
    _iterator_slice,
)
from Py2APK.compiler_j.types import (
    _BUILTIN_TYPES,
    _JAVA_ITERATOR,
    JasminCompilerError,
    LocalType,
    _array_descriptor,
    _descriptor,
    _internal_name,
    _listed_element,
    _MethodSignature,
    _package_for,
)


def index_project_types(
    source_root: Path,
    extra_sources: list[Path] | None = None,
) -> dict[str, LocalType]:
    """Read class bases, constructors, and methods from the project sources."""
    found: dict[str, LocalType] = {}
    sources = sorted(source_root.rglob("*.py")) if source_root.is_dir() else []
    for source in list(extra_sources or []) + sources:
        _index_source(source, found)
    return found


def _index_source(source: Path, found: dict[str, LocalType]) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    package = _package_for(source, source)
    imports = _collect_imports(tree, package)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if functions:
        _index_functions(functions, _indexed_module_name(source), imports, found)
    class_package = (
        _indexed_module_name(source)
        if functions or (_in_stdlib(source) and len(classes) != 1)
        else package
    )
    if len(classes) == 1 and not functions:
        _index_class(classes[0], package, imports, found)
        return
    for node in classes:
        _index_class(node, class_package, imports, found)


def _in_stdlib(source: Path) -> bool:
    from Py2APK.module_resolve import stdlib_root

    try:
        source.resolve().relative_to(stdlib_root().resolve())
    except ValueError:
        return False
    return True


def _indexed_module_name(source: Path) -> str:
    from Py2APK.module_resolve import module_internal

    found = module_internal(source)
    if found:
        return found
    package = _package_for(source, source)
    stem = source.parent.name if source.name == "__init__.py" else source.stem
    return "/".join(filter(None, (package, stem)))


def _index_functions(
    functions: list[ast.FunctionDef],
    internal: str,
    imports: dict[str, str],
    found: dict[str, LocalType],
) -> None:
    methods: dict[str, _MethodSignature] = {}
    generators: dict[str, str] = {}
    serial = 0
    context_serial = 0
    for item in functions:
        class_name = internal.rsplit("/", 1)[-1]
        parameters, parameter_names = _annotated_parameters(
            item.args.args, imports, internal, class_name
        )
        result = (
            _type_from_expr(item.returns, imports, internal, class_name)
            if item.returns is not None
            else "V"
        )
        yielded = _iterator_slice(item.returns, imports)
        if yielded is not None and _has_yield(item):
            result = _type_from_expr(yielded, imports, internal, class_name)
        if _is_contextmanager_def(item, imports):
            context_serial += 1
            synthetic = f"{internal}$cm${context_serial}"
            methods[item.name] = _MethodSignature(
                parameters,
                _descriptor(synthetic),
                opcode="invokestatic",
                owner=internal,
                parameter_names=parameter_names,
            )
            found[synthetic] = _context_manager_type(synthetic, result)
            continue
        result, serial = _indexed_generator(item, result, generators, internal, serial)
        methods[item.name] = _MethodSignature(
            parameters,
            result,
            opcode="invokestatic",
            owner=internal,
            parameter_names=parameter_names,
        )
    found[internal] = LocalType(internal, None, (), methods, generators)


def _index_class(
    node: ast.ClassDef,
    package: str,
    imports: dict[str, str],
    found: dict[str, LocalType],
) -> None:
    if len(node.bases) > 1:
        return
    internal = "/".join(filter(None, (package, node.name)))
    base_type = (
        _type_from_expr(node.bases[0], imports, internal, node.name)
        if node.bases
        else None
    )
    base = base_type[1:-1] if base_type and base_type.startswith("L") else base_type
    constructor: tuple[str, ...] = ()
    constructor_names: tuple[str, ...] = ()
    methods: dict[str, _MethodSignature] = {}
    generators: dict[str, str] = {}
    fields: dict[str, str] = {}
    serial = 0
    context_serial = 0
    for item in node.body:
        if (
            isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.value is None
            and item.annotation is not None
        ):
            fields[item.target.id] = _type_from_expr(
                item.annotation, imports, internal, node.name
            )
        if not isinstance(item, ast.FunctionDef):
            continue
        parameters, parameter_names = _annotated_parameters(
            item.args.args[1:], imports, internal, node.name
        )
        result = (
            _type_from_expr(item.returns, imports, internal, node.name)
            if item.returns is not None
            else "V"
        )
        yielded = _iterator_slice(item.returns, imports)
        if yielded is not None and _has_yield(item):
            result = _type_from_expr(yielded, imports, internal, node.name)
        if item.name == "__init__":
            constructor = parameters
            constructor_names = parameter_names
        elif _is_contextmanager_def(item, imports):
            context_serial += 1
            synthetic = f"{internal}$cm${context_serial}"
            methods[item.name] = _MethodSignature(
                parameters,
                _descriptor(synthetic),
                owner=internal,
                parameter_names=parameter_names,
            )
            found[synthetic] = _context_manager_type(synthetic, result)
        else:
            result, serial = _indexed_generator(
                item, result, generators, internal, serial
            )
            methods[item.name] = _MethodSignature(
                parameters, result, owner=internal, parameter_names=parameter_names
            )
    found[internal] = LocalType(
        internal,
        base,
        constructor,
        methods,
        generators,
        fields,
        constructor_names=constructor_names,
    )


def _annotated_parameters(
    arguments: list[ast.arg],
    imports: dict[str, str],
    internal: str,
    class_name: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    chosen = [argument for argument in arguments if argument.annotation is not None]
    parameters = tuple(
        _type_from_expr(argument.annotation, imports, internal, class_name)
        for argument in chosen
    )
    names = tuple(argument.arg for argument in chosen)
    return parameters, names


def _collect_imports(tree: ast.Module, package: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    statements = list(tree.body)
    for statement in tree.body:
        if (
            isinstance(statement, ast.If)
            and isinstance(statement.test, ast.Name)
            and statement.test.id == "TYPE_CHECKING"
        ):
            statements.extend(statement.body)
    for statement in statements:
        if isinstance(statement, ast.ImportFrom):
            if statement.level:
                if statement.level != 1 or not statement.module:
                    continue
                for alias in statement.names:
                    if alias.name != statement.module:
                        continue
                    local_name = alias.asname or alias.name
                    imports[local_name] = "/".join(filter(None, (package, alias.name)))
                continue
            if statement.module is None:
                continue
            if statement.module == "collections.abc":
                for alias in statement.names:
                    if alias.name != "Iterator" or alias.name == "*":
                        continue
                    imports[alias.asname or alias.name] = _JAVA_ITERATOR
                continue
            module = statement.module.replace(".", "/")
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                imports[local_name] = f"{module}/{alias.name}"
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                imports[local_name] = alias.name.replace(".", "/")
    return imports


def _type_from_expr(
    node: ast.expr | None,
    imports: dict[str, str],
    class_internal: str,
    class_name: str,
) -> str:
    if node is None:
        return "V"
    if isinstance(node, ast.Constant) and node.value is None:
        return "V"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except SyntaxError as exc:
            raise JasminCompilerError(
                f"invalid string annotation {node.value!r}"
            ) from exc
        if not isinstance(parsed, ast.expr):
            raise JasminCompilerError(f"invalid string annotation {node.value!r}")
        return _type_from_expr(parsed, imports, class_internal, class_name)
    if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPES:
        return _BUILTIN_TYPES[node.id]
    listed = _listed_element(node) if node is not None else None
    if listed is not None:
        return _array_descriptor(
            _type_from_expr(listed, imports, class_internal, class_name)
        )
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and imports.get(node.value.id) == _JAVA_ITERATOR
    ):
        return _descriptor(_JAVA_ITERATOR)
    if isinstance(node, ast.Name):
        if node.id == class_name:
            return _descriptor(class_internal)
        imported = imports.get(node.id)
        if imported is None:
            raise JasminCompilerError(f"unknown type {node.id!r}")
        return _descriptor(imported)
    if isinstance(node, ast.Attribute):
        return _descriptor(
            f"{_internal_name(_type_from_expr(node.value, imports, class_internal, class_name))}${node.attr}"
        )
    raise JasminCompilerError(f"unsupported annotation {type(node).__name__}")
