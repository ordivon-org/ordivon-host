from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import tempfile
from typing import Any
import unittest

from ordivon_host import HostStorage, TaskState
from ordivon_host.engine import (
    DeterministicReadHost,
    ReadTaskPlan,
    ReadVerificationError,
)
from ordivon_host.runtime import (
    RuntimeErrorDetail,
    RuntimeProtocolError,
    RuntimeToolRejected,
)


def tool_descriptor(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "schemaVersion": {"type": "integer", "const": 1},
    }
    required = ["schemaVersion"]
    if name == "workspace.read":
        properties.update(
            {
                "workspaceId": {"type": "string"},
                "relativePath": {"type": "string"},
                "mode": {"type": "string", "enum": ["FULL", "SLICE"]},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "maxBytes": {"type": "integer", "minimum": 1, "maximum": 4_194_304},
            }
        )
        required.extend(["workspaceId", "relativePath", "mode", "maxBytes"])
    return {
        "name": name,
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


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
            trace_id="test-trace",
            raw={},
        ),
    )


class FakeRuntime:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.content = "hello from Runtime\n"
        self.bad_digest = False
        self.catalog_generation = 1

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {"serverInfo": {"name": "ordivon-runtime-mcp"}}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        tools = [
            tool_descriptor("workspace.close"),
            tool_descriptor("workspace.get"),
            tool_descriptor("workspace.open"),
            tool_descriptor("workspace.read"),
        ]
        if self.catalog_generation > 1:
            tools[-1]["inputSchema"]["properties"]["encoding"] = {
                "type": "string"
            }
        return tuple(deepcopy(tools))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        workspace_id = arguments.get("workspaceId")
        if name == "workspace.get":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            return deepcopy(self.workspaces[workspace_id])
        if name == "workspace.open":
            if workspace_id in self.workspaces:
                raise RuntimeError("workspace.open repeated unexpectedly")
            record = {
                "workspaceId": workspace_id,
                "sourceRevision": arguments["sourceRevision"],
                "createdAtMs": 1,
                "dirty": False,
                "headMode": "detached",
            }
            self.workspaces[workspace_id] = record
            return {
                "workspaceId": workspace_id,
                "sourceRevision": arguments["sourceRevision"],
            }
        if name == "workspace.read":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            digest = f"sha256:{hashlib.sha256(self.content.encode()).hexdigest()}"
            if self.bad_digest:
                digest = "sha256:" + ("0" * 64)
            return {"content": self.content, "digest": digest}
        if name == "workspace.close":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            del self.workspaces[workspace_id]
            return {"workspaceId": workspace_id, "closed": True}
        raise AssertionError(f"unexpected Tool: {name}")


def plan() -> ReadTaskPlan:
    return ReadTaskPlan(
        task_id="task:read-runtime-readme",
        goal_id="goal:inspect-runtime-readme",
        workspace_id="host-read-runtime-readme",
        source_repo="/root/projects/ordivon-computing",
        source_revision="a" * 40,
        relative_path="README.md",
    )


def host(storage: HostStorage, runtime: FakeRuntime) -> DeterministicReadHost:
    clock = itertools.count(1_000).__next__
    return DeterministicReadHost(storage, runtime, clock_ms=clock)


class DeterministicReadHostTests(unittest.TestCase):
    def test_task_advances_across_fresh_host_process_boundaries(self) -> None:
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                created = host(storage, runtime).create(plan())
                self.assertEqual(created.revision, 1)

            for expected_revision in (2, 3, 4):
                with HostStorage(directory) as storage:
                    result = host(storage, runtime).step(plan().task_id)
                    self.assertEqual(result.revision, expected_revision)

            with HostStorage(directory) as storage:
                completed = storage.journal.get_task(plan().task_id)
                self.assertIsNotNone(completed)
                assert completed is not None
                self.assertEqual(completed.state, TaskState.COMPLETED)
                self.assertEqual(completed.revision, 4)
                self.assertEqual(storage.rebuild_task(plan().task_id), completed)
                kinds = {value.kind for value in storage.journal.object_refs()}
                self.assertTrue(
                    {
                        "host-read-task-plan",
                        "runtime-catalog",
                        "effect",
                        "effect-binding",
                        "read-observation",
                        "verification-receipt",
                        "task-outcome",
                    }.issubset(kinds)
                )
            self.assertNotIn(plan().workspace_id, runtime.workspaces)
            self.assertEqual(runtime.calls.count("workspace.open"), 1)
            self.assertEqual(runtime.calls.count("workspace.read"), 1)
            self.assertEqual(runtime.calls.count("workspace.close"), 1)

    def test_opened_workspace_is_reconciled_without_second_open(self) -> None:
        runtime = FakeRuntime()
        task_plan = plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host(storage, runtime).create(task_plan)
            runtime.call_tool(
                "workspace.open",
                {
                    "schemaVersion": 1,
                    "sourceRepo": task_plan.source_repo,
                    "sourceRevision": task_plan.source_revision,
                    "workspaceId": task_plan.workspace_id,
                },
            )
            with HostStorage(directory) as storage:
                result = host(storage, runtime).step(task_plan.task_id)
                self.assertEqual(result.revision, 2)
                self.assertEqual(result.frontier, "node:read-runtime-readme:read")
            self.assertEqual(runtime.calls.count("workspace.open"), 1)
            self.assertGreaterEqual(runtime.calls.count("workspace.get"), 1)

    def test_catalog_drift_stops_before_runtime_read(self) -> None:
        runtime = FakeRuntime()
        task_plan = plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.step(task_plan.task_id)
            runtime.catalog_generation = 2
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(RuntimeProtocolError, "catalog changed"):
                    host(storage, runtime).step(task_plan.task_id)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 2)
                self.assertEqual(storage.journal.event_count(task_plan.task_id), 2)
            self.assertEqual(runtime.calls.count("workspace.read"), 0)

    def test_digest_mismatch_does_not_advance_task(self) -> None:
        runtime = FakeRuntime()
        task_plan = plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.step(task_plan.task_id)
            runtime.bad_digest = True
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(ReadVerificationError, "digest differs"):
                    host(storage, runtime).step(task_plan.task_id)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 2)
                self.assertEqual(storage.journal.event_count(task_plan.task_id), 2)

    def test_already_absent_workspace_completes_close_reconciliation(self) -> None:
        runtime = FakeRuntime()
        task_plan = plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.step(task_plan.task_id)
                runner.step(task_plan.task_id)
            runtime.workspaces.clear()
            with HostStorage(directory) as storage:
                result = host(storage, runtime).step(task_plan.task_id)
                self.assertTrue(result.completed)
                self.assertEqual(result.revision, 4)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.state, TaskState.COMPLETED)
            self.assertEqual(runtime.calls.count("workspace.close"), 0)


if __name__ == "__main__":
    unittest.main()
