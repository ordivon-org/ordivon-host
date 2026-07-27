from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host import EventKind, HostStorage, TaskProjection, TaskState
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
    def test_inspect_doctor_and_gc_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            populate(root)
            inspection = inspect_state(root)
            self.assertEqual(inspection["schemaVersion"], 3)
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
            self.assertEqual(manifest["hostJournalSchemaVersion"], 3)
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
