from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import itertools
import tempfile
from typing import Any
import unittest

from ordivon_host import HostStorage, TaskState
from ordivon_host.engine import (
    GuardedMutationHost,
    GuardedMutationPlan,
    MutationVerificationError,
)
from ordivon_host.runtime import (
    RuntimeErrorDetail,
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)


def descriptor(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "schemaVersion": {"type": "integer", "const": 1},
    }
    required = ["schemaVersion"]
    if name == "task.list":
        properties.update(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "object"},
                "clientRequestId": {"type": "string"},
            }
        )
    if name == "workspace.exec":
        properties.update(
            {
                "clientRequestId": {"type": "string"},
                "execution": {"type": "object"},
                "waitMs": {"type": "integer", "minimum": 0},
                "stdoutTailBytes": {"type": "integer", "minimum": 0},
                "stderrTailBytes": {"type": "integer", "minimum": 0},
            }
        )
        required.extend(["clientRequestId", "execution"])
    return {
        "name": name,
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "execution": {
            "taskSupport": "optional" if name == "workspace.exec" else "forbidden"
        },
    }


def runtime_semantics(status: str, *, recovery_required: bool = False) -> dict[str, Any]:
    if status in {"queued", "working"}:
        return {
            "executionTerminal": False,
            "executionDisposition": None,
            "deliveryDisposition": "in_progress",
            "recoveryRequired": False,
            "semanticCompletionEvaluated": False,
            "resultAvailable": False,
        }
    if status == "succeeded":
        return {
            "executionTerminal": True,
            "executionDisposition": "succeeded",
            "deliveryDisposition": (
                "reconciliation_required" if recovery_required else "committed"
            ),
            "recoveryRequired": recovery_required,
            "semanticCompletionEvaluated": False,
            "resultAvailable": True,
        }
    if status in {"failed", "timed_out", "cancelled"}:
        return {
            "executionTerminal": True,
            "executionDisposition": status,
            "deliveryDisposition": "committed",
            "recoveryRequired": False,
            "semanticCompletionEvaluated": False,
            "resultAvailable": True,
        }
    if status == "lost":
        return {
            "executionTerminal": True,
            "executionDisposition": "lost",
            "deliveryDisposition": "unknown",
            "recoveryRequired": False,
            "semanticCompletionEvaluated": False,
            "resultAvailable": True,
        }
    if status == "orphaned":
        return {
            "executionTerminal": True,
            "executionDisposition": "orphaned",
            "deliveryDisposition": "reconciliation_required",
            "recoveryRequired": True,
            "semanticCompletionEvaluated": False,
            "resultAvailable": True,
        }
    raise AssertionError(f"unsupported fake Runtime status: {status}")


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


class FakeMutationRuntime:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.physical_deliveries = 0
        self.drop_first_success = False
        self.response_dropped = False
        self.catalog_generation = 1
        self.reject_not_committed = False
        self.task_list_page_size: int | None = None
        self.task_list_filter_supported = True
        self.task_list_ignore_filter = False
        self.task_list_arguments: list[dict[str, Any]] = []
        self.terminal_status = "succeeded"
        self.recovery_required = False

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {"serverInfo": {"name": "ordivon-runtime-mcp"}}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        tools = [
            descriptor(name)
            for name in (
                "task.list",
                "task.observe",
                "workspace.close",
                "workspace.exec",
                "workspace.get",
                "workspace.open",
                "workspace.read",
            )
        ]
        if self.catalog_generation > 1:
            exec_tool = next(tool for tool in tools if tool["name"] == "workspace.exec")
            exec_tool["inputSchema"]["properties"]["newField"] = {"type": "string"}
        if not self.task_list_filter_supported:
            list_tool = next(tool for tool in tools if tool["name"] == "task.list")
            del list_tool["inputSchema"]["properties"]["clientRequestId"]
        return tuple(deepcopy(tools))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        workspace_id = arguments.get("workspaceId")
        if name == "workspace.get":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            return deepcopy(self.workspaces[workspace_id])
        if name == "workspace.open":
            record = {
                "workspaceId": workspace_id,
                "sourceRevision": arguments["sourceRevision"],
                "createdAtMs": 1,
                "dirty": False,
                "headMode": "detached",
            }
            self.workspaces[workspace_id] = record
            return deepcopy(record)
        if name == "workspace.exec":
            if self.reject_not_committed:
                raise RuntimeToolRejected(
                    name,
                    RuntimeErrorDetail(
                        code="INVALID_REQUEST",
                        message="execution rejected",
                        field="execution",
                        retryable=False,
                        retry_class="never",
                        commit_state="not_committed",
                        origin="runtime_core",
                        trace_id="test-rejection",
                        raw={},
                    ),
                )
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
                relative_path = execution["args"][-2]
                content = base64.b64decode(execution["args"][-1]).decode()
                key = (workspace_id, relative_path)
                if key in self.files:
                    raise AssertionError("guarded create was physically delivered twice")
                self.files[key] = content
                self.workspaces[workspace_id]["dirty"] = True
                self.physical_deliveries += 1
                job_id = f"job-{self.physical_deliveries}"
                existing = {
                    "jobId": job_id,
                    "attemptId": f"attempt-{self.physical_deliveries}",
                    "clientRequestId": client_request_id,
                    "workspaceId": workspace_id,
                    "status": self.terminal_status,
                    **runtime_semantics(
                        self.terminal_status, recovery_required=self.recovery_required
                    ),
                    "createdAtMs": 100 + self.physical_deliveries,
                    "artifacts": [],
                }
                self.jobs[job_id] = existing
            if self.drop_first_success and not self.response_dropped:
                self.response_dropped = True
                raise RuntimeTransportError(
                    "injected response loss after successful workspace.exec"
                )
            return deepcopy(existing)
        if name == "task.list":
            self.task_list_arguments.append(deepcopy(arguments))
            jobs = sorted(
                (deepcopy(job) for job in self.jobs.values()),
                key=lambda job: (job["createdAtMs"], job["jobId"]),
                reverse=True,
            )
            requested = arguments.get("clientRequestId")
            if isinstance(requested, str) and not self.task_list_ignore_filter:
                jobs = [
                    job for job in jobs if job.get("clientRequestId") == requested
                ]
            cursor = arguments.get("cursor")
            start = 0
            if isinstance(cursor, dict):
                identity = (cursor.get("createdAtMs"), cursor.get("jobId"))
                for index, job in enumerate(jobs):
                    if (job["createdAtMs"], job["jobId"]) == identity:
                        start = index + 1
                        break
            page_size = self.task_list_page_size or arguments.get("limit", 100)
            page = jobs[start : start + page_size]
            next_cursor = None
            if start + len(page) < len(jobs) and page:
                last = page[-1]
                next_cursor = {
                    "createdAtMs": last["createdAtMs"],
                    "jobId": last["jobId"],
                }
            return {"jobs": page, "nextCursor": next_cursor}
        if name == "task.observe":
            return deepcopy(self.jobs[arguments["jobId"]])
        if name == "workspace.read":
            key = (workspace_id, arguments["relativePath"])
            content = self.files[key]
            return {
                "content": content,
                "digest": f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
            }
        if name == "workspace.close":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            del self.workspaces[workspace_id]
            return {"workspaceId": workspace_id, "closed": True}
        raise AssertionError(f"unexpected Tool: {name}")


def plan(token: str = "guarded-mutation") -> GuardedMutationPlan:
    return GuardedMutationPlan(
        task_id=f"task:{token}",
        goal_id=f"goal:{token}",
        workspace_id=f"host-{token}",
        source_repo="/root/projects/ordivon-computing",
        source_revision="a" * 40,
        relative_path="host-h4-proof.txt",
        content="one durable physical delivery\n",
    )


def host(storage: HostStorage, runtime: FakeMutationRuntime) -> GuardedMutationHost:
    return GuardedMutationHost(
        storage,
        runtime,
        clock_ms=itertools.count(10_000).__next__,
    )


class GuardedMutationHostTests(unittest.TestCase):
    def test_terminal_runtime_failure_is_persisted_and_workspace_is_closed(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.terminal_status = "failed"
        task_plan = plan("terminal-failure")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                blocked = runner.deliver(prepared)
                self.assertEqual(blocked.state, TaskState.BLOCKED)
                self.assertTrue(blocked.frontier.endswith(":close"))
                closed = runner.close(task_plan.task_id)
                self.assertEqual(closed.state, TaskState.FAILED)
                self.assertNotIn(task_plan.workspace_id, runtime.workspaces)
                snapshot = storage.read_task_event(task_plan.task_id)
                self.assertEqual(snapshot.projection.state, TaskState.FAILED)
                self.assertEqual(snapshot.data["jobStatus"], "failed")

    def test_succeeded_status_waits_while_runtime_recovery_is_required(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.recovery_required = True
        task_plan = plan("recovery-required")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                waiting = runner.deliver(prepared)
                self.assertEqual(waiting.state, TaskState.WAITING)
                self.assertTrue(waiting.frontier.endswith(":reconcile"))

            runtime.recovery_required = False
            runtime.jobs["job-1"].update(runtime_semantics("succeeded"))
            with HostStorage(directory) as storage:
                reconciled = host(storage, runtime).reconcile(task_plan.task_id)
                self.assertEqual(reconciled.state, TaskState.VERIFYING)

    def test_response_loss_reconciles_original_job_across_fresh_hosts(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.drop_first_success = True
        task_plan = plan("response-loss")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                unknown = runner.deliver(prepared)
                self.assertEqual(unknown.state, TaskState.WAITING)
                self.assertEqual(unknown.revision, 4)

            with HostStorage(directory) as storage:
                reconciled = host(storage, runtime).reconcile(task_plan.task_id)
                self.assertEqual(reconciled.state, TaskState.VERIFYING)
                self.assertTrue(reconciled.reconciled)
                self.assertEqual(reconciled.job_id, "job-1")

            with HostStorage(directory) as storage:
                verified = host(storage, runtime).verify(task_plan.task_id)
                self.assertEqual(verified.state, TaskState.READY)

            with HostStorage(directory) as storage:
                completed = host(storage, runtime).close(task_plan.task_id)
                self.assertTrue(completed.completed)
                projection = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(projection)
                assert projection is not None
                self.assertEqual(projection.state, TaskState.COMPLETED)
                self.assertEqual(storage.rebuild_task(task_plan.task_id), projection)
                kinds = {item.kind for item in storage.journal.object_refs()}
                self.assertTrue(
                    {
                        "effect",
                        "effect-binding",
                        "runtime-dispatch-intent",
                        "runtime-uncertain-delivery",
                        "runtime-job-observation",
                        "mutation-read-observation",
                        "verification-receipt",
                        "task-outcome",
                    }.issubset(kinds)
                )

        self.assertTrue(runtime.response_dropped)
        self.assertEqual(runtime.physical_deliveries, 1)
        self.assertEqual(runtime.calls.count("workspace.exec"), 1)
        self.assertGreaterEqual(runtime.calls.count("task.list"), 1)
        self.assertTrue(runtime.task_list_arguments)
        self.assertTrue(
            all(
                item.get("clientRequestId") == prepared.dispatch.client_request_id
                for item in runtime.task_list_arguments
            )
        )
        self.assertEqual(runtime.calls.count("task.observe"), 1)
        self.assertNotIn(task_plan.workspace_id, runtime.workspaces)

    def test_crash_after_delivery_before_host_commit_still_reconciles(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = plan("commit-gap")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
            runtime.call_tool("workspace.exec", prepared.arguments)

            with HostStorage(directory) as storage:
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 3)
                reconciled = host(storage, runtime).reconcile(task_plan.task_id)
                self.assertEqual(reconciled.state, TaskState.VERIFYING)
            self.assertEqual(runtime.physical_deliveries, 1)
            self.assertEqual(runtime.calls.count("workspace.exec"), 1)

    def test_missing_original_job_never_redispatches(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = plan("missing-job")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                runner.prepare(task_plan.task_id)
            for _ in range(3):
                with HostStorage(directory) as storage:
                    result = host(storage, runtime).reconcile(task_plan.task_id)
                    self.assertEqual(result.state, TaskState.WAITING)
                    self.assertTrue(result.reconciled)
                    self.assertEqual(result.revision, 3)
            self.assertEqual(runtime.physical_deliveries, 0)
            self.assertEqual(runtime.calls.count("workspace.exec"), 0)

    def test_explicit_not_committed_rejection_is_not_recorded_as_unknown(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.reject_not_committed = True
        task_plan = plan("not-committed")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                with self.assertRaises(RuntimeToolRejected):
                    runner.deliver(prepared)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 3)
                self.assertEqual(current.state, TaskState.WAITING)
                kinds = {item.kind for item in storage.journal.object_refs()}
                self.assertNotIn("runtime-uncertain-delivery", kinds)
        self.assertEqual(runtime.physical_deliveries, 0)
        self.assertEqual(runtime.calls.count("workspace.exec"), 1)

    def test_conflicting_jobs_across_pages_fail_closed(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.task_list_page_size = 1
        task_plan = plan("conflicting-jobs")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
            for index, created_at in enumerate((200, 100), start=1):
                runtime.jobs[f"job-conflict-{index}"] = {
                    "jobId": f"job-conflict-{index}",
                    "attemptId": f"attempt-conflict-{index}",
                    "clientRequestId": prepared.dispatch.client_request_id,
                    "workspaceId": task_plan.workspace_id,
                    "status": "succeeded",
                    **runtime_semantics("succeeded"),
                    "createdAtMs": created_at,
                    "artifacts": [],
                }
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(
                    RuntimeProtocolError, "conflicting Runtime Jobs"
                ):
                    host(storage, runtime).reconcile(task_plan.task_id)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 3)
                self.assertEqual(current.state, TaskState.WAITING)
        self.assertEqual(runtime.calls.count("task.list"), 2)
        self.assertEqual(runtime.calls.count("task.observe"), 0)
        self.assertEqual(runtime.calls.count("workspace.exec"), 0)

    def test_old_task_list_schema_falls_back_to_full_pagination(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.task_list_filter_supported = False
        runtime.task_list_page_size = 1
        runtime.drop_first_success = True
        task_plan = plan("legacy-task-list")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                runner.deliver(prepared)
            runtime.jobs["job-unrelated-newer"] = {
                "jobId": "job-unrelated-newer",
                "attemptId": "attempt-unrelated-newer",
                "clientRequestId": "request:unrelated",
                "workspaceId": task_plan.workspace_id,
                "status": "succeeded",
                **runtime_semantics("succeeded"),
                "createdAtMs": 500,
                "artifacts": [],
            }
            with HostStorage(directory) as storage:
                result = host(storage, runtime).reconcile(task_plan.task_id)
                self.assertEqual(result.state, TaskState.VERIFYING)
                self.assertEqual(result.job_id, "job-1")
        self.assertEqual(runtime.calls.count("task.list"), 2)
        self.assertTrue(
            all("clientRequestId" not in item for item in runtime.task_list_arguments)
        )

    def test_filtered_task_list_mismatch_fails_closed(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.task_list_ignore_filter = True
        task_plan = plan("filtered-mismatch")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
            runtime.jobs["job-wrong-request"] = {
                "jobId": "job-wrong-request",
                "attemptId": "attempt-wrong-request",
                "clientRequestId": "request:wrong",
                "workspaceId": task_plan.workspace_id,
                "status": "succeeded",
                **runtime_semantics("succeeded"),
                "createdAtMs": 500,
                "artifacts": [],
            }
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(
                    RuntimeProtocolError,
                    "another clientRequestId",
                ):
                    host(storage, runtime).reconcile(task_plan.task_id)
                current = storage.journal.get_task(task_plan.task_id)
                assert current is not None
                self.assertEqual(current.revision, prepared.task_revision)
                self.assertEqual(current.state, TaskState.WAITING)
        self.assertEqual(runtime.calls.count("task.observe"), 0)

    def test_catalog_drift_stops_before_dispatch_is_prepared(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = plan("catalog-drift")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
            runtime.catalog_generation = 2
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(RuntimeProtocolError, "catalog changed"):
                    host(storage, runtime).prepare(task_plan.task_id)
                current = storage.journal.get_task(task_plan.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 2)
            self.assertEqual(runtime.calls.count("workspace.exec"), 0)

    def test_verification_mismatch_does_not_advance_or_complete(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = plan("verification-mismatch")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                delivered = runner.deliver(prepared)
                self.assertEqual(delivered.state, TaskState.VERIFYING)
            runtime.files[(task_plan.workspace_id, task_plan.relative_path)] = "tampered\n"
            with HostStorage(directory) as storage:
                before = storage.journal.get_task(task_plan.task_id)
                with self.assertRaisesRegex(
                    MutationVerificationError, "verification failed"
                ):
                    host(storage, runtime).verify(task_plan.task_id)
                after = storage.journal.get_task(task_plan.task_id)
                self.assertEqual(before, after)
                self.assertIsNotNone(after)
                assert after is not None
                self.assertEqual(after.state, TaskState.VERIFYING)

    def test_prepared_dispatch_is_restart_loadable_and_exact(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = plan("prepared-restart")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
            with HostStorage(directory) as storage:
                recovered = host(storage, runtime).load_prepared(task_plan.task_id)
                self.assertEqual(recovered.dispatch, prepared.dispatch)
                self.assertEqual(recovered.arguments, prepared.arguments)
                self.assertEqual(recovered.task_revision, 3)
                effect = storage.objects.get(
                    recovered.effect_object.digest,
                    expected_kind="effect",
                )
                self.assertIsInstance(effect, dict)
                assert isinstance(effect, dict)
                verification = effect.get("verification")
                self.assertIsInstance(verification, dict)
                assert isinstance(verification, dict)
                self.assertEqual(
                    verification.get("requiredEvidence"),
                    ["observation"],
                )
                self.assertEqual(
                    recovered.dispatch.request_digest,
                    prepared.dispatch.request_digest,
                )


if __name__ == "__main__":
    unittest.main()
