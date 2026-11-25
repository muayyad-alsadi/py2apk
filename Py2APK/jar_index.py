"""SQLite indexes of jar members and the merged project ``jars.db``."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from tqdm.auto import tqdm

from Py2APK.classfile import (
    ClassFileError,
    ParsedClass,
    parse_class,
    split_method_descriptor,
)

MEMBER_SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    internal TEXT PRIMARY KEY,
    super_internal TEXT,
    is_interface INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS class_interfaces (
    owner TEXT NOT NULL,
    interface TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS methods (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT NOT NULL,
    is_static INTEGER NOT NULL,
    opcode TEXT NOT NULL,
    param_names TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fields (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    is_static INTEGER NOT NULL
);
"""

PROJECT_SCHEMA = """
CREATE TABLE jars (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    coordinate TEXT,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE classes (
    jar_id INTEGER NOT NULL,
    internal TEXT NOT NULL,
    super_internal TEXT,
    is_interface INTEGER NOT NULL
);
CREATE TABLE class_interfaces (
    jar_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    interface TEXT NOT NULL
);
CREATE TABLE methods (
    jar_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT NOT NULL,
    is_static INTEGER NOT NULL,
    opcode TEXT NOT NULL,
    param_names TEXT NOT NULL DEFAULT ''
);
CREATE TABLE fields (
    jar_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    is_static INTEGER NOT NULL
);
"""


def index_jar(jar_path: Path) -> Path:
    """Write ``jar.sqlite`` beside the cached jar and return that database path.

    ``jar_path`` may be a project symlink. The database is stored next to the
    real file, in the same cache directory as a Maven ``.pom``, so the slow
    class scan runs once per cached jar.
    """
    real_jar = jar_path.resolve()
    database_path = real_jar.with_suffix(".sqlite")
    stray = jar_path.with_suffix(".sqlite")
    if stray.resolve() != database_path:
        if stray.is_file() and not database_path.is_file():
            database_path.parent.mkdir(parents=True, exist_ok=True)
            stray.replace(database_path)
        elif stray.is_file():
            stray.unlink()
    if database_path.is_file() and not _sqlite_has_param_names(database_path):
        database_path.unlink()
    if (
        database_path.is_file()
        and database_path.stat().st_mtime_ns >= real_jar.stat().st_mtime_ns
    ):
        return database_path
    with zipfile.ZipFile(real_jar) as archive:
        names = [info.filename for info in archive.infolist() if info.filename.endswith(".class")]
        connection = sqlite3.connect(database_path)
        try:
            _ = connection.executescript(MEMBER_SCHEMA)
            _ = connection.execute("DELETE FROM fields")
            _ = connection.execute("DELETE FROM methods")
            _ = connection.execute("DELETE FROM class_interfaces")
            _ = connection.execute("DELETE FROM classes")
            with _progress(names, desc=f"Indexing {jar_path.name}", unit="class") as progress:
                for name in progress:
                    try:
                        parsed = parse_class(archive.read(name))
                    except ClassFileError:
                        continue
                    _insert_class(connection, parsed, jar_id=None)
            connection.commit()
        finally:
            connection.close()
    return database_path


def write_project_database(
    database_path: Path,
    jars: Sequence[tuple[Path, str, str | None]],
) -> None:
    """Rewrite ``jars.db`` from ``(jar, kind, coordinate)`` rows."""
    temporary = database_path.with_name(database_path.name + ".part")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        _ = connection.executescript(PROJECT_SCHEMA)
        for jar_path, kind, coordinate in jars:
            digest = hashlib.sha256(jar_path.read_bytes()).hexdigest()
            filename = _project_filename(database_path.parent, jar_path)
            cursor = connection.execute(
                "INSERT INTO jars (filename, coordinate, kind, sha256) VALUES (?, ?, ?, ?)",
                (filename, coordinate, kind, digest),
            )
            row_id = cursor.lastrowid
            if row_id is None:
                raise sqlite3.DatabaseError("insert into jars returned no row id")
            jar_id = row_id
            member_db = index_jar(jar_path)
            source = sqlite3.connect(member_db)
            try:
                _copy_members(connection, source, jar_id, jar_path.name)
            finally:
                source.close()
        connection.commit()
    finally:
        connection.close()
    temporary.replace(database_path)


class JarCatalog:
    """Read-only view of a project ``jars.db``."""

    def __init__(self, database_path: Path) -> None:
        self.connection = sqlite3.connect(database_path)
        self._has_param_names = _table_has_column(self.connection, "methods", "param_names")

    def close(self) -> None:
        self.connection.close()

    def super_of(self, internal: str) -> str | None:
        row = self.connection.execute(
            "SELECT super_internal FROM classes WHERE internal = ? LIMIT 1",
            (internal,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def is_interface(self, internal: str) -> bool:
        row = self.connection.execute(
            "SELECT is_interface FROM classes WHERE internal = ? LIMIT 1",
            (internal,),
        ).fetchone()
        return bool(row and row[0])

    def interfaces_of(self, internal: str) -> list[str]:
        return [
            str(row[0])
            for row in self.connection.execute(
                "SELECT interface FROM class_interfaces WHERE owner = ?",
                (internal,),
            )
        ]

    def interface_methods(self, owner: str) -> list[tuple[str, str, str]]:
        """Return instance methods declared on one interface."""
        return [
            (str(name), str(params), str(result))
            for name, params, result, is_static in self.connection.execute(
                "SELECT name, params, result, is_static FROM methods WHERE owner = ?",
                (owner,),
            )
            if not is_static and str(name) not in {"<init>", "<clinit>"}
        ]

    def methods(
        self, owner: str, name: str
    ) -> list[tuple[str, str, str, bool, str, tuple[str, ...]]]:
        if self._has_param_names:
            query = (
                "SELECT params, result, owner, is_static, opcode, param_names "
                "FROM methods WHERE owner = ? AND name = ?"
            )
        else:
            query = (
                "SELECT params, result, owner, is_static, opcode "
                "FROM methods WHERE owner = ? AND name = ?"
            )
        found: list[tuple[str, str, str, bool, str, tuple[str, ...]]] = []
        for row in self.connection.execute(query, (owner, name)):
            names = _split_param_names(row[5]) if self._has_param_names else ()
            found.append(
                (str(row[0]), str(row[1]), str(row[2]), bool(row[3]), str(row[4]), names)
            )
        return found

    def field(self, owner: str, name: str) -> tuple[str, bool] | None:
        row = self.connection.execute(
            "SELECT descriptor, is_static FROM fields WHERE owner = ? AND name = ? LIMIT 1",
            (owner, name),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), bool(row[1])

    def has_package(self, package: str) -> bool:
        """Return whether any class lives in ``package`` (``android/widget``)."""
        prefix = package.strip("/") + "/"
        row = self.connection.execute(
            "SELECT 1 FROM classes WHERE internal LIKE ? ESCAPE '\\' LIMIT 1",
            (_like_prefix(prefix),),
        ).fetchone()
        return row is not None

    def classes_in_package(self, package: str) -> list[tuple[str, str | None, bool]]:
        """Return top-level classes whose internal name is ``package/Name``."""
        prefix = package.strip("/") + "/"
        rows = self.connection.execute(
            """
            SELECT internal, MIN(super_internal), MAX(is_interface)
            FROM classes
            WHERE internal LIKE ? ESCAPE '\\'
              AND internal NOT LIKE ? ESCAPE '\\'
              AND instr(internal, '$') = 0
            GROUP BY internal
            ORDER BY internal
            """,
            (_like_prefix(prefix), _like_nested(prefix)),
        ).fetchall()
        return [
            (str(internal), None if super_name is None else str(super_name), bool(is_interface))
            for internal, super_name, is_interface in rows
        ]

    def methods_of(self, owner: str) -> list[tuple[str, str, str, bool]]:
        """Return ``(name, params, result, is_static)`` for one class."""
        return [
            (str(name), str(params), str(result), bool(is_static))
            for name, params, result, is_static in self.connection.execute(
                """
                SELECT name, params, result, is_static
                FROM methods
                WHERE owner = ? AND name != '<clinit>'
                GROUP BY name, params, result, is_static
                ORDER BY name, params
                """,
                (owner,),
            )
        ]

    def fields_of(self, owner: str) -> list[tuple[str, str, bool]]:
        """Return ``(name, descriptor, is_static)`` for one class."""
        return [
            (str(name), str(descriptor), bool(is_static))
            for name, descriptor, is_static in self.connection.execute(
                """
                SELECT name, descriptor, is_static
                FROM fields
                WHERE owner = ?
                GROUP BY name, descriptor, is_static
                ORDER BY name
                """,
                (owner,),
            )
        ]


def dex_jars(database_path: Path) -> list[Path]:
    """Return project jars that D8 should compile into the dex."""
    return _jars_of_kind(database_path, ("direct", "transitive"))


def library_jars(database_path: Path) -> list[Path]:
    """Return platform jars D8 should read with ``--lib`` and not dex."""
    return _jars_of_kind(database_path, ("platform",))


def _jars_of_kind(database_path: Path, kinds: tuple[str, ...]) -> list[Path]:
    if not database_path.is_file():
        return []
    marks = ", ".join("?" for _ in kinds)
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            f"SELECT filename FROM jars WHERE kind IN ({marks}) ORDER BY filename",
            kinds,
        ).fetchall()
    finally:
        connection.close()
    root = database_path.parent
    return [root / str(row[0]) for row in rows]


def _progress(items, *, desc: str, unit: str):
    """A stderr bar that updates while a jar is indexed or merged."""
    return tqdm(
        items,
        desc=desc,
        unit=unit,
        file=sys.stderr,
        mininterval=0.2,
        dynamic_ncols=True,
        leave=True,
    )


def _copy_members(
    destination: sqlite3.Connection,
    source: sqlite3.Connection,
    jar_id: int,
    label: str,
) -> None:
    tables = (
        (
            "classes",
            "internal, super_internal, is_interface",
            "?, ?, ?",
        ),
        (
            "class_interfaces",
            "owner, interface",
            "?, ?",
        ),
        (
            "methods",
            "owner, name, params, result, is_static, opcode, param_names",
            "?, ?, ?, ?, ?, ?, ?",
        ),
        (
            "fields",
            "owner, name, descriptor, is_static",
            "?, ?, ?, ?",
        ),
    )
    total = 0
    for table, _columns, _marks in tables:
        total += int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    progress = tqdm(
        total=total,
        desc=f"Merging {label}",
        unit="row",
        file=sys.stderr,
        mininterval=0.2,
        dynamic_ncols=True,
        leave=True,
    )
    copied = 0
    try:
        for table, columns, marks in tables:
            cursor = source.execute(f"SELECT {columns} FROM {table}")
            batch: list[tuple[object, ...]] = []
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                batch.extend((jar_id, *row) for row in rows)
                if len(batch) >= 1000:
                    _ = destination.executemany(
                        f"INSERT INTO {table} (jar_id, {columns}) VALUES (?, {marks})",
                        batch,
                    )
                    copied += len(batch)
                    progress.update(len(batch))
                    batch = []
            if batch:
                _ = destination.executemany(
                    f"INSERT INTO {table} (jar_id, {columns}) VALUES (?, {marks})",
                    batch,
                )
                copied += len(batch)
                progress.update(len(batch))
    finally:
        progress.close()
    if copied != total:
        raise RuntimeError(f"merged {copied} rows from {label}, expected {total}")


def _insert_class(connection: sqlite3.Connection, parsed: ParsedClass, jar_id: int | None) -> None:
    prefix: tuple[object, ...] = () if jar_id is None else (jar_id,)
    columns = "" if jar_id is None else "jar_id, "
    marks = "" if jar_id is None else "?, "
    _ = connection.execute(
        f"INSERT INTO classes ({columns}internal, super_internal, is_interface) VALUES ({marks}?, ?, ?)",
        (*prefix, parsed.internal, parsed.super_internal, int(parsed.is_interface)),
    )
    _ = connection.executemany(
        f"INSERT INTO class_interfaces ({columns}owner, interface) VALUES ({marks}?, ?)",
        [(*prefix, parsed.internal, name) for name in parsed.interfaces],
    )
    method_rows = []
    for method in parsed.methods:
        params, result = split_method_descriptor(method.descriptor)
        method_rows.append(
            (
                *prefix,
                method.owner,
                method.name,
                params,
                result,
                int(method.is_static),
                method.opcode,
                _join_param_names(method.param_names),
            )
        )
    _ = connection.executemany(
        f"INSERT INTO methods ({columns}owner, name, params, result, is_static, opcode, param_names) "
        f"VALUES ({marks}?, ?, ?, ?, ?, ?, ?)",
        method_rows,
    )
    _ = connection.executemany(
        f"INSERT INTO fields ({columns}owner, name, descriptor, is_static) VALUES ({marks}?, ?, ?, ?)",
        [
            (*prefix, field.owner, field.name, field.descriptor, int(field.is_static))
            for field in parsed.fields
        ],
    )


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _sqlite_has_param_names(database_path: Path) -> bool:
    connection = sqlite3.connect(database_path)
    try:
        return _table_has_column(connection, "methods", "param_names")
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


def _split_param_names(text: object) -> tuple[str, ...]:
    if text is None:
        return ()
    raw = str(text)
    if raw == "":
        return ()
    return tuple(raw.split(","))


def _join_param_names(names: tuple[str, ...]) -> str:
    return ",".join(names)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_prefix(prefix: str) -> str:
    """Escape a class-name prefix and add a SQL ``LIKE`` wildcard."""
    return _like_escape(prefix) + "%"


def _like_nested(prefix: str) -> str:
    """Match ``prefix`` plus one or more extra package segments."""
    return _like_escape(prefix) + "%/%"


def _project_filename(project_root: Path, jar_path: Path) -> str:
    """Return a project-relative path, or the cache path for a platform jar."""
    try:
        return jar_path.absolute().relative_to(project_root.absolute()).as_posix()
    except ValueError:
        return jar_path.resolve().as_posix()
