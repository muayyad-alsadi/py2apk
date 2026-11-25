from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _listener_catalog,
)

from Py2APK.compiler_j import (
    JasminCompilerError,
    compile_py2j,
    index_project_types,
)
from Py2APK.compiler_j.types import _function_type, _parse_function_type
from Py2APK.jar_index import JarCatalog


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_listener_lambdas_and_method_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "jars.db"
            _alias_catalog(database)
            _listener_catalog(database)
            source = root / "src" / "Host.py"
            source.parent.mkdir(parents=True)
            _ = source.write_text(
                "\n".join(
                    (
                        "from java.util.concurrent import ExecutorService",
                        "from android.os import Handler",
                        "from android.widget import Button",
                        "from android.view import View",
                        "",
                        "class Host:",
                        "    def ping(self) -> None:",
                        "        pass",
                        "",
                        "    def load(self, url: str) -> None:",
                        "        pass",
                        "",
                        "    def start(self, pool: ExecutorService, handler: Handler, button: Button, url: str) -> None:",
                        "        pool.execute(lambda: self.load(url))",
                        "        handler.post(lambda: self.ping())",
                        "        button.setOnClickListener(lambda view: self.load(url))",
                        "        pool.execute(self.ping)",
                        "        handler.post(lambda: pool.execute(lambda: self.load(url)))",
                        "",
                    )
                )
            )
            output = root / ".py2apk" / "j" / "Host.j"
            catalog = JarCatalog(database)
            try:
                compile_py2j(source, output, catalog, {})
            finally:
                catalog.close()
            jasmin = output.read_text()
            runnable = (output.parent / "Host$lambda$1.j").read_text()
            click = (output.parent / "Host$lambda$3.j").read_text()
            self.assertIn("new Host$lambda$1", jasmin)
            self.assertIn(
                "invokeinterface java/util/concurrent/ExecutorService/execute(Ljava/lang/Runnable;)V 2",
                jasmin,
            )
            self.assertEqual(jasmin.count("new Host$lambda$"), 5)
            self.assertIn(".implements java/lang/Runnable", runnable)
            self.assertIn("invokevirtual Host/load(Ljava/lang/String;)V", runnable)
            self.assertIn(".method public onClick(Landroid/view/View;)V", click)
            self.assertIn(".implements android/view/View$OnClickListener", click)
            nested = (output.parent / "Host$lambda$5.j").read_text()
            inner = (output.parent / "Host$lambda$6.j").read_text()
            self.assertIn(
                "invokeinterface java/util/concurrent/ExecutorService/execute", nested
            )
            self.assertIn("invokevirtual Host/load(Ljava/lang/String;)V", inner)

            watcher = root / "src" / "Watch.py"
            _ = watcher.write_text(
                "\n".join(
                    (
                        "from android.widget import EditText",
                        "",
                        "class Watch:",
                        "    def note(self, text: str) -> None:",
                        "        pass",
                        "",
                        "    def start(self, field: EditText) -> None:",
                        '        field.addTextChangedListener(lambda text: self.note("ok"))',
                        "",
                    )
                )
            )
            catalog = JarCatalog(database)
            try:
                compile_py2j(watcher, root / "Watch.j", catalog, {})
            finally:
                catalog.close()
            text_watcher = (root / "Watch$lambda$1.j").read_text()
            self.assertIn(".implements android/text/TextWatcher", text_watcher)
            self.assertIn(
                ".method public afterTextChanged(Landroid/text/Editable;)V",
                text_watcher,
            )
            self.assertIn("invokevirtual Watch/note(Ljava/lang/String;)V", text_watcher)
            before = text_watcher.split(".method public beforeTextChanged", 1)[1]
            before = before.split(".end method", 1)[0]
            self.assertIn("\n    return\n", before)
            self.assertNotIn("invokevirtual", before)

            rejected = root / "src" / "Bad.py"
            _ = rejected.write_text(
                "\n".join(
                    (
                        "from okhttp3 import Call",
                        "",
                        "class Bad:",
                        "    def ping(self) -> None:",
                        "        pass",
                        "",
                        "    def go(self, call: Call) -> None:",
                        "        call.enqueue(lambda: self.ping())",
                        "",
                    )
                )
            )
            catalog = JarCatalog(database)
            try:
                with self.assertRaises(JasminCompilerError) as raised:
                    compile_py2j(rejected, root / "Bad.j", catalog, {})
            finally:
                catalog.close()
            self.assertIn("matches no method", str(raised.exception))


def _compile_files(files: dict[str, str], main: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source_root = root / "src"
        for name, text in files.items():
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(text)
        project = index_project_types(source_root)
        output = root / ".py2apk" / "j" / f"{Path(main).stem}.j"
        output.parent.mkdir(parents=True)
        catalog = JarCatalog(database)
        try:
            compile_py2j(source_root / main, output, catalog, project)
        finally:
            catalog.close()
        return {path.name: path.read_text() for path in output.parent.glob("*.j")}


def _compile_one(source: str, name: str = "Host.py") -> dict[str, str]:
    return _compile_files({name: source}, name)


def _failure(source: str, name: str = "Host.py") -> str:
    try:
        _ = _compile_one(source, name)
    except JasminCompilerError as exc:
        return str(exc)
    return ""


class FunctionValueTests(unittest.TestCase):
    def test_function_type_roundtrip(self) -> None:
        samples = (
            ((), "V"),
            (("I",), "I"),
            (("Ljava/lang/String;",), "I"),
            (("[I",), "Z"),
            (("[[Ljava/lang/String;",), "V"),
            (("I", "F"), "Ljava/lang/String;"),
            (("Lpkg/Foo_Bar$Baz;",), "I"),
            (("D", "J"), "F"),
        )
        for parameters, result in samples:
            encoded = _function_type(parameters, result)
            parsed = _parse_function_type(encoded)
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertEqual(parsed[1:], (parameters, result))
        self.assertEqual(_function_type((), "V"), "Lpy2apk$fn$_r_V;")
        self.assertEqual(_function_type(("I",), "I"), "Lpy2apk$fn$I_r_I;")
        self.assertEqual(
            _function_type(("Ljava/lang/String;",), "I"),
            "Lpy2apk$fn$Ljava_slang_sString_e_r_I;",
        )

    def test_annotated_stored_lambda_emits_base_and_invokevirtual(self) -> None:
        files = _compile_one(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use() -> int:",
                    "    f: Callable[[int], int] = lambda x: x + 1",
                    "    return f(2)",
                    "",
                )
            )
        )
        host = files["Host.j"]
        base = files["py2apk$fn$I_r_I.j"]
        concrete = files["Host$fn$1.j"]
        self.assertIn(".source py2apk$fn", base)
        self.assertIn(".class public abstract py2apk$fn$I_r_I", base)
        self.assertIn(".method public abstract call(I)I", base)
        self.assertIn(".super py2apk$fn$I_r_I", concrete)
        self.assertNotIn(".implements", concrete)
        self.assertIn("invokevirtual py2apk$fn$I_r_I/call(I)I", host)

    def test_zero_arg_and_void_lambdas(self) -> None:
        value = _compile_one(
            "\n".join(
                (
                    "def use() -> int:",
                    "    f = lambda: 42",
                    "    return f()",
                    "",
                )
            )
        )
        self.assertIn("invokevirtual py2apk$fn$_r_I/call()I", value["Host.j"])
        void = _compile_one(
            "\n".join(
                (
                    "class Host:",
                    "    def ping(self) -> None:",
                    "        pass",
                    "",
                    "    def go(self) -> None:",
                    "        f = lambda: self.ping()",
                    "        f()",
                    "",
                )
            )
        )
        method = void["Host.j"].split(".method public go()V", 1)[1]
        self.assertIn("invokevirtual py2apk$fn$_r_V/call()V", method)
        self.assertNotIn("\n    pop\n", method)

    def test_module_function_reference_same_and_imported(self) -> None:
        same = _compile_one(
            "\n".join(
                (
                    "def square(n: int) -> int:",
                    "    return n * n",
                    "",
                    "def use() -> int:",
                    "    g = square",
                    "    return g(3)",
                    "",
                )
            )
        )
        wrapper = same["Host$fn$1.j"]
        self.assertIn("invokestatic Host/square(I)I", wrapper)
        self.assertIn("invokevirtual py2apk$fn$I_r_I/call(I)I", same["Host.j"])
        imported = _compile_files(
            {
                "ops.py": "\n".join(
                    (
                        "def square(n: int) -> int:",
                        "    return n * n",
                        "",
                    )
                ),
                "Host.py": "\n".join(
                    (
                        "from ops import square",
                        "",
                        "def use() -> int:",
                        "    g = square",
                        "    return g(3)",
                        "",
                    )
                ),
            },
            "Host.py",
        )
        self.assertIn("invokestatic ops/square(I)I", imported["Host$fn$1.j"])

    def test_same_signature_shares_one_base(self) -> None:
        files = _compile_one(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def square(n: int) -> int:",
                    "    return n * n",
                    "",
                    "def use() -> int:",
                    "    f: Callable[[int], int] = lambda x: x + 1",
                    "    g = square",
                    "    return f(1) + g(2)",
                    "",
                )
            )
        )
        bases = [name for name in files if name.startswith("py2apk$fn$")]
        self.assertEqual(bases, ["py2apk$fn$I_r_I.j"])

    def test_captures_effectively_final_local(self) -> None:
        files = _compile_one(
            "\n".join(
                (
                    "def use(n: int) -> int:",
                    "    f = lambda: n + 1",
                    "    return f()",
                    "",
                )
            )
        )
        concrete = files["Host$fn$1.j"]
        self.assertIn('.field private "n" I', concrete)
        self.assertIn("invokespecial Host$fn$1/<init>(I)V", files["Host.j"])

    def test_passes_and_returns_function_values(self) -> None:
        files = _compile_one(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def apply(fn: Callable[[int], int], value: int) -> int:",
                    "    return fn(value)",
                    "",
                    "def square(n: int) -> int:",
                    "    return n * n",
                    "",
                    "def use(f: Callable[[int], int]) -> int:",
                    "    return apply(lambda x: x + 1, 2) + apply(square, 3) + apply(f, 2)",
                    "",
                    "def make() -> Callable[[int], int]:",
                    "    return lambda x: x + 1",
                    "",
                )
            )
        )
        host = files["Host.j"]
        self.assertIn("invokevirtual py2apk$fn$I_r_I/call(I)I", host)
        self.assertEqual(host.count("new Host$fn$"), 3)
        self.assertIn("areturn", host)
        other = _compile_files(
            {
                "ops.py": "\n".join(
                    (
                        "from typing import Callable",
                        "",
                        "def apply(fn: Callable[[int], int], value: int) -> int:",
                        "    return fn(value)",
                        "",
                        "def square(n: int) -> int:",
                        "    return n * n",
                        "",
                    )
                ),
                "Host.py": "\n".join(
                    (
                        "from typing import Callable",
                        "from ops import apply, square",
                        "",
                        "def use(f: Callable[[int], int]) -> int:",
                        "    return apply(lambda x: x + 1, 2) + apply(square, 3) + apply(f, 2)",
                        "",
                    )
                ),
            },
            "Host.py",
        )
        self.assertIn("invokestatic ops/apply", other["Host.j"])
        self.assertIn(".super py2apk$fn$I_r_I", other["Host$fn$1.j"])

    def test_two_modules_emit_the_same_base(self) -> None:
        left = _compile_one(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def make() -> Callable[[int], int]:",
                    "    return lambda x: x + 1",
                    "",
                )
            ),
            "Left.py",
        )
        right = _compile_one(
            "\n".join(
                (
                    "from collections.abc import Callable",
                    "",
                    "def make() -> Callable[[int], int]:",
                    "    return lambda x: x + 2",
                    "",
                )
            ),
            "Right.py",
        )
        self.assertEqual(left["py2apk$fn$I_r_I.j"], right["py2apk$fn$I_r_I.j"])

    def test_rejects_unannotated_parameterized_lambda(self) -> None:
        message = _failure(
            "\n".join(
                (
                    "def use() -> int:",
                    "    f = lambda x: x + 1",
                    "    return f(1)",
                    "",
                )
            )
        )
        self.assertIn("Callable annotation", message)

    def test_rejects_malformed_callable(self) -> None:
        ellipsis = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use(fn: Callable[..., int]) -> int:",
                    "    return 1",
                    "",
                )
            )
        )
        self.assertIn("Callable[..., R]", ellipsis)
        bare = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use(fn: Callable[int, int]) -> int:",
                    "    return 1",
                    "",
                )
            )
        )
        self.assertIn("must be a list", bare)

    def test_rejects_unknown_and_non_final_captures(self) -> None:
        unknown = _failure(
            "\n".join(
                (
                    "def use() -> int:",
                    "    f = lambda: x",
                    "    return f()",
                    "",
                )
            )
        )
        self.assertIn("unknown local 'x'", unknown)
        reassigned = _failure(
            "\n".join(
                (
                    "def use() -> int:",
                    "    x = 1",
                    "    f = lambda: x",
                    "    x = 2",
                    "    return f()",
                    "",
                )
            )
        )
        self.assertIn("effectively final", reassigned)
        parameter = _failure(
            "\n".join(
                (
                    "def use(x: int) -> int:",
                    "    x = 2",
                    "    f = lambda: x",
                    "    return f()",
                    "",
                )
            )
        )
        self.assertIn("effectively final", parameter)
        loop = _failure(
            "\n".join(
                (
                    "def use() -> int:",
                    "    f = lambda: 0",
                    "    for i in range(3):",
                    "        f = lambda: i",
                    "    return f()",
                    "",
                )
            )
        )
        self.assertIn("effectively final", loop)

    def test_rejects_bad_calls_and_signatures(self) -> None:
        arity = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use() -> int:",
                    "    f: Callable[[int], int] = lambda x: x + 1",
                    "    return f(1, 2)",
                    "",
                )
            )
        )
        self.assertIn("expected 1 arguments", arity)
        wrong = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use() -> int:",
                    '    f: Callable[[int], int] = lambda x: x + 1',
                    '    return f("a")',
                    "",
                )
            )
        )
        self.assertIn("expected I", wrong)
        keyword = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use() -> int:",
                    "    f: Callable[[int], int] = lambda x: x + 1",
                    "    return f(x=1)",
                    "",
                )
            )
        )
        self.assertIn("keyword arguments are unsupported", keyword)
        mismatch = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def apply(fn: Callable[[int], int], value: int) -> int:",
                    "    return fn(value)",
                    "",
                    "def use() -> int:",
                    "    return apply(lambda x, y: x + y, 2)",
                    "",
                )
            )
        )
        self.assertIn("Callable has 1", mismatch)
        other = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def apply(fn: Callable[[int], int], value: int) -> int:",
                    "    return fn(value)",
                    "",
                    "def other(n: int, m: int) -> int:",
                    "    return n + m",
                    "",
                    "def use() -> int:",
                    "    return apply(other, 2)",
                    "",
                )
            )
        )
        self.assertTrue(other)
        result = _failure(
            "\n".join(
                (
                    "from typing import Callable",
                    "",
                    "def use() -> int:",
                    '    f: Callable[[int], int] = lambda x: "no"',
                    "    return f(1)",
                    "",
                )
            )
        )
        self.assertIn("Callable returns", result)
        math = _failure(
            "\n".join(
                (
                    "from math import sqrt",
                    "",
                    "def use() -> float:",
                    "    g = sqrt",
                    "    return g(1.0)",
                    "",
                )
            )
        )
        self.assertIn("java.lang.Math", math)
        mapped = _failure(
            "\n".join(
                (
                    "def square(n: int) -> int:",
                    "    return n * n",
                    "",
                    "def use() -> int:",
                    "    f = square",
                    "    return map(f, 1)",
                    "",
                )
            )
        )
        self.assertIn("unsupported builtin map", mapped)
