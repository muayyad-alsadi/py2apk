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
    _immediate_return,
    _return_opcode,
)


class _LambdasMixin(_CompilerBase):
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
            for name, descriptor in captures:
                self._instruction("aload_0")
                self._instruction(f"getfield {internal}/{name} {descriptor}")
                self._instruction(
                    f"{self._store_opcode(descriptor)} {body_locals[name][0]}"
                )
            produced = self._expression(node.body)
            if result == "V":
                if produced != "V":
                    self._instruction("pop2" if produced in {"D", "J"} else "pop")
                self._instruction("return")
            else:
                if produced != result:
                    raise self.error(
                        node,
                        f"lambda has type {produced}, listener method returns {result}",
                    )
                self._instruction(_return_opcode(result))
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
            stub_locals = 1 + sum(
                2 if descriptor in {"D", "J"} else 1 for descriptor in stub_parameters
            )
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
