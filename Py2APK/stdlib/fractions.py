"""Rational numbers. ``gcd`` is Euclid's algorithm."""


def gcd(a: int, b: int) -> int:
    while b != 0:
        rest = a % b
        a = b
        b = rest
    if a < 0:
        return 0 - a
    return a


class Fraction:
    num: int
    den: int

    def __init__(self, num: int, den: int) -> None:
        self.num = num
        self.den = den

    def add(self, num: int, den: int) -> Fraction:
        return Fraction(self.num * den + num * self.den, self.den * den)
