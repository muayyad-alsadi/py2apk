"""A whitespace splitter. Quotes are not special."""


def split(text: str) -> str:
    result = ""
    index = 0
    size = len(text)
    while index < size:
        code = text[index]
        if code != 32:
            result = result + chr(code)
        else:
            if len(result) > 0:
                if result[len(result) - 1] != 32:
                    result = result + " "
        index = index + 1
    return result
