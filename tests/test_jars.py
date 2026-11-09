"""Tests for jar indexing, platform selection, and catalog compilation."""

from __future__ import annotations

import asyncio
import sqlite3
import struct
import subprocess
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from Py2APK import cli
from Py2APK.classfile import parse_class
from Py2APK.cli_compile import compile_project
from Py2APK.cli_fetch_jars import (
    _extract_classes_jar,
    describe_repositories,
    link_jar_dex,
)
from Py2APK.maven_resolve import parse_dependency_file
from Py2APK.cli_fetch_platform import select_platform_archive
from Py2APK.downloader import PARALLEL_DOWNLOADS, download_files
from Py2APK.jar_index import dex_jars, library_jars, write_project_database
from Py2APK.maven_resolve import resolve_artifacts


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
            _extract_classes_jar(aar, jar)
            self.assertEqual(jar.read_bytes(), b"class-bytes")


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
                        async def read(inner) -> bytes:  # noqa: N805
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
            with mock.patch.dict("sys.modules", {"aiohttp": None}):
                with mock.patch("Py2APK.downloader._download_urllib") as urllib_download:
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
        methods=(("valueOf", "D", string, 1, static),),
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
    _insert(connection, "android/text/TextWatcher", "java/lang/Object", interface=True)
    _insert(
        connection,
        "android/text/Editable",
        "java/lang/Object",
        interface=True,
        interfaces=("java/lang/CharSequence",),
    )
    _insert(connection, "android/view/View$OnClickListener", "java/lang/Object", interface=True)
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
                self.assertTrue((destination / "target/j").rglob("*.j"))


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


if __name__ == "__main__":
    unittest.main()
