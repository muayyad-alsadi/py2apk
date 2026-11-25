"""Compiler errors, JVM descriptors, and pure helpers."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


class JasminCompilerError(Exception):
    """Raised when source cannot be represented by the supported JVM subset."""


@dataclass(frozen=True)
class _MethodSignature:
    parameters: tuple[str, ...]
    result: str
    opcode: str = "invokevirtual"
    owner: str = ""
    parameter_names: tuple[str, ...] = ()

    @property
    def descriptor(self) -> str:
        return f"({''.join(self.parameters)}){self.result}"


@dataclass
class LocalType:
    internal: str
    base: str | None
    constructor: tuple[str, ...]
    methods: dict[str, _MethodSignature] = field(default_factory=dict)
    # Synthetic ``Class$gen$N`` internal name -> yielded JVM element type.
    generators: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    constructor_names: tuple[str, ...] = ()


_STRING = "Ljava/lang/String;"

_BUILTIN_TYPES = {
    "int": "I",
    "bool": "Z",
    "float": "F",
    "double": "D",
    "str": _STRING,
    "None": "V",
}

_STRING_BUILDER = "java/lang/StringBuilder"

_OBJECT_ARRAY = "[Ljava/lang/Object;"

_STRING_ARRAY = "[Ljava/lang/String;"

_ARRAY_LIST = "Ljava/util/ArrayList;"

_LINKED_HASH_MAP = "Ljava/util/LinkedHashMap;"

_HASH_SET = "Ljava/util/HashSet;"

_ARRAY_LIST_OWNER = "java/util/ArrayList"

_LINKED_HASH_MAP_OWNER = "java/util/LinkedHashMap"

_HASH_SET_OWNER = "java/util/HashSet"

_NEWARRAY = {"Z": "boolean", "I": "int", "F": "float", "D": "double", "J": "long"}

_ARRAY_STORE = {
    "Z": "bastore",
    "I": "iastore",
    "F": "fastore",
    "D": "dastore",
    "J": "lastore",
}

_ARRAY_LOAD = {
    "Z": "baload",
    "I": "iaload",
    "F": "faload",
    "D": "daload",
    "J": "laload",
}

_STRING_METHOD_ALIASES = {
    "upper": "toUpperCase",
    "lower": "toLowerCase",
    "strip": "trim",
    "startswith": "startsWith",
    "endswith": "endsWith",
    "find": "indexOf",
    "replace": "replace",
}

_BOX_METHODS = {
    "I": ("java/lang/Integer", "valueOf"),
    "Z": ("java/lang/Boolean", "valueOf"),
    "F": ("java/lang/Float", "valueOf"),
    "D": ("java/lang/Double", "valueOf"),
    "J": ("java/lang/Long", "valueOf"),
}

_UNBOX_METHODS = {
    "I": ("java/lang/Integer", "intValue", "()I"),
    "Z": ("java/lang/Boolean", "booleanValue", "()Z"),
    "F": ("java/lang/Float", "floatValue", "()F"),
    "D": ("java/lang/Double", "doubleValue", "()D"),
    "J": ("java/lang/Long", "longValue", "()J"),
}

_NUMERIC_CASTS = {
    ("I", "I"): "",
    ("Z", "I"): "",
    ("F", "I"): "f2i",
    ("D", "I"): "d2i",
    ("J", "I"): "l2i",
    ("I", "F"): "i2f",
    ("Z", "F"): "i2f",
    ("F", "F"): "",
    ("D", "F"): "d2f",
    ("J", "F"): "l2f",
    ("I", "D"): "i2d",
    ("Z", "D"): "i2d",
    ("F", "D"): "f2d",
    ("D", "D"): "",
    ("J", "D"): "l2d",
}

_PARSE_METHODS = {
    "int": ("java/lang/Integer", "parseInt", "I"),
    "float": ("java/lang/Float", "parseFloat", "F"),
    "double": ("java/lang/Double", "parseDouble", "D"),
}


def _java_method_name(owner: str, name: str) -> str:
    if owner == "java/lang/String":
        return _STRING_METHOD_ALIASES.get(name, name)
    return name


def _return_opcode(descriptor: str) -> str:
    if descriptor in {"I", "Z"}:
        return "ireturn"
    if descriptor == "F":
        return "freturn"
    if descriptor == "D":
        return "dreturn"
    if descriptor == "J":
        return "lreturn"
    if descriptor == "V":
        return "return"
    return "areturn"


def _immediate_return(descriptor: str) -> tuple[str, ...]:
    if descriptor == "V":
        return ("    return",)
    if descriptor in {"I", "Z", "B", "C", "S"}:
        return ("    iconst_0", "    ireturn")
    if descriptor == "J":
        return ("    lconst_0", "    lreturn")
    if descriptor == "F":
        return ("    fconst_0", "    freturn")
    if descriptor == "D":
        return ("    dconst_0", "    dreturn")
    return ("    aconst_null", "    areturn")


_WIDEN = {
    ("I", "J"): "i2l",
    ("I", "F"): "i2f",
    ("I", "D"): "i2d",
    ("J", "D"): "l2d",
    ("F", "D"): "f2d",
}


def _numeric(descriptor: str) -> bool:
    return descriptor in {"I", "Z", "J", "F", "D"}


def _reference(descriptor: str) -> bool:
    return descriptor == "null" or descriptor.startswith(("L", "["))


def _equality_width(left: str, right: str) -> str:
    kinds = {"I" if item in {"I", "Z"} else item for item in (left, right)}
    if "D" in kinds or kinds == {"J", "F"}:
        return "D"
    if "F" in kinds:
        return "F"
    if "J" in kinds:
        return "J"
    return "I"


def _literal_int(node: ast.expr) -> int | None:
    """Fold a constant int, including ``-n`` and ``+n``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return int(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal_int(node.operand)
        if inner is None:
            return None
        return -inner
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _literal_int(node.operand)
    return None


_JAVA_ITERATOR = "java/util/Iterator"


def _format_pattern(spec: str) -> str | None:
    """Return a Java format pattern for one supported Python spec."""
    if spec == "s":
        return "%s"
    if re.fullmatch(r"(?:0\d+|\d+)?d", spec):
        return "%" + spec
    if re.fullmatch(r"(?:0\d+|\d+)?\.\d+f", spec):
        return "%" + spec
    return None


def _listed_element(node: ast.expr) -> ast.expr | None:
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "list"
    ):
        return node.slice
    return None


def _static_matches(signature: _MethodSignature, static: bool) -> bool:
    return (signature.opcode == "invokestatic") == static


def _descriptor(internal_name: str) -> str:
    return f"L{internal_name};"


def _list_element(descriptor: str) -> str | None:
    prefix = "arraylist:"
    if descriptor.startswith(prefix):
        return descriptor[len(prefix) :]
    return None


def _jvm_descriptor(descriptor: str) -> str:
    if _list_element(descriptor) is not None:
        return _ARRAY_LIST
    return descriptor


def _array_descriptor(element: str) -> str:
    return f"[{element}"


def _internal_name(descriptor: str) -> str:
    if not (descriptor.startswith("L") and descriptor.endswith(";")):
        raise ValueError(f"not a reference descriptor: {descriptor}")
    return descriptor[1:-1]


def _package_for(source: Path, output: Path) -> str:
    """Infer the JVM package from the conventional src/ or .py2apk/j layout."""
    source_parts = source.resolve().parts
    src_positions = [index for index, part in enumerate(source_parts) if part == "src"]
    if src_positions:
        return "/".join(source_parts[src_positions[-1] + 1 : -1])

    output_parts = output.resolve().parts
    j_positions = [index for index, part in enumerate(output_parts) if part == "j"]
    if j_positions:
        return "/".join(output_parts[j_positions[-1] + 1 : -1])

    return ""


def _project_root(source: Path) -> Path:
    parts = source.resolve().parts
    if "src" in parts:
        index = max(position for position, part in enumerate(parts) if part == "src")
        return Path(*parts[:index])
    return source.resolve().parent


def _list_origin(name: str, origins: dict[str, str]) -> str:
    seen: set[str] = set()
    while name in origins and origins[name] != name and name not in seen:
        seen.add(name)
        name = origins[name]
    return name
