"""Compile the project's typed Python-like source subset to Jasmin assembly.

This is intentionally not a general Python compiler.  It accepts the small,
statically typed language used by the bundled Android templates and fails fast
when a construct or API signature is not known.
"""

from __future__ import annotations

import ast
import json
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


def _descriptor(internal_name: str) -> str:
    return f"L{internal_name};"


def _internal_name(descriptor: str) -> str:
    if not (descriptor.startswith("L") and descriptor.endswith(";")):
        raise ValueError(f"not a reference descriptor: {descriptor}")
    return descriptor[1:-1]


def _package_for(source: Path, output: Path) -> str:
    """Infer the JVM package from the conventional src/ or target/j layout."""
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
        for argument, descriptor in zip(node.args.args[1:], signature.parameters):
            self.locals[argument.arg] = (self.next_local, descriptor)
            self.next_local += 2 if descriptor in {"D", "J"} else 1

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
            descriptor = self._expression(node.value)
            self._store(node.targets[0], descriptor)
        elif isinstance(node, ast.AnnAssign):
            if node.value is None:
                raise self.error(node, "local annotated assignments require a value")
            descriptor = self._expression(node.value)
            declared = self._annotation(node.annotation)
            if descriptor != declared:
                if descriptor.startswith("L") and declared.startswith("L"):
                    self._instruction(f"checkcast {_internal_name(declared)}")
                    descriptor = declared
                else:
                    raise self.error(node, f"assignment type {descriptor} does not match {declared}")
            self._store(node.target, declared)
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
            left = self._expression(node.left)
            right = self._expression(node.comparators[0])
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

    def _store(self, target: ast.expr, descriptor: str) -> None:
        if isinstance(target, ast.Name):
            local = self.locals.get(target.id)
            if local is None:
                local = (self.next_local, descriptor)
                self.locals[target.id] = local
                self.next_local += 2 if descriptor in {"D", "J"} else 1
            elif local[1] != descriptor:
                raise self.error(target, f"cannot change local {target.id!r} from {local[1]} to {descriptor}")
            self._instruction(f"{self._store_opcode(descriptor)} {local[0]}")
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            field_type = self.fields.get(target.attr)
            if field_type is None:
                raise self.error(target, f"unknown field {target.attr!r}")
            if descriptor != field_type:
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
            return local[1]
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.List):
            return self._string_array(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._expression(node.left)
            right = self._expression(node.right)
            if left != _STRING or right != _STRING:
                raise self.error(node, "only string concatenation is supported")
            self._instruction("invokevirtual java/lang/String/concat(Ljava/lang/String;)Ljava/lang/String;")
            return _STRING
        raise self.error(node, f"unsupported expression {type(node).__name__}")

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
                raise self.error(node, f"unknown field {node.attr!r}")
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
            raise self.error(node, "call target must be a known constructor or method")
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
        signature = self._find_method(owner, node.func.attr, self._argument_types(node), static=False)
        if signature is None:
            raise self.error(node, f"unknown method signature {owner}.{node.func.attr}")
        self._emit_arguments(node, signature.parameters)
        declaring = signature.owner or owner
        instruction = f"{signature.opcode} {declaring}/{node.func.attr}{signature.descriptor}"
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
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                field_type = self.fields.get(node.attr)
                if field_type is None:
                    raise self.error(node, f"unknown field {node.attr!r}")
                return field_type
            found = self.catalog.field(self._resolve_type_name(node.value), node.attr)
            if found is None:
                raise self.error(node, f"unknown static field {node.attr}")
            return found[0]
        if isinstance(node, ast.List):
            return "[Ljava/lang/String;"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return _STRING
        if isinstance(node, ast.Call):
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
                    signature = self._find_method(
                        _internal_name(receiver),
                        node.func.attr,
                        self._argument_types(node),
                        static=False,
                    )
                    if signature is not None:
                        return signature.result
        raise self.error(node, f"unsupported expression {type(node).__name__}")

    def _string_array(self, node: ast.List) -> str:
        if not node.elts or any(
            not isinstance(item, ast.Constant) or not isinstance(item.value, str) for item in node.elts
        ):
            raise self.error(node, "only string lists are supported, and only as String[] arguments")
        count = ast.Constant(value=len(node.elts))
        ast.copy_location(count, node)
        _ = self._constant(count)
        self._instruction("anewarray java/lang/String")
        for index, item in enumerate(node.elts):
            self._instruction("dup")
            position = ast.Constant(value=index)
            ast.copy_location(position, node)
            _ = self._constant(position)
            _ = self._expression(item)
            self._instruction("aastore")
        return "[Ljava/lang/String;"

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
            actual = self._expression(argument)
            if actual == "I" and wanted == "J":
                self._instruction("i2l")
                actual = "J"
            if not self._compatible(actual, wanted):
                raise self.error(argument, f"argument has type {actual}, expected {wanted}")

    def _compatible(self, actual: str, wanted: str) -> bool:
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

    jasmin = _Compiler(source, output, tree, catalog, project).compile()
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(jasmin, encoding="utf-8")
