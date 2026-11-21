"""Iterator helpers from the itertools recipes, with explicit parameters."""

from collections.abc import Iterator


def count(start: int, step: int) -> Iterator[int]:
    value = start
    while 1:
        yield value
        value = value + step


def repeat(value: int, times: int) -> Iterator[int]:
    left = times
    while left > 0:
        yield value
        left = left - 1


def islice(source: Iterator, stop: int) -> Iterator[int]:
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
