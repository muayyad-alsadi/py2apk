"""Parse JVM class files into the members the Jasmin compiler needs.

The reader uses only ``struct``. It walks the constant pool and member tables.
Method bodies stay undecoded. Parameter names are read from
``MethodParameters`` or from ``Code`` / ``LocalVariableTable``.
"""

from __future__ import annotations

import builtins
import struct
from dataclasses import dataclass

ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_INTERFACE = 0x0200
ACC_SYNTHETIC = 0x1000


@dataclass(frozen=True)
class ClassMember:
    owner: str
    name: str
    descriptor: str
    is_static: bool
    opcode: str = ""
    param_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedClass:
    internal: str
    super_internal: str | None
    is_interface: bool
    interfaces: tuple[str, ...]
    methods: tuple[ClassMember, ...]
    fields: tuple[ClassMember, ...]


class ClassFileError(ValueError):
    """Raised when a class file is not readable."""


class _Reader:
    def __init__(self, data: builtins.bytes) -> None:
        self.data = data
        self.offset = 0

    def u1(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u2(self) -> int:
        value = struct.unpack_from(">H", self.data, self.offset)[0]
        self.offset += 2
        return value

    def u4(self) -> int:
        value = struct.unpack_from(">I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def skip(self, count: int) -> None:
        self.offset += count

    def read(self, count: int) -> builtins.bytes:
        chunk = self.data[self.offset : self.offset + count]
        self.offset += count
        return chunk


def parameter_descriptors(params: str) -> tuple[str, ...]:
    index = 0
    found: list[str] = []
    while index < len(params):
        start = index
        index = _skip_field(params, index)
        found.append(params[start:index])
    return tuple(found)


def split_method_descriptor(descriptor: str) -> tuple[str, str]:
    """Return the concatenated parameter descriptors and the result descriptor."""
    if not descriptor.startswith("(") or ")" not in descriptor:
        raise ClassFileError(f"bad method descriptor: {descriptor}")
    index = 1
    while descriptor[index] != ")":
        index = _skip_field(descriptor, index)
    return descriptor[1:index], descriptor[index + 1 :]


def parse_class(data: bytes) -> ParsedClass:
    reader = _Reader(data)
    if len(data) < 8 or reader.u4() != 0xCAFEBABE:
        raise ClassFileError("class file does not start with 0xCAFEBABE")
    _ = reader.u2()
    _ = reader.u2()
    pool = _constant_pool(reader)
    access = reader.u2()
    internal = _class_name(pool, reader.u2())
    super_index = reader.u2()
    super_internal = _class_name(pool, super_index) if super_index else None
    interfaces = tuple(_class_name(pool, reader.u2()) for _ in range(reader.u2()))
    is_interface = bool(access & ACC_INTERFACE)
    fields = tuple(_members(reader, pool, internal, is_interface, methods=False))
    methods = tuple(_members(reader, pool, internal, is_interface, methods=True))
    return ParsedClass(internal, super_internal, is_interface, interfaces, methods, fields)


def _constant_pool(reader: _Reader) -> list[object]:
    count = reader.u2()
    pool: list[object] = [None] * count
    index = 1
    while index < count:
        tag = reader.u1()
        if tag == 1:
            pool[index] = reader.read(reader.u2()).decode("utf-8", "surrogateescape")
        elif tag in {7, 8, 16, 19, 20}:
            pool[index] = ("class", reader.u2()) if tag == 7 else reader.u2()
        elif tag in {3, 4, 9, 10, 11, 12, 17, 18}:
            reader.skip(4)
        elif tag in {5, 6}:
            reader.skip(8)
            index += 1
        elif tag == 15:
            reader.skip(3)
        else:
            raise ClassFileError(f"unknown constant-pool tag {tag}")
        index += 1
    return pool


def _class_name(pool: list[object], index: int) -> str:
    entry = pool[index]
    if not isinstance(entry, tuple) or entry[0] != "class":
        raise ClassFileError(f"constant pool {index} is not a class")
    name = pool[entry[1]]
    if not isinstance(name, str):
        raise ClassFileError(f"class name {entry[1]} is not Utf8")
    return name


def _utf8(pool: list[object], index: int) -> str:
    value = pool[index]
    if not isinstance(value, str):
        raise ClassFileError(f"constant pool {index} is not Utf8")
    return value


def _members(
    reader: _Reader,
    pool: list[object],
    owner: str,
    is_interface: bool,
    *,
    methods: bool,
) -> list[ClassMember]:
    found: list[ClassMember] = []
    for _ in range(reader.u2()):
        access = reader.u2()
        name = _utf8(pool, reader.u2())
        descriptor = _utf8(pool, reader.u2())
        is_static = bool(access & ACC_STATIC)
        attribute_count = reader.u2()
        param_names = (
            _method_param_names(reader, pool, descriptor, is_static, attribute_count)
            if methods
            else _skip_attributes(reader, attribute_count)
        )
        visible = bool(access & (ACC_PUBLIC | ACC_PROTECTED))
        if not visible or access & ACC_SYNTHETIC or access & ACC_PRIVATE:
            continue
        opcode = ""
        if methods:
            if is_static:
                opcode = "invokestatic"
            elif name == "<init>":
                opcode = "invokespecial"
            elif is_interface:
                opcode = "invokeinterface"
            else:
                opcode = "invokevirtual"
        found.append(
            ClassMember(owner, name, descriptor, is_static, opcode, param_names)
        )
    return found


def _skip_attributes(reader: _Reader, count: int) -> tuple[str, ...]:
    for _attribute in range(count):
        _ = reader.u2()
        reader.skip(reader.u4())
    return ()


def _method_param_names(
    reader: _Reader,
    pool: list[object],
    descriptor: str,
    is_static: bool,
    count: int,
) -> tuple[str, ...]:
    from_parameters: tuple[str, ...] | None = None
    from_locals: tuple[str, ...] | None = None
    for _attribute in range(count):
        name = _utf8(pool, reader.u2())
        length = reader.u4()
        start = reader.offset
        if name == "MethodParameters":
            from_parameters = _read_method_parameters(reader, pool)
        elif name == "Code":
            from_locals = _read_code_parameter_names(reader, pool, descriptor, is_static)
        end = start + length
        if reader.offset > end:
            raise ClassFileError(f"{name} overruns its attribute")
        reader.offset = end
    expected = len(parameter_descriptors(split_method_descriptor(descriptor)[0]))
    if from_parameters is not None and len(from_parameters) == expected:
        return from_parameters
    if from_locals is not None:
        return from_locals
    return ()


def _read_method_parameters(reader: _Reader, pool: list[object]) -> tuple[str, ...]:
    names: list[str] = []
    for _parameter in range(reader.u1()):
        name_index = reader.u2()
        _ = reader.u2()
        names.append("" if name_index == 0 else _utf8(pool, name_index))
    return tuple(names)


def _read_code_parameter_names(
    reader: _Reader,
    pool: list[object],
    descriptor: str,
    is_static: bool,
) -> tuple[str, ...]:
    _ = reader.u2()
    _ = reader.u2()
    reader.skip(reader.u4())
    reader.skip(reader.u2() * 8)
    slot_names: dict[int, str] = {}
    for _attribute in range(reader.u2()):
        name = _utf8(pool, reader.u2())
        length = reader.u4()
        start = reader.offset
        if name == "LocalVariableTable":
            _read_local_variable_table(reader, pool, slot_names)
        reader.offset = start + length
    return _parameter_names_at_slots(descriptor, is_static, slot_names)


def _read_local_variable_table(
    reader: _Reader,
    pool: list[object],
    slot_names: dict[int, str],
) -> None:
    for _entry in range(reader.u2()):
        start_pc = reader.u2()
        _ = reader.u2()
        name_index = reader.u2()
        _ = reader.u2()
        slot = reader.u2()
        if start_pc == 0 and slot not in slot_names:
            slot_names[slot] = _utf8(pool, name_index)


def _parameter_names_at_slots(
    descriptor: str,
    is_static: bool,
    slot_names: dict[int, str],
) -> tuple[str, ...]:
    parts = parameter_descriptors(split_method_descriptor(descriptor)[0])
    slot = 0 if is_static else 1
    names: list[str] = []
    for part in parts:
        name = slot_names.get(slot)
        if name is None:
            return ()
        names.append(name)
        slot += 2 if part in {"J", "D"} else 1
    return tuple(names)


def _skip_field(descriptor: str, index: int) -> int:
    while descriptor[index] == "[":
        index += 1
    if descriptor[index] == "L":
        end = descriptor.index(";", index)
        return end + 1
    return index + 1
