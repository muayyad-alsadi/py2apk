"""List, tuple, dict, set, and subscript lowering."""

from __future__ import annotations

import ast
from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar

from Py2APK.compiler_j.base import _CompilerBase
from Py2APK.compiler_j.types import (
    _ARRAY_LIST,
    _ARRAY_LIST_OWNER,
    _ARRAY_LOAD,
    _ARRAY_STORE,
    _HASH_SET,
    _HASH_SET_OWNER,
    _LINKED_HASH_MAP,
    _LINKED_HASH_MAP_OWNER,
    _NEWARRAY,
    _OBJECT_ARRAY,
    _STRING,
    _array_descriptor,
    _list_origin,
    _literal_int,
    _MethodSignature,
)


def _slice_step_is_one(bounds: ast.Slice) -> bool:
    if bounds.step is None:
        return True
    return _literal_int(bounds.step) == 1


class _CollectionsMixin(_CompilerBase):
    EXPR_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Subscript: "_subscript_load",
        ast.List: "_list_literal",
        ast.Tuple: "_tuple_literal",
        ast.Dict: "_dict_literal",
        ast.Set: "_set_literal",
    }
    TYPE_HANDLERS: ClassVar[dict[type[Any], str]] = {
        ast.Subscript: "_type_of_subscript",
        ast.List: "_type_of_list",
        ast.Tuple: "_type_of_tuple",
        ast.Dict: "_type_of_dict",
        ast.Set: "_type_of_set",
    }

    def _type_of_list(self, node: ast.List) -> str:
        return _array_descriptor(self._element_type(node))

    def _type_of_tuple(self, node: ast.Tuple) -> str:
        return _array_descriptor(self._element_type(node))

    def _type_of_dict(self, node: ast.Dict) -> str:
        self._dict_types(node)
        return _LINKED_HASH_MAP

    def _type_of_set(self, node: ast.Set) -> str:
        _ = self._element_type(node)
        return _HASH_SET

    def _subscript_load(self, node: ast.Subscript) -> str:
        if isinstance(node.slice, ast.Slice):
            return self._subscript_slice_load(node)
        sequence = self._expression(node.value)
        index = self._expression(node.slice)
        if index != "I":
            raise self.error(node.slice, "a subscript index must be int")
        if sequence == _STRING:
            self._instruction("invokevirtual java/lang/String/charAt(I)C")
            return "I"
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(node, "subscript needs an array, a list, or a string")
        if sequence.startswith("["):
            self._instruction(_ARRAY_LOAD.get(element, "aaload"))
            return element
        self._instruction(f"invokevirtual {_ARRAY_LIST_OWNER}/get(I)Ljava/lang/Object;")
        return self._restore_yield_type(element)

    def _subscript_store(self, target: ast.Subscript, value: ast.expr) -> None:
        if isinstance(target.slice, ast.Slice):
            raise self.error(target, "a slice is only supported as a for iterable")
        sequence = self._type_of(target.value)
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(target, "subscript assignment needs an array or a list")
        produced = self._expression(value)
        if produced != element and not self._compatible(produced, element):
            raise self.error(value, f"element type {produced} does not match {element}")
        held = self._hold_temp(produced)
        _ = self._expression(target.value)
        sequence_name = self._hold_temp(sequence)
        index = self._expression(target.slice)
        if index != "I":
            raise self.error(target.slice, "a subscript index must be int")
        index_name = self._hold_temp("I")
        self._load_local(sequence_name)
        self._load_local(index_name)
        self._load_local(held)
        if sequence.startswith("["):
            self._instruction(_ARRAY_STORE.get(element, "aastore"))
            return
        if not element.startswith(("L", "[")):
            self._box(element)
        self._instruction(
            f"invokevirtual {_ARRAY_LIST_OWNER}/set(ILjava/lang/Object;)Ljava/lang/Object;"
        )
        self._instruction("pop")

    def _type_of_subscript(self, node: ast.Subscript) -> str:
        if isinstance(node.slice, ast.Slice):
            return self._type_of_subscript_slice(node)
        sequence = self._type_of(node.value)
        if sequence == _STRING:
            return "I"
        element = self._sequence_element(sequence)
        if element is None:
            raise self.error(node, "subscript needs an array, a list, or a string")
        return element

    def _type_of_subscript_slice(self, node: ast.Subscript) -> str:
        sequence = self._type_of(node.value)
        if sequence == _STRING or sequence.startswith("arraylist:"):
            return sequence
        if sequence.startswith("["):
            element = sequence[1:]
            if element in _NEWARRAY or element.startswith(("L", "[")):
                return sequence
        raise self.error(node, "subscript needs an array, a list, or a string")

    def _subscript_slice_load(self, node: ast.Subscript) -> str:
        bounds = node.slice
        if not isinstance(bounds, ast.Slice):
            raise self.error(node, "subscript needs an array, a list, or a string")
        descriptor = self._type_of_subscript_slice(node)
        produced = self._expression(node.value)
        if produced != descriptor:
            raise self.error(node, "subscript needs an array, a list, or a string")
        sequence, _length, step, start, stop = self._bind_slice_locals(
            node, produced, bounds
        )
        if descriptor == _STRING:
            return self._emit_string_slice(node, bounds, sequence, start, stop, step)
        if descriptor.startswith("["):
            return self._emit_array_slice(
                node, bounds, descriptor, sequence, start, stop, step
            )
        return self._emit_list_slice(node, descriptor, sequence, start, stop, step)

    def _emit_string_slice(
        self,
        node: ast.Subscript,
        bounds: ast.Slice,
        sequence: str,
        start: str,
        stop: str,
        step: str,
    ) -> str:
        if _slice_step_is_one(bounds):
            self._emit_substring(node, sequence, start, stop)
            return _STRING
        self._emit_string_gather(node, sequence, start, stop, step)
        return _STRING

    def _emit_substring(
        self, node: ast.Subscript, sequence: str, start: str, stop: str
    ) -> None:
        empty = self._label("sliceempty")
        done = self._label("slicestr")
        self._load_local(start)
        self._load_local(stop)
        self._instruction(f"if_icmpgt {empty}")
        self._load_local(sequence)
        self._load_local(start)
        self._load_local(stop)
        signature = self._require_method(
            "java/lang/String", "substring", ("I", "I"), static=False, node=node
        )
        self._invoke(signature, "substring")
        self._instruction(f"goto {done}")
        self._instruction(f"{empty}:", indented=False)
        self._instruction('ldc ""')
        self._instruction(f"{done}:", indented=False)

    def _emit_string_gather(
        self,
        node: ast.Subscript,
        sequence: str,
        start: str,
        stop: str,
        step: str,
    ) -> None:
        self._emit_slice_count(start, stop, step)
        self._instruction("newarray char")
        chars = self._hold_temp("[C")
        self._walk_slice(
            node,
            start,
            stop,
            step,
            partial(self._emit_char_unit, sequence, chars),
        )
        self._instruction("new java/lang/String")
        self._instruction("dup")
        self._load_local(chars)
        self._instruction("invokespecial java/lang/String/<init>([C)V")

    def _emit_char_unit(
        self, sequence: str, chars: str, source: str, dest: str
    ) -> None:
        self._load_local(sequence)
        self._load_local(source)
        self._instruction("invokevirtual java/lang/String/charAt(I)C")
        self._instruction("i2c")
        held = self._hold_temp("I")
        self._load_local(chars)
        self._load_local(dest)
        self._load_local(held)
        self._instruction("castore")

    def _emit_array_slice(
        self,
        node: ast.Subscript,
        bounds: ast.Slice,
        descriptor: str,
        sequence: str,
        start: str,
        stop: str,
        step: str,
    ) -> str:
        if _slice_step_is_one(bounds):
            self._emit_array_copy(node, descriptor, sequence, start, stop)
            return descriptor
        self._emit_array_gather(node, descriptor, sequence, start, stop, step)
        return descriptor

    def _emit_array_copy(
        self,
        node: ast.Subscript,
        descriptor: str,
        sequence: str,
        start: str,
        stop: str,
    ) -> None:
        empty = self._label("sliceempty")
        done = self._label("slicecopy")
        self._load_local(start)
        self._load_local(stop)
        self._instruction(f"if_icmpgt {empty}")
        self._load_local(sequence)
        self._load_local(start)
        self._load_local(stop)
        self._emit_copy_of_range(node, descriptor)
        self._instruction(f"goto {done}")
        self._instruction(f"{empty}:", indented=False)
        self._emit_int(0)
        self._emit_blank_array(descriptor[1:])
        self._instruction(f"{done}:", indented=False)

    def _emit_copy_of_range(self, node: ast.Subscript, descriptor: str) -> None:
        element = descriptor[1:]
        if element in _NEWARRAY:
            signature = self._require_method(
                "java/util/Arrays",
                "copyOfRange",
                (descriptor, "I", "I"),
                static=True,
                node=node,
            )
            self._invoke(signature, "copyOfRange")
            return
        signature = self._require_method(
            "java/util/Arrays",
            "copyOfRange",
            (_OBJECT_ARRAY, "I", "I"),
            static=True,
            node=node,
        )
        self._invoke(signature, "copyOfRange")
        self._instruction(f"checkcast {descriptor}")

    def _emit_array_gather(
        self,
        node: ast.Subscript,
        descriptor: str,
        sequence: str,
        start: str,
        stop: str,
        step: str,
    ) -> None:
        element = descriptor[1:]
        self._emit_slice_count(start, stop, step)
        self._emit_blank_array(element)
        result = self._hold_temp(descriptor)
        self._walk_slice(
            node,
            start,
            stop,
            step,
            partial(self._emit_array_unit, sequence, descriptor, element, result),
        )
        self._load_local(result)

    def _emit_array_unit(
        self,
        sequence: str,
        descriptor: str,
        element: str,
        result: str,
        source: str,
        dest: str,
    ) -> None:
        self._emit_element(sequence, descriptor, source, element)
        held = self._hold_temp(element)
        self._load_local(result)
        self._load_local(dest)
        self._load_local(held)
        self._instruction(_ARRAY_STORE.get(element, "aastore"))

    def _emit_list_slice(
        self,
        node: ast.Subscript,
        descriptor: str,
        sequence: str,
        start: str,
        stop: str,
        step: str,
    ) -> str:
        element = descriptor.removeprefix("arraylist:")
        init = self._require_method(
            _ARRAY_LIST_OWNER, "<init>", (), static=False, node=node
        )
        self._instruction(f"new {_ARRAY_LIST_OWNER}")
        self._instruction("dup")
        self._instruction(f"invokespecial {_ARRAY_LIST_OWNER}/<init>{init.descriptor}")
        result = self._hold_temp(descriptor)
        add = self._require_method(
            _ARRAY_LIST_OWNER,
            "add",
            ("Ljava/lang/Object;",),
            static=False,
            node=node,
        )
        self._walk_slice(
            node,
            start,
            stop,
            step,
            partial(self._emit_list_unit, sequence, descriptor, element, result, add),
        )
        self._load_local(result)
        return descriptor

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
        self._load_local(result)
        self._emit_element(sequence, descriptor, source, element)
        self._box(element)
        self._invoke(add, "add")
        self._instruction("pop")

    def _emit_blank_array(self, element: str) -> None:
        kind = _NEWARRAY.get(element)
        if kind is not None:
            self._instruction(f"newarray {kind}")
            return
        internal = (
            element[1:-1]
            if element.startswith("L") and element.endswith(";")
            else element
        )
        self._instruction(f"anewarray {internal}")

    def _emit_slice_count(self, start: str, stop: str, step: str) -> None:
        """Push the number of elements a gather slice stores."""
        negative = self._label("sliceneg")
        zero = self._label("slicezero")
        done = self._label("slicelen")
        self._load_local(step)
        self._instruction(f"ifle {negative}")
        self._load_local(start)
        self._load_local(stop)
        self._instruction(f"if_icmpge {zero}")
        self._emit_forward_count(start, stop, step)
        self._instruction(f"goto {done}")
        self._instruction(f"{negative}:", indented=False)
        self._load_local(start)
        self._load_local(stop)
        self._instruction(f"if_icmple {zero}")
        self._emit_backward_count(start, stop, step)
        self._instruction(f"goto {done}")
        self._instruction(f"{zero}:", indented=False)
        self._emit_int(0)
        self._instruction(f"{done}:", indented=False)

    def _emit_forward_count(self, start: str, stop: str, step: str) -> None:
        self._load_local(stop)
        self._load_local(start)
        self._instruction("isub")
        self._emit_int(1)
        self._instruction("isub")
        self._load_local(step)
        self._instruction("idiv")
        self._emit_int(1)
        self._instruction("iadd")

    def _emit_backward_count(self, start: str, stop: str, step: str) -> None:
        self._load_local(start)
        self._load_local(stop)
        self._instruction("isub")
        self._emit_int(1)
        self._instruction("isub")
        self._load_local(step)
        self._instruction("ineg")
        self._instruction("idiv")
        self._emit_int(1)
        self._instruction("iadd")

    def _walk_slice(
        self,
        origin: ast.AST,
        start: str,
        stop: str,
        step: str,
        emit_body: Callable[[str, str], None],
    ) -> None:
        source = self._cursor_name("idx")
        dest = self._cursor_name("dst")
        test_label = self._label("slice")
        down_label = self._label("slicedown")
        body_label = self._label("slicebody")
        end_label = self._label("sliceend")
        self._load_local(start)
        self._keep(source, "I", origin)
        self._emit_int(0)
        self._keep(dest, "I", origin)
        self._instruction(f"{test_label}:", indented=False)
        self._load_local(step)
        self._instruction(f"ifle {down_label}")
        self._load_local(source)
        self._load_local(stop)
        self._instruction(f"if_icmpge {end_label}")
        self._instruction(f"goto {body_label}")
        self._instruction(f"{down_label}:", indented=False)
        self._load_local(source)
        self._load_local(stop)
        self._instruction(f"if_icmple {end_label}")
        self._instruction(f"{body_label}:", indented=False)
        emit_body(source, dest)
        self._load_local(source)
        self._load_local(step)
        self._instruction("iadd")
        self._keep(source, "I", origin)
        self._load_local(dest)
        self._emit_int(1)
        self._instruction("iadd")
        self._keep(dest, "I", origin)
        self._instruction(f"goto {test_label}")
        self._instruction(f"{end_label}:", indented=False)

    def _tuple_literal(self, node: ast.Tuple) -> str:
        return self._java_array(node, self._element_type(node))

    def _list_literal(self, node: ast.List) -> str:
        element = self._element_type(node)
        sink = self.list_sink
        self.list_sink = None
        try:
            if id(node) in self.field_list_ids or (
                sink is not None and sink in self.mutable_lists
            ):
                return self._array_list(node, element)
            return self._java_array(node, element)
        finally:
            self.list_sink = sink

    def _element_type(self, node: ast.List | ast.Tuple | ast.Set) -> str:
        if isinstance(node, ast.List):
            kind = "list"
        elif isinstance(node, ast.Tuple):
            kind = "tuple"
        else:
            kind = "set"
        if not node.elts:
            raise self.error(node, f"an empty {kind} has no element type")
        element: str | None = None
        for item in node.elts:
            if isinstance(item, ast.Starred):
                raise self.error(item, f"{kind} unpacking is unsupported")
            current = self._type_of(item)
            if current in {"null", "lambda"}:
                raise self.error(item, f"a {kind} element needs a value type")
            if element is None:
                element = current
            elif current != element:
                raise self.error(node, f"a {kind} needs one element type")
        if element is None:
            raise self.error(node, f"a {kind} has no element type")
        return element

    def _java_array(self, node: ast.List | ast.Tuple, element: str) -> str:
        count = ast.Constant(value=len(node.elts))
        ast.copy_location(count, node)
        _ = self._constant(count)
        kind = _NEWARRAY.get(element)
        if kind is None:
            internal = (
                element[1:-1]
                if element.startswith("L") and element.endswith(";")
                else element
            )
            self._instruction(f"anewarray {internal}")
        else:
            self._instruction(f"newarray {kind}")
        store = _ARRAY_STORE.get(element, "aastore")
        for index, item in enumerate(node.elts):
            self._instruction("dup")
            position = ast.Constant(value=index)
            ast.copy_location(position, node)
            _ = self._constant(position)
            produced = self._expression(item)
            if produced != element:
                raise self.error(
                    item, f"list element has type {produced}, expected {element}"
                )
            self._instruction(store)
        return _array_descriptor(element)

    def _array_list(self, node: ast.List, element: str) -> str:
        self._fill_collection(node, _ARRAY_LIST_OWNER, element, node.elts, "list")
        return f"arraylist:{element}"

    def _fill_collection(
        self,
        node: ast.AST,
        owner: str,
        element: str,
        items: list[ast.expr],
        kind: str,
    ) -> None:
        init = self._require_method(owner, "<init>", (), static=False, node=node)
        self._instruction(f"new {owner}")
        self._instruction("dup")
        self._instruction(f"invokespecial {owner}/<init>{init.descriptor}")
        add = self._require_method(
            owner, "add", ("Ljava/lang/Object;",), static=False, node=node
        )
        for item in items:
            self._instruction("dup")
            produced = self._expression(item)
            if produced != element:
                raise self.error(
                    item, f"{kind} element has type {produced}, expected {element}"
                )
            self._box(produced)
            self._invoke(add, "add")
            self._instruction("pop")

    def _dict_types(self, node: ast.Dict) -> tuple[str, str]:
        if not node.keys:
            raise self.error(node, "an empty dict has no key or value type")
        key_type: str | None = None
        value_type: str | None = None
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise self.error(node, "dict unpacking is unsupported")
            current_key = self._type_of(key)
            current_value = self._type_of(value)
            if current_key in {"null", "lambda"} or current_value in {"null", "lambda"}:
                raise self.error(node, "a dict key and value need value types")
            if key_type is None:
                key_type = current_key
                value_type = current_value
            elif current_key != key_type or current_value != value_type:
                raise self.error(node, "a dict needs one key type and one value type")
        if key_type is None or value_type is None:
            raise self.error(node, "a dict has no key or value type")
        return key_type, value_type

    def _dict_literal(self, node: ast.Dict) -> str:
        key_type, value_type = self._dict_types(node)
        init = self._require_method(
            _LINKED_HASH_MAP_OWNER, "<init>", (), static=False, node=node
        )
        self._instruction(f"new {_LINKED_HASH_MAP_OWNER}")
        self._instruction("dup")
        self._instruction(
            f"invokespecial {_LINKED_HASH_MAP_OWNER}/<init>{init.descriptor}"
        )
        put = self._require_method(
            _LINKED_HASH_MAP_OWNER,
            "put",
            ("Ljava/lang/Object;", "Ljava/lang/Object;"),
            static=False,
            node=node,
        )
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise self.error(node, "dict unpacking is unsupported")
            self._instruction("dup")
            produced_key = self._expression(key)
            if produced_key != key_type:
                raise self.error(
                    key, f"dict key has type {produced_key}, expected {key_type}"
                )
            self._box(produced_key)
            produced_value = self._expression(value)
            if produced_value != value_type:
                raise self.error(
                    value,
                    f"dict value has type {produced_value}, expected {value_type}",
                )
            self._box(produced_value)
            self._invoke(put, "put")
            self._instruction("pop")
        return _LINKED_HASH_MAP

    def _set_literal(self, node: ast.Set) -> str:
        element = self._element_type(node)
        self._fill_collection(node, _HASH_SET_OWNER, element, node.elts, "set")
        return _HASH_SET

    def _list_append(self, node: ast.Call) -> str:
        if node.keywords or len(node.args) != 1:
            raise self.error(node, "append takes one positional argument")
        value = node.func
        if not isinstance(value, ast.Attribute):
            raise self.error(node, "append needs a stored list")
        if not isinstance(value.value, ast.Name):
            raise self.error(node, "append needs a stored list")
        local = self.locals.get(value.value.id)
        if local is None:
            raise self.error(node, f"unknown local {value.value.id!r}")
        element = self.list_elements.get(value.value.id)
        if element is None:
            raise self.error(node, "this list is an array and cannot be changed")
        self._instruction(f"aload {local[0]}")
        produced = self._expression(node.args[0])
        if produced != element:
            raise self.error(
                node.args[0], f"append has type {produced}, expected {element}"
            )
        self._box(produced)
        add = self._require_method(
            _ARRAY_LIST_OWNER, "add", ("Ljava/lang/Object;",), static=False, node=node
        )
        self._invoke(add, "add")
        self._instruction("pop")
        return "V"

    def _emit_to_array(self, element: str) -> None:
        self._instruction("dup")
        size = self._require_method(_ARRAY_LIST_OWNER, "size", (), static=False)
        self._invoke(size, "size")
        internal = (
            element[1:-1]
            if element.startswith("L") and element.endswith(";")
            else element
        )
        self._instruction(f"anewarray {internal}")
        signature = self._require_method(
            _ARRAY_LIST_OWNER,
            "toArray",
            (_OBJECT_ARRAY,),
            static=False,
        )
        self._invoke(signature, "toArray")
        self._instruction(f"checkcast {_array_descriptor(element)}")

    def _classify_lists(self, statements: list[ast.stmt]) -> None:
        origins: dict[str, str] = {}
        literals: dict[str, ast.List] = {}
        field_names: set[str] = set()
        field_lists: set[int] = set()
        appends: set[str] = set()
        captured: set[str] = set()
        escapes: list[tuple[ast.Call, str]] = []
        self._note_list_assignments(
            statements, origins, literals, field_names, field_lists
        )
        self._note_list_uses(statements, origins, appends, captured, escapes)
        mutable: set[str] = set()
        for name in appends | field_names | captured:
            if name in origins:
                mutable.add(_list_origin(name, origins))
        changed = True
        while changed:
            changed = False
            for call, arg_name in escapes:
                root = _list_origin(arg_name, origins)
                if root in mutable or root not in literals:
                    continue
                if not self._accepts_list_array(
                    call, arg_name, origins, literals, mutable
                ):
                    mutable.add(root)
                    changed = True
        self.mutable_lists = {
            name for name in origins if _list_origin(name, origins) in mutable
        }
        self.field_list_ids = field_lists

    def _note_list_assignments(
        self,
        body: list[ast.stmt],
        origins: dict[str, str],
        literals: dict[str, ast.List],
        field_names: set[str],
        field_lists: set[int],
    ) -> None:
        for statement in body:
            if isinstance(statement, ast.If | ast.While | ast.For):
                self._note_list_assignments(
                    statement.body, origins, literals, field_names, field_lists
                )
                self._note_list_assignments(
                    statement.orelse, origins, literals, field_names, field_lists
                )
                continue
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target
                value = statement.value
            if target is None or value is None:
                continue
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                if isinstance(value, ast.Name):
                    field_names.add(value.id)
                elif isinstance(value, ast.List):
                    field_lists.add(id(value))
            if isinstance(target, ast.Name) and isinstance(value, ast.List):
                origins[target.id] = target.id
                literals[target.id] = value
            elif (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Name)
                and value.id in origins
            ):
                origins[target.id] = _list_origin(value.id, origins)

    def _note_list_uses(
        self,
        statements: list[ast.stmt],
        origins: dict[str, str],
        appends: set[str],
        captured: set[str],
        escapes: list[tuple[ast.Call, str]],
    ) -> None:
        for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
            if isinstance(node, ast.Lambda):
                bound = {arg.arg for arg in node.args.args}
                for inner in ast.walk(node.body):
                    if (
                        isinstance(inner, ast.Name)
                        and isinstance(inner.ctx, ast.Load)
                        and inner.id not in bound
                    ):
                        captured.add(inner.id)
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if isinstance(node.func.value, ast.Name) and node.func.attr == "append":
                appends.add(node.func.value.id)
                continue
            escapes.extend(
                (node, argument.id)
                for argument in node.args
                if isinstance(argument, ast.Name) and argument.id in origins
            )

    def _accepts_list_array(
        self,
        call: ast.Call,
        arg_name: str,
        origins: dict[str, str],
        literals: dict[str, ast.List],
        mutable: set[str],
    ) -> bool:
        def origin_of(name: str) -> str:
            seen: set[str] = set()
            while name in origins and origins[name] != name and name not in seen:
                seen.add(name)
                name = origins[name]
            return name

        types: list[str] = []
        index = -1
        for position, argument in enumerate(call.args):
            if isinstance(argument, ast.Name) and argument.id == arg_name:
                index = position
            if isinstance(argument, ast.Name) and argument.id in origins:
                root = origin_of(argument.id)
                literal = literals.get(root)
                element = (
                    self._preview_list_element(literal) if literal is not None else None
                )
                if root in mutable or element is None:
                    types.append(_ARRAY_LIST)
                else:
                    types.append(_array_descriptor(element))
                continue
            preview = self._preview_type(argument)
            if preview is None:
                return False
            types.append(preview)
        if index < 0:
            return False
        signature = self._signature_for_call(call, tuple(types))
        element = self._preview_list_element(literals.get(origin_of(arg_name)))
        if signature is None or element is None or index >= len(signature.parameters):
            return False
        return signature.parameters[index] == _array_descriptor(element)

    def _preview_list_element(self, node: ast.List | None) -> str | None:
        if node is None or not node.elts:
            return None
        element: str | None = None
        for item in node.elts:
            current = self._preview_type(item)
            if current is None:
                return None
            if element is None:
                element = current
            elif current != element:
                return None
        return element

    def _preview_type(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant):
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
        if isinstance(node, ast.Name):
            return self.preview_locals.get(node.id)
        if isinstance(node, ast.List):
            element = self._preview_list_element(node)
            if element is None:
                return None
            return _array_descriptor(element)
        if isinstance(node, ast.Dict):
            return _LINKED_HASH_MAP
        if isinstance(node, ast.Set):
            return _HASH_SET
        return None
