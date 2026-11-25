class Object: ...


class Throwable(Object): ...


class Exception(Throwable): ...


class AutoCloseable:
    def close(self) -> None: ...


class System:
    @staticmethod
    def currentTimeMillis() -> int: ...


class Thread:
    @staticmethod
    def sleep(millis: int) -> None: ...
