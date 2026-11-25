"""try, except, raise, with, and suppress."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.generators import (
    _SUPPRESS,
)
from Py2APK.compiler_j.types import (
    JasminCompilerError,
    _descriptor,
    _internal_name,
    _MethodSignature,
)


class _ExceptionsMixin(_CompilerBase):
    STMT_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Try: "_try_statement",
        ast.Raise: "_raise_statement",
        ast.With: "_with_statement",
    }

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
        if node.finalbody and not any(
            handler.type is None for handler in node.handlers
        ):
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
        if not isinstance(node, ast.Call) or not self._names_imported(
            node.func, _SUPPRESS
        ):
            return None
        if node.keywords:
            raise self.error(node, "keyword arguments are unsupported")
        catches: list[str] = []
        for argument in node.args:
            if not self._is_type_reference(argument):
                raise self.error(argument, "suppress expects a type name")
            catches.append(self._resolve_type_name(argument))
        return catches

    def _with_suppress(
        self, catches: list[str], items: list[ast.withitem], body: list[ast.stmt]
    ) -> None:
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

    def _emit_exit(
        self, manager: str, method: _MethodSignature, thrown: str | None
    ) -> None:
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
