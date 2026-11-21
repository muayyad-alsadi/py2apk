"""Sync helpers from ``contextlib``.

``@contextmanager`` is a marker. The compiler rewrites one ``yield`` into
``__enter__`` and ``__exit__``. ``suppress`` is recognized at ``with``.
"""

from java.lang import AutoCloseable, Object, Throwable


class contextmanager:
    pass


class closing:
    thing: AutoCloseable

    def __init__(self, thing: AutoCloseable) -> None:
        self.thing = thing

    def __enter__(self) -> AutoCloseable:
        return self.thing

    def __exit__(self, exc_type: Throwable, exc_value: Throwable, traceback: Throwable) -> bool:
        self.thing.close()
        return False


class nullcontext:
    value: Object

    def __init__(self, value: Object) -> None:
        self.value = value

    def __enter__(self) -> Object:
        return self.value

    def __exit__(self, exc_type: Throwable, exc_value: Throwable, traceback: Throwable) -> bool:
        return False
