"""Keyword arguments on Java calls and project functions."""

from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _collection_catalog,
    _compiled_class,
    _compiled_with_stdlib,
)

from Py2APK.compiler_j import JasminCompilerError, compile_py2j, index_project_types
from Py2APK.compiler_j.calls import _guard_builtin_keywords
from Py2APK.compiler_j.compiler import _Compiler
from Py2APK.jar_index import API_SCHEMA, JarCatalog

_STRING = "Ljava/lang/String;"


def _compile_java(
    source_text: str,
    methods: list[tuple[object, ...]],
    extra: dict[str, str] | None = None,
    api_levels: list[tuple[str, str, int]] | None = None,
    min_api: int | None = None,
) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        connection = sqlite3.connect(database)
        _ = connection.executemany(
            "INSERT INTO classes (internal, super_internal, is_interface) VALUES (?, ?, ?)",
            (
                ("demo/Builder", "java/lang/Object", 0),
                ("demo/Base", "java/lang/Object", 0),
                ("java/lang/String", "java/lang/Object", 0),
                ("java/lang/CharSequence", "java/lang/Object", 1),
                ("java/lang/Appendable", "java/lang/Object", 1),
            ),
        )
        _ = connection.executemany(
            "INSERT INTO class_interfaces (owner, interface) VALUES (?, ?)",
            (
                ("java/lang/String", "java/lang/CharSequence"),
                ("java/lang/String", "java/lang/Appendable"),
            ),
        )
        _ = connection.executemany(
            "INSERT INTO methods "
            "(owner, name, params, result, is_static, opcode, param_names) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            methods,
        )
        if api_levels is not None:
            _ = connection.executescript(API_SCHEMA)
            _ = connection.executemany(
                "INSERT INTO api_levels (owner, member, since) VALUES (?, ?, ?)",
                api_levels,
            )
        connection.commit()
        connection.close()
        source = root / "src" / "Host.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        for name, text in (extra or {}).items():
            extra_path = root / "src" / name
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            _ = extra_path.write_text(text)
        output = root / "Host.j"
        project = index_project_types(root / "src")
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project, min_api)
        finally:
            catalog.close()
        return output.read_text()


def _builder_methods() -> list[tuple[object, ...]]:
    builder = "Ldemo/Builder;"
    return [
        ("demo/Builder", "<init>", _STRING, "V", 0, "invokespecial", "title"),
        ("demo/Builder", "setTitle", _STRING, builder, 0, "invokevirtual", "title"),
        ("demo/Builder", "setTitle", "I", builder, 0, "invokevirtual", "titleId"),
        (
            "demo/Builder",
            "show",
            _STRING + _STRING,
            "V",
            0,
            "invokevirtual",
            "title,message",
        ),
        (
            "demo/Builder",
            "format",
            _STRING + "I",
            _STRING,
            1,
            "invokestatic",
            "pattern,width",
        ),
        ("demo/Base", "greet", _STRING, "V", 0, "invokevirtual", "title"),
        ("demo/Builder", "length", _STRING, "I", 0, "invokevirtual", ""),
        (
            "demo/Builder",
            "copy",
            "Ljava/lang/CharSequence;",
            "V",
            0,
            "invokevirtual",
            "text",
        ),
        (
            "demo/Builder",
            "copy",
            "Ljava/lang/Appendable;",
            "V",
            0,
            "invokevirtual",
            "text",
        ),
    ]


class KeywordCallTests(unittest.TestCase):
    def test_reorders_java_keywords_and_picks_named_overloads(self) -> None:
        text = _compile_java(
            "\n".join(
                (
                    "from demo import Builder",
                    "",
                    "class Host:",
                    "    def run(self, builder: Builder) -> None:",
                    '        made = Builder(title="hi")',
                    '        builder.setTitle(title="hi")',
                    "        builder.setTitle(titleId=1)",
                    '        builder.show(message="m", title="t")',
                    '        label = Builder.format(width=4, pattern="n")',
                    "        builder.setTitle(label)",
                    "",
                )
            ),
            _builder_methods(),
        )

        self.assertIn("invokespecial demo/Builder/<init>(Ljava/lang/String;)V", text)
        self.assertIn(
            "invokevirtual demo/Builder/setTitle(Ljava/lang/String;)Ldemo/Builder;",
            text,
        )
        self.assertIn("invokevirtual demo/Builder/setTitle(I)Ldemo/Builder;", text)
        show = text.split("invokevirtual demo/Builder/show", 1)[0]
        self.assertLess(show.rindex('ldc "t"'), show.rindex('ldc "m"'))
        formatted = text.split("invokestatic demo/Builder/format", 1)[0]
        self.assertLess(formatted.rindex('ldc "n"'), formatted.rindex("iconst_4"))

    def test_reorders_project_functions_methods_and_super(self) -> None:
        text = _compile_java(
            "\n".join(
                (
                    "from Widget import Widget",
                    "from demo import Base",
                    "",
                    "class Host(Base):",
                    "    def __init__(self, title: str) -> None:",
                    "        pass",
                    "",
                    "    def paint(self, title: str, message: str) -> None:",
                    "        pass",
                    "",
                    "    def run(self) -> None:",
                    '        made = Widget(title="hi")',
                    '        other = Host(title="hi")',
                    '        self.paint(message="m", title="t")',
                    '        super().greet(title="hi")',
                    "",
                )
            ),
            _builder_methods(),
            {
                "Widget.py": "\n".join(
                    (
                        "class Widget:",
                        "    def __init__(self, title: str) -> None:",
                        "        pass",
                        "",
                    )
                )
            },
        )
        helper = _compile_java(
            "\n".join(
                (
                    "def helper(title: str, message: str) -> str:",
                    "    return title",
                    "",
                    "def run() -> str:",
                    '    return helper(message="m", title="t")',
                    "",
                )
            ),
            [],
        )

        self.assertIn("invokespecial Widget/<init>(Ljava/lang/String;)V", text)
        self.assertIn("invokespecial Host/<init>(Ljava/lang/String;)V", text)
        self.assertIn(
            "invokevirtual Host/paint(Ljava/lang/String;Ljava/lang/String;)V", text
        )
        paint = text.split("invokevirtual Host/paint", 1)[0]
        self.assertLess(paint.rindex('ldc "t"'), paint.rindex('ldc "m"'))
        self.assertIn("invokespecial demo/Base/greet(Ljava/lang/String;)V", text)
        self.assertIn(
            "invokestatic Host/helper(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
            helper,
        )
        called = helper.split("invokestatic Host/helper", 1)[0]
        self.assertLess(called.rindex('ldc "t"'), called.rindex('ldc "m"'))

    def test_rejects_bad_keywords(self) -> None:
        source = "\n".join(
            (
                "from demo import Builder",
                "",
                "class Host:",
                "    def run(self, builder: Builder, label: str) -> None:",
                "        {body}",
                "",
            )
        )
        cases = (
            ('builder.setTitle(**{"title": "hi"})', "unpacked keyword arguments"),
            (
                'builder.setTitle("hi", title="x")',
                "overlaps a positional argument",
            ),
            ('builder.setTitle(missing="x")', "unknown keyword argument 'missing'"),
            ('builder.show(title="t")', "missing argument for parameter 'message'"),
            ('builder.length(text="hi")', "need parameter names"),
            ('builder.copy(text=label)', "ambiguous keyword call"),
        )
        for body, message in cases:
            with self.subTest(body=body):
                with self.assertRaises(JasminCompilerError) as raised:
                    _ = _compile_java(source.format(body=body), _builder_methods())
                self.assertIn(message, str(raised.exception))

    def test_rejects_duplicate_keywords_before_lookup(self) -> None:
        expression = ast.parse('builder.show(title="t")').body[0]
        self.assertIsInstance(expression, ast.Expr)
        call = expression.value
        self.assertIsInstance(call, ast.Call)
        call.keywords.append(ast.keyword(arg="title", value=ast.Constant(value="u")))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "jars.db"
            _alias_catalog(database)
            source = root / "Host.py"
            output = root / "Host.j"
            _ = source.write_text("class Host:\n    pass\n")
            catalog = JarCatalog(database)
            try:
                compiler = _Compiler(
                    source,
                    output,
                    ast.parse(source.read_text()),
                    catalog,
                    {},
                )
                with self.assertRaises(JasminCompilerError) as raised:
                    _guard_builtin_keywords(compiler, call)
            finally:
                catalog.close()
        self.assertIn("duplicate keyword argument 'title'", str(raised.exception))

    def test_rejects_keywords_on_append_suppress_and_super(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "jars.db"
            _alias_catalog(database)
            _collection_catalog(database)
            source = root / "src" / "Host.py"
            source.parent.mkdir(parents=True)
            _ = source.write_text(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self) -> None:",
                        '        items = ["a"]',
                        '        items.append(item="b")',
                        "",
                    )
                )
            )
            output = root / "Host.j"
            catalog = JarCatalog(database)
            try:
                with self.assertRaises(JasminCompilerError) as appended:
                    compile_py2j(source, output, catalog, {})
            finally:
                catalog.close()
        self.assertIn("one positional argument", str(appended.exception))
        with self.assertRaises(JasminCompilerError) as suppressed:
            _ = _compiled_with_stdlib(
                "\n".join(
                    (
                        "from contextlib import suppress",
                        "from py2apk import Error",
                        "",
                        "class Host:",
                        "    def run(self) -> None:",
                        "        with suppress(kind=Error):",
                        "            raise Error()",
                        "",
                    )
                )
            )
        self.assertIn("keyword arguments are unsupported", str(suppressed.exception))
        with self.assertRaises(JasminCompilerError) as parent:
            _ = _compiled_class(
                "\n".join(
                    (
                        "class Host:",
                        "    def run(self) -> None:",
                        "        super(kind=1)",
                        "",
                    )
                )
            )
        self.assertIn("keyword arguments are unsupported", str(parent.exception))


_BUILDER_SET_TITLE = "setTitle(Ljava/lang/String;)Ldemo/Builder;"
_BUILDER_FORMAT = "format(Ljava/lang/String;I)Ljava/lang/String;"
_BUILDER_INIT = "<init>(Ljava/lang/String;)V"
_BASE_GREET = "greet(Ljava/lang/String;)V"


def _min_api_source(body: str, base: bool = False) -> str:
    header = "class Host(Base):" if base else "class Host:"
    return "\n".join(
        (
            "from demo import Base",
            "from demo import Builder",
            "",
            header,
            "    def run(self, builder: Builder, label: str) -> None:",
            f"        {body}",
            "",
        )
    )


class MinApiTests(unittest.TestCase):
    def test_rejects_a_method_newer_than_min_api(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compile_java(
                _min_api_source("builder.setTitle(label)"),
                _builder_methods(),
                api_levels=[("demo/Builder", _BUILDER_SET_TITLE, 26)],
                min_api=21,
            )

        message = str(raised.exception)
        self.assertIn(
            "demo.Builder.setTitle requires API 26, minSdkVersion is 21", message
        )
        self.assertIn("Host.py:6: ", message)

    def test_accepts_a_method_at_or_below_min_api(self) -> None:
        text = _compile_java(
            _min_api_source("builder.setTitle(label)"),
            _builder_methods(),
            api_levels=[("demo/Builder", _BUILDER_SET_TITLE, 26)],
            min_api=26,
        )

        self.assertIn("invokevirtual demo/Builder/setTitle", text)

    def test_accepts_a_member_absent_from_the_table(self) -> None:
        text = _compile_java(
            _min_api_source("builder.setTitle(label)"),
            _builder_methods(),
            api_levels=[("demo/Builder", "unrelated()V", 26)],
            min_api=1,
        )

        self.assertIn("invokevirtual demo/Builder/setTitle", text)

    def test_skips_the_check_without_min_api(self) -> None:
        text = _compile_java(
            _min_api_source("builder.setTitle(label)"),
            _builder_methods(),
            api_levels=[("demo/Builder", _BUILDER_SET_TITLE, 26)],
            min_api=None,
        )

        self.assertIn("invokevirtual demo/Builder/setTitle", text)

    def test_checks_constructors(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compile_java(
                _min_api_source('made = Builder(title="hi")'),
                _builder_methods(),
                api_levels=[("demo/Builder", _BUILDER_INIT, 26)],
                min_api=21,
            )

        self.assertIn("demo.Builder.<init> requires API 26", str(raised.exception))

    def test_checks_static_calls(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compile_java(
                _min_api_source('label = Builder.format("n", 4)'),
                _builder_methods(),
                api_levels=[("demo/Builder", _BUILDER_FORMAT, 26)],
                min_api=21,
            )

        self.assertIn("demo.Builder.format requires API 26", str(raised.exception))

    def test_checks_super_calls(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _ = _compile_java(
                _min_api_source("super().greet(label)", base=True),
                _builder_methods(),
                api_levels=[("demo/Base", _BASE_GREET, 26)],
                min_api=21,
            )

        self.assertIn("demo.Base.greet requires API 26", str(raised.exception))

    def test_never_checks_project_local_methods(self) -> None:
        text = _compile_java(
            "\n".join(
                (
                    "class Host:",
                    "    def paint(self, title: str) -> None:",
                    "        pass",
                    "",
                    "    def run(self, label: str) -> None:",
                    "        self.paint(label)",
                    "",
                )
            ),
            [],
            api_levels=[("Host", "paint(Ljava/lang/String;)V", 99)],
            min_api=21,
        )

        self.assertIn("invokevirtual Host/paint(Ljava/lang/String;)V", text)
