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


class SliceValueTests(unittest.TestCase):
    def test_string_step_one_uses_substring(self) -> None:
        jasmin = _compiled_class(
            _collection_source(
                "\n".join(
                    (
                        'text = "abcdefghij"',
                        "tail = text[1:]",
                        "head = text[:10]",
                        "middle = text[1:10]",
                        "whole = text[:]",
                        "neg = text[-2:]",
                        "back = text[5:2]",
                    )
                )
            )
        )
        self.assertIn("substring(II)Ljava/lang/String;", jasmin)
        self.assertIn("slice_start", jasmin)
        self.assertIn("slice_stop", jasmin)
        guard = jasmin.index("if_icmpgt")
        self.assertLess(guard, jasmin.index("substring", guard))

    def test_reversed_string_gathers_chars(self) -> None:
        jasmin = _compiled_class(_collection_source('text = "abcd"\nrev = text[::-1]\nstepped = text[::2]'))
        self.assertIn("charAt", jasmin)
        self.assertIn("newarray char", jasmin)
        self.assertIn("String/<init>([C)V", jasmin)
        self.assertNotIn("substring", jasmin)
        self.assertIn("if_icmple", jasmin)

    def test_runtime_string_step_gathers(self) -> None:
        jasmin = _compiled_class(
            _collection_source('step = 2\ntext = "abcd"\ntaken = text[::step]')
        )
        self.assertIn("charAt", jasmin)
        self.assertIn("IllegalArgumentException", jasmin)
        self.assertNotIn("substring", jasmin)

    def test_zero_step_is_rejected(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source('text = "abcd"\ntaken = text[::0]'))
        self.assertIn("slice step cannot be zero", str(raised.exception))

    def test_non_int_step_is_rejected(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source('text = "abcd"\ntaken = text[::"a"]'))
        self.assertIn("slice bound must be int", str(raised.exception))

    def test_int_array_step_one_copies(self) -> None:
        jasmin = _compiled_class(_collection_source("items = [1, 2, 3, 4]\ntaken = items[:2]"))
        self.assertIn("copyOfRange", jasmin)
        self.assertIn("slice_start", jasmin)
        self.assertNotIn("ArrayList", jasmin)

    def test_string_array_copies_and_casts(self) -> None:
        jasmin = _compiled_class(_collection_source('names = ["a", "b", "c"]\nhead = names[:2]'))
        self.assertIn("copyOfRange", jasmin)
        self.assertIn("checkcast [Ljava/lang/String;", jasmin)

    def test_array_list_slice_gathers(self) -> None:
        jasmin = _compiled_class(
            _collection_source("items = [1, 2, 3]\nitems.append(4)\ntaken = items[:2]"),
            collections=True,
        )
        self.assertIn("new java/util/ArrayList", jasmin)
        self.assertIn("add", jasmin)
        self.assertNotIn("subList", jasmin)
        self.assertNotIn("copyOfRange", jasmin)

    def test_reversed_int_array_gathers(self) -> None:
        jasmin = _compiled_class(_collection_source("items = [1, 2, 3]\nrev = items[::-1]"))
        self.assertIn("newarray int", jasmin)
        self.assertIn("iaload", jasmin)
        self.assertIn("iastore", jasmin)
        self.assertIn("if_icmple", jasmin)
        self.assertNotIn("copyOfRange", jasmin)

    def test_slice_store_stays_rejected(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(_collection_source("items = [1, 2, 3]\nxs = [4, 5]\nitems[1:3] = xs"))
        self.assertIn(
            "a slice is only supported as a for iterable", str(raised.exception)
        )

    def test_hashset_slice_is_rejected(self) -> None:
        with self.assertRaises(JasminCompilerError) as raised:
            _compiled_class(
                _collection_source(
                    "\n".join(('values = {"a", "b"}', "taken = values[1:]"))
                ),
                collections=True,
            )
        self.assertIn(
            "subscript needs an array, a list, or a string", str(raised.exception)
        )

    def test_for_over_a_slice_does_not_copy(self) -> None:
        jasmin = _compiled_class(
            _collection_source("items = [1, 2, 3, 4]\nfor item in items[1:]:\n    pass")
        )
        self.assertNotIn("copyOfRange", jasmin)
        self.assertIn("slice_start", jasmin)
