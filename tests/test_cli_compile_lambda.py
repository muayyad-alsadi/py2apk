from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _listener_catalog,
)

from Py2APK.compiler_j import JasminCompilerError, compile_py2j
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
