from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage
from ordivon_host.journal import JournalCorruption


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
    def test_empty_v1_reserved_tables_migrate_through_v5_with_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(_V1)
            connection.close()
            with HostStorage(directory) as storage:
                version = storage.journal.connection.execute(
                    "SELECT value FROM host_metadata WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "5")
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
                self.assertEqual(storage.journal.legacy_object_refs(), ())
                self.assertEqual(
                    storage.journal.event_object_refs_start_sequence(), 1
                )
            self._assert_backup_version(database, 2, "1")
            self._assert_backup_version(database, 3, "2")
            self._assert_backup_version(database, 4, "3")
            self._assert_backup_version(database, 5, "4")

    def test_v2_migrates_through_v5_with_backups(self) -> None:
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
                    "5",
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
                    ],
                )
            self._assert_backup_version(database, 3, "2")
            self._assert_backup_version(database, 4, "3")
            self._assert_backup_version(database, 5, "4")

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
            connection.execute("DROP TABLE task_extension_state")
            connection.execute("DROP TABLE event_object_refs")
            connection.execute("DROP TABLE legacy_object_refs")
            connection.execute(
                "DELETE FROM host_metadata "
                "WHERE key = 'event_object_refs_start_sequence'"
            )
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

    def test_existing_migration_backup_must_match_current_database(self) -> None:
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
            with self.assertRaisesRegex(JournalCorruption, "does not match"):
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
