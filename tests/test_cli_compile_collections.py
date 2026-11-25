from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _collection_catalog,
)

from Py2APK.compiler_j import JasminCompilerError, compile_py2j
from Py2APK.jar_index import JarCatalog


class AliasLoweringTests(unittest.TestCase):
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
            direct = jasmin.split(".method public direct", 1)[1].split(
                ".end method", 1
            )[0]
            stored = jasmin.split(".method public stored", 1)[1].split(
                ".end method", 1
            )[0]
            changed = jasmin.split(".method public changed", 1)[1].split(
                ".end method", 1
            )[0]
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
            self.assertIn(
                "invokevirtual java/util/ArrayList/add(Ljava/lang/Object;)Z", changed
            )
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
