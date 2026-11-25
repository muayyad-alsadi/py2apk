from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _collection_catalog,
    _compiled_class,
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
            self.assertIn("a list needs one element type", str(raised.exception))

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


def _collection_source(body: str, preamble: str = "") -> str:
    lines = ["class Host:", "    def run(self) -> None:"]
    lines.extend(f"        {line}" if line else "" for line in body.splitlines())
    joined = "\n".join(lines)
    return f"{preamble}{joined}\n"


class SetLiteralTests(unittest.TestCase):
    def test_lowers_a_string_set(self) -> None:
        jasmin = _compiled_class(
            _collection_source('values = {"a", "b"}'), collections=True
        )
        self.assertIn("new java/util/HashSet", jasmin)
        self.assertEqual(
            jasmin.count(
                "invokevirtual java/util/HashSet/add(Ljava/lang/Object;)Z"
            ),
            2,
        )
        self.assertNotIn("anewarray", jasmin)
        self.assertNotIn("ArrayList", jasmin)

    def test_boxes_int_elements(self) -> None:
        jasmin = _compiled_class(
            _collection_source("values = {1, 2}"), collections=True
        )
        self.assertIn(
            "invokestatic java/lang/Integer/valueOf(I)Ljava/lang/Integer;", jasmin
        )
        self.assertEqual(jasmin.count("Integer/valueOf"), 2)

    def test_rejects_a_mixed_set(self) -> None:
        source = _collection_source('values = {1, "a"}')
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(source, collections=True)
        self.assertIn("a set needs one element type", str(raised.exception))

    def test_rejects_a_null_element(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source("values = {None}"), collections=True)
        self.assertIn("a set element needs a value type", str(raised.exception))

    def test_rejects_unpacking(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source("values = {*items}"), collections=True)
        self.assertIn("set unpacking is unsupported", str(raised.exception))

    def test_len_and_truth_use_size(self) -> None:
        jasmin = _compiled_class(
            _collection_source(
                "\n".join(
                    (
                        'values = {"a", "b"}',
                        "count = len(values)",
                        "if not values:",
                        "    pass",
                        "if values:",
                        "    pass",
                    )
                )
            ),
            collections=True,
        )
        self.assertIn("invokevirtual java/util/HashSet/size()I", jasmin)
        self.assertIn("ifne", jasmin)
        self.assertIn("ifeq", jasmin)

    def test_set_builtin_stays_unsupported(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source("values = set()"), collections=True)
        self.assertIn("unsupported builtin set", str(raised.exception))

    def test_stores_a_set_on_a_hashset_field(self) -> None:
        source = "\n".join(
            (
                "from java.util import HashSet",
                "",
                "class Host:",
                "    items: HashSet",
                "    def run(self) -> None:",
                '        self.items = {"a", "b"}',
                "",
            )
        )
        jasmin = _compiled_class(source, collections=True)
        self.assertIn("new java/util/HashSet", jasmin)
        self.assertIn("putfield Host/items Ljava/util/HashSet;", jasmin)

    def test_passes_a_set_as_object(self) -> None:
        jasmin = _compiled_class(
            _collection_source(
                "\n".join(
                    (
                        'values = {"a", "b"}',
                        "Objects.requireNonNull(values)",
                    )
                ),
                preamble="from java.util import Objects\n\n",
            ),
            collections=True,
        )
        self.assertIn(
            "invokestatic java/util/Objects/requireNonNull(Ljava/lang/Object;)Ljava/lang/Object;",
            jasmin,
        )
