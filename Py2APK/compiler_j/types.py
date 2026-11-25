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

# Python math name -> (Java name, arity, force double signature).
_MATH_METHOD_ALIASES: dict[str, tuple[str, int, bool]] = {
    "sqrt": ("sqrt", 1, False),
    "sin": ("sin", 1, False),
    "cos": ("cos", 1, False),
    "tan": ("tan", 1, False),
    "asin": ("asin", 1, False),
    "acos": ("acos", 1, False),
    "atan": ("atan", 1, False),
    "exp": ("exp", 1, False),
    "log": ("log", 1, False),
    "log10": ("log10", 1, False),
    "floor": ("floor", 1, False),
    "ceil": ("ceil", 1, False),
    "atan2": ("atan2", 2, False),
    "hypot": ("hypot", 2, False),
    "pow": ("pow", 2, False),
    "radians": ("toRadians", 1, False),
    "degrees": ("toDegrees", 1, False),
    "fabs": ("abs", 1, True),
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


def math_method(name: str) -> tuple[str, int, bool] | None:
    """Return ``(java_name, arity, force_double)`` for one math name."""
    return _MATH_METHOD_ALIASES.get(name)


def _java_method_name(owner: str, name: str) -> str:
    if owner == "java/lang/String":
        return _STRING_METHOD_ALIASES.get(name, name)
    if owner == "java/lang/Math":
        row = _MATH_METHOD_ALIASES.get(name)
        if row is None:
            return name
        return row[0]
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

_PY2APK_CALLABLE = "py2apk/Callable"

_FN_PREFIX = "py2apk$fn$"

_DESCRIPTOR_PRIMITIVES = frozenset("ZIFDJVBCS")

_ESCAPE = str.maketrans({"_": "_u", "$": "_d", "/": "_s"})

_UNESCAPE = {"u": "_", "d": "$", "s": "/"}


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


def _mangle_descriptor(descriptor: str) -> str:
    """Encode one JVM descriptor so it can sit inside a class name."""
    if descriptor in _DESCRIPTOR_PRIMITIVES:
        return descriptor
    if descriptor.startswith("["):
        return "A" + _mangle_descriptor(descriptor[1:])
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return f"L{descriptor[1:-1].translate(_ESCAPE)}_e"
    raise JasminCompilerError(f"cannot encode descriptor {descriptor}")


def _unmangle_descriptor(encoded: str) -> str | None:
    """Invert :func:`_mangle_descriptor`, or return None when it does not."""
    if encoded in _DESCRIPTOR_PRIMITIVES:
        return encoded
    if encoded.startswith("A"):
        rest = _unmangle_descriptor(encoded[1:])
        if rest is None:
            return None
        return f"[{rest}"
    if not (encoded.startswith("L") and encoded.endswith("_e")):
        return None
    internal = _unescape_internal(encoded[1:-2])
    if internal is None:
        return None
    return f"L{internal};"


def _unescape_internal(encoded: str) -> str | None:
    pieces: list[str] = []
    index = 0
    while index < len(encoded):
        if encoded[index] != "_":
            pieces.append(encoded[index])
            index += 1
            continue
        if index + 1 >= len(encoded):
            return None
        decoded = _UNESCAPE.get(encoded[index + 1])
        if decoded is None:
            return None
        pieces.append(decoded)
        index += 2
    return "".join(pieces)


def _function_internal(parameters: tuple[str, ...], result: str) -> str:
    """Return the internal name of the canonical function base class."""
    encoded = "_p_".join(_mangle_descriptor(item) for item in parameters)
    return f"{_FN_PREFIX}{encoded}_r_{_mangle_descriptor(result)}"


def _function_type(parameters: tuple[str, ...], result: str) -> str:
    """Return the JVM descriptor of one canonical function base class."""
    return _descriptor(_function_internal(parameters, result))


def _parse_function_type(
    descriptor: str,
) -> tuple[str, tuple[str, ...], str] | None:
    """Return ``(internal, parameters, result)`` for a function descriptor."""
    if not (descriptor.startswith("L") and descriptor.endswith(";")):
        return None
    internal = descriptor[1:-1]
    if not internal.startswith(_FN_PREFIX):
        return None
    body = internal[len(_FN_PREFIX) :]
    marker = body.find("_r_")
    if marker < 0 or "_r_" in body[marker + 3 :]:
        return None
    result = _unmangle_descriptor(body[marker + 3 :])
    if result is None:
        return None
    encoded = body[:marker]
    if not encoded:
        return internal, (), result
    parameters: list[str] = []
    for part in encoded.split("_p_"):
        item = _unmangle_descriptor(part)
        if item is None:
            return None
        parameters.append(item)
    return internal, tuple(parameters), result


def _is_function_type(descriptor: str) -> bool:
    """Return whether ``descriptor`` names a canonical function base class."""
    return _parse_function_type(descriptor) is not None


def _callable_parts(
    node: ast.expr, imports: dict[str, str]
) -> tuple[list[ast.expr], ast.expr] | None:
    """Return ``Callable[[...], R]`` parts, or None when ``node`` is not one."""
    if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
        return None
    if imports.get(node.value.id) != _PY2APK_CALLABLE:
        return None
    sliced = node.slice
    if not isinstance(sliced, ast.Tuple) or len(sliced.elts) != 2:
        raise JasminCompilerError(
            "Callable[...] needs a parameter list and a result"
        )
    parameters, result = sliced.elts
    if isinstance(parameters, ast.Constant) and parameters.value is Ellipsis:
        raise JasminCompilerError("Callable[..., R] is not supported")
    if not isinstance(parameters, ast.List):
        raise JasminCompilerError(
            "Callable parameters must be a list, as in Callable[[int], int]"
        )
    return list(parameters.elts), result


def _list_origin(name: str, origins: dict[str, str]) -> str:
    seen: set[str] = set()
    while name in origins and origins[name] != name and name not in seen:
        seen.add(name)
        name = origins[name]
    return name
