"""Date helpers. ``weekday`` is Sakamoto's method, Sunday is 0."""


def isleap(year: int) -> bool:
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return False
    if year % 4 == 0:
        return True
    return False


def weekday(year: int, month: int, day: int) -> int:
    offsets = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if month < 3:
        year = year - 1
    return (year + year // 4 - year // 100 + year // 400 + offsets[month - 1] + day) % 7
