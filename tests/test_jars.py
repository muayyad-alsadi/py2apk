"""Tests for jar indexing, platform selection, and catalog compilation."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sqlite3
import struct
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import defusedxml.ElementTree as ET

from Py2APK import cli
from Py2APK.classfile import parse_class
from Py2APK.cli_check import check_project
from Py2APK.cli_compile import compile_project
from Py2APK.cli_fetch_jars import (
    _extract_classes_jar,
    describe_repositories,
    link_jar_dex,
)
from Py2APK.cli_fetch_platform import (
    api_levels_from_xml,
    fetch_platform,
    select_platform_archive,
)
from Py2APK.cli_stubs import write_stubs
from Py2APK.downloader import (
    PARALLEL_DOWNLOADS,
    _download_urllib,
    download_files,
    require_http_url,
)
from Py2APK.jar_index import (
    JarCatalog,
    api_database_path,
    dex_jars,
    index_jar,
    library_jars,
    write_api_table,
    write_project_database,
)
from Py2APK.maven_resolve import parse_dependency_file, resolve_artifacts


def _utf8(text: str) -> bytes:
    encoded = text.encode()
    return bytes([1]) + struct.pack(">H", len(encoded)) + encoded


def _class(name_index: int) -> bytes:
    return bytes([7]) + struct.pack(">H", name_index)


def sample_class(*, interface: bool) -> bytes:
    entries = [
        _utf8("Sample"),
        _class(1),
        _utf8("java/lang/Object"),
        _class(3),
        _utf8("count"),
        _utf8("I"),
        _utf8("secret"),
        _utf8("<init>"),
        _utf8("()V"),
        _utf8("hidden"),
        _utf8("kept"),
        _utf8("(I)I"),
        _utf8("ping"),
        _utf8("()Ljava/lang/String;"),
        _utf8("java/lang/Runnable"),
        _class(15),
    ]
    pool = b"".join(entries)
    if interface:
        body = b"".join(
            (
                struct.pack(">HHHH", 0x0601, 2, 4, 0),
                struct.pack(">H", 0),
                struct.pack(">H", 1),
                struct.pack(">HHH", 0x0401, 11, 9),
                struct.pack(">H", 0),
            )
        )
    else:
        body = b"".join(
            (
                struct.pack(">HHH", 0x0021, 2, 4),
                struct.pack(">HH", 1, 16),
                struct.pack(">H", 2),
                struct.pack(">HHH", 0x0009, 5, 6),
                struct.pack(">H", 0),
                struct.pack(">HHH", 0x0002, 7, 6),
                struct.pack(">H", 0),
                struct.pack(">H", 4),
                struct.pack(">HHH", 0x0001, 8, 9),
                struct.pack(">H", 0),
                struct.pack(">HHH", 0x0002, 10, 9),
                struct.pack(">H", 0),
                struct.pack(">HHH", 0x0004, 11, 12),
                struct.pack(">H", 0),
                struct.pack(">HHH", 0x0009, 13, 14),
                struct.pack(">H", 0),
            )
        )
    return b"\xCA\xFE\xBA\xBE" + struct.pack(">HHH", 0, 55, len(entries) + 1) + pool + body


class ClassFileTests(unittest.TestCase):
    def test_keeps_public_protected_and_drops_private(self) -> None:
        parsed = parse_class(sample_class(interface=False))

        self.assertEqual(parsed.internal, "Sample")
        self.assertFalse(parsed.is_interface)
        self.assertEqual(parsed.interfaces, ("java/lang/Runnable",))
        names = {member.name for member in parsed.methods}
        self.assertEqual(names, {"kept", "ping", "<init>"})
        ping = next(member for member in parsed.methods if member.name == "ping")
        self.assertEqual(ping.opcode, "invokestatic")
        kept = next(member for member in parsed.methods if member.name == "kept")
        self.assertEqual(kept.opcode, "invokevirtual")
        self.assertEqual({member.name for member in parsed.fields}, {"count"})

    def test_interface_method_uses_invokeinterface(self) -> None:
        parsed = parse_class(sample_class(interface=True))

        self.assertTrue(parsed.is_interface)
        self.assertEqual(parsed.methods[0].opcode, "invokeinterface")
        self.assertEqual(parsed.methods[0].param_names, ())

    def test_reads_method_parameters_before_local_variables(self) -> None:
        parsed = parse_class(
            _named_class(
                static=True,
                descriptor="(Ljava/lang/String;)V",
                parameters=("title",),
                locals_=(("wrong", "Ljava/lang/String;", 0, 0),),
            )
        )

        self.assertEqual(parsed.methods[0].param_names, ("title",))

    def test_reads_local_variable_slots_for_wide_parameters(self) -> None:
        instance = parse_class(
            _named_class(
                static=False,
                descriptor="(IJD)V",
                locals_=(
                    ("this", "Ldemo/Box;", 0, 0),
                    ("count", "I", 1, 0),
                    ("wide", "J", 2, 0),
                    ("scale", "D", 4, 0),
                    ("nope", "I", 1, 5),
                ),
            )
        )
        static = parse_class(
            _named_class(
                static=True,
                descriptor="(DJ)V",
                locals_=(
                    ("scale", "D", 0, 0),
                    ("wide", "J", 2, 0),
                ),
            )
        )

        self.assertEqual(instance.methods[0].param_names, ("count", "wide", "scale"))
        self.assertEqual(static.methods[0].param_names, ("scale", "wide"))


class StaleJarIndexTests(unittest.TestCase):
    def test_rebuilds_sqlite_missing_param_names(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            jar_path = root / "demo.jar"
            _write_named_jar(jar_path)
            database = jar_path.with_suffix(".sqlite")
            connection = sqlite3.connect(database)
            _ = connection.executescript(
                """
                CREATE TABLE classes (
                    internal TEXT PRIMARY KEY,
                    super_internal TEXT,
                    is_interface INTEGER NOT NULL
                );
                CREATE TABLE methods (
                    owner TEXT NOT NULL,
                    name TEXT NOT NULL,
                    params TEXT NOT NULL,
                    result TEXT NOT NULL,
                    is_static INTEGER NOT NULL,
                    opcode TEXT NOT NULL
                );
                """
            )
            _ = connection.execute(
                "INSERT INTO methods (owner, name, params, result, is_static, opcode) "
                "VALUES ('demo/Box', 'mix', 'Ljava/lang/String;', 'V', 1, 'invokestatic')"
            )
            connection.commit()
            connection.close()
            later = jar_path.stat().st_mtime + 10
            os.utime(database, (later, later))

            indexed = index_jar(jar_path)

            connection = sqlite3.connect(indexed)
            columns = [
                str(row[1]) for row in connection.execute("PRAGMA table_info(methods)")
            ]
            row = connection.execute("SELECT param_names FROM methods").fetchone()
            connection.close()
            self.assertIn("param_names", columns)
            self.assertEqual(row, ("title",))

    def test_keeps_sqlite_that_already_has_param_names(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            jar_path = root / "demo.jar"
            _write_named_jar(jar_path)
            indexed = index_jar(jar_path)
            connection = sqlite3.connect(indexed)
            _ = connection.execute("UPDATE methods SET param_names = 'sentinel'")
            connection.commit()
            connection.close()
            later = jar_path.stat().st_mtime + 10
            os.utime(indexed, (later, later))

            _ = index_jar(jar_path)

            connection = sqlite3.connect(indexed)
            row = connection.execute("SELECT param_names FROM methods").fetchone()
            connection.close()
            self.assertEqual(row, ("sentinel",))


def _class_entry(name_index: int) -> bytes:
    return bytes([7]) + struct.pack(">H", name_index)


def _attribute(name_index: int, body: bytes) -> bytes:
    return struct.pack(">HI", name_index, len(body)) + body


def _named_class(
    *,
    static: bool,
    descriptor: str,
    parameters: tuple[str, ...] | None = None,
    locals_: tuple[tuple[str, str, int, int], ...] = (),
) -> bytes:
    entries: list[bytes] = []

    def add(entry: bytes) -> int:
        entries.append(entry)
        return len(entries)

    this_name = add(_utf8("demo/Box"))
    this_class = add(_class_entry(this_name))
    super_name = add(_utf8("java/lang/Object"))
    super_class = add(_class_entry(super_name))
    method_name = add(_utf8("mix"))
    method_desc = add(_utf8(descriptor))
    attributes: list[bytes] = []
    if parameters is not None:
        name_index = add(_utf8("MethodParameters"))
        parameter_indexes = [add(_utf8(name)) for name in parameters]
        body = bytes([len(parameter_indexes)])
        for index in parameter_indexes:
            body += struct.pack(">HH", index, 0)
        attributes.append(_attribute(name_index, body))
    if locals_:
        code_name = add(_utf8("Code"))
        table_name = add(_utf8("LocalVariableTable"))
        encoded: list[tuple[int, int, int, int, int]] = []
        for name, local_descriptor, slot, start_pc in locals_:
            encoded.append(
                (
                    start_pc,
                    1,
                    add(_utf8(name)),
                    add(_utf8(local_descriptor)),
                    slot,
                )
            )
        table = struct.pack(">H", len(encoded))
        for entry in encoded:
            table += struct.pack(">HHHHH", *entry)
        code = b"\xb1"
        code_body = (
            struct.pack(">HHI", 1, 8, len(code))
            + code
            + struct.pack(">H", 0)
            + struct.pack(">H", 1)
            + _attribute(table_name, table)
        )
        attributes.append(_attribute(code_name, code_body))
    access = 0x0009 if static else 0x0001
    method = struct.pack(">HHH", access, method_name, method_desc)
    method += struct.pack(">H", len(attributes)) + b"".join(attributes)
    body = struct.pack(">HHHHHH", 0x0021, this_class, super_class, 0, 0, 1)
    body += method + struct.pack(">H", 0)
    pool = b"".join(entries)
    return b"\xCA\xFE\xBA\xBE" + struct.pack(">HHH", 0, 55, len(entries) + 1) + pool + body


def _write_named_jar(jar_path: Path) -> None:
    with zipfile.ZipFile(jar_path, "w") as archive:
        archive.writestr(
            "demo/Box.class",
            _named_class(
                static=True,
                descriptor="(Ljava/lang/String;)V",
                parameters=("title",),
            ),
        )


class PlatformSelectionTests(unittest.TestCase):
    def test_highest_revision_for_the_plain_platform_path(self) -> None:
        document = ET.fromstring(
            """
            <sdk-repository>
              <remotePackage path="platforms;android-34">
                <revision><major>2</major></revision>
                <archives><archive><complete><url>platform-34-ext7_r02.zip</url></complete></archive></archives>
              </remotePackage>
              <remotePackage path="platforms;android-34-ext12">
                <revision><major>9</major></revision>
                <archives><archive><complete><url>platform-34-ext12_r01.zip</url></complete></archive></archives>
              </remotePackage>
              <remotePackage path="platforms;android-34">
                <revision><major>3</major></revision>
                <archives><archive><complete><url>platform-34-ext7_r03.zip</url></complete></archive></archives>
              </remotePackage>
            </sdk-repository>
            """
        )

        self.assertEqual(select_platform_archive(document, 34), "platform-34-ext7_r03.zip")


class RepositoryOrderTests(unittest.TestCase):
    def test_extra_repositories_are_tried_before_central(self) -> None:
        self.assertEqual(
            describe_repositories(
                [
                    "https://jitpack.io",
                    "https://dl.google.com/dl/android/maven2/",
                ]
            ),
            [
                "https://jitpack.io",
                "https://dl.google.com/dl/android/maven2",
                "https://repo.maven.apache.org/maven2",
            ],
        )


class RepositoryFileTests(unittest.TestCase):
    def test_extra_index_url_is_read_from_deps_txt(self) -> None:
        parsed = parse_dependency_file(
            "# chart\n"
            "--extra-index-url https://jitpack.io\n"
            "\n"
            "com.github.PhilJay:MPAndroidChart:v3.1.0\n"
        )
        self.assertEqual(parsed.repositories, ["https://jitpack.io"])
        self.assertEqual(
            parsed.coordinates,
            [("com.github.PhilJay", "MPAndroidChart", "v3.1.0")],
        )

    def test_empty_deps_txt_has_no_coordinates(self) -> None:
        for text in ("", "\n", "# none yet\n"):
            parsed = parse_dependency_file(text)
            self.assertEqual(parsed.coordinates, [])
            self.assertEqual(parsed.repositories, [])

    def test_google_maven_and_central_are_implied(self) -> None:
        self.assertEqual(
            describe_repositories(["https://jitpack.io"]),
            [
                "https://jitpack.io",
                "https://dl.google.com/dl/android/maven2",
                "https://repo.maven.apache.org/maven2",
            ],
        )

    def test_aar_contributes_its_classes_jar(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            aar = root / "chart.aar"
            jar = root / "out" / "chart.jar"
            with zipfile.ZipFile(aar, "w") as archive:
                archive.writestr("classes.jar", b"class-bytes")
            self.assertTrue(_extract_classes_jar(aar, jar))
            self.assertEqual(jar.read_bytes(), b"class-bytes")

    def test_aar_without_classes_jar_is_skipped(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            aar = root / "res.aar"
            jar = root / "out" / "res.jar"
            with zipfile.ZipFile(aar, "w") as archive:
                archive.writestr("res/values/strings.xml", b"<resources/>")
            self.assertFalse(_extract_classes_jar(aar, jar))
            self.assertFalse(jar.exists())


class MavenResolveTests(unittest.TestCase):
    def test_transitive_dependency_uses_managed_version(self) -> None:
        documents = {
            "com/example/parent/1/parent-1.pom": b"""
                <project>
                  <groupId>com.example</groupId><artifactId>parent</artifactId><version>1</version>
                  <properties><lib.version>9</lib.version></properties>
                  <dependencyManagement><dependencies>
                    <dependency>
                      <groupId>com.example</groupId><artifactId>lib</artifactId><version>${lib.version}</version>
                    </dependency>
                  </dependencies></dependencyManagement>
                </project>
            """,
            "com/example/app/1/app-1.pom": b"""
                <project>
                  <parent><groupId>com.example</groupId><artifactId>parent</artifactId><version>1</version></parent>
                  <groupId>com.example</groupId><artifactId>app</artifactId><version>1</version>
                  <dependencies>
                    <dependency><groupId>com.example</groupId><artifactId>lib</artifactId></dependency>
                  </dependencies>
                </project>
            """,
            "com/example/lib/9/lib-9.pom": b"""
                <project>
                  <groupId>com.example</groupId><artifactId>lib</artifactId><version>9</version>
                </project>
            """,
        }

        resolved = resolve_artifacts(
            [("com.example", "app", "1")],
            lambda relative: documents.get(relative),
        )
        self.assertEqual(
            [(item.coordinate, item.kind) for item in resolved],
            [("com.example:app:1", "direct"), ("com.example:lib:9", "transitive")],
        )


class DownloaderTests(unittest.TestCase):
    def test_file_url_is_refused_before_urlopen(self) -> None:
        with self.assertRaises(ValueError):
            require_http_url("file:///tmp/secret")
        with (
            TemporaryDirectory() as temporary,
            mock.patch("Py2APK.downloader.urllib.request.urlopen") as urlopen,
            self.assertRaises(ValueError),
        ):
            _download_urllib("file:///tmp/secret", Path(temporary) / "out")
        urlopen.assert_not_called()

    def test_parallel_cap_and_urllib_fallback(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = [(f"https://example.test/{index}", root / f"{index}.bin") for index in range(8)]

            class Response:
                def __init__(self, body: bytes) -> None:
                    self.status = 200
                    self._body = body

                async def __aenter__(self) -> Response:
                    return self

                async def __aexit__(self, *args: object) -> None:
                    return None

                def raise_for_status(self) -> None:
                    return None

                async def read(self) -> bytes:
                    await asyncio.sleep(0.01)
                    return self._body

            class Session:
                def __init__(self) -> None:
                    self.active = 0
                    self.max_active = 0

                async def __aenter__(self) -> Session:
                    return self

                async def __aexit__(self, *args: object) -> None:
                    return None

                def get(self, url: str) -> Response:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)

                    class Counting(Response):
                        async def read(inner) -> bytes:
                            await asyncio.sleep(0.01)
                            self.active -= 1
                            return b"ok"

                    _ = url
                    return Counting(b"ok")

            class FakeAiohttp:
                session = Session()

                def ClientSession(self, **kwargs: object) -> Session:
                    _ = kwargs
                    return self.session

                def ClientTimeout(self, **kwargs: object) -> object:
                    _ = kwargs
                    return object()

            fake = FakeAiohttp()
            with mock.patch.dict("sys.modules", {"aiohttp": fake}):
                missing = download_files(jobs)

            self.assertEqual(missing, [])
            self.assertLessEqual(fake.session.max_active, PARALLEL_DOWNLOADS)
            self.assertGreater(fake.session.max_active, 1)
            self.assertTrue((root / "0.bin").is_file())

            urllib_target = root / "urllib.bin"
            with (
                mock.patch.dict("sys.modules", {"aiohttp": None}),
                mock.patch("Py2APK.downloader._download_urllib") as urllib_download,
            ):
                urllib_download.side_effect = lambda url, destination: destination.write_bytes(b"u")
                missing = download_files([("https://example.test/u", urllib_target)])
            self.assertEqual(missing, [])
            urllib_download.assert_called_once()


def _insert(
    connection: sqlite3.Connection,
    internal: str,
    super_name: str | None,
    *,
    interface: bool = False,
    interfaces: tuple[str, ...] = (),
    methods: tuple[tuple[str, str, str, int, str], ...] = (),
    fields: tuple[tuple[str, str, int], ...] = (),
) -> None:
    _ = connection.execute(
        "INSERT INTO classes (jar_id, internal, super_internal, is_interface) VALUES (1, ?, ?, ?)",
        (internal, super_name, int(interface)),
    )
    _ = connection.executemany(
        "INSERT INTO class_interfaces (jar_id, owner, interface) VALUES (1, ?, ?)",
        [(internal, name) for name in interfaces],
    )
    _ = connection.executemany(
        "INSERT INTO methods (jar_id, owner, name, params, result, is_static, opcode) VALUES (1, ?, ?, ?, ?, ?, ?)",
        [(internal, name, params, result, static, opcode) for name, params, result, static, opcode in methods],
    )
    _ = connection.executemany(
        "INSERT INTO fields (jar_id, owner, name, descriptor, is_static) VALUES (1, ?, ?, ?, ?)",
        [(internal, name, descriptor, static) for name, descriptor, static in fields],
    )


def _fixture(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE jars (id INTEGER PRIMARY KEY, filename TEXT, coordinate TEXT, kind TEXT, sha256 TEXT);
        CREATE TABLE classes (jar_id INTEGER, internal TEXT, super_internal TEXT, is_interface INTEGER);
        CREATE TABLE class_interfaces (jar_id INTEGER, owner TEXT, interface TEXT);
        CREATE TABLE methods (jar_id INTEGER, owner TEXT, name TEXT, params TEXT, result TEXT, is_static INTEGER, opcode TEXT);
        CREATE TABLE fields (jar_id INTEGER, owner TEXT, name TEXT, descriptor TEXT, is_static INTEGER);
        """
    )
    virtual = "invokevirtual"
    special = "invokespecial"
    interface = "invokeinterface"
    static = "invokestatic"
    context = "Landroid/content/Context;"
    view = "Landroid/view/View;"
    char_sequence = "Ljava/lang/CharSequence;"
    string = "Ljava/lang/String;"
    builder = "Landroid/app/AlertDialog$Builder;"
    _insert(
        connection,
        "java/lang/String",
        "java/lang/Object",
        interfaces=("java/lang/CharSequence",),
        methods=(
            ("valueOf", "D", string, 1, static),
            ("length", "", "I", 0, virtual),
        ),
    )
    _insert(
        connection,
        "java/lang/CharSequence",
        "java/lang/Object",
        interface=True,
        methods=(("length", "", "I", 0, interface), ("toString", "", string, 0, interface)),
    )
    _insert(connection, "android/content/Context", "java/lang/Object", fields=(("LOCATION_SERVICE", string, 1),))
    _insert(
        connection,
        "android/content/Context",
        "java/lang/Object",
        methods=(("getSystemService", string, "Ljava/lang/Object;", 0, virtual), ("checkSelfPermission", string, "I", 0, virtual)),
    )
    _insert(connection, "android/app/Activity", "android/content/Context", methods=(
        ("onCreate", "Landroid/os/Bundle;", "V", 0, virtual),
        ("setContentView", view, "V", 0, virtual),
        ("requestPermissions", "[Ljava/lang/String;I", "V", 0, virtual),
    ))
    _insert(connection, "android/view/View", "java/lang/Object")
    _insert(connection, "android/widget/LinearLayout", "android/view/View", methods=(
        ("<init>", context, "V", 0, special),
        ("setOrientation", "I", "V", 0, virtual),
        ("setGravity", "I", "V", 0, virtual),
        ("setPadding", "IIII", "V", 0, virtual),
        ("addView", view, "V", 0, virtual),
    ), fields=(("VERTICAL", "I", 1),))
    _insert(connection, "android/view/Gravity", "java/lang/Object", fields=(("CENTER_VERTICAL", "I", 1),))
    _insert(connection, "android/widget/TextView", "android/view/View", methods=(
        ("<init>", context, "V", 0, special),
        ("setText", char_sequence, "V", 0, virtual),
        ("setTextSize", "F", "V", 0, virtual),
        ("getText", "", char_sequence, 0, virtual),
        ("setHint", char_sequence, "V", 0, virtual),
        ("setSingleLine", "Z", "V", 0, virtual),
    ))
    _insert(connection, "android/widget/EditText", "android/widget/TextView", methods=(("<init>", context, "V", 0, special),))
    _insert(connection, "android/widget/Button", "android/widget/TextView", methods=(
        ("<init>", context, "V", 0, special),
        ("setOnClickListener", "Landroid/view/View$OnClickListener;", "V", 0, virtual),
    ))
    _insert(connection, "java/lang/StringBuilder", "java/lang/Object", methods=(
        ("<init>", "", "V", 0, special),
        ("append", string, "Ljava/lang/StringBuilder;", 0, virtual),
        ("append", char_sequence, "Ljava/lang/StringBuilder;", 0, virtual),
        ("append", "D", "Ljava/lang/StringBuilder;", 0, virtual),
        ("toString", "", string, 0, virtual),
    ))
    _insert(connection, "android/text/TextWatcher", "java/lang/Object", interface=True, methods=(
        ("afterTextChanged", "Landroid/text/Editable;", "V", 0, interface),
        ("beforeTextChanged", "Ljava/lang/CharSequence;III", "V", 0, interface),
        ("onTextChanged", "Ljava/lang/CharSequence;III", "V", 0, interface),
    ))
    _insert(
        connection,
        "android/text/Editable",
        "java/lang/Object",
        interface=True,
        interfaces=("java/lang/CharSequence",),
    )
    _insert(connection, "android/view/View$OnClickListener", "java/lang/Object", interface=True, methods=(
        ("onClick", "Landroid/view/View;", "V", 0, interface),
    ))
    _insert(connection, "android/widget/EditText", "android/widget/TextView", methods=(
        ("addTextChangedListener", "Landroid/text/TextWatcher;", "V", 0, virtual),
    ))
    _insert(connection, "android/app/AlertDialog$Builder", "java/lang/Object", methods=(
        ("<init>", context, "V", 0, special),
        ("setTitle", char_sequence, builder, 0, virtual),
        ("setMessage", char_sequence, builder, 0, virtual),
        ("setPositiveButton", char_sequence + "Landroid/content/DialogInterface$OnClickListener;", builder, 0, virtual),
        ("show", "", "Landroid/app/AlertDialog;", 0, virtual),
    ))
    _insert(connection, "android/content/pm/PackageManager", "java/lang/Object", fields=(("PERMISSION_GRANTED", "I", 1),))
    _insert(connection, "android/location/LocationManager", "java/lang/Object", methods=(
        ("requestLocationUpdates", "Ljava/lang/String;JFLandroid/location/LocationListener;", "V", 0, virtual),
    ), fields=(("NETWORK_PROVIDER", string, 1),))
    _insert(connection, "android/location/LocationListener", "java/lang/Object", interface=True)
    _insert(connection, "android/location/Location", "java/lang/Object", methods=(
        ("getLatitude", "", "D", 0, virtual),
        ("getLongitude", "", "D", 0, virtual),
    ))
    _insert(connection, "okhttp3/Request$Builder", "java/lang/Object", methods=(
        ("<init>", "", "V", 0, special),
        ("url", string, "Lokhttp3/Request$Builder;", 0, virtual),
        ("build", "", "Lokhttp3/Request;", 0, virtual),
    ))
    _insert(connection, "okhttp3/OkHttpClient", "java/lang/Object", methods=(
        ("<init>", "", "V", 0, special),
        ("newCall", "Lokhttp3/Request;", "Lokhttp3/Call;", 0, virtual),
    ))
    _insert(connection, "okhttp3/Call", "java/lang/Object", interface=True, methods=(
        ("enqueue", "Lokhttp3/Callback;", "V", 0, interface),
    ))
    _insert(connection, "okhttp3/Callback", "java/lang/Object", interface=True)
    _insert(connection, "okhttp3/Response", "java/lang/Object", methods=(
        ("body", "", "Lokhttp3/ResponseBody;", 0, virtual),
    ))
    _insert(connection, "okhttp3/ResponseBody", "java/lang/Object", methods=(("string", "", string, 0, virtual),))
    _insert(connection, "com/google/gson/JsonParser", "java/lang/Object", methods=(
        ("parseString", string, "Lcom/google/gson/JsonElement;", 1, static),
    ))
    _insert(connection, "com/google/gson/JsonElement", "java/lang/Object", methods=(
        ("getAsJsonObject", "", "Lcom/google/gson/JsonObject;", 0, virtual),
        ("getAsJsonObject", string, "Lcom/google/gson/JsonObject;", 0, virtual),
        ("getAsString", "", string, 0, virtual),
    ))
    _insert(connection, "com/google/gson/JsonObject", "com/google/gson/JsonElement", methods=(
        ("get", string, "Lcom/google/gson/JsonElement;", 0, virtual),
    ))
    connection.commit()
    connection.close()


class CatalogCompileTests(unittest.TestCase):
    def test_compiles_helloworld_and_weather_from_fixture(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for template, package, name in (
                ("helloworld", "com.example.helloworld", "Hello"),
                ("weather", "com.example.myweather", "My First Weather App"),
            ):
                destination = root / template
                status = cli.main(
                    [
                        "init",
                        "--template",
                        template,
                        "--pkg",
                        package,
                        "--name",
                        name,
                        str(destination),
                    ]
                )
                self.assertEqual(status, 0)
                _fixture(destination / "jars.db")
                compile_status = compile_project(destination)
                self.assertEqual(compile_status, 0, template)
                self.assertTrue((destination / ".py2apk/j").rglob("*.j"))


class DexSelectionTests(unittest.TestCase):
    def test_only_direct_and_transitive_jars_are_selected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            connection = sqlite3.connect(root / "jars.db")
            _ = connection.execute(
                "CREATE TABLE jars (filename TEXT, kind TEXT)"
            )
            _ = connection.executemany(
                "INSERT INTO jars VALUES (?, ?)",
                [
                    ("jars/okhttp.jar", "direct"),
                    ("jars/okio.jar", "transitive"),
                    ("jars/android.jar", "platform"),
                ],
            )
            connection.commit()
            connection.close()

            selected = [path.name for path in dex_jars(root / "jars.db")]
            self.assertEqual(selected, ["okhttp.jar", "okio.jar"])
            libraries = [path.name for path in library_jars(root / "jars.db")]
            self.assertEqual(libraries, ["android.jar"])

    def test_platform_jar_outside_the_project_keeps_its_cache_path(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cache" / "android.jar"
            cached.parent.mkdir()
            with zipfile.ZipFile(cached, "w"):
                pass
            project = root / "proj"
            project.mkdir()
            write_project_database(
                project / "jars.db",
                [(cached, "platform", "platform:android:34")],
            )
            self.assertEqual(library_jars(project / "jars.db"), [cached.resolve()])
            self.assertFalse((project / "jars" / "android.jar").exists())


class ApiLevelTests(unittest.TestCase):
    def test_parses_own_inherited_field_and_constructor_since(self) -> None:
        payload = b"""<api version="3">
  <class name="android/app/A" since="1">
    <extends class="java/lang/Object"/>
    <method name="&lt;init>()V" since="26"/>
    <method name="m(I)V"/>
    <method name="old()V" since="2" deprecated="28"/>
    <field name="F" since="30"/>
  </class>
  <class name="android/app/B" since="3"/>
</api>
"""

        self.assertEqual(
            api_levels_from_xml(payload),
            [
                ("android/app/A", "<init>()V", 26),
                ("android/app/A", "m(I)V", 1),
                ("android/app/A", "old()V", 2),
                ("android/app/A", "F", 30),
            ],
        )

    def test_catalog_returns_none_when_table_or_row_is_absent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bare = root / "bare.db"
            sqlite3.connect(bare).close()
            catalog = JarCatalog(bare)
            self.assertIsNone(catalog.api_since("android/app/A", "m(I)V"))
            catalog.close()

            cached = root / "cache" / "android.jar"
            cached.parent.mkdir()
            with zipfile.ZipFile(cached, "w"):
                pass
            write_api_table(
                [("android/app/A", "m(I)V", 26)], api_database_path(cached)
            )
            project = root / "proj"
            project.mkdir()
            write_project_database(
                project / "jars.db", [(cached, "platform", "platform:android:34")]
            )
            catalog = JarCatalog(project / "jars.db")
            self.assertEqual(catalog.api_since("android/app/A", "m(I)V"), 26)
            self.assertIsNone(catalog.api_since("android/app/A", "other()V"))
            catalog.close()

    def test_platform_rows_merge_and_direct_rows_do_not(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            platform = root / "cache" / "android.jar"
            platform.parent.mkdir()
            with zipfile.ZipFile(platform, "w"):
                pass
            write_api_table(
                [("android/app/A", "m(I)V", 26)], api_database_path(platform)
            )
            direct = root / "lib" / "direct.jar"
            direct.parent.mkdir()
            with zipfile.ZipFile(direct, "w"):
                pass
            write_api_table(
                [("lib/Direct", "d()V", 9)], api_database_path(direct)
            )
            project = root / "proj"
            project.mkdir()
            write_project_database(
                project / "jars.db",
                [
                    (platform, "platform", "platform:android:34"),
                    (direct, "direct", None),
                ],
            )

            connection = sqlite3.connect(project / "jars.db")
            rows = connection.execute(
                "SELECT owner, member, since FROM api_levels ORDER BY owner"
            ).fetchall()
            connection.close()
            self.assertEqual(rows, [("android/app/A", "m(I)V", 26)])

    def test_fetch_platform_rebuilds_api_table_from_cached_zip(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "proj"
            project.mkdir()
            _ = (project / "apktool.yml").write_text(
                "sdkInfo:\n  minSdkVersion: 21\n  targetSdkVersion: 34\n"
            )
            cache = root / "cache"
            destination = cache / "platforms" / "34"
            destination.mkdir(parents=True)
            with zipfile.ZipFile(destination / "android.jar", "w"):
                pass
            with zipfile.ZipFile(destination / "platform-34_r02.zip", "w") as archive:
                archive.writestr(
                    "android-34/data/api-versions.xml",
                    '<api><class name="android/app/A" since="1">'
                    '<method name="m(I)V" since="26"/></class></api>',
                )

            with mock.patch(
                "Py2APK.cli_fetch_platform._repository_document",
                side_effect=AssertionError("network must not be queried"),
            ):
                _ = fetch_platform(project, cache=cache)

            connection = sqlite3.connect(api_database_path(destination / "android.jar"))
            rows = connection.execute("SELECT owner, member, since FROM api_levels").fetchall()
            connection.close()
            self.assertEqual(rows, [("android/app/A", "m(I)V", 26)])


class SymlinkIndexTests(unittest.TestCase):
    def test_sqlite_stays_beside_the_cached_jar(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cache" / "demo.jar"
            cached.parent.mkdir()
            with zipfile.ZipFile(cached, "w"):
                pass
            project = root / "proj"
            link = project / "jars" / "demo.jar"
            link.parent.mkdir(parents=True)
            link.symlink_to(cached)
            write_project_database(project / "jars.db", [(link, "direct", "com.example:demo:1")])
            self.assertTrue((root / "cache" / "demo.sqlite").is_file())
            self.assertFalse((project / "jars" / "demo.sqlite").exists())
            self.assertEqual(dex_jars(project / "jars.db"), [link])


class DependencyDexTests(unittest.TestCase):
    def test_cached_dex_is_linked_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cache" / "demo.jar"
            cached.parent.mkdir()
            _ = cached.write_bytes(b"jar")
            platform = root / "android.jar"
            r8 = root / "r8.jar"
            _ = platform.write_bytes(b"android")
            _ = r8.write_bytes(b"r8")
            link = root / "proj" / "jars" / "demo.jar"
            link.parent.mkdir(parents=True)
            link.symlink_to(cached)
            calls: list[list[str]] = []

            def run(command: list[str], check: bool = False) -> subprocess.CompletedProcess[bytes]:
                _ = check
                calls.append(command)
                output = Path(command[command.index("--output") + 1])
                _ = (output / "classes.dex").write_bytes(b"dex")
                _ = (output / "classes2.dex").write_bytes(b"dex2")
                return subprocess.CompletedProcess(command, 0)

            with mock.patch("Py2APK.cli_fetch_jars.subprocess.run", side_effect=run):
                first = link_jar_dex(link, min_api=23, platform_jar=platform, r8_jar=r8)
                second = link_jar_dex(link, min_api=23, platform_jar=platform, r8_jar=r8)

            self.assertEqual(len(calls), 1)
            command = calls[0]
            self.assertIn("--intermediate", command)
            self.assertEqual(command[command.index("--min-api") + 1], "23")
            self.assertEqual(command[command.index("--lib") + 1], str(platform))
            self.assertEqual(command[-1], str(cached.resolve()))
            primary = root / "cache" / "demo.min23.dex"
            extra = root / "cache" / "demo.min23-2.dex"
            self.assertEqual(first, [primary, extra])
            self.assertEqual(primary.read_bytes(), b"dex")
            self.assertEqual(extra.read_bytes(), b"dex2")
            self.assertEqual((root / "proj" / "jars" / "demo.dex").resolve(), primary.resolve())
            self.assertEqual((root / "proj" / "jars" / "demo-2.dex").resolve(), extra.resolve())
            self.assertEqual(second, first)

    def test_metadata_only_jar_is_not_dexed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cache" / "okio.jar"
            cached.parent.mkdir()
            with zipfile.ZipFile(cached, "w") as archive:
                archive.writestr("okio/Buffer.kotlin_metadata", b"meta")
            platform = root / "android.jar"
            r8 = root / "r8.jar"
            _ = platform.write_bytes(b"android")
            _ = r8.write_bytes(b"r8")
            link = root / "proj" / "jars" / "okio.jar"
            link.parent.mkdir(parents=True)
            link.symlink_to(cached)

            with mock.patch("Py2APK.cli_fetch_jars.subprocess.run") as run:
                dexes = link_jar_dex(link, min_api=23, platform_jar=platform, r8_jar=r8)

            run.assert_not_called()
            self.assertEqual(dexes, [])
            self.assertFalse((root / "proj" / "jars" / "okio.dex").exists())

    def test_module_info_jar_is_not_dexed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cache" / "lib.jar"
            cached.parent.mkdir()
            with zipfile.ZipFile(cached, "w") as archive:
                archive.writestr("module-info.class", b"module")
                archive.writestr("com/example/package-info.class", b"package")
            platform = root / "android.jar"
            r8 = root / "r8.jar"
            _ = platform.write_bytes(b"android")
            _ = r8.write_bytes(b"r8")
            link = root / "proj" / "jars" / "lib.jar"
            link.parent.mkdir(parents=True)
            link.symlink_to(cached)

            with mock.patch("Py2APK.cli_fetch_jars.subprocess.run") as run:
                dexes = link_jar_dex(link, min_api=23, platform_jar=platform, r8_jar=r8)

            run.assert_not_called()
            self.assertEqual(dexes, [])


class StubTests(unittest.TestCase):
    def test_writes_stubs_for_imported_packages(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _project(root)
            _overload_set_text(root / "jars.db")

            status = write_stubs(root)

            self.assertEqual(status, 0)
            widget = (root / "typings" / "android" / "widget.pyi").read_text(encoding="utf-8")
            view = (root / "typings" / "android" / "view.pyi").read_text(encoding="utf-8")
            self.assertIn("from android.view import View", widget)
            self.assertIn("class TextView(View):", widget)
            self.assertIn("def setText(self, arg0: object) -> None: ...", widget)
            self.assertIn("@overload", widget)
            self.assertIn("VERTICAL: int", widget)
            self.assertNotIn("$", widget)
            self.assertIn("class View(object):", view)
            self.assertFalse((root / "typings" / "android" / "app.pyi").exists())

    def test_leaves_a_newer_stub_alone(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _project(root)
            self.assertEqual(write_stubs(root), 0)
            stub = root / "typings" / "android" / "widget.pyi"
            stub.write_text("sentinel\n", encoding="utf-8")
            newer = (root / "jars.db").stat().st_mtime_ns + 1_000_000_000
            os.utime(stub, ns=(newer, newer))

            self.assertEqual(write_stubs(root), 0)

            self.assertEqual(stub.read_text(encoding="utf-8"), "sentinel\n")

    def test_missing_database_asks_for_fetch(self) -> None:
        with TemporaryDirectory() as temporary:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                status = write_stubs(Path(temporary))

        self.assertEqual(status, 1)
        self.assertIn("fetch_jars", errors.getvalue())

    def test_check_runs_ty(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _project(root)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "ty-args.txt"
            ty = bindir / "ty"
            ty.write_text(
                f"#!{sys.executable}\n"
                "import os\n"
                "import pathlib\n"
                "import sys\n"
                "pathlib.Path(os.environ['TY_LOG']).write_text('\\n'.join(sys.argv))\n",
                encoding="utf-8",
            )
            ty.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(bindir), "TY_LOG": str(log)}):
                status = check_project(root)

            self.assertEqual(status, 0)
            recorded = log.read_text(encoding="utf-8").splitlines()
            self.assertIn("check", recorded)
            self.assertIn("-q", recorded)
            self.assertIn("--extra-search-path", recorded)

    def test_check_explains_a_missing_ty(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _project(root)
            empty = root / "empty"
            empty.mkdir()
            errors = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"PATH": str(empty)}),
                contextlib.redirect_stderr(errors),
            ):
                status = check_project(root)

            self.assertEqual(status, 1)
            self.assertIn("python3 -m pip install ty", errors.getvalue())
            self.assertTrue((root / "typings" / "android" / "widget.pyi").is_file())


def _project(root: Path) -> None:
    _fixture(root / "jars.db")
    source = root / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from android.widget import TextView\nfrom android.view import View\n",
        encoding="utf-8",
    )


def _overload_set_text(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute(
        """
        INSERT INTO methods (jar_id, owner, name, params, result, is_static, opcode)
        VALUES (1, 'android/widget/TextView', 'setText', 'I', 'V', 0, 'invokevirtual')
        """
    )
    connection.commit()
    connection.close()


if __name__ == "__main__":
    unittest.main()
