from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _compile_helpers import FakeProgress, _alias_catalog

from Py2APK.cli_compile import compile_project
from Py2APK.jar_index import API_SCHEMA

_LENGTH_SOURCE = "\n".join(
    (
        "class Host:",
        "    def run(self, label: str) -> None:",
        "        size = label.length()",
        "",
    )
)


def _api_project(root: Path, min_sdk: int | None) -> None:
    _ = (root / "AndroidManifest.xml").write_text("<manifest />")
    (root / "src" / "com" / "example").mkdir(parents=True)
    _ = (root / "src" / "com" / "example" / "Host.py").write_text(_LENGTH_SOURCE)
    if min_sdk is not None:
        _ = (root / "apktool.yml").write_text(
            f"apkFileName: app.apk\nsdkInfo:\n  minSdkVersion: {min_sdk}\n"
        )
    database = root / "jars.db"
    _alias_catalog(database)
    connection = sqlite3.connect(database)
    _ = connection.executescript(API_SCHEMA)
    _ = connection.execute(
        "INSERT INTO api_levels (owner, member, since) VALUES (?, ?, ?)",
        ("java/lang/String", "length()I", 26),
    )
    connection.commit()
    connection.close()


class MinApiCompileTests(unittest.TestCase):
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

    def test_rejects_an_invoke_newer_than_min_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _api_project(root, min_sdk=21)

            status, _output, errors = self.run_compiler(root)

            self.assertEqual(status, 1)
            self.assertEqual(errors.count("error: "), 1)
            self.assertIn("java.lang.String.length requires API 26", errors)
            self.assertIn("minSdkVersion is 21", errors)
            self.assertIn("Host.py:3:", errors)

    def test_compiles_when_min_sdk_covers_the_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _api_project(root, min_sdk=26)

            status, _output, errors = self.run_compiler(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")

    def test_project_without_apktool_yml_skips_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _api_project(root, min_sdk=None)

            status, _output, errors = self.run_compiler(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")

    def test_lowering_min_sdk_recompiles_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _api_project(root, min_sdk=26)
            status, _output, errors = self.run_compiler(root)
            self.assertEqual((status, errors), (0, ""))
            output_file = root / ".py2apk" / "j" / "com" / "example" / "Host.j"
            self.assertTrue(output_file.is_file())

            config = root / "apktool.yml"
            _ = config.write_text(
                "apkFileName: app.apk\nsdkInfo:\n  minSdkVersion: 21\n"
            )
            later = output_file.stat().st_mtime_ns + 1_000_000_000
            os.utime(config, ns=(later, later))
            source = root / "src" / "com" / "example" / "Host.py"
            old = output_file.stat().st_mtime_ns - 1_000_000_000
            os.utime(source, ns=(old, old))

            status, _output, errors = self.run_compiler(root)

            self.assertEqual(status, 1)
            self.assertIn("requires API 26, minSdkVersion is 21", errors)


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
            self.assertNotIn(
                "./src/com/example/app/Equal.py", FakeProgress.descriptions
            )
            self.assertNotIn(
                "./src/com/example/app/Older.py", FakeProgress.descriptions
            )
            self.assertIn(
                ".class public com/example/app/Changed", changed_output.read_text()
            )
            self.assertTrue((jasmin_package / "Missing.j").is_file())
            self.assertEqual(equal_output.read_text(), "; current\n")
            self.assertEqual(older_output.read_text(), "; current\n")
