"""Comparisons, truth tests, boolean operators, and len."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.types import (
    _ARRAY_LIST_OWNER,
    _HASH_SET,
    _HASH_SET_OWNER,
    _STRING,
    _WIDEN,
    _equality_width,
    _internal_name,
    _jvm_descriptor,
    _list_element,
    _MethodSignature,
    _numeric,
    _reference,
)


class _CompareMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.BoolOp: "_bool_value",
        ast.Compare: "_compare_value",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.BoolOp: "_bool_type",
        ast.Compare: "_compare_type",
    }
    UNARY_HANDLERS: ClassVar[dict[type[Any], tuple[str, str]]] = {
        ast.Not: ("_unary_not", "_unary_not_type")
    }
    _ORDERED_FALSE: ClassVar[dict[type[Any], str]] = {
        ast.Lt: "if_icmpge",
        ast.LtE: "if_icmpgt",
        ast.Gt: "if_icmple",
        ast.GtE: "if_icmplt",
    }

    def _compare_value(self, node: ast.Compare) -> str:
        return self._condition_as_bool(node)

    def _bool_value(self, node: ast.BoolOp) -> str:
        return self._condition_as_bool(node)

    def _unary_not(self, node: ast.UnaryOp) -> str:
        return self._condition_as_bool(node)

    def _unary_not_type(self, node: ast.UnaryOp) -> str:
        return self._condition_type(node.operand)

    def _compare_type(self, node: ast.Compare) -> str:
        if len(node.ops) != len(node.comparators) or not node.ops:
            raise self.error(node, "only single comparisons are supported")
        left = self._type_of(node.left)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = self._type_of(right_node)
            self._check_compare_link(node, op, left, right)
            left = right
        return "Z"

    def _bool_type(self, node: ast.BoolOp) -> str:
        if not isinstance(node.op, ast.And | ast.Or):
            raise self.error(
                node, f"unsupported boolean operator {type(node.op).__name__}"
            )
        for value in node.values:
            _ = self._condition_type(value)
        return "Z"

    def _condition_type(self, node: ast.expr) -> str:
        """Return ``Z`` when ``node`` is a legal condition. Does not emit."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self._condition_type(node.operand)
        if isinstance(node, ast.Compare):
            return self._compare_type(node)
        if isinstance(node, ast.BoolOp):
            return self._bool_type(node)
        preview = self._type_of(node)
        if preview in {"I", "Z"} or self._supports_truth(preview):
            return "Z"
        raise self.error(node, "if condition must be bool or int")

    def _condition_as_bool(self, node: ast.expr) -> str:
        """Leave 1 or 0 for a condition. ``if`` and ``while`` keep the jump."""
        false_label = self._label("false")
        done = self._label("bool")
        self._condition_jump_false(node, false_label)
        self._instruction("iconst_1")
        self._instruction(f"goto {done}")
        self._instruction(f"{false_label}:", indented=False)
        self._instruction("iconst_0")
        self._instruction(f"{done}:", indented=False)
        return "Z"

    def _check_compare_link(
        self, node: ast.Compare, op: ast.cmpop, left: str, right: str
    ) -> None:
        if type(op) in self._ORDERED_FALSE:
            if left not in {"I", "Z"} or right not in {"I", "Z"}:
                raise self.error(
                    node, "only integer/boolean comparisons are supported"
                )
            return
        if isinstance(op, ast.Eq | ast.NotEq):
            numeric = _numeric(left) and _numeric(right)
            refs = _reference(left) and _reference(right)
            mixed = (_reference(left) and _numeric(right)) or (
                _numeric(left) and _reference(right)
            )
            if (left in {"I", "Z"} and right in {"I", "Z"}) or numeric or refs or mixed:
                return
            raise self.error(node, "only integer/boolean comparisons are supported")
        if isinstance(op, ast.Is | ast.IsNot):
            refs = _reference(left) and _reference(right)
            mixed = (_reference(left) and _numeric(right)) or (
                _numeric(left) and _reference(right)
            )
            if refs or mixed:
                return
            raise self.error(node, "is compares references")
        raise self.error(node, f"unsupported comparison {type(op).__name__}")

    def _condition_jump_false(self, node: ast.expr, label: str) -> None:
        if isinstance(node, ast.BoolOp):
            self._bool_jump_false(node, label)
            return
        if isinstance(node, ast.Compare):
            if len(node.ops) > 1 and len(node.comparators) == len(node.ops):
                self._chained_compare_jump(node, label)
                return
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise self.error(node, "only single comparisons are supported")
            left, right, swapped = self._comparison_operands(node)
            opcode = self._ORDERED_FALSE.get(type(node.ops[0]))
            if opcode is not None:
                if left not in {"I", "Z"} or right not in {"I", "Z"}:
                    raise self.error(
                        node, "only integer/boolean comparisons are supported"
                    )
                self._instruction(f"{opcode} {label}")
                return
            if isinstance(node.ops[0], ast.Eq):
                self._equality_jump(
                    node, left, right, label, equal=True, swapped=swapped
                )
                return
            if isinstance(node.ops[0], ast.NotEq):
                self._equality_jump(
                    node, left, right, label, equal=False, swapped=swapped
                )
                return
            if isinstance(node.ops[0], ast.Is):
                self._identity_jump(
                    node, left, right, label, same=True, swapped=swapped
                )
                return
            if isinstance(node.ops[0], ast.IsNot):
                self._identity_jump(
                    node, left, right, label, same=False, swapped=swapped
                )
                return
            raise self.error(
                node, f"unsupported comparison {type(node.ops[0]).__name__}"
            )
        negated = False
        operand = node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            negated = True
            operand = node.operand
        preview = self._type_of(operand)
        if preview in {"I", "Z"}:
            _ = self._expression(operand)
            self._instruction(f"{'ifne' if negated else 'ifeq'} {label}")
            return
        if self._supports_truth(preview):
            self._truth_jump_false(operand, label, negated=negated)
            return
        if negated:
            raise self.error(node, "if condition must be bool or int")
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
        return self._bool_method(descriptor) is not None or self._len_accepted(
            descriptor
        )

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
            descriptor in {_STRING, _HASH_SET}
            or descriptor.startswith("[")
            or _list_element(descriptor) is not None
            or (
                descriptor != "null"
                and self._compatible(descriptor, "Ljava/lang/CharSequence;")
            )
        )

    def _emit_len(self, node: ast.AST, descriptor: str) -> None:
        """Replace the value on the stack with its length."""
        if descriptor == _STRING:
            signature = self._require_method(
                "java/lang/String", "length", (), static=False, node=node
            )
            self._invoke(signature, "length")
            return
        if descriptor != "null" and self._compatible(
            descriptor, "Ljava/lang/CharSequence;"
        ):
            signature = self._require_method(
                "java/lang/CharSequence", "length", (), static=False, node=node
            )
            self._invoke(signature, "length")
            return
        if descriptor.startswith("[") or _list_element(descriptor) is not None:
            if _list_element(descriptor) is not None:
                signature = self._require_method(
                    _ARRAY_LIST_OWNER, "size", (), static=False, node=node
                )
                self._invoke(signature, "size")
            else:
                self._instruction("arraylength")
            return
        if descriptor == _HASH_SET:
            signature = self._require_method(
                _HASH_SET_OWNER, "size", (), static=False, node=node
            )
            self._invoke(signature, "size")
            return
        raise self.error(node, f"len() does not accept {descriptor}")

    def _chained_compare_jump(self, node: ast.Compare, label: str) -> None:
        """Jump to ``label`` when a chain fails. Each comparator is emitted once."""
        left_node: ast.expr = node.left
        left_desc = self._expression(left_node)
        left_slot = self._park(left_desc)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right_desc = self._expression(right_node)
            right_slot = self._park(right_desc)
            self._compare_link_jump(
                node,
                op,
                left_node,
                right_node,
                left_desc,
                right_desc,
                left_slot,
                right_slot,
                label,
            )
            left_node = right_node
            left_desc = right_desc
            left_slot = right_slot

    def _compare_link_jump(
        self,
        node: ast.Compare,
        op: ast.cmpop,
        left_node: ast.expr,
        right_node: ast.expr,
        left: str,
        right: str,
        left_slot: int,
        right_slot: int,
        label: str,
    ) -> None:
        opcode = self._ORDERED_FALSE.get(type(op))
        if opcode is not None:
            if left not in {"I", "Z"} or right not in {"I", "Z"}:
                raise self.error(
                    node, "only integer/boolean comparisons are supported"
                )
            self._reload(left_slot, left)
            self._reload(right_slot, right)
            self._instruction(f"{opcode} {label}")
            return
        swapped = isinstance(right_node, ast.Constant) and not isinstance(
            left_node, ast.Constant
        )
        self._reload_pair(left_slot, left, right_slot, right, swapped)
        if isinstance(op, ast.Eq):
            self._equality_jump(node, left, right, label, equal=True, swapped=swapped)
            return
        if isinstance(op, ast.NotEq):
            self._equality_jump(
                node, left, right, label, equal=False, swapped=swapped
            )
            return
        if isinstance(op, ast.Is):
            self._identity_jump(node, left, right, label, same=True, swapped=swapped)
            return
        if isinstance(op, ast.IsNot):
            self._identity_jump(
                node, left, right, label, same=False, swapped=swapped
            )
            return
        raise self.error(node, f"unsupported comparison {type(op).__name__}")

    def _reload(self, slot: int, descriptor: str) -> None:
        self._instruction(f"{self._load_opcode(descriptor)} {slot}")

    def _reload_pair(
        self,
        left_slot: int,
        left: str,
        right_slot: int,
        right: str,
        swapped: bool,
    ) -> None:
        if swapped:
            self._reload(right_slot, right)
            self._reload(left_slot, left)
            return
        self._reload(left_slot, left)
        self._reload(right_slot, right)

    def _comparison_operands(self, node: ast.Compare) -> tuple[str, str, bool]:
        """Push a constant before a variable so the constant is the left operand.

        The third value is true when that swap happened, so the source
        left operand is on top of the stack.
        """
        if isinstance(node.ops[0], ast.Lt | ast.LtE | ast.Gt | ast.GtE):
            left = self._expression(node.left)
            right = self._expression(node.comparators[0])
            return left, right, False
        if isinstance(node.comparators[0], ast.Constant) and not isinstance(
            node.left, ast.Constant
        ):
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
        if (_reference(left) and _numeric(right)) or (
            _numeric(left) and _reference(right)
        ):
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
