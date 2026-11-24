"""Compile the project's typed Python-like source subset to Jasmin assembly.

This is intentionally not a general Python compiler.  It accepts the small,
statically typed language used by the bundled Android templates and fails fast
when a construct or API signature is not known.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from Py2APK.classfile import parameter_descriptors
from Py2APK.jar_index import JarCatalog


class JasminCompilerError(Exception):
    """Raised when source cannot be represented by the supported JVM subset."""


@dataclass(frozen=True)
class _MethodSignature:
    parameters: tuple[str, ...]
    result: str
    opcode: str = "invokevirtual"
    owner: str = ""

    @property
    def descriptor(self) -> str:
        return f"({''.join(self.parameters)}){self.result}"


@dataclass
class LocalType:
    internal: str
    base: str | None
    constructor: tuple[str, ...]
    methods: dict[str, _MethodSignature] = field(default_factory=dict)
    # Synthetic ``Class$gen$N`` internal name -> yielded JVM element type.
    generators: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)


_STRING = "Ljava/lang/String;"

_BUILTIN_TYPES = {
    "int": "I",
    "bool": "Z",
    "float": "F",
    "double": "D",
    "str": _STRING,
    "None": "V",
}

_STRING_BUILDER = "java/lang/StringBuilder"
_OBJECT_ARRAY = "[Ljava/lang/Object;"
_STRING_ARRAY = "[Ljava/lang/String;"
_ARRAY_LIST = "Ljava/util/ArrayList;"
_LINKED_HASH_MAP = "Ljava/util/LinkedHashMap;"
_ARRAY_LIST_OWNER = "java/util/ArrayList"
_LINKED_HASH_MAP_OWNER = "java/util/LinkedHashMap"

_NEWARRAY = {"Z": "boolean", "I": "int", "F": "float", "D": "double", "J": "long"}
_ARRAY_STORE = {"Z": "bastore", "I": "iastore", "F": "fastore", "D": "dastore", "J": "lastore"}
_ARRAY_LOAD = {"Z": "baload", "I": "iaload", "F": "faload", "D": "daload", "J": "laload"}

_STRING_METHOD_ALIASES = {
    "upper": "toUpperCase",
    "lower": "toLowerCase",
    "strip": "trim",
    "startswith": "startsWith",
    "endswith": "endsWith",
    "find": "indexOf",
    "replace": "replace",
}

_BOX_METHODS = {
    "I": ("java/lang/Integer", "valueOf"),
    "Z": ("java/lang/Boolean", "valueOf"),
    "F": ("java/lang/Float", "valueOf"),
    "D": ("java/lang/Double", "valueOf"),
    "J": ("java/lang/Long", "valueOf"),
}

_UNBOX_METHODS = {
    "I": ("java/lang/Integer", "intValue", "()I"),
    "Z": ("java/lang/Boolean", "booleanValue", "()Z"),
    "F": ("java/lang/Float", "floatValue", "()F"),
    "D": ("java/lang/Double", "doubleValue", "()D"),
    "J": ("java/lang/Long", "longValue", "()J"),
}

_NUMERIC_CASTS = {
    ("I", "I"): "",
    ("Z", "I"): "",
    ("F", "I"): "f2i",
    ("D", "I"): "d2i",
    ("J", "I"): "l2i",
    ("I", "F"): "i2f",
    ("Z", "F"): "i2f",
    ("F", "F"): "",
    ("D", "F"): "d2f",
    ("J", "F"): "l2f",
    ("I", "D"): "i2d",
    ("Z", "D"): "i2d",
    ("F", "D"): "f2d",
    ("D", "D"): "",
    ("J", "D"): "l2d",
}

_PARSE_METHODS = {
    "int": ("java/lang/Integer", "parseInt", "I"),
    "float": ("java/lang/Float", "parseFloat", "F"),
    "double": ("java/lang/Double", "parseDouble", "D"),
}


def _java_method_name(owner: str, name: str) -> str:
    if owner == "java/lang/String":
        return _STRING_METHOD_ALIASES.get(name, name)
    return name


def _return_opcode(descriptor: str) -> str:
    if descriptor in {"I", "Z"}:
        return "ireturn"
    if descriptor == "F":
        return "freturn"
    if descriptor == "D":
        return "dreturn"
    if descriptor == "J":
        return "lreturn"
    if descriptor == "V":
        return "return"
    return "areturn"


def _immediate_return(descriptor: str) -> tuple[str, ...]:
    if descriptor == "V":
        return ("    return",)
    if descriptor in {"I", "Z", "B", "C", "S"}:
        return ("    iconst_0", "    ireturn")
    if descriptor == "J":
        return ("    lconst_0", "    lreturn")
    if descriptor == "F":
        return ("    fconst_0", "    freturn")
    if descriptor == "D":
        return ("    dconst_0", "    dreturn")
    return ("    aconst_null", "    areturn")


_WIDEN = {
    ("I", "J"): "i2l",
    ("I", "F"): "i2f",
    ("I", "D"): "i2d",
    ("J", "D"): "l2d",
    ("F", "D"): "f2d",
}


def _numeric(descriptor: str) -> bool:
    return descriptor in {"I", "Z", "J", "F", "D"}


def _reference(descriptor: str) -> bool:
    return descriptor == "null" or descriptor.startswith(("L", "["))


def _equality_width(left: str, right: str) -> str:
    kinds = {"I" if item in {"I", "Z"} else item for item in (left, right)}
    if "D" in kinds or kinds == {"J", "F"}:
        return "D"
    if "F" in kinds:
        return "F"
    if "J" in kinds:
        return "J"
    return "I"


def _literal_int(node: ast.expr) -> int | None:
    """Fold a constant int, including ``-n`` and ``+n``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return int(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal_int(node.operand)
        if inner is None:
            return None
        return -inner
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _literal_int(node.operand)
    return None


def _has_yield(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Yield | ast.YieldFrom) for child in ast.walk(node))


def _contains_yield(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Yield) for child in ast.walk(node))


def _yield_in_loop(statements: list[ast.stmt], in_loop: bool = False) -> bool:
    for statement in statements:
        loop = in_loop or isinstance(statement, ast.For | ast.While)
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Yield) and loop:
            return True
        nested: list[list[ast.stmt]] = []
        if isinstance(statement, ast.If | ast.For | ast.While | ast.With | ast.Try):
            nested.append(statement.body)
            nested.append(getattr(statement, "orelse", []))
        if isinstance(statement, ast.Try):
            nested.append(statement.finalbody)
            nested.extend(handler.body for handler in statement.handlers)
        if any(_yield_in_loop(block, loop) for block in nested):
            return True
    return False


_CONTEXT_MANAGER = "contextlib/contextmanager"
_NULLCONTEXT = "contextlib/nullcontext"
_SUPPRESS = "contextlib/suppress"


def _names_contextmanager(node: ast.expr, imports: dict[str, str]) -> bool:
    if isinstance(node, ast.Name):
        return imports.get(node.id) == _CONTEXT_MANAGER
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.attr == "contextmanager"
        and imports.get(node.value.id) == "contextlib"
    )


def _is_contextmanager_def(node: ast.FunctionDef, imports: dict[str, str]) -> bool:
    return len(node.decorator_list) == 1 and _names_contextmanager(node.decorator_list[0], imports)


def _context_manager_type(internal: str, element: str) -> LocalType:
    throwable = "Ljava/lang/Throwable;"
    return LocalType(
        internal,
        None,
        (),
        {
            "__enter__": _MethodSignature((), element, owner=internal),
            "__exit__": _MethodSignature((throwable, throwable, throwable), "Z", owner=internal),
        },
    )


_JAVA_ITERATOR = "java/util/Iterator"


def _iterator_slice(node: ast.expr | None, imports: dict[str, str]) -> ast.expr | None:
    """Return ``T`` from ``Iterator[T]`` when ``Iterator`` is ``java.util.Iterator``."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
        if not isinstance(parsed, ast.expr):
            return None
        return _iterator_slice(parsed, imports)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and imports.get(node.value.id) == _JAVA_ITERATOR
        and not isinstance(node.slice, ast.Tuple)
    ):
        return node.slice
    return None


def _indexed_generator(
    item: ast.FunctionDef,
    result: str,
    generators: dict[str, str],
    internal: str,
    serial: int,
) -> tuple[str, int]:
    if result == "V" or not _has_yield(item):
        return result, serial
    serial += 1
    synthetic = f"{internal}$gen${serial}"
    generators[synthetic] = result
    return _descriptor(synthetic), serial


def _loaded_names(node: ast.AST, bound: set[str]) -> list[str]:
    if isinstance(node, ast.Lambda):
        inner = set(bound)
        inner.update(arg.arg for arg in node.args.args)
        return _loaded_names(node.body, inner)
    found: list[str] = []
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
        found.append(node.id)
    for child in ast.iter_child_nodes(node):
        for name in _loaded_names(child, bound):
            if name not in found:
                found.append(name)
    return found


def _format_pattern(spec: str) -> str | None:
    """Return a Java format pattern for one supported Python spec."""
    if spec == "s":
        return "%s"
    if re.fullmatch(r"(?:0\d+|\d+)?d", spec):
        return "%" + spec
    if re.fullmatch(r"(?:0\d+|\d+)?\.\d+f", spec):
        return "%" + spec
    return None


def _listed_element(node: ast.expr) -> ast.expr | None:
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "list":
        return node.slice
    return None


def _static_matches(signature: _MethodSignature, static: bool) -> bool:
    return (signature.opcode == "invokestatic") == static


def _descriptor(internal_name: str) -> str:
    return f"L{internal_name};"


def _list_element(descriptor: str) -> str | None:
    prefix = "arraylist:"
    if descriptor.startswith(prefix):
        return descriptor[len(prefix):]
    return None


def _jvm_descriptor(descriptor: str) -> str:
    if _list_element(descriptor) is not None:
        return _ARRAY_LIST
    return descriptor


def _array_descriptor(element: str) -> str:
    return f"[{element}"


def _internal_name(descriptor: str) -> str:
    if not (descriptor.startswith("L") and descriptor.endswith(";")):
        raise ValueError(f"not a reference descriptor: {descriptor}")
    return descriptor[1:-1]


def _package_for(source: Path, output: Path) -> str:
    """Infer the JVM package from the conventional src/ or .py2apk/j layout."""
    source_parts = source.resolve().parts
    src_positions = [index for index, part in enumerate(source_parts) if part == "src"]
    if src_positions:
        return "/".join(source_parts[src_positions[-1] + 1 : -1])

    output_parts = output.resolve().parts
    j_positions = [index for index, part in enumerate(output_parts) if part == "j"]
    if j_positions:
        return "/".join(output_parts[j_positions[-1] + 1 : -1])

    return ""


def _project_root(source: Path) -> Path:
    parts = source.resolve().parts
    if "src" in parts:
        index = max(position for position, part in enumerate(parts) if part == "src")
        return Path(*parts[:index])
    return source.resolve().parent


def _list_origin(name: str, origins: dict[str, str]) -> str:
    seen: set[str] = set()
    while name in origins and origins[name] != name and name not in seen:
        seen.add(name)
        name = origins[name]
    return name


class _Compiler:
    def __init__(
        self,
        source: Path,
        output: Path,
        tree: ast.Module,
        catalog: JarCatalog,
        project: dict[str, LocalType],
    ) -> None:
        self.source: Path = source
        self.output: Path = output
        self.tree: ast.Module = tree
        self.catalog: JarCatalog = catalog
        self.project: dict[str, LocalType] = project
        self.package: str = _package_for(source, output)
        self.imports: dict[str, str] = {}
        self.imported_functions: dict[str, tuple[str, str]] = {}
        self.project_root: Path = source.parent
        self.method_result: str = "V"
        self.class_name: str = ""
        self.class_internal: str = ""
        self.super_internal: str = "java/lang/Object"
        self.fields: dict[str, str] = {}
        self.methods: dict[str, _MethodSignature] = {}
        self.lines: list[str] = []
        self.instructions: list[str] = []
        self.locals: dict[str, tuple[int, str]] = {}
        self.next_local: int = 0
        self.label_counter: int = 0
        self.lambda_body: bool = False
        self.lambda_serial: int = 0
        self.extra_classes: list[tuple[str, str]] = []
        self.list_sink: str | None = None
        self.mutable_lists: set[str] = set()
        self.field_list_ids: set[int] = set()
        self.list_elements: dict[str, str] = {}
        self.preview_locals: dict[str, str] = {}
        self.generator_elements: dict[str, str] = {}
        for local in project.values():
            self.generator_elements.update(local.generators)
        self.generator_names: dict[str, str] = {}
        self.context_names: dict[str, str] = {}
        self.context_elements: dict[str, str] = {}
        self.generator_internal: str | None = None
        self.generator_element: str | None = None
        self.generator_fields: dict[str, str] | None = None
        self.generator_exhausted: str | None = None
        self.resume_labels: list[str] = []
        self.yield_cursor: int = 0
        self.temp_serial: int = 0
        self.for_serial: int = 0
        self.loops: list[tuple[str, str]] = []
        self.handlers: list[str] = []

    def error(self, node: ast.AST, message: str) -> JasminCompilerError:
        line = getattr(node, "lineno", "?")
        return JasminCompilerError(f"{self.source}:{line}: {message}")

    def emit(self) -> str:
        classes: list[ast.ClassDef] = []
        functions: list[ast.FunctionDef] = []
        for statement in self.tree.body:
            if isinstance(statement, ast.Import | ast.ImportFrom):
                self._record_import(statement)
            elif isinstance(statement, ast.If) and self._is_type_checking(statement.test):
                for nested in statement.body:
                    if isinstance(nested, ast.Import | ast.ImportFrom):
                        self._record_import(nested)
                        continue
                    if (
                        isinstance(nested, ast.AnnAssign)
                        and nested.value is None
                        and isinstance(nested.target, ast.Name)
                        and nested.target.id in _BUILTIN_TYPES
                    ):
                        continue
                    raise self.error(
                        nested,
                        "TYPE_CHECKING blocks may contain only imports and builtin annotations",
                    )
            elif isinstance(statement, ast.ClassDef):
                classes.append(statement)
            elif isinstance(statement, ast.FunctionDef):
                functions.append(statement)
            elif (
                isinstance(statement, ast.Pass)
                or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
                or (
                    isinstance(statement, ast.AnnAssign)
                    and statement.value is None
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id in _BUILTIN_TYPES
                )
                or (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and statement.targets[0].id in _BUILTIN_TYPES
                    and isinstance(statement.value, ast.Name)
                )
            ):
                continue
            else:
                raise self.error(statement, f"unsupported module statement {type(statement).__name__}")

        if len(classes) == 1 and not functions:
            self._compile_class(classes[0])
            return self._take_text()
        if not classes and not functions:
            raise JasminCompilerError(f"{self.source}: expected a class or a function")

        primary = ""
        if functions:
            self._compile_module(functions)
            primary = self._take_text()
        saved_package = self.package
        if functions:
            self.package = self.class_internal
        elif len(classes) != 1 and _in_stdlib(self.source):
            self.package = _indexed_module_name(self.source)
        for node in classes:
            self._compile_class(node)
            text = self._take_text()
            if not primary:
                primary = text
                continue
            self.extra_classes.append((f"{node.name}.j", text))
        self.package = saved_package
        return primary

    def _take_text(self) -> str:
        text = "\n".join(self.lines)
        self.lines = []
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    def _compile_module(self, functions: list[ast.FunctionDef]) -> None:
        self._begin_type()
        self.class_name = self.source.parent.name if self.source.name == "__init__.py" else self.source.stem
        self.class_internal = "/".join(filter(None, (self.package, self.class_name)))
        for function in functions:
            self.methods[function.name] = self._function_signature(function)
        self._emit_type_header()
        self._emit_default_constructor()
        self._bind_generators(functions)
        self._bind_context_managers(functions)
        for function in functions:
            self._compile_method(function, constructor=False)

    @staticmethod
    def _is_type_checking(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"

    def _record_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    continue
                local_name = alias.asname or alias.name.split(".", 1)[0]
                if "." in alias.name and alias.asname is None:
                    local_name = alias.name.rsplit(".", 1)[-1]
                if not self._bind_module(alias.name, local_name, node):
                    raise self.error(node, self._missing_import(alias.name))
            return
        self._record_from_import(node)

    def _record_from_import(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            if any(alias.name != "annotations" for alias in node.names):
                raise self.error(node, "only __future__.annotations is supported")
            return
        if node.module == "typing":
            allowed = {"TYPE_CHECKING", "cast"}
            if any(alias.name not in allowed for alias in node.names):
                raise self.error(node, "only typing.TYPE_CHECKING and typing.cast are supported")
            return
        if node.module == "collections.abc":
            for alias in node.names:
                if alias.name != "Iterator":
                    raise self.error(node, "only collections.abc.Iterator is supported")
                self.imports[alias.asname or alias.name] = _JAVA_ITERATOR
            return
        if any(alias.name == "*" for alias in node.names):
            raise self.error(node, "wildcard imports are unsupported")

        if node.level:
            if node.level != 1 or not node.module:
                raise self.error(node, "only same-package relative imports are supported")
            for alias in node.names:
                if alias.name != node.module:
                    raise self.error(
                        node,
                        "relative imports must import the class named by the module",
                    )
                local_name = alias.asname or alias.name
                self.imports[local_name] = "/".join(
                    filter(None, (self.package, alias.name))
                )
            return
        if not node.module:
            raise self.error(node, "import must name a module")

        if self._bind_module_names(node):
            return
        module_internal = node.module.replace(".", "/")
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.imports[local_name] = f"{module_internal}/{alias.name}"

    def _bind_module(self, module: str, local_name: str, node: ast.AST) -> bool:
        _ = node
        internal = module.replace(".", "/")
        if internal not in self.project:
            return False
        self.imports[local_name] = internal
        return True

    def _bind_module_names(self, node: ast.ImportFrom) -> bool:
        if node.module is None:
            return False
        internal = node.module.replace(".", "/")
        found = self.project.get(internal)
        if found is None:
            return False
        for alias in node.names:
            local_name = alias.asname or alias.name
            method = found.methods.get(alias.name)
            if method is not None and method.opcode == "invokestatic":
                self.imported_functions[local_name] = (internal, alias.name)
                continue
            class_internal = alias.name if alias.name in self.project else f"{internal}/{alias.name}"
            if class_internal not in self.project:
                raise self.error(node, f"unsupported import {node.module}.{alias.name}")
            self.imports[local_name] = class_internal
        return True

    def _java_class(self, internal: str) -> bool:
        if self.catalog.super_of(internal) is not None or self.catalog.is_interface(internal):
            return True
        return bool(self.catalog.methods_of(internal))

    def _missing_import(self, name: str) -> str:
        from Py2APK.module_resolve import describe_search

        return f"unsupported import {name!r}; looked in {describe_search(name, self.project_root)}"

    def _begin_type(self) -> None:
        self.fields = {}
        self.methods = {}
        self.generator_names = {}
        self.context_names = {}
        self.context_elements = {}
        self.lines = []
        self.super_internal = "java/lang/Object"

    def _emit_type_header(self) -> None:
        self.lines.extend((f".source {self.source.name}", f".class public {self.class_internal}"))
        self.lines.append(f".super {self.super_internal}")

    def _compile_class(self, node: ast.ClassDef) -> None:
        if node.decorator_list or node.keywords:
            raise self.error(node, "class decorators and class keywords are unsupported")
        self._begin_type()
        self.class_name = node.name
        self.class_internal = "/".join(filter(None, (self.package, node.name)))

        interfaces: list[str] = []
        if len(node.bases) > 1:
            raise self.error(node, "multiple base declarations are unsupported")
        if node.bases:
            base = self._resolve_type_name(node.bases[0])
            if base in self._interface_bases(base):
                interfaces.append(base)
            else:
                self.super_internal = base

        functions: list[ast.FunctionDef] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                if not isinstance(item.target, ast.Name) or item.value is not None:
                    raise self.error(item, "fields must be uninitialized annotated names")
                self.fields[item.target.id] = self._annotation(item.annotation)
            elif isinstance(item, ast.FunctionDef):
                functions.append(item)
                if item.name != "__init__":
                    signature = self._method_signature(item)
                    self.methods[item.name] = _MethodSignature(
                        signature.parameters,
                        signature.result,
                        owner=self.class_internal,
                    )
            elif isinstance(item, ast.Pass) or (
                isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
            ):
                continue
            else:
                raise self.error(item, f"unsupported class member {type(item).__name__}")

        self._emit_type_header()
        for interface in interfaces:
            self.lines.append(f".implements {interface}")
        self.lines.append("")
        for name, descriptor in self.fields.items():
            # Jasmin 3.0.3's grammar requires field declarations to use a
            # quoted string token for the field name.
            self.lines.append(f".field public {json.dumps(name)} {descriptor}")
        if self.fields:
            self.lines.append("")

        constructors = [function for function in functions if function.name == "__init__"]
        if len(constructors) > 1:
            raise self.error(node, "multiple constructors are unsupported")
        if constructors:
            if _has_yield(constructors[0]):
                raise self.error(constructors[0], "yield is unsupported in a constructor")
            self._compile_method(constructors[0], constructor=True)
        else:
            self._emit_default_constructor()
        self._bind_generators(functions)
        self._bind_context_managers(functions)
        for function in functions:
            if function.name != "__init__":
                self._compile_method(function, constructor=False)

    def _method_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        if node.decorator_list and not _is_contextmanager_def(node, self.imports):
            raise self.error(node, "method decorators are unsupported")
        if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
            raise self.error(node, "only positional method parameters are supported")
        if not node.args.args or node.args.args[0].arg != "self":
            raise self.error(node, "instance methods must begin with self")
        parameters: list[str] = []
        for argument in node.args.args[1:]:
            if argument.annotation is None:
                raise self.error(argument, f"parameter {argument.arg!r} requires a type annotation")
            parameters.append(self._annotation(argument.annotation))
        if node.returns is None:
            raise self.error(node, "methods require a return annotation")
        return _MethodSignature(tuple(parameters), self._annotation(node.returns))

    def _function_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        if node.decorator_list and not _is_contextmanager_def(node, self.imports):
            raise self.error(node, "function decorators are unsupported")
        if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
            raise self.error(node, "only positional function parameters are supported")
        parameters: list[str] = []
        for argument in node.args.args:
            if argument.annotation is None:
                raise self.error(argument, f"parameter {argument.arg!r} requires a type annotation")
            parameters.append(self._annotation(argument.annotation))
        if node.returns is None:
            raise self.error(node, "functions require a return annotation")
        return _MethodSignature(
            tuple(parameters),
            self._annotation(node.returns),
            opcode="invokestatic",
            owner=self.class_internal,
        )

    def _bind_generators(self, functions: list[ast.FunctionDef]) -> None:
        serial = 0
        for function in functions:
            if function.name == "__init__" or not _has_yield(function):
                continue
            if _is_contextmanager_def(function, self.imports):
                continue
            element = self.methods[function.name].result
            yielded = _iterator_slice(function.returns, self.imports)
            if yielded is not None:
                element = self._annotation(yielded)
            if element == "V":
                raise self.error(function, "a generator requires an element type")
            serial += 1
            synthetic = f"{self.class_internal}$gen${serial}"
            self.generator_names[function.name] = synthetic
            self.generator_elements[synthetic] = element
            current = self.methods[function.name]
            self.methods[function.name] = _MethodSignature(
                current.parameters,
                _descriptor(synthetic),
                opcode=current.opcode,
                owner=current.owner,
            )

    def _bind_context_managers(self, functions: list[ast.FunctionDef]) -> None:
        serial = 0
        for function in functions:
            if function.name == "__init__" or not _is_contextmanager_def(function, self.imports):
                continue
            element = self.methods[function.name].result
            if element == "V":
                raise self.error(function, "contextmanager requires a yielded type")
            serial += 1
            synthetic = f"{self.class_internal}$cm${serial}"
            self.context_names[function.name] = synthetic
            self.context_elements[synthetic] = element
            current = self.methods[function.name]
            self.methods[function.name] = _MethodSignature(
                current.parameters,
                _descriptor(synthetic),
                opcode=current.opcode,
                owner=current.owner,
            )
            self.project[synthetic] = _context_manager_type(synthetic, element)

    def _compile_method(self, node: ast.FunctionDef, *, constructor: bool) -> None:
        if not constructor and node.name in self.context_names:
            self._compile_context_method(node)
            return
        if not constructor and node.name in self.generator_names:
            self._compile_generator_method(node)
            return
        if not constructor and self.class_internal == "py2apk" and node.name == "chr":
            self._compile_chr_intrinsic(node)
            return
        signature = self._constructor_signature(node) if constructor else self.methods[node.name]
        self.method_result = signature.result
        self._bind_parameters(node, signature, static=signature.opcode == "invokestatic")
        self._classify_lists(node.body)

        if constructor:
            self._instruction("aload_0")
            self._instruction(f"invokespecial {self.super_internal}/<init>()V")
        for statement in node.body:
            self._statement(statement)
        if signature.result == "V":
            self._instruction("return")
        else:
            self._emit_default(signature.result)
            self._instruction(_return_opcode(signature.result))

        method_name = "<init>" if constructor else node.name
        access = "public static" if signature.opcode == "invokestatic" else "public"
        self.lines.append(f".method {access} {method_name}{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(self.next_local, 1)}")
        self.lines.extend(self.instructions)
        self.lines.append(".end method")
        self.lines.append("")

    def _compile_chr_intrinsic(self, node: ast.FunctionDef) -> None:
        signature = self.methods[node.name]
        self.method_result = signature.result
        self._bind_parameters(node, signature, static=True)
        self._load_local(node.args.args[0].arg)
        self._emit_char_string()
        self._instruction("areturn")
        self.lines.append(f".method public static chr{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(self.next_local, 1)}")
        self.lines.extend(self.instructions)
        self.lines.append(".end method")
        self.lines.append("")

    def _constructor_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        signature = self._method_signature(node)
        if signature.result != "V":
            raise self.error(node, "constructors must return None")
        return signature

    def _emit_default_constructor(self) -> None:
        self.lines.extend(
            (
                ".method public <init>()V",
                "    .limit stack 1",
                "    .limit locals 1",
                "    aload_0",
                f"    invokespecial {self.super_internal}/<init>()V",
                "    return",
                ".end method",
                "",
            )
        )

    def _statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                raise self.error(node, "chained assignments are unsupported")
            self._assign(node.targets[0], node.value)
        elif isinstance(node, ast.AnnAssign):
            if node.value is None:
                raise self.error(node, "local annotated assignments require a value")
            descriptor = self._assigned_value(node.target, node.value)
            declared = self._annotation(node.annotation)
            jvm = _jvm_descriptor(descriptor)
            if jvm != declared:
                if jvm.startswith("L") and declared.startswith("L"):
                    self._instruction(f"checkcast {_internal_name(declared)}")
                    descriptor = declared
                else:
                    raise self.error(node, f"assignment type {descriptor} does not match {declared}")
            self._store(node.target, descriptor)
        elif self._lower_control(node):
            return
        else:
            raise self.error(node, f"unsupported statement {type(node).__name__}")

    def _lower_control(self, node: ast.stmt) -> bool:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield):
            self._emit_yield(node.value)
            return True
        if isinstance(node, ast.Expr):
            descriptor = self._expression(node.value)
            if descriptor != "V":
                self._instruction("pop2" if descriptor in {"D", "J"} else "pop")
            return True
        if isinstance(node, ast.If):
            self._if_statement(node)
            return True
        if isinstance(node, ast.While):
            self._while_statement(node)
            return True
        if isinstance(node, ast.For):
            self._for_statement(node)
            return True
        if isinstance(node, ast.Pass):
            return True
        if isinstance(node, ast.Break):
            self._jump_loop(node, breaking=True)
            return True
        if isinstance(node, ast.Continue):
            self._jump_loop(node, breaking=False)
            return True
        if isinstance(node, ast.Return):
            self._emit_return(node)
            return True
        if isinstance(node, ast.Try):
            self._try_statement(node)
            return True
        if isinstance(node, ast.Raise):
            self._raise_statement(node)
            return True
        if isinstance(node, ast.With):
            self._with_statement(node)
            return True
        return False

    def _jump_loop(self, node: ast.AST, *, breaking: bool) -> None:
        if not self.loops:
            name = "break" if breaking else "continue"
            raise self.error(node, f"{name} outside a loop")
        continue_label, break_label = self.loops[-1]
        self._instruction(f"goto {break_label if breaking else continue_label}")

    def _try_statement(self, node: ast.Try) -> None:
        start = self._label("try")
        end = self._label("tryend")
        after = self._label("aftertry")
        else_label = self._label("tryelse") if node.orelse else None
        final_label = self._label("finally") if node.finalbody else None
        bound = [
            (handler, self._label("catch"), self._except_types(handler))
            for handler in node.handlers
        ]
        catchall = None
        if node.finalbody and not any(handler.type is None for handler in node.handlers):
            catchall = self._label("catchall")
        for _handler, label, types in bound:
            for catch_type in types:
                self._instruction(
                    f".catch {catch_type} from {start} to {end} using {label}",
                    indented=False,
                )
        if catchall is not None:
            self._instruction(
                f".catch java/lang/Throwable from {start} to {end} using {catchall}",
                indented=False,
            )
        body_resume = else_label or final_label or after
        handler_resume = final_label or after
        self._instruction(f"{start}:", indented=False)
        defined = set(self.locals)
        mark = len(self.instructions)
        for statement in node.body:
            self._statement(statement)
        self._define_born(mark, defined)
        self._instruction(f"{end}:", indented=False)
        self._instruction(f"goto {body_resume}")
        for handler, label, types in bound:
            self._emit_handler(handler, label, handler_resume, types)
        if catchall is not None:
            self._emit_rethrow(catchall, node.finalbody)
        if else_label is not None:
            self._instruction(f"{else_label}:", indented=False)
            for statement in node.orelse:
                self._statement(statement)
        if final_label is not None:
            self._instruction(f"{final_label}:", indented=False)
            for statement in node.finalbody:
                self._statement(statement)
        self._instruction(f"{after}:", indented=False)

    def _except_types(self, handler: ast.ExceptHandler) -> list[str]:
        if handler.type is None:
            return ["java/lang/Throwable"]
        if not isinstance(handler.type, ast.Tuple):
            return [self._resolve_type_name(handler.type)]
        if not handler.type.elts:
            raise self.error(handler.type, "except tuple is empty")
        found: list[str] = []
        for item in handler.type.elts:
            if isinstance(item, ast.Tuple | ast.Starred):
                raise self.error(item, "except tuple entries must be type names")
            catch_type = self._resolve_type_name(item)
            if catch_type not in found:
                found.append(catch_type)
        return found

    def _emit_handler(
        self,
        handler: ast.ExceptHandler,
        label: str,
        resume: str,
        types: list[str],
    ) -> None:
        self._instruction(f"{label}:", indented=False)
        stored = types[0] if len(types) == 1 else "java/lang/Throwable"
        descriptor = _descriptor(stored)
        caught = handler.name if handler.name else self._cursor_name("exc")
        self._keep(caught, descriptor, handler)
        self.handlers.append(caught)
        for statement in handler.body:
            self._statement(statement)
        self.handlers.pop()
        self._instruction(f"goto {resume}")

    def _emit_rethrow(self, label: str, statements: list[ast.stmt]) -> None:
        self._instruction(f"{label}:", indented=False)
        caught = self._cursor_name("exc")
        self._keep(caught, "Ljava/lang/Throwable;", statements[0])
        self.handlers.append(caught)
        for statement in statements:
            self._statement(statement)
        self.handlers.pop()
        self._load_local(caught)
        self._instruction("athrow")

    def _raise_statement(self, node: ast.Raise) -> None:
        if node.exc is None:
            if node.cause is not None:
                raise self.error(node, "bare raise cannot have a cause")
            if not self.handlers:
                raise self.error(node, "bare raise outside a handler")
            self._load_local(self.handlers[-1])
            self._instruction("athrow")
            return
        descriptor = self._expression(node.exc)
        if not self._is_throwable(descriptor):
            raise self.error(node.exc, "raise needs a throwable")
        if node.cause is not None:
            self._put_cause(node, descriptor)
        self._instruction("athrow")

    def _put_cause(self, node: ast.Raise, descriptor: str) -> None:
        cause_node = node.cause
        if cause_node is None:
            raise self.error(node, "raise cause is missing")
        owner = _internal_name(descriptor)
        typed = self.project.get(owner)
        field_type = typed.fields.get("cause") if typed is not None else None
        if field_type is None:
            raise self.error(node, f"{owner} has no cause field")
        self._instruction("dup")
        cause = self._expression(cause_node)
        if not self._compatible(cause, field_type):
            raise self.error(cause_node, f"cause expects {field_type}, got {cause}")
        self._instruction(f"putfield {owner}/cause {field_type}")

    def _is_throwable(self, descriptor: str) -> bool:
        if not descriptor.startswith("L") or not descriptor.endswith(";"):
            return False
        name = _internal_name(descriptor)
        seen: set[str] = set()
        while name and name not in seen:
            if name in {
                "java/lang/Throwable",
                "java/lang/Exception",
                "java/lang/Error",
                "java/lang/RuntimeException",
            }:
                return True
            seen.add(name)
            local = self.project.get(name)
            base = local.base if local is not None else None
            if name == self.class_internal:
                base = base or self.super_internal
            if base:
                name = base
                continue
            name = self.catalog.super_of(name) or ""
        return False

    def _with_statement(self, node: ast.With) -> None:
        self._with_items(node.items, node.body)

    def _with_items(self, items: list[ast.withitem], body: list[ast.stmt]) -> None:
        item = items[0]
        catches = self._suppress_catches(item.context_expr)
        if catches is not None:
            self._with_suppress(catches, items, body)
            return
        descriptor = self._expression(item.context_expr)
        if not descriptor.startswith("L"):
            raise self.error(item.context_expr, "with needs an object")
        manager = self._cursor_name("with")
        self._keep(manager, descriptor, item.context_expr)
        owner = _internal_name(descriptor)
        enter = self._protocol(owner, "__enter__", item.context_expr)
        if enter.parameters:
            raise self.error(item.context_expr, "__enter__ takes no arguments")
        self._load_local(manager)
        self._instruction(f"invokevirtual {enter.owner}/__enter__{enter.descriptor}")
        if item.optional_vars is None:
            self._pop_result(enter.result)
        elif isinstance(item.optional_vars, ast.Name):
            self._keep(item.optional_vars.id, enter.result, item.optional_vars)
        else:
            raise self.error(item.optional_vars, "with target must be a name")
        leave = self._protocol(owner, "__exit__", item.context_expr)
        start = self._label("with")
        end = self._label("withend")
        handler = self._label("withexc")
        after = self._label("withafter")
        self._instruction(
            f".catch java/lang/Throwable from {start} to {end} using {handler}",
            indented=False,
        )
        self._instruction(f"{start}:", indented=False)
        defined = set(self.locals)
        mark = len(self.instructions)
        if len(items) > 1:
            self._with_items(items[1:], body)
        else:
            for statement in body:
                self._statement(statement)
        self._define_born(mark, defined)
        self._instruction(f"{end}:", indented=False)
        self._emit_exit(manager, leave, None)
        self._instruction(f"goto {after}")
        self._instruction(f"{handler}:", indented=False)
        caught = self._cursor_name("exc")
        self._keep(caught, "Ljava/lang/Throwable;", item.context_expr)
        self.handlers.append(caught)
        self._emit_exit(manager, leave, caught)
        self.handlers.pop()
        self._instruction(f"{after}:", indented=False)

    def _suppress_catches(self, node: ast.expr) -> list[str] | None:
        if not isinstance(node, ast.Call) or not self._names_imported(node.func, _SUPPRESS):
            return None
        if node.keywords:
            raise self.error(node, "keyword arguments are unsupported")
        catches: list[str] = []
        for argument in node.args:
            if not self._is_type_reference(argument):
                raise self.error(argument, "suppress expects a type name")
            catches.append(self._resolve_type_name(argument))
        return catches

    def _names_imported(self, node: ast.expr, internal: str) -> bool:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id) == internal
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and self.imports.get(node.value.id) == internal.rsplit("/", 1)[0]
            and node.attr == internal.rsplit("/", 1)[-1]
        )

    def _with_suppress(self, catches: list[str], items: list[ast.withitem], body: list[ast.stmt]) -> None:
        start = self._label("suppress")
        end = self._label("suppressend")
        handler = self._label("suppressed")
        after = self._label("suppressafter")
        for catch_type in catches:
            self._instruction(
                f".catch {catch_type} from {start} to {end} using {handler}",
                indented=False,
            )
        self._instruction(f"{start}:", indented=False)
        defined = set(self.locals)
        mark = len(self.instructions)
        if len(items) > 1:
            self._with_items(items[1:], body)
        else:
            for statement in body:
                self._statement(statement)
        self._define_born(mark, defined)
        self._instruction(f"{end}:", indented=False)
        self._instruction(f"goto {after}")
        if catches:
            self._instruction(f"{handler}:", indented=False)
            self._instruction("pop")
            self._instruction(f"goto {after}")
        self._instruction(f"{after}:", indented=False)

    def _emit_exit(self, manager: str, method: _MethodSignature, thrown: str | None) -> None:
        self._load_local(manager)
        for index, parameter in enumerate(method.parameters):
            if parameter in {"I", "Z", "F", "D", "J"}:
                raise JasminCompilerError("__exit__ parameters must be objects")
            if index == 1 and thrown is not None:
                self._load_local(thrown)
            else:
                self._instruction("aconst_null")
        self._instruction(f"invokevirtual {method.owner}/__exit__{method.descriptor}")
        if thrown is None:
            self._pop_result(method.result)
            return
        if method.result == "Z":
            skip = self._label("suppress")
            self._instruction(f"ifne {skip}")
            self._load_local(thrown)
            self._instruction("athrow")
            self._instruction(f"{skip}:", indented=False)
            return
        self._pop_result(method.result)
        self._load_local(thrown)
        self._instruction("athrow")

    def _pop_result(self, descriptor: str) -> None:
        if descriptor == "V":
            return
        self._instruction("pop2" if descriptor in {"D", "J"} else "pop")

    def _protocol(self, owner: str, name: str, node: ast.AST) -> _MethodSignature:
        typed = self.project.get(owner)
        method = typed.methods.get(name) if typed is not None else None
        if method is None:
            raise self.error(node, f"{owner} has no {name}")
        return method

    def _bind_parameters(
        self,
        node: ast.FunctionDef,
        signature: _MethodSignature,
        *,
        static: bool,
    ) -> None:
        self.instructions = []
        self.list_elements = {}
        self.loops = []
        self.handlers = []
        if static:
            self.locals = {}
            self.next_local = 0
            self.preview_locals = {}
            arguments = node.args.args
        else:
            self.locals = {"self": (0, _descriptor(self.class_internal))}
            self.next_local = 1
            self.preview_locals = {"self": _descriptor(self.class_internal)}
            arguments = node.args.args[1:]
        for argument, descriptor in zip(arguments, signature.parameters, strict=True):
            self.locals[argument.arg] = (self.next_local, descriptor)
            self.preview_locals[argument.arg] = descriptor
            self.next_local += 2 if descriptor in {"D", "J"} else 1

    def _define_born(self, mark: int, defined: set[str]) -> None:
        """Store a default for locals first assigned inside a protected region.

        The handler joins the code after the region. A local stored only in
        the body is undefined on that path, and D8 rejects the method.
        """
        prefix: list[str] = []
        for name in self.locals:
            if name in defined:
                continue
            slot, descriptor = self.locals[name]
            saved = self.instructions
            self.instructions = []
            self._emit_default(descriptor)
            self._instruction(f"{self._store_opcode(descriptor)} {slot}")
            prefix.extend(self.instructions)
            self.instructions = saved
        if prefix:
            self.instructions[mark:mark] = prefix

    def _emit_default(self, descriptor: str) -> None:
        if descriptor in {"I", "Z"}:
            self._instruction("iconst_0")
            return
        if descriptor == "F":
            self._instruction("fconst_0")
            return
        if descriptor == "D":
            self._instruction("dconst_0")
            return
        if descriptor == "J":
            self._instruction("lconst_0")
            return
        self._instruction("aconst_null")

    def _emit_return(self, node: ast.Return) -> None:
        if self.generator_internal is not None:
            if node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None
            ):
                raise self.error(node, "a generator return cannot carry a value")
            if self.generator_exhausted is None:
                raise self.error(node, "yield is only supported in a generator method")
            self._instruction(f"goto {self.generator_exhausted}")
            return
        if node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None):
            if self.method_result != "V":
                raise self.error(node, "this return needs a value")
            self._instruction("return")
            return
        actual = self._expression(node.value)
        if actual != self.method_result and not self._compatible(actual, self.method_result):
            raise self.error(node, f"return type {actual} does not match {self.method_result}")
        self._instruction(_return_opcode(self.method_result))

    def _if_statement(self, node: ast.If) -> None:
        else_label = self._label("else")
        end_label = self._label("endif")
        self._condition_jump_false(node.test, else_label)
        for statement in node.body:
            self._statement(statement)
        self._instruction(f"goto {end_label}")
        self._instruction(f"{else_label}:", indented=False)
        for statement in node.orelse:
            self._statement(statement)
        self._instruction(f"{end_label}:", indented=False)

    def _while_statement(self, node: ast.While) -> None:
        test_label = self._label("while")
        end_label = self._label("endwhile")
        break_label = self._label("break") if node.orelse else end_label
        self._instruction(f"{test_label}:", indented=False)
        self._condition_jump_false(node.test, end_label)
        self.loops.append((test_label, break_label))
        for statement in node.body:
            self._statement(statement)
        self.loops.pop()
        self._instruction(f"goto {test_label}")
        self._instruction(f"{end_label}:", indented=False)
        for statement in node.orelse:
            self._statement(statement)
        if node.orelse:
            self._instruction(f"{break_label}:", indented=False)

    def _for_statement(self, node: ast.For) -> None:
        if not isinstance(node.target, ast.Name):
            raise self.error(node.target, "for target must be a name")
        iterable = node.iter
        bounds = iterable.slice if isinstance(iterable, ast.Subscript) else None
        if isinstance(iterable, ast.Subscript):
            if not isinstance(bounds, ast.Slice):
                raise self.error(iterable, "subscript is unsupported")
            iterable = iterable.value
        descriptor = self._expression(iterable)
        if descriptor == "Ljava/util/Iterator;":
            self.generator_elements["java/util/Iterator"] = "I"
        if self._is_generator_type(descriptor):
            if isinstance(node.iter, ast.Subscript):
                raise self.error(node.iter, "a slice of a generator is unsupported")
            self._for_generator(node, descriptor)
            return
        element = self._sequence_element(descriptor)
        if element is None:
            raise self.error(node.iter, "for requires an array, a list, or a generator")
        self._for_index(node, descriptor, element, bounds if isinstance(bounds, ast.Slice) else None)

    def _for_generator(self, node: ast.For, descriptor: str) -> None:
        if not isinstance(node.target, ast.Name):
            raise self.error(node.target, "for target must be a name")
        owner = _internal_name(descriptor)
        element = self.generator_elements[owner]
        iterator = self._cursor_name("it")
        self._keep(iterator, descriptor, node)
        test_label = self._label("for")
        next_label = self._label("fornext")
        got_label = self._label("forgot")
        handler_label = self._label("endfor")
        break_label = self._label("break")
        self._instruction(
            f".catch java/lang/NoSuchElementException from {next_label} to {got_label} using {handler_label}",
            indented=False,
        )
        self._instruction(f"{test_label}:", indented=False)
        self._load_local(iterator)
        self._instruction(f"{next_label}:", indented=False)
        self._instruction(f"invokevirtual {owner}/next()Ljava/lang/Object;")
        self._instruction(f"{got_label}:", indented=False)
        _ = self._restore_yield_type(element)
        self._store(node.target, element)
        self.loops.append((test_label, break_label))
        for statement in node.body:
            self._statement(statement)
        self.loops.pop()
        self._instruction(f"goto {test_label}")
        self._instruction(f"{handler_label}:", indented=False)
        self._instruction("pop")
        for statement in node.orelse:
            self._statement(statement)
        self._instruction(f"{break_label}:", indented=False)

    def _for_index(
        self,
        node: ast.For,
        descriptor: str,
        element: str,
        bounds: ast.Slice | None,
    ) -> None:
        if not isinstance(node.target, ast.Name):
            raise self.error(node.target, "for target must be a name")
        sequence = self._cursor_name("seq")
        length = self._cursor_name("len")
        step = self._cursor_name("step")
        start = self._cursor_name("start")
        stop = self._cursor_name("stop")
        index = self._cursor_name("idx")
        self._keep(sequence, descriptor, node)
        self._emit_sequence_length(sequence, descriptor)
        self._keep(length, "I", node)
        self._emit_slice_step(bounds, node)
        self._keep(step, "I", node)
        self._reject_zero_step(bounds, step, node)
        self._emit_slice_bound(None if bounds is None else bounds.lower, is_start=True, length=length, step=step)
        self._keep(start, "I", node)
        self._emit_slice_bound(None if bounds is None else bounds.upper, is_start=False, length=length, step=step)
        self._keep(stop, "I", node)
        self._load_local(start)
        self._keep(index, "I", node)
        test_label = self._label("for")
        down_label = self._label("fordown")
        body_label = self._label("forbody")
        continue_label = self._label("forcont")
        end_label = self._label("endfor")
        break_label = self._label("break") if node.orelse else end_label
        self._instruction(f"{test_label}:", indented=False)
        self._load_local(step)
        self._instruction(f"ifle {down_label}")
        self._load_local(index)
        self._load_local(stop)
        self._instruction(f"if_icmpge {end_label}")
        self._instruction(f"goto {body_label}")
        self._instruction(f"{down_label}:", indented=False)
        self._load_local(index)
        self._load_local(stop)
        self._instruction(f"if_icmple {end_label}")
        self._instruction(f"{body_label}:", indented=False)
        self._emit_element(sequence, descriptor, index, element)
        self._store(node.target, element)
        self.loops.append((continue_label, break_label))
        for statement in node.body:
            self._statement(statement)
        self.loops.pop()
        self._instruction(f"{continue_label}:", indented=False)
        self._load_local(index)
        self._load_local(step)
        self._instruction("iadd")
        self._keep(index, "I", node)
        self._instruction(f"goto {test_label}")
        self._instruction(f"{end_label}:", indented=False)
        for statement in node.orelse:
            self._statement(statement)
        if node.orelse:
            self._instruction(f"{break_label}:", indented=False)

    def _emit_slice_step(self, bounds: ast.Slice | None, _node: ast.AST) -> None:
        step = None if bounds is None else bounds.step
        if step is None:
            self._emit_int(1)
            return
        literal = _literal_int(step)
        if literal is not None:
            if literal == 0:
                raise self.error(step, "slice step cannot be zero")
            self._emit_int(literal)
            return
        self._emit_int_expr(step)
        ok = self._label("stepok")
        self._instruction("dup")
        self._instruction(f"ifne {ok}")
        self._instruction("pop")
        self._instruction("new java/lang/IllegalArgumentException")
        self._instruction("dup")
        self._instruction("invokespecial java/lang/IllegalArgumentException/<init>()V")
        self._instruction("athrow")
        self._instruction(f"{ok}:", indented=False)

    def _emit_int_expr(self, node: ast.expr) -> None:
        literal = _literal_int(node)
        if literal is not None:
            self._emit_int(literal)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            self._emit_int_expr(node.operand)
            self._instruction("ineg")
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            self._emit_int_expr(node.operand)
            return
        actual = self._expression(node)
        if actual != "I":
            raise self.error(node, "slice bound must be int")

    def _reject_zero_step(self, bounds: ast.Slice | None, step: str, node: ast.AST) -> None:
        _ = node
        step_expr = None if bounds is None else bounds.step
        if step_expr is None or _literal_int(step_expr) is not None:
            return
        bad = self._label("badstep")
        ok = self._label("stepok")
        self._load_local(step)
        self._instruction(f"ifne {ok}")
        self._instruction(f"{bad}:", indented=False)
        self._instruction("new java/lang/IllegalArgumentException")
        self._instruction("dup")
        self._instruction("invokespecial java/lang/IllegalArgumentException/<init>()V")
        self._instruction("athrow")
        self._instruction(f"{ok}:", indented=False)

    def _emit_slice_bound(
        self,
        bound: ast.expr | None,
        *,
        is_start: bool,
        length: str,
        step: str,
    ) -> None:
        self._load_local(length)
        if bound is None:
            self._emit_int(0)
        else:
            self._emit_int_expr(bound)
        self._load_local(step)
        self._emit_int(0 if bound is None else 1)
        name = "slice_start" if is_start else "slice_stop"
        self._instruction(f"invokestatic py2apk/{name}(IIII)I")

    def _emit_sequence_length(self, sequence: str, descriptor: str) -> None:
        self._load_local(sequence)
        if descriptor.startswith("["):
            self._instruction("arraylength")
            return
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/size()I")

    def _emit_element(self, sequence: str, descriptor: str, index: str, element: str) -> None:
        self._load_local(sequence)
        self._load_local(index)
        if descriptor.startswith("["):
            self._instruction(_ARRAY_LOAD.get(element, "aaload"))
            return
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/get(I)Ljava/lang/Object;")
        _ = self._restore_yield_type(element)

    def _keep(self, name: str, descriptor: str, node: ast.AST) -> None:
        target = ast.Name(id=name, ctx=ast.Store())
        ast.copy_location(target, node)
        self._store(target, descriptor)

    def _cursor_name(self, role: str) -> str:
        self.for_serial += 1
        return f"py2apk_for_{role}_{self.for_serial}"

    def _is_generator_type(self, descriptor: str) -> bool:
        return descriptor.startswith("L") and descriptor.endswith(";") and _internal_name(descriptor) in self.generator_elements

    def _sequence_element(self, descriptor: str) -> str | None:
        listed = _list_element(descriptor)
        if listed is not None:
            return listed
        if descriptor.startswith("["):
            return descriptor[1:]
        return None

    def _subscript_load(self, node: ast.Subscript) -> str:
        if isinstance(node.slice, ast.Slice):
            raise self.error(node, "a slice is only supported as a for iterable")
        sequence = self._expression(node.value)
        index = self._expression(node.slice)
        if index != "I":
            raise self.error(node.slice, "a subscript index must be int")
        if sequence == _STRING:
            self._instruction("invokevirtual java/lang/String/charAt(I)C")
            return "I"
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(node, "subscript needs an array, a list, or a string")
        if sequence.startswith("["):
            self._instruction(_ARRAY_LOAD.get(element, "aaload"))
            return element
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/get(I)Ljava/lang/Object;")
        return self._restore_yield_type(element)

    def _subscript_store(self, target: ast.Subscript, value: ast.expr) -> None:
        if isinstance(target.slice, ast.Slice):
            raise self.error(target, "a slice is only supported as a for iterable")
        sequence = self._type_of(target.value)
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(target, "subscript assignment needs an array or a list")
        produced = self._expression(value)
        if produced != element and not self._compatible(produced, element):
            raise self.error(value, f"element type {produced} does not match {element}")
        held = self._hold_temp(produced)
        _ = self._expression(target.value)
        sequence_name = self._hold_temp(sequence)
        index = self._expression(target.slice)
        if index != "I":
            raise self.error(target.slice, "a subscript index must be int")
        index_name = self._hold_temp("I")
        self._load_local(sequence_name)
        self._load_local(index_name)
        self._load_local(held)
        if sequence.startswith("["):
            self._instruction(_ARRAY_STORE.get(element, "aastore"))
            return
        if not element.startswith(("L", "[")):
            self._box(element)
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/set(ILjava/lang/Object;)Ljava/lang/Object;")
        self._instruction("pop")

    def _condition_jump_false(self, node: ast.expr, label: str) -> None:
        if isinstance(node, ast.BoolOp):
            self._bool_jump_false(node, label)
            return
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise self.error(node, "only single comparisons are supported")
            left, right, swapped = self._comparison_operands(node)
            ordered = {
                ast.Lt: "if_icmpge",
                ast.LtE: "if_icmpgt",
                ast.Gt: "if_icmple",
                ast.GtE: "if_icmplt",
            }
            opcode = ordered.get(type(node.ops[0]))
            if opcode is not None:
                if left not in {"I", "Z"} or right not in {"I", "Z"}:
                    raise self.error(node, "only integer/boolean comparisons are supported")
                self._instruction(f"{opcode} {label}")
                return
            if isinstance(node.ops[0], ast.Eq):
                self._equality_jump(node, left, right, label, equal=True, swapped=swapped)
                return
            if isinstance(node.ops[0], ast.NotEq):
                self._equality_jump(node, left, right, label, equal=False, swapped=swapped)
                return
            if isinstance(node.ops[0], ast.Is):
                self._identity_jump(node, left, right, label, same=True, swapped=swapped)
                return
            if isinstance(node.ops[0], ast.IsNot):
                self._identity_jump(node, left, right, label, same=False, swapped=swapped)
                return
            raise self.error(node, f"unsupported comparison {type(node.ops[0]).__name__}")
        negated = False
        operand = node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            negated = True
            operand = node.operand
        preview = self._type_of(operand)
        if negated or self._supports_truth(preview):
            self._truth_jump_false(operand, label, negated=negated)
            return
        descriptor = self._expression(node)
        if descriptor not in {"I", "Z"}:
            raise self.error(node, "if condition must be bool or int")
        self._instruction(f"ifeq {label}")

    def _bool_jump_false(self, node: ast.BoolOp, label: str) -> None:
        """Jump to ``label`` when ``and`` or ``or`` is false. The right arm is skipped when it cannot change the result."""
        if isinstance(node.op, ast.And):
            for value in node.values:
                self._condition_jump_false(value, label)
            return
        if isinstance(node.op, ast.Or):
            done = self._label("or")
            for value in node.values[:-1]:
                nxt = self._label("or")
                self._condition_jump_false(value, nxt)
                self._instruction(f"goto {done}")
                self._instruction(f"{nxt}:", indented=False)
            self._condition_jump_false(node.values[-1], label)
            self._instruction(f"{done}:", indented=False)
            return
        raise self.error(node, f"unsupported boolean operator {type(node.op).__name__}")

    def _supports_truth(self, descriptor: str) -> bool:
        return self._bool_method(descriptor) is not None or self._len_accepted(descriptor)

    def _truth_jump_false(self, node: ast.expr, label: str, *, negated: bool) -> None:
        """Jump to ``label`` when the value is false, or true when ``negated``."""
        descriptor = self._type_of(node)
        boolean = self._bool_method(descriptor)
        if boolean is None and not self._len_accepted(descriptor):
            raise self.error(node, f"not does not accept {descriptor}")
        _ = self._expression(node)
        present = self._label("present")
        self._instruction("dup")
        self._instruction(f"ifnull {present if negated else label}")
        if boolean is not None:
            self._invoke(boolean, "__bool__")
        else:
            self._emit_len(node, descriptor)
        self._instruction(f"{'ifne' if negated else 'ifeq'} {label}")
        if negated:
            self._instruction(f"{present}:", indented=False)

    def _bool_method(self, descriptor: str) -> _MethodSignature | None:
        jvm = _jvm_descriptor(descriptor)
        if not (jvm.startswith("L") and jvm.endswith(";")):
            return None
        found = self._find_method(_internal_name(jvm), "__bool__", (), static=False)
        if found is None or found.result != "Z":
            return None
        return found

    def _len_accepted(self, descriptor: str) -> bool:
        return (
            descriptor == _STRING
            or descriptor.startswith("[")
            or _list_element(descriptor) is not None
            or (descriptor != "null" and self._compatible(descriptor, "Ljava/lang/CharSequence;"))
        )

    def _emit_len(self, node: ast.AST, descriptor: str) -> None:
        """Replace the value on the stack with its length."""
        if descriptor == _STRING:
            signature = self._require_method("java/lang/String", "length", (), static=False, node=node)
            self._invoke(signature, "length")
            return
        if descriptor != "null" and self._compatible(descriptor, "Ljava/lang/CharSequence;"):
            signature = self._require_method("java/lang/CharSequence", "length", (), static=False, node=node)
            self._invoke(signature, "length")
            return
        if descriptor.startswith("[") or _list_element(descriptor) is not None:
            if _list_element(descriptor) is not None:
                signature = self._require_method(_ARRAY_LIST_OWNER, "size", (), static=False, node=node)
                self._invoke(signature, "size")
            else:
                self._instruction("arraylength")
            return
        raise self.error(node, f"len() does not accept {descriptor}")

    def _comparison_operands(self, node: ast.Compare) -> tuple[str, str, bool]:
        """Push a constant before a variable so the constant is the left operand.

        The third value is true when that swap happened, so the source
        left operand is on top of the stack.
        """
        if isinstance(node.ops[0], ast.Lt | ast.LtE | ast.Gt | ast.GtE):
            left = self._expression(node.left)
            right = self._expression(node.comparators[0])
            return left, right, False
        if isinstance(node.comparators[0], ast.Constant) and not isinstance(node.left, ast.Constant):
            right = self._expression(node.comparators[0])
            left = self._expression(node.left)
            return left, right, True
        left = self._expression(node.left)
        right = self._expression(node.comparators[0])
        return left, right, False

    def _equality_jump(
        self,
        node: ast.Compare,
        left: str,
        right: str,
        label: str,
        *,
        equal: bool,
        swapped: bool,
    ) -> None:
        if left in {"I", "Z"} and right in {"I", "Z"}:
            opcode = "if_icmpne" if equal else "if_icmpeq"
            self._instruction(f"{opcode} {label}")
            return
        if _numeric(left) and _numeric(right):
            self._numeric_equality(left, right, label, equal=equal, swapped=swapped)
            return
        if _reference(left) and _reference(right):
            self._reference_equality(left, right, label, equal=equal, swapped=swapped)
            return
        if (_reference(left) and _numeric(right)) or (_numeric(left) and _reference(right)):
            top, bottom = (left, right) if swapped else (right, left)
            self._pop_value(top)
            self._pop_value(bottom)
            if equal:
                self._instruction(f"goto {label}")
            return
        raise self.error(node, "only integer/boolean comparisons are supported")

    def _identity_jump(
        self,
        node: ast.Compare,
        left: str,
        right: str,
        label: str,
        *,
        same: bool,
        swapped: bool,
    ) -> None:
        """Jump to ``label`` when ``is`` is false, or when ``is not`` is false."""
        if not (_reference(left) and _reference(right)):
            mixed = (_reference(left) and _numeric(right)) or (
                _numeric(left) and _reference(right)
            )
            if mixed:
                top, bottom = (left, right) if swapped else (right, left)
                self._pop_value(top)
                self._pop_value(bottom)
                if same:
                    self._instruction(f"goto {label}")
                return
            raise self.error(node, "is compares references")
        left_slot, right_slot = self._park_pair(left, right, swapped)
        if left == "null" and right == "null":
            if not same:
                self._instruction(f"goto {label}")
            return
        if left == "null" or right == "null":
            slot = right_slot if left == "null" else left_slot
            opcode = "ifnonnull" if same else "ifnull"
            self._instruction(f"aload {slot}")
            self._instruction(f"{opcode} {label}")
            return
        opcode = "if_acmpne" if same else "if_acmpeq"
        self._instruction(f"aload {left_slot}")
        self._instruction(f"aload {right_slot}")
        self._instruction(f"{opcode} {label}")

    def _identity_same(self, node: ast.Compare) -> bool:
        """Return whether this comparison is ``is`` (true) or ``is not`` (false)."""
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise self.error(node, "only single comparisons are supported")
        if isinstance(node.ops[0], ast.Is):
            return True
        if isinstance(node.ops[0], ast.IsNot):
            return False
        raise self.error(node, f"unsupported expression {type(node).__name__}")

    def _identity_type(self, node: ast.Compare) -> str:
        _ = self._identity_same(node)
        left = self._type_of(node.left)
        right = self._type_of(node.comparators[0])
        mixed = (_reference(left) and _numeric(right)) or (_numeric(left) and _reference(right))
        if (_reference(left) and _reference(right)) or mixed:
            return "Z"
        raise self.error(node, "is compares references")

    def _identity_value(self, node: ast.Compare) -> str:
        """Leave 1 or 0 for an identity test. ``if`` and ``while`` keep the jump."""
        same = self._identity_same(node)
        left, right, swapped = self._comparison_operands(node)
        false_label = self._label("isfalse")
        done = self._label("isdone")
        self._identity_jump(node, left, right, false_label, same=same, swapped=swapped)
        self._instruction("iconst_1")
        self._instruction(f"goto {done}")
        self._instruction(f"{false_label}:", indented=False)
        self._instruction("iconst_0")
        self._instruction(f"{done}:", indented=False)
        return "Z"

    def _numeric_equality(
        self,
        left: str,
        right: str,
        label: str,
        *,
        equal: bool,
        swapped: bool,
    ) -> None:
        target = _equality_width(left, right)
        left_slot, right_slot = self._park_pair(left, right, swapped)
        self._load_widened(left_slot, left, target)
        self._load_widened(right_slot, right, target)
        self._instruction({"J": "lcmp", "F": "fcmpg", "D": "dcmpg"}[target])
        self._instruction(f"{'ifne' if equal else 'ifeq'} {label}")

    def _reference_equality(
        self,
        left: str,
        right: str,
        label: str,
        *,
        equal: bool,
        swapped: bool,
    ) -> None:
        left_slot, right_slot = self._park_pair(left, right, swapped)
        null_left = self._label("eqnull")
        done = self._label("eqdone")
        self._instruction(f"aload {left_slot}")
        self._instruction(f"ifnull {null_left}")
        self._instruction(f"aload {left_slot}")
        self._instruction(f"aload {right_slot}")
        self._instruction("invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z")
        self._instruction(f"{'ifeq' if equal else 'ifne'} {label}")
        self._instruction(f"goto {done}")
        self._instruction(f"{null_left}:", indented=False)
        self._instruction(f"aload {right_slot}")
        self._instruction(f"{'ifnonnull' if equal else 'ifnull'} {label}")
        self._instruction(f"{done}:", indented=False)

    def _park_pair(self, left: str, right: str, swapped: bool) -> tuple[int, int]:
        if swapped:
            left_slot = self._park(left)
            right_slot = self._park(right)
            return left_slot, right_slot
        right_slot = self._park(right)
        left_slot = self._park(left)
        return left_slot, right_slot

    def _park(self, descriptor: str) -> int:
        slot = self.next_local
        self.next_local += 2 if descriptor in {"J", "D"} else 1
        self._instruction(f"{self._store_opcode(descriptor)} {slot}")
        return slot

    def _load_widened(self, slot: int, source: str, target: str) -> None:
        kind = "I" if source in {"I", "Z"} else source
        self._instruction(f"{self._load_opcode(source)} {slot}")
        opcode = _WIDEN.get((kind, target))
        if opcode is not None:
            self._instruction(opcode)

    def _pop_value(self, descriptor: str) -> None:
        self._instruction("pop2" if descriptor in {"J", "D"} else "pop")

    def _assign(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Tuple):
            self._unpack_assign(target, value)
            return
        if isinstance(target, ast.Subscript):
            self._subscript_store(target, value)
            return
        self._store(target, self._assigned_value(target, value))

    def _unpack_assign(self, target: ast.Tuple, value: ast.expr) -> None:
        if any(isinstance(element, ast.Starred) for element in target.elts):
            raise self.error(target, "starred assignment targets are unsupported")
        if not isinstance(value, ast.Tuple):
            raise self.error(value, "unpacking requires a tuple of the same length")
        if any(isinstance(element, ast.Starred) for element in value.elts):
            raise self.error(value, "starred assignment values are unsupported")
        if len(target.elts) != len(value.elts):
            raise self.error(target, "assignment unpack length does not match")
        temps: list[tuple[str, str]] = []
        for element in value.elts:
            descriptor = self._expression(element)
            temps.append((self._hold_temp(descriptor), descriptor))
        for element, (name, descriptor) in zip(target.elts, temps, strict=True):
            self._load_local(name)
            self._store(element, descriptor)

    def _assigned_value(self, target: ast.expr, value: ast.expr) -> str:
        self.list_sink = target.id if isinstance(target, ast.Name) else None
        try:
            return self._expression(value)
        finally:
            self.list_sink = None

    def _store(self, target: ast.expr, descriptor: str) -> None:
        element = _list_element(descriptor)
        stored = _jvm_descriptor(descriptor)
        if isinstance(target, ast.Name):
            if element is None:
                self.list_elements.pop(target.id, None)
            else:
                previous = self.list_elements.get(target.id)
                if previous not in {None, element}:
                    raise self.error(target, f"cannot change the element type of {target.id!r}")
                self.list_elements[target.id] = element
            local = self.locals.get(target.id)
            if local is None:
                local = (self.next_local, stored)
                self.locals[target.id] = local
                self.next_local += 2 if stored in {"D", "J"} else 1
            elif local[1] != stored:
                raise self.error(target, f"cannot change local {target.id!r} from {local[1]} to {stored}")
            self._track_generator_local(target, stored)
            self._instruction(f"{self._store_opcode(stored)} {local[0]}")
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            field_type = self.fields.get(target.attr)
            if field_type is None:
                raise self.error(target, f"unknown field {target.attr!r}")
            if _jvm_descriptor(descriptor) != field_type and not self._compatible(descriptor, field_type):
                raise self.error(target, f"field {target.attr!r} expects {field_type}, got {descriptor}")
            # The value is already on the stack; putfield needs object, value.
            self._instruction("aload_0")
            self._instruction("swap")
            self._instruction(f"putfield {self.class_internal}/{target.attr} {field_type}")
            return
        raise self.error(target, f"unsupported assignment target {type(target).__name__}")

    def _track_generator_local(self, target: ast.Name, stored: str) -> None:
        if self.generator_fields is None or target.id.startswith("$"):
            return
        previous = self.generator_fields.get(target.id)
        if previous not in {None, stored}:
            raise self.error(target, f"cannot change local {target.id!r} from {previous} to {stored}")
        self.generator_fields[target.id] = stored

    def _expression(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            local = self.locals.get(node.id)
            if local is None:
                raise self.error(node, f"unknown local {node.id!r}")
            self._instruction(f"{self._load_opcode(local[1])} {local[0]}")
            element = self.list_elements.get(node.id)
            if element is not None:
                return f"arraylist:{element}"
            return local[1]
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Subscript):
            return self._subscript_load(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            actual = self._expression(node.operand)
            if actual != "I":
                raise self.error(node, "unary minus needs an int")
            self._instruction("ineg")
            return "I"
        if isinstance(node, ast.List):
            return self._list_literal(node)
        if isinstance(node, ast.Tuple):
            return self._tuple_literal(node)
        if isinstance(node, ast.Dict):
            return self._dict_literal(node)
        if isinstance(node, ast.Yield | ast.BinOp):
            return self._yield_or_add(node)
        if isinstance(node, ast.JoinedStr):
            return self._joined_string(node)
        if isinstance(node, ast.IfExp):
            return self._conditional(node)
        if isinstance(node, ast.Compare):
            return self._identity_value(node)
        raise self.error(node, f"unsupported expression {type(node).__name__}")

    def _yield_or_add(self, node: ast.expr) -> str:
        if isinstance(node, ast.Yield):
            raise self.error(node, "yield expressions are unsupported")
        if not isinstance(node, ast.BinOp):
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        left = self._expression(node.left)
        right = self._expression(node.right)
        if isinstance(node.op, ast.Add) and left == _STRING and right == _STRING:
            self._instruction("invokevirtual java/lang/String/concat(Ljava/lang/String;)Ljava/lang/String;")
            return _STRING
        integer = {
            ast.Add: "iadd",
            ast.Sub: "isub",
            ast.Mult: "imul",
            ast.FloorDiv: "idiv",
            ast.Mod: "irem",
        }
        opcode = integer.get(type(node.op))
        if opcode is None or left != "I" or right != "I":
            raise self.error(node, "only string concatenation and integer arithmetic are supported")
        self._instruction(opcode)
        return "I"

    def _joined_string(self, node: ast.JoinedStr) -> str:
        pieces = self._string_pieces(node)
        if any(pattern is not None for _value, pattern, _descriptor in pieces):
            return self._format_string(pieces)
        return self._builder_string(pieces)

    def _string_pieces(self, node: ast.JoinedStr) -> list[tuple[ast.expr, str | None, str]]:
        pieces: list[tuple[ast.expr, str | None, str]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append((value, None, _STRING))
                continue
            if not isinstance(value, ast.FormattedValue):
                raise self.error(value, "unsupported f-string piece")
            if value.conversion != -1:
                raise self.error(value, "format conversions are not supported")
            spec = self._format_spec(value)
            descriptor = self._type_of(value.value)
            pattern = "%s" if spec == "" else _format_pattern(spec)
            if pattern is None:
                raise self.error(value, f"format spec {spec!r} is not supported")
            self._check_format_type(value, pattern, descriptor)
            pieces.append((value.value, pattern if spec else None, descriptor))
        return pieces

    def _format_spec(self, node: ast.FormattedValue) -> str:
        spec = node.format_spec
        if spec is None:
            return ""
        if isinstance(spec, ast.Constant) and isinstance(spec.value, str):
            return spec.value
        if isinstance(spec, ast.JoinedStr):
            parts: list[str] = []
            for value in spec.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    continue
                raise self.error(node, "nested format fields are not supported")
            return "".join(parts)
        raise self.error(node, "nested format fields are not supported")

    def _check_format_type(self, node: ast.AST, pattern: str, descriptor: str) -> None:
        if pattern.endswith("d") and descriptor != "I":
            raise self.error(node, f"format spec {pattern!r} requires int")
        if pattern.endswith("f") and descriptor not in {"F", "D"}:
            raise self.error(node, f"format spec {pattern!r} requires float or double")
        if pattern == "%s":
            return
        if descriptor not in _BOX_METHODS and not descriptor.startswith(("L", "[")):
            raise self.error(node, f"cannot format type {descriptor}")

    def _builder_string(self, pieces: list[tuple[ast.expr, str | None, str]]) -> str:
        init = self._require_method(_STRING_BUILDER, "<init>", (), static=False)
        self._instruction(f"new {_STRING_BUILDER}")
        self._instruction("dup")
        self._instruction(f"invokespecial {_STRING_BUILDER}/<init>{init.descriptor}")
        for value, _pattern, descriptor in pieces:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                _ = self._constant(value)
                parameter = _STRING
            else:
                parameter = self._append_parameter(self._expression(value), descriptor)
            signature = self._require_method(_STRING_BUILDER, "append", (parameter,), static=False)
            self._invoke(signature, "append")
        finished = self._require_method(_STRING_BUILDER, "toString", (), static=False)
        self._invoke(finished, "toString")
        return _STRING

    def _append_parameter(self, actual: str, expected: str) -> str:
        descriptor = actual or expected
        if descriptor in {"I", "Z", "F", "D", "J", _STRING}:
            return descriptor
        if descriptor != "null" and self._compatible(descriptor, "Ljava/lang/CharSequence;"):
            return "Ljava/lang/CharSequence;"
        if descriptor == "null" or descriptor.startswith(("L", "[")):
            return "Ljava/lang/Object;"
        raise JasminCompilerError(f"cannot append type {descriptor}")

    def _format_string(self, pieces: list[tuple[ast.expr, str | None, str]]) -> str:
        pattern: list[str] = []
        fields: list[tuple[ast.expr, str]] = []
        for value, conversion, descriptor in pieces:
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and conversion is None:
                pattern.append(value.value.replace("%", "%%"))
                continue
            pattern.append("%s" if conversion is None else conversion)
            fields.append((value, descriptor))
        _ = self._constant(ast.Constant(value="".join(pattern)))
        count = ast.Constant(value=len(fields))
        _ = self._constant(count)
        self._instruction("anewarray java/lang/Object")
        for index, (value, _descriptor) in enumerate(fields):
            self._instruction("dup")
            _ = self._constant(ast.Constant(value=index))
            self._box(self._expression(value))
            self._instruction("aastore")
        signature = self._require_method(
            "java/lang/String",
            "format",
            (_STRING, _OBJECT_ARRAY),
            static=True,
        )
        self._instruction(f"invokestatic {signature.owner or 'java/lang/String'}/format{signature.descriptor}")
        return _STRING

    def _box(self, descriptor: str) -> None:
        if descriptor == "null" or descriptor.startswith(("L", "[")):
            return
        boxed = _BOX_METHODS.get(descriptor)
        if boxed is None:
            raise JasminCompilerError(f"cannot box type {descriptor}")
        owner, name = boxed
        signature = self._require_method(owner, name, (descriptor,), static=True)
        self._instruction(f"invokestatic {signature.owner or owner}/{name}{signature.descriptor}")

    def _emit_cast(self, node: ast.Call) -> str:
        return self._cast_descriptor(node, emit=True)

    def _cast_descriptor(self, node: ast.Call, *, emit: bool = False) -> str:
        if len(node.args) != 2:
            raise self.error(node, "cast() accepts a type and a value")
        expected = self._annotation(node.args[0])
        actual = self._expression(node.args[1]) if emit else self._type_of(node.args[1])
        if not self._compatible(actual, expected):
            raise self.error(node.args[1], f"cast expects {expected}, got {actual}")
        return expected

    def _builtin_call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, "call target must be a known constructor or method")
        name = node.func.id
        if name == "cast":
            return self._emit_cast(node)
        types = self._argument_types(node)
        if name == "str" and len(types) == 1:
            return self._static_invoke(node, "java/lang/String", "valueOf", types)
        if name in _PARSE_METHODS and len(types) == 1:
            owner, method, result = _PARSE_METHODS[name]
            if types[0] == _STRING:
                _ = self._static_invoke(node, owner, method, types)
                return result
            return self._numeric_cast(node, types[0], result)
        if name in _PARSE_METHODS:
            raise self.error(node, f"{name}() accepts one argument")
        bound = self._static_binding(name, types)
        if bound is not None:
            owner, method, signature = bound
            self._emit_arguments(node, signature.parameters)
            self._instruction(f"invokestatic {owner}/{method}{signature.descriptor}")
            return signature.result
        if name == "chr" and len(types) == 1 and types[0] == "I":
            _ = self._expression(node.args[0])
            self._instruction("invokestatic py2apk/chr(I)Ljava/lang/String;")
            return _STRING
        if name == "range" and 1 <= len(types) <= 3 and all(kind == "I" for kind in types):
            return self._emit_range(node)
        if name == "len" and len(types) == 1:
            descriptor = self._expression(node.args[0])
            self._emit_len(node, descriptor)
            return "I"
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", "abs", types)
        if name in {"min", "max"} and len(types) == 2 and types[0] == types[1] and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", name, types)
        raise self.error(node, f"unsupported builtin {name}")

    def _static_binding(
        self,
        name: str,
        types: tuple[str, ...],
    ) -> tuple[str, str, _MethodSignature] | None:
        if name in self.methods and self.methods[name].opcode == "invokestatic":
            signature = self._find_method(self.class_internal, name, types, static=True)
            if signature is not None:
                return self.class_internal, name, signature
        imported = self.imported_functions.get(name)
        if imported is None:
            return None
        owner, method = imported
        signature = self._find_method(owner, method, types, static=True)
        if signature is None:
            return None
        return owner, method, signature

    def _emit_char_string(self) -> None:
        code = self._hold_temp("I")
        self._instruction("new java/lang/String")
        self._instruction("dup")
        self._instruction("iconst_1")
        self._instruction("newarray char")
        self._instruction("dup")
        self._instruction("iconst_0")
        self._load_local(code)
        self._instruction("i2c")
        self._instruction("castore")
        self._instruction("invokespecial java/lang/String/<init>([C)V")

    def _builtin_result(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, f"unsupported expression {type(node.func).__name__}")
        name = node.func.id
        if name == "cast" and len(node.args) == 2:
            return self._cast_descriptor(node)
        types = self._argument_types(node)
        bound = self._static_binding(name, types)
        if bound is not None:
            return bound[2].result
        if name == "chr" and len(types) == 1 and types[0] == "I":
            return _STRING
        if name == "range" and 1 <= len(types) <= 3 and all(kind == "I" for kind in types):
            self.generator_elements["py2apk$gen$1"] = "I"
            return "Lpy2apk$gen$1;"
        if name == "str" and len(types) == 1:
            if self._find_method("java/lang/String", "valueOf", types, static=True) is None:
                raise self.error(node, "unknown method signature java/lang/String.valueOf")
            return _STRING
        if name in _PARSE_METHODS and len(types) == 1:
            owner, method, result = _PARSE_METHODS[name]
            if types[0] == _STRING:
                if self._find_method(owner, method, types, static=True) is None:
                    raise self.error(node, f"unknown method signature {owner}.{method}")
                return result
            if (types[0], result) not in _NUMERIC_CASTS:
                raise self.error(node, f"{name}() does not accept {types[0]}")
            return result
        if name == "len" and len(types) == 1 and self._len_accepted(types[0]):
            return "I"
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            if self._find_method("java/lang/Math", "abs", types, static=True) is None:
                raise self.error(node, "unknown method signature java/lang/Math.abs")
            return types[0]
        if name in {"min", "max"} and len(types) == 2 and types[0] == types[1] and types[0] in {"I", "F", "D"}:
            if self._find_method("java/lang/Math", name, types, static=True) is None:
                raise self.error(node, f"unknown method signature java/lang/Math.{name}")
            return types[0]
        raise self.error(node, f"unsupported builtin {name}")

    def _static_invoke(self, node: ast.Call, owner: str, name: str, types: tuple[str, ...]) -> str:
        signature = self._require_method(owner, name, types, static=True, node=node)
        self._emit_arguments(node, signature.parameters)
        declaring = signature.owner or owner
        self._instruction(f"invokestatic {declaring}/{name}{signature.descriptor}")
        return signature.result

    def _numeric_cast(self, node: ast.Call, actual: str, target: str) -> str:
        opcode = _NUMERIC_CASTS.get((actual, target))
        if opcode is None:
            raise self.error(node, f"cannot convert {actual} to {target}")
        _ = self._expression(node.args[0])
        if opcode:
            self._instruction(opcode)
        return target

    def _require_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        *,
        static: bool,
        node: ast.AST | None = None,
    ) -> _MethodSignature:
        signature = self._find_method(owner, name, arguments, static=static)
        if signature is None:
            message = f"unknown method signature {owner}.{name}"
            if node is None:
                raise JasminCompilerError(message)
            raise self.error(node, message)
        return signature

    def _invoke(self, signature: _MethodSignature, name: str) -> None:
        declaring = signature.owner or _STRING_BUILDER
        instruction = f"{signature.opcode} {declaring}/{name}{signature.descriptor}"
        if signature.opcode == "invokeinterface":
            instruction += f" {len(signature.parameters) + 1}"
        self._instruction(instruction)

    def _constant(self, node: ast.Constant) -> str:
        value = node.value
        if value is None:
            self._instruction("aconst_null")
            return "null"
        if isinstance(value, bool):
            self._instruction("iconst_1" if value else "iconst_0")
            return "Z"
        if isinstance(value, int):
            if -1 <= value <= 5:
                name = "m1" if value == -1 else str(value)
                self._instruction(f"iconst_{name}")
            elif -128 <= value <= 127:
                self._instruction(f"bipush {value}")
            elif -32768 <= value <= 32767:
                self._instruction(f"sipush {value}")
            else:
                self._instruction(f"ldc {value}")
            return "I"
        if isinstance(value, float):
            self._instruction(f"ldc {value!r}")
            return "F"
        if isinstance(value, str):
            self._instruction(f"ldc {json.dumps(value)}")
            return _STRING
        raise self.error(node, f"unsupported constant {value!r}")

    def _instance_field(self, name: str) -> tuple[str, dict[str, str]] | None:
        local = self.locals.get(name)
        if local is None or not local[1].startswith("L"):
            return None
        owner = _internal_name(local[1])
        typed = self.project.get(owner)
        if typed is None or not typed.fields:
            return None
        return owner, typed.fields

    def _attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id != "self":
            found = self._instance_field(node.value.id)
            if found is not None and node.attr in found[1]:
                self._load_local(node.value.id)
                field_type = found[1][node.attr]
                self._instruction(f"getfield {found[0]}/{node.attr} {field_type}")
                return field_type
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            field_type = self.fields.get(node.attr)
            if field_type is None:
                if node.attr in self.methods:
                    raise self.error(node, "pass the method as a listener argument, or call it")
                raise self.error(node, f"unknown field {node.attr!r}")
            if self.lambda_body:
                self._load_local("self")
            else:
                self._instruction("aload_0")
            self._instruction(f"getfield {self.class_internal}/{node.attr} {field_type}")
            return field_type

        owner = self._resolve_type_name(node.value)
        found = self.catalog.field(owner, node.attr)
        if found is None or not found[1]:
            raise self.error(node, f"unknown static field {owner}.{node.attr}")
        self._instruction(f"getstatic {owner}/{node.attr} {found[0]}")
        return found[0]

    def _call(self, node: ast.Call) -> str:
        if self._names_imported(node.func, _SUPPRESS):
            raise self.error(node, "suppress is only supported in with")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "append":
            return self._list_append(node)
        if node.keywords:
            raise self.error(node, "keyword arguments are unsupported")
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(node.func.value):
            owner = self._resolve_type_name(node.func.value)
            signature = self._find_method(owner, node.func.attr, self._argument_types(node), static=True)
            if signature is not None:
                self._emit_arguments(node, signature.parameters)
                self._instruction(f"invokestatic {signature.owner}/{node.func.attr}{signature.descriptor}")
                return signature.result
            return self._emit_constructor(f"{owner}${node.func.attr}", node)
        if self._is_type_reference(node.func):
            return self._emit_constructor(self._resolve_type_name(node.func), node)
        if not isinstance(node.func, ast.Attribute):
            return self._builtin_call(node)
        if self._is_super_call(node.func.value):
            signature = self._find_method(
                self.super_internal,
                node.func.attr,
                self._argument_types(node),
                static=False,
            )
            if signature is None:
                raise self.error(node, f"unknown super call {self.super_internal}.{node.func.attr}")
            self._instruction("aload_0")
            self._emit_arguments(node, signature.parameters)
            self._instruction(
                f"invokespecial {signature.owner or self.super_internal}/{node.func.attr}{signature.descriptor}"
            )
            return signature.result
        receiver_type = self._expression(node.func.value)
        if not receiver_type.startswith("L"):
            raise self.error(node, f"method receiver must be an object, got {receiver_type}")
        owner = _internal_name(receiver_type)
        method_name = _java_method_name(owner, node.func.attr)
        signature = self._find_method(owner, method_name, self._argument_types(node), static=False)
        if signature is None:
            raise self.error(node, f"unknown method signature {owner}.{method_name}")
        self._emit_arguments(node, signature.parameters)
        declaring = signature.owner or owner
        instruction = f"{signature.opcode} {declaring}/{method_name}{signature.descriptor}"
        if signature.opcode == "invokeinterface":
            instruction += f" {len(signature.parameters) + 1}"
        self._instruction(instruction)
        if method_name == "next" and owner in self.generator_elements:
            return self._restore_yield_type(self.generator_elements[owner])
        return signature.result

    def _emit_constructor(self, owner: str, node: ast.Call) -> str:
        if owner == _NULLCONTEXT and not node.args and not node.keywords:
            self._instruction(f"new {owner}")
            self._instruction("dup")
            self._instruction("aconst_null")
            self._instruction(f"invokespecial {owner}/<init>(Ljava/lang/Object;)V")
            return _descriptor(owner)
        parameters = self._constructor_parameters(owner, node)
        self._instruction(f"new {owner}")
        self._instruction("dup")
        self._emit_arguments(node, parameters)
        self._instruction(f"invokespecial {owner}/<init>({''.join(parameters)})V")
        return _descriptor(owner)

    def _argument_types(self, node: ast.Call) -> tuple[str, ...]:
        return tuple(self._type_of(argument) for argument in node.args)

    def _type_of(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            local = self.locals.get(node.id)
            if local is None:
                raise self.error(node, f"unknown local {node.id!r}")
            element = self.list_elements.get(node.id)
            if element is not None:
                return f"arraylist:{element}"
            return local[1]
        if isinstance(node, ast.Constant):
            constant = self._type_of_constant(node)
            if constant is not None:
                return constant
        if isinstance(node, ast.Lambda):
            return "lambda"
        if isinstance(node, ast.Attribute):
            return self._type_of_attribute(node)
        if isinstance(node, ast.List):
            return _array_descriptor(self._element_type(node))
        if isinstance(node, ast.Dict):
            self._dict_types(node)
            return _LINKED_HASH_MAP
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if self._type_of(node.operand) != "I":
                raise self.error(node, "unary minus needs an int")
            return "I"
        if isinstance(node, ast.Subscript):
            return self._type_of_subscript(node)
        if isinstance(node, ast.BinOp):
            return self._type_of_binop(node)
        if isinstance(node, ast.JoinedStr):
            return _STRING
        if isinstance(node, ast.IfExp):
            return self._conditional_type(node)
        if isinstance(node, ast.Compare):
            return self._identity_type(node)
        if isinstance(node, ast.Call):
            call = self._type_of_call(node)
            if call is not None:
                return call
        raise self.error(node, f"unsupported expression {type(node).__name__}")

    def _conditional_type(self, node: ast.IfExp) -> str:
        return self._common_type(node, self._type_of(node.body), self._type_of(node.orelse))

    def _common_type(self, node: ast.AST, left: str, right: str) -> str:
        if left == right or self._compatible(right, left):
            return left
        if self._compatible(left, right):
            return right
        raise self.error(node, f"conditional arms have types {left} and {right}")

    def _conditional(self, node: ast.IfExp) -> str:
        result = self._conditional_type(node)
        else_label = self._label("else")
        end_label = self._label("endif")
        self._condition_jump_false(node.test, else_label)
        self._fit_conditional(node.body, self._expression(node.body), result)
        self._instruction(f"goto {end_label}")
        self._instruction(f"{else_label}:", indented=False)
        self._fit_conditional(node.orelse, self._expression(node.orelse), result)
        self._instruction(f"{end_label}:", indented=False)
        return result

    def _fit_conditional(self, node: ast.AST, produced: str, result: str) -> None:
        if produced == result or produced == "null":
            return
        if not self._compatible(produced, result):
            raise self.error(node, f"conditional arm has type {produced}, expected {result}")
        if produced.startswith("L") and result.startswith("L"):
            self._instruction(f"checkcast {result[1:-1]}")

    def _type_of_constant(self, node: ast.Constant) -> str | None:
        value = node.value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "Z"
        if isinstance(value, int):
            return "I"
        if isinstance(value, float):
            return "F"
        if isinstance(value, str):
            return _STRING
        return None

    def _type_of_attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id != "self":
            found = self._instance_field(node.value.id)
            if found is not None and node.attr in found[1]:
                return found[1][node.attr]
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            field_type = self.fields.get(node.attr)
            if field_type is None and node.attr in self.methods:
                return "lambda"
            if field_type is None:
                raise self.error(node, f"unknown field {node.attr!r}")
            return field_type
        found = self.catalog.field(self._resolve_type_name(node.value), node.attr)
        if found is None:
            raise self.error(node, f"unknown static field {node.attr}")
        return found[0]

    def _type_of_subscript(self, node: ast.Subscript) -> str:
        if isinstance(node.slice, ast.Slice):
            raise self.error(node, "a slice is only supported as a for iterable")
        sequence = self._type_of(node.value)
        if sequence == _STRING:
            return "I"
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(node, "subscript needs an array, a list, or a string")
        return element

    def _type_of_binop(self, node: ast.BinOp) -> str:
        left = self._type_of(node.left)
        right = self._type_of(node.right)
        if isinstance(node.op, ast.Add) and left == _STRING and right == _STRING:
            return _STRING
        if type(node.op) in {ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod} and left == "I" and right == "I":
            return "I"
        raise self.error(node, "only string concatenation and integer arithmetic are supported")

    def _type_of_call(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name) and not self._is_type_reference(node.func):
            return self._builtin_result(node)
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(node.func.value):
            owner = self._resolve_type_name(node.func.value)
            signature = self._find_method(owner, node.func.attr, self._argument_types(node), static=True)
            if signature is not None:
                return signature.result
            return _descriptor(f"{owner}${node.func.attr}")
        if self._is_type_reference(node.func):
            return _descriptor(self._resolve_type_name(node.func))
        if isinstance(node.func, ast.Attribute):
            receiver = self._type_of(node.func.value)
            if receiver.startswith("L"):
                owner = _internal_name(receiver)
                method_name = _java_method_name(owner, node.func.attr)
                signature = self._find_method(
                    owner,
                    method_name,
                    self._argument_types(node),
                    static=False,
                )
                if signature is not None:
                    return self._yielded_result(owner, method_name, signature.result)
        return None

    def _emit_range(self, node: ast.Call) -> str:
        count = len(node.args)
        if count == 1:
            self._emit_int(0)
            _ = self._expression(node.args[0])
            self._emit_int(1)
        elif count == 2:
            _ = self._expression(node.args[0])
            _ = self._expression(node.args[1])
            self._emit_int(1)
        else:
            _ = self._expression(node.args[0])
            _ = self._expression(node.args[1])
            self._emit_range_step(node.args[2])
        self.generator_elements["py2apk$gen$1"] = "I"
        self._instruction("invokestatic py2apk/range(III)Lpy2apk$gen$1;")
        return "Lpy2apk$gen$1;"

    def _emit_range_step(self, step: ast.expr) -> None:
        literal = _literal_int(step)
        if literal is not None:
            if literal == 0:
                raise self.error(step, "range step cannot be zero")
            self._emit_int(literal)
            return
        self._emit_int_expr(step)
        ok = self._label("rangestep")
        self._instruction("dup")
        self._instruction(f"ifne {ok}")
        self._instruction("pop")
        self._instruction("new java/lang/IllegalArgumentException")
        self._instruction("dup")
        self._instruction("invokespecial java/lang/IllegalArgumentException/<init>()V")
        self._instruction("athrow")
        self._instruction(f"{ok}:", indented=False)

    def _tuple_literal(self, node: ast.Tuple) -> str:
        return self._java_array(node, self._element_type(node))

    def _list_literal(self, node: ast.List) -> str:
        element = self._element_type(node)
        sink = self.list_sink
        self.list_sink = None
        try:
            if id(node) in self.field_list_ids or (sink is not None and sink in self.mutable_lists):
                return self._array_list(node, element)
            return self._java_array(node, element)
        finally:
            self.list_sink = sink

    def _element_type(self, node: ast.List | ast.Tuple) -> str:
        kind = "list" if isinstance(node, ast.List) else "tuple"
        if not node.elts:
            raise self.error(node, f"an empty {kind} has no element type")
        element: str | None = None
        for item in node.elts:
            current = self._type_of(item)
            if current in {"null", "lambda"}:
                raise self.error(item, f"a {kind} element needs a value type")
            if element is None:
                element = current
            elif current != element:
                raise self.error(node, f"a {kind} needs one element type")
        if element is None:
            raise self.error(node, f"a {kind} has no element type")
        return element

    def _java_array(self, node: ast.List | ast.Tuple, element: str) -> str:
        count = ast.Constant(value=len(node.elts))
        ast.copy_location(count, node)
        _ = self._constant(count)
        kind = _NEWARRAY.get(element)
        if kind is None:
            internal = element[1:-1] if element.startswith("L") and element.endswith(";") else element
            self._instruction(f"anewarray {internal}")
        else:
            self._instruction(f"newarray {kind}")
        store = _ARRAY_STORE.get(element, "aastore")
        for index, item in enumerate(node.elts):
            self._instruction("dup")
            position = ast.Constant(value=index)
            ast.copy_location(position, node)
            _ = self._constant(position)
            produced = self._expression(item)
            if produced != element:
                raise self.error(item, f"list element has type {produced}, expected {element}")
            self._instruction(store)
        return _array_descriptor(element)

    def _array_list(self, node: ast.List, element: str) -> str:
        init = self._require_method(_ARRAY_LIST_OWNER, "<init>", (), static=False, node=node)
        self._instruction(f"new {_ARRAY_LIST_OWNER}")
        self._instruction("dup")
        self._instruction(f"invokespecial {_ARRAY_LIST_OWNER}/<init>{init.descriptor}")
        add = self._require_method(_ARRAY_LIST_OWNER, "add", ("Ljava/lang/Object;",), static=False, node=node)
        for item in node.elts:
            self._instruction("dup")
            produced = self._expression(item)
            if produced != element:
                raise self.error(item, f"list element has type {produced}, expected {element}")
            self._box(produced)
            self._invoke(add, "add")
            self._instruction("pop")
        return f"arraylist:{element}"

    def _dict_types(self, node: ast.Dict) -> tuple[str, str]:
        if not node.keys:
            raise self.error(node, "an empty dict has no key or value type")
        key_type: str | None = None
        value_type: str | None = None
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise self.error(node, "dict unpacking is unsupported")
            current_key = self._type_of(key)
            current_value = self._type_of(value)
            if current_key in {"null", "lambda"} or current_value in {"null", "lambda"}:
                raise self.error(node, "a dict key and value need value types")
            if key_type is None:
                key_type = current_key
                value_type = current_value
            elif current_key != key_type or current_value != value_type:
                raise self.error(node, "a dict needs one key type and one value type")
        if key_type is None or value_type is None:
            raise self.error(node, "a dict has no key or value type")
        return key_type, value_type

    def _dict_literal(self, node: ast.Dict) -> str:
        key_type, value_type = self._dict_types(node)
        init = self._require_method(_LINKED_HASH_MAP_OWNER, "<init>", (), static=False, node=node)
        self._instruction(f"new {_LINKED_HASH_MAP_OWNER}")
        self._instruction("dup")
        self._instruction(f"invokespecial {_LINKED_HASH_MAP_OWNER}/<init>{init.descriptor}")
        put = self._require_method(
            _LINKED_HASH_MAP_OWNER,
            "put",
            ("Ljava/lang/Object;", "Ljava/lang/Object;"),
            static=False,
            node=node,
        )
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise self.error(node, "dict unpacking is unsupported")
            self._instruction("dup")
            produced_key = self._expression(key)
            if produced_key != key_type:
                raise self.error(key, f"dict key has type {produced_key}, expected {key_type}")
            self._box(produced_key)
            produced_value = self._expression(value)
            if produced_value != value_type:
                raise self.error(value, f"dict value has type {produced_value}, expected {value_type}")
            self._box(produced_value)
            self._invoke(put, "put")
            self._instruction("pop")
        return _LINKED_HASH_MAP

    def _list_append(self, node: ast.Call) -> str:
        if node.keywords or len(node.args) != 1:
            raise self.error(node, "append takes one positional argument")
        value = node.func
        if not isinstance(value, ast.Attribute):
            raise self.error(node, "append needs a stored list")
        if not isinstance(value.value, ast.Name):
            raise self.error(node, "append needs a stored list")
        local = self.locals.get(value.value.id)
        if local is None:
            raise self.error(node, f"unknown local {value.value.id!r}")
        element = self.list_elements.get(value.value.id)
        if element is None:
            raise self.error(node, "this list is an array and cannot be changed")
        self._instruction(f"aload {local[0]}")
        produced = self._expression(node.args[0])
        if produced != element:
            raise self.error(node.args[0], f"append has type {produced}, expected {element}")
        self._box(produced)
        add = self._require_method(_ARRAY_LIST_OWNER, "add", ("Ljava/lang/Object;",), static=False, node=node)
        self._invoke(add, "add")
        self._instruction("pop")
        return "V"

    def _emit_to_array(self, element: str) -> None:
        self._instruction("dup")
        size = self._require_method(_ARRAY_LIST_OWNER, "size", (), static=False)
        self._invoke(size, "size")
        internal = element[1:-1] if element.startswith("L") and element.endswith(";") else element
        self._instruction(f"anewarray {internal}")
        signature = self._require_method(
            _ARRAY_LIST_OWNER,
            "toArray",
            (_OBJECT_ARRAY,),
            static=False,
        )
        self._invoke(signature, "toArray")
        self._instruction(f"checkcast {_array_descriptor(element)}")

    def _classify_lists(self, statements: list[ast.stmt]) -> None:
        origins: dict[str, str] = {}
        literals: dict[str, ast.List] = {}
        field_names: set[str] = set()
        field_lists: set[int] = set()
        appends: set[str] = set()
        captured: set[str] = set()
        escapes: list[tuple[ast.Call, str]] = []
        self._note_list_assignments(statements, origins, literals, field_names, field_lists)
        self._note_list_uses(statements, origins, appends, captured, escapes)
        mutable: set[str] = set()
        for name in appends | field_names | captured:
            if name in origins:
                mutable.add(_list_origin(name, origins))
        changed = True
        while changed:
            changed = False
            for call, arg_name in escapes:
                root = _list_origin(arg_name, origins)
                if root in mutable or root not in literals:
                    continue
                if not self._accepts_list_array(call, arg_name, origins, literals, mutable):
                    mutable.add(root)
                    changed = True
        self.mutable_lists = {name for name in origins if _list_origin(name, origins) in mutable}
        self.field_list_ids = field_lists

    def _note_list_assignments(
        self,
        body: list[ast.stmt],
        origins: dict[str, str],
        literals: dict[str, ast.List],
        field_names: set[str],
        field_lists: set[int],
    ) -> None:
        for statement in body:
            if isinstance(statement, ast.If | ast.While | ast.For):
                self._note_list_assignments(statement.body, origins, literals, field_names, field_lists)
                self._note_list_assignments(statement.orelse, origins, literals, field_names, field_lists)
                continue
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target
                value = statement.value
            if target is None or value is None:
                continue
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                if isinstance(value, ast.Name):
                    field_names.add(value.id)
                elif isinstance(value, ast.List):
                    field_lists.add(id(value))
            if isinstance(target, ast.Name) and isinstance(value, ast.List):
                origins[target.id] = target.id
                literals[target.id] = value
            elif isinstance(target, ast.Name) and isinstance(value, ast.Name) and value.id in origins:
                origins[target.id] = _list_origin(value.id, origins)

    def _note_list_uses(
        self,
        statements: list[ast.stmt],
        origins: dict[str, str],
        appends: set[str],
        captured: set[str],
        escapes: list[tuple[ast.Call, str]],
    ) -> None:
        for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
            if isinstance(node, ast.Lambda):
                bound = {arg.arg for arg in node.args.args}
                for inner in ast.walk(node.body):
                    if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and inner.id not in bound:
                        captured.add(inner.id)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if isinstance(node.func.value, ast.Name) and node.func.attr == "append":
                appends.add(node.func.value.id)
                continue
            escapes.extend(
                (node, argument.id)
                for argument in node.args
                if isinstance(argument, ast.Name) and argument.id in origins
            )

    def _accepts_list_array(
        self,
        call: ast.Call,
        arg_name: str,
        origins: dict[str, str],
        literals: dict[str, ast.List],
        mutable: set[str],
    ) -> bool:
        def origin_of(name: str) -> str:
            seen: set[str] = set()
            while name in origins and origins[name] != name and name not in seen:
                seen.add(name)
                name = origins[name]
            return name

        types: list[str] = []
        index = -1
        for position, argument in enumerate(call.args):
            if isinstance(argument, ast.Name) and argument.id == arg_name:
                index = position
            if isinstance(argument, ast.Name) and argument.id in origins:
                root = origin_of(argument.id)
                literal = literals.get(root)
                element = self._preview_list_element(literal) if literal is not None else None
                if root in mutable or element is None:
                    types.append(_ARRAY_LIST)
                else:
                    types.append(_array_descriptor(element))
                continue
            preview = self._preview_type(argument)
            if preview is None:
                return False
            types.append(preview)
        if index < 0:
            return False
        signature = self._signature_for_call(call, tuple(types))
        element = self._preview_list_element(literals.get(origin_of(arg_name)))
        if signature is None or element is None or index >= len(signature.parameters):
            return False
        return signature.parameters[index] == _array_descriptor(element)

    def _preview_list_element(self, node: ast.List | None) -> str | None:
        if node is None or not node.elts:
            return None
        element: str | None = None
        for item in node.elts:
            current = self._preview_type(item)
            if current is None:
                return None
            if element is None:
                element = current
            elif current != element:
                return None
        return element

    def _preview_type(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None:
                return "null"
            if isinstance(value, bool):
                return "Z"
            if isinstance(value, int):
                return "I"
            if isinstance(value, float):
                return "F"
            if isinstance(value, str):
                return _STRING
            return None
        if isinstance(node, ast.Name):
            return self.preview_locals.get(node.id)
        if isinstance(node, ast.List):
            element = self._preview_list_element(node)
            if element is None:
                return None
            return _array_descriptor(element)
        if isinstance(node, ast.Dict):
            return _LINKED_HASH_MAP
        return None

    def _signature_for_call(self, node: ast.Call, arguments: tuple[str, ...]) -> _MethodSignature | None:
        if node.keywords or not isinstance(node.func, ast.Attribute):
            return None
        func = node.func
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            return self._find_method(self.class_internal, func.attr, arguments, static=False)
        if self._is_type_reference(func.value):
            return self._find_method(self._resolve_type_name(func.value), func.attr, arguments, static=True)
        receiver = self._preview_type(func.value)
        if receiver is None or not receiver.startswith("L"):
            return None
        return self._find_method(_internal_name(receiver), func.attr, arguments, static=False)

    def _interface_bases(self, internal: str) -> set[str]:
        if self.catalog.is_interface(internal):
            return {internal}
        return set()

    def _is_type_reference(self, node: ast.expr) -> bool:
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and (
            root.id in self.imports or root.id == self.class_name
        )

    @staticmethod
    def _is_super_call(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "super"
            and not node.args
            and not node.keywords
        )

    def _constructor_parameters(self, owner: str, node: ast.Call) -> tuple[str, ...]:
        local = self.project.get(owner)
        if local is not None:
            return local.constructor
        signature = self._find_method(owner, "<init>", self._argument_types(node), static=False)
        if signature is None:
            raise self.error(node, f"unknown constructor {owner}")
        return signature.parameters

    def _find_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        *,
        static: bool,
    ) -> _MethodSignature | None:
        return self._search_method(owner, name, arguments, static, set())

    def _search_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        static: bool,
        seen: set[str],
    ) -> _MethodSignature | None:
        if owner in seen:
            return None
        seen.add(owner)
        candidates: list[_MethodSignature] = []
        generated = self._generator_method(owner, name, arguments, static)
        if generated is not None:
            candidates.append(generated)
        if owner == self.class_internal and name in self.methods and _static_matches(self.methods[name], static):
            candidates.append(self.methods[name])
        local = self.project.get(owner)
        if local is not None and name in local.methods and _static_matches(local.methods[name], static):
            candidates.append(local.methods[name])
        for params, result, method_owner, is_static, opcode in self.catalog.methods(owner, name):
            if is_static != static and name != "<init>":
                continue
            candidates.append(
                _MethodSignature(parameter_descriptors(params), result, opcode, method_owner)
            )
        match = _best_signature(candidates, arguments, self)
        if match is not None:
            return match
        bases: list[str] = []
        if local is not None and local.base:
            bases.append(local.base)
        if owner == self.class_internal and self.super_internal not in {"", "java/lang/Object"}:
            bases.append(self.super_internal)
        super_name = self.catalog.super_of(owner)
        if super_name:
            bases.append(super_name)
        bases.extend(self.catalog.interfaces_of(owner))
        for base in bases:
            found = self._search_method(base, name, arguments, static, seen)
            if found is not None:
                return found
        return None

    def _emit_arguments(self, node: ast.Call, expected: tuple[str, ...]) -> None:
        if len(node.args) != len(expected):
            raise self.error(node, f"expected {len(expected)} arguments, got {len(node.args)}")
        for argument, wanted in zip(node.args, expected, strict=True):
            if isinstance(argument, ast.Lambda) or self._is_method_reference(argument):
                self._emit_listener(argument, wanted)
                continue
            actual = self._expression(argument)
            element = _list_element(actual)
            if element is not None and wanted == _array_descriptor(element):
                if not element.startswith(("L", "[")):
                    raise self.error(argument, "a changed number list cannot be passed as an array")
                self._emit_to_array(element)
                actual = wanted
            if actual == "I" and wanted == "J":
                self._instruction("i2l")
                actual = "J"
            if not self._compatible(actual, wanted):
                raise self.error(argument, f"argument has type {actual}, expected {wanted}")

    def _is_method_reference(self, node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr not in self.fields
            and node.attr in self.methods
        )

    def _is_interface_type(self, descriptor: str) -> bool:
        if not (descriptor.startswith("L") and descriptor.endswith(";")):
            return False
        return self.catalog.is_interface(descriptor[1:-1])

    def _listener_methods(
        self, descriptor: str
    ) -> tuple[str, list[tuple[str, tuple[str, ...], str]]] | None:
        if not (descriptor.startswith("L") and descriptor.endswith(";")):
            return None
        internal = descriptor[1:-1]
        if not self.catalog.is_interface(internal):
            return None
        declared = [
            (name, parameter_descriptors(params), result)
            for name, params, result in self.catalog.interface_methods(internal)
        ]
        return internal, declared

    def _emit_listener(self, node: ast.expr, expected: str) -> None:
        found = self._listener_methods(expected)
        if found is None or not found[1]:
            raise self.error(node, "a lambda needs an interface with one method; write a class")
        interface, declared = found
        if self._is_method_reference(node):
            if not isinstance(node, ast.Attribute):
                raise self.error(node, "a method reference must be an attribute")
            chosen, listener = self._method_reference_choice(node, declared)
        else:
            chosen = self._lambda_choice(node, declared)
            listener = node
        if not isinstance(listener, ast.Lambda):
            raise self.error(node, "a lambda needs an interface with one method; write a class")
        if listener.args.vararg or listener.args.kwarg or listener.args.defaults or listener.args.kwonlyargs:
            raise self.error(node, "lambda arguments must be plain positional names")
        method_name, parameters, result = chosen
        names = [arg.arg for arg in listener.args.args]
        if len(names) != len(parameters):
            raise self.error(node, f"lambda has {len(names)} arguments, the listener method has {len(parameters)}")
        stubs = [item for item in declared if item != chosen]
        self._emit_lambda(
            listener,
            interface,
            method_name,
            tuple(zip(names, parameters, strict=True)),
            result,
            stubs,
        )

    def _lambda_choice(
        self,
        node: ast.expr,
        declared: list[tuple[str, tuple[str, ...], str]],
    ) -> tuple[str, tuple[str, ...], str]:
        if not isinstance(node, ast.Lambda):
            raise self.error(node, "a lambda needs an interface with one method; write a class")
        count = len(node.args.args)
        if len(declared) == 1:
            _name, parameters, _result = declared[0]
            if count != len(parameters):
                raise self.error(
                    node,
                    f"lambda has {count} arguments, the listener method has {len(parameters)}",
                )
            return declared[0]
        matches = [item for item in declared if len(item[1]) == count]
        if not matches:
            raise self.error(node, "lambda matches no method of this interface; write a class")
        if len(matches) > 1:
            raise self.error(node, "lambda matches more than one method; write a class")
        return matches[0]

    def _method_reference_choice(
        self,
        node: ast.Attribute,
        declared: list[tuple[str, tuple[str, ...], str]],
    ) -> tuple[tuple[str, tuple[str, ...], str], ast.Lambda]:
        method = self.methods[node.attr]
        matches = [
            item for item in declared if item[1] == method.parameters and item[2] == method.result
        ]
        if len(matches) != 1:
            raise self.error(node, f"{node.attr} does not match the listener method; write a lambda")
        chosen = matches[0]
        return chosen, self._method_reference_lambda(node, chosen[1], chosen[2])

    def _method_reference_lambda(
        self,
        node: ast.Attribute,
        parameters: tuple[str, ...],
        result: str,
    ) -> ast.Lambda:
        method = self.methods[node.attr]
        if method.parameters != parameters or method.result != result:
            raise self.error(node, f"{node.attr} does not match the listener method; write a lambda")
        arguments = ", ".join(f"a{index}" for index in range(len(parameters)))
        call = f"self.{node.attr}({arguments})" if arguments else f"self.{node.attr}()"
        header = f"lambda {arguments}: {call}" if arguments else f"lambda: {call}"
        parsed = ast.parse(header, mode="eval").body
        if not isinstance(parsed, ast.Lambda):
            raise self.error(node, f"{node.attr} does not match the listener method; write a lambda")
        ast.copy_location(parsed, node)
        ast.fix_missing_locations(parsed)
        return parsed

    def _emit_lambda(
        self,
        node: ast.Lambda,
        interface: str,
        method_name: str,
        parameters: tuple[tuple[str, str], ...],
        result: str,
        stubs: list[tuple[str, tuple[str, ...], str]],
    ) -> None:
        bound = {name for name, _descriptor in parameters}
        captures = [
            (name, self.locals[name][1])
            for name in _loaded_names(node.body, bound)
            if name in self.locals
        ]
        missing = [name for name in _loaded_names(node.body, bound) if name not in self.locals]
        if missing:
            raise self.error(node, f"unknown local {missing[0]!r}")
        self.lambda_serial += 1
        simple = self.class_internal.rsplit("/", 1)[-1]
        internal = f"{self.class_internal}$lambda${self.lambda_serial}"
        filename = f"{simple}$lambda${self.lambda_serial}.j"
        descriptor = "".join(descriptor for _name, descriptor in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, capture_type in captures:
            slot = self.locals[name][0]
            self._instruction(f"{self._load_opcode(capture_type)} {slot}")
        self._instruction(f"invokespecial {internal}/<init>({descriptor})V")
        self.extra_classes.append(
            (
                filename,
                self._lambda_class(
                    node, internal, interface, method_name, parameters, result, captures, stubs
                ),
            )
        )

    def _lambda_class(
        self,
        node: ast.Lambda,
        internal: str,
        interface: str,
        method_name: str,
        parameters: tuple[tuple[str, str], ...],
        result: str,
        captures: list[tuple[str, str]],
        stubs: list[tuple[str, tuple[str, ...], str]],
    ) -> str:
        body_locals: dict[str, tuple[int, str]] = {}
        slot = 1
        for name, descriptor in parameters:
            body_locals[name] = (slot, descriptor)
            slot += 2 if descriptor in {"D", "J"} else 1
        for name, descriptor in captures:
            body_locals[name] = (slot, descriptor)
            slot += 2 if descriptor in {"D", "J"} else 1
        saved = (self.instructions, self.locals, self.next_local, self.lambda_body, self.list_elements)
        self.instructions = []
        self.locals = body_locals
        self.next_local = slot
        self.lambda_body = True
        self.list_elements = {
            name: self.list_elements[name] for name, _descriptor in captures if name in self.list_elements
        }
        try:
            for name, descriptor in captures:
                self._instruction("aload_0")
                self._instruction(f"getfield {internal}/{name} {descriptor}")
                self._instruction(f"{self._store_opcode(descriptor)} {body_locals[name][0]}")
            produced = self._expression(node.body)
            if result == "V":
                if produced != "V":
                    self._instruction("pop2" if produced in {"D", "J"} else "pop")
                self._instruction("return")
            else:
                if produced != result:
                    raise self.error(node, f"lambda has type {produced}, listener method returns {result}")
                self._instruction(_return_opcode(result))
            body = list(self.instructions)
            limit = self.next_local
        finally:
            self.instructions, self.locals, self.next_local, self.lambda_body, self.list_elements = saved
        parameter_descriptor = "".join(descriptor for _name, descriptor in parameters)
        lines = [
            f".source {self.source.name}",
            f".class public {internal}",
            ".super java/lang/Object",
            f".implements {interface}",
            "",
        ]
        for name, descriptor in captures:
            lines.append(f".field private {json.dumps(name)} {descriptor}")
        if captures:
            lines.append("")
        lines.extend(
            (
                f".method public <init>({''.join(descriptor for _name, descriptor in captures)})V",
                "    .limit stack 32",
                f"    .limit locals {1 + sum(2 if descriptor in {'D', 'J'} else 1 for _name, descriptor in captures)}",
                "    aload_0",
                "    invokespecial java/lang/Object/<init>()V",
            )
        )
        init_slot = 1
        for name, descriptor in captures:
            lines.append("    aload_0")
            lines.append(f"    {self._load_opcode(descriptor)} {init_slot}")
            lines.append(f"    putfield {internal}/{name} {descriptor}")
            init_slot += 2 if descriptor in {"D", "J"} else 1
        lines.extend(("    return", ".end method", ""))
        lines.extend(
            (
                f".method public {method_name}({parameter_descriptor}){result}",
                "    .limit stack 32",
                f"    .limit locals {max(limit, 1)}",
            )
        )
        lines.extend(body)
        lines.extend((".end method", ""))
        for stub_name, stub_parameters, stub_result in stubs:
            stub_descriptor = "".join(stub_parameters)
            stub_locals = 1 + sum(2 if descriptor in {"D", "J"} else 1 for descriptor in stub_parameters)
            lines.extend(
                (
                    f".method public {stub_name}({stub_descriptor}){stub_result}",
                    "    .limit stack 1",
                    f"    .limit locals {stub_locals}",
                    *_immediate_return(stub_result),
                    ".end method",
                    "",
                )
            )
        return "\n".join(lines) + "\n"

    def _compile_context_method(self, node: ast.FunctionDef) -> None:
        internal = self.context_names[node.name]
        element = self.context_elements[internal]
        before, yielded, after = self._split_context_body(node)
        signature = self.methods[node.name]
        self.method_result = element
        self._bind_parameters(node, signature, static=signature.opcode == "invokestatic")
        captures = [(argument.arg, self.locals[argument.arg][1]) for argument in node.args.args]
        capture_descriptor = "".join(descriptor for _name, descriptor in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, descriptor in captures:
            self._instruction(f"{self._load_opcode(descriptor)} {self.locals[name][0]}")
        self._instruction(f"invokespecial {internal}/<init>({capture_descriptor})V")
        self._instruction("areturn")
        wrapper = list(self.instructions)
        wrapper_limit = self.next_local
        enter_body, enter_limit = self._context_body(internal, captures, before, yielded, element, 1)
        exit_body, exit_limit = self._context_body(internal, captures, after, None, "Z", 4)
        access = "public static" if signature.opcode == "invokestatic" else "public"
        self.lines.append(f".method {access} {node.name}{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(wrapper_limit, 1)}")
        self.lines.extend(wrapper)
        self.lines.append(".end method")
        self.lines.append("")
        simple = self.class_internal.rsplit("/", 1)[-1]
        serial = internal.rsplit("$", 1)[-1]
        self.extra_classes.append(
            (
                f"{simple}$cm${serial}.j",
                self._context_class(
                    internal,
                    element,
                    captures,
                    enter_body,
                    enter_limit,
                    exit_body,
                    exit_limit,
                ),
            )
        )

    def _split_context_body(
        self,
        node: ast.FunctionDef,
    ) -> tuple[list[ast.stmt], ast.expr, list[ast.stmt]]:
        yields = [child for child in ast.walk(node) if isinstance(child, ast.Yield)]
        if len(yields) != 1:
            raise self.error(node, "contextmanager needs one yield")
        if _yield_in_loop(node.body):
            raise self.error(node, "contextmanager yield cannot be inside a loop")
        found = self._split_context_statements(node.body)
        if found is None:
            raise self.error(node, "contextmanager yield must be a statement")
        return found

    def _split_context_statements(
        self,
        statements: list[ast.stmt],
    ) -> tuple[list[ast.stmt], ast.expr, list[ast.stmt]] | None:
        for index, statement in enumerate(statements):
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Yield):
                if statement.value.value is None:
                    raise self.error(statement, "contextmanager yield needs a value")
                return statements[:index], statement.value.value, statements[index + 1 :]
            if isinstance(statement, ast.Try) and _contains_yield(statement):
                if statement.handlers or statement.orelse:
                    raise self.error(statement, "contextmanager try cannot have except or else")
                inner = self._split_context_statements(statement.body)
                if inner is None:
                    raise self.error(statement, "contextmanager yield must be a statement")
                before, yielded, after = inner
                return (
                    statements[:index] + before,
                    yielded,
                    after + statement.finalbody + statements[index + 1 :],
                )
        return None

    def _context_body(
        self,
        internal: str,
        captures: list[tuple[str, str]],
        statements: list[ast.stmt],
        yielded: ast.expr | None,
        result: str,
        base_slot: int,
    ) -> tuple[list[str], int]:
        saved = (
            self.instructions,
            self.locals,
            self.next_local,
            self.lambda_body,
            self.list_elements,
            self.preview_locals,
            self.loops,
            self.handlers,
        )
        self.instructions = []
        self.locals = {}
        self.list_elements = {}
        self.preview_locals = {}
        self.loops = []
        self.handlers = []
        slot = base_slot
        for name, descriptor in captures:
            self.locals[name] = (slot, descriptor)
            self.preview_locals[name] = descriptor
            slot += 2 if descriptor in {"D", "J"} else 1
        self.next_local = slot
        self.lambda_body = "self" in self.locals
        try:
            for name, descriptor in captures:
                self._instruction("aload_0")
                self._instruction(f"getfield {internal}/{name} {descriptor}")
                self._instruction(f"{self._store_opcode(descriptor)} {self.locals[name][0]}")
            for statement in statements:
                self._statement(statement)
            if yielded is None:
                self._instruction("iconst_0")
                self._instruction("ireturn")
            else:
                actual = self._expression(yielded)
                if actual != result and not self._compatible(actual, result):
                    raise self.error(yielded, f"yield has type {actual}, expected {result}")
                self._instruction(_return_opcode(result))
            return list(self.instructions), self.next_local
        finally:
            (
                self.instructions,
                self.locals,
                self.next_local,
                self.lambda_body,
                self.list_elements,
                self.preview_locals,
                self.loops,
                self.handlers,
            ) = saved

    def _context_class(
        self,
        internal: str,
        element: str,
        captures: list[tuple[str, str]],
        enter_body: list[str],
        enter_limit: int,
        exit_body: list[str],
        exit_limit: int,
    ) -> str:
        lines = [
            f".source {self.source.name}",
            f".class public {internal}",
            ".super java/lang/Object",
            "",
        ]
        for name, descriptor in captures:
            lines.append(f".field private {json.dumps(name)} {descriptor}")
        if captures:
            lines.append("")
        capture_descriptor = "".join(descriptor for _name, descriptor in captures)
        init_slot = 1
        init_locals = 1
        for _name, descriptor in captures:
            init_locals = init_slot + (2 if descriptor in {"D", "J"} else 1)
            init_slot += 2 if descriptor in {"D", "J"} else 1
        lines.extend(
            (
                f".method public <init>({capture_descriptor})V",
                "    .limit stack 32",
                f"    .limit locals {max(init_locals, 1)}",
                "    aload_0",
                "    invokespecial java/lang/Object/<init>()V",
            )
        )
        init_slot = 1
        for name, descriptor in captures:
            lines.append("    aload_0")
            lines.append(f"    {self._load_opcode(descriptor)} {init_slot}")
            lines.append(f"    putfield {internal}/{name} {descriptor}")
            init_slot += 2 if descriptor in {"D", "J"} else 1
        lines.extend(("    return", ".end method", ""))
        throwable = "Ljava/lang/Throwable;"
        lines.extend(
            (
                f".method public __enter__(){element}",
                "    .limit stack 32",
                f"    .limit locals {max(enter_limit, 1)}",
            )
        )
        lines.extend(enter_body)
        lines.extend(
            (
                ".end method",
                "",
                f".method public __exit__({throwable}{throwable}{throwable})Z",
                "    .limit stack 32",
                f"    .limit locals {max(exit_limit, 4)}",
            )
        )
        lines.extend(exit_body)
        lines.extend((".end method", ""))
        return "\n".join(lines) + "\n"

    def _compile_generator_method(self, node: ast.FunctionDef) -> None:
        internal = self.generator_names[node.name]
        element = self.generator_elements[internal]
        self._reject_bad_yields(node)
        signature = self.methods[node.name]
        self.method_result = element
        self._bind_parameters(node, signature, static=signature.opcode == "invokestatic")
        loaded = set(_loaded_names(node, set()))
        captures = [
            (argument.arg, self.locals[argument.arg][1])
            for argument in node.args.args
            if argument.arg in loaded
        ]
        capture_descriptor = "".join(descriptor for _name, descriptor in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, capture_type in captures:
            self._instruction(f"{self._load_opcode(capture_type)} {self.locals[name][0]}")
        self._instruction(f"invokespecial {internal}/<init>({capture_descriptor})V")
        self._instruction("areturn")
        owner_body = list(self.instructions)
        owner_limit = self.next_local

        yield_count = sum(
            isinstance(child, ast.Expr) and isinstance(child.value, ast.Yield)
            for child in ast.walk(node)
        )
        saved = (
            self.instructions,
            self.locals,
            self.next_local,
            self.lambda_body,
            self.list_elements,
            self.preview_locals,
            self.generator_internal,
            self.generator_element,
            self.generator_fields,
            self.generator_exhausted,
            self.resume_labels,
            self.yield_cursor,
        )
        self.instructions = []
        self.locals = {}
        self.next_local = 1
        self.lambda_body = False
        self.list_elements = {}
        self.preview_locals = {}
        self.generator_internal = internal
        self.generator_element = element
        self.generator_fields = {}
        self.yield_cursor = 0
        self.resume_labels = [self._label("gen") for _index in range(yield_count)]
        self.generator_exhausted = self._label("genend")
        start_label = self._label("genstart")
        slot = 1
        for name, descriptor in captures:
            self.locals[name] = (slot, descriptor)
            self.generator_fields[name] = descriptor
            self.preview_locals[name] = descriptor
            slot += 2 if descriptor in {"D", "J"} else 1
        self.next_local = slot
        self.lambda_body = "self" in self.locals
        try:
            self._instruction("aload_0")
            self._instruction(f"getfield {internal}/$state I")
            self._instruction("lookupswitch")
            self._instruction(f"0 : {start_label}")
            for index, label in enumerate(self.resume_labels, start=1):
                self._instruction(f"{index} : {label}")
            self._instruction(f"default : {self.generator_exhausted}")
            self._instruction(f"{start_label}:", indented=False)
            self._reload_generator_fields()
            self._classify_lists(node.body)
            for statement in node.body:
                self._statement(statement)
            self._instruction(f"goto {self.generator_exhausted}")
            self._instruction(f"{self.generator_exhausted}:", indented=False)
            self._emit_generator_stop()
            body = list(self.instructions)
            limit = self.next_local
            fields = list(self.generator_fields.items())
        finally:
            (
                self.instructions,
                self.locals,
                self.next_local,
                self.lambda_body,
                self.list_elements,
                self.preview_locals,
                self.generator_internal,
                self.generator_element,
                self.generator_fields,
                self.generator_exhausted,
                self.resume_labels,
                self.yield_cursor,
            ) = saved

        access = "public static" if signature.opcode == "invokestatic" else "public"
        self.lines.append(f".method {access} {node.name}{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(owner_limit, 1)}")
        self.lines.extend(owner_body)
        self.lines.append(".end method")
        self.lines.append("")
        simple = self.class_internal.rsplit("/", 1)[-1]
        serial = internal.rsplit("$", 1)[-1]
        self.extra_classes.append(
            (
                f"{simple}$gen${serial}.j",
                self._generator_class(internal, element, captures, fields, body, limit),
            )
        )

    def _generator_class(
        self,
        internal: str,
        element: str,
        captures: list[tuple[str, str]],
        fields: list[tuple[str, str]],
        body: list[str],
        limit: int,
    ) -> str:
        _ = element
        lines = [
            f".source {self.source.name}",
            f".class public {internal}",
            ".super java/lang/Object",
            ".implements java/util/Iterator",
            "",
            '.field private "$state" I',
        ]
        for name, descriptor in fields:
            lines.append(f".field private {json.dumps(name)} {descriptor}")
        lines.append("")
        init_locals = 1
        init_slot = 1
        for _name, descriptor in captures:
            init_locals = init_slot + (2 if descriptor in {"D", "J"} else 1)
            init_slot += 2 if descriptor in {"D", "J"} else 1
        capture_descriptor = "".join(descriptor for _name, descriptor in captures)
        lines.extend(
            (
                f".method public <init>({capture_descriptor})V",
                "    .limit stack 32",
                f"    .limit locals {max(init_locals, 1)}",
                "    aload_0",
                "    invokespecial java/lang/Object/<init>()V",
                "    aload_0",
                "    iconst_0",
                f"    putfield {internal}/$state I",
            )
        )
        init_slot = 1
        for name, descriptor in captures:
            lines.append("    aload_0")
            lines.append(f"    {self._load_opcode(descriptor)} {init_slot}")
            lines.append(f"    putfield {internal}/{name} {descriptor}")
            init_slot += 2 if descriptor in {"D", "J"} else 1
        lines.extend(("    return", ".end method", ""))
        lines.extend(
            (
                ".method public hasNext()Z",
                "    .limit stack 2",
                "    .limit locals 1",
                "    aload_0",
                f"    getfield {internal}/$state I",
                "    iconst_m1",
                "    if_icmpeq L_gen_exhausted",
                "    iconst_1",
                "    ireturn",
                "L_gen_exhausted:",
                "    iconst_0",
                "    ireturn",
                ".end method",
                "",
                ".method public next()Ljava/lang/Object;",
                "    .limit stack 32",
                f"    .limit locals {max(limit, 1)}",
            )
        )
        lines.extend(body)
        lines.extend((".end method", ""))
        return "\n".join(lines) + "\n"

    def _reject_bad_yields(self, node: ast.AST) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.YieldFrom):
                raise self.error(child, "yield from is unsupported")
            if isinstance(child, ast.Lambda) and _has_yield(child):
                raise self.error(child, "yield inside a lambda is unsupported")

    def _emit_yield(self, node: ast.Yield) -> None:
        if self.generator_internal is None or self.generator_element is None:
            raise self.error(node, "yield is only supported in a generator method")
        if node.value is None:
            raise self.error(node, "yield requires a value")
        if self.yield_cursor >= len(self.resume_labels) or self.generator_exhausted is None:
            raise self.error(node, "yield is only supported in a generator method")
        element = self.generator_element
        actual = self._expression(node.value)
        if actual != element:
            if (
                actual.startswith(("L", "["))
                and element.startswith(("L", "["))
                and self._compatible(actual, element)
            ):
                cast = element if element.startswith("[") else _internal_name(element)
                self._instruction(f"checkcast {cast}")
                actual = element
            else:
                raise self.error(node, f"yield type {actual} does not match {element}")
        self._hold_named("$yield", actual)
        self._spill_generator_fields()
        state = self.yield_cursor + 1
        resume = self.resume_labels[self.yield_cursor]
        self.yield_cursor += 1
        self._instruction("aload_0")
        self._emit_int(state)
        self._instruction(f"putfield {self.generator_internal}/$state I")
        self._load_local("$yield")
        if element in _BOX_METHODS:
            self._box(element)
        self._instruction("areturn")
        self._instruction(f"{resume}:", indented=False)
        self._reload_generator_fields()

    def _emit_generator_stop(self) -> None:
        if self.generator_internal is None:
            raise JasminCompilerError("generator stop without a generator class")
        self._instruction("aload_0")
        self._emit_int(-1)
        self._instruction(f"putfield {self.generator_internal}/$state I")
        self._instruction("new java/lang/NoSuchElementException")
        self._instruction("dup")
        self._instruction("invokespecial java/lang/NoSuchElementException/<init>()V")
        self._instruction("athrow")

    def _spill_generator_fields(self) -> None:
        if self.generator_internal is None or self.generator_fields is None:
            raise JasminCompilerError("generator spill without a generator class")
        for name, descriptor in self.generator_fields.items():
            slot = self.locals[name][0]
            self._instruction("aload_0")
            self._instruction(f"{self._load_opcode(descriptor)} {slot}")
            self._instruction(f"putfield {self.generator_internal}/{name} {descriptor}")

    def _reload_generator_fields(self) -> None:
        if self.generator_internal is None or self.generator_fields is None:
            raise JasminCompilerError("generator reload without a generator class")
        for name, descriptor in self.generator_fields.items():
            slot = self.locals[name][0]
            self._instruction("aload_0")
            self._instruction(f"getfield {self.generator_internal}/{name} {descriptor}")
            self._instruction(f"{self._store_opcode(descriptor)} {slot}")

    def _yielded_result(self, owner: str, method_name: str, result: str) -> str:
        if method_name == "next" and owner in self.generator_elements:
            return self.generator_elements[owner]
        return result

    def _generator_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        static: bool,
    ) -> _MethodSignature | None:
        if owner not in self.generator_elements or static or arguments != ():
            return None
        if name == "next":
            return _MethodSignature((), "Ljava/lang/Object;", "invokevirtual", owner)
        if name == "hasNext":
            return _MethodSignature((), "Z", "invokevirtual", owner)
        return None

    def _restore_yield_type(self, element: str) -> str:
        unbox = _UNBOX_METHODS.get(element)
        if unbox is not None:
            owner, name, descriptor = unbox
            self._instruction(f"invokevirtual {owner}/{name}{descriptor}")
            return element
        if element.startswith("["):
            self._instruction(f"checkcast {element}")
            return element
        if element.startswith("L"):
            self._instruction(f"checkcast {_internal_name(element)}")
            return element
        raise JasminCompilerError(f"cannot restore yield type {element}")

    def _hold_temp(self, descriptor: str) -> str:
        self.temp_serial += 1
        return self._hold_named(f"$u{self.temp_serial}", descriptor)

    def _hold_named(self, name: str, descriptor: str) -> str:
        stored = _jvm_descriptor(descriptor)
        local = self.locals.get(name)
        if local is None:
            local = (self.next_local, stored)
            self.locals[name] = local
            self.next_local += 2 if stored in {"D", "J"} else 1
        elif local[1] != stored:
            raise JasminCompilerError(f"cannot change local {name!r} from {local[1]} to {stored}")
        self._instruction(f"{self._store_opcode(stored)} {local[0]}")
        return name

    def _emit_int(self, value: int) -> None:
        if -1 <= value <= 5:
            name = "m1" if value == -1 else str(value)
            self._instruction(f"iconst_{name}")
            return
        if -128 <= value <= 127:
            self._instruction(f"bipush {value}")
            return
        if -32768 <= value <= 32767:
            self._instruction(f"sipush {value}")
            return
        self._instruction(f"ldc {value}")

    def _load_local(self, name: str) -> None:
        slot, descriptor = self.locals[name]
        self._instruction(f"{self._load_opcode(descriptor)} {slot}")

    def _compatible(self, actual: str, wanted: str) -> bool:
        if actual == "lambda":
            return self._is_interface_type(wanted)
        element = _list_element(actual)
        if element is not None:
            if wanted == _array_descriptor(element) and element.startswith(("L", "[")):
                return True
            actual = _ARRAY_LIST
        if actual == wanted or actual == "null":
            return True
        if wanted == "Ljava/util/Iterator;" and self._is_generator_type(actual):
            return True
        if not (actual.startswith("L") and wanted.startswith("L")):
            return False
        return self._related(_internal_name(actual), _internal_name(wanted), set())

    def _related(self, actual: str, wanted: str, seen: set[str]) -> bool:
        if actual == wanted or actual in seen:
            return actual == wanted
        seen.add(actual)
        local = self.project.get(actual)
        base = local.base if local is not None else None
        if actual == self.class_internal:
            base = base or self.super_internal
        if base and self._related(base, wanted, seen):
            return True
        super_name = self.catalog.super_of(actual)
        if super_name and self._related(super_name, wanted, seen):
            return True
        return any(self._related(name, wanted, seen) for name in self.catalog.interfaces_of(actual))

    def _annotation(self, node: ast.expr) -> str:
        if isinstance(node, ast.Constant) and node.value is None:
            return "V"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except SyntaxError as exc:
                raise self.error(node, f"invalid string annotation {node.value!r}") from exc
            return self._annotation(parsed)
        if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPES:
            return _BUILTIN_TYPES[node.id]
        listed = _listed_element(node)
        if listed is not None:
            return _array_descriptor(self._annotation(listed))
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and self.imports.get(node.value.id) == _JAVA_ITERATOR
        ):
            return _descriptor(_JAVA_ITERATOR)
        return _descriptor(self._resolve_type_name(node))

    def _resolve_type_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            if node.id == self.class_name:
                return self.class_internal
            imported = self.imports.get(node.id)
            if imported is not None:
                return imported
            raise self.error(node, f"unknown type {node.id!r}")
        if isinstance(node, ast.Attribute):
            return f"{self._resolve_type_name(node.value)}${node.attr}"
        raise self.error(node, f"expected a type name, got {type(node).__name__}")

    def _label(self, prefix: str) -> str:
        self.label_counter += 1
        return f"L_{prefix}_{self.label_counter}"

    def _instruction(self, text: str, *, indented: bool = True) -> None:
        self.instructions.append(f"    {text}" if indented else text)

    @staticmethod
    def _load_opcode(descriptor: str) -> str:
        if descriptor in {"I", "Z"}:
            return "iload"
        if descriptor == "F":
            return "fload"
        if descriptor == "D":
            return "dload"
        if descriptor == "J":
            return "lload"
        return "aload"

    @staticmethod
    def _store_opcode(descriptor: str) -> str:
        if descriptor in {"I", "Z"}:
            return "istore"
        if descriptor == "F":
            return "fstore"
        if descriptor == "D":
            return "dstore"
        if descriptor == "J":
            return "lstore"
        return "astore"


def _best_signature(
    candidates: list[_MethodSignature],
    arguments: tuple[str, ...],
    compiler: _Compiler,
) -> _MethodSignature | None:
    exact = [item for item in candidates if item.parameters == arguments]
    if len(exact) == 1:
        return exact[0]
    compatible = [
        item
        for item in candidates
        if len(item.parameters) == len(arguments)
        and all(
            compiler._compatible(actual, wanted) or (actual == "I" and wanted == "J")
            for actual, wanted in zip(arguments, item.parameters, strict=True)
        )
    ]
    if len(compatible) == 1:
        return compatible[0]
    if compatible and len({(item.parameters, item.result) for item in compatible}) == 1:
        return compatible[0]
    if exact:
        return exact[0]
    return None


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
    class_package = _indexed_module_name(source) if functions or (_in_stdlib(source) and len(classes) != 1) else package
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
        parameters = tuple(
            _type_from_expr(argument.annotation, imports, internal, internal.rsplit("/", 1)[-1])
            for argument in item.args.args
            if argument.annotation is not None
        )
        class_name = internal.rsplit("/", 1)[-1]
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
            )
            found[synthetic] = _context_manager_type(synthetic, result)
            continue
        result, serial = _indexed_generator(item, result, generators, internal, serial)
        methods[item.name] = _MethodSignature(
            parameters,
            result,
            opcode="invokestatic",
            owner=internal,
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
    base_type = _type_from_expr(node.bases[0], imports, internal, node.name) if node.bases else None
    base = base_type[1:-1] if base_type and base_type.startswith("L") else base_type
    constructor: tuple[str, ...] = ()
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
            fields[item.target.id] = _type_from_expr(item.annotation, imports, internal, node.name)
        if not isinstance(item, ast.FunctionDef):
            continue
        parameters = tuple(
            _type_from_expr(argument.annotation, imports, internal, node.name)
            for argument in item.args.args[1:]
            if argument.annotation is not None
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
        elif _is_contextmanager_def(item, imports):
            context_serial += 1
            synthetic = f"{internal}$cm${context_serial}"
            methods[item.name] = _MethodSignature(parameters, _descriptor(synthetic), owner=internal)
            found[synthetic] = _context_manager_type(synthetic, result)
        else:
            result, serial = _indexed_generator(item, result, generators, internal, serial)
            methods[item.name] = _MethodSignature(parameters, result, owner=internal)
    found[internal] = LocalType(internal, base, constructor, methods, generators, fields)


def _collect_imports(tree: ast.Module, package: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    statements = list(tree.body)
    for statement in tree.body:
        if isinstance(statement, ast.If) and isinstance(statement.test, ast.Name) and statement.test.id == "TYPE_CHECKING":
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
            raise JasminCompilerError(f"invalid string annotation {node.value!r}") from exc
        if not isinstance(parsed, ast.expr):
            raise JasminCompilerError(f"invalid string annotation {node.value!r}")
        return _type_from_expr(parsed, imports, class_internal, class_name)
    if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPES:
        return _BUILTIN_TYPES[node.id]
    listed = _listed_element(node) if node is not None else None
    if listed is not None:
        return _array_descriptor(_type_from_expr(listed, imports, class_internal, class_name))
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
        return _descriptor(f"{_internal_name(_type_from_expr(node.value, imports, class_internal, class_name))}${node.attr}")
    raise JasminCompilerError(f"unsupported annotation {type(node).__name__}")


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
