"""Listener lambdas and method references."""

from __future__ import annotations

import ast
import json
from typing import Any, ClassVar

from Py2APK.classfile import parameter_descriptors
from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.generators import (
    _loaded_names,
)
from Py2APK.compiler_j.types import (
    _STRING,
    _UNBOX_METHODS,
    JasminCompilerError,
    _function_type,
    _immediate_return,
    _jvm_descriptor,
    _list_element,
    _MethodSignature,
    _parse_function_type,
    _return_opcode,
)


class _LambdasMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Lambda: "_emit_lambda_value"
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {ast.Lambda: "_type_of_lambda"}

    def _type_of_lambda(self, _node: ast.Lambda) -> str:
        return "lambda"

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
            raise self.error(
                node, "a lambda needs an interface with one method; write a class"
            )
        interface, declared = found
        if self._is_method_reference(node):
            if not isinstance(node, ast.Attribute):
                raise self.error(node, "a method reference must be an attribute")
            chosen, listener = self._method_reference_choice(node, declared)
        else:
            chosen = self._lambda_choice(node, declared)
            listener = node
        if not isinstance(listener, ast.Lambda):
            raise self.error(
                node, "a lambda needs an interface with one method; write a class"
            )
        if (
            listener.args.vararg
            or listener.args.kwarg
            or listener.args.defaults
            or listener.args.kwonlyargs
        ):
            raise self.error(node, "lambda arguments must be plain positional names")
        method_name, parameters, result = chosen
        names = [arg.arg for arg in listener.args.args]
        if len(names) != len(parameters):
            raise self.error(
                node,
                f"lambda has {len(names)} arguments, the listener method has {len(parameters)}",
            )
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
            raise self.error(
                node, "a lambda needs an interface with one method; write a class"
            )
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
            raise self.error(
                node, "lambda matches no method of this interface; write a class"
            )
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
            item
            for item in declared
            if item[1] == method.parameters and item[2] == method.result
        ]
        if len(matches) != 1:
            raise self.error(
                node, f"{node.attr} does not match the listener method; write a lambda"
            )
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
            raise self.error(
                node, f"{node.attr} does not match the listener method; write a lambda"
            )
        arguments = ", ".join(f"a{index}" for index in range(len(parameters)))
        call = f"self.{node.attr}({arguments})" if arguments else f"self.{node.attr}()"
        header = f"lambda {arguments}: {call}" if arguments else f"lambda: {call}"
        parsed = ast.parse(header, mode="eval").body
        if not isinstance(parsed, ast.Lambda):
            raise self.error(
                node, f"{node.attr} does not match the listener method; write a lambda"
            )
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
        missing = [
            name for name in _loaded_names(node.body, bound) if name not in self.locals
        ]
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
                    node,
                    internal,
                    interface,
                    method_name,
                    parameters,
                    result,
                    captures,
                    stubs,
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
        body, limit = self._compile_lambda_body(
            node,
            internal,
            parameters,
            result,
            captures,
            "lambda has type {produced}, listener method returns {result}",
        )
        parameter_descriptor = "".join(descriptor for _name, descriptor in parameters)
        methods = [
            (
                f".method public {method_name}({parameter_descriptor}){result}",
                body,
                limit,
                32,
            )
        ]
        for stub_name, stub_parameters, stub_result in stubs:
            stub_descriptor = "".join(stub_parameters)
            stub_locals = 1 + sum(
                2 if descriptor in {"D", "J"} else 1 for descriptor in stub_parameters
            )
            methods.append(
                (
                    f".method public {stub_name}({stub_descriptor}){stub_result}",
                    list(_immediate_return(stub_result)),
                    stub_locals,
                    1,
                )
            )
        return self._emit_synthetic_class(
            self.source.name,
            internal,
            "java/lang/Object",
            interface,
            captures,
            methods,
        )

    def _compile_lambda_body(
        self,
        node: ast.Lambda,
        internal: str,
        parameters: tuple[tuple[str, str], ...],
        result: str,
        captures: list[tuple[str, str]],
        mismatch: str,
    ) -> tuple[list[str], int]:
        body_locals: dict[str, tuple[int, str]] = {}
        slot = 1
        for name, descriptor in parameters:
            body_locals[name] = (slot, descriptor)
            slot += 2 if descriptor in {"D", "J"} else 1
        for name, descriptor in captures:
            body_locals[name] = (slot, descriptor)
            slot += 2 if descriptor in {"D", "J"} else 1
        saved = (
            self.instructions,
            self.locals,
            self.next_local,
            self.lambda_body,
            self.list_elements,
        )
        self.instructions = []
        self.locals = body_locals
        self.next_local = slot
        self.lambda_body = True
        self.list_elements = {
            name: self.list_elements[name]
            for name, _descriptor in captures
            if name in self.list_elements
        }
        try:
            self._load_captures(internal, captures, body_locals)
            produced = self._expression(node.body)
            self._finish_lambda_result(node, produced, result, mismatch)
            body = list(self.instructions)
            limit = self.next_local
        finally:
            (
                self.instructions,
                self.locals,
                self.next_local,
                self.lambda_body,
                self.list_elements,
            ) = saved
        return body, limit

    def _load_captures(
        self,
        internal: str,
        captures: list[tuple[str, str]],
        body_locals: dict[str, tuple[int, str]],
    ) -> None:
        for name, descriptor in captures:
            self._instruction("aload_0")
            self._instruction(f"getfield {internal}/{name} {descriptor}")
            self._instruction(
                f"{self._store_opcode(descriptor)} {body_locals[name][0]}"
            )

    def _finish_lambda_result(
        self, node: ast.Lambda, produced: str, result: str, mismatch: str
    ) -> None:
        if result == "V":
            if produced != "V":
                self._instruction("pop2" if produced in {"D", "J"} else "pop")
            self._instruction("return")
            return
        if produced != result:
            raise self.error(node, mismatch.format(produced=produced, result=result))
        self._instruction(_return_opcode(result))

    def _emit_synthetic_class(
        self,
        source: str,
        internal: str,
        super_name: str,
        interface: str | None,
        captures: list[tuple[str, str]],
        methods: list[tuple[str, list[str], int, int]],
    ) -> str:
        lines = _synthetic_preamble(source, internal, super_name, interface)
        for name, descriptor in captures:
            lines.append(f".field private {json.dumps(name)} {descriptor}")
        if captures:
            lines.append("")
        lines.extend(self._synthetic_init(internal, super_name, captures))
        for header, body, limit, stack in methods:
            lines.extend(
                (
                    header,
                    f"    .limit stack {stack}",
                    f"    .limit locals {max(limit, 1)}",
                )
            )
            lines.extend(body)
            lines.extend((".end method", ""))
        return "\n".join(lines) + "\n"

    def _synthetic_init(
        self,
        internal: str,
        super_name: str,
        captures: list[tuple[str, str]],
    ) -> list[str]:
        capture_desc = "".join(descriptor for _name, descriptor in captures)
        init_locals = 1 + sum(
            2 if descriptor in {"D", "J"} else 1 for _name, descriptor in captures
        )
        lines = [
            f".method public <init>({capture_desc})V",
            "    .limit stack 32",
            f"    .limit locals {init_locals}",
            "    aload_0",
            f"    invokespecial {super_name}/<init>()V",
        ]
        init_slot = 1
        for name, descriptor in captures:
            lines.append("    aload_0")
            lines.append(f"    {self._load_opcode(descriptor)} {init_slot}")
            lines.append(f"    putfield {internal}/{name} {descriptor}")
            init_slot += 2 if descriptor in {"D", "J"} else 1
        lines.extend(("    return", ".end method", ""))
        return lines

    def _emit_lambda_value(self, node: ast.Lambda) -> str:
        if _lambda_has_parameters(node):
            raise self.error(
                node,
                "a lambda with parameters requires a Callable annotation "
                "or function-typed parameter",
            )
        result = _inferred_lambda_result(self._type_of(node.body))
        return self._emit_function_lambda(node, _function_type((), result))

    def _emit_function_lambda(self, node: ast.Lambda, expected: str) -> str:
        parsed = _parse_function_type(expected)
        if parsed is None:
            raise self.error(node, "expected a Callable type")
        internal, parameters, result = parsed
        self._require_plain_lambda(node, len(parameters))
        names = [arg.arg for arg in node.args.args]
        captures = self._function_captures(node, set(names))
        self._ensure_function_base(internal, parameters, result)
        self.lambda_serial += 1
        owner = f"{self.class_internal}$fn${self.lambda_serial}"
        simple = self.class_internal.rsplit("/", 1)[-1]
        paired = tuple(zip(names, parameters, strict=True))
        body, limit = self._compile_lambda_body(
            node,
            owner,
            paired,
            result,
            captures,
            "lambda has type {produced}, Callable returns {result}",
        )
        header = f".method public call({''.join(parameters)}){result}"
        text = self._emit_synthetic_class(
            self.source.name,
            owner,
            internal,
            None,
            captures,
            [(header, body, limit, 32)],
        )
        self._push_function_value(owner, captures)
        self.extra_classes.append((f"{simple}$fn${self.lambda_serial}.j", text))
        return expected

    def _require_plain_lambda(self, node: ast.Lambda, count: int) -> None:
        if (
            node.args.vararg
            or node.args.kwarg
            or node.args.defaults
            or node.args.kwonlyargs
        ):
            raise self.error(node, "lambda arguments must be plain positional names")
        found = len(node.args.args)
        if found != count:
            raise self.error(
                node, f"lambda has {found} arguments, Callable has {count}"
            )

    def _function_captures(
        self, node: ast.Lambda, bound: set[str]
    ) -> list[tuple[str, str]]:
        loaded = _loaded_names(node.body, bound)
        missing = [
            name
            for name in loaded
            if name not in self.locals and name not in _BUILTIN_CALLS
        ]
        if missing:
            raise self.error(node, f"unknown local {missing[0]!r}")
        captured = [name for name in loaded if name in self.non_final_locals]
        if captured:
            raise self.error(
                node,
                f"captured local {captured[0]!r} is reassigned; "
                "only effectively final locals can be captured",
            )
        return [(name, self.locals[name][1]) for name in loaded if name in self.locals]

    def _ensure_function_base(
        self, internal: str, parameters: tuple[str, ...], result: str
    ) -> None:
        if internal in self.emitted_function_bases:
            return
        self.emitted_function_bases.add(internal)
        self.extra_classes.append(
            (f"{internal}.j", _function_base_class(internal, parameters, result))
        )

    def _push_function_value(
        self, internal: str, captures: list[tuple[str, str]]
    ) -> None:
        descriptor = "".join(descriptor for _name, descriptor in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, capture_type in captures:
            slot = self.locals[name][0]
            self._instruction(f"{self._load_opcode(capture_type)} {slot}")
        self._instruction(f"invokespecial {internal}/<init>({descriptor})V")

    def _function_ref_binding(
        self, name: str, node: ast.AST
    ) -> tuple[str, str, _MethodSignature] | None:
        if name in self.locals:
            return None
        if name in self.methods and self.methods[name].opcode == "invokestatic":
            signature = self.methods[name]
            return signature.owner or self.class_internal, name, signature
        imported = self.imported_functions.get(name)
        if imported is None:
            return None
        owner, method = imported
        if owner == "java/lang/Math":
            raise self.error(
                node, "overloaded java.lang.Math function references are unsupported"
            )
        typed = self.project.get(owner)
        signature = typed.methods.get(method) if typed is not None else None
        if signature is None:
            raise self.error(node, f"unknown function {owner}.{method}")
        return owner, method, signature

    def _emit_function_ref(self, node: ast.Name) -> str:
        binding = self._function_ref_binding(node.id, node)
        if binding is None:
            raise self.error(node, f"unknown local {node.id!r}")
        owner, method, signature = binding
        expected = _function_type(signature.parameters, signature.result)
        parsed = _parse_function_type(expected)
        if parsed is None:
            raise self.error(node, "expected a Callable type")
        internal, parameters, result = parsed
        self._ensure_function_base(internal, parameters, result)
        self.lambda_serial += 1
        concrete = f"{self.class_internal}$fn${self.lambda_serial}"
        simple = self.class_internal.rsplit("/", 1)[-1]
        body = self._static_call_body(owner, method, parameters, result)
        header = f".method public call({''.join(parameters)}){result}"
        text = self._emit_synthetic_class(
            self.source.name,
            concrete,
            internal,
            None,
            [],
            [(header, body, _local_limit(parameters), 32)],
        )
        self._push_function_value(concrete, [])
        self.extra_classes.append((f"{simple}$fn${self.lambda_serial}.j", text))
        return expected

    def _static_call_body(
        self, owner: str, method: str, parameters: tuple[str, ...], result: str
    ) -> list[str]:
        lines: list[str] = []
        slot = 1
        for descriptor in parameters:
            lines.append(f"    {self._load_opcode(descriptor)} {slot}")
            slot += 2 if descriptor in {"D", "J"} else 1
        joined = "".join(parameters)
        lines.append(f"    invokestatic {owner}/{method}({joined}){result}")
        lines.append(f"    {_return_opcode(result)}")
        return lines

    def _infer_lambda_result(
        self, node: ast.Lambda, param_types: tuple[str, ...]
    ) -> str:
        self._require_plain_lambda(node, len(param_types))
        names = [arg.arg for arg in node.args.args]
        _ = self._function_captures(node, set(names))
        saved_locals = dict(self.locals)
        saved_lists = dict(self.list_elements)
        try:
            for name, descriptor in zip(names, param_types, strict=True):
                self.locals[name] = (0, _jvm_descriptor(descriptor))
                self.list_elements.pop(name, None)
                element = _list_element(descriptor)
                if element is not None:
                    self.list_elements[name] = element
            return _inferred_lambda_result(self._type_of(node.body))
        finally:
            self.locals = saved_locals
            self.list_elements = saved_lists

    def _emit_comparator(
        self,
        node: ast.AST,
        element: str,
        key_local: str | None,
        key_descriptor: str | None,
        key_result: str | None,
        reverse_local: str | None,
    ) -> None:
        compare_type = element if key_result is None else key_result
        _require_ordering(self, node, compare_type)
        self.lambda_serial += 1
        simple = self.class_internal.rsplit("/", 1)[-1]
        internal = f"{self.class_internal}$cmp${self.lambda_serial}"
        captures: list[tuple[str, str]] = []
        if key_local is not None and key_descriptor is not None:
            captures.append(("key", key_descriptor))
        if reverse_local is not None:
            captures.append(("reverse", "Z"))
        body = _comparator_instructions(
            internal,
            element,
            key_descriptor,
            key_result,
            reverse_local is not None,
        )
        header = ".method public compare(Ljava/lang/Object;Ljava/lang/Object;)I"
        text = self._emit_synthetic_class(
            self.source.name,
            internal,
            "java/lang/Object",
            "java/util/Comparator",
            captures,
            [(header, body, _comparator_limit(compare_type), 8)],
        )
        self._push_comparator(internal, captures, key_local, reverse_local)
        self.extra_classes.append((f"{simple}$cmp${self.lambda_serial}.j", text))

    def _push_comparator(
        self,
        internal: str,
        captures: list[tuple[str, str]],
        key_local: str | None,
        reverse_local: str | None,
    ) -> None:
        sources = {"key": key_local, "reverse": reverse_local}
        descriptor = "".join(kind for _name, kind in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, capture_type in captures:
            local = sources[name]
            if local is None:
                raise JasminCompilerError(f"missing comparator capture {name}")
            slot = self.locals[local][0]
            self._instruction(f"{self._load_opcode(capture_type)} {slot}")
        self._instruction(f"invokespecial {internal}/<init>({descriptor})V")


_BUILTIN_CALLS = {
    "abs",
    "bool",
    "cast",
    "chr",
    "dict",
    "double",
    "filter",
    "float",
    "int",
    "len",
    "list",
    "map",
    "max",
    "min",
    "range",
    "set",
    "sorted",
    "str",
    "tuple",
}


def _synthetic_preamble(
    source: str, internal: str, super_name: str, interface: str | None
) -> list[str]:
    lines = [
        f".source {source}",
        f".class public {internal}",
        f".super {super_name}",
    ]
    if interface is not None:
        lines.append(f".implements {interface}")
    lines.append("")
    return lines


def _function_base_class(
    internal: str, parameters: tuple[str, ...], result: str
) -> str:
    descriptor = "".join(parameters)
    lines = [
        ".source py2apk$fn",
        f".class public abstract {internal}",
        ".super java/lang/Object",
        "",
        ".method public <init>()V",
        "    .limit stack 1",
        "    .limit locals 1",
        "    aload_0",
        "    invokespecial java/lang/Object/<init>()V",
        "    return",
        ".end method",
        "",
        f".method public abstract call({descriptor}){result}",
        ".end method",
        "",
    ]
    return "\n".join(lines) + "\n"


def _lambda_has_parameters(node: ast.Lambda) -> bool:
    return bool(
        node.args.args
        or node.args.vararg
        or node.args.kwarg
        or node.args.defaults
        or node.args.kwonlyargs
    )


def _inferred_lambda_result(produced: str) -> str:
    if produced == "null":
        return "V"
    if produced.startswith("arraylist:"):
        raise JasminCompilerError("a stored lambda cannot return a list")
    return produced


def _require_ordering(compiler: _CompilerBase, node: ast.AST, kind: str) -> None:
    if kind == "I":
        _ = compiler._require_method(
            "java/lang/Integer", "compare", ("I", "I"), static=True, node=node
        )
        return
    if kind == "F":
        _ = compiler._require_method(
            "java/lang/Float", "compare", ("F", "F"), static=True, node=node
        )
        return
    if kind == "D":
        _ = compiler._require_method(
            "java/lang/Double", "compare", ("D", "D"), static=True, node=node
        )
        return
    if kind == "J":
        return
    if kind == _STRING:
        _ = compiler._require_method(
            "java/lang/String", "compareTo", (_STRING,), static=False, node=node
        )
        return
    raise compiler.error(node, "sorted() needs int, float, double, long, or str")


def _comparator_limit(kind: str) -> int:
    width = 2 if kind in {"D", "J"} else 1
    return 3 + width + width


def _comparator_instructions(
    internal: str,
    element: str,
    key_descriptor: str | None,
    key_result: str | None,
    reverse: bool,
) -> list[str]:
    kind = element if key_result is None else key_result
    lines: list[str] = _reverse_swap(internal) if reverse else []
    left = 3
    width = 2 if kind in {"D", "J"} else 1
    right = left + width
    lines.extend(_ordered_value(internal, 1, element, key_descriptor, key_result))
    lines.extend(_store_kind(left, kind))
    lines.extend(_ordered_value(internal, 2, element, key_descriptor, key_result))
    lines.extend(_store_kind(right, kind))
    lines.extend(_compare_kind(kind, left, right))
    return lines


def _reverse_swap(internal: str) -> list[str]:
    return [
        "    aload_0",
        f"    getfield {internal}/reverse Z",
        "    ifeq L_rev",
        "    aload_1",
        "    aload_2",
        "    astore_1",
        "    astore_2",
        "L_rev:",
    ]


def _ordered_value(
    internal: str,
    arg: int,
    element: str,
    key_descriptor: str | None,
    key_result: str | None,
) -> list[str]:
    if key_descriptor is None or key_result is None:
        return [f"    aload_{arg}", *_cast_unbox(element)]
    parsed = _parse_function_type(key_descriptor)
    if parsed is None:
        raise JasminCompilerError("expected a Callable type")
    fn_internal, params, result = parsed
    call = f"invokevirtual {fn_internal}/call({''.join(params)}){result}"
    return [
        "    aload_0",
        f"    getfield {internal}/key {key_descriptor}",
        f"    aload_{arg}",
        *_cast_unbox(element),
        f"    {call}",
    ]


def _cast_unbox(descriptor: str) -> list[str]:
    unbox = _UNBOX_METHODS.get(descriptor)
    if unbox is not None:
        owner, name, signature = unbox
        return [
            f"    checkcast {owner}",
            f"    invokevirtual {owner}/{name}{signature}",
        ]
    if descriptor.startswith("["):
        return [f"    checkcast {descriptor}"]
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return [f"    checkcast {descriptor[1:-1]}"]
    raise JasminCompilerError(f"cannot unbox {descriptor}")


def _store_kind(slot: int, kind: str) -> list[str]:
    opcode = {
        "I": "istore",
        "Z": "istore",
        "F": "fstore",
        "D": "dstore",
        "J": "lstore",
    }.get(kind, "astore")
    return [f"    {opcode} {slot}"]


def _compare_kind(kind: str, left: int, right: int) -> list[str]:
    if kind == "I":
        return [
            f"    iload {left}",
            f"    iload {right}",
            "    invokestatic java/lang/Integer/compare(II)I",
            "    ireturn",
        ]
    if kind == "F":
        return [
            f"    fload {left}",
            f"    fload {right}",
            "    invokestatic java/lang/Float/compare(FF)I",
            "    ireturn",
        ]
    if kind == "D":
        return [
            f"    dload {left}",
            f"    dload {right}",
            "    invokestatic java/lang/Double/compare(DD)I",
            "    ireturn",
        ]
    if kind == "J":
        return [
            f"    lload {left}",
            f"    lload {right}",
            "    lcmp",
            "    ireturn",
        ]
    if kind == _STRING:
        return [
            f"    aload {left}",
            f"    aload {right}",
            "    invokevirtual java/lang/String/compareTo(Ljava/lang/String;)I",
            "    ireturn",
        ]
    raise JasminCompilerError(f"cannot compare {kind}")


def _local_limit(parameters: tuple[str, ...]) -> int:
    slot = 1
    for descriptor in parameters:
        slot += 2 if descriptor in {"D", "J"} else 1
    return slot


def _non_final_names(node: ast.FunctionDef) -> set[str]:
    """Return locals that a nested lambda must not capture."""
    parameters = _parameter_names(node)
    counts: dict[str, int] = {}
    looped: set[str] = set()
    for statement in node.body:
        _walk_assignments(statement, in_loop=False, counts=counts, looped=looped)
    chosen = {name for name, count in counts.items() if count > 1}
    chosen.update(looped)
    chosen.update(name for name in parameters if counts.get(name, 0) > 0)
    return chosen


def _parameter_names(node: ast.FunctionDef) -> set[str]:
    names = {arg.arg for arg in node.args.args}
    names.update(arg.arg for arg in node.args.posonlyargs)
    names.update(arg.arg for arg in node.args.kwonlyargs)
    if node.args.vararg is not None:
        names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.add(node.args.kwarg.arg)
    return names


def _walk_assignments(
    node: ast.AST,
    *,
    in_loop: bool,
    counts: dict[str, int],
    looped: set[str],
) -> None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
        return
    if isinstance(node, ast.For | ast.AsyncFor):
        _note_targets(node.target, in_loop=True, counts=counts, looped=looped)
        _walk_assignments(node.iter, in_loop=in_loop, counts=counts, looped=looped)
        for child in (*node.body, *node.orelse):
            _walk_assignments(child, in_loop=True, counts=counts, looped=looped)
        return
    if isinstance(node, ast.While):
        _walk_assignments(node.test, in_loop=in_loop, counts=counts, looped=looped)
        for child in (*node.body, *node.orelse):
            _walk_assignments(child, in_loop=True, counts=counts, looped=looped)
        return
    if isinstance(node, ast.Assign):
        for target in node.targets:
            _note_targets(target, in_loop=in_loop, counts=counts, looped=looped)
        _walk_assignments(node.value, in_loop=in_loop, counts=counts, looped=looped)
        return
    if isinstance(node, ast.AnnAssign):
        _note_targets(node.target, in_loop=in_loop, counts=counts, looped=looped)
        if node.value is not None:
            _walk_assignments(node.value, in_loop=in_loop, counts=counts, looped=looped)
        return
    if isinstance(node, ast.AugAssign):
        _note_targets(node.target, in_loop=in_loop, counts=counts, looped=looped)
        _walk_assignments(node.value, in_loop=in_loop, counts=counts, looped=looped)
        return
    if isinstance(node, ast.With):
        for item in node.items:
            if item.optional_vars is not None:
                _note_targets(
                    item.optional_vars, in_loop=in_loop, counts=counts, looped=looped
                )
            _walk_assignments(
                item.context_expr, in_loop=in_loop, counts=counts, looped=looped
            )
        for child in node.body:
            _walk_assignments(child, in_loop=in_loop, counts=counts, looped=looped)
        return
    if isinstance(node, ast.ExceptHandler):
        if isinstance(node.name, str):
            _note_name(node.name, in_loop=in_loop, counts=counts, looped=looped)
        for child in node.body:
            _walk_assignments(child, in_loop=in_loop, counts=counts, looped=looped)
        return
    for child in ast.iter_child_nodes(node):
        _walk_assignments(child, in_loop=in_loop, counts=counts, looped=looped)


def _note_targets(
    node: ast.AST,
    *,
    in_loop: bool,
    counts: dict[str, int],
    looped: set[str],
) -> None:
    for name in _store_names(node):
        _note_name(name, in_loop=in_loop, counts=counts, looped=looped)


def _note_name(
    name: str,
    *,
    in_loop: bool,
    counts: dict[str, int],
    looped: set[str],
) -> None:
    counts[name] = counts.get(name, 0) + 1
    if in_loop:
        looped.add(name)


def _store_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple | ast.List):
        names: list[str] = []
        for element in node.elts:
            names.extend(_store_names(element))
        return names
    if isinstance(node, ast.Starred):
        return _store_names(node.value)
    return []
