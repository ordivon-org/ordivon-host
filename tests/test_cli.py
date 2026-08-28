from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from ordivon_host import (
    EventKind,
    HostStorage,
    TaskProjection,
    TaskState,
    WorkingCheckpoint,
)
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
            self.assertEqual(result["schemaVersion"], 7)
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

    def test_deployment_projects_release_commit_without_git_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "host"
            revision = "a" * 40
            release_id = revision + "-release-digest"
            release = root / "releases" / release_id
            release.mkdir(parents=True)
            (release / "COMMIT").write_text(revision + "\n", encoding="utf-8")
            (root / "current").symlink_to(Path("releases") / release_id)
            code, result = self.invoke(
                "deployment", "--release-root", str(root)
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["kind"], "ordivon.host-deployment")
            self.assertEqual(result["deployedRevision"], revision)
            self.assertEqual(result["releaseId"], release_id)
            self.assertEqual(result["currentRelease"], str(release))

            (release / "COMMIT").write_text("not-a-git-revision\n", encoding="utf-8")
            code, result = self.invoke(
                "deployment", "--release-root", str(root)
            )
            self.assertEqual(code, 1)
            self.assertEqual(result["error"], "ValueError")

    def test_history_doctor_command(self) -> None:
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

    def test_external_continuity_adopt_resume_and_checkpoint_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            initial_file = Path(directory) / "initial.json"
            update_file = Path(directory) / "update.json"
            task_id = "task:cli:external-continuity"
            initial = WorkingCheckpoint(
                task_id=task_id,
                objective="resume external work",
                frontier="inspect Runtime truth",
                established=("semantic boundary established",),
                unresolved=("physical state requires revalidation",),
                constraints=("Runtime truth overrides checkpoint",),
                next_actions=("inspect workspace",),
            )
            update = WorkingCheckpoint(
                task_id=task_id,
                objective="resume external work",
                frontier="continue after Runtime revalidation",
                established=("Runtime state revalidated",),
                unresolved=("next blocker unknown",),
                constraints=("Runtime truth overrides checkpoint",),
                next_actions=("continue work",),
            )
            initial_file.write_text(json.dumps(initial.to_dict()))
            update_file.write_text(json.dumps(update.to_dict()))

            code, adopted = self.invoke(
                "--state-root", str(state), "task", "adopt", task_id,
                "goal:cli:external-continuity", "--checkpoint-file", str(initial_file),
            )
            self.assertEqual(code, 0)
            self.assertEqual(adopted["projection"]["revision"], 2)
            self.assertEqual(
                adopted["checkpoint"]["checkpoint"]["truthRole"],
                "semantic-working-claim",
            )

            code, resumed = self.invoke(
                "--state-root", str(state), "task", "resume", task_id,
                "--expected-revision", "2",
            )
            self.assertEqual(code, 0)
            self.assertEqual(resumed, adopted)

            code, receipt = self.invoke(
                "--state-root", str(state), "task", "checkpoint", task_id,
                "--expected-revision", "2", "--checkpoint-file", str(update_file),
            )
            self.assertEqual(code, 0)
            self.assertEqual(receipt["admission"], "created")
            self.assertEqual(receipt["projection"]["revision"], 3)

            code, retry = self.invoke(
                "--state-root", str(state), "task", "checkpoint", task_id,
                "--expected-revision", "2", "--checkpoint-file", str(update_file),
            )
            self.assertEqual(code, 0)
            self.assertEqual(retry["admission"], "existing")
            self.assertEqual(retry["projection"]["revision"], 3)

    def test_missing_state_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            code, result = self.invoke(
                "--state-root", str(missing), "inspect"
            )
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
