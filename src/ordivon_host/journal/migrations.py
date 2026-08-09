from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile

from . import _schema


class SchemaMigrationError(RuntimeError):
    pass


def initialize_schema(connection: sqlite3.Connection, path: Path) -> None:
    if not _table_exists(connection, "host_metadata"):
        existing = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        if existing:
            raise SchemaMigrationError(
                "Host Journal metadata is missing from a non-empty database: "
                + ", ".join(existing)
            )
        connection.executescript(_schema.SCHEMA)
        return
    while True:
        version = schema_version(connection)
        if version == _schema.SCHEMA_VERSION:
            break
        if version == 1:
            _migrate_v1_to_v2(connection, path)
            continue
        if version == 2:
            _migrate_v2_to_v3(connection, path)
            continue
        if version == 3:
            _migrate_v3_to_v4(connection, path)
            continue
        if version == 4:
            _migrate_v4_to_v5(connection, path)
            continue
        raise SchemaMigrationError(f"unsupported Host Journal schema version: {version}")
    connection.executescript(_schema.SCHEMA)
    if schema_version(connection) != _schema.SCHEMA_VERSION:
        raise SchemaMigrationError("Host Journal schema initialization did not converge")


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM host_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise SchemaMigrationError("Host Journal schema version is missing")
    try:
        return int(row[0])
    except (TypeError, ValueError) as error:
        raise SchemaMigrationError("Host Journal schema version is invalid") from error


def migration_history(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        "SELECT sequence, from_version, to_version, name, backup_path "
        "FROM schema_migrations ORDER BY sequence"
    ).fetchall()
    return tuple(
        {
            "sequence": int(row[0]),
            "fromVersion": int(row[1]),
            "toVersion": int(row[2]),
            "name": row[3],
            "backupPath": row[4],
        }
        for row in rows
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection, path: Path) -> None:
    populated: list[str] = []
    for table in _schema.LEGACY_UNUSED_TABLES:
        if not _table_exists(connection, table):
            continue
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if int(count) != 0:
            populated.append(table)
    if populated:
        raise SchemaMigrationError(
            "legacy unowned Host tables contain state: " + ", ".join(populated)
        )
    backup_path = path.with_name(f"{path.name}.pre-schema-v2.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=1)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "from_version INTEGER NOT NULL CHECK(from_version >= 1), "
            "to_version INTEGER NOT NULL UNIQUE CHECK(to_version > from_version), "
            "name TEXT NOT NULL, backup_path TEXT NOT NULL)"
        )
        for table in _schema.LEGACY_UNUSED_TABLES:
            if _table_exists(connection, table):
                connection.execute(f"DROP TABLE {table}")
        _advance_version(connection, 1, 2)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (1, 2, 'remove-unowned-pre-h7-tables', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _migrate_v2_to_v3(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.pre-schema-v3.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=2)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE object_validation("
            "digest TEXT PRIMARY KEY REFERENCES object_refs(digest) ON DELETE CASCADE, "
            "device INTEGER NOT NULL CHECK(device >= 0), "
            "inode INTEGER NOT NULL CHECK(inode >= 0), "
            "byte_length INTEGER NOT NULL CHECK(byte_length >= 0), "
            "modified_at_ns INTEGER NOT NULL CHECK(modified_at_ns >= 0), "
            "changed_at_ns INTEGER NOT NULL CHECK(changed_at_ns >= 0), "
            "mode INTEGER NOT NULL CHECK(mode >= 0))"
        )
        _advance_version(connection, 2, 3)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (2, 3, 'cache-verified-object-file-identity', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _migrate_v3_to_v4(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.pre-schema-v4.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=3)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE legacy_object_refs("
            "digest TEXT PRIMARY KEY REFERENCES object_refs(digest))"
        )
        connection.execute(
            "INSERT INTO legacy_object_refs(digest) SELECT digest FROM object_refs"
        )
        connection.execute(
            "CREATE TABLE event_object_refs("
            "event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, "
            "digest TEXT NOT NULL REFERENCES object_refs(digest), "
            "role TEXT NOT NULL CHECK(role IN ('payload', 'reference')), "
            "PRIMARY KEY(event_id, digest))"
        )
        connection.execute(
            "CREATE UNIQUE INDEX event_object_refs_one_payload "
            "ON event_object_refs(event_id) WHERE role = 'payload'"
        )
        next_sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events"
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO host_metadata(key, value) VALUES "
            "('event_object_refs_start_sequence', ?)",
            (str(next_sequence),),
        )
        _advance_version(connection, 3, 4)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (3, 4, 'bind-event-object-admission', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")



def _migrate_v4_to_v5(connection: sqlite3.Connection, path: Path) -> None:
    from ..domain import EventKind

    backup_path = path.with_name(f"{path.name}.pre-schema-v5.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=4)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE task_extension_state("
            "task_id TEXT NOT NULL REFERENCES task_projection(task_id) ON DELETE CASCADE, "
            "namespace TEXT NOT NULL, "
            "state_digest TEXT NOT NULL REFERENCES object_refs(digest), "
            "event_id TEXT NOT NULL REFERENCES events(event_id), "
            "revision INTEGER NOT NULL CHECK(revision >= 1), "
            "legacy INTEGER NOT NULL CHECK(legacy IN (0, 1)), "
            "PRIMARY KEY(task_id, namespace))"
        )
        latest: dict[tuple[str, str], sqlite3.Row] = {}
        rows = connection.execute(
            "SELECT event_id, stream_id, stream_revision, event_kind, payload_digest "
            "FROM events WHERE stream_kind = 'task' ORDER BY stream_id, stream_revision"
        ).fetchall()
        for row in rows:
            kind = EventKind(str(row["event_kind"]))
            if kind.name != "EXTENSION":
                continue
            latest[(str(row["stream_id"]), kind.namespace)] = row
        for (task_id, namespace), row in sorted(latest.items()):
            connection.execute(
                "INSERT INTO task_extension_state("
                "task_id, namespace, state_digest, event_id, revision, legacy"
                ") VALUES (?, ?, ?, ?, ?, 1)",
                (
                    task_id,
                    namespace,
                    str(row["payload_digest"]),
                    str(row["event_id"]),
                    int(row["stream_revision"]),
                ),
            )
        _advance_version(connection, 4, 5)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (4, 5, 'preserve-namespaced-extension-state', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")

def _advance_version(
    connection: sqlite3.Connection, from_version: int, to_version: int
) -> None:
    changed = connection.execute(
        "UPDATE host_metadata SET value = ? "
        "WHERE key = 'schema_version' AND value = ?",
        (str(to_version), str(from_version)),
    ).rowcount
    if changed != 1:
        raise SchemaMigrationError("Host Journal schema changed during migration")


def _ensure_backup(
    connection: sqlite3.Connection,
    path: Path,
    *,
    expected_version: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.current-", suffix=".sqlite3", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)
    try:
        _write_backup(connection, temporary)
        _validate_backup(temporary, expected_version=expected_version)
        if path.exists():
            _validate_backup(path, expected_version=expected_version)
            if _sha256_file(path) != _sha256_file(temporary):
                raise SchemaMigrationError(
                    "existing schema migration backup does not match the current database"
                )
            return
        os.replace(temporary, path)
        _fsync(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_backup(connection: sqlite3.Connection, path: Path) -> None:
    backup = sqlite3.connect(path)
    try:
        connection.backup(backup)
        if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SchemaMigrationError("schema migration backup failed quick_check")
    except BaseException:
        backup.close()
        path.unlink(missing_ok=True)
        raise
    else:
        backup.close()
    _fsync(path)


def _validate_backup(path: Path, *, expected_version: int) -> None:
    backup = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        quick = backup.execute("PRAGMA quick_check").fetchall()
        row = backup.execute(
            "SELECT value FROM host_metadata WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error as error:
        raise SchemaMigrationError("schema migration backup cannot be read") from error
    finally:
        backup.close()
    if quick != [("ok",)] or row is None or row[0] != str(expected_version):
        raise SchemaMigrationError("existing schema migration backup is invalid")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _fsync(path: Path) -> None:
    file_fd = os.open(path, os.O_RDONLY)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(file_fd)
        os.fsync(directory_fd)
    finally:
        os.close(file_fd)
        os.close(directory_fd)
