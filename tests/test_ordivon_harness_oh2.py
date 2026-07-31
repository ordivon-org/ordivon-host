from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from typing import Any

from anc_canonical import canonical_digest

from ordivon_host.harness import (
    CommittedHarnessAssignment,
    HarnessAssignment,
    HarnessCapabilityManifest,
    TaskAttemptDescriptor,
)
from ordivon_host.harness.ordivon import (
    AgentToolCall,
    RuntimeToolBridge,
    ToolBridgeError,
    discover_harness_runtime_catalog,
)
from ordivon_host.objects import StoredObject
from ordivon_host.runtime import (
    RuntimeErrorDetail,
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)


_REQUIRED = (
    "artifact.read",
    "task.list",
    "task.observe",
    "workspace.diff",
    "workspace.exec",
    "workspace.mutate",
    "workspace.read",
)


class _Runtime:
    def __init__(self, *, lose_exec_response: bool = False) -> None:
        self.lose_exec_response = lose_exec_response
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exec_calls = 0

    def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": "test"}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        values = []
        for name in _REQUIRED:
            properties: dict[str, Any] = {}
            if name == "task.list":
                properties["clientRequestId"] = {"type": "string"}
            values.append(
                {
                    "name": name,
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                    },
                    "outputSchema": {"type": "object"},
                    "execution": "asynchronous" if name == "workspace.exec" else "synchronous",
                }
            )
        return tuple(values)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "workspace.exec":
            self.exec_calls += 1
            if self.lose_exec_response:
                raise RuntimeTransportError("response dropped after possible commitment")
            return {
                "jobId": "job:test-direct",
                "status": "working",
                "artifacts": [],
            }
        if name == "task.list":
            request_id = arguments.get("clientRequestId")
            return {
                "jobs": [
                    {
                        "jobId": "job:test-recovered",
                        "clientRequestId": request_id,
                        "status": "working",
                    }
                ],
                "nextCursor": None,
            }
        if name == "task.observe":
            return {
                "jobId": arguments["jobId"],
                "status": "working",
                "artifacts": [
                    {
                        "artifactId": "artifact:test-output",
                        "kind": "stdout",
                        "digest": canonical_digest("output"),
                    }
                ],
            }
        if name == "workspace.read":
            return {"content": "hello", "digest": canonical_digest("hello")}
        return {"ok": True}


class _RejectingRuntime(_Runtime):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "workspace.read":
            raise RuntimeToolRejected(
                name,
                RuntimeErrorDetail(
                    code="INVALID_REQUEST",
                    message="bad path",
                    field="relativePath",
                    retryable=False,
                    retry_class=None,
                    commit_state="not_committed",
                    origin="runtime",
                    trace_id="trace:test",
                    raw={},
                ),
            )
        return super().call_tool(name, arguments)


def _stored(kind: str, suffix: str) -> StoredObject:
    return StoredObject(canonical_digest({"kind": kind, "suffix": suffix}), 0, kind)


def _committed(runtime: _Runtime) -> CommittedHarnessAssignment:
    catalog = discover_harness_runtime_catalog(runtime)
    manifest = HarnessCapabilityManifest(
        harness_id="ordivon-harness-v0",
        protocol="ordivon.agent-loop",
        protocol_revision="oh2",
        persistent_session=False,
        session_resume=False,
        session_fork=False,
        interrupt=True,
        tool_events=True,
        approval_events=False,
        usage=True,
        images=False,
        compaction=False,
        checkpoint=False,
        local_subagents=False,
    )
    attempt = TaskAttemptDescriptor(
        task_attempt_id="task-attempt:test:1",
        task_id="task:test",
        started_at_task_revision=1,
        objective_digest=canonical_digest({"objective": 1}),
        acceptance_criteria_digest=canonical_digest({"acceptance": 1}),
        created_at_ms=1,
    )
    assignment = HarnessAssignment(
        assignment_id="assignment:test:g1",
        task_id="task:test",
        task_revision=1,
        task_attempt_id=attempt.task_attempt_id,
        generation=1,
        target_harness_id=manifest.harness_id,
        harness_manifest_digest=manifest.digest,
        context_object_digest=canonical_digest({"context": 1}),
        acceptance_criteria_digest=attempt.acceptance_criteria_digest,
        tool_catalog_digest=catalog.digest,
        workspace_ref="workspace:test",
        source_ref="repo:test@abc",
        source_digest=canonical_digest({"source": 1}),
        prior_artifact_refs=(),
        required_capabilities=("tool_events",),
        budget={"maxToolCalls": 8},
        deadline_ms=None,
        created_at_ms=2,
    )
    return CommittedHarnessAssignment(
        attempt=attempt,
        attempt_object=_stored("task-attempt-descriptor", "attempt"),
        manifest=manifest,
        manifest_object=_stored("harness-capability-manifest", "manifest"),
        assignment=assignment,
        assignment_object=_stored("harness-assignment", "assignment"),
        task_revision=2,
    )


class OrdivonHarnessOH2Tests(unittest.TestCase):
    def test_runtime_catalog_binds_runtime_and_model_aci(self) -> None:
        catalog = discover_harness_runtime_catalog(_Runtime())
        self.assertEqual(catalog.runtime_operations, _REQUIRED)
        self.assertEqual(
            tuple(tool.name for tool in catalog.model_tools),
            (
                "read_workspace",
                "mutate_workspace",
                "diff_workspace",
                "run_check",
                "run_in_workspace",
                "observe_job",
                "read_artifact",
            ),
        )
        self.assertTrue(catalog.digest.startswith("sha256:"))

    def test_exec_request_is_assignment_and_run_bound(self) -> None:
        runtime = _Runtime()
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-oh2",
            runtime=runtime,
        )
        observation = bridge.execute(
            AgentToolCall(
                "tool-call:exec-1",
                "run_in_workspace",
                {
                    "executable": "/usr/bin/python3",
                    "args": ["-V"],
                    "waitMs": 0,
                },
            ),
            step_id="turn-1-tool-1",
        )
        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.runtime_job_ref, "job:test-direct")
        exec_calls = [call for call in runtime.calls if call[0] == "workspace.exec"]
        self.assertEqual(len(exec_calls), 1)
        request = exec_calls[0][1]
        self.assertTrue(str(request["clientRequestId"]).startswith("request:harness:g1:"))
        references = request["execution"]["foreignReferences"]
        self.assertEqual(
            [item["type"] for item in references],
            ["assignment", "harness_run", "task", "task_attempt"],
        )

    def test_response_loss_reconciles_original_job_without_redispatch(self) -> None:
        runtime = _Runtime(lose_exec_response=True)
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-loss",
            runtime=runtime,
        )
        observation = bridge.execute(
            AgentToolCall(
                "tool-call:exec-loss",
                "run_in_workspace",
                {"executable": "/usr/bin/true"},
            ),
            step_id="turn-1-tool-1",
        )
        self.assertEqual(runtime.exec_calls, 1)
        self.assertEqual(observation.status, "observed")
        self.assertTrue(observation.reconciled)
        self.assertEqual(observation.runtime_job_ref, "job:test-recovered")
        self.assertEqual(observation.artifact_refs[0].ref, "artifact:test-output")

    def test_pre_admission_rejection_is_not_dispatch_unknown(self) -> None:
        runtime = _RejectingRuntime()
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-reject",
            runtime=runtime,
        )
        observation = bridge.execute(
            AgentToolCall(
                "tool-call:read-reject",
                "read_workspace",
                {"relativePath": "bad"},
            ),
            step_id="turn-1-tool-1",
        )
        self.assertEqual(observation.status, "rejected")
        self.assertIsNone(observation.runtime_job_ref)
        self.assertEqual(observation.structured_content["error"]["commitState"], "not_committed")

    def test_artifact_read_binds_producing_job_and_artifact(self) -> None:
        runtime = _Runtime()
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-artifact-read",
            runtime=runtime,
        )
        observation = bridge.execute(
            AgentToolCall(
                "tool-call:artifact-read",
                "read_artifact",
                {
                    "jobId": "job:test-output",
                    "artifactId": "artifact:test-output",
                    "offset": 0,
                    "maxBytes": 4_096,
                },
            ),
            step_id="turn-2-tool-1",
        )
        self.assertEqual(observation.status, "observed")
        operation, arguments = runtime.calls[-1]
        self.assertEqual(operation, "artifact.read")
        self.assertEqual(arguments["schemaVersion"], 1)
        self.assertEqual(arguments["jobId"], "job:test-output")
        self.assertEqual(arguments["artifactId"], "artifact:test-output")

    def test_invalid_model_arguments_fail_before_runtime_call(self) -> None:
        runtime = _Runtime()
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-invalid",
            runtime=runtime,
        )
        calls_before = len(runtime.calls)
        with self.assertRaises(ToolBridgeError):
            bridge.execute(
                AgentToolCall(
                    "tool-call:invalid",
                    "run_in_workspace",
                    {"executable": "relative-command"},
                ),
                step_id="turn-1-tool-1",
            )
        self.assertEqual(len(runtime.calls), calls_before)

    def test_duplicate_tool_call_identity_cannot_dispatch_twice(self) -> None:
        runtime = _Runtime()
        bridge = RuntimeToolBridge(
            _committed(runtime),
            harness_run_id="harness-run:test-duplicate",
            runtime=runtime,
        )
        call = AgentToolCall(
            "tool-call:duplicate",
            "read_workspace",
            {"relativePath": "README.md"},
        )
        bridge.execute(call, step_id="turn-1-tool-1")
        with self.assertRaises(ToolBridgeError):
            bridge.execute(call, step_id="turn-1-tool-1")
        self.assertEqual(
            len([item for item in runtime.calls if item[0] == "workspace.read"]),
            1,
        )

    def test_catalog_drift_rejects_bridge_construction(self) -> None:
        runtime = _Runtime()
        committed = _committed(runtime)
        drifted = replace(
            committed.assignment,
            tool_catalog_digest=canonical_digest({"drift": True}),
        )
        drifted_committed = CommittedHarnessAssignment(
            attempt=committed.attempt,
            attempt_object=committed.attempt_object,
            manifest=committed.manifest,
            manifest_object=committed.manifest_object,
            assignment=drifted,
            assignment_object=committed.assignment_object,
            task_revision=committed.task_revision,
        )
        with self.assertRaises(RuntimeProtocolError):
            RuntimeToolBridge(
                drifted_committed,
                harness_run_id="harness-run:test-drift",
                runtime=runtime,
            )

    def test_ordivon_package_does_not_import_host_storage_or_kernel(self) -> None:
        root = Path("src/ordivon_host/harness/ordivon")
        source = "\n".join(path.read_text() for path in sorted(root.glob("*.py")))
        self.assertNotIn("HostStorage", source)
        self.assertNotIn("HostKernel", source)
        self.assertNotIn("sqlite", source.lower())


if __name__ == "__main__":
    unittest.main()
