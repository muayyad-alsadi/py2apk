"""URL helpers. ``urlsplit`` returns the scheme, or the whole string."""

from typing import cast


def quote(text: str) -> str:
    result = ""
    index = 0
    size = len(text)
    while index < size:
        code = cast(int, text[index])
        if _safe(code):
            result = result + chr(code)
        else:
            result = result + "%" + _digit(code // 16) + _digit(code % 16)
        index = index + 1
    return result


def urlencode(key: str, value: str) -> str:
    return quote(key) + "=" + quote(value)


def urlsplit(url: str) -> str:
    mark = url.find("://")
    if mark < 0:
        return url
    scheme = ""
    index = 0
    while index < mark:
        scheme = scheme + chr(cast(int, url[index]))
        index = index + 1
    return scheme


def _safe(code: int) -> bool:
    if code >= 48:  # noqa: SIM102
        if code <= 57:
            return True
    if code >= 65:  # noqa: SIM102
        if code <= 90:
            return True
    if code >= 97:  # noqa: SIM102
        if code <= 122:
            return True
    if code == 45:
        return True
    if code == 46:
        return True
    if code == 95:
        return True
    if code == 126:
        return True
    return False


def _digit(value: int) -> str:
    if value < 10:
        return chr(48 + value)
    return chr(65 + value - 10)
