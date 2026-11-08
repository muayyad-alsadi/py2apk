from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from Py2APK.cli_build import build_project


class BuildCommandTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        _ = (root / "AndroidManifest.xml").write_text("<manifest package='new' />")
        _ = (root / "apktool.yml").write_text("apkFileName: app.apk\n")
        (root / "src").mkdir()
        target = root / "target"
        (target / "j/com/example").mkdir(parents=True)
        _ = (target / "j/com/example/Main.j").write_text("; jasmin\n")
        target_manifest = target / "AndroidManifest.xml"
        target_config = target / "apktool.yml"
        _ = target_manifest.write_text("<manifest package='old' />")
        _ = target_config.write_text("apkFileName: old.apk\n")
        old_time = 1_700_000_000_000_000_000
        new_time = old_time + 1_000_000_000
        for path in (target_manifest, target_config):
            os.utime(path, ns=(old_time, old_time))
        for path in (root / "AndroidManifest.xml", root / "apktool.yml"):
            os.utime(path, ns=(new_time, new_time))

    def test_compile_failure_stops_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                mock.patch("Py2APK.cli_build.fetch_jars", return_value=0),
                mock.patch("Py2APK.cli_build.compile_project", return_value=1),
                mock.patch("Py2APK.cli_build._run_command") as run_command,
            ):
                status = build_project(root)

            self.assertEqual(status, 1)
            run_command.assert_not_called()

    def test_missing_index_fetches_jars_before_compile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_project(root)
            order: list[str] = []

            def fetch(project_root: Path) -> int:
                self.assertEqual(project_root, root.resolve())
                order.append("fetch")
                _ = (project_root / "jars.db").write_bytes(b"")
                return 0

            def compile_first(project_root: Path) -> int:
                order.append("compile")
                return 1

            with (
                mock.patch("Py2APK.cli_build.fetch_jars", side_effect=fetch),
                mock.patch("Py2APK.cli_build.compile_project", side_effect=compile_first),
            ):
                status = build_project(root)

            self.assertEqual(status, 1)
            self.assertEqual(order, ["fetch", "compile"])

    def test_missing_toolchain_reports_setup_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_project(root)
            assets = root / "assets"
            jars_dir = assets / "jars"
            jars_dir.mkdir(parents=True)
            errors = io.StringIO()

            with (
                mock.patch("Py2APK.cli_build.fetch_jars", return_value=0),
                mock.patch("Py2APK.cli_build.compile_project", return_value=0),
                mock.patch(
                    "Py2APK.cli_build.asset_directories",
                    return_value=[assets],
                ),
                contextlib.redirect_stderr(errors),
            ):
                status = build_project(root)

            self.assertEqual(status, 1)
            self.assertIn(str(jars_dir.resolve()), errors.getvalue())
            self.assertIn(
                f"py2apk setup -d {jars_dir.resolve()}",
                errors.getvalue(),
            )

    def test_runs_compile_jasmin_d8_and_apktool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.make_project(root)
            tools = SimpleNamespace(
                apktool=Path("/tools/apktool.jar"),
                jasmin=Path("/tools/jasmin.jar"),
                r8=Path("/tools/r8.jar"),
                signer=Path("/tools/uber-apk-signer.jar"),
            )
            events: list[str] = []

            def compile_first(project_root: Path) -> int:
                self.assertEqual(project_root, root.resolve())
                events.append("compile")
                return 0

            def run_stage(command: list[str], project_root: Path, visible: list[str] | None = None) -> int:
                self.assertEqual(project_root, root.resolve())
                if "-d" in command:
                    events.append("jasmin")
                    class_file = root / "target/classes/com/example/Main.class"
                    class_file.parent.mkdir(parents=True)
                    _ = class_file.write_bytes(b"class")
                elif "com.android.tools.r8.D8" in command:
                    events.append("d8")
                    _ = (root / "target/dex/classes.dex").write_bytes(b"dex")
                elif "--ks" in command:
                    events.append("sign")
                    self.assertIn(str(root / "default.keystore"), command)
                    self.assertEqual(command[command.index("--ksPass") + 1], "secret")
                    self.assertIsNotNone(visible)
                    assert visible is not None
                    self.assertEqual(visible[visible.index("--ksPass") + 1], "***")
                    self.assertNotIn("secret", visible)
                    signed_dir = root / "target/sign"
                    signed_dir.mkdir(parents=True, exist_ok=True)
                    _ = (signed_dir / "app-aligned-signed.apk").write_bytes(b"signed")
                else:
                    events.append("apktool")
                    apk = root / "target/dist/app.apk"
                    apk.parent.mkdir(parents=True)
                    _ = apk.write_bytes(b"apk")
                    _ = (apk.parent / "app-aligned-signed.apk").write_bytes(b"signed")
                return 0

            keystore = root / "default.keystore"
            _ = keystore.write_bytes(b"keystore")
            _ = (root / "default.keystore.pass").write_text("secret\n", encoding="utf-8")
            output = io.StringIO()
            errors = io.StringIO()
            with (
                mock.patch("Py2APK.cli_build.fetch_jars", return_value=0),
                mock.patch("Py2APK.cli_build.compile_project", side_effect=compile_first),
                mock.patch("Py2APK.cli_build._find_toolchain", return_value=tools),
                mock.patch("Py2APK.cli_build.ensure_keystore", return_value=keystore),
                mock.patch("Py2APK.cli_build._run_command", side_effect=run_stage),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                status = build_project(root)

            self.assertEqual(status, 0)
            self.assertEqual(errors.getvalue(), "")
            self.assertEqual(events, ["compile", "jasmin", "d8", "apktool", "sign"])
            self.assertIn("built: target/dist/app.apk", output.getvalue())
            self.assertIn("signed: target/dist/app.signed.apk", output.getvalue())
            self.assertNotIn("secret", output.getvalue())
            self.assertNotIn("app-aligned-signed.apk", output.getvalue())
            self.assertEqual((root / "target/dist/app.signed.apk").read_bytes(), b"signed")
            self.assertEqual(
                (root / "target/AndroidManifest.xml").read_text(),
                "<manifest package='new' />",
            )
            self.assertEqual(
                (root / "target/apktool.yml").read_text(),
                "apkFileName: app.apk\n",
            )
            self.assertTrue((root / "target/classes.dex").is_file())


if __name__ == "__main__":
    _ = unittest.main()
