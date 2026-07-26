from __future__ import annotations

import os
from pathlib import Path
from queue import Queue
import sqlite3
import subprocess
import threading
import sys
import tempfile
import unittest

from anc_canonical import canonical_digest
from ordivon_host import (
    EventAdmission,
    EventKind,
    HostStorage,
    LeaseHeld,
    RevisionConflict,
    TaskProjection,
    TaskState,
)
from ordivon_host.journal import EventConflict, LeaseConflict
from ordivon_host.objects import ObjectCorrupt, ObjectMissing


def projection(
    revision: int,
    *,
    state: TaskState = TaskState.READY,
    updated_at_ms: int | None = None,
) -> TaskProjection:
    active = "node:active" if state is TaskState.RUNNING else None
    frontier = ("node:inspect",) if state is TaskState.READY else ()
    return TaskProjection(
        task_id="task:journal-test",
        goal_id="goal:journal-test",
        state=state,
        active_node_id=active,
        ready_frontier=frontier,
        revision=revision,
        updated_at_ms=revision if updated_at_ms is None else updated_at_ms,
    )


class HostStorageTests(unittest.TestCase):
    def test_event_projection_and_object_ref_commit_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                admitted = storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"taskId": "task:journal-test", "state": "ready"},
                    projection=projection(1),
                    expected_revision=0,
                )
                self.assertEqual(admitted, EventAdmission.CREATED)
                self.assertEqual(storage.journal.event_count(), 1)
                self.assertEqual(storage.journal.object_ref_count(), 1)
                self.assertEqual(storage.journal.get_task("task:journal-test"), projection(1))
                self.assertEqual(storage.rebuild_task("task:journal-test"), projection(1))
                storage.journal.validate_invariants()

            with HostStorage(directory) as reopened:
                self.assertEqual(reopened.journal.get_task("task:journal-test"), projection(1))
                reopened.journal.validate_invariants()

    def test_database_failure_rolls_back_event_object_ref_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.journal.connection.executescript(
                    """
                    CREATE TRIGGER injected_event_failure
                    BEFORE INSERT ON events
                    BEGIN
                        SELECT RAISE(ABORT, 'injected event failure');
                    END;
                    """
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    storage.record_task_event(
                        event_id="event:rollback",
                        kind=EventKind.TASK_CREATED,
                        payload={"revision": 1},
                        projection=projection(1),
                        expected_revision=0,
                    )
                self.assertEqual(storage.journal.event_count(), 0)
                self.assertEqual(storage.journal.object_ref_count(), 0)
                self.assertIsNone(storage.journal.get_task("task:journal-test"))
                storage.journal.validate_invariants()

    def test_revision_conflict_appends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
                with self.assertRaises(RevisionConflict):
                    storage.record_task_event(
                        event_id="event:stale",
                        kind=EventKind.TASK_STATE_CHANGED,
                        payload={"revision": 1, "state": "running"},
                        projection=projection(1, state=TaskState.RUNNING),
                        expected_revision=0,
                    )
                self.assertEqual(storage.journal.event_count(), 1)
                self.assertEqual(storage.journal.get_task("task:journal-test"), projection(1))

    def test_two_writers_racing_same_stream_only_commit_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory):
                pass
            barrier = threading.Barrier(2)
            results: Queue[str] = Queue()

            def write(worker: str) -> None:
                try:
                    with HostStorage(directory) as storage:
                        barrier.wait(timeout=5)
                        storage.record_task_event(
                            event_id=f"event:{worker}",
                            kind=EventKind.TASK_CREATED,
                            payload={"worker": worker},
                            projection=projection(1),
                            expected_revision=0,
                        )
                except RevisionConflict:
                    results.put("conflict")
                except BaseException as error:
                    results.put(f"error:{type(error).__name__}:{error}")
                else:
                    results.put("created")

            writers = [
                threading.Thread(target=write, args=("writer-a",)),
                threading.Thread(target=write, args=("writer-b",)),
            ]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(timeout=10)
                self.assertFalse(writer.is_alive())

            self.assertEqual(
                sorted((results.get(timeout=1), results.get(timeout=1))),
                ["conflict", "created"],
            )
            with HostStorage(directory) as storage:
                self.assertEqual(storage.journal.event_count("task:journal-test"), 1)
                self.assertEqual(
                    storage.journal.get_task("task:journal-test"),
                    projection(1),
                )
                storage.journal.validate_invariants()

    def test_repeated_event_identity_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                arguments = {
                    "event_id": "event:create",
                    "kind": EventKind.TASK_CREATED,
                    "payload": {"revision": 1},
                    "projection": projection(1),
                    "expected_revision": 0,
                }
                self.assertEqual(storage.record_task_event(**arguments), EventAdmission.CREATED)
                self.assertEqual(storage.record_task_event(**arguments), EventAdmission.EXISTING)
                self.assertEqual(storage.journal.event_count(), 1)

    def test_event_identity_binds_resulting_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"same": "data"},
                    projection=projection(1),
                    expected_revision=0,
                )
                with self.assertRaises(EventConflict):
                    storage.record_task_event(
                        event_id="event:create",
                        kind=EventKind.TASK_CREATED,
                        payload={"same": "data"},
                        projection=projection(1, state=TaskState.RUNNING),
                        expected_revision=0,
                    )
                self.assertEqual(storage.journal.event_count(), 1)
                self.assertEqual(
                    storage.journal.get_task("task:journal-test"),
                    projection(1),
                )

    def test_reused_event_identity_with_different_content_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
                with self.assertRaises(EventConflict):
                    storage.record_task_event(
                        event_id="event:create",
                        kind=EventKind.TASK_CREATED,
                        payload={"revision": 999},
                        projection=projection(1),
                        expected_revision=0,
                    )
                self.assertEqual(storage.journal.event_count(), 1)

    def test_task_lease_is_exclusive_and_revisioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
                first = storage.journal.acquire_lease(
                    "task:journal-test", owner_id="host:a", now_ms=10, ttl_ms=100
                )
                with self.assertRaises(LeaseHeld):
                    storage.journal.acquire_lease(
                        "task:journal-test", owner_id="host:b", now_ms=20, ttl_ms=100
                    )
                renewed = storage.journal.acquire_lease(
                    "task:journal-test", owner_id="host:a", now_ms=30, ttl_ms=100
                )
                self.assertEqual(renewed.revision, first.revision + 1)
                with self.assertRaises(LeaseConflict):
                    storage.journal.release_lease(first)
                storage.journal.release_lease(renewed)
                takeover = storage.journal.acquire_lease(
                    "task:journal-test", owner_id="host:b", now_ms=200, ttl_ms=100
                )
                self.assertEqual(takeover.owner_id, "host:b")

    def test_object_kind_participates_in_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                first = storage.objects.put({"value": 1}, kind="first-kind")
                second = storage.objects.put({"value": 1}, kind="second-kind")
                self.assertNotEqual(first.digest, second.digest)
                self.assertEqual(
                    storage.objects.get(first.digest, expected_kind="first-kind"),
                    {"value": 1},
                )
                with self.assertRaises(ObjectCorrupt):
                    storage.objects.get(first.digest, expected_kind="second-kind")

    def test_reopen_fails_when_referenced_object_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
                digest = storage.journal.object_refs()[0].digest
            path = Path(directory) / "objects" / f"{digest[7:]}.json"
            path.unlink()
            with self.assertRaises(ObjectMissing):
                HostStorage(directory)

    def test_object_store_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                stored = storage.objects.put({"value": 1}, kind="test")
                path = Path(directory) / "objects" / f"{stored.digest[7:]}.json"
                path.write_text('{"value":2}')
                with self.assertRaises(ObjectCorrupt):
                    storage.objects.get(stored.digest)

    def test_separate_process_recovers_projection_without_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
            root = Path(__file__).resolve().parents[1]
            protocol = root.parents[1] / "packages" / "ordivon-protocol" / "src"
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(protocol)))
            script = """
from ordivon_host import HostStorage
import sys
with HostStorage(sys.argv[1]) as storage:
    task = storage.journal.get_task('task:journal-test')
    assert task is not None and task.revision == 1 and task.state.value == 'ready'
    storage.journal.validate_invariants()
    print('host-recovery-ok')
"""
            completed = subprocess.run(
                [sys.executable, "-c", script, directory],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertIn("host-recovery-ok", completed.stdout)

    def test_payload_digest_is_canonical(self) -> None:
        self.assertEqual(
            canonical_digest({"a": 1, "b": 2}),
            canonical_digest({"b": 2, "a": 1}),
        )


if __name__ == "__main__":
    unittest.main()
