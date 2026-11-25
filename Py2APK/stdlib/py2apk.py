"""Helpers the compiler calls. This is not a CPython module.

``slice_start`` and ``slice_stop`` are ``slice.indices``. ``present`` is
``0`` when the bound was missing. ``chr`` is a stub: the compiler replaces
that method with the one-character ``String`` sequence. ``Error`` is the
throwable ``raise`` builds. ``cause`` starts null. ``raise Error() from other``
is the only writer.
"""

from collections.abc import Iterator

from java.lang import Exception, StringBuilder


class Error(Exception):
    cause: Exception


def slice_start(length: int, bound: int, step: int, present: int) -> int:
    if present == 0:
        if step < 0:
            return length - 1
        return 0
    return _adjust(length, bound, step)


def slice_stop(length: int, bound: int, step: int, present: int) -> int:
    if present == 0:
        if step < 0:
            return -1
        return length
    return _adjust(length, bound, step)


def _adjust(length: int, bound: int, step: int) -> int:
    if bound < 0:
        bound = bound + length
    if bound < 0:
        if step < 0:
            return -1
        return 0
    if bound >= length:
        if step < 0:
            return length - 1
        return length
    return bound


def chr(code: int) -> str:
    return ""


def str_join(sep: str, parts: list[str]) -> str:
    built = StringBuilder()
    index = 0
    size = len(parts)
    while index < size:
        if index > 0:
            built.append(sep)
        built.append(parts[index])
        index = index + 1
    return built.toString()


def range(start: int, stop: int, step: int) -> Iterator[int]:
    value = start
    if step > 0:
        while value < stop:
            yield value
            value = value + step
    if step < 0:
        while value > stop:
            yield value
            value = value + step
