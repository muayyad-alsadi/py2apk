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
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self, left: str, right: str) -> bool:",
                        "        same = left == right",
                        "",
                    )
                )
            )
        self.assertIn("unsupported expression Compare", str(raised.exception))

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
