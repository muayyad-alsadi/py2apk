"""Text filling. ``shorten`` keeps the first ``width`` characters."""


def shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    result = ""
    index = 0
    while index < width:
        result = result + chr(text[index])
        index = index + 1
    return result
