from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _compiled_class,
    _compiled_named,
    _compiled_outputs,
    _compiled_with_stdlib,
)

from Py2APK.cli_compile import compile_project
from Py2APK.compiler_j import JasminCompilerError


def _assert_stdlib_fallback(test: unittest.TestCase) -> None:
    from Py2APK.module_resolve import resolve_module, stdlib_root

    classes = _compiled_outputs(
        "\n".join(
            (
                "class Alpha:",
                "    def run(self) -> None:",
                "        pass",
                "",
                "class Beta:",
                "    def run(self) -> None:",
                "        pass",
                "",
            )
        )
    )
    test.assertIn(".class public Alpha", classes["Host.j"])
    test.assertIn(".class public Beta", classes["Beta.j"])
    functions = _compiled_class(
        "\n".join(
            (
                "def answer() -> int:",
                "    return 7",
                "",
            )
        )
    )
    test.assertIn(".method public static answer()I", functions)
    test.assertIn("ireturn", functions)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project = root / "proj"
        (project / "src").mkdir(parents=True)
        _ = (project / "src" / "bisect.py").write_text(
            "def marker() -> int:\n    return 1\n"
        )
        test.assertEqual(
            resolve_module("bisect", project), project / "src" / "bisect.py"
        )
        _ = (project / "src" / "math.py").write_text(
            "def marker() -> int:\n    return 1\n"
        )
        test.assertEqual(resolve_module("math", project), project / "src" / "math.py")
        empty = root / "empty"
        (empty / "src").mkdir(parents=True)
        test.assertIsNone(resolve_module("math", empty))
        fallback = resolve_module("bisect", empty)
        test.assertEqual(fallback, stdlib_root() / "bisect.py")
        host = root / "host"
        host.mkdir()
        _ = (host / "onlyhost.py").write_text("def marker() -> int:\n    return 2\n")
        missing = root / "missing-fallback"
        missing.mkdir()
        test.assertEqual(
            resolve_module("onlyhost", empty, fallback=missing, host=host),
            host / "onlyhost.py",
        )
    bisect = _compiled_named(Path("Py2APK/stdlib/bisect.py"))
    test.assertIn("ireturn", bisect)
    test.assertIn("if_icmp", bisect)
    itertools = _compiled_named(Path("Py2APK/stdlib/itertools.py"))
    test.assertIn(".catch java/lang/NoSuchElementException", itertools)
    test.assertIn("if_icmp", itertools)
    caller = _compiled_with_stdlib(
        "\n".join(
            (
                "import itertools",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        it = itertools.islice(itertools.count(0, 1), 1)",
                "",
            )
        )
    )
    test.assertIn("invokestatic itertools/islice", caller)


def _assert_stdlib_cache(test: unittest.TestCase) -> None:
    from Py2APK.stdlib_cache import ensure_stdlib_jasmin

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "stdlib"
        source.mkdir()
        module = source / "sample.py"
        _ = module.write_text("def answer() -> int:\n    return 1\n")
        cache = root / "stdlib_j"
        _ = ensure_stdlib_jasmin(cache, source)
        cached = cache / "sample.j"
        first = cached.stat().st_mtime_ns
        past = time.time() - 100
        os.utime(source, (past, past))
        os.utime(module, (past, past))
        os.utime(cache, None)
        _ = ensure_stdlib_jasmin(cache, source)
        test.assertEqual(cached.stat().st_mtime_ns, first)
        os.utime(source, None)
        _ = ensure_stdlib_jasmin(cache, source)
        test.assertGreater(cached.stat().st_mtime_ns, first)

        project = root / "app"
        app_src = project / "src"
        app_src.mkdir(parents=True)
        _ = (project / "AndroidManifest.xml").write_text("<manifest />")
        _ = (app_src / "bisect.py").write_text("def marker() -> int:\n    return 3\n")
        _alias_catalog(project / "jars.db")
        test.assertEqual(compile_project(project), 0)
        produced = (project / ".py2apk" / "j" / "bisect.j").read_text(encoding="utf-8")
        test.assertIn("marker", produced)
        test.assertIn("iconst_3", produced)


def _assert_slice_and_chr_calls(test: unittest.TestCase) -> None:
    from Py2APK.stdlib_cache import cache_directory, ensure_stdlib_jasmin

    sliced = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> None:",
                '        items = ["abc", "def", "ghi"]',
                "        for item in items[1:3]:",
                "            pass",
                "",
            )
        )
    )
    test.assertIn("invokestatic py2apk/slice_start(IIII)I", sliced)
    test.assertIn("invokestatic py2apk/slice_stop(IIII)I", sliced)
    test.assertNotIn("ifge", sliced)
    letters = _compiled_class(
        "\n".join(
            (
                "class Host:",
                "    def run(self) -> str:",
                "        return chr(65)",
                "",
            )
        )
    )
    test.assertIn("invokestatic py2apk/chr(I)Ljava/lang/String;", letters)
    _ = ensure_stdlib_jasmin()
    cached = (cache_directory() / "py2apk.j").read_text(encoding="utf-8")
    test.assertIn("newarray char", cached)
    test.assertIn("str_join", cached)
    test.assertIn("StringBuilder/append", cached)


def _expect_compile_error(test: unittest.TestCase, source: str, text: str) -> None:
    with test.assertRaises(JasminCompilerError) as raised:
        _compiled_class(source)
    test.assertIn(text, str(raised.exception))


def _class_source(body: str, preamble: str = "", params: str = "") -> str:
    signature = "self" if not params else f"self, {params}"
    lines: list[str] = []
    if preamble:
        lines.extend((preamble, ""))
    lines.extend(("class Host:", f"    def run({signature}) -> None:"))
    lines.extend(f"        {line}" for line in body.splitlines())
    return "\n".join(lines) + "\n"


class AliasLoweringTests(unittest.TestCase):
    def test_resolves_stdlib_fallbacks(self) -> None:
        _assert_stdlib_fallback(self)

    def test_caches_stdlib_jasmin_and_calls_runtime_helpers(self) -> None:
        _assert_stdlib_cache(self)
        _assert_slice_and_chr_calls(self)

    def test_lowers_math_sqrt_of_float(self) -> None:
        jasmin = _compiled_class(
            _class_source("x: double = math.sqrt(4.0)", "import math")
        )
        self.assertIn("f2d", jasmin)
        self.assertIn("invokestatic java/lang/Math/sqrt(D)D", jasmin)
        self.assertNotIn("d2f", jasmin)

    def test_lowers_math_sqrt_of_int(self) -> None:
        jasmin = _compiled_class(
            _class_source("n: int = 4\nx: double = math.sqrt(n)", "import math")
        )
        self.assertIn("i2d", jasmin)
        self.assertIn("invokestatic java/lang/Math/sqrt(D)D", jasmin)

    def test_lowers_math_alias_pow(self) -> None:
        jasmin = _compiled_class(
            _class_source("x: double = m.pow(2.0, 3.0)", "import math as m")
        )
        self.assertIn("invokestatic java/lang/Math/pow(DD)D", jasmin)

    def test_lowers_math_radians_alias(self) -> None:
        jasmin = _compiled_class(
            _class_source(
                "x: double = rad(value)",
                "from math import radians as rad",
                "value: double",
            )
        )
        self.assertIn("invokestatic java/lang/Math/toRadians(D)D", jasmin)
        for opcode in ("i2d", "f2d", "d2f"):
            self.assertNotIn(opcode, jasmin)

    def test_lowers_math_fabs_to_double_abs(self) -> None:
        jasmin = _compiled_class(
            _class_source("x: double = fabs(4)", "from math import fabs")
        )
        self.assertIn("i2d", jasmin)
        self.assertIn("invokestatic java/lang/Math/abs(D)D", jasmin)
        self.assertNotIn("invokestatic java/lang/Math/abs(I)I", jasmin)

    def test_builtin_abs_stays_int(self) -> None:
        jasmin = _compiled_class(_class_source("x: int = abs(4)"))
        self.assertIn("invokestatic java/lang/Math/abs(I)I", jasmin)

    def test_rejects_unknown_math_import(self) -> None:
        _expect_compile_error(
            self, "from math import factorial\n", "unsupported import math.factorial"
        )

    def test_rejects_unknown_math_call(self) -> None:
        _expect_compile_error(
            self,
            _class_source("math.factorial(1.0)", "import math"),
            "unknown method signature java/lang/Math.factorial",
        )

    def test_lowers_time_time(self) -> None:
        jasmin = _compiled_with_stdlib(
            _class_source("t: float = time.time()", "import time")
        )
        self.assertIn("invokestatic time/time()F", jasmin)

    def test_lowers_time_sleep(self) -> None:
        jasmin = _compiled_with_stdlib(
            _class_source("sleep(1)", "from time import sleep")
        )
        self.assertIn("invokestatic time/sleep(I)V", jasmin)

    def test_compiles_time_fallback_body(self) -> None:
        jasmin = _compiled_named(Path("Py2APK/stdlib/time.py"))
        self.assertIn("System/currentTimeMillis()J", jasmin)
        self.assertIn("l2f", jasmin)
        self.assertIn("fdiv", jasmin)
        self.assertIn("Thread/sleep(J)V", jasmin)
        self.assertIn("i2l", jasmin)
