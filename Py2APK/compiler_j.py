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

    def error(self, node: ast.AST, message: str) -> JasminCompilerError:
        line = getattr(node, "lineno", "?")
        return JasminCompilerError(f"{self.source}:{line}: {message}")

    def compile(self) -> str:
        classes: list[ast.ClassDef] = []
        for statement in self.tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                self._record_import(statement)
            elif isinstance(statement, ast.If) and self._is_type_checking(statement.test):
                for nested in statement.body:
                    if not isinstance(nested, (ast.Import, ast.ImportFrom)):
                        raise self.error(nested, "TYPE_CHECKING blocks may contain only imports")
                    self._record_import(nested)
            elif isinstance(statement, ast.ClassDef):
                classes.append(statement)
            else:
                raise self.error(statement, f"unsupported module statement {type(statement).__name__}")

        if len(classes) != 1:
            raise JasminCompilerError(
                f"{self.source}: expected exactly one class, found {len(classes)}"
            )
        self._compile_class(classes[0])
        return "\n".join(self.lines) + "\n"

    @staticmethod
    def _is_type_checking(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"

    def _record_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in {"typing"}:
                    raise self.error(node, f"unsupported import {alias.name!r}")
            return

        if node.module == "__future__":
            if any(alias.name != "annotations" for alias in node.names):
                raise self.error(node, "only __future__.annotations is supported")
            return
        if node.module == "typing":
            if any(alias.name != "TYPE_CHECKING" for alias in node.names):
                raise self.error(node, "only typing.TYPE_CHECKING is supported")
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

        module_internal = node.module.replace(".", "/")
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.imports[local_name] = f"{module_internal}/{alias.name}"

    def _compile_class(self, node: ast.ClassDef) -> None:
        if node.decorator_list or node.keywords:
            raise self.error(node, "class decorators and class keywords are unsupported")
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
            elif isinstance(item, ast.Pass):
                continue
            else:
                raise self.error(item, f"unsupported class member {type(item).__name__}")

        self.lines.extend((f".source {self.source.name}", f".class public {self.class_internal}"))
        self.lines.append(f".super {self.super_internal}")
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
            self._compile_method(constructors[0], constructor=True)
        else:
            self._emit_default_constructor()
        for function in functions:
            if function.name != "__init__":
                self._compile_method(function, constructor=False)

    def _method_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        if node.decorator_list:
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

    def _compile_method(self, node: ast.FunctionDef, *, constructor: bool) -> None:
        signature = self._constructor_signature(node) if constructor else self.methods[node.name]
        self.instructions = []
        self.locals = {"self": (0, _descriptor(self.class_internal))}
        self.next_local = 1
        self.list_elements = {}
        self.preview_locals = {"self": _descriptor(self.class_internal)}
        for argument, descriptor in zip(node.args.args[1:], signature.parameters):
            self.locals[argument.arg] = (self.next_local, descriptor)
            self.preview_locals[argument.arg] = descriptor
            self.next_local += 2 if descriptor in {"D", "J"} else 1
        self._classify_lists(node.body)

        if constructor:
            self._instruction("aload_0")
            self._instruction(f"invokespecial {self.super_internal}/<init>()V")
        for statement in node.body:
            self._statement(statement)
        if signature.result == "V":
            self._instruction("return")
        else:
            raise self.error(node, "non-void methods require an explicit supported return")

        method_name = "<init>" if constructor else node.name
        self.lines.append(f".method public {method_name}{signature.descriptor}")
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
        elif isinstance(node, ast.Expr):
            descriptor = self._expression(node.value)
            if descriptor != "V":
                self._instruction("pop2" if descriptor in {"D", "J"} else "pop")
        elif isinstance(node, ast.If):
            self._if_statement(node)
        elif isinstance(node, ast.Pass):
            return
        elif isinstance(node, ast.Return):
            if node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None
            ):
                raise self.error(node, "only empty returns are supported")
            self._instruction("return")
        else:
            raise self.error(node, f"unsupported statement {type(node).__name__}")

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

    def _condition_jump_false(self, node: ast.expr, label: str) -> None:
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise self.error(node, "only single comparisons are supported")
            left, right = self._comparison_operands(node)
            if left not in {"I", "Z"} or right not in {"I", "Z"}:
                raise self.error(node, "only integer/boolean comparisons are supported")
            if isinstance(node.ops[0], ast.Eq):
                self._instruction(f"if_icmpne {label}")
                return
            if isinstance(node.ops[0], ast.NotEq):
                self._instruction(f"if_icmpeq {label}")
                return
            raise self.error(node, f"unsupported comparison {type(node.ops[0]).__name__}")
        descriptor = self._expression(node)
        if descriptor not in {"I", "Z"}:
            raise self.error(node, "if condition must be bool or int")
        self._instruction(f"ifeq {label}")

    def _comparison_operands(self, node: ast.Compare) -> tuple[str, str]:
        """Push a constant before a variable so the constant is the left operand."""
        if isinstance(node.comparators[0], ast.Constant) and not isinstance(node.left, ast.Constant):
            right = self._expression(node.comparators[0])
            left = self._expression(node.left)
            return left, right
        left = self._expression(node.left)
        right = self._expression(node.comparators[0])
        return left, right

    def _assign(self, target: ast.expr, value: ast.expr) -> None:
        self._store(target, self._assigned_value(target, value))

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
        if isinstance(node, ast.List):
            return self._list_literal(node)
        if isinstance(node, ast.Dict):
            return self._dict_literal(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._expression(node.left)
            right = self._expression(node.right)
            if left != _STRING or right != _STRING:
                raise self.error(node, "only string concatenation is supported")
            self._instruction("invokevirtual java/lang/String/concat(Ljava/lang/String;)Ljava/lang/String;")
            return _STRING
        if isinstance(node, ast.JoinedStr):
            return self._joined_string(node)
        raise self.error(node, f"unsupported expression {type(node).__name__}")

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

    def _builtin_call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, "call target must be a known constructor or method")
        name = node.func.id
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
        if name == "len" and len(types) == 1:
            descriptor = self._expression(node.args[0])
            if descriptor == _STRING:
                signature = self._require_method("java/lang/String", "length", (), static=False)
                self._invoke(signature, "length")
                return "I"
            if descriptor.startswith("[") or _list_element(descriptor) is not None:
                if _list_element(descriptor) is not None:
                    signature = self._require_method(_ARRAY_LIST_OWNER, "size", (), static=False, node=node)
                    self._invoke(signature, "size")
                else:
                    self._instruction("arraylength")
                return "I"
            raise self.error(node, f"len() does not accept {descriptor}")
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", "abs", types)
        if name in {"min", "max"} and len(types) == 2 and types[0] == types[1] and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", name, types)
        raise self.error(node, f"unsupported builtin {name}")

    def _builtin_result(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, f"unsupported expression {type(node.func).__name__}")
        name = node.func.id
        types = self._argument_types(node)
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
        if name == "len" and len(types) == 1 and (
            types[0] == _STRING or types[0].startswith("[") or _list_element(types[0]) is not None
        ):
            if types[0] == _STRING and self._find_method("java/lang/String", "length", (), static=False) is None:
                raise self.error(node, "unknown method signature java/lang/String.length")
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

    def _attribute(self, node: ast.Attribute) -> str:
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
        return signature.result

    def _emit_constructor(self, owner: str, node: ast.Call) -> str:
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
        if isinstance(node, ast.Lambda):
            return "lambda"
        if isinstance(node, ast.Attribute):
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
        if isinstance(node, ast.List):
            return _array_descriptor(self._element_type(node))
        if isinstance(node, ast.Dict):
            self._dict_types(node)
            return _LINKED_HASH_MAP
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return _STRING
        if isinstance(node, ast.JoinedStr):
            return _STRING
        if isinstance(node, ast.Call):
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
                        return signature.result
        raise self.error(node, f"unsupported expression {type(node).__name__}")

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

    def _element_type(self, node: ast.List) -> str:
        if not node.elts:
            raise self.error(node, "an empty list has no element type")
        element: str | None = None
        for item in node.elts:
            current = self._type_of(item)
            if current in {"null", "lambda"}:
                raise self.error(item, "a list element needs a value type")
            if element is None:
                element = current
            elif current != element:
                raise self.error(node, "a list needs one element type")
        assert element is not None
        return element

    def _java_array(self, node: ast.List, element: str) -> str:
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
        for key, value in zip(node.keys, node.values):
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
        assert key_type is not None and value_type is not None
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
        for key, value in zip(node.keys, node.values):
            assert key is not None
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
        assert isinstance(value, ast.Attribute)
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

        def origin_of(name: str) -> str:
            seen: set[str] = set()
            while name in origins and origins[name] != name and name not in seen:
                seen.add(name)
                name = origins[name]
            return name

        def walk(body: list[ast.stmt]) -> None:
            for statement in body:
                if isinstance(statement, ast.If):
                    walk(statement.body)
                    walk(statement.orelse)
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
                    origins[target.id] = origin_of(value.id)

        walk(statements)
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
            for argument in node.args:
                if isinstance(argument, ast.Name) and argument.id in origins:
                    escapes.append((node, argument.id))

        mutable: set[str] = set()
        for name in appends | field_names | captured:
            if name in origins:
                mutable.add(origin_of(name))
        changed = True
        while changed:
            changed = False
            for call, arg_name in escapes:
                root = origin_of(arg_name)
                if root in mutable or root not in literals:
                    continue
                if not self._accepts_list_array(call, arg_name, origins, literals, mutable):
                    mutable.add(root)
                    changed = True
        self.mutable_lists = {name for name in origins if origin_of(name) in mutable}
        self.field_list_ids = field_lists

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
        if owner == self.class_internal and name in self.methods and not static:
            candidates.append(self.methods[name])
        local = self.project.get(owner)
        if local is not None and name in local.methods and not static:
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
        for argument, wanted in zip(node.args, expected):
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

    def _sam_method(self, descriptor: str) -> tuple[str, str, tuple[str, ...], str] | None:
        if not (descriptor.startswith("L") and descriptor.endswith(";")):
            return None
        internal = descriptor[1:-1]
        if not self.catalog.is_interface(internal):
            return None
        methods = self.catalog.interface_methods(internal)
        if len(methods) != 1:
            return None
        name, params, result = methods[0]
        return internal, name, parameter_descriptors(params), result

    def _emit_listener(self, node: ast.expr, expected: str) -> None:
        sam = self._sam_method(expected)
        if sam is None:
            raise self.error(node, "a lambda needs an interface with one method; write a class")
        _interface, method_name, parameters, result = sam
        listener = node
        if self._is_method_reference(node):
            assert isinstance(node, ast.Attribute)
            listener = self._method_reference_lambda(node, parameters, result)
        if not isinstance(listener, ast.Lambda):
            raise self.error(node, "a lambda needs an interface with one method; write a class")
        if listener.args.vararg or listener.args.kwarg or listener.args.defaults or listener.args.kwonlyargs:
            raise self.error(node, "lambda arguments must be plain positional names")
        names = [arg.arg for arg in listener.args.args]
        if len(names) != len(parameters):
            raise self.error(node, f"lambda has {len(names)} arguments, the listener method has {len(parameters)}")
        self._emit_lambda(listener, sam[0], method_name, tuple(zip(names, parameters)), result)

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
            (filename, self._lambda_class(node, internal, interface, method_name, parameters, result, captures))
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
        return "\n".join(lines) + "\n"

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
            for actual, wanted in zip(arguments, item.parameters)
        )
    ]
    if len(compatible) == 1:
        return compatible[0]
    if exact:
        return exact[0]
    return None


def index_project_types(source_root: Path) -> dict[str, LocalType]:
    """Read class bases, constructors, and methods from the project sources."""
    found: dict[str, LocalType] = {}
    for source in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        package = _package_for(source, source)
        imports = _collect_imports(tree, package)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or len(node.bases) > 1:
                continue
            internal = "/".join(filter(None, (package, node.name)))
            base_type = _type_from_expr(node.bases[0], imports, internal, node.name) if node.bases else None
            base = base_type[1:-1] if base_type and base_type.startswith("L") else base_type
            constructor: tuple[str, ...] = ()
            methods: dict[str, _MethodSignature] = {}
            for item in node.body:
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
                if item.name == "__init__":
                    constructor = parameters
                else:
                    methods[item.name] = _MethodSignature(parameters, result, owner=internal)
            found[internal] = LocalType(internal, base, constructor, methods)
    return found


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
    if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPES:
        return _BUILTIN_TYPES[node.id]
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
) -> None:
    """Compile one typed Python-like source file into a Jasmin ``.j`` file."""
    source = Path(source)
    output = Path(output)
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except SyntaxError as exc:
        location = f"{source}:{exc.lineno or '?'}"
        raise JasminCompilerError(f"{location}: invalid syntax: {exc.msg}") from exc

    compiler = _Compiler(source, output, tree, catalog, project)
    jasmin = compiler.compile()
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(jasmin, encoding="utf-8")
    for filename, text in compiler.extra_classes:
        _ = (output.parent / filename).write_text(text, encoding="utf-8")
