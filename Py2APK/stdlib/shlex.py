"""A whitespace splitter. Quotes are not special."""

from typing import cast


def split(text: str) -> str:
    result = ""
    index = 0
    size = len(text)
    while index < size:
        code = cast(int, text[index])
        if code != 32:
            result = result + chr(code)
        else:
            if len(result) > 0:  # noqa: SIM102
                if cast(int, result[len(result) - 1]) != 32:
                    result = result + " "
        index = index + 1
    return result
