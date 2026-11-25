"""Arithmetic, unary minus, conditionals, and format strings."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.types import (
    _BOX_METHODS,
    _OBJECT_ARRAY,
    _STRING,
    _STRING_BUILDER,
    JasminCompilerError,
    _format_pattern,
)


class _ExprMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.BinOp: "_yield_or_add",
        ast.Yield: "_yield_or_add",
        ast.JoinedStr: "_joined_string",
        ast.IfExp: "_conditional",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.BinOp: "_type_of_binop",
        ast.JoinedStr: "_type_of_joined",
        ast.IfExp: "_conditional_type",
    }
    UNARY_HANDLERS: ClassVar[dict[type[Any], tuple[str, str]]] = {
        ast.USub: ("_unary_minus", "_unary_minus_type")
    }

    def _unary_minus(self, node: ast.UnaryOp) -> str:
        actual = self._expression(node.operand)
        if actual != "I":
            raise self.error(node, "unary minus needs an int")
        self._instruction("ineg")
        return "I"

    def _unary_minus_type(self, node: ast.UnaryOp) -> str:
        if self._type_of(node.operand) != "I":
            raise self.error(node, "unary minus needs an int")
        return "I"

    def _type_of_joined(self, _node: ast.JoinedStr) -> str:
        return _STRING

    def _yield_or_add(self, node: ast.expr) -> str:
        if isinstance(node, ast.Yield):
            raise self.error(node, "yield expressions are unsupported")
        if not isinstance(node, ast.BinOp):
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        left = self._expression(node.left)
        right = self._expression(node.right)
        if isinstance(node.op, ast.Add) and left == _STRING and right == _STRING:
            self._instruction(
                "invokevirtual java/lang/String/concat(Ljava/lang/String;)Ljava/lang/String;"
            )
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
            raise self.error(
                node, "only string concatenation and integer arithmetic are supported"
            )
        self._instruction(opcode)
        return "I"

    def _joined_string(self, node: ast.JoinedStr) -> str:
        pieces = self._string_pieces(node)
        if any(pattern is not None for _value, pattern, _descriptor in pieces):
            return self._format_string(pieces)
        return self._builder_string(pieces)

    def _string_pieces(
        self, node: ast.JoinedStr
    ) -> list[tuple[ast.expr, str | None, str]]:
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
            signature = self._require_method(
                _STRING_BUILDER, "append", (parameter,), static=False
            )
            self._invoke(signature, "append")
        finished = self._require_method(_STRING_BUILDER, "toString", (), static=False)
        self._invoke(finished, "toString")
        return _STRING

    def _append_parameter(self, actual: str, expected: str) -> str:
        descriptor = actual or expected
        if descriptor in {"I", "Z", "F", "D", "J", _STRING}:
            return descriptor
        if descriptor != "null" and self._compatible(
            descriptor, "Ljava/lang/CharSequence;"
        ):
            return "Ljava/lang/CharSequence;"
        if descriptor == "null" or descriptor.startswith(("L", "[")):
            return "Ljava/lang/Object;"
        raise JasminCompilerError(f"cannot append type {descriptor}")

    def _format_string(self, pieces: list[tuple[ast.expr, str | None, str]]) -> str:
        pattern: list[str] = []
        fields: list[tuple[ast.expr, str]] = []
        for value, conversion, descriptor in pieces:
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and conversion is None
            ):
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
        self._instruction(
            f"invokestatic {signature.owner or 'java/lang/String'}/format{signature.descriptor}"
        )
        return _STRING

    def _conditional_type(self, node: ast.IfExp) -> str:
        return self._common_type(
            node, self._type_of(node.body), self._type_of(node.orelse)
        )

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
            raise self.error(
                node, f"conditional arm has type {produced}, expected {result}"
            )
        if produced.startswith("L") and result.startswith("L"):
            self._instruction(f"checkcast {result[1:-1]}")

    def _type_of_binop(self, node: ast.BinOp) -> str:
        left = self._type_of(node.left)
        right = self._type_of(node.right)
        if isinstance(node.op, ast.Add) and left == _STRING and right == _STRING:
            return _STRING
        if (
            type(node.op) in {ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod}
            and left == "I"
            and right == "I"
        ):
            return "I"
        raise self.error(
            node, "only string concatenation and integer arithmetic are supported"
        )
