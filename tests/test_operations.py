from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ordivon_host import EventKind, HostKernel, HostStorage, TaskProjection, TaskState
from ordivon_host.ops import (
    create_backup,
    doctor_state,
    inspect_state,
    plan_gc,
    restore_backup,
    verify_backup,
)


def populate(root: Path) -> None:
    with HostStorage(root) as storage:
        storage.record_task_event(
            event_id="event:ops-create",
            kind=EventKind.TASK_CREATED,
            payload={"purpose": "operations-test"},
            projection=TaskProjection(
                task_id="task:ops",
                goal_id="goal:ops",
                state=TaskState.READY,
                active_node_id=None,
                ready_frontier=("node:ops",),
                revision=1,
                updated_at_ms=1,
            ),
            expected_revision=0,
        )


class HostOperationsTests(unittest.TestCase):
    def test_inspect_counts_directly_without_bounded_task_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            populate(root)
            with patch(
                "ordivon_host.ops.inspect.list_tasks",
                side_effect=AssertionError("inspect must not use bounded listing"),
            ):
                inspection = inspect_state(root)
            self.assertEqual(inspection["tasks"], 1)

    def test_doctor_reports_and_hardens_legacy_public_state_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            populate(root)
            (root / "objects").chmod(0o755)
            (root / "host.sqlite3").chmod(0o644)
            report = doctor_state(root)
            self.assertTrue(report["healthy"])
            check = next(
                item for item in report["checks"] if item["name"] == "state.permissions"
            )
            self.assertEqual(check["status"], "warning")
            self.assertIn("hardened on open", check["detail"])
            self.assertEqual((root / "objects").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "host.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_inspect_doctor_and_gc_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            populate(root)
            inspection = inspect_state(root)
            self.assertEqual(inspection["schemaVersion"], 4)
            self.assertEqual(inspection["tasks"], 1)
            self.assertEqual(inspection["terminalTasks"], 0)
            report = doctor_state(root, now_ms=10)
            self.assertTrue(report["healthy"])
            orphan = root / "objects" / ("f" * 64 + ".json")
            orphan.write_text("{}")
            plan = plan_gc(root)
            self.assertEqual(plan["orphanedObjects"], [orphan.name])
            self.assertFalse(plan["deleteAllowed"])

    def test_backup_verify_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            backup = base / "backup"
            restored = base / "restored"
            populate(source)
            manifest = create_backup(source, backup, created_at_ms=1_000)
            self.assertEqual(manifest["hostJournalSchemaVersion"], 4)
            verified = verify_backup(backup)
            self.assertEqual(verified["kind"], "ordivon.host-backup-manifest")
            result = restore_backup(backup, restored)
            self.assertTrue(result["restored"])
            self.assertEqual(inspect_state(restored)["tasks"], 1)

    def test_replace_restore_preserves_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            backup = base / "backup"
            target = base / "target"
            populate(source)
            create_backup(source, backup)
            with HostStorage(target):
                pass
            result = restore_backup(backup, target, replace=True)
            previous = result["previousRoot"]
            self.assertIsInstance(previous, str)
            self.assertTrue(Path(previous).is_dir())
            self.assertEqual(inspect_state(target)["tasks"], 1)

    def test_backup_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            backup = base / "backup"
            populate(source)
            create_backup(source, backup)
            (backup / "host.sqlite3").write_bytes(b"forged")
            with self.assertRaises(ValueError):
                verify_backup(backup)

class HostHistoryDoctorTests(unittest.TestCase):
    def _two_event_state(self, root: Path) -> None:
        with HostStorage(root) as storage:
            first = TaskProjection(
                task_id="task:history",
                goal_id="goal:history",
                state=TaskState.READY,
                active_node_id=None,
                ready_frontier=("node:history:work",),
                revision=1,
                updated_at_ms=1,
            )
            storage.record_task_event(
                event_id="event:history:r1",
                kind=EventKind.TASK_CREATED,
                payload={"stage": "created"},
                projection=first,
                expected_revision=0,
            )
            kernel = HostKernel(
                storage,
                clock_ms=iter((2, 2)).__next__,
                owner_id="host:history-test",
            )
            with kernel.locked_task(
                first.task_id,
                expected_revision=1,
                expected_state=TaskState.READY,
                expected_frontier=first.ready_frontier,
            ) as locked:
                locked.commit(
                    event_id="event:history:r2",
                    kind=EventKind.TASK_STATE_CHANGED,
                    payload={"stage": "completed"},
                    state=TaskState.COMPLETED,
                    frontier=(),
                )

    def test_history_doctor_validates_every_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._two_event_state(root)
            report = doctor_state(root, check_history=True)
            self.assertTrue(report["healthy"])
            check = next(
                item for item in report["checks"] if item["name"] == "journal.history"
            )
            self.assertEqual(check["status"], "ok")
            self.assertIn('"events": 2', check["detail"])

    def test_history_doctor_catches_old_row_bound_to_newer_payload(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._two_event_state(root)
            database = root / "host.sqlite3"
            connection = sqlite3.connect(database)
            latest = connection.execute(
                "SELECT payload_digest FROM events WHERE stream_revision = 2"
            ).fetchone()[0]
            connection.execute(
                "UPDATE events SET payload_digest = ? WHERE stream_revision = 1",
                (latest,),
            )
            connection.commit()
            connection.close()
            baseline = doctor_state(root)
            self.assertFalse(baseline["healthy"])
            opening = next(
                item
                for item in baseline["checks"]
                if item["name"] == "host.open"
            )
            self.assertEqual(opening["status"], "error")
            self.assertIn("payload object edge", opening["detail"])

            report = doctor_state(root, check_history=True)
            self.assertFalse(report["healthy"])
