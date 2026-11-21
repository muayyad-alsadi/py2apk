"""Filename matching. ``*`` and ``?`` are the only metacharacters."""


def fnmatch(name: str, pattern: str) -> bool:
    return _match(name, pattern, 0, 0)


def _match(name: str, pattern: str, name_at: int, pattern_at: int) -> bool:
    if pattern_at == len(pattern):
        if name_at == len(name):
            return True
        return False
    code = pattern[pattern_at]
    if code == 42:
        if _match(name, pattern, name_at, pattern_at + 1):
            return True
        if name_at == len(name):
            return False
        return _match(name, pattern, name_at + 1, pattern_at)
    if name_at == len(name):
        return False
    if code == 63:
        return _match(name, pattern, name_at + 1, pattern_at + 1)
    if code == name[name_at]:
        return _match(name, pattern, name_at + 1, pattern_at + 1)
    return False
