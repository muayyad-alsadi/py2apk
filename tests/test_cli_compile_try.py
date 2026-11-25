from __future__ import annotations

import unittest

from _compile_helpers import (
    _assert_region_default,
    _compiled_class,
    _compiled_indexed,
    _compiled_with_stdlib,
)

from Py2APK.compiler_j import JasminCompilerError


def _assert_try_raise_with(test: unittest.TestCase) -> None:
    joined = _compiled_indexed(
        "\n".join(
            (
                "from java.lang import Object",
                "",
                "class Host:",
                "    def __enter__(self) -> int:",
                "        return 1",
                "",
                "    def __exit__(self, exc_type: Object, exc_value: Object, traceback: Object) -> bool:",
                "        return False",
                "",
                "    def use(self, value: Host) -> None:",
                "        pass",
                "",
                "    def use_count(self, value: int) -> None:",
                "        pass",
                "",
                "    def run(self) -> None:",
                "        with self:",
                "            kept = self",
                "        self.use(kept)",
                "",
                "    def counted(self) -> None:",
                "        with self:",
                "            kept = 1",
                "        self.use_count(kept)",
                "",
                "    def prior(self) -> None:",
                "        kept = self",
                "        with self:",
                "            pass",
                "        self.use(kept)",
                "",
                "    def tried(self) -> None:",
                "        try:",
                "            kept = self",
                "        except:",
                "            pass",
                "        self.use(kept)",
                "",
            )
        )
    )
    _assert_region_default(
        test,
        joined.split(".method public run", 1)[1],
        "L_with_",
        "L_withafter_",
    )
    counted = joined.split(".method public counted", 1)[1]
    test.assertLess(counted.index("iconst_0"), counted.index("iconst_1"))
    test.assertGreaterEqual(counted.count("istore"), 2)
    prior = joined.split(".method public prior", 1)[1]
    protected = prior[prior.index("L_with_") : prior.index("L_withend_")]
    test.assertNotIn("aconst_null", protected)
    _assert_region_default(
        test,
        joined.split(".method public tried", 1)[1],
        "L_try_",
        "L_aftertry_",
    )
    raised = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        raise Error()",
                "",
            )
        )
    )
    test.assertIn("athrow", raised)
    test.assertNotIn("astore", raised)
    caught = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        try:",
                "            raise Error()",
                "        except Error as error:",
                "            raise",
                "",
            )
        )
    )
    test.assertIn(".catch py2apk/Error", caught)
    stored = caught.index("astore")
    slot = caught[stored:].split()[1]
    test.assertIn(f"aload {slot}", caught)
    caused = _compiled_with_stdlib(
        "\n".join(
            (
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        other = Error()",
                "        raise Error() from other",
                "",
            )
        )
    )
    test.assertIn("putfield py2apk/Error/cause Ljava/lang/Exception;", caused)
    read = _compiled_with_stdlib(
        "\n".join(
            (
                "from java.lang import Exception",
                "from py2apk import Error",
                "",
                "class Host:",
                "    def read(self) -> Exception:",
                "        try:",
                "            raise Error()",
                "        except Error as error:",
                "            return error.cause",
                "        return Error()",
                "",
            )
        )
    )
    test.assertIn("getfield py2apk/Error/cause Ljava/lang/Exception;", read)
    shared = _compiled_with_stdlib(
        "\n".join(
            (
                "from java.lang import Exception",
                "from py2apk import Error",
                "",
                "class Host:",
                "    def run(self) -> None:",
                "        try:",
                "            raise Error()",
                "        except (Error, Exception, Error) as error:",
                "            raise",
                "",
                "    def again(self) -> Exception:",
                "        try:",
                "            raise Error()",
                "        except (Error,) as error:",
                "            return error.cause",
                "        return Error()",
                "",
                "    def split(self) -> None:",
                "        try:",
                "            raise Error()",
                "        except (Error, Exception):",
                "            pass",
                "        except Exception:",
                "            pass",
                "",
                "    def closed(self) -> None:",
                "        try:",
                "            raise Error()",
                "        except (Error, Exception):",
                "            pass",
                "        finally:",
                "            pass",
                "",
            )
        )
    )
    run = shared.split(".method public run", 1)[1].split(".method", 1)[0]
    catches = [
        line.strip() for line in run.splitlines() if line.strip().startswith(".catch ")
    ]
    test.assertEqual(
        [line.split()[1] for line in catches],
        ["py2apk/Error", "java/lang/Exception"],
    )
    labels = {line.split()[-1] for line in catches}
    test.assertEqual(len(labels), 1)
    label = labels.pop()
    test.assertEqual(run.count(f"{label}:"), 1)
    test.assertEqual(run.count("astore"), 1)
    test.assertEqual(run.count("athrow"), 2)
    again = shared.split(".method public again", 1)[1].split(".method", 1)[0]
    test.assertEqual(again.count(".catch "), 1)
    test.assertIn("getfield py2apk/Error/cause Ljava/lang/Exception;", again)
    split = shared.split(".method public split", 1)[1].split(".method", 1)[0]
    split_catches = [
        line.strip()
        for line in split.splitlines()
        if line.strip().startswith(".catch ")
    ]
    test.assertEqual(len(split_catches), 3)
    test.assertEqual(len({line.split()[-1] for line in split_catches}), 2)
    closed = shared.split(".method public closed", 1)[1]
    closed_catches = [
        line.strip()
        for line in closed.splitlines()
        if line.strip().startswith(".catch ")
    ]
    test.assertEqual(
        [line.split()[1] for line in closed_catches],
        ["py2apk/Error", "java/lang/Exception", "java/lang/Throwable"],
    )
    test.assertEqual(len({line.split()[-1] for line in closed_catches[:2]}), 1)
    test.assertNotEqual(closed_catches[0].split()[-1], closed_catches[2].split()[-1])
    with test.assertRaises(JasminCompilerError) as mixed:
        _compiled_with_stdlib(
            "\n".join(
                (
                    "from java.lang import Exception",
                    "from py2apk import Error",
                    "",
                    "class Host:",
                    "    def run(self) -> Exception:",
                    "        try:",
                    "            raise Error()",
                    "        except (Error, Exception) as error:",
                    "            return error.cause",
                    "        return Error()",
                    "",
                )
            )
        )
    test.assertIn("unknown type", str(mixed.exception))
    with test.assertRaises(JasminCompilerError) as empty:
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    "        try:",
                    "            pass",
                    "        except ():",
                    "            pass",
                    "",
                )
            )
        )
    test.assertIn("except tuple is empty", str(empty.exception))
    with test.assertRaises(JasminCompilerError) as nested:
        _compiled_with_stdlib(
            "\n".join(
                (
                    "from java.lang import Exception",
                    "from py2apk import Error",
                    "",
                    "class Host:",
                    "    def run(self) -> None:",
                    "        try:",
                    "            pass",
                    "        except ((Error, Exception),):",
                    "            pass",
                    "",
                )
            )
        )
    test.assertIn("except tuple entries must be type names", str(nested.exception))
    managed = _compiled_indexed(
        "\n".join(
            (
                "from java.lang import Throwable",
                "",
                "class Host:",
                "    def __enter__(self) -> Host:",
                "        return self",
                "",
                "    def __exit__(self, exc_type: Throwable, exc_value: Throwable, traceback: Throwable) -> bool:",
                "        return False",
                "",
                "    def run(self) -> None:",
                "        with Host() as item:",
                "            pass",
                "",
            )
        )
    )
    test.assertIn("invokevirtual Host/__enter__()LHost;", managed)
    test.assertGreaterEqual(
        managed.count(
            "invokevirtual Host/__exit__(Ljava/lang/Throwable;Ljava/lang/Throwable;Ljava/lang/Throwable;)Z"
        ),
        2,
    )
    with test.assertRaises(JasminCompilerError):
        _compiled_class(
            "\n".join(
                (
                    "class Host:",
                    "    def run(self) -> None:",
                    "        raise",
                    "",
                )
            )
        )


class AliasLoweringTests(unittest.TestCase):
    def test_lowers_try_raise_and_with(self) -> None:
        _assert_try_raise_with(self)
