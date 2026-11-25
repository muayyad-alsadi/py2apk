from __future__ import annotations

import unittest

from _compile_helpers import _compiled_class

from Py2APK.compiler_j import JasminCompilerError


def _method(jasmin: str, name: str) -> str:
    return jasmin.split(f".method public {name}", 1)[1].split(".end method", 1)[0]


class ArithmeticLoweringTests(unittest.TestCase):
    def test_lowers_float_add_sub_mul(self) -> None:
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def add_sub_mul(self) -> None:",
                        "        added = 1.5 + 1.0",
                        "        subtracted = 1.5 - 1.0",
                        "        scaled = 1.5 * 2.0",
                        "",
                    )
                )
            ),
            "add_sub_mul",
        )
        self.assertIn("fadd", body)
        self.assertIn("fsub", body)
        self.assertIn("fmul", body)
        self.assertNotIn("iadd", body)

    def test_lowers_true_division_int_and_float(self) -> None:
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def true_div(self, count: int, a: float, b: float) -> None:",
                        "        half = count / 2",
                        "        quot = a / b",
                        "",
                    )
                )
            ),
            "true_div",
        )
        self.assertEqual(body.count("i2f"), 2)
        self.assertEqual(body.count("fdiv"), 2)
        self.assertNotIn("idiv", body)

    def test_promotes_int_before_the_float_operand(self) -> None:
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def promote(self, ratio: float) -> None:",
                        "        mix = 1 + ratio",
                        "",
                    )
                )
            ),
            "promote",
        )
        self.assertLess(body.index("i2f"), body.index("fload"))
        self.assertLess(body.index("fload"), body.index("fadd"))

    def test_lowers_float_floor_div(self) -> None:
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def floor_div(self, a: float) -> None:",
                        "        whole = a // 2.0",
                        "",
                    )
                )
            ),
            "floor_div",
        )
        self.assertIn("fdiv", body)
        self.assertIn("f2d", body)
        self.assertIn("invokestatic java/lang/Math/floor(D)D", body)
        self.assertIn("d2f", body)

    def test_float_mod_uses_floor_product(self) -> None:
        # (0.0 - 5.0) is Python -5.0. Unary minus on float stays rejected.
        # (-5.0) % 3.0 is 1.0 in Python and -2.0 under frem.
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def float_mod(self) -> None:",
                        "        rest = (0.0 - 5.0) % 3.0",
                        "",
                    )
                )
            ),
            "float_mod",
        )
        self.assertIn("invokestatic java/lang/Math/floor(D)D", body)
        self.assertIn("fmul", body)
        self.assertIn("fsub", body)
        self.assertNotIn("frem", body)

    def test_integer_binops_stay_truncating(self) -> None:
        body = _method(
            _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def int_ops(self) -> None:",
                        "        whole = (-5) // 2",
                        "        rest = (-5) % 2",
                        "        total = 1 + 2",
                        "",
                    )
                )
            ),
            "int_ops",
        )
        self.assertIn("idiv", body)
        self.assertIn("irem", body)
        self.assertIn("iadd", body)
        self.assertNotIn("Math/floor", body)
        self.assertNotIn("fdiv", body)

    def test_rejects_non_numeric_binop(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, label: str) -> None:",
                        "        mixed = label + 1.5",
                        "",
                    )
                )
            )
        self.assertIn("arithmetic needs int or float", str(raised.exception))
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def scale(self, latitude: double) -> None:",
                        "        half = latitude / 2",
                        "",
                    )
                )
            )
        self.assertIn("arithmetic needs int or float", str(raised.exception))
