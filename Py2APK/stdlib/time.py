"""Clock helpers with millisecond resolution.

``time()`` is ``l2f`` of ``System.currentTimeMillis``, then ``fdiv``
by ``1000.0``. Epoch milliseconds do not fit in a float mantissa.
``sleep`` takes whole seconds.
"""

from java.lang import System, Thread


def time() -> float:
    return float(System.currentTimeMillis()) / 1000.0


def sleep(seconds: int) -> None:
    Thread.sleep(seconds * 1000)
