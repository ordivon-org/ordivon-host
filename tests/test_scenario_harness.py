from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anc_canonical import canonical_digest
from ordivon_host.runtime import RuntimeErrorDetail, RuntimeToolRejected
from ordivon_host.testing import (
    DropFirstSuccessfulExecResponse,
    DropFirstSuccessfulToolResponse,
    RuntimeClientFactory,
    ScenarioIdentity,
    cleanup_state_root,
    emit_receipt,
    jobs_for_request,
    load_scenario_token,
    scenario_state_root,
    service_state,
    workspace_absent,
)


class FakeRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.page = 0

    def initialize(self) -> dict[str, object]:
        return {"serverInfo": {"name": "fake"}}

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "name": "task.list",
                "inputSchema": {
                    "properties": {"clientRequestId": {"type": "string"}}
                },
            },
        )

    def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((name, dict(arguments)))
        if name in {"workspace.exec", "workspace.execPlan"}:
            return {"jobId": "job:1", "status": "working"}
        if name == "task.list":
            self.page += 1
            if self.page == 1:
                return {
                    "jobs": [
                        {
                            "jobId": "job:1",
                            "clientRequestId": arguments["clientRequestId"],
                        }
                    ],
                    "nextCursor": {"createdAtMs": 1, "jobId": "job:1"},
                }
            return {"jobs": [], "nextCursor": None}
        if name == "workspace.get":
            detail = RuntimeErrorDetail(
                code="INVALID_REQUEST",
                message="missing",
                field="workspaceId",
                retryable=False,
                retry_class=None,
                commit_state="not_committed",
                origin="runtime",
                trace_id=None,
                raw={},
            )
            raise RuntimeToolRejected(name, detail)
        raise AssertionError(name)


class ScenarioHarnessTests(unittest.TestCase):
    def test_identity_and_state_root_are_bounded(self) -> None:
        identity = ScenarioIdentity.create("scenario", stamp_ms=123)
        self.assertEqual(identity.task_id, f"task:scenario-123-{identity.nonce}")
        self.assertEqual(identity.goal_id, f"goal:scenario-123-{identity.nonce}")
        self.assertEqual(identity.workspace_id, f"host-scenario-123-{identity.nonce}")
        with tempfile.TemporaryDirectory() as directory:
            requested = Path(directory) / "state"
            root = scenario_state_root(
                requested, prefix="test", identity=identity
            )
            self.assertEqual(root, requested)
            self.assertTrue(root.is_dir())
            cleanup_state_root(root, keep=False)
            self.assertFalse(root.exists())

    def test_token_loading_prefers_explicit_token_then_file(self) -> None:
        self.assertEqual(
            load_scenario_token({"ORDIVON_BEARER_TOKEN": "inline-token"}),
            "inline-token",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("file-token\n")
            self.assertEqual(
                load_scenario_token({"ORDIVON_BEARER_TOKEN_FILE": str(path)}),
                "file-token",
            )
        with self.assertRaises(RuntimeError):
            load_scenario_token({})

    def test_receipt_integrity_is_canonical(self) -> None:
        receipt = {"schemaVersion": 1, "kind": "test", "value": 2}
        expected = canonical_digest(receipt)
        output = io.StringIO()
        with redirect_stdout(output):
            emit_receipt(receipt)
        value = json.loads(output.getvalue())
        self.assertEqual(value["integrity"]["payloadDigest"], expected)
        self.assertEqual(receipt["integrity"]["payloadDigest"], expected)

    def test_fault_injector_drops_only_first_successful_exec_response(self) -> None:
        fake = FakeRuntimeClient()
        lossy = DropFirstSuccessfulExecResponse(fake)  # type: ignore[arg-type]
        with self.assertRaisesRegex(RuntimeError, "injected response loss"):
            lossy.call_tool("workspace.exec", {"schemaVersion": 1})
        result = lossy.call_tool("workspace.exec", {"schemaVersion": 1})
        self.assertEqual(result["jobId"], "job:1")
        self.assertEqual(lossy.calls.count("workspace.exec"), 2)

    def test_fault_injector_can_target_exec_plan(self) -> None:
        fake = FakeRuntimeClient()
        lossy = DropFirstSuccessfulToolResponse(
            fake, "workspace.execPlan"  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(RuntimeError, "workspace.execPlan"):
            lossy.call_tool("workspace.execPlan", {"schemaVersion": 1})
        result = lossy.call_tool("workspace.execPlan", {"schemaVersion": 1})
        self.assertEqual(result["jobId"], "job:1")
        self.assertEqual(lossy.calls.count("workspace.execPlan"), 2)

    def test_runtime_audit_helpers_use_exact_request_identity(self) -> None:
        fake = FakeRuntimeClient()
        jobs = jobs_for_request(fake, "request:1")  # type: ignore[arg-type]
        self.assertEqual([job["jobId"] for job in jobs], ["job:1"])
        self.assertEqual(fake.calls[0][1]["clientRequestId"], "request:1")
        self.assertTrue(workspace_absent(fake, "workspace:1"))  # type: ignore[arg-type]

    def test_factory_keeps_token_private_and_builds_client_identity(self) -> None:
        factory = RuntimeClientFactory(
            "http://127.0.0.1:8897/mcp", "secret", "scenario-client"
        )
        client = factory.client("one")
        self.assertEqual(client.client_name, "scenario-client-one")
        self.assertNotIn("secret", repr(factory))
        self.assertFalse(hasattr(client, "token"))

    def test_service_state_rejects_unsafe_name_before_subprocess(self) -> None:
        with patch("subprocess.run") as run:
            with self.assertRaises(ValueError):
                service_state("bad service; reboot")
            run.assert_not_called()
