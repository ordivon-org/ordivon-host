from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from ordivon_host import EventKind, HostStorage, TaskProjection, TaskState
from ordivon_host.cli import main


class HostCliTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        output = stdout.getvalue() if code == 0 else stderr.getvalue()
        return code, json.loads(output)

    def test_init_inspect_doctor_backup_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            backup = Path(directory) / "backup"
            restored = Path(directory) / "restored"
            code, result = self.invoke("--state-root", str(state), "init")
            self.assertEqual(code, 0)
            self.assertEqual(result["schemaVersion"], 3)
            code, result = self.invoke("--state-root", str(state), "doctor")
            self.assertEqual(code, 0)
            self.assertTrue(result["healthy"])
            code, result = self.invoke(
                "--state-root", str(state), "backup", str(backup)
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["kind"], "ordivon.host-backup-manifest")
            code, result = self.invoke(
                "--state-root", str(restored), "restore", str(backup)
            )
            self.assertEqual(code, 0)
            self.assertTrue(result["restored"])

    def test_history_doctor_and_recovery_assessment_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with HostStorage(state) as storage:
                storage.record_task_event(
                    event_id="event:cli-assess:r1",
                    kind=EventKind.TASK_CREATED,
                    payload={"purpose": "cli-assessment"},
                    projection=TaskProjection(
                        task_id="task:cli-assess",
                        goal_id="goal:cli-assess",
                        state=TaskState.READY,
                        active_node_id=None,
                        ready_frontier=("node:cli-assess",),
                        revision=1,
                        updated_at_ms=1,
                    ),
                    expected_revision=0,
                )
            code, result = self.invoke(
                "--state-root", str(state), "doctor", "--history"
            )
            self.assertEqual(code, 0)
            history = next(
                item for item in result["checks"] if item["name"] == "journal.history"
            )
            self.assertEqual(history["status"], "ok")
            code, result = self.invoke(
                "--state-root",
                str(state),
                "task",
                "assess",
                "task:cli-assess",
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["action"], "unsupported")
            self.assertFalse(result["automatic"])
            code, result = self.invoke(
                "--state-root",
                str(state),
                "task",
                "reconcile",
                "task:cli-assess",
            )
            self.assertEqual(code, 0)
            self.assertFalse(result["changed"])
            self.assertEqual(result["before"]["action"], "unsupported")

    def test_task_list_and_missing_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            self.invoke("--state-root", str(state), "init")
            code, result = self.invoke(
                "--state-root", str(state), "task", "list"
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["tasks"], [])
            code, result = self.invoke(
                "--state-root", str(state), "task", "show", "task:missing"
            )
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])

    def test_task_handoff_is_deterministic_and_revision_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with HostStorage(state) as storage:
                storage.record_task_event(
                    event_id="event:cli-handoff:r1",
                    kind=EventKind.TASK_CREATED,
                    payload={"descriptorDigest": "sha256:" + ("a" * 64)},
                    projection=TaskProjection(
                        task_id="task:cli-handoff",
                        goal_id="goal:cli-handoff",
                        state=TaskState.READY,
                        active_node_id=None,
                        ready_frontier=("node:cli-handoff",),
                        revision=1,
                        updated_at_ms=1,
                    ),
                    expected_revision=0,
                )
            arguments = (
                "--state-root",
                str(state),
                "task",
                "handoff",
                "task:cli-handoff",
                "--expected-revision",
                "1",
            )
            first_code, first = self.invoke(*arguments)
            second_code, second = self.invoke(*arguments)
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(first, second)
            self.assertEqual(first["capsule"]["taskRevision"], 1)
            self.assertTrue(first["capsuleDigest"].startswith("sha256:"))
            with HostStorage(state) as storage:
                self.assertEqual(
                    storage.read_task_event("task:cli-handoff").projection.revision,
                    1,
                )
            stale_code, stale = self.invoke(
                "--state-root",
                str(state),
                "task",
                "handoff",
                "task:cli-handoff",
                "--expected-revision",
                "2",
            )
            self.assertEqual(stale_code, 1)
            self.assertEqual(stale["error"], "ValueError")
            self.assertEqual(
                stale["message"],
                "stale Operator Handoff revision: expected 2, current 1",
            )

    def test_missing_state_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            code, result = self.invoke(
                "--state-root", str(missing), "inspect"
            )
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
