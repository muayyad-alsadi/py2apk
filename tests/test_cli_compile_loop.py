from __future__ import annotations

import unittest
from pathlib import Path

from _compile_helpers import (
    _compiled_class,
    _compiled_generator,
    _compiled_with_stdlib,
)

from Py2APK.compiler_j import JasminCompilerError


def _assert_for_loops(test: unittest.TestCase) -> None:
    literal = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                '        for item in ["abc", "def"]:',
                "            pass",
                "",
            )
        )
    )
    test.assertIn("arraylength", literal)
    test.assertIn("aaload", literal)
    test.assertIn('ldc "abc"', literal)
    reverse = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                '        items = ["abc", "def", "ghi"]',
                "        for item in items[-2::-1]:",
                "            pass",
                "",
            )
        )
    )
    test.assertIn("invokestatic py2apk/slice_start(IIII)I", reverse)
    test.assertIn("if_icmple", reverse)
    test.assertIn("iconst_m1", reverse)
    caller, _generated = _compiled_generator(
        "Host",
        "\n".join(
            (
                "class Host:",
                "    def values(self) -> int:",
                "        yield 1",
                "        return",
                "",
                "    def run(self) -> None:",
                "        for item in self.values():",
                "            pass",
                "",
            )
        ),
    )
    test.assertIn(".catch java/lang/NoSuchElementException from ", caller)
    test.assertIn("invokevirtual Host$gen$1/next()Ljava/lang/Object;", caller)
    test.assertIn("invokevirtual java/lang/Integer/intValue()I", caller)
    test.assertNotIn("hasNext", caller)
    catch_at = caller.index(".catch java/lang/NoSuchElementException")
    next_at = caller.index("invokevirtual Host$gen$1/next()Ljava/lang/Object;")
    body_at = caller.index("goto L_for_")
    test.assertLess(catch_at, next_at)
    test.assertLess(next_at, body_at)
    yielded = _compiled_generator(
        "Host",
        "\n".join(
            (
                "class Host:",
                "    def values(self) -> int:",
                "        for item in [1, 2]:",
                "            yield item",
                "",
            )
        ),
    )[1]
    test.assertIn('.field private "py2apk_for_idx_', yielded)
    test.assertIn("getfield Host$gen$1/py2apk_for_idx_", yielded)
    with test.assertRaises(JasminCompilerError) as raised:
        _ = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    '        items = ["abc"]',
                    "        for item in items[::0]:",
                    "            pass",
                    "",
                )
            )
        )
    test.assertIn("slice step cannot be zero", str(raised.exception))


def _assert_integer_loop(test: unittest.TestCase) -> None:
    jasmin = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                "        a = 1",
                "        b = 1",
                "        while a:",
                "            a, b = b, a + b",
                "            a = 0",
                "",
            )
        )
    )
    test.assertIn("iadd", jasmin)
    test.assertIn("goto L_while_", jasmin)
    test.assertIn("ifeq L_endwhile_", jasmin)


def _assert_loop_controls(test: unittest.TestCase) -> None:
    stopped = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> int:",
                "        n = 0",
                "        while n < 10:",
                "            n = n + 1",
                "            if n == 3:",
                "                break",
                "        return n",
                "",
            )
        )
    )
    test.assertIn("goto L_endwhile_", stopped)
    test.assertNotIn("goto L_break_", stopped)
    continued = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                '        items = ["a", "b", "c"]',
                "        for item in items:",
                "            continue",
                "",
            )
        )
    )
    test.assertIn("goto L_forcont_", continued)
    test.assertLess(continued.index("L_forcont_"), continued.index("iadd"))
    skipped = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> int:",
                '        items = ["a"]',
                "        n = 0",
                "        for item in items:",
                "            break",
                "        else:",
                '            label = "missed"',
                "        return n",
                "",
            )
        )
    )
    test.assertIn("goto L_break_", skipped)
    test.assertLess(skipped.index("goto L_break_"), skipped.index('ldc "missed"'))
    ranged = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                "        for i in range(1, 4, 1):",
                "            pass",
                "        for i in range(3):",
                "            pass",
                "",
            )
        )
    )
    test.assertEqual(ranged.count("invokestatic py2apk/range(III)Lpy2apk$gen$1;"), 2)
    test.assertIn("iconst_0", ranged)
    test.assertIn("iconst_1", ranged)
    values = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                "        for i in (1, 2, 3):",
                "            pass",
                "",
            )
        )
    )
    test.assertIn("newarray int", values)
    helper = Path("Py2APK/stdlib/py2apk.py").read_text(encoding="utf-8")
    test.assertNotIn("return", helper[helper.index("def range") :])
    with test.assertRaises(JasminCompilerError):
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    '        items = (1, "a")',
                    "",
                )
            )
        )
    with test.assertRaises(JasminCompilerError):
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    "        break",
                    "",
                )
            )
        )
    with test.assertRaises(JasminCompilerError):
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    "        for a, b in (1, 2):",
                    "            pass",
                    "",
                )
            )
        )
    wrapped = _compiled_with_stdlib(
        "\n".join(
            (
                "import itertools",
                "",
                "def fibonacci() -> int:",
                "    yield 1",
                "    yield 1",
                "    a = 1",
                "    b = 1",
                "    while True:",
                "        a, b = b, a + b",
                "        yield b",
                "",
                "def show() -> None:",
                "    for value in itertools.islice(fibonacci(), 10):",
                "        pass",
                "",
            )
        )
    )
    test.assertIn("invokestatic itertools/islice(Ljava/util/Iterator;I)", wrapped)


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_integer_addition_while_and_unpack(self) -> None:
        _assert_integer_loop(self)

    def test_lowers_for_over_arrays_slices_and_generators(self) -> None:
        _assert_for_loops(self)

    def test_lowers_break_continue_else_range_and_tuples(self) -> None:
        _assert_loop_controls(self)
