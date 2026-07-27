from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from . import _schema


class SchemaMigrationError(RuntimeError):
    pass


def initialize_schema(connection: sqlite3.Connection, path: Path) -> None:
    if not _table_exists(connection, "host_metadata"):
        connection.executescript(_schema.SCHEMA)
        return
    version = schema_version(connection)
    if version == 1:
        _migrate_v1_to_v2(connection, path)
    elif version != _schema.SCHEMA_VERSION:
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
    _ensure_backup(connection, backup_path)
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
        changed = connection.execute(
            "UPDATE host_metadata SET value = '2' "
            "WHERE key = 'schema_version' AND value = '1'"
        ).rowcount
        if changed != 1:
            raise SchemaMigrationError("Host Journal schema changed during migration")
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


def _ensure_backup(connection: sqlite3.Connection, path: Path) -> None:
    if path.exists():
        backup = sqlite3.connect(path)
        try:
            quick = backup.execute("PRAGMA quick_check").fetchall()
            row = backup.execute(
                "SELECT value FROM host_metadata WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            backup.close()
        if quick != [("ok",)] or row is None or row[0] != "1":
            raise SchemaMigrationError("existing schema migration backup is invalid")
        return
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
