from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage
from ordivon_host.board import HostMessageBoard
from ordivon_host.journal import JournalCorruption, _schema
from ordivon_host.journal.migrations import (
    _CURRENT_SCHEMA_INDEXES,
    _CURRENT_SCHEMA_METADATA,
    _CURRENT_SCHEMA_TABLES,
)


_V1 = """
CREATE TABLE host_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO host_metadata VALUES ('schema_version', '1');
CREATE TABLE object_refs(digest TEXT PRIMARY KEY, kind TEXT NOT NULL, byte_length INTEGER NOT NULL, first_seen_at_ms INTEGER NOT NULL);
CREATE TABLE streams(stream_id TEXT PRIMARY KEY, stream_kind TEXT NOT NULL, revision INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, stream_id TEXT NOT NULL, stream_kind TEXT NOT NULL, stream_revision INTEGER NOT NULL, event_kind TEXT NOT NULL, payload_digest TEXT NOT NULL, caused_by_event_id TEXT, recorded_at_ms INTEGER NOT NULL);
CREATE TABLE task_projection(task_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, state TEXT NOT NULL, active_node_id TEXT, ready_frontier_json TEXT NOT NULL, revision INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE task_nodes(node_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, node_kind TEXT NOT NULL, node_state TEXT NOT NULL, payload_digest TEXT, revision INTEGER NOT NULL);
CREATE TABLE task_edges(task_id TEXT NOT NULL, from_node_id TEXT NOT NULL, to_node_id TEXT NOT NULL, edge_kind TEXT NOT NULL);
CREATE TABLE runtime_links(dispatch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, effect_id TEXT NOT NULL, binding_id TEXT NOT NULL, workspace_id TEXT, runtime_job_id TEXT, client_request_id TEXT, commit_state TEXT NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE wakeups(wakeup_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, wake_at_ms INTEGER NOT NULL, reason TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE leases(task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);
"""

_V2 = """
CREATE TABLE host_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO host_metadata VALUES ('schema_version', '2');
CREATE TABLE schema_migrations(sequence INTEGER PRIMARY KEY AUTOINCREMENT, from_version INTEGER NOT NULL, to_version INTEGER NOT NULL UNIQUE, name TEXT NOT NULL, backup_path TEXT NOT NULL);
CREATE TABLE object_refs(digest TEXT PRIMARY KEY, kind TEXT NOT NULL, byte_length INTEGER NOT NULL, first_seen_at_ms INTEGER NOT NULL);
CREATE TABLE streams(stream_id TEXT PRIMARY KEY, stream_kind TEXT NOT NULL, revision INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, stream_id TEXT NOT NULL, stream_kind TEXT NOT NULL, stream_revision INTEGER NOT NULL, event_kind TEXT NOT NULL, payload_digest TEXT NOT NULL, caused_by_event_id TEXT, recorded_at_ms INTEGER NOT NULL);
CREATE TABLE task_projection(task_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, state TEXT NOT NULL, active_node_id TEXT, ready_frontier_json TEXT NOT NULL, revision INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE leases(task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);
"""


class HostSchemaMigrationTests(unittest.TestCase):
    def test_empty_v1_reserved_tables_migrate_through_v8_with_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V1)
            connection.close()
            with HostStorage(directory) as storage:
                version = storage.journal.connection.execute(
                    "SELECT value FROM host_metadata WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "8")
                history = storage.journal.connection.execute(
                    "SELECT from_version, to_version, name FROM schema_migrations "
                    "ORDER BY sequence"
                ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in history],
                    [
                        (1, 2, "remove-unowned-pre-h7-tables"),
                        (2, 3, "cache-verified-object-file-identity"),
                        (3, 4, "bind-event-object-admission"),
                        (4, 5, "preserve-namespaced-extension-state"),
                        (5, 6, "add-host-message-board"),
                        (6, 7, "scope-object-reference-validation"),
                        (7, 8, "add-daily-news-projection"),
                    ],
                )
                names = {
                    row[0]
                    for row in storage.journal.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {"task_nodes", "task_edges", "runtime_links", "wakeups"}.isdisjoint(names)
                )
                self.assertIn("object_validation", names)
                self.assertIn("event_object_refs", names)
                self.assertIn("legacy_object_refs", names)
                self.assertIn("task_extension_state", names)
                self.assertIn("board_messages", names)
                indexes = {
                    row[0]
                    for row in storage.journal.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertIn("idx_object_refs_validation_timing_digest", indexes)
                object_ref_columns = {
                    row[1]
                    for row in storage.journal.connection.execute(
                        "PRAGMA table_info(object_refs)"
                    )
                }
                self.assertIn("validation_timing", object_ref_columns)
                self.assertIn("news_editions", names)
                self.assertIn("news_publications", names)
                self.assertEqual(storage.journal.legacy_object_refs(), ())
                self.assertEqual(
                    storage.journal.event_object_refs_start_sequence(), 1
                )
            self._assert_backup_version(database, 2, "1")
            self._assert_backup_version(database, 3, "2")
            self._assert_backup_version(database, 4, "3")
            self._assert_backup_version(database, 5, "4")
            self._assert_backup_version(database, 6, "5")
            self._assert_backup_version(database, 7, "6")
            self._assert_backup_version(database, 8, "7")

    def test_current_schema_fast_path_requirements_match_canonical_schema(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(_schema.SCHEMA)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            indexes = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                )
            }
            metadata = {
                str(row[0])
                for row in connection.execute("SELECT key FROM host_metadata")
            }
        finally:
            connection.close()
        self.assertEqual(tables, _CURRENT_SCHEMA_TABLES)
        self.assertEqual(indexes, _CURRENT_SCHEMA_INDEXES)
        self.assertEqual(metadata, _CURRENT_SCHEMA_METADATA)

    def test_current_schema_open_does_not_replay_ddl_under_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "host.sqlite3"
            with HostStorage(root):
                pass

            blocker = sqlite3.connect(database, isolation_level=None)
            blocker.execute("BEGIN IMMEDIATE")
            try:
                with HostStorage(
                    root,
                    validation_mode="targeted",
                    update_validation_cache=False,
                ) as storage:
                    self.assertEqual(storage.journal.task_count(), 0)
            finally:
                blocker.execute("ROLLBACK")
                blocker.close()

    def test_current_schema_missing_named_index_fails_closed_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "host.sqlite3"
            with HostStorage(root):
                pass
            connection = sqlite3.connect(database)
            connection.execute("DROP INDEX idx_object_refs_validation_timing_digest")
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                JournalCorruption,
                "schema shape differs: missing=index:idx_object_refs_validation_timing_digest",
            ):
                HostStorage(
                    root,
                    validation_mode="targeted",
                    update_validation_cache=False,
                )
            connection = sqlite3.connect(database)
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='index' "
                        "AND name='idx_object_refs_validation_timing_digest'"
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_current_schema_missing_board_table_preserves_evidence_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "host.sqlite3"
            with HostStorage(root) as storage:
                receipt = HostMessageBoard(storage).post(
                    client_message_id="msg:test:strict-current-schema",
                    author_label="agent:test",
                    message_kind="note",
                    message="Retain evidence instead of self-healing its pointer table.",
                    topic="schema",
                    reply_to_client_message_id=None,
                    recorded_at_ms=1,
                )
                digest = receipt.message.message_digest
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE board_messages")
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                JournalCorruption, "schema shape differs: missing=table:board_messages"
            ):
                HostStorage(root)
            connection = sqlite3.connect(database)
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='board_messages'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM object_refs WHERE digest = ?", (digest,)
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_current_schema_constraint_weakening_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "host.sqlite3"
            with HostStorage(root):
                pass
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE leases")
            connection.execute(
                "CREATE TABLE leases("
                "task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
                "revision INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL)"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(
                JournalCorruption, "schema shape differs: changed=table:leases"
            ):
                HostStorage(root)

    def test_canonical_current_shape_is_allowed_with_start_one_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            with HostStorage(directory):
                pass
            connection = sqlite3.connect(database)
            for from_version, to_version, name in (
                (1, 2, "remove-unowned-pre-h7-tables"),
                (2, 3, "cache-verified-object-file-identity"),
                (3, 4, "bind-event-object-admission"),
                (4, 5, "preserve-namespaced-extension-state"),
                (5, 6, "add-host-message-board"),
                (6, 7, "scope-object-reference-validation"),
                (7, 8, "add-daily-news-projection"),
            ):
                connection.execute(
                    "INSERT INTO schema_migrations("
                    "from_version, to_version, name, backup_path"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        from_version,
                        to_version,
                        name,
                        str(database.with_name(f"host.sqlite3.pre-schema-v{to_version}.sqlite3")),
                    ),
                )
            connection.commit()
            connection.close()
            with HostStorage(directory) as storage:
                self.assertEqual(storage.journal.task_count(), 0)

    def test_legacy_lineage_does_not_grant_partial_table_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            with HostStorage(directory):
                pass
            connection = sqlite3.connect(database)
            for from_version, to_version, name in (
                (2, 3, "cache-verified-object-file-identity"),
                (3, 4, "bind-event-object-admission"),
                (4, 5, "preserve-namespaced-extension-state"),
                (5, 6, "add-host-message-board"),
                (6, 7, "scope-object-reference-validation"),
                (7, 8, "add-daily-news-projection"),
            ):
                connection.execute(
                    "INSERT INTO schema_migrations("
                    "from_version, to_version, name, backup_path"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        from_version,
                        to_version,
                        name,
                        str(database.with_name(f"host.sqlite3.pre-schema-v{to_version}.sqlite3")),
                    ),
                )
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE leases")
            connection.execute(
                "CREATE TABLE leases("
                "task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
                "revision INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL)"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(
                JournalCorruption, r"schema shape differs: changed=.*table:leases"
            ):
                HostStorage(directory)

    def test_current_schema_validates_migration_history_before_legacy_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V2)
            connection.close()
            with HostStorage(directory):
                pass
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE schema_migrations SET name = 'forged' WHERE sequence = 1"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(
                JournalCorruption, "migration history differs"
            ):
                HostStorage(directory)

    def test_v2_migrates_through_v8_with_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V2)
            connection.close()
            with HostStorage(directory) as storage:
                self.assertEqual(
                    storage.journal.connection.execute(
                        "SELECT value FROM host_metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "8",
                )
                history = storage.journal.connection.execute(
                    "SELECT from_version, to_version, name FROM schema_migrations "
                    "ORDER BY sequence"
                ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in history],
                    [
                        (2, 3, "cache-verified-object-file-identity"),
                        (3, 4, "bind-event-object-admission"),
                        (4, 5, "preserve-namespaced-extension-state"),
                        (5, 6, "add-host-message-board"),
                        (6, 7, "scope-object-reference-validation"),
                        (7, 8, "add-daily-news-projection"),
                    ],
                )
            self._assert_backup_version(database, 3, "2")
            self._assert_backup_version(database, 4, "3")
            self._assert_backup_version(database, 5, "4")
            self._assert_backup_version(database, 6, "5")
            self._assert_backup_version(database, 7, "6")
            self._assert_backup_version(database, 8, "7")

    def test_v6_board_refs_migrate_to_on_access_without_reinterpreting_v6_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "host.sqlite3"
            with HostStorage(root) as storage:
                receipt = HostMessageBoard(storage).post(
                    client_message_id="msg:test:v6-migration",
                    author_label="agent:test",
                    message_kind="note",
                    message="Preserve an already-materialized schema-v6 Board authority.",
                    topic="migration",
                    reply_to_client_message_id=None,
                    recorded_at_ms=1,
                )

            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE news_publications")
            connection.execute("DROP TABLE news_editions")
            connection.execute("DROP INDEX IF EXISTS idx_object_refs_validation_timing_digest")
            connection.execute("ALTER TABLE object_refs DROP COLUMN validation_timing")
            connection.execute("DELETE FROM schema_migrations WHERE to_version > 6")
            connection.execute(
                "UPDATE host_metadata SET value = '6' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            with HostStorage(root) as storage:
                self.assertEqual(
                    storage.journal.connection.execute(
                        "SELECT value FROM host_metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "8",
                )
                retained = storage.journal.object_ref(receipt.message.message_digest)
                self.assertIsNotNone(retained)
                self.assertEqual(retained[1], "on_access")
                self.assertEqual(storage.validation_summary.object_refs, 0)
                listing = HostMessageBoard(storage).list(limit=10)
                self.assertEqual(listing["messageCount"], 1)
                self.assertEqual(
                    listing["messages"][0]["clientMessageId"],
                    "msg:test:v6-migration",
                )
            self._assert_backup_version(database, 7, "6")
            self._assert_backup_version(database, 8, "7")

    def test_v3_history_is_legacy_and_new_events_use_exact_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            with HostStorage(directory) as storage:
                HostKernel(
                    storage, clock_ms=lambda: 1, owner_id="host:v3-fixture"
                ).create_task(
                    event_id="event:v3-fixture:legacy",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:v3-fixture:legacy",
                    goal_id="goal:v3-fixture",
                    payload={"workloadId": "v3-fixture-legacy"},
                    frontier=("node:v3-fixture:legacy",),
                )

            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE news_publications")
            connection.execute("DROP TABLE news_editions")
            connection.execute("DROP TABLE board_messages")
            connection.execute("DROP INDEX IF EXISTS idx_object_refs_validation_timing_digest")
            connection.execute("ALTER TABLE object_refs DROP COLUMN validation_timing")
            connection.execute("DROP TABLE task_extension_state")
            connection.execute("DROP TABLE event_object_refs")
            connection.execute("DROP TABLE legacy_object_refs")
            connection.execute(
                "DELETE FROM host_metadata "
                "WHERE key = 'event_object_refs_start_sequence'"
            )
            connection.execute("DELETE FROM schema_migrations WHERE to_version > 3")
            connection.execute(
                "UPDATE host_metadata SET value = '3' "
                "WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            with HostStorage(directory) as storage:
                self.assertEqual(
                    storage.journal.event_object_refs_start_sequence(), 2
                )
                self.assertEqual(
                    storage.journal.event_object_references(
                        "event:v3-fixture:legacy"
                    ),
                    (),
                )
                legacy_payload = storage.journal.connection.execute(
                    "SELECT payload_digest FROM events WHERE event_id = ?",
                    ("event:v3-fixture:legacy",),
                ).fetchone()[0]
                self.assertIn(
                    legacy_payload,
                    {item.digest for item in storage.journal.legacy_object_refs()},
                )
                HostKernel(
                    storage, clock_ms=lambda: 2, owner_id="host:v4-fixture"
                ).create_task(
                    event_id="event:v4-fixture:exact",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:v4-fixture:exact",
                    goal_id="goal:v3-fixture",
                    payload={"workloadId": "v4-fixture-exact"},
                    frontier=("node:v4-fixture:exact",),
                )
                exact = storage.journal.event_object_references(
                    "event:v4-fixture:exact"
                )
                self.assertEqual(len(exact), 1)
                self.assertEqual(exact[0].role, "payload")
            self._assert_backup_version(database, 4, "3")

    def test_populated_unowned_v1_table_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V1)
            connection.execute(
                "INSERT INTO wakeups VALUES ('wakeup:1', 'task:1', 1, 'test', 'ready')"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(JournalCorruption, "wakeups"):
                HostStorage(directory)
            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM host_metadata WHERE key='schema_version'"
                ).fetchone()[0],
                "1",
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM wakeups").fetchone()[0], 1)
            connection.close()

    def test_nonempty_database_without_host_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(JournalCorruption, "metadata is missing"):
                HostStorage(directory)

    def test_valid_stale_migration_backup_is_archived_before_current_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V2)
            connection.close()
            stale = Path(f"{database}.pre-schema-v3.sqlite3")
            other = sqlite3.connect(stale)
            other.executescript(_V2)
            other.execute("INSERT INTO host_metadata VALUES ('stale-marker', 'other')")
            other.commit()
            other.close()

            with HostStorage(directory):
                pass

            archives = list(
                Path(directory).glob(
                    "host.sqlite3.pre-schema-v3.superseded-*.sqlite3"
                )
            )
            self.assertEqual(len(archives), 1)
            current = sqlite3.connect(stale)
            try:
                self.assertEqual(
                    current.execute(
                        "SELECT value FROM host_metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "2",
                )
                self.assertIsNone(
                    current.execute(
                        "SELECT value FROM host_metadata WHERE key='stale-marker'"
                    ).fetchone()
                )
                self.assertEqual(current.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            finally:
                current.close()
            archived = sqlite3.connect(archives[0])
            try:
                self.assertEqual(
                    archived.execute(
                        "SELECT value FROM host_metadata WHERE key='stale-marker'"
                    ).fetchone()[0],
                    "other",
                )
            finally:
                archived.close()
            self.assertFalse(Path(f"{stale}-wal").exists())
            self.assertFalse(Path(f"{stale}-shm").exists())

    def test_invalid_existing_migration_backup_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V2)
            connection.close()
            stale = Path(f"{database}.pre-schema-v3.sqlite3")
            stale.write_bytes(b"not-a-sqlite-database")
            with self.assertRaisesRegex(JournalCorruption, "backup"):
                HostStorage(directory)

    def test_unsupported_future_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V1)
            connection.execute(
                "UPDATE host_metadata SET value='99' WHERE key='schema_version'"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(JournalCorruption, "99"):
                HostStorage(directory)

    def _assert_backup_version(
        self, database: Path, target_version: int, expected: str
    ) -> None:
        backup = Path(f"{database}.pre-schema-v{target_version}.sqlite3")
        self.assertTrue(backup.is_file())
        connection = sqlite3.connect(backup)
        try:
            actual = connection.execute(
                "SELECT value FROM host_metadata WHERE key='schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(actual, expected)
