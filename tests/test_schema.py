from __future__ import annotations

import sqlite3
import tempfile
import unittest

from ordivon_host import EventKind, HostStorage, TaskProjection, TaskState
from ordivon_host.journal import JournalCorruption


def create_task(directory: str) -> None:
    with HostStorage(directory) as storage:
        storage.record_task_event(
            event_id="event:create",
            kind=EventKind.TASK_CREATED,
            payload={"state": "ready"},
            projection=TaskProjection(
                task_id="task:tamper",
                goal_id="goal:tamper",
                state=TaskState.READY,
                active_node_id=None,
                ready_frontier=("node:inspect",),
                revision=1,
                updated_at_ms=1,
            ),
            expected_revision=0,
        )


class HostSchemaTests(unittest.TestCase):
    def test_owned_tables_exist_without_second_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                names = {
                    row[0]
                    for row in storage.journal.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "events",
                        "streams",
                        "task_projection",
                        "leases",
                        "object_refs",
                        "object_validation",
                        "schema_migrations",
                    }.issubset(names)
                )
                self.assertTrue(
                    {"task_nodes", "task_edges", "runtime_links", "wakeups"}.isdisjoint(names)
                )
                self.assertNotIn("semantic_journal", names)

    def test_projection_tampering_is_detected_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            create_task(directory)
            connection = sqlite3.connect(f"{directory}/host.sqlite3")
            connection.execute("UPDATE task_projection SET goal_id = 'goal:forged'")
            connection.commit()
            connection.close()
            with self.assertRaises(JournalCorruption):
                HostStorage(directory)

    def test_event_revision_gap_is_detected_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            create_task(directory)
            connection = sqlite3.connect(f"{directory}/host.sqlite3")
            connection.execute("UPDATE events SET stream_revision = 2")
            connection.execute("UPDATE streams SET revision = 2")
            connection.execute("UPDATE task_projection SET revision = 2")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(JournalCorruption, "not contiguous"):
                HostStorage(directory)

    def test_event_stream_kind_tampering_is_detected_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            create_task(directory)
            connection = sqlite3.connect(f"{directory}/host.sqlite3")
            connection.execute("UPDATE events SET stream_kind = 'goal'")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(JournalCorruption, "stream kind differs"):
                HostStorage(directory)


if __name__ == "__main__":
    unittest.main()
