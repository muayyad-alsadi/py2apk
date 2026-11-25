"""if, while, for, range, and loop control."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.types import (
    _ARRAY_LIST_OWNER,
    _ARRAY_LOAD,
    _array_descriptor,
    _internal_name,
    _literal_int,
)


class _LoopsMixin(_CompilerBase):
    STMT_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.If: "_if_statement",
        ast.While: "_while_statement",
        ast.For: "_for_statement",
        ast.Pass: "_pass_statement",
        ast.Break: "_break_statement",
        ast.Continue: "_continue_statement",
        ast.Expr: "_expr_statement",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Tuple: "_type_of_tuple",
    }

    def _type_of_tuple(self, node: ast.Tuple) -> str:
        return _array_descriptor(self._element_type(node))

    def _pass_statement(self, _node: ast.Pass) -> None:
        """A pass statement emits no instructions."""

    def _break_statement(self, node: ast.Break) -> None:
        self._jump_loop(node, breaking=True)

    def _continue_statement(self, node: ast.Continue) -> None:
        self._jump_loop(node, breaking=False)

    def _expr_statement(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Yield):
            self._emit_yield(node.value)
            return
        descriptor = self._expression(node.value)
        if descriptor != "V":
            self._instruction("pop2" if descriptor in {"D", "J"} else "pop")

    def _jump_loop(self, node: ast.AST, *, breaking: bool) -> None:
        if not self.loops:
            name = "break" if breaking else "continue"
            raise self.error(node, f"{name} outside a loop")
        continue_label, break_label = self.loops[-1]
        self._instruction(f"goto {break_label if breaking else continue_label}")

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

    def _for_target_names(self, target: ast.expr) -> list[ast.Name]:
        if isinstance(target, ast.Name):
            return [target]
        if not isinstance(target, ast.Tuple):
            raise self.error(
                target, "for target must be a name or a tuple of names"
            )
        for element in target.elts:
            if isinstance(element, ast.Starred):
                raise self.error(
                    target, "starred assignment targets are unsupported"
                )
        names: list[ast.Name] = []
        for element in target.elts:
            if not isinstance(element, ast.Name):
                raise self.error(
                    target, "for target must be a name or a tuple of names"
                )
            names.append(element)
        if not names:
            raise self.error(
                target, "for target must be a name or a tuple of names"
            )
        return names

    def _reject_for_unpack_length(
        self, names: list[ast.Name], node: ast.AST
    ) -> None:
        if not isinstance(node, ast.For):
            return
        iterable = node.iter
        if isinstance(iterable, ast.Subscript):
            return
        if not isinstance(iterable, ast.List | ast.Tuple):
            return
        sequences: list[ast.List | ast.Tuple] = []
        for item in iterable.elts:
            if not isinstance(item, ast.List | ast.Tuple):
                return
            sequences.append(item)
        for item in sequences:
            if len(item.elts) != len(names):
                raise self.error(item, "for unpack length does not match")

    def _store_for_unpack(
        self,
        names: list[ast.Name],
        element: str,
        node: ast.AST,
    ) -> None:
        self._reject_for_unpack_length(names, node)
        where = node.iter if isinstance(node, ast.For) else node
        if not element.startswith("["):
            raise self.error(where, "for unpack requires an array element")
        slot = self._sequence_element(element)
        if slot is None:
            raise self.error(where, "for unpack requires an array element")
        pair = self._cursor_name("pair")
        self._keep(pair, element, node)
        ok = self._label("unpack")
        self._load_local(pair)
        self._instruction("arraylength")
        self._emit_int(len(names))
        self._instruction(f"if_icmpeq {ok}")
        self._instruction("new java/lang/IllegalArgumentException")
        self._instruction("dup")
        self._instruction(
            "invokespecial java/lang/IllegalArgumentException/<init>()V"
        )
        self._instruction("athrow")
        self._instruction(f"{ok}:", indented=False)
        load = _ARRAY_LOAD.get(slot, "aaload")
        for index, name in enumerate(names):
            self._load_local(pair)
            self._emit_int(index)
            self._instruction(load)
            self._store(name, slot)

    def _for_statement(self, node: ast.For) -> None:
        _ = self._for_target_names(node.target)
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
        self._for_index(
            node, descriptor, element, bounds if isinstance(bounds, ast.Slice) else None
        )

    def _for_generator(self, node: ast.For, descriptor: str) -> None:
        names = self._for_target_names(node.target)
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
        if isinstance(node.target, ast.Name):
            self._store(node.target, element)
        if isinstance(node.target, ast.Tuple):
            self._store_for_unpack(names, element, node)
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
        names = self._for_target_names(node.target)
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
        self._emit_slice_bound(
            None if bounds is None else bounds.lower,
            is_start=True,
            length=length,
            step=step,
        )
        self._keep(start, "I", node)
        self._emit_slice_bound(
            None if bounds is None else bounds.upper,
            is_start=False,
            length=length,
            step=step,
        )
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
        if isinstance(node.target, ast.Name):
            self._store(node.target, element)
        if isinstance(node.target, ast.Tuple):
            self._store_for_unpack(names, element, node)
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

    def _reject_zero_step(
        self, bounds: ast.Slice | None, step: str, node: ast.AST
    ) -> None:
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

    def _emit_element(
        self, sequence: str, descriptor: str, index: str, element: str
    ) -> None:
        self._load_local(sequence)
        self._load_local(index)
        if descriptor.startswith("["):
            self._instruction(_ARRAY_LOAD.get(element, "aaload"))
            return
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/get(I)Ljava/lang/Object;")
        _ = self._restore_yield_type(element)

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
