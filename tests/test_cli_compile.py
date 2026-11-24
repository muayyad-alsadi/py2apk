from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import time
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import ClassVar, Generic, TypeVar
from unittest import mock

from Py2APK.cli_compile import _imported_modules, compile_project
from Py2APK.compiler_j import JasminCompilerError, compile_py2j, index_project_types
from Py2APK.jar_index import MEMBER_SCHEMA, JarCatalog
from Py2APK.module_resolve import resolve_module

T = TypeVar("T")


class FakeProgress(Generic[T]):
    descriptions: ClassVar[list[str]] = []
    initial_descriptions: ClassVar[list[str]] = []

    def __init__(
        self,
        items: Iterable[T],
        desc: str | None = None,
        unit: str | None = None,
    ) -> None:
        self.items: list[T] = list(items)
        if desc is not None:
            self.initial_descriptions.append(desc)
        _ = unit

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __enter__(self) -> FakeProgress[T]:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def set_description(self, description: str) -> None:
        self.descriptions.append(description)

    def close(self) -> None:
        pass


class CompileCommandTests(unittest.TestCase):
    def make_project(self, root: Path) -> tuple[Path, Path]:
        _ = (root / "AndroidManifest.xml").write_text("<manifest />")
        source_root = root / "src"
        source_root.mkdir()
        return source_root, root / ".py2apk"

    def run_compiler(self, root: Path) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        FakeProgress.descriptions = []
        FakeProgress.initial_descriptions = []
        with (
            mock.patch("Py2APK.cli_compile.tqdm", new=FakeProgress),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            status = compile_project(root)
        return status, output.getvalue(), errors.getvalue()

    def test_rejects_a_missing_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            status, output, errors = self.run_compiler(root)

            self.assertEqual(status, 1)
            self.assertEqual(output, "")
            self.assertIn("./AndroidManifest.xml", errors)
            self.assertIn("./src", errors)
            self.assertNotIn("./target", errors)

    def test_creates_jasmin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _ = self.make_project(root)

            status, output, errors = self.run_compiler(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertTrue((root / ".py2apk/j").is_dir())
            self.assertIn("scanned: 0; modified: 0; compiled: 0", output)

    def test_compiles_missing_and_older_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root, target_root = self.make_project(root)
            package = source_root / "com/example/app"
            package.mkdir(parents=True)

            changed = package / "Changed.py"
            equal = package / "Equal.py"
            older = package / "Older.py"
            missing = package / "Missing.py"
            for source in (changed, equal, older, missing):
                _ = source.write_text(f"class {source.stem}:\n    pass\n")

            jasmin_package = target_root / "j/com/example/app"
            jasmin_package.mkdir(parents=True)
            changed_output = jasmin_package / "Changed.j"
            equal_output = jasmin_package / "Equal.j"
            older_output = jasmin_package / "Older.j"
            _ = changed_output.write_text("; old\n")
            _ = equal_output.write_text("; current\n")
            _ = older_output.write_text("; current\n")
            _ = (root / "jars.db").write_bytes(b"")

            old_time = 1_700_000_000_000_000_000
            new_time = old_time + 1_000_000_000
            os.utime(changed_output, ns=(old_time, old_time))
            os.utime(changed, ns=(new_time, new_time))
            os.utime(equal_output, ns=(new_time, new_time))
            os.utime(equal, ns=(new_time, new_time))
            os.utime(older_output, ns=(new_time, new_time))
            os.utime(older, ns=(old_time, old_time))

            status, output, errors = self.run_compiler(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertIn("scanned: 4; modified: 2; compiled: 2", output)
            self.assertIn("Scanning sources", FakeProgress.initial_descriptions)
            self.assertIn("./src/com/example/app/Changed.py", FakeProgress.descriptions)
            self.assertIn("./src/com/example/app/Missing.py", FakeProgress.descriptions)
            self.assertNotIn("./src/com/example/app/Equal.py", FakeProgress.descriptions)
            self.assertNotIn("./src/com/example/app/Older.py", FakeProgress.descriptions)
            self.assertIn(".class public com/example/app/Changed", changed_output.read_text())
            self.assertTrue((jasmin_package / "Missing.j").is_file())
            self.assertEqual(equal_output.read_text(), "; current\n")
            self.assertEqual(older_output.read_text(), "; current\n")


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_fstrings_builtins_and_constant_comparisons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "jars.db"
            _alias_catalog(database)
            source = root / "src" / "Sample.py"
            source.parent.mkdir(parents=True)
            _ = source.write_text(
                "\n".join(
                    (
                        "from java.lang import CharSequence, String",
                        "",
                        "class Sample:",
                        "    def run(self, latitude: double, count: int, label: str) -> None:",
                        '        plain = f"https://example.test/?lat={latitude}&n={count}"',
                        '        shaped = f"{label}-{count:04d}-{latitude:.2f}"',
                        '        padded = f"{count:04d}%"',
                        "        text = str(count)",
                        "        same = String.valueOf(count)",
                        "        number = int(label)",
                        "        width = float(label)",
                        "        height = double(label)",
                        "        size = len(label)",
                        '        items = ["a", "b"]',
                        "        count_items = len(items)",
                        "        magnitude = abs(count)",
                        "        smaller = min(count, 2)",
                        "        larger = max(count, 2)",
                        "        upper = label.upper()",
                        "        lower = label.lower()",
                        "        stripped = label.strip()",
                        '        ended = label.endswith("z")',
                        '        found = label.find("a")',
                        '        changed = label.replace("a", "b")',
                        '        parts = label.split(",")',
                        "",
                        "    def compare(self, left: int, right: int) -> None:",
                        "        if left == 1:",
                        "            pass",
                        "        if 1 == left:",
                        "            pass",
                        "        if left == right:",
                        "            pass",
                        "        if left != 1:",
                        "            pass",
                        "",
                        "    def match(self, folded: str) -> None:",
                        '        if folded == "commons":',
                        "            pass",
                        '        if folded != "commons":',
                        "            pass",
                        "",
                        "    def missing(self, folded: str) -> None:",
                        "        if folded == None:",
                        "            pass",
                        "",
                        "    def mixed(self, folded: str, count: int) -> None:",
                        "        if folded == count:",
                        "            pass",
                        "",
                        "    def widths(self, width: float, height: double, count: int) -> None:",
                        "        if width == 1.0:",
                        "            pass",
                        "        if height == count:",
                        "            pass",
                        "",
                        "    def greet(self, name: CharSequence) -> None:",
                        "        size = len(name)",
                        '        message = f"Hello, {name}"',
                        "",
                        "    def blanks(self, name: CharSequence, items: list[str]) -> None:",
                        "        if not name:",
                        "            pass",
                        "        if not items:",
                        "            pass",
                        "",
                    )
                )
            )
            output = root / ".py2apk" / "j" / "Sample.j"
            catalog = JarCatalog(database)
            try:
                compile_py2j(source, output, catalog, {})
            finally:
                catalog.close()
            jasmin = output.read_text()
            compare = jasmin.split(".method public compare", 1)[1].split(".end method", 1)[0]
            run = jasmin.split(".method public run", 1)[1].split(".end method", 1)[0]

            self.assertIn("new java/lang/StringBuilder", run)
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/append(Ljava/lang/String;)Ljava/lang/StringBuilder;",
                run,
            )
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/append(D)Ljava/lang/StringBuilder;",
                run,
            )
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/append(I)Ljava/lang/StringBuilder;",
                run,
            )
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/toString()Ljava/lang/String;",
                run,
            )
            self.assertIn('ldc "%s-%04d-%.2f"', run)
            self.assertIn('ldc "%04d%%"', run)
            self.assertIn(
                "invokestatic java/lang/String/format(Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/String;",
                run,
            )
            self.assertEqual(run.count("invokestatic java/lang/String/valueOf(I)Ljava/lang/String;"), 2)
            self.assertIn("invokestatic java/lang/Integer/parseInt(Ljava/lang/String;)I", run)
            self.assertIn("invokestatic java/lang/Float/parseFloat(Ljava/lang/String;)F", run)
            self.assertIn("invokestatic java/lang/Double/parseDouble(Ljava/lang/String;)D", run)
            self.assertIn("invokevirtual java/lang/String/length()I", run)
            self.assertIn("arraylength", run)
            self.assertIn("invokestatic java/lang/Math/abs(I)I", run)
            self.assertIn("invokestatic java/lang/Math/min(II)I", run)
            self.assertIn("invokestatic java/lang/Math/max(II)I", run)
            self.assertIn("invokevirtual java/lang/String/toUpperCase()Ljava/lang/String;", run)
            self.assertIn("invokevirtual java/lang/String/toLowerCase()Ljava/lang/String;", run)
            self.assertIn("invokevirtual java/lang/String/trim()Ljava/lang/String;", run)
            self.assertIn("invokevirtual java/lang/String/endsWith(Ljava/lang/String;)Z", run)
            self.assertIn("invokevirtual java/lang/String/indexOf(Ljava/lang/String;)I", run)
            self.assertIn(
                "invokevirtual java/lang/String/replace(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                run,
            )
            self.assertIn(
                "invokevirtual java/lang/String/split(Ljava/lang/String;)[Ljava/lang/String;",
                run,
            )
            self.assertEqual(compare.count("iconst_1\n    iload 1\n    if_icmpne"), 2)
            self.assertIn("iconst_1\n    iload 1\n    if_icmpeq", compare)
            self.assertIn("iload 1\n    iload 2\n    if_icmpne", compare)
            match = jasmin.split(".method public match", 1)[1].split(".end method", 1)[0]
            self.assertIn("invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z", match)
            self.assertIn("ifnull", match)
            self.assertIn("ifeq", match)
            self.assertIn("ifne", match)
            missing = jasmin.split(".method public missing", 1)[1].split(".end method", 1)[0]
            self.assertIn("aconst_null", missing)
            self.assertIn("ifnull", missing)
            self.assertIn("invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z", missing)
            mixed = jasmin.split(".method public mixed", 1)[1].split(".end method", 1)[0]
            self.assertNotIn("equals", mixed)
            self.assertIn("pop", mixed)
            widths = jasmin.split(".method public widths", 1)[1].split(".end method", 1)[0]
            self.assertIn("fcmpg", widths)
            self.assertIn("i2d", widths)
            self.assertIn("dcmpg", widths)
            greet = jasmin.split(".method public greet", 1)[1].split(".end method", 1)[0]
            self.assertIn("invokeinterface java/lang/CharSequence/length()I 1", greet)
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/append(Ljava/lang/CharSequence;)Ljava/lang/StringBuilder;",
                greet,
            )
            blanks = jasmin.split(".method public blanks", 1)[1].split(".end method", 1)[0]
            self.assertIn("ifnull", blanks)
            self.assertIn("invokeinterface java/lang/CharSequence/length()I 1", blanks)
            self.assertIn("arraylength", blanks)
            with self.assertRaises(JasminCompilerError):
                _ = _compiled_class(
                    "\n".join(
                        (
                            "class Host:",
                            "    def run(self, count: int) -> None:",
                            "        if not count:",
                            "            pass",
                            "",
                        )
                    )
                )

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
        primitive = jasmin.split(".method public primitive", 1)[1].split(".end method", 1)[0]
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

    def test_rejects_unsupported_format_specs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "jars.db"
            _alias_catalog(database)
            source = root / "src" / "Bad.py"
            source.parent.mkdir(parents=True)
            _ = source.write_text(
                "\n".join(
                    (
                        "class Bad:",
                        "    def run(self, count: int) -> None:",
                        '        text = f"{count:+d}"',
                        "",
                    )
                )
            )
            catalog = JarCatalog(database)
            try:
                with self.assertRaises(JasminCompilerError) as raised:
                    compile_py2j(source, root / "Bad.j", catalog, {})
            finally:
                catalog.close()
            self.assertIn("format spec '+d' is not supported", str(raised.exception))

    def test_lowers_lists_and_maps(self) -> None:
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
                        "from android.app import Activity",
                        "",
                        "class Host(Activity):",
                        "    def direct(self) -> None:",
                        '        self.requestPermissions(["android.permission.ACCESS_COARSE_LOCATION"], 1)',
                        "",
                        "    def stored(self) -> None:",
                        '        items = ["a", "b"]',
                        "        count = len(items)",
                        "        numbers = [1, 2, 3]",
                        '        pairs = {"a": 1}',
                        "",
                        "    def changed(self) -> None:",
                        '        values = ["a", "b"]',
                        '        values.append("c")',
                        "        if not values:",
                        "            pass",
                        "        self.requestPermissions(values, 1)",
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
            direct = jasmin.split(".method public direct", 1)[1].split(".end method", 1)[0]
            stored = jasmin.split(".method public stored", 1)[1].split(".end method", 1)[0]
            changed = jasmin.split(".method public changed", 1)[1].split(".end method", 1)[0]
            self.assertIn("anewarray java/lang/String", direct)
            self.assertNotIn("ArrayList", direct)
            self.assertNotIn("toArray", direct)
            self.assertIn("anewarray java/lang/String", stored)
            self.assertIn("arraylength", stored)
            self.assertIn("newarray int", stored)
            self.assertIn("new java/util/LinkedHashMap", stored)
            self.assertIn(
                "invokevirtual java/util/LinkedHashMap/put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                stored,
            )
            self.assertNotIn("ArrayList", stored)
            self.assertIn("new java/util/ArrayList", changed)
            self.assertIn("invokevirtual java/util/ArrayList/add(Ljava/lang/Object;)Z", changed)
            self.assertIn("invokevirtual java/util/ArrayList/size()I", changed)
            self.assertIn(
                "invokevirtual java/util/ArrayList/toArray([Ljava/lang/Object;)[Ljava/lang/Object;",
                changed,
            )
            self.assertIn("checkcast [Ljava/lang/String;", changed)

            mixed = root / "src" / "Mixed.py"
            _ = mixed.write_text(
                "\n".join(
                    (
                        "class Mixed:",
                        "    def run(self) -> None:",
                        '        values = [1, "a"]',
                        "",
                    )
                )
            )
            catalog = JarCatalog(database)
            try:
                with self.assertRaises(JasminCompilerError) as raised:
                    compile_py2j(mixed, root / "Mixed.j", catalog, {})
            finally:
                catalog.close()
            self.assertIn("one element type", str(raised.exception))

            mixed_map = root / "src" / "MixedMap.py"
            _ = mixed_map.write_text(
                "\n".join(
                    (
                        "class MixedMap:",
                        "    def run(self) -> None:",
                        '        pairs = {"a": 1, "b": "c"}',
                        "",
                    )
                )
            )
            catalog = JarCatalog(database)
            try:
                with self.assertRaises(JasminCompilerError) as raised_map:
                    compile_py2j(mixed_map, root / "MixedMap.j", catalog, {})
            finally:
                catalog.close()
            self.assertIn("one key type and one value type", str(raised_map.exception))

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
            self.assertIn("invokeinterface java/util/concurrent/ExecutorService/execute", nested)
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
                        "        field.addTextChangedListener(lambda text: self.note(\"ok\"))",
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
            self.assertIn(".method public afterTextChanged(Landroid/text/Editable;)V", text_watcher)
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

    def test_lowers_integer_addition_while_and_unpack(self) -> None:
        _assert_integer_loop(self)

    def test_lowers_yield_to_iterator(self) -> None:
        _assert_yield_iterator(self)

    def test_iterator_annotation_is_java_util_iterator(self) -> None:
        _assert_iterator_alias(self)

    def test_type_checking_builtin_annotation_stays_primitive(self) -> None:
        text = _compiled_class(
            "\n".join(
                (
                    "from typing import TYPE_CHECKING",
                    "",
                    "if TYPE_CHECKING:",
                    "    double: float",
                    "",
                    "class Host:",
                    "    def widen(self, latitude: double) -> double:",
                    "        return double(1)",
                    "",
                )
            )
        )
        self.assertIn("widen(D)D", text)
        self.assertIn("i2d", text)
        module_level = _compiled_class(
            "\n".join(
                (
                    "double = float",
                    "",
                    "class Host:",
                    "    def widen(self, latitude: double) -> double:",
                    "        return double(1)",
                    "",
                )
            )
        )
        self.assertIn("widen(D)D", module_level)
        self.assertIn("i2d", module_level)

    def test_conditional_expression_branches_on_the_truth_test(self) -> None:
        text = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def greet(self, name: str) -> str:",
                    '        return f"Hello, {name}" if name else "Hello, world"',
                    "",
                )
            )
        )
        self.assertIn("Hello, world", text)
        self.assertIn("Hello, ", text)
        self.assertIn("goto", text)
        false_arm = text.index("Hello, world")
        self.assertLess(text.index("ifeq"), false_arm)

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

    def test_lowers_for_over_arrays_slices_and_generators(self) -> None:
        _assert_for_loops(self)

    def test_resolves_stdlib_fallbacks(self) -> None:
        _assert_stdlib_fallback(self)

    def test_caches_stdlib_jasmin_and_calls_runtime_helpers(self) -> None:
        _assert_stdlib_cache(self)
        _assert_slice_and_chr_calls(self)

    def test_lowers_break_continue_else_range_and_tuples(self) -> None:
        _assert_loop_controls(self)

    def test_lowers_try_raise_and_with(self) -> None:
        _assert_try_raise_with(self)

    def test_lowers_contextmanager_closing_and_suppress(self) -> None:
        _assert_context_managers(self)


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
        _ = (project / "src" / "bisect.py").write_text("def marker() -> int:\n    return 1\n")
        test.assertEqual(resolve_module("bisect", project), project / "src" / "bisect.py")
        empty = root / "empty"
        (empty / "src").mkdir(parents=True)
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


def _compiled_outputs(source_text: str, name: str = "Host") -> dict[str, str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        texts = {output.name: output.read_text()}
        for path in output.parent.glob("*.j"):
            texts[path.name] = path.read_text()
        return texts


def _compiled_named(source: Path) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        output = root / f"{source.stem}.j"
        project = index_project_types(root / "src", [source])
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        parts = [output.read_text()]
        parts.extend(path.read_text() for path in output.parent.glob("*.j") if path != output)
        return "\n".join(parts)


def _compiled_with_stdlib(source_text: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / "Host.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / "Host.j"
        extra = list(Path("Py2APK/stdlib").rglob("*.py"))
        project = index_project_types(root / "src", extra)
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        return output.read_text()


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
    test.assertIn("invokestatic java/lang/Integer/valueOf(I)Ljava/lang/Integer;", counted)
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
    test.assertIn("invokestatic java/lang/Integer/valueOf(I)Ljava/lang/Integer;", fibonacci)
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


def _compiled_indexed(source_text: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / "Host.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / "Host.j"
        project = index_project_types(root / "src")
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        return output.read_text()


def _compiled_class(source_text: str, name: str = "Host") -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        return output.read_text()


def _compiled_generator(name: str, source_text: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        return output.read_text(), (output.parent / f"{name}$gen$1.j").read_text()


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


def _assert_region_default(
    test: unittest.TestCase,
    method: str,
    start: str,
    after: str,
) -> None:
    region = method[method.index(start):method.index(after)]
    store = region.split("aconst_null", 1)[1].strip().splitlines()[0].strip()
    test.assertTrue(store.startswith("astore "))
    slot = store.split()[1]
    test.assertGreaterEqual(region.count(f"astore {slot}"), 2)
    test.assertIn(f"aload {slot}", method[method.index(after):])


def _assert_try_raise_with(test: unittest.TestCase) -> None:
    joined = _compiled_indexed(
        "\n".join(
            (
                "from java.lang import Object",
                "",
                "class Host:",
                "    def __enter__(self) -> int:",
                "        return 1",
                "",
                "    def __exit__(self, exc_type: Object, exc_value: Object, traceback: Object) -> bool:",
                "        return False",
                "",
                "    def use(self, value: Host) -> None:",
                "        pass",
                "",
                "    def use_count(self, value: int) -> None:",
                "        pass",
                "",
                "    def run(self) -> None:",
                "        with self:",
                "            kept = self",
                "        self.use(kept)",
                "",
                "    def counted(self) -> None:",
                "        with self:",
                "            kept = 1",
                "        self.use_count(kept)",
                "",
                "    def prior(self) -> None:",
                "        kept = self",
                "        with self:",
                "            pass",
                "        self.use(kept)",
                "",
                "    def tried(self) -> None:",
                "        try:",
                "            kept = self",
                "        except:",
                "            pass",
                "        self.use(kept)",
                "",
            )
        )
    )
    _assert_region_default(
        test,
        joined.split(".method public run", 1)[1],
        "L_with_",
        "L_withafter_",
    )
    counted = joined.split(".method public counted", 1)[1]
    test.assertLess(counted.index("iconst_0"), counted.index("iconst_1"))
    test.assertGreaterEqual(counted.count("istore"), 2)
    prior = joined.split(".method public prior", 1)[1]
    protected = prior[prior.index("L_with_"):prior.index("L_withend_")]
    test.assertNotIn("aconst_null", protected)
    _assert_region_default(
        test,
        joined.split(".method public tried", 1)[1],
        "L_try_",
        "L_aftertry_",
    )
    raised = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        raise Error()",
                "",
            )
        )
    )
    test.assertIn("athrow", raised)
    test.assertNotIn("astore", raised)
    caught = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        try:",
                "            raise Error()",
                "        except Error as error:",
                "            raise",
                "",
            )
        )
    )
    test.assertIn(".catch py2apk/Error", caught)
    stored = caught.index("astore")
    slot = caught[stored:].split()[1]
    test.assertIn(f"aload {slot}", caught)
    caused = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        other = Error()",
                "        raise Error() from other",
                "",
            )
        )
    )
    test.assertIn("putfield py2apk/Error/cause Ljava/lang/Exception;", caused)
    read = _compiled_with_stdlib(
        "\n".join(
            (
                "from java.lang import Exception",
                "from py2apk import Error",
                "",
                "class Host:",
                "    def read(self) -> Exception:",
                "        try:",
                "            raise Error()",
                "        except Error as error:",
                "            return error.cause",
                "        return Error()",
                "",
            )
        )
    )
    test.assertIn("getfield py2apk/Error/cause Ljava/lang/Exception;", read)
    managed = _compiled_indexed(
        "\n".join(
            (
                "from java.lang import Throwable",
                "",
                "class Host:",
                "    def __enter__(self) -> Host:",
                "        return self",
                "",
                "    def __exit__(self, exc_type: Throwable, exc_value: Throwable, traceback: Throwable) -> bool:",
                "        return False",
                "",
                "    def run(self) -> None:",
                "        with Host() as item:",
                "            pass",
                "",
            )
        )
    )
    test.assertIn("invokevirtual Host/__enter__()LHost;", managed)
    test.assertGreaterEqual(managed.count("invokevirtual Host/__exit__(Ljava/lang/Throwable;Ljava/lang/Throwable;Ljava/lang/Throwable;)Z"), 2)
    with test.assertRaises(JasminCompilerError):
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    "        raise",
                    "",
                )
            )
        )


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
    test.assertIn("invokespecial contextlib/nullcontext/<init>(Ljava/lang/Object;)V", managed)
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


def _alias_catalog(database: Path) -> None:
    string = "Ljava/lang/String;"
    builder = "Ljava/lang/StringBuilder;"
    connection = sqlite3.connect(database)
    connection.executescript(MEMBER_SCHEMA)
    methods = (
        ("java/lang/String", "valueOf", "I", string, 1, "invokestatic"),
        ("java/lang/String", "format", string + "[Ljava/lang/Object;", string, 1, "invokestatic"),
        ("java/lang/String", "length", "", "I", 0, "invokevirtual"),
        ("java/lang/CharSequence", "length", "", "I", 0, "invokeinterface"),
        ("java/lang/String", "toUpperCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "toLowerCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "trim", "", string, 0, "invokevirtual"),
        ("java/lang/String", "endsWith", string, "Z", 0, "invokevirtual"),
        ("java/lang/String", "indexOf", string, "I", 0, "invokevirtual"),
        ("java/lang/String", "replace", string + string, string, 0, "invokevirtual"),
        ("java/lang/String", "split", string, "[Ljava/lang/String;", 0, "invokevirtual"),
        ("java/lang/StringBuilder", "<init>", "", "V", 0, "invokespecial"),
        ("java/lang/StringBuilder", "append", string, builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "Ljava/lang/CharSequence;", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "I", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "D", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "toString", "", string, 0, "invokevirtual"),
        ("java/lang/AutoCloseable", "close", "", "V", 0, "invokeinterface"),
        ("java/lang/Integer", "parseInt", string, "I", 1, "invokestatic"),
        ("java/lang/Integer", "valueOf", "I", "Ljava/lang/Integer;", 1, "invokestatic"),
        ("java/lang/Float", "parseFloat", string, "F", 1, "invokestatic"),
        ("java/lang/Double", "parseDouble", string, "D", 1, "invokestatic"),
        ("java/lang/Double", "valueOf", "D", "Ljava/lang/Double;", 1, "invokestatic"),
        ("java/lang/Math", "abs", "I", "I", 1, "invokestatic"),
        ("java/lang/Math", "min", "II", "I", 1, "invokestatic"),
        ("java/lang/Math", "max", "II", "I", 1, "invokestatic"),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        methods,
    )
    connection.commit()
    connection.close()


def _listener_catalog(database: Path) -> None:
    connection = sqlite3.connect(database)
    _ = connection.executemany(
        "INSERT INTO classes (internal, super_internal, is_interface) VALUES (?, ?, ?)",
        (
            ("java/lang/Runnable", "java/lang/Object", 1),
            ("java/util/concurrent/ExecutorService", "java/lang/Object", 1),
            ("android/view/View$OnClickListener", "java/lang/Object", 1),
            ("okhttp3/Callback", "java/lang/Object", 1),
            ("android/text/TextWatcher", "java/lang/Object", 1),
            ("android/widget/EditText", "java/lang/Object", 0),
        ),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("java/lang/Runnable", "run", "", "V", 0, "invokeinterface"),
            ("java/util/concurrent/ExecutorService", "execute", "Ljava/lang/Runnable;", "V", 0, "invokeinterface"),
            ("android/os/Handler", "post", "Ljava/lang/Runnable;", "Z", 0, "invokevirtual"),
            ("android/widget/Button", "setOnClickListener", "Landroid/view/View$OnClickListener;", "V", 0, "invokevirtual"),
            ("android/view/View$OnClickListener", "onClick", "Landroid/view/View;", "V", 0, "invokeinterface"),
            ("okhttp3/Call", "enqueue", "Lokhttp3/Callback;", "V", 0, "invokeinterface"),
            ("okhttp3/Callback", "onResponse", "Lokhttp3/Call;Lokhttp3/Response;", "V", 0, "invokeinterface"),
            ("okhttp3/Callback", "onFailure", "Lokhttp3/Call;Ljava/io/IOException;", "V", 0, "invokeinterface"),
            ("android/widget/EditText", "addTextChangedListener", "Landroid/text/TextWatcher;", "V", 0, "invokevirtual"),
            ("android/text/TextWatcher", "afterTextChanged", "Landroid/text/Editable;", "V", 0, "invokeinterface"),
            ("android/text/TextWatcher", "beforeTextChanged", "Ljava/lang/CharSequence;III", "V", 0, "invokeinterface"),
            ("android/text/TextWatcher", "onTextChanged", "Ljava/lang/CharSequence;III", "V", 0, "invokeinterface"),
        ),
    )
    connection.commit()
    connection.close()


def _collection_catalog(database: Path) -> None:
    connection = sqlite3.connect(database)
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("java/util/ArrayList", "<init>", "", "V", 0, "invokespecial"),
            ("java/util/ArrayList", "add", "Ljava/lang/Object;", "Z", 0, "invokevirtual"),
            ("java/util/ArrayList", "size", "", "I", 0, "invokevirtual"),
            ("java/util/ArrayList", "toArray", "[Ljava/lang/Object;", "[Ljava/lang/Object;", 0, "invokevirtual"),
            ("java/util/LinkedHashMap", "<init>", "", "V", 0, "invokespecial"),
            (
                "java/util/LinkedHashMap",
                "put",
                "Ljava/lang/Object;Ljava/lang/Object;",
                "Ljava/lang/Object;",
                0,
                "invokevirtual",
            ),
            ("android/app/Activity", "requestPermissions", "[Ljava/lang/String;I", "V", 0, "invokevirtual"),
        ),
    )
    connection.commit()
    connection.close()


if __name__ == "__main__":
    _ = unittest.main()
