"""Shared compiler state, emitters, and AST dispatch."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, ClassVar, cast

from Py2APK.compiler_j.types import (
    _ARRAY_LIST,
    _ARRAY_LIST_OWNER,
    _ARRAY_LOAD,
    _BOX_METHODS,
    _BUILTIN_TYPES,
    _JAVA_ITERATOR,
    _STRING,
    _STRING_BUILDER,
    JasminCompilerError,
    LocalType,
    _array_descriptor,
    _callable_parts,
    _descriptor,
    _function_type,
    _internal_name,
    _is_function_type,
    _list_element,
    _listed_element,
    _literal_int,
    _MethodSignature,
    _package_for,
)
from Py2APK.jar_index import JarCatalog


class _CompilerBase:
    STMT_HANDLERS: ClassVar[dict[type[Any], str]] = {}
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {ast.Constant: "_constant"}
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {}
    UNARY_HANDLERS: ClassVar[dict[type[Any], tuple[str, str]]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        tables = (
            "STMT_HANDLERS",
            "EXPR_HANDLERS",
            "TYPE_HANDLERS",
            "UNARY_HANDLERS",
        )
        for name in tables:
            merged: dict[type[Any], object] = {}
            owners: dict[type[Any], str] = {}
            for base in reversed(cls.__mro__):
                table = base.__dict__.get(name)
                if not isinstance(table, dict):
                    continue
                for key, value in table.items():
                    previous = merged.get(key)
                    if previous is not None and previous != value:
                        raise TypeError(
                            f"{name} conflict on {getattr(key, '__name__', key)}: "
                            f"{previous} from {owners[key]}, {value} from {base.__name__}"
                        )
                    merged[key] = value
                    owners[key] = base.__name__
            setattr(cls, name, merged)

    def _statement(self, node: ast.stmt) -> None:
        method_name = type(self).STMT_HANDLERS.get(type(node))
        if method_name is None:
            raise self.error(node, f"unsupported statement {type(node).__name__}")
        getattr(self, method_name)(node)

    def _expression(self, node: ast.expr) -> str:
        if isinstance(node, ast.UnaryOp):
            found = type(self).UNARY_HANDLERS.get(type(node.op))
            if found is None:
                raise self.error(node, f"unsupported expression {type(node).__name__}")
            method_name, _type_method = found
            return cast("str", getattr(self, method_name)(node))
        method_name = type(self).EXPR_HANDLERS.get(type(node))
        if method_name is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return cast("str", getattr(self, method_name)(node))

    def _type_of(self, node: ast.expr) -> str:
        if isinstance(node, ast.UnaryOp):
            found = type(self).UNARY_HANDLERS.get(type(node.op))
            if found is None:
                raise self.error(node, f"unsupported expression {type(node).__name__}")
            _method_name, type_method = found
            return cast("str", getattr(self, type_method)(node))
        method_name = type(self).TYPE_HANDLERS.get(type(node))
        if method_name is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return cast("str", getattr(self, method_name)(node))

    def __init__(
        self,
        source: Path,
        output: Path,
        tree: ast.Module,
        catalog: JarCatalog,
        project: dict[str, LocalType],
        min_api: int | None = None,
    ) -> None:
        self.source: Path = source
        self.min_api: int | None = min_api
        self.output: Path = output
        self.tree: ast.Module = tree
        self.catalog: JarCatalog = catalog
        self.project: dict[str, LocalType] = project
        self.package: str = _package_for(source, output)
        self.imports: dict[str, str] = {}
        self.imported_functions: dict[str, tuple[str, str]] = {}
        self.math_force_double: set[str] = set()
        self.project_root: Path = source.parent
        self.method_result: str = "V"
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
        self.emitted_function_bases: set[str] = set()
        self.non_final_locals: set[str] = set()
        self.list_sink: str | None = None
        self.mutable_lists: set[str] = set()
        self.field_list_ids: set[int] = set()
        self.list_elements: dict[str, str] = {}
        self.preview_locals: dict[str, str] = {}
        self.generator_elements: dict[str, str] = {}
        for local in project.values():
            self.generator_elements.update(local.generators)
        self.generator_names: dict[str, str] = {}
        self.context_names: dict[str, str] = {}
        self.context_elements: dict[str, str] = {}
        self.generator_internal: str | None = None
        self.generator_element: str | None = None
        self.generator_fields: dict[str, str] | None = None
        self.generator_exhausted: str | None = None
        self.resume_labels: list[str] = []
        self.yield_cursor: int = 0
        self.temp_serial: int = 0
        self.for_serial: int = 0
        self.loops: list[tuple[str, str]] = []
        self.handlers: list[str] = []
        self._keyword_call: ast.Call | None = None
        self._keyword_failures: list[str] = []

    def error(self, node: ast.AST, message: str) -> JasminCompilerError:
        line = getattr(node, "lineno", "?")
        return JasminCompilerError(f"{self.source}:{line}: {message}")

    def _names_imported(self, node: ast.expr, internal: str) -> bool:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id) == internal
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and self.imports.get(node.value.id) == internal.rsplit("/", 1)[0]
            and node.attr == internal.rsplit("/", 1)[-1]
        )

    def _keep(self, name: str, descriptor: str, node: ast.AST) -> None:
        target = ast.Name(id=name, ctx=ast.Store())
        ast.copy_location(target, node)
        self._store(target, descriptor)

    def _cursor_name(self, role: str) -> str:
        self.for_serial += 1
        return f"py2apk_for_{role}_{self.for_serial}"

    def _is_generator_type(self, descriptor: str) -> bool:
        return (
            descriptor.startswith("L")
            and descriptor.endswith(";")
            and _internal_name(descriptor) in self.generator_elements
        )

    def _sequence_element(self, descriptor: str) -> str | None:
        listed = _list_element(descriptor)
        if listed is not None:
            return listed
        if descriptor.startswith("["):
            return descriptor[1:]
        return None

    def _box(self, descriptor: str) -> None:
        if descriptor == "null" or descriptor.startswith(("L", "[")):
            return
        boxed = _BOX_METHODS.get(descriptor)
        if boxed is None:
            raise JasminCompilerError(f"cannot box type {descriptor}")
        owner, name = boxed
        signature = self._require_method(owner, name, (descriptor,), static=True)
        self._instruction(
            f"invokestatic {signature.owner or owner}/{name}{signature.descriptor}"
        )

    def _emit_cast(self, node: ast.Call) -> str:
        return self._cast_descriptor(node, emit=True)

    def _cast_descriptor(self, node: ast.Call, *, emit: bool = False) -> str:
        if len(node.args) != 2:
            raise self.error(node, "cast() accepts a type and a value")
        expected = self._annotation(node.args[0])
        actual = self._expression(node.args[1]) if emit else self._type_of(node.args[1])
        if not self._compatible(actual, expected):
            raise self.error(node.args[1], f"cast expects {expected}, got {actual}")
        return expected

    def _static_invoke(
        self, node: ast.Call, owner: str, name: str, types: tuple[str, ...]
    ) -> str:
        signature = self._require_method(owner, name, types, static=True, node=node)
        self._emit_arguments(node, signature.parameters)
        declaring = signature.owner or owner
        self._instruction(f"invokestatic {declaring}/{name}{signature.descriptor}")
        return signature.result

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

    def _emit_int(self, value: int) -> None:
        if -1 <= value <= 5:
            name = "m1" if value == -1 else str(value)
            self._instruction(f"iconst_{name}")
            return
        if -128 <= value <= 127:
            self._instruction(f"bipush {value}")
            return
        if -32768 <= value <= 32767:
            self._instruction(f"sipush {value}")
            return
        self._instruction(f"ldc {value}")

    def _load_local(self, name: str) -> None:
        slot, descriptor = self.locals[name]
        self._instruction(f"{self._load_opcode(descriptor)} {slot}")

    def _compatible(self, actual: str, wanted: str) -> bool:
        if actual == "lambda":
            return self._lambda_target(wanted)
        element = _list_element(actual)
        if element is not None:
            if wanted == _array_descriptor(element) and element.startswith(("L", "[")):
                return True
            actual = _ARRAY_LIST
        if actual == wanted or actual == "null":
            return True
        if wanted == "Ljava/util/Iterator;" and self._is_generator_type(actual):
            return True
        if not (actual.startswith("L") and wanted.startswith("L")):
            return False
        return self._related(_internal_name(actual), _internal_name(wanted), set())

    def _lambda_target(self, wanted: str) -> bool:
        return self._is_interface_type(wanted) or _is_function_type(wanted)

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
        return any(
            self._related(name, wanted, seen)
            for name in self.catalog.interfaces_of(actual)
        )

    def _annotation(self, node: ast.expr) -> str:
        if isinstance(node, ast.Constant) and node.value is None:
            return "V"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except SyntaxError as exc:
                raise self.error(
                    node, f"invalid string annotation {node.value!r}"
                ) from exc
            return self._annotation(parsed)
        if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPES:
            return _BUILTIN_TYPES[node.id]
        listed = _listed_element(node)
        if listed is not None:
            return _array_descriptor(self._annotation(listed))
        named = self._named_subscript_annotation(node)
        if named is not None:
            return named
        return _descriptor(self._resolve_type_name(node))

    def _named_subscript_annotation(self, node: ast.expr) -> str | None:
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
            return None
        if self.imports.get(node.value.id) == _JAVA_ITERATOR and not isinstance(
            node.slice, ast.Tuple
        ):
            return _descriptor(_JAVA_ITERATOR)
        return self._callable_annotation(node)

    def _callable_annotation(self, node: ast.expr) -> str | None:
        try:
            parts = _callable_parts(node, self.imports)
        except JasminCompilerError as exc:
            raise self.error(node, str(exc)) from exc
        if parts is None:
            return None
        parameters, result = parts
        decoded = tuple(self._annotation(item) for item in parameters)
        return _function_type(decoded, self._annotation(result))

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
        boxed = {
            "I": "java/lang/Integer",
            "Z": "java/lang/Boolean",
            "F": "java/lang/Float",
            "D": "java/lang/Double",
            "J": "java/lang/Long",
        }.get(element)
        if boxed is not None:
            self._instruction(f"checkcast {boxed}")
        _ = self._restore_yield_type(element)

    def _bind_slice_locals(
        self, origin: ast.AST, descriptor: str, bounds: ast.Slice
    ) -> tuple[str, str, str, str, str]:
        """Keep the sequence, then length, step, start, and stop."""
        sequence = self._cursor_name("seq")
        length = self._cursor_name("len")
        step = self._cursor_name("step")
        start = self._cursor_name("start")
        stop = self._cursor_name("stop")
        self._keep(sequence, descriptor, origin)
        if descriptor == _STRING:
            self._load_local(sequence)
            signature = self._require_method(
                "java/lang/String", "length", (), static=False, node=origin
            )
            self._invoke(signature, "length")
        else:
            self._emit_sequence_length(sequence, descriptor)
        self._keep(length, "I", origin)
        self._emit_slice_step(bounds, origin)
        self._keep(step, "I", origin)
        self._reject_zero_step(bounds, step, origin)
        self._emit_slice_bound(bounds.lower, is_start=True, length=length, step=step)
        self._keep(start, "I", origin)
        self._emit_slice_bound(bounds.upper, is_start=False, length=length, step=step)
        self._keep(stop, "I", origin)
        return sequence, length, step, start, stop

    # Mixins override these cross-family entry points.
    def _bind_parameters(
        self,
        node: ast.FunctionDef,
        signature: _MethodSignature,
        *,
        static: bool,
    ) -> None:
        raise NotImplementedError((node, signature, static))

    def _classify_lists(self, statements: list[ast.stmt]) -> None:
        raise NotImplementedError(statements)

    def _compile_context_method(self, node: ast.FunctionDef) -> None:
        raise NotImplementedError(node)

    def _compile_generator_method(self, node: ast.FunctionDef) -> None:
        raise NotImplementedError(node)

    def _condition_jump_false(self, node: ast.expr, label: str) -> None:
        raise NotImplementedError((node, label))

    def _define_born(self, mark: int, defined: set[str]) -> None:
        raise NotImplementedError((mark, defined))

    def _emit_arguments(self, node: ast.Call, expected: tuple[str, ...]) -> None:
        raise NotImplementedError((node, expected))

    def _emit_char_string(self) -> None:
        raise NotImplementedError

    def _emit_len(self, node: ast.AST, descriptor: str) -> None:
        raise NotImplementedError((node, descriptor))

    def _emit_listener(self, node: ast.expr, expected: str) -> None:
        raise NotImplementedError((node, expected))

    def _emit_function_lambda(self, node: ast.Lambda, expected: str) -> str:
        raise NotImplementedError((node, expected))

    def _emit_function_ref(self, node: ast.Name) -> str:
        raise NotImplementedError(node)

    def _function_ref_binding(
        self, name: str, node: ast.AST
    ) -> tuple[str, str, _MethodSignature] | None:
        raise NotImplementedError((name, node))

    def _emit_range(self, node: ast.Call) -> str:
        raise NotImplementedError(node)

    def _emit_to_array(self, element: str) -> None:
        raise NotImplementedError(element)

    def _emit_yield(self, node: ast.Yield) -> None:
        raise NotImplementedError(node)

    def _element_type(self, node: ast.List | ast.Tuple | ast.Set) -> str:
        raise NotImplementedError(node)

    def _argument_types(self, node: ast.Call) -> tuple[str, ...]:
        raise NotImplementedError(node)

    def _find_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        *,
        static: bool,
        call: ast.Call | None = None,
    ) -> _MethodSignature | None:
        raise NotImplementedError((owner, name, arguments, static, call))

    def _generator_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        static: bool,
    ) -> _MethodSignature | None:
        raise NotImplementedError((owner, name, arguments, static))

    def _hold_temp(self, descriptor: str) -> str:
        raise NotImplementedError(descriptor)

    def _interface_bases(self, internal: str) -> set[str]:
        raise NotImplementedError(internal)

    def _is_interface_type(self, descriptor: str) -> bool:
        raise NotImplementedError(descriptor)

    def _is_method_reference(self, node: ast.expr) -> bool:
        raise NotImplementedError(node)

    def _is_type_reference(self, node: ast.expr) -> bool:
        raise NotImplementedError(node)

    def _len_accepted(self, descriptor: str) -> bool:
        raise NotImplementedError(descriptor)

    def _list_append(self, node: ast.Call) -> str:
        raise NotImplementedError(node)

    def _preview_type(self, node: ast.expr) -> str | None:
        raise NotImplementedError(node)

    def _protocol(self, owner: str, name: str, node: ast.AST) -> _MethodSignature:
        raise NotImplementedError((owner, name, node))

    def _restore_yield_type(self, element: str) -> str:
        raise NotImplementedError(element)

    def _signature_for_call(
        self, node: ast.Call, arguments: tuple[str, ...]
    ) -> _MethodSignature | None:
        raise NotImplementedError((node, arguments))

    def _store(self, target: ast.expr, descriptor: str) -> None:
        raise NotImplementedError((target, descriptor))

    def _subscript_store(self, target: ast.Subscript, value: ast.expr) -> None:
        raise NotImplementedError((target, value))

    def _yielded_result(self, owner: str, method_name: str, result: str) -> str:
        raise NotImplementedError((owner, method_name, result))

    def _dispatch_higher_order(self, node: ast.Call, *, emit: bool) -> str | None:
        raise NotImplementedError((node, emit))

    def _infer_lambda_result(
        self, node: ast.Lambda, param_types: tuple[str, ...]
    ) -> str:
        raise NotImplementedError((node, param_types))

    def _emit_list_unit(
        self,
        sequence: str,
        descriptor: str,
        element: str,
        result: str,
        add: _MethodSignature,
        source: str,
        _dest: str,
    ) -> None:
        raise NotImplementedError(
            (sequence, descriptor, element, result, add, source, _dest)
        )

    def _emit_comparator(
        self,
        node: ast.AST,
        element: str,
        key_local: str | None,
        key_descriptor: str | None,
        key_result: str | None,
        reverse_local: str | None,
    ) -> None:
        raise NotImplementedError(
            (node, element, key_local, key_descriptor, key_result, reverse_local)
        )
