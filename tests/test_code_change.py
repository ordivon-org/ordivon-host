from __future__ import annotations

import base64
from copy import deepcopy
import itertools
import json
import tempfile
from typing import Any
import unittest

from ordivon_host import HostStorage, TaskState
from ordivon_host.engine.code_change import (
    CodeChangeHost,
    CodeChangePlan,
    CodeFileReplacement,
    ExecutionCheck,
)
from ordivon_host.engine._serde import digest_text
from ordivon_host.runtime import (
    RuntimeErrorDetail,
    RuntimeToolRejected,
    RuntimeTransportError,
)


def missing_workspace(operation: str) -> RuntimeToolRejected:
    return RuntimeToolRejected(
        operation,
        RuntimeErrorDetail(
            code="INVALID_REQUEST",
            message="missing workspace",
            field="workspaceId",
            retryable=False,
            retry_class="never",
            commit_state="not_committed",
            origin="runtime_core",
            trace_id="test",
            raw={},
        ),
    )


def descriptor(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {"schemaVersion": {"const": 1}}
    if name == "task.list":
        properties["clientRequestId"] = {"type": "string"}
    return {
        "name": name,
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        },
        "outputSchema": None,
        "execution": {
            "taskSupport": "optional" if name == "workspace.execPlan" else "forbidden"
        },
    }


class FakeCodeChangeRuntime:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.base_files = {
            "src/example.py": "VALUE = 1\n",
            "tests/test_example.py": "def test_value():\n    assert 1 == 1\n",
        }
        self.calls: list[str] = []
        self.physical_deliveries = 0
        self.drop_first_response = False
        self.response_dropped = False
        self.fail_check = False
        self.catalog_generation = 1

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {"serverInfo": {"name": "fake-runtime"}}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        names = (
            "task.list",
            "task.observe",
            "workspace.close",
            "workspace.diff",
            "workspace.execPlan",
            "workspace.get",
            "workspace.open",
            "workspace.read",
        )
        tools = [descriptor(name) for name in names]
        if self.catalog_generation > 1:
            tools[0]["inputSchema"]["properties"]["newField"] = {"type": "string"}
        return tuple(deepcopy(tools))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if name == "workspace.get":
            workspace_id = arguments["workspaceId"]
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            return deepcopy(self.workspaces[workspace_id])
        if name == "workspace.open":
            workspace_id = arguments["workspaceId"]
            record = {
                "workspaceId": workspace_id,
                "sourceRevision": arguments["sourceRevision"],
                "dirty": False,
                "headMode": "detached",
            }
            self.workspaces[workspace_id] = record
            for path, content in self.base_files.items():
                self.files[(workspace_id, path)] = content
            return deepcopy(record)
        if name == "workspace.execPlan":
            return self._exec_plan(arguments)
        if name == "task.list":
            requested = arguments.get("clientRequestId")
            jobs = [
                deepcopy(job)
                for job in self.jobs.values()
                if requested is None or job["clientRequestId"] == requested
            ]
            return {"jobs": jobs, "nextCursor": None}
        if name == "task.observe":
            return deepcopy(self.jobs[arguments["jobId"]])
        if name == "workspace.read":
            content = self.files[(arguments["workspaceId"], arguments["relativePath"])]
            return {"content": content, "digest": digest_text(content)}
        if name == "workspace.diff":
            workspace_id = arguments["workspaceId"]
            paths = sorted(
                path
                for (observed_workspace, path), content in self.files.items()
                if observed_workspace == workspace_id
                and self.base_files.get(path) != content
            )
            diff = "".join(
                f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                for path in paths
            )
            return {"diff": diff, "untrackedPaths": []}
        if name == "workspace.close":
            workspace_id = arguments["workspaceId"]
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            del self.workspaces[workspace_id]
            return {"workspaceId": workspace_id, "closed": True}
        raise AssertionError(name)

    def _exec_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        client_request_id = arguments["clientRequestId"]
        existing = next(
            (
                job
                for job in self.jobs.values()
                if job["clientRequestId"] == client_request_id
            ),
            None,
        )
        if existing is None:
            execution = arguments["execution"]
            workspace_id = execution["workspaceId"]
            steps = execution["steps"]
            encoded = steps[0]["args"][-1]
            spec = json.loads(base64.b64decode(encoded, validate=True))
            for item in spec["files"]:
                key = (workspace_id, item["relativePath"])
                current = self.files[key]
                if digest_text(current) != item["expectedDigest"]:
                    raise AssertionError("code change precondition differs")
            for item in spec["files"]:
                self.files[(workspace_id, item["relativePath"])] = base64.b64decode(
                    item["contentBase64"], validate=True
                ).decode()
            self.workspaces[workspace_id]["dirty"] = True
            self.physical_deliveries += 1
            job_id = f"job-{self.physical_deliveries}"
            failed = self.fail_check
            existing = {
                "jobId": job_id,
                "attemptId": f"attempt-{self.physical_deliveries}",
                "clientRequestId": client_request_id,
                "workspaceId": workspace_id,
                "status": "failed" if failed else "succeeded",
                "completedSteps": 1 if failed else len(steps),
                "totalSteps": len(steps),
                "failedStepId": steps[1]["id"] if failed else None,
                "failedStepIndex": 1 if failed else None,
                "createdAtMs": 100 + self.physical_deliveries,
                "artifacts": [],
            }
            self.jobs[job_id] = existing
        if self.drop_first_response and not self.response_dropped:
            self.response_dropped = True
            raise RuntimeTransportError("response lost after code-change Job admission")
        return deepcopy(existing)


def plan() -> CodeChangePlan:
    return CodeChangePlan(
        task_id="task:code-change-test",
        goal_id="goal:code-change-test",
        workspace_id="workspace-code-change-test",
        source_repo="/root/projects/ordivon-host",
        source_revision="a" * 40,
        files=(
            CodeFileReplacement(
                "src/example.py",
                digest_text("VALUE = 1\n"),
                "VALUE = 2\n",
            ),
            CodeFileReplacement(
                "tests/test_example.py",
                digest_text("def test_value():\n    assert 1 == 1\n"),
                "def test_value():\n    assert 2 == 2\n",
            ),
        ),
        checks=(
            ExecutionCheck(
                "ruff",
                "/root/.local/bin/python3.12",
                ("-m", "ruff", "check", "src/example.py", "tests/test_example.py"),
            ),
            ExecutionCheck(
                "tests",
                "/root/.local/bin/python3.12",
                ("-m", "unittest", "tests.test_example"),
                env=(("PYTHONPATH", "src"),),
            ),
        ),
    )


class CodeChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = itertools.count(1).__next__

    def test_plan_round_trip_and_result_digests(self) -> None:
        value = plan()
        self.assertEqual(CodeChangePlan.from_dict(value.to_dict()), value)
        self.assertNotEqual(value.files[0].expected_digest, value.files[0].result_digest)

    def test_realistic_code_change_completes_across_fresh_host_instances(self) -> None:
        runtime = FakeCodeChangeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                created = CodeChangeHost(storage, runtime, clock_ms=self.clock).create(plan())
                self.assertEqual(created.revision, 1)
            with HostStorage(directory) as storage:
                opened = CodeChangeHost(storage, runtime, clock_ms=self.clock).open_workspace(
                    plan().task_id
                )
                self.assertEqual(opened.revision, 2)
            with HostStorage(directory) as storage:
                prepared = CodeChangeHost(storage, runtime, clock_ms=self.clock).prepare(
                    plan().task_id
                )
                self.assertEqual(prepared.task_revision, 3)
            with HostStorage(directory) as storage:
                observed = CodeChangeHost(storage, runtime, clock_ms=self.clock).deliver(
                    prepared
                )
                self.assertEqual(observed.state, TaskState.VERIFYING)
            with HostStorage(directory) as storage:
                verified = CodeChangeHost(storage, runtime, clock_ms=self.clock).verify(
                    plan().task_id
                )
                self.assertEqual(verified.state, TaskState.READY)
            with HostStorage(directory) as storage:
                closed = CodeChangeHost(storage, runtime, clock_ms=self.clock).close(
                    plan().task_id
                )
                self.assertTrue(closed.completed)
                projection = storage.journal.get_task(plan().task_id)
                self.assertIsNotNone(projection)
                self.assertEqual(projection.state, TaskState.COMPLETED)
                snapshot = storage.read_task_event(plan().task_id)
                self.assertIsInstance(snapshot.data, dict)
                outcome = storage.objects.get(
                    snapshot.data["outcomeDigest"], expected_kind="task-outcome"
                )
                self.assertEqual(outcome["status"], "completed")
            self.assertEqual(runtime.physical_deliveries, 1)
            self.assertNotIn(plan().workspace_id, runtime.workspaces)

    def test_response_loss_recovers_original_job_without_redispatch(self) -> None:
        runtime = FakeCodeChangeRuntime()
        runtime.drop_first_response = True
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = CodeChangeHost(storage, runtime, clock_ms=self.clock)
                host.create(plan())
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).open_workspace(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                prepared = CodeChangeHost(storage, runtime, clock_ms=self.clock).prepare(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                unknown = CodeChangeHost(storage, runtime, clock_ms=self.clock).deliver(
                    prepared
                )
                self.assertEqual(unknown.state, TaskState.WAITING)
                self.assertTrue(runtime.response_dropped)
            with HostStorage(directory) as storage:
                reconciled = CodeChangeHost(
                    storage, runtime, clock_ms=self.clock
                ).reconcile(plan().task_id)
                self.assertEqual(reconciled.state, TaskState.VERIFYING)
                self.assertTrue(reconciled.reconciled)
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).verify(plan().task_id)
            with HostStorage(directory) as storage:
                closed = CodeChangeHost(storage, runtime, clock_ms=self.clock).close(
                    plan().task_id
                )
                self.assertEqual(closed.revision, 7)
            self.assertEqual(runtime.physical_deliveries, 1)

    def test_failed_check_persists_blocked_then_closes_failed(self) -> None:
        runtime = FakeCodeChangeRuntime()
        runtime.fail_check = True
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).create(plan())
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).open_workspace(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                prepared = CodeChangeHost(storage, runtime, clock_ms=self.clock).prepare(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                blocked = CodeChangeHost(storage, runtime, clock_ms=self.clock).deliver(
                    prepared
                )
                self.assertEqual(blocked.state, TaskState.BLOCKED)
            with HostStorage(directory) as storage:
                closed = CodeChangeHost(storage, runtime, clock_ms=self.clock).close(
                    plan().task_id
                )
                self.assertFalse(closed.completed)
                self.assertEqual(closed.state, TaskState.FAILED)

    def test_catalog_drift_blocks_preparation(self) -> None:
        runtime = FakeCodeChangeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).create(plan())
            with HostStorage(directory) as storage:
                CodeChangeHost(storage, runtime, clock_ms=self.clock).open_workspace(
                    plan().task_id
                )
            runtime.catalog_generation = 2
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(RuntimeError, "catalog changed"):
                    CodeChangeHost(storage, runtime, clock_ms=self.clock).prepare(
                        plan().task_id
                    )
