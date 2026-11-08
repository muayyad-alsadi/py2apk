from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Generic, TypeVar
from unittest import mock

from Py2APK.cli_compile import compile_project


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
        target_root = root / "target"
        target_root.mkdir()
        return source_root, target_root

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
            self.assertIn("./target", errors)

    def test_creates_jasmin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _ = self.make_project(root)

            status, output, errors = self.run_compiler(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertTrue((root / "target/j").is_dir())
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


if __name__ == "__main__":
    _ = unittest.main()
