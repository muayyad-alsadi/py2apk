"""Binary search over an int array. ``hi`` is exclusive, as in CPython."""


def bisect_left(a: list[int], x: int, lo: int, hi: int) -> int:
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bisect_right(a: list[int], x: int, lo: int, hi: int) -> int:
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def insort_left(a: list[int], x: int, lo: int, hi: int) -> None:
    """Shift ``a[index:hi]`` one step toward ``hi`` and write ``x``.

    The array does not grow. The value at ``hi - 1`` is overwritten.
    """
    index = bisect_left(a, x, lo, hi)
    last = hi - 1
    while last > index:
        a[last] = a[last - 1]
        last = last - 1
    a[index] = x
