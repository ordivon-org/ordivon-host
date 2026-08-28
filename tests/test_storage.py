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
                self.assertEqual(
                    {(item.digest, item.role) for item in storage.journal.event_object_references("event:create")},
                    {(storage.journal.get_task_head("task:journal-test").payload_digest, "payload")},
                )
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
                self.assertEqual(storage.journal.event_object_references("event:rollback"), ())
                self.assertIsNone(storage.journal.get_task("task:journal-test"))
                storage.journal.validate_invariants()

    def test_event_records_exact_payload_and_reference_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                reference = storage.put_object(
                    {"evidence": "exact-event-edge"}, kind="test-evidence"
                )
                storage.record_task_event(
                    event_id="event:exact-edges",
                    kind=EventKind.TASK_CREATED,
                    payload={"evidenceObjectDigest": reference.digest},
                    projection=projection(1),
                    expected_revision=0,
                    referenced_objects=(reference,),
                )
                head = storage.journal.get_task_head("task:journal-test")
                assert head is not None
                self.assertEqual(
                    {(item.digest, item.role) for item in storage.journal.event_object_references("event:exact-edges")},
                    {
                        (head.payload_digest, "payload"),
                        (reference.digest, "reference"),
                    },
                )

    def test_existing_event_rejects_different_object_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                first = storage.put_object({"value": 1}, kind="test-reference")
                second = storage.put_object({"value": 2}, kind="test-reference")
                arguments = {
                    "event_id": "event:edge-identity",
                    "kind": EventKind.TASK_CREATED,
                    "payload": {"revision": 1},
                    "projection": projection(1),
                    "expected_revision": 0,
                }
                storage.record_task_event(
                    **arguments, referenced_objects=(first,)
                )
                with self.assertRaisesRegex(
                    EventConflict, "different object references"
                ):
                    storage.record_task_event(
                        **arguments, referenced_objects=(second,)
                    )

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
                self.assertEqual(storage.journal.lease_records(), (first,))
                self.assertEqual(storage.journal.quick_check(), ("ok",))
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

    def test_object_store_get_with_metadata_loads_payload_and_exact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                stored = storage.objects.put({"value": 1}, kind="test-metadata")
                value, metadata = storage.objects.get_with_metadata(stored.digest)
                self.assertEqual(value, {"value": 1})
                self.assertEqual(metadata, stored)

                path = Path(directory) / "objects" / f"{stored.digest[7:]}.json"
                path.write_text('{"value":2}')
                with self.assertRaises(ObjectCorrupt):
                    storage.objects.get_with_metadata(stored.digest)

    def test_object_store_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                stored = storage.objects.put({"value": 1}, kind="test")
                path = Path(directory) / "objects" / f"{stored.digest[7:]}.json"
                path.write_text('{"value":2}')
                with self.assertRaises(ObjectCorrupt):
                    storage.objects.get(stored.digest)

    def test_projection_validation_rows_remain_self_consistent_after_concurrent_advance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as reader:
                reader.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
                rows = reader.journal.task_projection_validation_rows()
                self.assertEqual(len(rows), 1)

                with HostStorage(directory) as writer:
                    lease = writer.journal.acquire_lease(
                        "task:journal-test",
                        owner_id="host:test:writer",
                        now_ms=10,
                        ttl_ms=100,
                    )
                    writer.record_task_event(
                        event_id="event:advance",
                        kind=EventKind.TASK_STATE_CHANGED,
                        payload={"revision": 2},
                        projection=projection(
                            2, state=TaskState.RUNNING, updated_at_ms=11
                        ),
                        expected_revision=1,
                        expected_lease=lease,
                        lease_checked_at_ms=11,
                    )

                materialized, pointer = rows[0]
                self.assertEqual(materialized, projection(1))
                self.assertEqual(pointer.revision, 1)
                self.assertEqual(
                    reader._read_task_event_pointer(pointer).projection, projection(1)
                )

            with HostStorage(directory) as reopened:
                self.assertEqual(
                    reopened.journal.get_task("task:journal-test"),
                    projection(2, state=TaskState.RUNNING, updated_at_ms=11),
                )

    def test_validation_cache_skips_unchanged_historical_object_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
            with HostStorage(directory) as first_reopen:
                self.assertEqual(first_reopen.validation_summary.hashed_objects, 1)
                self.assertEqual(first_reopen.validation_summary.cached_objects, 0)
                self.assertEqual(first_reopen.journal.object_validation_count(), 1)
            with HostStorage(directory) as second_reopen:
                self.assertEqual(second_reopen.validation_summary.hashed_objects, 0)
                self.assertEqual(second_reopen.validation_summary.cached_objects, 1)
                self.assertEqual(second_reopen.validation_summary.task_heads, 1)

    def test_full_validation_rehashes_cached_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                )
            with HostStorage(directory):
                pass
            with HostStorage(directory, validation_mode="full") as full:
                self.assertTrue(full.validation_summary.full)
                self.assertEqual(full.validation_summary.hashed_objects, 1)
                self.assertEqual(full.validation_summary.cached_objects, 0)

    def test_cached_object_metadata_change_forces_hash_and_rejects_tampering(self) -> None:
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
            with HostStorage(directory):
                pass
            path = Path(directory) / "objects" / f"{digest[7:]}.json"
            path.write_text('{"forged":true}')
            with self.assertRaises(ObjectCorrupt):
                HostStorage(directory)

    def test_targeted_validation_defers_unrelated_reference_failure_to_global_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                reference = storage.put_object({"evidence": 1}, kind="test-evidence")
                storage.record_task_event(
                    event_id="event:create",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                    referenced_objects=(reference,),
                )
            path = Path(directory) / "objects" / f"{reference.digest[7:]}.json"
            path.unlink()

            with HostStorage(directory, validation_mode="targeted") as targeted:
                self.assertEqual(targeted.read_task_event("task:journal-test").projection, projection(1))
                self.assertEqual(targeted.validation_summary.object_refs, 0)
                self.assertEqual(targeted.validation_summary.hashed_objects, 0)
                self.assertEqual(targeted.validation_summary.task_heads, 0)

            with self.assertRaises(ObjectMissing):
                HostStorage(directory)

    def test_targeted_object_read_hardens_only_consumed_legacy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                first = storage.put_object({"value": 1}, kind="test")
                second = storage.put_object({"value": 2}, kind="test")
                storage.record_task_event(
                    event_id="event:mode-hardening",
                    kind=EventKind.TASK_CREATED,
                    payload={"revision": 1},
                    projection=projection(1),
                    expected_revision=0,
                    referenced_objects=(first, second),
                )
            first_path = Path(directory) / "objects" / f"{first.digest[7:]}.json"
            second_path = Path(directory) / "objects" / f"{second.digest[7:]}.json"
            os.chmod(first_path, 0o644)
            os.chmod(second_path, 0o644)

            with HostStorage(directory, validation_mode="targeted") as targeted:
                self.assertEqual(targeted.objects.get(first.digest, expected_kind="test"), {"value": 1})
                self.assertEqual(first_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(second_path.stat().st_mode & 0o777, 0o644)

            with HostStorage(directory):
                pass
            self.assertEqual(second_path.stat().st_mode & 0o777, 0o600)

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
            env = dict(os.environ)
            inherited_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(
                path
                for path in (str(root / "src"), inherited_pythonpath)
                if path
            )
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
