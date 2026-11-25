"""Calls, attributes, builtins, and method lookup."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.classfile import parameter_descriptors
from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.generators import (
    _NULLCONTEXT,
    _SUPPRESS,
)
from Py2APK.compiler_j.types import (
    _NUMERIC_CASTS,
    _PARSE_METHODS,
    _STRING,
    _array_descriptor,
    _descriptor,
    _internal_name,
    _java_method_name,
    _list_element,
    _MethodSignature,
    _static_matches,
)


def _best_signature(
    candidates: list[_MethodSignature],
    arguments: tuple[str, ...],
    compiler: _CompilerBase,
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


class _CallsMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Attribute: "_attribute",
        ast.Call: "_call",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Attribute: "_type_of_attribute",
        ast.Call: "_type_call",
        ast.Constant: "_type_constant",
    }

    def _type_constant(self, node: ast.Constant) -> str:
        constant = self._type_of_constant(node)
        if constant is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return constant

    def _type_call(self, node: ast.Call) -> str:
        call = self._type_of_call(node)
        if call is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return call

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
        if (
            name == "range"
            and 1 <= len(types) <= 3
            and all(kind == "I" for kind in types)
        ):
            return self._emit_range(node)
        if name == "len" and len(types) == 1:
            descriptor = self._expression(node.args[0])
            self._emit_len(node, descriptor)
            return "I"
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", "abs", types)
        if (
            name in {"min", "max"}
            and len(types) == 2
            and types[0] == types[1]
            and types[0] in {"I", "F", "D"}
        ):
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
        if (
            name == "range"
            and 1 <= len(types) <= 3
            and all(kind == "I" for kind in types)
        ):
            self.generator_elements["py2apk$gen$1"] = "I"
            return "Lpy2apk$gen$1;"
        if name == "str" and len(types) == 1:
            if (
                self._find_method("java/lang/String", "valueOf", types, static=True)
                is None
            ):
                raise self.error(
                    node, "unknown method signature java/lang/String.valueOf"
                )
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
        if (
            name in {"min", "max"}
            and len(types) == 2
            and types[0] == types[1]
            and types[0] in {"I", "F", "D"}
        ):
            if self._find_method("java/lang/Math", name, types, static=True) is None:
                raise self.error(
                    node, f"unknown method signature java/lang/Math.{name}"
                )
            return types[0]
        raise self.error(node, f"unsupported builtin {name}")

    def _numeric_cast(self, node: ast.Call, actual: str, target: str) -> str:
        opcode = _NUMERIC_CASTS.get((actual, target))
        if opcode is None:
            raise self.error(node, f"cannot convert {actual} to {target}")
        _ = self._expression(node.args[0])
        if opcode:
            self._instruction(opcode)
        return target

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
                    raise self.error(
                        node, "pass the method as a listener argument, or call it"
                    )
                raise self.error(node, f"unknown field {node.attr!r}")
            if self.lambda_body:
                self._load_local("self")
            else:
                self._instruction("aload_0")
            self._instruction(
                f"getfield {self.class_internal}/{node.attr} {field_type}"
            )
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
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(
            node.func.value
        ):
            owner = self._resolve_type_name(node.func.value)
            signature = self._find_method(
                owner, node.func.attr, self._argument_types(node), static=True
            )
            if signature is not None:
                self._emit_arguments(node, signature.parameters)
                self._instruction(
                    f"invokestatic {signature.owner}/{node.func.attr}{signature.descriptor}"
                )
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
                raise self.error(
                    node, f"unknown super call {self.super_internal}.{node.func.attr}"
                )
            self._instruction("aload_0")
            self._emit_arguments(node, signature.parameters)
            self._instruction(
                f"invokespecial {signature.owner or self.super_internal}/{node.func.attr}{signature.descriptor}"
            )
            return signature.result
        receiver_type = self._expression(node.func.value)
        if not receiver_type.startswith("L"):
            raise self.error(
                node, f"method receiver must be an object, got {receiver_type}"
            )
        owner = _internal_name(receiver_type)
        method_name = _java_method_name(owner, node.func.attr)
        signature = self._find_method(
            owner, method_name, self._argument_types(node), static=False
        )
        if signature is None:
            raise self.error(node, f"unknown method signature {owner}.{method_name}")
        self._emit_arguments(node, signature.parameters)
        declaring = signature.owner or owner
        instruction = (
            f"{signature.opcode} {declaring}/{method_name}{signature.descriptor}"
        )
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

    def _type_of_call(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name) and not self._is_type_reference(node.func):
            return self._builtin_result(node)
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(
            node.func.value
        ):
            owner = self._resolve_type_name(node.func.value)
            signature = self._find_method(
                owner, node.func.attr, self._argument_types(node), static=True
            )
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

    def _signature_for_call(
        self, node: ast.Call, arguments: tuple[str, ...]
    ) -> _MethodSignature | None:
        if node.keywords or not isinstance(node.func, ast.Attribute):
            return None
        func = node.func
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            return self._find_method(
                self.class_internal, func.attr, arguments, static=False
            )
        if self._is_type_reference(func.value):
            return self._find_method(
                self._resolve_type_name(func.value), func.attr, arguments, static=True
            )
        receiver = self._preview_type(func.value)
        if receiver is None or not receiver.startswith("L"):
            return None
        return self._find_method(
            _internal_name(receiver), func.attr, arguments, static=False
        )

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
        signature = self._find_method(
            owner, "<init>", self._argument_types(node), static=False
        )
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
        if (
            owner == self.class_internal
            and name in self.methods
            and _static_matches(self.methods[name], static)
        ):
            candidates.append(self.methods[name])
        local = self.project.get(owner)
        if (
            local is not None
            and name in local.methods
            and _static_matches(local.methods[name], static)
        ):
            candidates.append(local.methods[name])
        for params, result, method_owner, is_static, opcode in self.catalog.methods(
            owner, name
        ):
            if is_static != static and name != "<init>":
                continue
            candidates.append(
                _MethodSignature(
                    parameter_descriptors(params), result, opcode, method_owner
                )
            )
        match = _best_signature(candidates, arguments, self)
        if match is not None:
            return match
        bases: list[str] = []
        if local is not None and local.base:
            bases.append(local.base)
        if owner == self.class_internal and self.super_internal not in {
            "",
            "java/lang/Object",
        }:
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
            raise self.error(
                node, f"expected {len(expected)} arguments, got {len(node.args)}"
            )
        for argument, wanted in zip(node.args, expected, strict=True):
            if isinstance(argument, ast.Lambda) or self._is_method_reference(argument):
                self._emit_listener(argument, wanted)
                continue
            actual = self._expression(argument)
            element = _list_element(actual)
            if element is not None and wanted == _array_descriptor(element):
                if not element.startswith(("L", "[")):
                    raise self.error(
                        argument, "a changed number list cannot be passed as an array"
                    )
                self._emit_to_array(element)
                actual = wanted
            if actual == "I" and wanted == "J":
                self._instruction("i2l")
                actual = "J"
            if not self._compatible(actual, wanted):
                raise self.error(
                    argument, f"argument has type {actual}, expected {wanted}"
                )
