from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from Py2APK.compiler_j import compile_py2j, index_project_types
from Py2APK.jar_index import MEMBER_SCHEMA, JarCatalog

T = TypeVar("T")


class FakeProgress(Generic[T]):
    descriptions: ClassVar[list[str]] = []
    initial_descriptions: ClassVar[list[str]] = []

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


def _compiled_outputs(
    source_text: str, name: str = "Host", *, collections: bool = False
) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        if collections:
            _collection_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        texts = {output.name: output.read_text()}
        for path in output.parent.glob("*.j"):
            texts[path.name] = path.read_text()
        return texts


def _compiled_named(source: Path) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        output = root / f"{source.stem}.j"
        project = index_project_types(root / "src", [source])
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        parts = [output.read_text()]
        parts.extend(
            path.read_text() for path in output.parent.glob("*.j") if path != output
        )
        return "\n".join(parts)


def _compiled_with_stdlib(source_text: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / "Host.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / "Host.j"
        extra = list(Path("Py2APK/stdlib").rglob("*.py"))
        project = index_project_types(root / "src", extra)
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        return output.read_text()


def _compiled_indexed(source_text: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / "Host.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / "Host.j"
        project = index_project_types(root / "src")
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, project)
        finally:
            catalog.close()
        return output.read_text()


def _compiled_class(
    source_text: str,
    name: str = "Host",
    *,
    collections: bool = False,
) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        if collections:
            _collection_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        return output.read_text()


def _compiled_generator(name: str, source_text: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = root / "jars.db"
        _alias_catalog(database)
        source = root / "src" / f"{name}.py"
        source.parent.mkdir(parents=True)
        _ = source.write_text(source_text)
        output = root / f"{name}.j"
        catalog = JarCatalog(database)
        try:
            compile_py2j(source, output, catalog, {})
        finally:
            catalog.close()
        return output.read_text(), (output.parent / f"{name}$gen$1.j").read_text()


def _assert_region_default(
    test: unittest.TestCase,
    method: str,
    start: str,
    after: str,
) -> None:
    region = method[method.index(start) : method.index(after)]
    store = region.split("aconst_null", 1)[1].strip().splitlines()[0].strip()
    test.assertTrue(store.startswith("astore "))
    slot = store.split()[1]
    test.assertGreaterEqual(region.count(f"astore {slot}"), 2)
    test.assertIn(f"aload {slot}", method[method.index(after) :])


def _math_double_rows() -> tuple[tuple[str, str, str, str, int, str], ...]:
    unary = (
        "abs sqrt sin cos tan asin acos atan exp log log10 "
        "floor ceil toRadians toDegrees"
    )
    binary = "atan2 hypot pow"
    rows = [
        ("java/lang/Math", name, "D", "D", 1, "invokestatic") for name in unary.split()
    ]
    rows.extend(
        ("java/lang/Math", name, "DD", "D", 1, "invokestatic")
        for name in binary.split()
    )
    return tuple(rows)


def _alias_catalog(database: Path) -> None:
    string = "Ljava/lang/String;"
    builder = "Ljava/lang/StringBuilder;"
    connection = sqlite3.connect(database)
    connection.executescript(MEMBER_SCHEMA)
    methods = (
        ("java/lang/String", "valueOf", "I", string, 1, "invokestatic"),
        (
            "java/lang/String",
            "format",
            string + "[Ljava/lang/Object;",
            string,
            1,
            "invokestatic",
        ),
        ("java/lang/String", "length", "", "I", 0, "invokevirtual"),
        ("java/lang/String", "substring", "II", string, 0, "invokevirtual"),
        ("java/lang/CharSequence", "length", "", "I", 0, "invokeinterface"),
        ("java/lang/String", "toUpperCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "toLowerCase", "", string, 0, "invokevirtual"),
        ("java/lang/String", "trim", "", string, 0, "invokevirtual"),
        ("java/lang/String", "endsWith", string, "Z", 0, "invokevirtual"),
        ("java/lang/String", "indexOf", string, "I", 0, "invokevirtual"),
        ("java/lang/String", "replace", string + string, string, 0, "invokevirtual"),
        (
            "java/lang/String",
            "split",
            string,
            "[Ljava/lang/String;",
            0,
            "invokevirtual",
        ),
        ("java/util/regex/Pattern", "quote", string, string, 1, "invokestatic"),
        (
            "java/lang/String",
            "split",
            string + "I",
            "[Ljava/lang/String;",
            0,
            "invokevirtual",
        ),
        ("java/lang/StringBuilder", "<init>", "", "V", 0, "invokespecial"),
        ("java/lang/StringBuilder", "append", string, builder, 0, "invokevirtual"),
        (
            "java/lang/StringBuilder",
            "append",
            "Ljava/lang/CharSequence;",
            builder,
            0,
            "invokevirtual",
        ),
        ("java/lang/StringBuilder", "append", "I", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "append", "D", builder, 0, "invokevirtual"),
        ("java/lang/StringBuilder", "toString", "", string, 0, "invokevirtual"),
        ("java/lang/AutoCloseable", "close", "", "V", 0, "invokeinterface"),
        ("java/lang/Integer", "parseInt", string, "I", 1, "invokestatic"),
        ("java/lang/Integer", "valueOf", "I", "Ljava/lang/Integer;", 1, "invokestatic"),
        ("java/lang/Float", "parseFloat", string, "F", 1, "invokestatic"),
        ("java/lang/Double", "parseDouble", string, "D", 1, "invokestatic"),
        ("java/lang/Double", "valueOf", "D", "Ljava/lang/Double;", 1, "invokestatic"),
        ("java/lang/Math", "abs", "I", "I", 1, "invokestatic"),
        ("java/lang/Math", "min", "II", "I", 1, "invokestatic"),
        ("java/lang/Math", "max", "II", "I", 1, "invokestatic"),
        *_math_double_rows(),
        ("java/util/Arrays", "copyOfRange", "[ZII", "[Z", 1, "invokestatic"),
        ("java/util/Arrays", "copyOfRange", "[III", "[I", 1, "invokestatic"),
        ("java/util/Arrays", "copyOfRange", "[FII", "[F", 1, "invokestatic"),
        ("java/util/Arrays", "copyOfRange", "[DII", "[D", 1, "invokestatic"),
        ("java/util/Arrays", "copyOfRange", "[JII", "[J", 1, "invokestatic"),
        (
            "java/util/Arrays",
            "copyOfRange",
            "[Ljava/lang/Object;II",
            "[Ljava/lang/Object;",
            1,
            "invokestatic",
        ),
        ("java/lang/System", "currentTimeMillis", "", "J", 1, "invokestatic"),
        ("java/lang/Thread", "sleep", "J", "V", 1, "invokestatic"),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        methods,
    )
    connection.commit()
    connection.close()


def _listener_catalog(database: Path) -> None:
    connection = sqlite3.connect(database)
    _ = connection.executemany(
        "INSERT INTO classes (internal, super_internal, is_interface) VALUES (?, ?, ?)",
        (
            ("java/lang/Runnable", "java/lang/Object", 1),
            ("java/util/concurrent/ExecutorService", "java/lang/Object", 1),
            ("android/view/View$OnClickListener", "java/lang/Object", 1),
            ("okhttp3/Callback", "java/lang/Object", 1),
            ("android/text/TextWatcher", "java/lang/Object", 1),
            ("android/widget/EditText", "java/lang/Object", 0),
        ),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("java/lang/Runnable", "run", "", "V", 0, "invokeinterface"),
            (
                "java/util/concurrent/ExecutorService",
                "execute",
                "Ljava/lang/Runnable;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "android/os/Handler",
                "post",
                "Ljava/lang/Runnable;",
                "Z",
                0,
                "invokevirtual",
            ),
            (
                "android/widget/Button",
                "setOnClickListener",
                "Landroid/view/View$OnClickListener;",
                "V",
                0,
                "invokevirtual",
            ),
            (
                "android/view/View$OnClickListener",
                "onClick",
                "Landroid/view/View;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "okhttp3/Call",
                "enqueue",
                "Lokhttp3/Callback;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "okhttp3/Callback",
                "onResponse",
                "Lokhttp3/Call;Lokhttp3/Response;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "okhttp3/Callback",
                "onFailure",
                "Lokhttp3/Call;Ljava/io/IOException;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "android/widget/EditText",
                "addTextChangedListener",
                "Landroid/text/TextWatcher;",
                "V",
                0,
                "invokevirtual",
            ),
            (
                "android/text/TextWatcher",
                "afterTextChanged",
                "Landroid/text/Editable;",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "android/text/TextWatcher",
                "beforeTextChanged",
                "Ljava/lang/CharSequence;III",
                "V",
                0,
                "invokeinterface",
            ),
            (
                "android/text/TextWatcher",
                "onTextChanged",
                "Ljava/lang/CharSequence;III",
                "V",
                0,
                "invokeinterface",
            ),
        ),
    )
    connection.commit()
    connection.close()


_ORDERING_ROWS: tuple[tuple[str, str, str, str, int, str], ...] = (
    ("java/util/ArrayList", "get", "I", "Ljava/lang/Object;", 0, "invokevirtual"),
    ("java/util/Collections", "sort", "Ljava/util/List;", "V", 1, "invokestatic"),
    (
        "java/util/Collections",
        "sort",
        "Ljava/util/List;Ljava/util/Comparator;",
        "V",
        1,
        "invokestatic",
    ),
    ("java/lang/Float", "valueOf", "F", "Ljava/lang/Float;", 1, "invokestatic"),
    ("java/lang/Long", "valueOf", "J", "Ljava/lang/Long;", 1, "invokestatic"),
    ("java/lang/Boolean", "valueOf", "Z", "Ljava/lang/Boolean;", 1, "invokestatic"),
    ("java/lang/Integer", "compare", "II", "I", 1, "invokestatic"),
    ("java/lang/Float", "compare", "FF", "I", 1, "invokestatic"),
    ("java/lang/Double", "compare", "DD", "I", 1, "invokestatic"),
    (
        "java/lang/String",
        "compareTo",
        "Ljava/lang/String;",
        "I",
        0,
        "invokevirtual",
    ),
)


def _collection_catalog(database: Path) -> None:
    connection = sqlite3.connect(database)
    _ = connection.executemany(
        "INSERT INTO classes (internal, super_internal, is_interface) VALUES (?, ?, ?)",
        (("java/util/HashSet", "java/lang/Object", 0),),
    )
    _ = connection.executemany(
        "INSERT INTO methods (owner, name, params, result, is_static, opcode) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("java/util/ArrayList", "<init>", "", "V", 0, "invokespecial"),
            (
                "java/util/ArrayList",
                "add",
                "Ljava/lang/Object;",
                "Z",
                0,
                "invokevirtual",
            ),
            ("java/util/ArrayList", "size", "", "I", 0, "invokevirtual"),
            (
                "java/util/ArrayList",
                "toArray",
                "[Ljava/lang/Object;",
                "[Ljava/lang/Object;",
                0,
                "invokevirtual",
            ),
            ("java/util/LinkedHashMap", "<init>", "", "V", 0, "invokespecial"),
            (
                "java/util/LinkedHashMap",
                "put",
                "Ljava/lang/Object;Ljava/lang/Object;",
                "Ljava/lang/Object;",
                0,
                "invokevirtual",
            ),
            ("java/util/HashSet", "<init>", "", "V", 0, "invokespecial"),
            (
                "java/util/HashSet",
                "add",
                "Ljava/lang/Object;",
                "Z",
                0,
                "invokevirtual",
            ),
            ("java/util/HashSet", "size", "", "I", 0, "invokevirtual"),
            (
                "java/util/Objects",
                "requireNonNull",
                "Ljava/lang/Object;",
                "Ljava/lang/Object;",
                1,
                "invokestatic",
            ),
            (
                "android/app/Activity",
                "requestPermissions",
                "[Ljava/lang/String;I",
                "V",
                0,
                "invokevirtual",
            ),
            *_ORDERING_ROWS,
        ),
    )
    connection.commit()
    connection.close()
