"""Module, class, method, import, and assignment lowering."""

from __future__ import annotations

import ast
import json
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.generators import (
    _context_manager_type,
    _has_yield,
    _is_contextmanager_def,
    _iterator_slice,
)
from Py2APK.compiler_j.indexer import (
    _in_stdlib,
    _indexed_module_name,
)
from Py2APK.compiler_j.lambdas import _non_final_names
from Py2APK.compiler_j.types import (
    _BUILTIN_TYPES,
    _JAVA_ITERATOR,
    _PY2APK_CALLABLE,
    JasminCompilerError,
    _descriptor,
    _function_type,
    _internal_name,
    _is_function_type,
    _jvm_descriptor,
    _list_element,
    _MethodSignature,
    _return_opcode,
    math_method,
)


class _ModuleMixin(_CompilerBase):
    STMT_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Assign: "_assign_statement",
        ast.AnnAssign: "_ann_assign",
        ast.Return: "_emit_return",
    }
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {ast.Name: "_load_name"}
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {ast.Name: "_type_of_name"}

    def _assign_statement(self, node: ast.Assign) -> None:
        if len(node.targets) != 1:
            raise self.error(node, "chained assignments are unsupported")
        self._assign(node.targets[0], node.value)

    def _ann_assign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            raise self.error(node, "local annotated assignments require a value")
        self._store(node.target, self._annotated_value(node.target, node.value, node))

    def _annotated_value(
        self, target: ast.expr, value: ast.expr, node: ast.AnnAssign
    ) -> str:
        declared = self._annotation(node.annotation)
        if isinstance(value, ast.Lambda) and _is_function_type(declared):
            return self._emit_function_lambda(value, declared)
        descriptor = self._assigned_value(target, value)
        jvm = _jvm_descriptor(descriptor)
        if jvm == declared:
            return descriptor
        if jvm.startswith("L") and declared.startswith("L"):
            self._instruction(f"checkcast {_internal_name(declared)}")
            return declared
        raise self.error(
            node,
            f"assignment type {descriptor} does not match {declared}",
        )

    def _load_name(self, node: ast.Name) -> str:
        local = self.locals.get(node.id)
        if local is None:
            return self._emit_function_ref(node)
        self._instruction(f"{self._load_opcode(local[1])} {local[0]}")
        element = self.list_elements.get(node.id)
        if element is not None:
            return f"arraylist:{element}"
        return local[1]

    def _type_of_name(self, node: ast.Name) -> str:
        local = self.locals.get(node.id)
        if local is None:
            return _descriptor_of_function_ref(self, node)
        element = self.list_elements.get(node.id)
        if element is not None:
            return f"arraylist:{element}"
        return local[1]

    def emit(self) -> str:
        classes: list[ast.ClassDef] = []
        functions: list[ast.FunctionDef] = []
        for statement in self.tree.body:
            if isinstance(statement, ast.Import | ast.ImportFrom):
                self._record_import(statement)
            elif isinstance(statement, ast.If) and self._is_type_checking(
                statement.test
            ):
                for nested in statement.body:
                    if isinstance(nested, ast.Import | ast.ImportFrom):
                        self._record_import(nested)
                        continue
                    if (
                        isinstance(nested, ast.AnnAssign)
                        and nested.value is None
                        and isinstance(nested.target, ast.Name)
                        and nested.target.id in _BUILTIN_TYPES
                    ):
                        continue
                    raise self.error(
                        nested,
                        "TYPE_CHECKING blocks may contain only imports and builtin annotations",
                    )
            elif isinstance(statement, ast.ClassDef):
                classes.append(statement)
            elif isinstance(statement, ast.FunctionDef):
                functions.append(statement)
            elif (
                isinstance(statement, ast.Pass)
                or (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                )
                or (
                    isinstance(statement, ast.AnnAssign)
                    and statement.value is None
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id in _BUILTIN_TYPES
                )
                or (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and statement.targets[0].id in _BUILTIN_TYPES
                    and isinstance(statement.value, ast.Name)
                )
            ):
                continue
            else:
                raise self.error(
                    statement,
                    f"unsupported module statement {type(statement).__name__}",
                )

        if len(classes) == 1 and not functions:
            self._compile_class(classes[0])
            return self._take_text()
        if not classes and not functions:
            raise JasminCompilerError(f"{self.source}: expected a class or a function")

        primary = ""
        if functions:
            self._compile_module(functions)
            primary = self._take_text()
        saved_package = self.package
        if functions:
            self.package = self.class_internal
        elif len(classes) != 1 and _in_stdlib(self.source):
            self.package = _indexed_module_name(self.source)
        for node in classes:
            self._compile_class(node)
            text = self._take_text()
            if not primary:
                primary = text
                continue
            self.extra_classes.append((f"{node.name}.j", text))
        self.package = saved_package
        return primary

    def _take_text(self) -> str:
        text = "\n".join(self.lines)
        self.lines = []
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    def _compile_module(self, functions: list[ast.FunctionDef]) -> None:
        self._begin_type()
        self.class_name = (
            self.source.parent.name
            if self.source.name == "__init__.py"
            else self.source.stem
        )
        self.class_internal = "/".join(filter(None, (self.package, self.class_name)))
        for function in functions:
            self.methods[function.name] = self._function_signature(function)
        self._emit_type_header()
        self._emit_default_constructor()
        self._bind_generators(functions)
        self._bind_context_managers(functions)
        for function in functions:
            self._compile_method(function, constructor=False)

    @staticmethod
    def _is_type_checking(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"

    def _record_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    continue
                local_name = alias.asname or alias.name.split(".", 1)[0]
                if "." in alias.name and alias.asname is None:
                    local_name = alias.name.rsplit(".", 1)[-1]
                if not self._bind_module(alias.name, local_name, node):
                    raise self.error(node, self._missing_import(alias.name))
            return
        self._record_from_import(node)

    def _record_from_import(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            if any(alias.name != "annotations" for alias in node.names):
                raise self.error(node, "only __future__.annotations is supported")
            return
        if node.module in {"typing", "collections.abc"}:
            self._record_type_alias_import(node)
            return
        if any(alias.name == "*" for alias in node.names):
            raise self.error(node, "wildcard imports are unsupported")

        if node.level:
            if node.level != 1 or not node.module:
                raise self.error(
                    node, "only same-package relative imports are supported"
                )
            for alias in node.names:
                if alias.name != node.module:
                    raise self.error(
                        node,
                        "relative imports must import the class named by the module",
                    )
                local_name = alias.asname or alias.name
                self.imports[local_name] = "/".join(
                    filter(None, (self.package, alias.name))
                )
            return
        if not node.module:
            raise self.error(node, "import must name a module")

        if node.module == "math" and "math" not in self.project:
            self._bind_math_from_import(node)
            return

        if self._bind_module_names(node):
            return
        module_internal = node.module.replace(".", "/")
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.imports[local_name] = f"{module_internal}/{alias.name}"

    def _record_type_alias_import(self, node: ast.ImportFrom) -> None:
        if node.module == "typing":
            self._record_typing_import(node)
            return
        self._record_abc_import(node)

    def _record_typing_import(self, node: ast.ImportFrom) -> None:
        allowed = {"TYPE_CHECKING", "cast", "Callable"}
        for alias in node.names:
            if alias.name not in allowed:
                raise self.error(
                    node,
                    "only typing.TYPE_CHECKING, typing.cast, and "
                    "typing.Callable are supported",
                )
            if alias.name != "Callable":
                continue
            self.imports[alias.asname or alias.name] = _PY2APK_CALLABLE

    def _record_abc_import(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name
            if alias.name == "Iterator":
                self.imports[local_name] = _JAVA_ITERATOR
                continue
            if alias.name == "Callable":
                self.imports[local_name] = _PY2APK_CALLABLE
                continue
            raise self.error(
                node,
                "only collections.abc.Iterator and collections.abc.Callable "
                "are supported",
            )

    def _bind_math_from_import(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            local = alias.asname or alias.name
            row = math_method(alias.name)
            if row is None:
                raise self.error(node, f"unsupported import math.{alias.name}")
            self.imported_functions[local] = ("java/lang/Math", row[0])
            if row[2]:
                self.math_force_double.add(local)

    def _bind_module(self, module: str, local_name: str, node: ast.AST) -> bool:
        _ = node
        internal = module.replace(".", "/")
        if module == "math" and "math" not in self.project:
            self.imports[local_name] = "java/lang/Math"
            return True
        if internal not in self.project:
            return False
        self.imports[local_name] = internal
        return True

    def _bind_module_names(self, node: ast.ImportFrom) -> bool:
        if node.module is None:
            return False
        internal = node.module.replace(".", "/")
        found = self.project.get(internal)
        if found is None:
            return False
        for alias in node.names:
            local_name = alias.asname or alias.name
            method = found.methods.get(alias.name)
            if method is not None and method.opcode == "invokestatic":
                self.imported_functions[local_name] = (internal, alias.name)
                continue
            class_internal = (
                alias.name if alias.name in self.project else f"{internal}/{alias.name}"
            )
            if class_internal not in self.project:
                raise self.error(node, f"unsupported import {node.module}.{alias.name}")
            self.imports[local_name] = class_internal
        return True

    def _java_class(self, internal: str) -> bool:
        if self.catalog.super_of(internal) is not None or self.catalog.is_interface(
            internal
        ):
            return True
        return bool(self.catalog.methods_of(internal))

    def _missing_import(self, name: str) -> str:
        from Py2APK.module_resolve import describe_search

        return f"unsupported import {name!r}; looked in {describe_search(name, self.project_root)}"

    def _begin_type(self) -> None:
        self.fields = {}
        self.methods = {}
        self.generator_names = {}
        self.context_names = {}
        self.context_elements = {}
        self.lines = []
        self.super_internal = "java/lang/Object"

    def _emit_type_header(self) -> None:
        self.lines.extend(
            (f".source {self.source.name}", f".class public {self.class_internal}")
        )
        self.lines.append(f".super {self.super_internal}")

    def _compile_class(self, node: ast.ClassDef) -> None:
        if node.decorator_list or node.keywords:
            raise self.error(
                node, "class decorators and class keywords are unsupported"
            )
        self._begin_type()
        self.class_name = node.name
        self.class_internal = "/".join(filter(None, (self.package, node.name)))

        interfaces: list[str] = []
        if len(node.bases) > 1:
            raise self.error(node, "multiple base declarations are unsupported")
        if node.bases:
            base = self._resolve_type_name(node.bases[0])
            if base in self._interface_bases(base):
                interfaces.append(base)
            else:
                self.super_internal = base

        functions: list[ast.FunctionDef] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                if not isinstance(item.target, ast.Name) or item.value is not None:
                    raise self.error(
                        item, "fields must be uninitialized annotated names"
                    )
                self.fields[item.target.id] = self._annotation(item.annotation)
            elif isinstance(item, ast.FunctionDef):
                functions.append(item)
                if item.name != "__init__":
                    signature = self._method_signature(item)
                    self.methods[item.name] = _MethodSignature(
                        signature.parameters,
                        signature.result,
                        owner=self.class_internal,
                        parameter_names=signature.parameter_names,
                    )
            elif isinstance(item, ast.Pass) or (
                isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
            ):
                continue
            else:
                raise self.error(
                    item, f"unsupported class member {type(item).__name__}"
                )

        self._emit_type_header()
        for interface in interfaces:
            self.lines.append(f".implements {interface}")
        self.lines.append("")
        for name, descriptor in self.fields.items():
            # Jasmin 3.0.3's grammar requires field declarations to use a
            # quoted string token for the field name.
            self.lines.append(f".field public {json.dumps(name)} {descriptor}")
        if self.fields:
            self.lines.append("")

        constructors = [
            function for function in functions if function.name == "__init__"
        ]
        if len(constructors) > 1:
            raise self.error(node, "multiple constructors are unsupported")
        if constructors:
            if _has_yield(constructors[0]):
                raise self.error(
                    constructors[0], "yield is unsupported in a constructor"
                )
            self._compile_method(constructors[0], constructor=True)
        else:
            self._emit_default_constructor()
        self._bind_generators(functions)
        self._bind_context_managers(functions)
        for function in functions:
            if function.name != "__init__":
                self._compile_method(function, constructor=False)

    def _method_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        if node.decorator_list and not _is_contextmanager_def(node, self.imports):
            raise self.error(node, "method decorators are unsupported")
        if (
            node.args.posonlyargs
            or node.args.kwonlyargs
            or node.args.vararg
            or node.args.kwarg
        ):
            raise self.error(node, "only positional method parameters are supported")
        if not node.args.args or node.args.args[0].arg != "self":
            raise self.error(node, "instance methods must begin with self")
        parameters: list[str] = []
        for argument in node.args.args[1:]:
            if argument.annotation is None:
                raise self.error(
                    argument, f"parameter {argument.arg!r} requires a type annotation"
                )
            parameters.append(self._annotation(argument.annotation))
        if node.returns is None:
            raise self.error(node, "methods require a return annotation")
        names = tuple(argument.arg for argument in node.args.args[1:])
        return _MethodSignature(
            tuple(parameters),
            self._annotation(node.returns),
            parameter_names=names,
        )

    def _function_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        if node.decorator_list and not _is_contextmanager_def(node, self.imports):
            raise self.error(node, "function decorators are unsupported")
        if (
            node.args.posonlyargs
            or node.args.kwonlyargs
            or node.args.vararg
            or node.args.kwarg
        ):
            raise self.error(node, "only positional function parameters are supported")
        parameters: list[str] = []
        for argument in node.args.args:
            if argument.annotation is None:
                raise self.error(
                    argument, f"parameter {argument.arg!r} requires a type annotation"
                )
            parameters.append(self._annotation(argument.annotation))
        if node.returns is None:
            raise self.error(node, "functions require a return annotation")
        names = tuple(argument.arg for argument in node.args.args)
        return _MethodSignature(
            tuple(parameters),
            self._annotation(node.returns),
            opcode="invokestatic",
            owner=self.class_internal,
            parameter_names=names,
        )

    def _bind_generators(self, functions: list[ast.FunctionDef]) -> None:
        serial = 0
        for function in functions:
            if function.name == "__init__" or not _has_yield(function):
                continue
            if _is_contextmanager_def(function, self.imports):
                continue
            element = self.methods[function.name].result
            yielded = _iterator_slice(function.returns, self.imports)
            if yielded is not None:
                element = self._annotation(yielded)
            if element == "V":
                raise self.error(function, "a generator requires an element type")
            serial += 1
            synthetic = f"{self.class_internal}$gen${serial}"
            self.generator_names[function.name] = synthetic
            self.generator_elements[synthetic] = element
            current = self.methods[function.name]
            self.methods[function.name] = _MethodSignature(
                current.parameters,
                _descriptor(synthetic),
                opcode=current.opcode,
                owner=current.owner,
                parameter_names=current.parameter_names,
            )

    def _bind_context_managers(self, functions: list[ast.FunctionDef]) -> None:
        serial = 0
        for function in functions:
            if function.name == "__init__" or not _is_contextmanager_def(
                function, self.imports
            ):
                continue
            element = self.methods[function.name].result
            if element == "V":
                raise self.error(function, "contextmanager requires a yielded type")
            serial += 1
            synthetic = f"{self.class_internal}$cm${serial}"
            self.context_names[function.name] = synthetic
            self.context_elements[synthetic] = element
            current = self.methods[function.name]
            self.methods[function.name] = _MethodSignature(
                current.parameters,
                _descriptor(synthetic),
                opcode=current.opcode,
                owner=current.owner,
                parameter_names=current.parameter_names,
            )
            self.project[synthetic] = _context_manager_type(synthetic, element)

    def _compile_method(self, node: ast.FunctionDef, *, constructor: bool) -> None:
        if not constructor and node.name in self.context_names:
            self._compile_context_method(node)
            return
        if not constructor and node.name in self.generator_names:
            self._compile_generator_method(node)
            return
        if not constructor and self.class_internal == "py2apk" and node.name == "chr":
            self._compile_chr_intrinsic(node)
            return
        self.non_final_locals = _non_final_names(node)
        signature = (
            self._constructor_signature(node)
            if constructor
            else self.methods[node.name]
        )
        self.method_result = signature.result
        self._bind_parameters(
            node, signature, static=signature.opcode == "invokestatic"
        )
        self._classify_lists(node.body)

        if constructor:
            self._instruction("aload_0")
            self._instruction(f"invokespecial {self.super_internal}/<init>()V")
        for statement in node.body:
            self._statement(statement)
        if signature.result == "V":
            self._instruction("return")
        else:
            self._emit_default(signature.result)
            self._instruction(_return_opcode(signature.result))

        method_name = "<init>" if constructor else node.name
        access = "public static" if signature.opcode == "invokestatic" else "public"
        self.lines.append(f".method {access} {method_name}{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(self.next_local, 1)}")
        self.lines.extend(self.instructions)
        self.lines.append(".end method")
        self.lines.append("")

    def _compile_chr_intrinsic(self, node: ast.FunctionDef) -> None:
        signature = self.methods[node.name]
        self.method_result = signature.result
        self._bind_parameters(node, signature, static=True)
        self._load_local(node.args.args[0].arg)
        self._emit_char_string()
        self._instruction("areturn")
        self.lines.append(f".method public static chr{signature.descriptor}")
        self.lines.append("    .limit stack 32")
        self.lines.append(f"    .limit locals {max(self.next_local, 1)}")
        self.lines.extend(self.instructions)
        self.lines.append(".end method")
        self.lines.append("")

    def _constructor_signature(self, node: ast.FunctionDef) -> _MethodSignature:
        signature = self._method_signature(node)
        if signature.result != "V":
            raise self.error(node, "constructors must return None")
        return signature

    def _emit_default_constructor(self) -> None:
        self.lines.extend(
            (
                ".method public <init>()V",
                "    .limit stack 1",
                "    .limit locals 1",
                "    aload_0",
                f"    invokespecial {self.super_internal}/<init>()V",
                "    return",
                ".end method",
                "",
            )
        )

    def _protocol(self, owner: str, name: str, node: ast.AST) -> _MethodSignature:
        typed = self.project.get(owner)
        method = typed.methods.get(name) if typed is not None else None
        if method is None:
            raise self.error(node, f"{owner} has no {name}")
        return method

    def _bind_parameters(
        self,
        node: ast.FunctionDef,
        signature: _MethodSignature,
        *,
        static: bool,
    ) -> None:
        self.instructions = []
        self.list_elements = {}
        self.loops = []
        self.handlers = []
        if static:
            self.locals = {}
            self.next_local = 0
            self.preview_locals = {}
            arguments = node.args.args
        else:
            self.locals = {"self": (0, _descriptor(self.class_internal))}
            self.next_local = 1
            self.preview_locals = {"self": _descriptor(self.class_internal)}
            arguments = node.args.args[1:]
        for argument, descriptor in zip(arguments, signature.parameters, strict=True):
            self.locals[argument.arg] = (self.next_local, descriptor)
            self.preview_locals[argument.arg] = descriptor
            self.next_local += 2 if descriptor in {"D", "J"} else 1

    def _define_born(self, mark: int, defined: set[str]) -> None:
        """Store a default for locals first assigned inside a protected region.

        The handler joins the code after the region. A local stored only in
        the body is undefined on that path, and D8 rejects the method.
        """
        prefix: list[str] = []
        for name in self.locals:
            if name in defined:
                continue
            slot, descriptor = self.locals[name]
            saved = self.instructions
            self.instructions = []
            self._emit_default(descriptor)
            self._instruction(f"{self._store_opcode(descriptor)} {slot}")
            prefix.extend(self.instructions)
            self.instructions = saved
        if prefix:
            self.instructions[mark:mark] = prefix

    def _emit_default(self, descriptor: str) -> None:
        if descriptor in {"I", "Z"}:
            self._instruction("iconst_0")
            return
        if descriptor == "F":
            self._instruction("fconst_0")
            return
        if descriptor == "D":
            self._instruction("dconst_0")
            return
        if descriptor == "J":
            self._instruction("lconst_0")
            return
        self._instruction("aconst_null")

    def _emit_return(self, node: ast.Return) -> None:
        if self.generator_internal is not None:
            self._emit_generator_return(node)
            return
        self._emit_plain_return(node)

    def _emit_generator_return(self, node: ast.Return) -> None:
        if node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None
        ):
            raise self.error(node, "a generator return cannot carry a value")
        if self.generator_exhausted is None:
            raise self.error(node, "yield is only supported in a generator method")
        self._instruction(f"goto {self.generator_exhausted}")

    def _emit_plain_return(self, node: ast.Return) -> None:
        if node.value is None or (
            isinstance(node.value, ast.Constant) and node.value.value is None
        ):
            if self.method_result != "V":
                raise self.error(node, "this return needs a value")
            self._instruction("return")
            return
        if isinstance(node.value, ast.Lambda) and _is_function_type(self.method_result):
            _ = self._emit_function_lambda(node.value, self.method_result)
            self._instruction("areturn")
            return
        actual = self._expression(node.value)
        if actual != self.method_result and not self._compatible(
            actual, self.method_result
        ):
            raise self.error(
                node, f"return type {actual} does not match {self.method_result}"
            )
        self._instruction(_return_opcode(self.method_result))

    def _assign(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Tuple):
            self._unpack_assign(target, value)
            return
        if isinstance(target, ast.Subscript):
            self._subscript_store(target, value)
            return
        self._store(target, self._assigned_value(target, value))

    def _unpack_assign(self, target: ast.Tuple, value: ast.expr) -> None:
        if any(isinstance(element, ast.Starred) for element in target.elts):
            raise self.error(target, "starred assignment targets are unsupported")
        if not isinstance(value, ast.Tuple):
            raise self.error(value, "unpacking requires a tuple of the same length")
        if any(isinstance(element, ast.Starred) for element in value.elts):
            raise self.error(value, "starred assignment values are unsupported")
        if len(target.elts) != len(value.elts):
            raise self.error(target, "assignment unpack length does not match")
        temps: list[tuple[str, str]] = []
        for element in value.elts:
            descriptor = self._expression(element)
            temps.append((self._hold_temp(descriptor), descriptor))
        for element, (name, descriptor) in zip(target.elts, temps, strict=True):
            self._load_local(name)
            self._store(element, descriptor)

    def _assigned_value(self, target: ast.expr, value: ast.expr) -> str:
        self.list_sink = target.id if isinstance(target, ast.Name) else None
        try:
            return self._expression(value)
        finally:
            self.list_sink = None

    def _store(self, target: ast.expr, descriptor: str) -> None:
        element = _list_element(descriptor)
        stored = _jvm_descriptor(descriptor)
        if isinstance(target, ast.Name):
            if element is None:
                self.list_elements.pop(target.id, None)
            else:
                previous = self.list_elements.get(target.id)
                if previous not in {None, element}:
                    raise self.error(
                        target, f"cannot change the element type of {target.id!r}"
                    )
                self.list_elements[target.id] = element
            local = self.locals.get(target.id)
            if local is None:
                local = (self.next_local, stored)
                self.locals[target.id] = local
                self.next_local += 2 if stored in {"D", "J"} else 1
            elif local[1] != stored:
                raise self.error(
                    target,
                    f"cannot change local {target.id!r} from {local[1]} to {stored}",
                )
            self._track_generator_local(target, stored)
            self._instruction(f"{self._store_opcode(stored)} {local[0]}")
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            field_type = self.fields.get(target.attr)
            if field_type is None:
                raise self.error(target, f"unknown field {target.attr!r}")
            if _jvm_descriptor(descriptor) != field_type and not self._compatible(
                descriptor, field_type
            ):
                raise self.error(
                    target,
                    f"field {target.attr!r} expects {field_type}, got {descriptor}",
                )
            # The value is already on the stack; putfield needs object, value.
            self._instruction("aload_0")
            self._instruction("swap")
            self._instruction(
                f"putfield {self.class_internal}/{target.attr} {field_type}"
            )
            return
        raise self.error(
            target, f"unsupported assignment target {type(target).__name__}"
        )

    def _track_generator_local(self, target: ast.Name, stored: str) -> None:
        if self.generator_fields is None or target.id.startswith("$"):
            return
        previous = self.generator_fields.get(target.id)
        if previous not in {None, stored}:
            raise self.error(
                target, f"cannot change local {target.id!r} from {previous} to {stored}"
            )
        self.generator_fields[target.id] = stored


def _descriptor_of_function_ref(compiler: _ModuleMixin, node: ast.Name) -> str:
    binding = compiler._function_ref_binding(node.id, node)
    if binding is None:
        raise compiler.error(node, f"unknown local {node.id!r}")
    _owner, _method, signature = binding
    return _function_type(signature.parameters, signature.result)
