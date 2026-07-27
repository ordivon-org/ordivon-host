from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

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
            self.assertEqual(result["schemaVersion"], 2)
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

    def test_missing_state_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            code, result = self.invoke(
                "--state-root", str(missing), "inspect"
            )
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
