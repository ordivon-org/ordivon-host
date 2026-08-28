from __future__ import annotations

import hashlib
import os
from functools import cache
from pathlib import Path
import re
import sqlite3
import tempfile

from . import _schema


class SchemaMigrationError(RuntimeError):
    pass


_MIGRATION_NAME_BY_TO_VERSION = {
    2: "remove-unowned-pre-h7-tables",
    3: "cache-verified-object-file-identity",
    4: "bind-event-object-admission",
    5: "preserve-namespaced-extension-state",
    6: "add-host-message-board",
    7: "scope-object-reference-validation",
    8: "add-daily-news-projection",
}

_SCHEMA_SQL_TOKEN = re.compile(
    r"""
    '(?:''|[^'])*'
    | "(?:""|[^"])*"
    | `(?:``|[^`])*`
    | \[(?:\]\]|[^\]])*\]
    | [A-Za-z_][A-Za-z0-9_]*
    | \d+(?:\.\d+)?
    | >=|<=|<>|!=|==|\|\||<<|>>
    | [(),=<>+\-*/%.;]
    """,
    re.VERBOSE,
)

_LEGACY_V1_V2_FINAL_TABLE_SQL = {
    "object_refs": """
        CREATE TABLE object_refs(
            digest TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            byte_length INTEGER NOT NULL,
            first_seen_at_ms INTEGER NOT NULL,
            validation_timing TEXT NOT NULL DEFAULT 'startup'
                CHECK(validation_timing IN ('startup', 'on_access'))
        )
    """,
    "streams": """
        CREATE TABLE streams(
            stream_id TEXT PRIMARY KEY,
            stream_kind TEXT NOT NULL,
            revision INTEGER NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )
    """,
    "events": """
        CREATE TABLE events(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            stream_id TEXT NOT NULL,
            stream_kind TEXT NOT NULL,
            stream_revision INTEGER NOT NULL,
            event_kind TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            caused_by_event_id TEXT,
            recorded_at_ms INTEGER NOT NULL
        )
    """,
    "task_projection": """
        CREATE TABLE task_projection(
            task_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            state TEXT NOT NULL,
            active_node_id TEXT,
            ready_frontier_json TEXT NOT NULL,
            revision INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )
    """,
    "leases": """
        CREATE TABLE leases(
            task_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            expires_at_ms INTEGER NOT NULL
        )
    """,
}

_LEGACY_V2_SCHEMA_MIGRATIONS_SQL = """
    CREATE TABLE schema_migrations(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        backup_path TEXT NOT NULL
    )
"""

_CURRENT_SCHEMA_TABLES = frozenset({
    "host_metadata", "schema_migrations", "object_refs", "object_validation",
    "streams", "events", "legacy_object_refs", "event_object_refs",
    "task_projection", "task_extension_state", "leases", "board_messages",
    "news_editions", "news_publications",
})
_CURRENT_SCHEMA_INDEXES = frozenset({
    "idx_object_refs_validation_timing_digest",
    "event_object_refs_one_payload",
    "news_editions_date_id",
})
_CURRENT_SCHEMA_METADATA = frozenset({
    "schema_version", "event_object_refs_start_sequence"
})


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
        _validate_current_schema(connection, path)
        return
    migrated = False
    while True:
        version = schema_version(connection)
        if version == _schema.SCHEMA_VERSION:
            if not migrated:
                _validate_current_schema(connection, path)
                return
            break
        if version == 1:
            _migrate_v1_to_v2(connection, path)
            migrated = True
            continue
        if version == 2:
            _migrate_v2_to_v3(connection, path)
            migrated = True
            continue
        if version == 3:
            _migrate_v3_to_v4(connection, path)
            migrated = True
            continue
        if version == 4:
            _migrate_v4_to_v5(connection, path)
            migrated = True
            continue
        if version == 5:
            _migrate_v5_to_v6(connection, path)
            migrated = True
            continue
        if version == 6:
            _migrate_v6_to_v7(connection, path)
            migrated = True
            continue
        if version == 7:
            _migrate_v7_to_v8(connection, path)
            migrated = True
            continue
        raise SchemaMigrationError(f"unsupported Host Journal schema version: {version}")
    connection.executescript(_schema.SCHEMA)
    _validate_current_schema(connection, path)
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

    for row in connection.execute(
        "SELECT event_id, event_kind FROM events "
        "WHERE stream_kind = 'task' ORDER BY sequence"
    ):
        try:
            kind = EventKind(str(row["event_kind"]))
        except ValueError as error:
            raise SchemaMigrationError(
                f"schema-v4 Task Event kind is invalid: {row['event_kind']}"
            ) from error
        if kind.name == "EXTENSION":
            raise SchemaMigrationError(
                "schema-v4 extension state requires a pre-0.5 Host client "
                f"for owner recovery/export before upgrade: {row['event_id']}:{kind.value}"
            )

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

def _migrate_v5_to_v6(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.pre-schema-v6.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=5)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE board_messages("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "client_message_id TEXT NOT NULL UNIQUE, "
            "author_label TEXT NOT NULL, "
            "message_kind TEXT NOT NULL CHECK(message_kind IN ('note', 'question', 'proposal', 'warning', 'reply')), "
            "topic TEXT, "
            "message_digest TEXT NOT NULL REFERENCES object_refs(digest), "
            "reply_to_client_message_id TEXT REFERENCES board_messages(client_message_id), "
            "recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0))"
        )
        _advance_version(connection, 5, 6)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (5, 6, 'add-host-message-board', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _migrate_v6_to_v7(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.pre-schema-v7.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=6)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "ALTER TABLE object_refs ADD COLUMN validation_timing TEXT NOT NULL "
            "DEFAULT 'startup' CHECK(validation_timing IN ('startup', 'on_access'))"
        )
        # Existing v6 Board CAS can move off the startup-critical validation path only
        # when Board is its sole durable use. Any Task/Event/extension reference keeps
        # the stronger startup classification.
        connection.execute(
            "UPDATE object_refs SET validation_timing = 'on_access' "
            "WHERE kind = 'host-board-message' "
            "AND EXISTS (SELECT 1 FROM board_messages b WHERE b.message_digest = object_refs.digest) "
            "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.payload_digest = object_refs.digest) "
            "AND NOT EXISTS (SELECT 1 FROM event_object_refs r WHERE r.digest = object_refs.digest) "
            "AND NOT EXISTS (SELECT 1 FROM legacy_object_refs l WHERE l.digest = object_refs.digest) "
            "AND NOT EXISTS (SELECT 1 FROM task_extension_state s WHERE s.state_digest = object_refs.digest)"
        )
        _advance_version(connection, 6, 7)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (6, 7, 'scope-object-reference-validation', ?)",
            (str(backup_path),),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")



def _migrate_v7_to_v8(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.pre-schema-v8.sqlite3")
    _ensure_backup(connection, backup_path, expected_version=7)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE news_editions("
            "edition_id TEXT PRIMARY KEY, "
            "edition_date TEXT NOT NULL, "
            "timezone TEXT NOT NULL, "
            "current_revision INTEGER NOT NULL CHECK(current_revision >= 1), "
            "current_digest TEXT NOT NULL REFERENCES object_refs(digest), "
            "created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0), "
            "updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms))"
        )
        connection.execute(
            "CREATE INDEX news_editions_date_id "
            "ON news_editions(edition_date DESC, edition_id ASC)"
        )
        connection.execute(
            "CREATE TABLE news_publications("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "client_publish_id TEXT NOT NULL UNIQUE, "
            "edition_id TEXT NOT NULL REFERENCES news_editions(edition_id) ON DELETE CASCADE, "
            "edition_date TEXT NOT NULL, "
            "timezone TEXT NOT NULL, "
            "expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0), "
            "revision INTEGER NOT NULL CHECK(revision >= 1), "
            "edition_digest TEXT NOT NULL REFERENCES object_refs(digest), "
            "recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0), "
            "UNIQUE(edition_id, revision))"
        )
        _advance_version(connection, 7, 8)
        connection.execute(
            "INSERT INTO schema_migrations(from_version, to_version, name, backup_path) "
            "VALUES (7, 8, 'add-daily-news-projection', ?)",
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
        temporary_digest = _sha256_file(temporary)
        if path.exists():
            _validate_backup(path, expected_version=expected_version)
            existing_digest = _sha256_file(path)
            if existing_digest == temporary_digest:
                return
            _archive_superseded_backup(
                path,
                expected_version=expected_version,
                digest=existing_digest,
            )
        _remove_sqlite_sidecars(path)
        os.replace(temporary, path)
        _fsync(path)
    finally:
        temporary.unlink(missing_ok=True)
        _remove_sqlite_sidecars(temporary)


def _archive_superseded_backup(
    path: Path,
    *,
    expected_version: int,
    digest: str,
) -> Path:
    wal = Path(f"{path}-wal")
    if wal.exists() and wal.stat().st_size != 0:
        raise SchemaMigrationError(
            "existing schema migration backup has pending WAL state"
        )
    archive = path.with_name(
        f"{path.stem}.superseded-{digest[:16]}{path.suffix}"
    )
    if archive.exists():
        _validate_backup(archive, expected_version=expected_version)
        if _sha256_file(archive) != digest:
            raise SchemaMigrationError(
                "schema migration backup archive identity collision"
            )
        path.unlink()
    else:
        os.replace(path, archive)
        _fsync(archive)
    _remove_sqlite_sidecars(path)
    return archive


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _write_backup(connection: sqlite3.Connection, path: Path) -> None:
    backup = sqlite3.connect(path)
    try:
        connection.backup(backup)
        mode = backup.execute("PRAGMA journal_mode = DELETE").fetchone()
        if mode is None or str(mode[0]).lower() != "delete":
            raise SchemaMigrationError(
                "schema migration backup did not become standalone"
            )
        if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SchemaMigrationError("schema migration backup failed quick_check")
    except BaseException:
        backup.close()
        path.unlink(missing_ok=True)
        _remove_sqlite_sidecars(path)
        raise
    else:
        backup.close()
    _remove_sqlite_sidecars(path)
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


def _schema_sql_tokens(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    sql = str(value)
    tokens: list[str] = []
    position = 0
    for match in _SCHEMA_SQL_TOKEN.finditer(sql):
        gap = sql[position : match.start()]
        if gap and not gap.isspace():
            raise SchemaMigrationError(
                f"Host Journal schema SQL contains unsupported syntax near: {gap!r}"
            )
        token = match.group(0)
        if token[0].isalpha() or token[0] == "_":
            token = token.upper()
        tokens.append(token)
        position = match.end()
    tail = sql[position:]
    if tail and not tail.isspace():
        raise SchemaMigrationError(
            f"Host Journal schema SQL contains unsupported syntax near: {tail!r}"
        )
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        if (
            index + 2 < len(tokens)
            and tokens[index : index + 3] == ["IF", "NOT", "EXISTS"]
        ):
            index += 3
            continue
        normalized.append(tokens[index])
        index += 1
    return tuple(normalized)


@cache
def _canonical_schema_shape() -> dict[tuple[str, str], tuple[str, ...] | None]:
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(_schema.SCHEMA)
        return _schema_shape(expected)
    finally:
        expected.close()


def _schema_shape(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[str, ...] | None]:
    return {
        (str(row[0]), str(row[1])): _schema_sql_tokens(row[2])
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    }


def _validated_migration_start_version(
    connection: sqlite3.Connection, path: Path
) -> int:
    rows = connection.execute(
        "SELECT sequence, from_version, to_version, name, backup_path "
        "FROM schema_migrations ORDER BY sequence"
    ).fetchall()
    if not rows:
        return _schema.SCHEMA_VERSION
    start = int(rows[0]["from_version"])
    if start < 1 or start >= _schema.SCHEMA_VERSION:
        raise SchemaMigrationError("Host Journal migration history start is invalid")
    if len(rows) != _schema.SCHEMA_VERSION - start:
        raise SchemaMigrationError("Host Journal migration history is incomplete")
    for offset, row in enumerate(rows):
        expected_from = start + offset
        expected_to = expected_from + 1
        expected_name = _MIGRATION_NAME_BY_TO_VERSION[expected_to]
        expected_backup = f"{path.name}.pre-schema-v{expected_to}.sqlite3"
        if (
            int(row["sequence"]) != offset + 1
            or int(row["from_version"]) != expected_from
            or int(row["to_version"]) != expected_to
            or str(row["name"]) != expected_name
            or Path(str(row["backup_path"])).name != expected_backup
        ):
            raise SchemaMigrationError("Host Journal migration history differs")
    return start


def _expected_schema_shape(
    *, migration_start_version: int
) -> dict[tuple[str, str], tuple[str, ...] | None]:
    expected = dict(_canonical_schema_shape())
    if migration_start_version in {1, 2}:
        for table, sql in _LEGACY_V1_V2_FINAL_TABLE_SQL.items():
            expected[("table", table)] = _schema_sql_tokens(sql)
    if migration_start_version == 2:
        expected[("table", "schema_migrations")] = _schema_sql_tokens(
            _LEGACY_V2_SCHEMA_MIGRATIONS_SQL
        )
    return expected


def _validate_current_schema(connection: sqlite3.Connection, path: Path) -> None:
    metadata = {
        str(row[0])
        for row in connection.execute("SELECT key FROM host_metadata ORDER BY key")
    }
    expected_metadata = {"schema_version", "event_object_refs_start_sequence"}
    if metadata != expected_metadata:
        missing = sorted(expected_metadata - metadata)
        unexpected = sorted(metadata - expected_metadata)
        raise SchemaMigrationError(
            "Host Journal metadata shape differs: "
            f"missing={missing}; unexpected={unexpected}"
        )
    migration_start = _validated_migration_start_version(connection, path)
    expected = _expected_schema_shape(migration_start_version=migration_start)
    actual = _schema_shape(connection)
    if actual == expected:
        return
    expected_keys = set(expected)
    actual_keys = set(actual)
    missing = sorted(expected_keys - actual_keys)
    unexpected = sorted(actual_keys - expected_keys)
    changed = sorted(
        key for key in expected_keys & actual_keys if expected[key] != actual[key]
    )
    parts: list[str] = []
    if missing:
        parts.append("missing=" + ",".join(f"{kind}:{name}" for kind, name in missing))
    if unexpected:
        parts.append(
            "unexpected=" + ",".join(f"{kind}:{name}" for kind, name in unexpected)
        )
    if changed:
        parts.append("changed=" + ",".join(f"{kind}:{name}" for kind, name in changed))
    raise SchemaMigrationError("Host Journal schema shape differs: " + "; ".join(parts))


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
