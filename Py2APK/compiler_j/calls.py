"""Calls, attributes, builtins, and method lookup."""

from __future__ import annotations

import ast
from typing import Any, ClassVar

from Py2APK.classfile import parameter_descriptors
from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.generators import (
    _NULLCONTEXT,
    _SUPPRESS,
)
from Py2APK.compiler_j.types import (
    _NUMERIC_CASTS,
    _PARSE_METHODS,
    _STRING,
    _STRING_ARRAY,
    _WIDEN,
    _array_descriptor,
    _descriptor,
    _internal_name,
    _is_function_type,
    _java_method_name,
    _list_element,
    _MethodSignature,
    _parse_function_type,
    _static_matches,
    math_method,
)


def _best_signature(
    candidates: list[_MethodSignature],
    arguments: tuple[str, ...],
    compiler: _CompilerBase,
) -> _MethodSignature | None:
    exact = [item for item in candidates if item.parameters == arguments]
    if len(exact) == 1:
        return exact[0]
    compatible = [
        item
        for item in candidates
        if len(item.parameters) == len(arguments)
        and all(
            compiler._compatible(actual, wanted) or (actual == "I" and wanted == "J")
            for actual, wanted in zip(arguments, item.parameters, strict=True)
        )
    ]
    if len(compatible) == 1:
        return compatible[0]
    if compatible and len({(item.parameters, item.result) for item in compatible}) == 1:
        return compatible[0]
    if exact:
        return exact[0]
    return None


_FAILURE_RANK = {"collision": 4, "unknown": 3, "missing": 2, "nameless": 1, "arity": 0}


def _keyword_problem(node: ast.Call) -> str | None:
    seen: set[str] = set()
    for keyword in node.keywords:
        if keyword.arg is None:
            return "unpacked keyword arguments are unsupported"
        if keyword.arg in seen:
            return f"duplicate keyword argument {keyword.arg!r}"
        seen.add(keyword.arg)
    return None


def _order_arguments(
    positional: list[ast.expr],
    keywords: list[ast.keyword],
    names: tuple[str, ...],
) -> tuple[ast.expr, ...] | str:
    if not names or all(not name for name in names):
        return "nameless"
    if len(positional) > len(names):
        return "arity"
    slots: list[ast.expr | None] = list(positional)
    slots.extend([None] * (len(names) - len(positional)))
    for keyword in keywords:
        name = keyword.arg
        if name is None or name not in names:
            return f"unknown:{name}"
        index = names.index(name)
        if slots[index] is not None:
            return f"collision:{name}"
        slots[index] = keyword.value
    ordered: list[ast.expr] = []
    for name, slot in zip(names, slots, strict=True):
        if slot is None:
            return "nameless" if not name else f"missing:{name}"
        ordered.append(slot)
    return tuple(ordered)


def _failure_message(code: str) -> str:
    if code == "nameless":
        return "keyword arguments need parameter names"
    if code == "arity":
        return "keyword arguments do not match this method"
    kind, _, detail = code.partition(":")
    if kind == "unknown":
        return f"unknown keyword argument {detail!r}"
    if kind == "collision":
        return f"keyword argument {detail!r} overlaps a positional argument"
    if kind == "missing":
        return f"missing argument for parameter {detail!r}"
    return "keyword arguments are unsupported"


def _note_failure(sink: list[str], code: str) -> None:
    if code.split(":", 1)[0] not in _FAILURE_RANK:
        return
    if not sink or _FAILURE_RANK[code.split(":", 1)[0]] >= _FAILURE_RANK.get(
        sink[0].split(":", 1)[0], -1
    ):
        if sink:
            sink[0] = code
        else:
            sink.append(code)


def _types_match(
    compiler: _CompilerBase,
    parameters: tuple[str, ...],
    arguments: tuple[str, ...],
) -> bool:
    return len(parameters) == len(arguments) and all(
        compiler._compatible(actual, wanted) or (actual == "I" and wanted == "J")
        for actual, wanted in zip(arguments, parameters, strict=True)
    )


def _bound_constructor(
    compiler: _CompilerBase, owner: str, node: ast.Call
) -> _MethodSignature:
    local = compiler.project.get(owner)
    if local is not None:
        return _MethodSignature(
            local.constructor,
            "V",
            "invokespecial",
            owner,
            local.constructor_names,
        )
    signature = compiler._find_method(
        owner, "<init>", compiler._argument_types(node), static=False, call=node
    )
    if signature is None:
        raise compiler.error(node, f"unknown constructor {owner}")
    return signature


def _reject_newer_api(
    compiler: _CompilerBase,
    node: ast.Call,
    name: str,
    signature: _MethodSignature,
) -> None:
    """Fail when a catalog member is newer than ``compiler.min_api``."""
    if compiler.min_api is None or not signature.owner:
        return
    if (
        signature.owner == compiler.class_internal
        or signature.owner in compiler.project
    ):
        return
    since = compiler.catalog.api_since(signature.owner, name + signature.descriptor)
    if since is None or since <= compiler.min_api:
        return
    dotted = signature.owner.replace("/", ".")
    raise compiler.error(
        node,
        f"{dotted}.{name} requires API {since}, minSdkVersion is {compiler.min_api}",
    )


def _reject_unsupported_keywords(compiler: _CompilerBase, node: ast.Call) -> None:
    if node.keywords:
        raise compiler.error(node, "keyword arguments are unsupported")


def _guard_builtin_keywords(compiler: _CompilerBase, node: ast.Call) -> None:
    problem = _keyword_problem(node)
    if problem is not None:
        raise compiler.error(node, problem)


def _ordered_exprs(
    compiler: _CompilerBase, node: ast.Call, signature: _MethodSignature
) -> tuple[ast.expr, ...]:
    return _ordered_named(compiler, node, signature.parameter_names)


def _ordered_named(
    compiler: _CompilerBase, node: ast.Call, names: tuple[str, ...]
) -> tuple[ast.expr, ...]:
    if not node.keywords:
        return tuple(node.args)
    problem = _keyword_problem(node)
    if problem is not None:
        raise compiler.error(node, problem)
    ordered = _order_arguments(node.args, node.keywords, names)
    if isinstance(ordered, str):
        raise compiler.error(node, _failure_message(ordered))
    return ordered


def _select_signature(
    compiler: _CompilerBase,
    candidates: list[_MethodSignature],
    arguments: tuple[str, ...],
) -> _MethodSignature | None:
    call = compiler._keyword_call
    if call is None or not call.keywords:
        return _best_signature(candidates, arguments, compiler)
    problem = _keyword_problem(call)
    if problem is not None:
        raise compiler.error(call, problem)
    if not candidates:
        return None
    return _select_keywords(compiler, candidates, arguments, call)


def _select_keywords(
    compiler: _CompilerBase,
    candidates: list[_MethodSignature],
    positional_types: tuple[str, ...],
    call: ast.Call,
) -> _MethodSignature | None:
    matches: list[tuple[_MethodSignature, tuple[str, ...]]] = []
    for signature in candidates:
        ordered = _order_arguments(call.args, call.keywords, signature.parameter_names)
        if isinstance(ordered, str):
            _note_failure(compiler._keyword_failures, ordered)
            continue
        types = _bound_types(compiler, call, positional_types, ordered)
        if not _types_match(compiler, signature.parameters, types):
            continue
        matches.append((signature, types))
    if not matches:
        return None
    chosen = _pick_keyword_match(matches)
    if chosen is None:
        raise compiler.error(call, "ambiguous keyword call")
    return chosen


def _bound_types(
    compiler: _CompilerBase,
    call: ast.Call,
    positional_types: tuple[str, ...],
    ordered: tuple[ast.expr, ...],
) -> tuple[str, ...]:
    types: list[str] = []
    for expr in ordered:
        matched = False
        for index, argument in enumerate(call.args):
            if argument is expr and index < len(positional_types):
                types.append(positional_types[index])
                matched = True
                break
        if matched:
            continue
        types.append(compiler._type_of(expr))
    return tuple(types)


def _catalog_signatures(
    compiler: _CompilerBase, owner: str, name: str, static: bool
) -> list[_MethodSignature]:
    found: list[_MethodSignature] = []
    for (
        params,
        result,
        method_owner,
        is_static,
        opcode,
        param_names,
    ) in compiler.catalog.methods(owner, name):
        if is_static != static and name != "<init>":
            continue
        found.append(
            _MethodSignature(
                parameter_descriptors(params),
                result,
                opcode,
                method_owner,
                param_names,
            )
        )
    return found


def _pick_keyword_match(
    matches: list[tuple[_MethodSignature, tuple[str, ...]]],
) -> _MethodSignature | None:
    exact = [item for item in matches if item[0].parameters == item[1]]
    pool = exact or matches
    descriptors = {(item[0].parameters, item[0].result) for item in pool}
    if len(descriptors) == 1 or len(pool) == 1:
        return pool[0][0]
    return None


def _append_is_list(compiler: _CallsMixin, node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return True
    if func.value.id in compiler.list_elements:
        return True
    local = compiler.locals.get(func.value.id)
    if local is None:
        return True
    return local[1].startswith("[")


def _lower_string_call(
    compiler: _CallsMixin, node: ast.Call, receiver_type: str
) -> str | None:
    found = compiler._str_method(node)
    if found is None:
        return None
    name, _receiver = found
    if name == "split":
        return _lower_split(compiler, node, receiver_type)
    if name == "join":
        _emit_str_join(compiler, node)
        return _STRING
    return None


def _lower_split(
    compiler: _CallsMixin, node: ast.Call, receiver_type: str
) -> str | None:
    if receiver_type != _STRING:
        return None
    literal = _split_literal(compiler, node)
    if literal is None:
        return None
    _emit_literal_split(compiler, node)
    return _STRING_ARRAY


def _split_literal(compiler: _CallsMixin, node: ast.Call) -> str | None:
    if node.keywords:
        raise compiler.error(node, "split keywords are unsupported")
    if len(node.args) == 0:
        raise compiler.error(node, "split needs a separator")
    if len(node.args) != 1:
        raise compiler.error(node, "split maxsplit is unsupported")
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and argument.value is None:
        raise compiler.error(node, "split(None) is unsupported")
    literal = compiler._literal_str(argument)
    if literal == "":
        raise compiler.error(node, "empty separator")
    return literal


def _emit_literal_split(compiler: _CallsMixin, node: ast.Call) -> None:
    argument = node.args[0]
    if not isinstance(argument, ast.Constant):
        raise compiler.error(node, "split needs a separator")
    _ = compiler._constant(argument)
    quote = compiler._require_method(
        "java/util/regex/Pattern",
        "quote",
        (_STRING,),
        static=True,
        node=node,
    )
    _reject_newer_api(compiler, node, "quote", quote)
    compiler._invoke(quote, "quote")
    compiler._emit_int(-1)
    split = compiler._require_method(
        "java/lang/String",
        "split",
        (_STRING, "I"),
        static=False,
        node=node,
    )
    _reject_newer_api(compiler, node, "split", split)
    compiler._invoke(split, "split")


def _emit_str_join(compiler: _CallsMixin, node: ast.Call) -> None:
    kind = _join_kind(compiler, node)
    _ = compiler._expression(node.args[0])
    if kind == "arraylist":
        compiler._emit_to_array(_STRING)
    compiler._instruction(
        "invokestatic py2apk/str_join"
        "(Ljava/lang/String;[Ljava/lang/String;)Ljava/lang/String;"
    )


def _join_kind(compiler: _CallsMixin, node: ast.Call) -> str:
    if node.keywords:
        raise compiler.error(node, "join keywords are unsupported")
    if len(node.args) != 1:
        raise compiler.error(node, "join takes one positional argument")
    kind = compiler._string_sequence(compiler._type_of(node.args[0]))
    if kind is None:
        raise compiler.error(node, "join needs a list or tuple of str")
    return kind


def _type_string_call(compiler: _CallsMixin, node: ast.Call) -> str | None:
    found = compiler._str_method(node)
    if found is None:
        return None
    name, _receiver = found
    if name == "split":
        literal = _split_literal(compiler, node)
        if literal is None:
            return None
        return _STRING_ARRAY
    if name != "join":
        return None
    _ = _join_kind(compiler, node)
    return _STRING


def _str_join_signature(
    compiler: _CallsMixin, node: ast.Call, arguments: tuple[str, ...]
) -> _MethodSignature | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "join":
        return None
    if node.keywords or len(node.args) != 1:
        return None
    value = node.func.value
    if isinstance(value, ast.Name) and value.id not in compiler.locals:
        if compiler._preview_type(value) != _STRING:
            return None
    else:
        found = compiler._str_method(node)
        if found is None or found[0] != "join":
            return None
    kind = _signature_parts_kind(compiler, node.args[0], arguments)
    if kind is None:
        return None
    return _MethodSignature((_STRING_ARRAY,), _STRING)


def _signature_parts_kind(
    compiler: _CallsMixin, node: ast.expr, arguments: tuple[str, ...]
) -> str | None:
    if len(arguments) == 1:
        kind = compiler._string_sequence(arguments[0])
        if kind is not None:
            return kind
    preview = compiler._preview_type(node)
    if preview is not None:
        kind = compiler._string_sequence(preview)
        if kind is not None:
            return kind
    if isinstance(node, ast.Name) and node.id not in compiler.locals:
        return None
    return compiler._string_sequence(compiler._type_of(node))


class _CallsMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Attribute: "_attribute",
        ast.Call: "_call",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Attribute: "_type_of_attribute",
        ast.Call: "_type_call",
        ast.Constant: "_type_constant",
    }

    def _type_constant(self, node: ast.Constant) -> str:
        constant = self._type_of_constant(node)
        if constant is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return constant

    def _type_call(self, node: ast.Call) -> str:
        call = self._type_of_call(node)
        if call is None:
            raise self.error(node, f"unsupported expression {type(node).__name__}")
        return call

    def _builtin_call(self, node: ast.Call) -> str:
        return self._dispatch_builtin(node, emit=True)

    def _builtin_call_body(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, "call target must be a known constructor or method")
        _guard_builtin_keywords(self, node)
        name = node.func.id
        if name == "cast":
            return self._emit_cast(node)
        types = self._argument_types(node)
        if name == "str" and len(types) == 1:
            return self._static_invoke(node, "java/lang/String", "valueOf", types)
        if name in _PARSE_METHODS and len(types) == 1:
            owner, method, result = _PARSE_METHODS[name]
            if types[0] == _STRING:
                _ = self._static_invoke(node, owner, method, types)
                return result
            return self._numeric_cast(node, types[0], result)
        if name in _PARSE_METHODS:
            raise self.error(node, f"{name}() accepts one argument")
        bound = self._static_binding(name, types, node)
        if node.keywords and bound is None:
            raise self.error(node, "keyword arguments are unsupported")
        if bound is not None:
            owner, method, signature = bound
            self._emit_arguments(
                node, signature.parameters, _ordered_exprs(self, node, signature)
            )
            self._instruction(f"invokestatic {owner}/{method}{signature.descriptor}")
            return signature.result
        imported = self.imported_functions.get(name)
        if imported is not None and imported[0] == "java/lang/Math":
            raise self.error(
                node, f"unknown method signature java/lang/Math.{imported[1]}"
            )
        if name == "chr" and len(types) == 1 and types[0] == "I":
            _ = self._expression(node.args[0])
            self._instruction("invokestatic py2apk/chr(I)Ljava/lang/String;")
            return _STRING
        if (
            name == "range"
            and 1 <= len(types) <= 3
            and all(kind == "I" for kind in types)
        ):
            return self._emit_range(node)
        if name == "len" and len(types) == 1:
            descriptor = self._expression(node.args[0])
            self._emit_len(node, descriptor)
            return "I"
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            return self._static_invoke(node, "java/lang/Math", "abs", types)
        if (
            name in {"min", "max"}
            and len(types) == 2
            and types[0] == types[1]
            and types[0] in {"I", "F", "D"}
        ):
            return self._static_invoke(node, "java/lang/Math", name, types)
        raise self.error(node, f"unsupported builtin {name}")

    def _static_binding(
        self,
        name: str,
        types: tuple[str, ...],
        call: ast.Call | None = None,
    ) -> tuple[str, str, _MethodSignature] | None:
        if name in self.methods and self.methods[name].opcode == "invokestatic":
            signature = self._find_method(
                self.class_internal, name, types, static=True, call=call
            )
            if signature is not None:
                return self.class_internal, name, signature
        imported = self.imported_functions.get(name)
        if imported is None:
            return None
        owner, method = imported
        if owner == "java/lang/Math":
            signature = self._math_signature(
                owner,
                method,
                types,
                call,
                name in self.math_force_double,
            )
            if signature is None:
                return None
            return owner, method, signature
        signature = self._find_method(owner, method, types, static=True, call=call)
        if signature is None:
            return None
        return owner, method, signature

    def _math_signature(
        self,
        owner: str,
        java_name: str,
        types: tuple[str, ...],
        call: ast.Call | None,
        force_double: bool,
    ) -> _MethodSignature | None:
        if owner != "java/lang/Math":
            return None
        if not force_double:
            found = self._find_method(owner, java_name, types, static=True, call=call)
            if found is not None:
                return found
        widened = tuple("D" if item in {"I", "F"} else item for item in types)
        if not force_double and widened == types:
            return None
        return self._find_method(owner, java_name, widened, static=True, call=call)

    def _math_call_signature(
        self, node: ast.Call, owner: str
    ) -> tuple[str, _MethodSignature]:
        func = node.func
        if not isinstance(func, ast.Attribute):
            raise self.error(node, "unknown method signature java/lang/Math")
        java_name = _java_method_name(owner, func.attr)
        row = math_method(func.attr)
        if row is None:
            raise self.error(
                node, f"unknown method signature java/lang/Math.{func.attr}"
            )
        signature = self._math_signature(
            owner,
            java_name,
            self._argument_types(node),
            node,
            row[2],
        )
        if signature is None:
            raise self.error(
                node, f"unknown method signature java/lang/Math.{java_name}"
            )
        return java_name, signature

    def _emit_char_string(self) -> None:
        code = self._hold_temp("I")
        self._instruction("new java/lang/String")
        self._instruction("dup")
        self._instruction("iconst_1")
        self._instruction("newarray char")
        self._instruction("dup")
        self._instruction("iconst_0")
        self._load_local(code)
        self._instruction("i2c")
        self._instruction("castore")
        self._instruction("invokespecial java/lang/String/<init>([C)V")

    def _builtin_result(self, node: ast.Call) -> str:
        return self._dispatch_builtin(node, emit=False)

    def _dispatch_builtin(self, node: ast.Call, *, emit: bool) -> str:
        found = _dispatch_named_call(self, node, emit=emit)
        if found is not None:
            return found
        higher = self._dispatch_higher_order(node, emit=emit)
        if higher is not None:
            return higher
        if emit:
            return self._builtin_call_body(node)
        return self._builtin_result_body(node)

    def _builtin_result_body(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise self.error(node, f"unsupported expression {type(node.func).__name__}")
        name = node.func.id
        if name == "cast" and len(node.args) == 2:
            return self._cast_descriptor(node)
        types = self._argument_types(node)
        bound = self._static_binding(name, types, node)
        if bound is not None:
            return bound[2].result
        if name == "chr" and len(types) == 1 and types[0] == "I":
            return _STRING
        if (
            name == "range"
            and 1 <= len(types) <= 3
            and all(kind == "I" for kind in types)
        ):
            self.generator_elements["py2apk$gen$1"] = "I"
            return "Lpy2apk$gen$1;"
        if name == "str" and len(types) == 1:
            if (
                self._find_method("java/lang/String", "valueOf", types, static=True)
                is None
            ):
                raise self.error(
                    node, "unknown method signature java/lang/String.valueOf"
                )
            return _STRING
        if name in _PARSE_METHODS and len(types) == 1:
            owner, method, result = _PARSE_METHODS[name]
            if types[0] == _STRING:
                if self._find_method(owner, method, types, static=True) is None:
                    raise self.error(node, f"unknown method signature {owner}.{method}")
                return result
            if (types[0], result) not in _NUMERIC_CASTS:
                raise self.error(node, f"{name}() does not accept {types[0]}")
            return result
        if name == "len" and len(types) == 1 and self._len_accepted(types[0]):
            return "I"
        if name == "abs" and len(types) == 1 and types[0] in {"I", "F", "D"}:
            if self._find_method("java/lang/Math", "abs", types, static=True) is None:
                raise self.error(node, "unknown method signature java/lang/Math.abs")
            return types[0]
        if (
            name in {"min", "max"}
            and len(types) == 2
            and types[0] == types[1]
            and types[0] in {"I", "F", "D"}
        ):
            if self._find_method("java/lang/Math", name, types, static=True) is None:
                raise self.error(
                    node, f"unknown method signature java/lang/Math.{name}"
                )
            return types[0]
        raise self.error(node, f"unsupported builtin {name}")

    def _numeric_cast(self, node: ast.Call, actual: str, target: str) -> str:
        opcode = _NUMERIC_CASTS.get((actual, target))
        if opcode is None:
            raise self.error(node, f"cannot convert {actual} to {target}")
        _ = self._expression(node.args[0])
        if opcode:
            self._instruction(opcode)
        return target

    def _instance_field(self, name: str) -> tuple[str, dict[str, str]] | None:
        local = self.locals.get(name)
        if local is None or not local[1].startswith("L"):
            return None
        owner = _internal_name(local[1])
        typed = self.project.get(owner)
        if typed is None or not typed.fields:
            return None
        return owner, typed.fields

    def _attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id != "self":
            found = self._instance_field(node.value.id)
            if found is not None and node.attr in found[1]:
                self._load_local(node.value.id)
                field_type = found[1][node.attr]
                self._instruction(f"getfield {found[0]}/{node.attr} {field_type}")
                return field_type
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            field_type = self.fields.get(node.attr)
            if field_type is None:
                if node.attr in self.methods:
                    raise self.error(
                        node, "pass the method as a listener argument, or call it"
                    )
                raise self.error(node, f"unknown field {node.attr!r}")
            if self.lambda_body:
                self._load_local("self")
            else:
                self._instruction("aload_0")
            self._instruction(
                f"getfield {self.class_internal}/{node.attr} {field_type}"
            )
            return field_type

        owner = self._resolve_type_name(node.value)
        found = self.catalog.field(owner, node.attr)
        if found is None or not found[1]:
            raise self.error(node, f"unknown static field {owner}.{node.attr}")
        self._instruction(f"getstatic {owner}/{node.attr} {found[0]}")
        return found[0]

    def _call(self, node: ast.Call) -> str:
        if self._names_imported(node.func, _SUPPRESS):
            _reject_unsupported_keywords(self, node)
            raise self.error(node, "suppress is only supported in with")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and _append_is_list(self, node)
        ):
            return self._list_append(node)
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(
            node.func.value
        ):
            owner = self._resolve_type_name(node.func.value)
            if owner == "java/lang/Math":
                java_name, signature = self._math_call_signature(node, owner)
                self._emit_arguments(
                    node, signature.parameters, _ordered_exprs(self, node, signature)
                )
                self._instruction(
                    f"invokestatic {signature.owner}/{java_name}{signature.descriptor}"
                )
                return signature.result
            signature = self._find_method(
                owner,
                node.func.attr,
                self._argument_types(node),
                static=True,
                call=node,
            )
            if signature is not None:
                self._emit_arguments(
                    node, signature.parameters, _ordered_exprs(self, node, signature)
                )
                self._instruction(
                    f"invokestatic {signature.owner}/{node.func.attr}{signature.descriptor}"
                )
                return signature.result
            return self._emit_constructor(f"{owner}${node.func.attr}", node)
        if self._is_type_reference(node.func):
            return self._emit_constructor(self._resolve_type_name(node.func), node)
        if not isinstance(node.func, ast.Attribute):
            return self._builtin_call(node)
        if self._is_super_call(node.func.value):
            signature = self._find_method(
                self.super_internal,
                node.func.attr,
                self._argument_types(node),
                static=False,
                call=node,
            )
            if signature is None:
                raise self.error(
                    node, f"unknown super call {self.super_internal}.{node.func.attr}"
                )
            self._instruction("aload_0")
            self._emit_arguments(
                node, signature.parameters, _ordered_exprs(self, node, signature)
            )
            self._instruction(
                f"invokespecial {signature.owner or self.super_internal}/{node.func.attr}{signature.descriptor}"
            )
            return signature.result
        receiver_type = self._expression(node.func.value)
        if not receiver_type.startswith("L"):
            raise self.error(
                node, f"method receiver must be an object, got {receiver_type}"
            )
        lowered = _lower_string_call(self, node, receiver_type)
        if lowered is not None:
            return lowered
        owner = _internal_name(receiver_type)
        method_name = _java_method_name(owner, node.func.attr)
        signature = self._find_method(
            owner, method_name, self._argument_types(node), static=False, call=node
        )
        if signature is None:
            raise self.error(node, f"unknown method signature {owner}.{method_name}")
        self._emit_arguments(
            node, signature.parameters, _ordered_exprs(self, node, signature)
        )
        declaring = signature.owner or owner
        instruction = (
            f"{signature.opcode} {declaring}/{method_name}{signature.descriptor}"
        )
        if signature.opcode == "invokeinterface":
            instruction += f" {len(signature.parameters) + 1}"
        self._instruction(instruction)
        if method_name == "next" and owner in self.generator_elements:
            return self._restore_yield_type(self.generator_elements[owner])
        return signature.result

    def _emit_constructor(self, owner: str, node: ast.Call) -> str:
        if owner == _NULLCONTEXT and not node.args and not node.keywords:
            self._instruction(f"new {owner}")
            self._instruction("dup")
            self._instruction("aconst_null")
            self._instruction(f"invokespecial {owner}/<init>(Ljava/lang/Object;)V")
            return _descriptor(owner)
        signature = _bound_constructor(self, owner, node)
        parameters = signature.parameters
        self._instruction(f"new {owner}")
        self._instruction("dup")
        self._emit_arguments(
            node,
            parameters,
            _ordered_named(self, node, signature.parameter_names),
        )
        self._instruction(f"invokespecial {owner}/<init>({''.join(parameters)})V")
        return _descriptor(owner)

    def _argument_types(self, node: ast.Call) -> tuple[str, ...]:
        return tuple(self._type_of(argument) for argument in node.args)

    def _type_of_constant(self, node: ast.Constant) -> str | None:
        value = node.value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "Z"
        if isinstance(value, int):
            return "I"
        if isinstance(value, float):
            return "F"
        if isinstance(value, str):
            return _STRING
        return None

    def _type_of_attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id != "self":
            found = self._instance_field(node.value.id)
            if found is not None and node.attr in found[1]:
                return found[1][node.attr]
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            field_type = self.fields.get(node.attr)
            if field_type is None and node.attr in self.methods:
                return "lambda"
            if field_type is None:
                raise self.error(node, f"unknown field {node.attr!r}")
            return field_type
        found = self.catalog.field(self._resolve_type_name(node.value), node.attr)
        if found is None:
            raise self.error(node, f"unknown static field {node.attr}")
        return found[0]

    def _type_of_call(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name) and not self._is_type_reference(node.func):
            return self._builtin_result(node)
        if isinstance(node.func, ast.Attribute) and self._is_type_reference(
            node.func.value
        ):
            owner = self._resolve_type_name(node.func.value)
            if owner == "java/lang/Math":
                return self._math_call_signature(node, owner)[1].result
            signature = self._find_method(
                owner,
                node.func.attr,
                self._argument_types(node),
                static=True,
                call=node,
            )
            if signature is not None:
                return signature.result
            return _descriptor(f"{owner}${node.func.attr}")
        if self._is_type_reference(node.func):
            return _descriptor(self._resolve_type_name(node.func))
        if isinstance(node.func, ast.Attribute):
            typed = _type_string_call(self, node)
            if typed is not None:
                return typed
            receiver = self._type_of(node.func.value)
            if receiver.startswith("L"):
                owner = _internal_name(receiver)
                method_name = _java_method_name(owner, node.func.attr)
                signature = self._find_method(
                    owner,
                    method_name,
                    self._argument_types(node),
                    static=False,
                    call=node,
                )
                if signature is not None:
                    return self._yielded_result(owner, method_name, signature.result)
        return None

    def _signature_for_call(
        self, node: ast.Call, arguments: tuple[str, ...]
    ) -> _MethodSignature | None:
        joined = _str_join_signature(self, node, arguments)
        if joined is not None:
            return joined
        if not isinstance(node.func, ast.Attribute):
            return None
        func = node.func
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            return self._find_method(
                self.class_internal, func.attr, arguments, static=False, call=None
            )
        if self._is_type_reference(func.value):
            return self._find_method(
                self._resolve_type_name(func.value),
                func.attr,
                arguments,
                static=True,
                call=None,
            )
        receiver = self._preview_type(func.value)
        if receiver is None or not receiver.startswith("L"):
            return None
        return self._find_method(
            _internal_name(receiver), func.attr, arguments, static=False, call=None
        )

    def _str_method(self, node: ast.Call) -> tuple[str, str] | None:
        if not isinstance(node.func, ast.Attribute):
            return None
        attr = node.func.attr
        if attr not in {"split", "join"}:
            return None
        receiver = self._type_of(node.func.value)
        if receiver != _STRING:
            return None
        return attr, receiver

    def _literal_str(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _string_sequence(self, descriptor: str) -> str | None:
        if descriptor == _STRING_ARRAY:
            return "array"
        if descriptor == f"arraylist:{_STRING}":
            return "arraylist"
        return None

    def _interface_bases(self, internal: str) -> set[str]:
        if self.catalog.is_interface(internal):
            return {internal}
        return set()

    def _is_type_reference(self, node: ast.expr) -> bool:
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and (
            root.id in self.imports or root.id == self.class_name
        )

    @staticmethod
    def _is_super_call(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "super"
            and not node.args
            and not node.keywords
        )

    def _find_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        *,
        static: bool,
        call: ast.Call | None = None,
    ) -> _MethodSignature | None:
        previous_call = self._keyword_call
        previous_failures = self._keyword_failures
        self._keyword_call = call
        self._keyword_failures = []
        try:
            found = self._search_method(owner, name, arguments, static, set())
        finally:
            failure = self._keyword_failures
            self._keyword_call = previous_call
            self._keyword_failures = previous_failures
        if found is None and failure and call is not None and call.keywords:
            raise self.error(call, _failure_message(failure[0]))
        if found is not None and call is not None:
            _reject_newer_api(self, call, name, found)
        return found

    def _search_method(
        self,
        owner: str,
        name: str,
        arguments: tuple[str, ...],
        static: bool,
        seen: set[str],
    ) -> _MethodSignature | None:
        if owner in seen:
            return None
        seen.add(owner)
        candidates: list[_MethodSignature] = []
        generated = self._generator_method(owner, name, arguments, static)
        if generated is not None:
            candidates.append(generated)
        if (
            owner == self.class_internal
            and name in self.methods
            and _static_matches(self.methods[name], static)
        ):
            candidates.append(self.methods[name])
        local = self.project.get(owner)
        if (
            local is not None
            and name in local.methods
            and _static_matches(local.methods[name], static)
        ):
            candidates.append(local.methods[name])
        candidates.extend(_catalog_signatures(self, owner, name, static))
        match = _select_signature(self, candidates, arguments)
        if match is not None:
            return match
        bases: list[str] = []
        if local is not None and local.base:
            bases.append(local.base)
        if owner == self.class_internal and self.super_internal not in {
            "",
            "java/lang/Object",
        }:
            bases.append(self.super_internal)
        super_name = self.catalog.super_of(owner)
        if super_name:
            bases.append(super_name)
        bases.extend(self.catalog.interfaces_of(owner))
        for base in bases:
            found = self._search_method(base, name, arguments, static, seen)
            if found is not None:
                return found
        return None

    def _emit_arguments(
        self,
        node: ast.Call,
        expected: tuple[str, ...],
        arguments: tuple[ast.expr, ...] | None = None,
    ) -> None:
        exprs = tuple(node.args) if arguments is None else arguments
        if len(exprs) != len(expected):
            raise self.error(
                node, f"expected {len(expected)} arguments, got {len(exprs)}"
            )
        for argument, wanted in zip(exprs, expected, strict=True):
            self._emit_expected_argument(argument, wanted)

    def _emit_expected_argument(self, argument: ast.expr, wanted: str) -> None:
        if isinstance(argument, ast.Lambda) and _is_function_type(wanted):
            _ = self._emit_function_lambda(argument, wanted)
            return
        if isinstance(argument, ast.Lambda) or self._is_method_reference(argument):
            self._emit_listener(argument, wanted)
            return
        actual = self._expression(argument)
        element = _list_element(actual)
        if element is not None and wanted == _array_descriptor(element):
            if not element.startswith(("L", "[")):
                raise self.error(
                    argument, "a changed number list cannot be passed as an array"
                )
            self._emit_to_array(element)
            actual = wanted
        widen = _WIDEN.get((actual, wanted))
        if widen is not None:
            self._instruction(widen)
            actual = wanted
        if not self._compatible(actual, wanted):
            raise self.error(
                argument, f"argument has type {actual}, expected {wanted}"
            )


def _local_function_signature(
    compiler: _CompilerBase, node: ast.Call
) -> tuple[str, tuple[str, ...], str] | None:
    if not isinstance(node.func, ast.Name):
        return None
    local = compiler.locals.get(node.func.id)
    if local is None:
        return None
    return _parse_function_type(local[1])


def _dispatch_named_call(
    compiler: _CompilerBase, node: ast.Call, *, emit: bool
) -> str | None:
    found = _local_function_signature(compiler, node)
    if found is None:
        return None
    if node.keywords:
        raise compiler.error(node, "keyword arguments are unsupported")
    if emit:
        return _emit_local_function_call(compiler, node, found)
    return found[2]


def _emit_local_function_call(
    compiler: _CompilerBase,
    node: ast.Call,
    fn: tuple[str, tuple[str, ...], str],
) -> str:
    internal, params, result = fn
    if not isinstance(node.func, ast.Name):
        raise compiler.error(node, "call target must be a known constructor or method")
    compiler._load_local(node.func.id)
    compiler._emit_arguments(node, params)
    compiler._instruction(f"invokevirtual {internal}/call({''.join(params)}){result}")
    return result
