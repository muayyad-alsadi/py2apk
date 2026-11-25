from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _assert_region_default,
    _compiled_class,
    _compiled_generator,
    _compiled_named,
    _compiled_with_stdlib,
)

from Py2APK.cli_compile import _imported_modules
from Py2APK.compiler_j import JasminCompilerError
from Py2APK.module_resolve import resolve_module


def _assert_iterator_alias(test: unittest.TestCase) -> None:
    host, counted = _compiled_generator(
        "Counted",
        "\n".join(
            (
                "from collections.abc import Iterator",
                "",
                "class Counted:",
                "    def values(self) -> Iterator[int]:",
                "        yield 1",
                "        yield 2",
                "",
            )
        ),
    )
    test.assertIn("new Counted$gen$1", host)
    test.assertIn(".implements java/util/Iterator", counted)
    test.assertIn(
        "invokestatic java/lang/Integer/valueOf(I)Ljava/lang/Integer;", counted
    )
    passed = _compiled_class(
        "\n".join(
            (
                "from collections.abc import Iterator",
                "",
                "class Host:",
                "    def take(self, source: Iterator) -> Iterator[int]:",
                "        return source",
                "",
            )
        )
    )
    test.assertIn("Ljava/util/Iterator;", passed)
    test.assertNotIn("$gen$", passed)
    coded = _compiled_class(
        "\n".join(
            (
                "from typing import cast",
                "",
                "class Host:",
                "    def code(self, text: str) -> str:",
                "        return chr(cast(int, text[0]))",
                "",
            )
        )
    )
    test.assertEqual(coded.count("invokevirtual java/lang/String/charAt(I)C"), 1)
    test.assertIn("invokestatic py2apk/chr(I)Ljava/lang/String;", coded)
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "mod.py"
        _ = source.write_text("from collections.abc import Iterator\n")
        test.assertNotIn("collections.abc", _imported_modules(source))


def _assert_yield_iterator(test: unittest.TestCase) -> None:
    host, fibonacci = _compiled_generator(
        "Fib",
        "\n".join(
            (
                "class Fib:",
                "    def fibonacci(self) -> int:",
                "        a = 1",
                "        b = 1",
                "        yield a",
                "        yield b",
                "        while True:",
                "            a, b = b, a + b",
                "            yield b",
                "",
            )
        ),
    )
    test.assertIn("new Fib$gen$1", host)
    test.assertIn("areturn", host)
    test.assertIn(".implements java/util/Iterator", fibonacci)
    test.assertIn(".method public hasNext()Z", fibonacci)
    test.assertIn(".method public next()Ljava/lang/Object;", fibonacci)
    test.assertIn(
        "invokestatic java/lang/Integer/valueOf(I)Ljava/lang/Integer;", fibonacci
    )
    test.assertIn("iadd", fibonacci)
    caller, lines = _compiled_generator(
        "Lines",
        "\n".join(
            (
                "class Lines:",
                "    def words(self) -> str:",
                '        yield "a"',
                '        yield "b"',
                "",
                "    def run(self) -> None:",
                "        it = self.words()",
                "        text = it.next()",
                "",
            )
        ),
    )
    test.assertIn(".implements java/util/Iterator", lines)
    test.assertIn(".method public hasNext()Z", lines)
    test.assertIn(".method public next()Ljava/lang/Object;", lines)
    test.assertNotIn("valueOf", lines)
    test.assertIn("invokevirtual Lines$gen$1/next()Ljava/lang/Object;", caller)
    test.assertIn("checkcast java/lang/String", caller)
    _caller, finite = _compiled_generator(
        "Once",
        "\n".join(
            (
                "class Once:",
                "    def one(self) -> int:",
                "        yield 1",
                "        return",
                "",
            )
        ),
    )
    test.assertIn("iconst_m1", finite)
    test.assertIn("new java/lang/NoSuchElementException", finite)
    with test.assertRaises(JasminCompilerError) as raised:
        _ = _compiled_class(
            "\n".join(
                (
                    "class BadYield:",
                    "    def words(self) -> str:",
                    "        yield 1",
                    "",
                )
            )
        )
    test.assertIn("does not match", str(raised.exception))


def _assert_context_managers(test: unittest.TestCase) -> None:
    managed = _compiled_with_stdlib(
        "\n".join(
            (
                "from contextlib import contextmanager, nullcontext",
                "",
                "@contextmanager",
                "def resource() -> int:",
                "    try:",
                "        yield 1",
                "    finally:",
                "        pass",
                "",
                "def show() -> int:",
                "    with resource() as value:",
                "        return value",
                "",
                "def empty() -> None:",
                "    with nullcontext():",
                "        pass",
                "",
            )
        )
    )
    test.assertIn("invokevirtual Host$cm$1/__enter__()I", managed)
    test.assertIn(
        "invokevirtual Host$cm$1/__exit__(Ljava/lang/Throwable;Ljava/lang/Throwable;Ljava/lang/Throwable;)Z",
        managed,
    )
    test.assertIn("aconst_null", managed)
    test.assertIn(
        "invokespecial contextlib/nullcontext/<init>(Ljava/lang/Object;)V", managed
    )
    helpers = _compiled_named(Path("Py2APK/stdlib/contextlib.py"))
    test.assertIn("invokeinterface java/lang/AutoCloseable/close()V 1", helpers)
    test.assertIn("getfield contextlib/nullcontext/value Ljava/lang/Object;", helpers)
    suppressed = _compiled_with_stdlib(
        "\n".join(
            (
                "from contextlib import suppress",
                "from java.lang import Exception",
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        with suppress(Error, Exception):",
                "            raise Error()",
                "",
                "    def miss(self) -> None:",
                "        with suppress(Error):",
                "            raise Error()",
                "",
            )
        )
    )
    test.assertIn(".catch py2apk/Error", suppressed)
    test.assertIn(".catch java/lang/Exception", suppressed)
    test.assertIn("pop", suppressed)
    test.assertIn("athrow", suppressed)
    test.assertNotIn(".catch java/lang/Throwable", suppressed)
    born = _compiled_with_stdlib(
        "\n".join(
            (
                "from contextlib import suppress",
                "from py2apk import Error",
                "",
                "class Host:",
                "    def use(self, value: Host) -> None:",
                "        pass",
                "",
                "    def run(self) -> None:",
                "        with suppress(Error):",
                "            kept = self",
                "        self.use(kept)",
                "",
            )
        )
    )
    _assert_region_default(
        test,
        born.split(".method public run", 1)[1],
        "L_suppress_",
        "L_suppressafter_",
    )
    with test.assertRaises(JasminCompilerError):
        _compiled_with_stdlib(
            "\n".join(
                (
                    "from contextlib import contextmanager",
                    "",
                    "@contextmanager",
                    "@contextmanager",
                    "def resource() -> int:",
                    "    yield 1",
                    "",
                )
            )
        )
    with test.assertRaises(JasminCompilerError):
        _compiled_with_stdlib(
            "\n".join(
                (
                    "from contextlib import contextmanager",
                    "",
                    "@contextmanager()",
                    "def resource() -> int:",
                    "    yield 1",
                    "",
                )
            )
        )
    with test.assertRaises(JasminCompilerError):
        _compiled_with_stdlib(
            "\n".join(
                (
                    "from contextlib import contextmanager",
                    "",
                    "@contextmanager",
                    "def resource() -> int:",
                    "    yield 1",
                    "    yield 2",
                    "",
                )
            )
        )
    found = resolve_module("contextlib", Path())
    test.assertIsNotNone(found)
    test.assertEqual(found.name, "contextlib.py")
    test.assertIn("stdlib", found.parts)


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_yield_to_iterator(self) -> None:
        _assert_yield_iterator(self)

    def test_iterator_annotation_is_java_util_iterator(self) -> None:
        _assert_iterator_alias(self)

    def test_lowers_contextmanager_closing_and_suppress(self) -> None:
        _assert_context_managers(self)
