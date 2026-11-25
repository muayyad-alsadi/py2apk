"""Yield iterators and contextmanager classes."""

from __future__ import annotations

import ast
import json

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.types import (
    _BOX_METHODS,
    _JAVA_ITERATOR,
    _UNBOX_METHODS,
    JasminCompilerError,
    LocalType,
    _descriptor,
    _internal_name,
    _jvm_descriptor,
    _MethodSignature,
    _return_opcode,
)


def _has_yield(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Yield | ast.YieldFrom) for child in ast.walk(node))


def _contains_yield(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Yield) for child in ast.walk(node))


def _yield_in_loop(statements: list[ast.stmt], in_loop: bool = False) -> bool:
    for statement in statements:
        loop = in_loop or isinstance(statement, ast.For | ast.While)
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Yield)
            and loop
        ):
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
    return len(node.decorator_list) == 1 and _names_contextmanager(
        node.decorator_list[0], imports
    )


def _context_manager_type(internal: str, element: str) -> LocalType:
    throwable = "Ljava/lang/Throwable;"
    return LocalType(
        internal,
        None,
        (),
        {
            "__enter__": _MethodSignature((), element, owner=internal),
            "__exit__": _MethodSignature(
                (throwable, throwable, throwable), "Z", owner=internal
            ),
        },
    )


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
    if (
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in bound
    ):
        found.append(node.id)
    for child in ast.iter_child_nodes(node):
        for name in _loaded_names(child, bound):
            if name not in found:
                found.append(name)
    return found


class _GeneratorsMixin(_CompilerBase):
    def _compile_context_method(self, node: ast.FunctionDef) -> None:
        internal = self.context_names[node.name]
        element = self.context_elements[internal]
        before, yielded, after = self._split_context_body(node)
        signature = self.methods[node.name]
        self.method_result = element
        self._bind_parameters(
            node, signature, static=signature.opcode == "invokestatic"
        )
        captures = [
            (argument.arg, self.locals[argument.arg][1]) for argument in node.args.args
        ]
        capture_descriptor = "".join(descriptor for _name, descriptor in captures)
        self._instruction(f"new {internal}")
        self._instruction("dup")
        for name, descriptor in captures:
            self._instruction(f"{self._load_opcode(descriptor)} {self.locals[name][0]}")
        self._instruction(f"invokespecial {internal}/<init>({capture_descriptor})V")
        self._instruction("areturn")
        wrapper = list(self.instructions)
        wrapper_limit = self.next_local
        enter_body, enter_limit = self._context_body(
            internal, captures, before, yielded, element, 1
        )
        exit_body, exit_limit = self._context_body(
            internal, captures, after, None, "Z", 4
        )
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
            if isinstance(statement, ast.Expr) and isinstance(
                statement.value, ast.Yield
            ):
                if statement.value.value is None:
                    raise self.error(statement, "contextmanager yield needs a value")
                return (
                    statements[:index],
                    statement.value.value,
                    statements[index + 1 :],
                )
            if isinstance(statement, ast.Try) and _contains_yield(statement):
                if statement.handlers or statement.orelse:
                    raise self.error(
                        statement, "contextmanager try cannot have except or else"
                    )
                inner = self._split_context_statements(statement.body)
                if inner is None:
                    raise self.error(
                        statement, "contextmanager yield must be a statement"
                    )
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
                self._instruction(
                    f"{self._store_opcode(descriptor)} {self.locals[name][0]}"
                )
            for statement in statements:
                self._statement(statement)
            if yielded is None:
                self._instruction("iconst_0")
                self._instruction("ireturn")
            else:
                actual = self._expression(yielded)
                if actual != result and not self._compatible(actual, result):
                    raise self.error(
                        yielded, f"yield has type {actual}, expected {result}"
                    )
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
        self._bind_parameters(
            node, signature, static=signature.opcode == "invokestatic"
        )
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
            self._instruction(
                f"{self._load_opcode(capture_type)} {self.locals[name][0]}"
            )
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
        if (
            self.yield_cursor >= len(self.resume_labels)
            or self.generator_exhausted is None
        ):
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
            raise JasminCompilerError(
                f"cannot change local {name!r} from {local[1]} to {stored}"
            )
        self._instruction(f"{self._store_opcode(stored)} {local[0]}")
        return name
