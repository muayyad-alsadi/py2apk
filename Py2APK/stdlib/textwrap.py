"""Text filling. ``shorten`` keeps the first ``width`` characters."""

from typing import cast


def shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    result = ""
    index = 0
    while index < width:
        result = result + chr(cast(int, text[index]))
        index = index + 1
    return result
