from __future__ import annotations

import contextlib
import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from Py2APK import cli


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = cli.main(args)
        return status, output.getvalue(), errors.getvalue()

    def test_no_command_prints_help(self) -> None:
        status, output, errors = self.run_cli()

        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("COMMAND", output)
        self.assertIn("setup", output)
        self.assertIn("init", output)
        self.assertIn("compile", output)
        self.assertIn("build", output)

    def test_setup_forwards_destination(self) -> None:
        with mock.patch("Py2APK.cli.download_jars", return_value=0) as download_jars:
            status, output, errors = self.run_cli("setup", "--dst", "/tmp/jars")

        self.assertEqual(status, 0)
        self.assertEqual(output, "")
        self.assertEqual(errors, "")
        download_jars.assert_called_once_with(Path("/tmp/jars"))

    def test_init_recursively_copies_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "helloworld2"
            status, output, errors = self.run_cli(
                "init",
                "--template",
                "helloworld",
                "--icon",
                "file.svg",
                "--pkg",
                "com.example.myapp",
                "--name",
                "My First App",
                str(destination),
            )

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertIn(f"initialized: {destination}", output)
            self.assertIn("template: helloworld", output)
            self.assertIn("icon: file.svg", output)
            self.assertIn("package: com.example.myapp", output)
            self.assertIn("name: My First App", output)
            self.assertTrue(
                (
                    destination
                    / "src/com/example/helloworld/MainActivity.py"
                ).is_file()
            )
            self.assertTrue((destination / "target/apktool.yml").is_file())

            for filename in ("AndroidManifest.xml", "apktool.yml"):
                source_file = destination / filename
                target_file = destination / "target" / filename
                self.assertTrue(source_file.is_file())
                self.assertEqual(
                    source_file.stat().st_mtime_ns,
                    target_file.stat().st_mtime_ns,
                )
                self.assertEqual(
                    stat.S_IMODE(source_file.stat().st_mode),
                    stat.S_IMODE(target_file.stat().st_mode),
                )

    def test_init_preserves_an_empty_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "blank-project"
            status, _output, errors = self.run_cli(
                "init",
                "--template",
                "blank",
                "--icon",
                "file.svg",
                "--pkg",
                "com.example.blank",
                "--name",
                "Blank",
                str(destination),
            )

            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])

    def test_init_refuses_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "existing"
            destination.mkdir()
            marker = destination / "keep.txt"
            _ = marker.write_text("keep")

            status, output, errors = self.run_cli(
                "init",
                "--template",
                "helloworld",
                "--icon",
                "file.svg",
                "--pkg",
                "com.example.myapp",
                "--name",
                "My First App",
                str(destination),
            )

            self.assertEqual(status, 1)
            self.assertEqual(output, "")
            self.assertIn("destination already exists", errors)
            self.assertEqual(marker.read_text(), "keep")

    def test_init_rejects_an_unknown_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "unknown"
            status, output, errors = self.run_cli(
                "init",
                "--template",
                "missing",
                "--icon",
                "file.svg",
                "--pkg",
                "com.example.myapp",
                "--name",
                "My First App",
                str(destination),
            )

            self.assertEqual(status, 1)
            self.assertEqual(output, "")
            self.assertIn("unknown template 'missing'", errors)
            self.assertFalse(destination.exists())

    def test_build_defaults_to_current_directory(self) -> None:
        with mock.patch("Py2APK.cli.build_project", return_value=0) as build_project:
            status, output, errors = self.run_cli("build")

        self.assertEqual(status, 0)
        self.assertEqual(output, "")
        self.assertEqual(errors, "")
        build_project.assert_called_once_with(Path("."))


if __name__ == "__main__":
    _ = unittest.main()
