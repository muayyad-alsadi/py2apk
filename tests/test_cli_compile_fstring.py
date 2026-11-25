from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _compile_helpers import (
    _alias_catalog,
    _compiled_class,
)

from Py2APK.compiler_j import JasminCompilerError, compile_py2j
from Py2APK.jar_index import JarCatalog


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
                        "from java.lang import CharSequence, String",
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
                        "    def match(self, folded: str) -> None:",
                        '        if folded == "commons":',
                        "            pass",
                        '        if folded != "commons":',
                        "            pass",
                        "",
                        "    def missing(self, folded: str) -> None:",
                        "        if folded == None:",
                        "            pass",
                        "",
                        "    def mixed(self, folded: str, count: int) -> None:",
                        "        if folded == count:",
                        "            pass",
                        "",
                        "    def widths(self, width: float, height: double, count: int) -> None:",
                        "        if width == 1.0:",
                        "            pass",
                        "        if height == count:",
                        "            pass",
                        "",
                        "    def greet(self, name: CharSequence) -> None:",
                        "        size = len(name)",
                        '        message = f"Hello, {name}"',
                        "",
                        "    def blanks(self, name: CharSequence, items: list[str]) -> None:",
                        "        if not name:",
                        "            pass",
                        "        if not items:",
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
            compare = jasmin.split(".method public compare", 1)[1].split(
                ".end method", 1
            )[0]
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
            self.assertEqual(
                run.count("invokestatic java/lang/String/valueOf(I)Ljava/lang/String;"),
                2,
            )
            self.assertIn(
                "invokestatic java/lang/Integer/parseInt(Ljava/lang/String;)I", run
            )
            self.assertIn(
                "invokestatic java/lang/Float/parseFloat(Ljava/lang/String;)F", run
            )
            self.assertIn(
                "invokestatic java/lang/Double/parseDouble(Ljava/lang/String;)D", run
            )
            self.assertIn("invokevirtual java/lang/String/length()I", run)
            self.assertIn("arraylength", run)
            self.assertIn("invokestatic java/lang/Math/abs(I)I", run)
            self.assertIn("invokestatic java/lang/Math/min(II)I", run)
            self.assertIn("invokestatic java/lang/Math/max(II)I", run)
            self.assertIn(
                "invokevirtual java/lang/String/toUpperCase()Ljava/lang/String;", run
            )
            self.assertIn(
                "invokevirtual java/lang/String/toLowerCase()Ljava/lang/String;", run
            )
            self.assertIn(
                "invokevirtual java/lang/String/trim()Ljava/lang/String;", run
            )
            self.assertIn(
                "invokevirtual java/lang/String/endsWith(Ljava/lang/String;)Z", run
            )
            self.assertIn(
                "invokevirtual java/lang/String/indexOf(Ljava/lang/String;)I", run
            )
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
            match = jasmin.split(".method public match", 1)[1].split(".end method", 1)[
                0
            ]
            self.assertIn(
                "invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z", match
            )
            self.assertIn("ifnull", match)
            self.assertIn("ifeq", match)
            self.assertIn("ifne", match)
            missing = jasmin.split(".method public missing", 1)[1].split(
                ".end method", 1
            )[0]
            self.assertIn("aconst_null", missing)
            self.assertIn("ifnull", missing)
            self.assertIn(
                "invokevirtual java/lang/Object/equals(Ljava/lang/Object;)Z", missing
            )
            mixed = jasmin.split(".method public mixed", 1)[1].split(".end method", 1)[
                0
            ]
            self.assertNotIn("equals", mixed)
            self.assertIn("pop", mixed)
            widths = jasmin.split(".method public widths", 1)[1].split(
                ".end method", 1
            )[0]
            self.assertIn("fcmpg", widths)
            self.assertIn("i2d", widths)
            self.assertIn("dcmpg", widths)
            greet = jasmin.split(".method public greet", 1)[1].split(".end method", 1)[
                0
            ]
            self.assertIn("invokeinterface java/lang/CharSequence/length()I 1", greet)
            self.assertIn(
                "invokevirtual java/lang/StringBuilder/append(Ljava/lang/CharSequence;)Ljava/lang/StringBuilder;",
                greet,
            )
            blanks = jasmin.split(".method public blanks", 1)[1].split(
                ".end method", 1
            )[0]
            self.assertIn("ifnull", blanks)
            self.assertIn("invokeinterface java/lang/CharSequence/length()I 1", blanks)
            self.assertIn("arraylength", blanks)
            with self.assertRaises(JasminCompilerError):
                _ = _compiled_class(
                    "\n".join(
                        (
                            "class Host:",
                            "    def run(self, count: int) -> None:",
                            "        if not count:",
                            "            pass",
                            "",
                        )
                    )
                )

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

    def test_type_checking_builtin_annotation_stays_primitive(self) -> None:
        text = _compiled_class(
            "\n".join(
                (
                    "from typing import TYPE_CHECKING",
                    "",
                    "if TYPE_CHECKING:",
                    "    double: float",
                    "",
                    "class Host:",
                    "    def widen(self, latitude: double) -> double:",
                    "        return double(1)",
                    "",
                )
            )
        )
        self.assertIn("widen(D)D", text)
        self.assertIn("i2d", text)
        module_level = _compiled_class(
            "\n".join(
                (
                    "double = float",
                    "",
                    "class Host:",
                    "    def widen(self, latitude: double) -> double:",
                    "        return double(1)",
                    "",
                )
            )
        )
        self.assertIn("widen(D)D", module_level)
        self.assertIn("i2d", module_level)

    def test_conditional_expression_branches_on_the_truth_test(self) -> None:
        text = _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def greet(self, name: str) -> str:",
                    '        return f"Hello, {name}" if name else "Hello, world"',
                    "",
                )
            )
        )
        self.assertIn("Hello, world", text)
        self.assertIn("Hello, ", text)
        self.assertIn("goto", text)
        false_arm = text.index("Hello, world")
        self.assertLess(text.index("ifeq"), false_arm)
