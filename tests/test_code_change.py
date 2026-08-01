from __future__ import annotations

import base64
from copy import deepcopy
import itertools
import json
import tempfile
from typing import Any
import unittest

from anc_canonical import canonical_digest

from ordivon_host import EventKind, HostStorage, TaskState
from ordivon_host.authority import CapabilityDenied, TrustedLocalAuthorizer
from ordivon_host.domain import RepositoryRef, StaticRepositoryResolver
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


def source_state_mismatch(operation: str) -> RuntimeToolRejected:
    return RuntimeToolRejected(
        operation,
        RuntimeErrorDetail(
            code="REVISION_MISMATCH",
            message="Workspace source state differs",
            field="expectedSourceStateDigest",
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
        self.closed_source_digests: dict[str, str] = {}

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
            result = deepcopy(self.workspaces[workspace_id])
            result["sourceStateDigest"] = self._source_state_digest(workspace_id)
            return result
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
            return {
                "diff": diff,
                "truncated": False,
                "changedPaths": paths,
                "addedPaths": [],
                "modifiedPaths": paths,
                "deletedPaths": [],
                "renamedPaths": [],
                "untrackedPaths": [],
            }
        if name == "workspace.close":
            workspace_id = arguments["workspaceId"]
            if workspace_id not in self.workspaces:
                digest = self.closed_source_digests.get(workspace_id)
                if digest is None:
                    raise missing_workspace(name)
                expected = arguments.get("expectedSourceStateDigest")
                if expected is not None and expected != digest:
                    raise source_state_mismatch(name)
                return {
                    "workspaceId": workspace_id,
                    "removed": False,
                    "sourceStateDigest": digest,
                }
            digest = self._source_state_digest(workspace_id)
            expected = arguments.get("expectedSourceStateDigest")
            if expected is not None and expected != digest:
                raise source_state_mismatch(name)
            self.closed_source_digests[workspace_id] = digest
            del self.workspaces[workspace_id]
            return {
                "workspaceId": workspace_id,
                "removed": True,
                "sourceStateDigest": digest,
            }
        raise AssertionError(name)

    def _source_state_digest(self, workspace_id: str) -> str:
        return canonical_digest(
            {
                "workspaceId": workspace_id,
                "sourceRevision": self.workspaces[workspace_id]["sourceRevision"],
                "files": [
                    {"path": path, "content": content}
                    for (observed_workspace, path), content in sorted(self.files.items())
                    if observed_workspace == workspace_id
                ],
            }
        )

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
        repository=RepositoryRef("repository:ordivon-host", "a" * 40),
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


def code_change_host(storage: HostStorage, runtime: FakeCodeChangeRuntime, clock) -> CodeChangeHost:
    return CodeChangeHost(
        storage,
        runtime,
        clock_ms=clock,
        repository_resolver=StaticRepositoryResolver(
            {"repository:ordivon-host": "/root/projects/ordivon-host"}
        ),
    )


class CodeChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = itertools.count(1).__next__

    def test_plan_round_trip_and_result_digests(self) -> None:
        value = plan()
        self.assertEqual(CodeChangePlan.from_dict(value.to_dict()), value)
        self.assertNotEqual(value.files[0].expected_digest, value.files[0].result_digest)

    def test_plan_v2_serializes_logical_repository_only(self) -> None:
        value = plan().to_dict()
        self.assertEqual(value["schemaVersion"], 2)
        self.assertEqual(
            value["repository"],
            {"repositoryId": "repository:ordivon-host", "revision": "a" * 40},
        )
        self.assertNotIn("sourceRepo", value)

    def test_legacy_v1_plan_upcasts_without_republishing_physical_path(self) -> None:
        current = plan().to_dict()
        legacy = {
            "schemaVersion": 1,
            "kind": current["kind"],
            "taskId": current["taskId"],
            "goalId": current["goalId"],
            "workspaceId": current["workspaceId"],
            "sourceRepo": "/root/projects/ordivon-host",
            "sourceRevision": "a" * 40,
            "files": current["files"],
            "checks": current["checks"],
            "patchExecutable": current["patchExecutable"],
            "principalId": current["principalId"],
        }
        decoded = CodeChangePlan.from_dict(legacy)
        self.assertTrue(decoded.repository.repository_id.startswith("repository:legacy-"))
        self.assertEqual(decoded.repository.legacy_path, legacy["sourceRepo"])
        self.assertNotIn("sourceRepo", decoded.to_dict())

    def test_trusted_local_authority_denies_another_principal(self) -> None:
        from anc_effect_ir import CapabilityRequirement

        with self.assertRaises(CapabilityDenied):
            TrustedLocalAuthorizer().authorize(
                CapabilityRequirement(
                    "principal:other",
                    "anc.source.change.v1",
                    "world_object:repository:ordivon-host",
                )
            )

    def test_execution_check_rejects_durable_secret_environment(self) -> None:
        with self.assertRaisesRegex(ValueError, "SecretRef"):
            ExecutionCheck(
                "unsafe",
                "/usr/bin/true",
                (),
                env=(("API_KEY", "should-not-enter-cas"),),
            )

    def test_realistic_code_change_completes_across_fresh_host_instances(self) -> None:
        runtime = FakeCodeChangeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                created = code_change_host(storage, runtime, self.clock).create(plan())
                self.assertEqual(created.revision, 1)
            with HostStorage(directory) as storage:
                opened = code_change_host(storage, runtime, self.clock).open_workspace(
                    plan().task_id
                )
                self.assertEqual(opened.revision, 2)
            with HostStorage(directory) as storage:
                prepared = code_change_host(storage, runtime, self.clock).prepare(
                    plan().task_id
                )
                self.assertEqual(prepared.task_revision, 3)
                kinds = {item.kind for item in storage.journal.object_refs()}
                self.assertTrue(
                    {"effect", "effect-binding", "capability-decision"}.issubset(kinds)
                )
                self.assertEqual(
                    prepared.dispatch.effect_id,
                    "effect:code-change-test:source-change:r1",
                )
            with HostStorage(directory) as storage:
                observed = code_change_host(storage, runtime, self.clock).deliver(
                    prepared
                )
                self.assertEqual(observed.state, TaskState.VERIFYING)
            with HostStorage(directory) as storage:
                verified = code_change_host(storage, runtime, self.clock).verify(
                    plan().task_id
                )
                self.assertEqual(verified.state, TaskState.READY)
            with HostStorage(directory) as storage:
                closed = code_change_host(storage, runtime, self.clock).close(
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
                host = code_change_host(storage, runtime, self.clock)
                host.create(plan())
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).open_workspace(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                prepared = code_change_host(storage, runtime, self.clock).prepare(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                unknown = code_change_host(storage, runtime, self.clock).deliver(
                    prepared
                )
                self.assertEqual(unknown.state, TaskState.WAITING)
                self.assertTrue(runtime.response_dropped)
            with HostStorage(directory) as storage:
                reconciled = code_change_host(storage, runtime, self.clock).reconcile(plan().task_id)
                self.assertEqual(reconciled.state, TaskState.VERIFYING)
                self.assertTrue(reconciled.reconciled)
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).verify(plan().task_id)
            with HostStorage(directory) as storage:
                closed = code_change_host(storage, runtime, self.clock).close(
                    plan().task_id
                )
                self.assertEqual(closed.revision, 8)
            self.assertEqual(runtime.physical_deliveries, 1)

    def test_failed_check_persists_blocked_then_closes_failed(self) -> None:
        runtime = FakeCodeChangeRuntime()
        runtime.fail_check = True
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).create(plan())
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).open_workspace(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                prepared = code_change_host(storage, runtime, self.clock).prepare(
                    plan().task_id
                )
            with HostStorage(directory) as storage:
                blocked = code_change_host(storage, runtime, self.clock).deliver(
                    prepared
                )
                self.assertEqual(blocked.state, TaskState.BLOCKED)
            with HostStorage(directory) as storage:
                closed = code_change_host(storage, runtime, self.clock).close(
                    plan().task_id
                )
                self.assertFalse(closed.completed)
                self.assertEqual(closed.state, TaskState.FAILED)

    def test_verification_close_response_loss_replays_tombstone_without_reexecution(self) -> None:
        class CloseResponseLossRuntime(FakeCodeChangeRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.drop_close_response = True

            def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = super().call_tool(name, arguments)
                if name == "workspace.close" and self.drop_close_response:
                    self.drop_close_response = False
                    raise RuntimeTransportError(
                        "response lost after fenced Workspace closure"
                    )
                return result

        runtime = CloseResponseLossRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = code_change_host(storage, runtime, self.clock)
                host.create(plan())
                host.open_workspace(plan().task_id)
                prepared = host.prepare(plan().task_id)
                host.deliver(prepared)
                with self.assertRaisesRegex(
                    RuntimeTransportError,
                    "response lost after fenced Workspace closure",
                ):
                    host.verify(plan().task_id)
                snapshot = storage.read_task_event(plan().task_id)
                self.assertEqual(snapshot.event_kind, EventKind.VERIFICATION_RECORDED)
                self.assertEqual(snapshot.projection.state, TaskState.VERIFYING)
                self.assertNotIn(plan().workspace_id, runtime.workspaces)
                self.assertIn(plan().workspace_id, runtime.closed_source_digests)
                verified = host.verify(plan().task_id)
                self.assertEqual(verified.state, TaskState.READY)
                completed = host.close(plan().task_id)
                self.assertEqual(completed.state, TaskState.COMPLETED)
                self.assertEqual(runtime.physical_deliveries, 1)

    def test_verification_close_fence_rejects_post_evidence_workspace_race(self) -> None:
        class RacingRuntime(FakeCodeChangeRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.race_once = True

            def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                if name == "workspace.close" and self.race_once:
                    self.race_once = False
                    self.files[(arguments["workspaceId"], "src/example.py")] = "VALUE = 999\n"
                return super().call_tool(name, arguments)

        runtime = RacingRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = code_change_host(storage, runtime, self.clock)
                host.create(plan())
                host.open_workspace(plan().task_id)
                prepared = host.prepare(plan().task_id)
                host.deliver(prepared)
                with self.assertRaisesRegex(RuntimeToolRejected, "source state differs"):
                    host.verify(plan().task_id)
                snapshot = storage.read_task_event(plan().task_id)
                self.assertEqual(snapshot.event_kind.value, "verification.recorded")
                self.assertEqual(snapshot.projection.state, TaskState.VERIFYING)
                self.assertIn(plan().workspace_id, runtime.workspaces)
                runtime.files[(plan().workspace_id, "src/example.py")] = "VALUE = 2\n"
                verified = host.verify(plan().task_id)
                self.assertEqual(verified.state, TaskState.READY)
                self.assertNotIn(plan().workspace_id, runtime.workspaces)
                completed = host.close(plan().task_id)
                self.assertEqual(completed.state, TaskState.COMPLETED)

    def test_catalog_drift_blocks_preparation(self) -> None:
        runtime = FakeCodeChangeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).create(plan())
            with HostStorage(directory) as storage:
                code_change_host(storage, runtime, self.clock).open_workspace(
                    plan().task_id
                )
            runtime.catalog_generation = 2
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(RuntimeError, "catalog changed"):
                    code_change_host(storage, runtime, self.clock).prepare(
                        plan().task_id
                    )
