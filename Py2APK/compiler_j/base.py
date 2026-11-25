"""Shared compiler state, emitters, and AST dispatch."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, ClassVar, cast

from Py2APK.compiler_j.types import (
    _ARRAY_LIST,
    _BOX_METHODS,
    _BUILTIN_TYPES,
    _JAVA_ITERATOR,
    _STRING,
    _STRING_BUILDER,
    JasminCompilerError,
    LocalType,
    _array_descriptor,
    _descriptor,
    _internal_name,
    _list_element,
    _listed_element,
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
            return self._is_interface_type(wanted)
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
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and self.imports.get(node.value.id) == _JAVA_ITERATOR
        ):
            return _descriptor(_JAVA_ITERATOR)
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

    def _emit_range(self, node: ast.Call) -> str:
        raise NotImplementedError(node)

    def _emit_to_array(self, element: str) -> None:
        raise NotImplementedError(element)

    def _emit_yield(self, node: ast.Yield) -> None:
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
