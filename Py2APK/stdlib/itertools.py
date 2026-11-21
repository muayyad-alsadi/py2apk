"""Iterator helpers from the itertools recipes, with explicit parameters."""

from java.util import Iterator


def count(start: int, step: int) -> int:
    value = start
    while 1:
        yield value
        value = value + step


def repeat(value: int, times: int) -> int:
    left = times
    while left > 0:
        yield value
        left = left - 1


def islice(source: Iterator, stop: int) -> int:
    """Yield ``stop`` values from ``source``.

    This is ``islice(iterable, stop)``. ``start`` and ``step`` are not
    part of this subset. ``source`` is a ``java.util.Iterator``, which
    a generator already implements.
    """
    seen = 0
    for value in source:
        if seen >= stop:
            break
        yield value
        seen = seen + 1
