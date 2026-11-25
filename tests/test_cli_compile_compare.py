from __future__ import annotations

import unittest

from _compile_helpers import (
    _compiled_class,
)

from Py2APK.compiler_j import JasminCompilerError


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_identity_checks(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def absent(self, response: str) -> None:",
                    "        if response is None:",
                    "            pass",
                    "        if response is not None:",
                    "            pass",
                    "        if None is response:",
                    "            pass",
                    "",
                    "    def same(self, left: str, right: str) -> None:",
                    "        if left is right:",
                    "            pass",
                    "        if left is not right:",
                    "            pass",
                    "",
                    "    def primitive(self, count: int) -> None:",
                    "        if count is None:",
                    "            pass",
                    "        if count is not None:",
                    "            pass",
                    "",
                )
            )
        )
        absent = jasmin.split(".method public absent", 1)[1].split(".end method", 1)[0]
        same = jasmin.split(".method public same", 1)[1].split(".end method", 1)[0]
        primitive = jasmin.split(".method public primitive", 1)[1].split(
            ".end method", 1
        )[0]
        self.assertEqual(absent.count("ifnonnull"), 2)
        self.assertIn("ifnull", absent)
        self.assertNotIn("equals", absent)
        self.assertIn("if_acmpne", same)
        self.assertIn("if_acmpeq", same)
        self.assertNotIn("equals", same)
        self.assertNotIn("ifnull", same)
        self.assertIn("pop", primitive)
        self.assertIn("goto", primitive)
        self.assertNotIn("equals", primitive)
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, count: int, other: int) -> None:",
                        "        if count is other:",
                        "            pass",
                        "",
                    )
                )
            )
        self.assertIn("is compares references", str(raised.exception))

    def test_stores_identity_checks(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def mark(self, body: str) -> bool:",
                    "        missing = body is None",
                    "        saved = body is None",
                    "        return missing",
                    "",
                    "    def watch(self, body: str) -> None:",
                    "        while body is not None:",
                    "            pass",
                    "",
                    "    def pair(self, left: str, right: str) -> bool:",
                    "        return left is not right",
                    "",
                    "    def number(self, count: int) -> bool:",
                    "        return count is None",
                    "",
                )
            )
        )
        mark = jasmin.split(".method public mark", 1)[1].split(".end method", 1)[0]
        watch = jasmin.split(".method public watch", 1)[1].split(".end method", 1)[0]
        pair = jasmin.split(".method public pair", 1)[1].split(".end method", 1)[0]
        number = jasmin.split(".method public number", 1)[1].split(".end method", 1)[0]
        self.assertIn("ifnonnull", mark)
        self.assertIn("iconst_1", mark)
        self.assertIn("iconst_0", mark)
        self.assertIn("istore", mark)
        self.assertIn("iload", mark)
        self.assertIn("ireturn", mark)
        self.assertNotIn("equals", mark)
        self.assertIn("ifnull", watch)
        self.assertNotIn("iconst", watch)
        self.assertNotIn("istore", watch)
        self.assertNotIn("equals", watch)
        self.assertIn("if_acmpeq", pair)
        self.assertIn("iconst_1", pair)
        self.assertNotIn("equals", pair)
        self.assertIn("pop", number)
        self.assertIn("iconst_0", number)
        self.assertNotIn("equals", number)
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, count: int, other: int) -> bool:",
                        "        return count is other",
                        "",
                    )
                )
            )
        self.assertIn("is compares references", str(raised.exception))

    def test_and_short_circuits_the_right_comparison(self) -> None:
        text = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def both(self, left: int, right: int) -> int:",
                    "        if left == 1 and right == 2:",
                    "            return 1",
                    "        return 0",
                    "",
                )
            )
        )
        first = text.index("iconst_1")
        second = text.index("iconst_2")
        self.assertLess(first, text.index("if_icmpne"))
        self.assertLess(text.index("if_icmpne"), second)
        self.assertEqual(text.count("if_icmpne"), 2)
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(
                "\n".join(
                    (
                        "from typing import TYPE_CHECKING",
                        "",
                        "if TYPE_CHECKING:",
                        "    pass",
                        "",
                        "class Host:",
                        "    def widen(self) -> None:",
                        "        return",
                        "",
                    )
                )
            )
        self.assertIn("builtin annotations", str(raised.exception))

    def test_stores_equality_checks(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def texts(self, left: str, right: str) -> bool:",
                    "        same = left == right",
                    "        return same",
                    "",
                    "    def numbers(self, left: int, right: int) -> bool:",
                    "        return left != right",
                    "",
                    "    def mixed(self, folded: str, count: int) -> bool:",
                    "        return folded == count",
                    "",
                    "    def width(self, width: float) -> bool:",
                    "        return width == 1.0",
                    "",
                    "    def missing(self, folded: str) -> bool:",
                    "        return folded == None",
                    "",
                )
            )
        )
        texts = jasmin.split(".method public texts", 1)[1].split(".end method", 1)[0]
        numbers = jasmin.split(".method public numbers", 1)[1].split(".end method", 1)[0]
        mixed = jasmin.split(".method public mixed", 1)[1].split(".end method", 1)[0]
        width = jasmin.split(".method public width", 1)[1].split(".end method", 1)[0]
        missing = jasmin.split(".method public missing", 1)[1].split(".end method", 1)[0]
        self.assertIn("invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z", texts)
        self.assertIn("iconst_1", texts)
        self.assertIn("iconst_0", texts)
        self.assertIn("istore", texts)
        self.assertIn("ireturn", texts)
        self.assertIn("if_icmpeq", numbers)
        self.assertNotIn("equals", numbers)
        self.assertIn("iconst_0", numbers)
        self.assertIn("ireturn", numbers)
        self.assertIn("pop", mixed)
        self.assertIn("iconst_0", mixed)
        self.assertNotIn("equals", mixed)
        self.assertIn("fcmpg", width)
        self.assertIn("iconst_0", width)
        self.assertIn("iconst_1", width)
        self.assertIn("ifnull", missing)
        self.assertIn("iconst_0", missing)
        self.assertIn("iconst_1", missing)

    def test_stores_bool_ops(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def both(self, ready: bool, count: int) -> bool:",
                    "        return ready and count > 2",
                    "",
                    "    def either(self, left: bool, right: bool) -> bool:",
                    "        return left or right",
                    "",
                    "    def gate(self, left: bool, right: bool) -> None:",
                    "        if left and right:",
                    "            pass",
                    "",
                )
            )
        )
        both = jasmin.split(".method public both", 1)[1].split(".end method", 1)[0]
        either = jasmin.split(".method public either", 1)[1].split(".end method", 1)[0]
        gate = jasmin.split(".method public gate", 1)[1].split(".end method", 1)[0]
        self.assertLess(both.index("ifeq"), both.index("iconst_2"))
        self.assertIn("if_icmple", both)
        self.assertIn("iconst_1", both)
        self.assertIn("iconst_0", both)
        self.assertLess(either.index("goto"), either.index("iload 2"))
        self.assertIn("iconst_1", either)
        self.assertIn("iconst_0", either)
        self.assertNotIn("iconst", gate)
        self.assertEqual(gate.count("ifeq"), 2)

    def test_stores_not(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def empty(self, items: list[str]) -> bool:",
                    "        return not items",
                    "",
                    "    def flip(self, flag: bool) -> bool:",
                    "        return not flag",
                    "",
                )
            ),
            collections=True,
        )
        empty = jasmin.split(".method public empty", 1)[1].split(".end method", 1)[0]
        flip = jasmin.split(".method public flip", 1)[1].split(".end method", 1)[0]
        self.assertIn("arraylength", empty)
        self.assertIn("iconst_0", empty)
        self.assertIn("iconst_1", empty)
        self.assertIn("ifne", flip)
        self.assertIn("iconst_0", flip)
        self.assertIn("iconst_1", flip)
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, width: float) -> bool:",
                        "        return not width",
                        "",
                    )
                )
            )
        self.assertIn("if condition must be bool or int", str(raised.exception))

    def test_chained_comparisons(self) -> None:
        jasmin = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def gate(self, left: int, mid: int, right: int) -> None:",
                    "        if left == mid == right:",
                    "            pass",
                    "",
                    "    def span(self, lo: int, mid: int, hi: int) -> None:",
                    "        if lo < mid < hi:",
                    "            pass",
                    "",
                    "    def measure(self, left: int, right: int, name: str) -> bool:",
                    "        return left == len(name) == right",
                    "",
                    "    def saved(self, left: int, mid: int, right: int) -> bool:",
                    "        return left == mid == right",
                    "",
                )
            )
        )
        gate = jasmin.split(".method public gate", 1)[1].split(".end method", 1)[0]
        span = jasmin.split(".method public span", 1)[1].split(".end method", 1)[0]
        measure = jasmin.split(".method public measure", 1)[1].split(".end method", 1)[0]
        saved = jasmin.split(".method public saved", 1)[1].split(".end method", 1)[0]
        self.assertEqual(gate.count("if_icmpne"), 2)
        self.assertNotIn("iconst", gate)
        self.assertEqual(span.count("if_icmpge"), 2)
        self.assertNotIn("iconst", span)
        self.assertEqual(measure.count("length()I"), 1)
        self.assertLess(measure.index("length()I"), measure.index("iload 2"))
        self.assertIn("iconst_1", saved)
        self.assertIn("iconst_0", saved)
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, left: int, mid: int, right: int) -> None:",
                        "        if left in mid:",
                        "            pass",
                        "",
                    )
                )
            )
        self.assertIn("unsupported comparison In", str(raised.exception))
