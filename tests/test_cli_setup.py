from __future__ import annotations

import contextlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from Py2APK import cli_setup


class SetupCommandTests(unittest.TestCase):
    def test_detects_writable_assets_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            assets = Path(temporary_directory)
            (assets / "templates").mkdir()

            with mock.patch.object(
                cli_setup,
                "asset_directories",
                return_value=[assets],
            ):
                destination = cli_setup.default_jars_directory()

            self.assertEqual(destination, assets / "jars")

    def test_caches_maven_and_release_jars_then_symlinks_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "jars"
            cache = root / "cache"

            def fetch(repos: list[str], relative: str, store: Path) -> bytes:
                self.assertEqual(store, cache)
                self.assertEqual(repos[-1], "https://repo.maven.apache.org/maven2")
                if relative.startswith("com/android/tools/r8/"):
                    self.assertEqual(repos[0], cli_setup.GOOGLE_MAVEN)
                destination_file = store / "maven" / relative
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                _ = destination_file.write_bytes(b"jar")
                return b"jar"

            def download(jobs, on_complete=None) -> list[str]:
                for url, path in jobs:
                    self.assertTrue(url.startswith("https://github.com/"))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _ = path.write_bytes(b"jar")
                    if on_complete is not None:
                        on_complete()
                return []

            with (
                mock.patch("Py2APK.cli_setup.cache_root", return_value=cache),
                mock.patch("Py2APK.cli_setup._fetch", side_effect=fetch),
                mock.patch("Py2APK.cli_setup.download_files", side_effect=download),
                mock.patch("Py2APK.cli_setup.ensure_keystore", return_value=root / "default.keystore"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = cli_setup.download_jars(destination)

            self.assertEqual(status, 0)
            expected = [
                "apktool_3.0.3.jar",
                "jasmin-3.0.3-jar-with-dependencies.jar",
                "r8-9.4.24.jar",
                "uber-apk-signer-1.3.0.jar",
            ]
            for name in expected:
                link = destination / name
                self.assertTrue(link.is_symlink(), name)
                self.assertTrue(link.resolve().is_file(), name)

    def test_creates_a_release_keystore_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            commands: list[list[str]] = []

            def run(command: list[str], check: bool, capture_output: bool, text: bool):
                self.assertFalse(check)
                self.assertTrue(capture_output)
                self.assertTrue(text)
                commands.append(command)
                keystore = Path(command[command.index("-keystore") + 1])
                _ = keystore.write_bytes(b"keystore")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("Py2APK.cli_setup.subprocess.run", side_effect=run):
                first = cli_setup.ensure_keystore(root)
                second = cli_setup.ensure_keystore(root)

            self.assertEqual(first, root / "default.keystore")
            self.assertEqual(second, first)
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertEqual(command[command.index("-alias") + 1], "py2apk")
            self.assertEqual(command[command.index("-keyalg") + 1], "RSA")
            self.assertEqual(command[command.index("-keysize") + 1], "2048")
            self.assertEqual(command[command.index("-validity") + 1], "10000")
            self.assertEqual(command[command.index("-storetype") + 1], "PKCS12")
            self.assertIn("CN=Py2APK Release", command[command.index("-dname") + 1])
            password = (root / "default.keystore.pass").read_text(encoding="utf-8").strip()
            self.assertEqual(command[command.index("-storepass") + 1], password)
            self.assertEqual(command[command.index("-keypass") + 1], password)
            self.assertEqual(stat.S_IMODE(os.stat(first).st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(os.stat(root / "default.keystore.pass").st_mode),
                0o600,
            )


if __name__ == "__main__":
    _ = unittest.main()
