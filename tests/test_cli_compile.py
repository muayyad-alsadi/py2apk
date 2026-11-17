from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Generic, TypeVar
from unittest import mock

from Py2APK.cli_compile import compile_project
from Py2APK.compiler_j import JasminCompilerError, compile_py2j
from Py2APK.jar_index import MEMBER_SCHEMA, JarCatalog


T = TypeVar("T")


class FakeProgress(Generic[T]):
    descriptions: list[str] = []
    initial_descriptions: list[str] = []

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
                        "from java.lang import String",
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
            self.assertIn("one method", str(raised.exception))


def _alias_catalog(database: Path) -> None:
    string = "Ljava/lang/String;"
    builder = "Ljava/lang/StringBuilder;"
    connection = sqlite3.connect(database)
    connection.executescript(MEMBER_SCHEMA)
    methods = (
        ("java/lang/String", "valueOf", "I", string, 1, "invokestatic"),
        ("java/lang/String", "format", string + "[Ljava/lang/Object;", string, 1, "invokestatic"),
        ("java/lang/String", "length", "", "I", 0, "invokevirtual"),
        ("java/lang/String", "toUpperCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "toLowerCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "trim", "", string, 0, "invokevirtual"),
        ("java/lang/String", "endsWith", string, "Z", 0, "invokevirtual"),
        ("java/lang/String", "indexOf", string, "I", 0, "invokevirtual"),
        ("java/lang/String", "replace", string + string, string, 0, "invokevirtual"),
        ("java/lang/String", "split", string, "[Ljava/lang/String;", 0, "invokevirtual"),
        ("java/lang/StringBuilder", "<init>", "", "V", 0, "invokespecial"),
        ("java/lang/StringBuilder", "append", string, builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "I", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "D", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "toString", "", string, 0, "invokevirtual"),
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
