from __future__ import annotations

import io
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error
from typing import Any

from anc_canonical import canonical_digest

from ordivon_host import (
    EventKind,
    HarnessHost,
    HarnessLifecycleError,
    NativeRunAbandonment,
    NativeRunRecoveryAssessment,
    HarnessSuperseded,
    HostKernel,
    HostStorage,
    TaskContract,
    TaskState,
    ToolGrant,
    operator_handoff,
)
from ordivon_host.cognition import BlockKind, ContextBlock, Freshness
from ordivon_host.harness import NativeRunRecoveryController
from ordivon_host.harness.ordivon import (
    AgentRunConclusion,
    AgentToolCall,
    AgentToolDefinition,
    AgentTurnAdapterError,
    AgentTurnFailureCode,
    AgentTurnResult,
    CancellationToken,
    HarnessContextCompiler,
    HarnessContextRequest,
    NativeRunTimes,
    OrdivonAgentLoop,
    RunBudget,
    RunStopCode,
    RuntimeToolBridge,
    UrllibDeepSeekTransport,
    ScriptedTurnAdapter,
    discover_harness_runtime_catalog,
    ordivon_harness_manifest,
    record_native_run_result,
)
from ordivon_host.ops import validate_history
from ordivon_host.runtime import (
    RuntimeErrorDetail,
    RuntimeToolRejected,
    RuntimeTransportError,
)

TASK_ID = "task:oh5-native"
GOAL_ID = "goal:oh5-native"
FRONTIER = "node:oh5-native:work"
_REQUIRED_RUNTIME_TOOLS = (
    "artifact.read",
    "task.list",
    "task.observe",
    "workspace.diff",
    "workspace.exec",
    "workspace.mutate",
    "workspace.read",
)


def _missing_workspace(operation: str) -> RuntimeToolRejected:
    return RuntimeToolRejected(
        operation,
        RuntimeErrorDetail(
            code="INVALID_REQUEST",
            message="Workspace does not exist",
            field="workspaceId",
            retryable=False,
            retry_class=None,
            commit_state="not_committed",
            origin="runtime",
            trace_id="trace:oh5-missing-workspace",
            raw={},
        ),
    )


class _RecoveryRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.workspaces: set[str] = set()
        self.cleanup_failure = False
        self.catalog_drift = False
        self.read_transport_failure = False

    def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": "test"}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        values: list[dict[str, Any]] = []
        for name in _REQUIRED_RUNTIME_TOOLS:
            properties: dict[str, Any] = {}
            if name == "task.list":
                properties["clientRequestId"] = {"type": "string"}
            if self.catalog_drift and name == "workspace.read":
                properties["drifted"] = {"type": "boolean"}
            values.append(
                {
                    "name": name,
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                    },
                    "outputSchema": {"type": "object"},
                    "execution": (
                        "asynchronous" if name == "workspace.exec" else "synchronous"
                    ),
                }
            )
        return tuple(values)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        workspace_id = arguments.get("workspaceId")
        if name == "workspace.get":
            if workspace_id not in self.workspaces:
                raise _missing_workspace(name)
            return {"workspaceId": workspace_id, "sourceRevision": "fixture"}
        if name == "workspace.close":
            if self.cleanup_failure:
                raise RuntimeTransportError("Workspace close response was lost")
            if workspace_id not in self.workspaces:
                raise _missing_workspace(name)
            self.workspaces.remove(workspace_id)
            return {"workspaceId": workspace_id, "closed": True}
        if name == "workspace.read":
            if self.read_transport_failure:
                raise RuntimeTransportError("read response was lost")
            return {
                "content": "# Ordivon Host\n",
                "digest": canonical_digest("# Ordivon Host\n"),
            }
        if name == "task.list":
            return {"jobs": [], "nextCursor": None}
        if name == "task.observe":
            return {"jobId": arguments["jobId"], "status": "working", "artifacts": []}
        if name == "workspace.exec":
            return {"jobId": "job:oh5", "status": "working", "artifacts": []}
        if name in {"workspace.diff", "workspace.mutate", "artifact.read"}:
            return {"ok": True}
        raise AssertionError(name)


def _create_task(storage: HostStorage, clock) -> None:
    HostKernel(
        storage,
        clock_ms=clock,
        owner_id="host:oh5-task-create",
    ).create_task(
        event_id="event:oh5-native:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "ordivon-native-harness-oh5"},
        frontier=(FRONTIER,),
    )


def _contract() -> TaskContract:
    return TaskContract(
        contract_id="task-contract:oh5-native:v1",
        task_id=TASK_ID,
        objective={"summary": "Read README.md and report its heading."},
        acceptance_criteria={"checks": ["Observed heading is # Ordivon Host"]},
        constraints=("Use only the granted Workspace.",),
    )


def _grant(kind: str = "read_only") -> ToolGrant:
    if kind == "read_only":
        return ToolGrant(
            tool_grant_id="tool-grant:oh5:read-only",
            allowed_tools=("read_workspace",),
            read_path_rules=("README.md",),
        )
    if kind == "mutation":
        return ToolGrant(
            tool_grant_id="tool-grant:oh5:mutation",
            allowed_tools=("mutate_workspace",),
            mutate_path_rules=("README.md",),
        )
    raise AssertionError(kind)


def _assign(
    storage: HostStorage,
    clock,
    runtime: _RecoveryRuntime,
    *,
    grant: ToolGrant | None = None,
    workspace_id: str = "workspace:oh5:g1",
):
    host = HarnessHost(storage, clock_ms=clock)
    contract = _contract()
    attempt = host.start_attempt(TASK_ID, task_contract=contract)
    context = HarnessContextCompiler().compile(
        attempt.descriptor,
        HarnessContextRequest(
            task_contract=contract,
            blocks=(
                ContextBlock(
                    block_id="context-block:oh5:readme",
                    kind=BlockKind.TASK,
                    priority=100,
                    required=True,
                    freshness=Freshness.CURRENT,
                    source_digest=canonical_digest({"source": "fixture"}),
                    payload={"relativePath": "README.md"},
                ),
            ),
        ),
        token_budget=4_000,
    )
    context_object = storage.put_object(context.to_dict(), kind="compiled-context")
    catalog = discover_harness_runtime_catalog(runtime)
    runtime.workspaces.add(workspace_id)
    committed = host.assign(
        attempt,
        manifest=ordivon_harness_manifest(),
        context_object_digest=context_object.digest,
        tool_catalog_digest=catalog.digest,
            tool_catalog=catalog,
        workspace_ref=workspace_id,
        source_ref="repository:ordivon-host@fixture",
        source_digest=canonical_digest({"revision": "fixture"}),
        required_capabilities=("tool_events",),
        budget={"maxModelCalls": 4, "maxToolCalls": 4},
        tool_grant=grant or _grant(),
    )
    return host, committed, context_object.digest, catalog.digest


class _NoopBridge:
    def __init__(self, digest: str) -> None:
        self.catalog_digest = digest

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return ()

    def execute(self, call: AgentToolCall, *, step_id: str):
        raise AssertionError((call, step_id))


class _FailingAdapter:
    adapter_id = "ordivon.test-failing-adapter.v1"
    model_id = "ordivon.test-model.v1"

    def __init__(self, code: AgentTurnFailureCode) -> None:
        self.code = code

    def invoke(self, request):
        raise AgentTurnAdapterError("injected Provider failure", failure_code=self.code)


def _fault_result(code: AgentTurnFailureCode, catalog_digest: str):
    return OrdivonAgentLoop(
        _FailingAdapter(code),
        _NoopBridge(catalog_digest),
        budget=RunBudget(2, 2, 65_536, 30_000),
        clock_ms=itertools.count(10_000).__next__,
    ).run(
        harness_run_id="harness-run:oh5-fault",
        assignment_id="assignment:oh5-fault:g1",
        context_digest=canonical_digest({"context": "fault"}),
        initial_messages=({"role": "user", "content": "test"},),
    )


def _conclusion_result(committed, context_digest: str):
    assert committed.native_run_contract is not None
    adapter = ScriptedTurnAdapter(
        (
            AgentTurnResult(
                model_call_id="model-call:oh5:conclusion",
                model_id="ordivon.scripted-model.v1",
                content=None,
                tool_calls=(),
                conclusion=AgentRunConclusion(
                    status="candidate_completed",
                    summary="Fixture candidate.",
                ),
                usage={},
                finish_reason="tool_calls",
                raw_response_digest=canonical_digest({"response": "oh5"}),
            ),
        )
    )
    return OrdivonAgentLoop(
        adapter,
        _NoopBridge(committed.assignment.tool_catalog_digest),
        budget=RunBudget(2, 2, 65_536, 30_000),
        clock_ms=itertools.count(20_000).__next__,
    ).run(
        harness_run_id=committed.native_run_contract.harness_run_id,
        assignment_id=committed.assignment.assignment_id,
        context_digest=context_digest,
        initial_messages=({"role": "user", "content": "test"},),
    )


class OH5RecoveryModelTests(unittest.TestCase):
    def test_recovery_and_abandonment_round_trip_and_reject_derived_drift(self) -> None:
        assessment = NativeRunRecoveryAssessment(
            assessment_id="harness-run-recovery:oh5-model:r1",
            sequence=1,
            harness_run_id="harness-run:oh5-model",
            assignment_id="assignment:oh5-model:g1",
            assignment_generation=1,
            assignment_digest=canonical_digest({"assignment": "oh5-model"}),
            trigger="process_lost",
            grant_effect_class="read_only",
            catalog_status="matched",
            workspace_status="closed",
            workspace_evidence={"workspaceId": "workspace:oh5-model", "closed": True},
            unresolved_unknowns=(),
            created_at_ms=1,
        )
        self.assertTrue(assessment.safe_to_abandon)
        self.assertEqual(
            NativeRunRecoveryAssessment.from_dict(assessment.to_dict()),
            assessment,
        )
        drifted = assessment.to_dict()
        drifted["safeToAbandon"] = False
        with self.assertRaisesRegex(ValueError, "safeToAbandon differs"):
            NativeRunRecoveryAssessment.from_dict(drifted)
        object_digest = canonical_digest(
            {
                "schemaVersion": 1,
                "kind": "native-run-recovery-assessment",
                "payload": assessment.to_dict(),
            }
        )
        abandonment = NativeRunAbandonment(
            abandonment_id="harness-run-abandonment:oh5-model",
            harness_run_id=assessment.harness_run_id,
            assignment_id=assessment.assignment_id,
            assignment_generation=assessment.assignment_generation,
            assignment_digest=assessment.assignment_digest,
            recovery_assessment_digest=assessment.digest,
            recovery_assessment_object_digest=object_digest,
            reason_code="process_lost",
            created_at_ms=2,
        )
        self.assertEqual(
            NativeRunAbandonment.from_dict(abandonment.to_dict()),
            abandonment,
        )
        invalid = abandonment.to_dict()
        invalid["replacementAllowed"] = False
        with self.assertRaisesRegex(ValueError, "replacementAllowed"):
            NativeRunAbandonment.from_dict(invalid)


class OH5ProviderFaultTests(unittest.TestCase):
    def test_deepseek_transport_classifies_http_network_and_timeout_faults(self) -> None:
        transport = UrllibDeepSeekTransport()
        cases = (
            (
                urllib.error.HTTPError(
                    "https://api.deepseek.com/chat/completions",
                    429,
                    "rate limited",
                    {},
                    io.BytesIO(b"rate limited"),
                ),
                AgentTurnFailureCode.UNAVAILABLE,
            ),
            (
                urllib.error.HTTPError(
                    "https://api.deepseek.com/chat/completions",
                    400,
                    "invalid",
                    {},
                    io.BytesIO(b"invalid"),
                ),
                AgentTurnFailureCode.REJECTED,
            ),
            (
                urllib.error.HTTPError(
                    "https://api.deepseek.com/chat/completions",
                    504,
                    "timeout",
                    {},
                    io.BytesIO(b"timeout"),
                ),
                AgentTurnFailureCode.TIMEOUT,
            ),
            (
                urllib.error.URLError("network unavailable"),
                AgentTurnFailureCode.TRANSPORT_FAILED,
            ),
            (TimeoutError("timed out"), AgentTurnFailureCode.TIMEOUT),
        )
        for error, expected in cases:
            with self.subTest(expected=expected.value):
                with mock.patch(
                    "urllib.request.urlopen", side_effect=error
                ) as urlopen:
                    with self.assertRaises(AgentTurnAdapterError) as raised:
                        transport.post(
                            "https://api.deepseek.com/chat/completions",
                            headers={"Content-Type": "application/json"},
                            body=b"{}",
                            timeout_seconds=1.0,
                            max_response_bytes=1_024,
                        )
                    self.assertEqual(raised.exception.failure_code, expected)
                    urlopen.assert_called_once()

    def test_provider_fault_codes_remain_exact_and_never_auto_retry(self) -> None:
        expected = {
            AgentTurnFailureCode.FAILED: RunStopCode.PROVIDER_FAILED,
            AgentTurnFailureCode.TIMEOUT: RunStopCode.PROVIDER_TIMEOUT,
            AgentTurnFailureCode.TRANSPORT_FAILED: RunStopCode.PROVIDER_TRANSPORT_FAILED,
            AgentTurnFailureCode.REJECTED: RunStopCode.PROVIDER_REJECTED,
            AgentTurnFailureCode.UNAVAILABLE: RunStopCode.PROVIDER_UNAVAILABLE,
        }
        digest = canonical_digest({"catalog": "faults"})
        for failure, stop_code in expected.items():
            with self.subTest(failure=failure.value):
                result = _fault_result(failure, digest)
                self.assertEqual(result.stop_code, stop_code)
                self.assertEqual(result.model_calls, 0)
                self.assertEqual(
                    [event.kind for event in result.trace.events].count(
                        "model_call_started"
                    ),
                    1,
                )

    def test_invalid_output_budget_and_cancellation_remain_distinct(self) -> None:
        digest = canonical_digest({"catalog": "faults"})
        invalid = ScriptedTurnAdapter(
            (
                AgentTurnResult(
                    model_call_id="model-call:oh5:invalid",
                    model_id="another-model",
                    content=None,
                    tool_calls=(),
                    conclusion=AgentRunConclusion(
                        status="needs_input",
                        summary="fixture",
                    ),
                    usage={},
                    finish_reason="tool_calls",
                    raw_response_digest=canonical_digest({"invalid": True}),
                ),
            )
        )
        invalid_result = OrdivonAgentLoop(
            invalid,
            _NoopBridge(digest),
            budget=RunBudget(2, 2, 65_536, 30_000),
        ).run(
            harness_run_id="harness-run:oh5-invalid",
            assignment_id="assignment:oh5-invalid:g1",
            context_digest=canonical_digest({"context": "invalid"}),
            initial_messages=({"role": "user", "content": "test"},),
        )
        self.assertEqual(invalid_result.stop_code, RunStopCode.INVALID_MODEL_OUTPUT)

        budget_result = OrdivonAgentLoop(
            ScriptedTurnAdapter(
                (
                    AgentTurnResult(
                        model_call_id="model-call:oh5:budget",
                        model_id="ordivon.scripted-model.v1",
                        content=None,
                        tool_calls=(
                            AgentToolCall("tool-call:1", "one", {}),
                            AgentToolCall("tool-call:2", "two", {}),
                        ),
                        conclusion=None,
                        usage={},
                        finish_reason="tool_calls",
                        raw_response_digest=canonical_digest({"budget": True}),
                    ),
                )
            ),
            _NoopBridge(digest),
            budget=RunBudget(2, 1, 65_536, 30_000),
        ).run(
            harness_run_id="harness-run:oh5-budget",
            assignment_id="assignment:oh5-budget:g1",
            context_digest=canonical_digest({"context": "budget"}),
            initial_messages=({"role": "user", "content": "test"},),
        )
        self.assertEqual(budget_result.stop_code, RunStopCode.BUDGET_EXHAUSTED)

        cancellation = CancellationToken()
        cancellation.cancel()
        cancelled = OrdivonAgentLoop(
            _FailingAdapter(AgentTurnFailureCode.FAILED),
            _NoopBridge(digest),
            budget=RunBudget(2, 2, 65_536, 30_000),
        ).run(
            harness_run_id="harness-run:oh5-cancelled",
            assignment_id="assignment:oh5-cancelled:g1",
            context_digest=canonical_digest({"context": "cancelled"}),
            initial_messages=({"role": "user", "content": "test"},),
            cancellation=cancellation,
        )
        self.assertEqual(cancelled.stop_code, RunStopCode.CANCELLED)


class OH5AbandonmentTests(unittest.TestCase):
    def test_read_only_process_loss_closes_workspace_abandons_and_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _, committed, context_digest, catalog_digest = _assign(
                    storage, clock, runtime
                )
                old_result = _conclusion_result(committed, context_digest)

            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                recovered = NativeRunRecoveryController(host, runtime).recover(
                    TASK_ID,
                    trigger="process_lost",
                )
                self.assertTrue(recovered.recovery.assessment.safe_to_abandon)
                self.assertEqual(
                    recovered.recovery.assessment.workspace_status,
                    "closed",
                )
                self.assertIsNotNone(recovered.abandonment)
                self.assertNotIn("workspace:oh5:g1", runtime.workspaces)
                handoff = operator_handoff(storage, TASK_ID)
                self.assertEqual(
                    handoff.next_admissible,
                    ("replace-harness-assignment",),
                )
                object_count = len(tuple((Path(directory) / "objects").glob("*.json")))
                with self.assertRaises(HarnessSuperseded):
                    record_native_run_result(
                        host,
                        committed,
                        old_result,
                        times=NativeRunTimes(20_000, 20_010),
                    )
                self.assertEqual(
                    len(tuple((Path(directory) / "objects").glob("*.json"))),
                    object_count,
                )
                attempt = host.load_attempt(TASK_ID)
                new_workspace = "workspace:oh5:g2"
                runtime.workspaces.add(new_workspace)
                replacement = host.assign(
                    attempt,
                    manifest=ordivon_harness_manifest(),
                    context_object_digest=context_digest,
                    tool_catalog_digest=catalog_digest,
                    workspace_ref=new_workspace,
                    source_ref="repository:ordivon-host@fixture",
                    source_digest=canonical_digest({"revision": "fixture"}),
                    required_capabilities=("tool_events",),
                    budget={"maxModelCalls": 4, "maxToolCalls": 4},
                    tool_grant=_grant(),
                )
                self.assertEqual(replacement.assignment.generation, 2)
                history = validate_history(storage)
                self.assertGreaterEqual(history.semantic_references, 10)
                self.assertGreaterEqual(history.semantic_link_checks, 3)

    def test_abandonment_reason_must_match_recovery_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_500).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _assign(storage, clock, runtime)
            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                result = NativeRunRecoveryController(host, runtime).recover(
                    TASK_ID,
                    trigger="process_lost",
                    auto_abandon=False,
                )
                with self.assertRaisesRegex(ValueError, "must match"):
                    host.abandon_native_run(
                        result.recovery,
                        reason_code="host_restart",
                    )
                retained = host.abandon_native_run(
                    result.recovery,
                    reason_code="process_lost",
                )
                self.assertEqual(
                    retained.abandonment.reason_code,
                    result.recovery.assessment.trigger,
                )

    def test_effectful_unrecorded_run_is_blocked_even_after_workspace_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _assign(storage, clock, runtime, grant=_grant("mutation"))
            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                result = NativeRunRecoveryController(host, runtime).recover(
                    TASK_ID,
                    trigger="host_restart",
                )
                self.assertIsNone(result.abandonment)
                self.assertFalse(result.recovery.assessment.safe_to_abandon)
                self.assertEqual(
                    result.recovery.assessment.grant_effect_class,
                    "workspace-change-possible",
                )
                self.assertEqual(
                    storage.journal.get_task(TASK_ID).state,
                    TaskState.BLOCKED,
                )
                self.assertEqual(
                    operator_handoff(storage, TASK_ID).next_admissible,
                    ("reconcile-current-harness-run-unknown",),
                )
                attempt = host.load_attempt(TASK_ID)
                with self.assertRaisesRegex(HarnessLifecycleError, "Runtime UNKNOWN"):
                    host.assign(
                        attempt,
                        manifest=ordivon_harness_manifest(),
                        context_object_digest=host.load_current_assignment(
                            TASK_ID
                        ).assignment.context_object_digest,
                        tool_catalog_digest=host.load_current_assignment(
                            TASK_ID
                        ).assignment.tool_catalog_digest,
                        workspace_ref="workspace:oh5:g2",
                        tool_grant=_grant("mutation"),
                    )

    def test_cleanup_unknown_can_be_reassessed_then_abandoned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            runtime = _RecoveryRuntime()
            runtime.cleanup_failure = True
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _assign(storage, clock, runtime)
            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                first = NativeRunRecoveryController(host, runtime).recover(TASK_ID)
                self.assertIsNone(first.abandonment)
                self.assertEqual(first.recovery.assessment.workspace_status, "unknown")
                self.assertEqual(first.recovery.assessment.sequence, 1)
            runtime.cleanup_failure = False
            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                second = NativeRunRecoveryController(host, runtime).recover(TASK_ID)
                self.assertEqual(second.recovery.assessment.sequence, 2)
                self.assertEqual(second.recovery.assessment.workspace_status, "closed")
                self.assertIsNotNone(second.abandonment)
                retained = host.load_current_native_run_abandonment(TASK_ID)
                self.assertEqual(retained.recovery.assessment.sequence, 2)

    def test_catalog_drift_is_recorded_but_does_not_fake_effect_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(4_000).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _assign(storage, clock, runtime)
            runtime.catalog_drift = True
            with HostStorage(directory) as storage:
                host = HarnessHost(storage, clock_ms=clock)
                result = NativeRunRecoveryController(host, runtime).recover(TASK_ID)
                self.assertEqual(result.recovery.assessment.catalog_status, "drifted")
                self.assertTrue(result.recovery.assessment.safe_to_abandon)
                self.assertIsNotNone(result.abandonment)


class OH5RecordedUnknownTests(unittest.TestCase):
    def test_runtime_unknown_receipt_persists_and_blocks_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(5_000).__next__
            runtime = _RecoveryRuntime()
            runtime.read_transport_failure = True
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                host, committed, context_digest, _ = _assign(storage, clock, runtime)
                assert committed.native_run_contract is not None
                result = OrdivonAgentLoop(
                    ScriptedTurnAdapter(
                        (
                            AgentTurnResult(
                                model_call_id="model-call:oh5:runtime-unknown",
                                model_id="ordivon.scripted-model.v1",
                                content=None,
                                tool_calls=(
                                    AgentToolCall(
                                        "tool-call:oh5:read",
                                        "read_workspace",
                                        {"relativePath": "README.md"},
                                    ),
                                ),
                                conclusion=None,
                                usage={},
                                finish_reason="tool_calls",
                                raw_response_digest=canonical_digest(
                                    {"response": "runtime-unknown"}
                                ),
                            ),
                        )
                    ),
                    RuntimeToolBridge(
                        committed,
                        harness_run_id=committed.native_run_contract.harness_run_id,
                        runtime=runtime,
                    ),
                    budget=RunBudget(2, 2, 65_536, 30_000),
                ).run(
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    assignment_id=committed.assignment.assignment_id,
                    context_digest=context_digest,
                    initial_messages=({"role": "user", "content": "read"},),
                )
                self.assertEqual(result.stop_code, RunStopCode.RUNTIME_UNKNOWN)
                recorded = record_native_run_result(
                    host,
                    committed,
                    result,
                    times=NativeRunTimes(30_000, 30_010),
                )
                self.assertEqual(recorded.receipt.termination_code, "runtime_unknown")

            with HostStorage(directory) as storage:
                fresh = HarnessHost(storage, clock_ms=clock)
                recorded = fresh.load_current_run(TASK_ID)
                self.assertEqual(recorded.receipt.termination_code, "runtime_unknown")
                self.assertEqual(
                    operator_handoff(storage, TASK_ID).next_admissible,
                    ("reconcile-current-harness-run-unknown",),
                )
                attempt = fresh.load_attempt(TASK_ID)
                with self.assertRaisesRegex(HarnessLifecycleError, "Runtime UNKNOWN"):
                    fresh.assign(
                        attempt,
                        manifest=ordivon_harness_manifest(),
                        context_object_digest=recorded.assignment.assignment.context_object_digest,
                        tool_catalog_digest=recorded.assignment.assignment.tool_catalog_digest,
                        workspace_ref="workspace:oh5:g2",
                        tool_grant=_grant(),
                    )

    def test_effectful_recorded_run_requires_verification_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(5_500).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                host, committed, context_digest, _ = _assign(
                    storage,
                    clock,
                    runtime,
                    grant=_grant("mutation"),
                )
                assert committed.native_run_contract is not None
                result = OrdivonAgentLoop(
                    ScriptedTurnAdapter(
                        (
                            AgentTurnResult(
                                model_call_id="model-call:oh5:mutation:1",
                                model_id="ordivon.scripted-model.v1",
                                content=None,
                                tool_calls=(
                                    AgentToolCall(
                                        "tool-call:oh5:mutation",
                                        "mutate_workspace",
                                        {
                                            "mutations": [
                                                {
                                                    "mode": "WRITE",
                                                    "relativePath": "README.md",
                                                    "content": "changed",
                                                }
                                            ]
                                        },
                                    ),
                                ),
                                conclusion=None,
                                usage={},
                                finish_reason="tool_calls",
                                raw_response_digest=canonical_digest(
                                    {"mutation": 1}
                                ),
                            ),
                            AgentTurnResult(
                                model_call_id="model-call:oh5:mutation:2",
                                model_id="ordivon.scripted-model.v1",
                                content=None,
                                tool_calls=(),
                                conclusion=AgentRunConclusion(
                                    status="candidate_completed",
                                    summary="Mutation candidate requires verification.",
                                ),
                                usage={},
                                finish_reason="tool_calls",
                                raw_response_digest=canonical_digest(
                                    {"mutation": 2}
                                ),
                            ),
                        )
                    ),
                    RuntimeToolBridge(
                        committed,
                        harness_run_id=committed.native_run_contract.harness_run_id,
                        runtime=runtime,
                    ),
                    budget=RunBudget(4, 4, 65_536, 30_000),
                ).run(
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    assignment_id=committed.assignment.assignment_id,
                    context_digest=context_digest,
                    initial_messages=({"role": "user", "content": "mutate"},),
                )
                record_native_run_result(
                    host,
                    committed,
                    result,
                    times=NativeRunTimes(35_000, 35_010),
                )

            with HostStorage(directory) as storage:
                fresh = HarnessHost(storage, clock_ms=clock)
                self.assertEqual(
                    operator_handoff(storage, TASK_ID).next_admissible,
                    ("propose-completion-from-current-harness-run",),
                )
                recorded = fresh.load_current_run(TASK_ID)
                attempt = fresh.load_attempt(TASK_ID)
                with self.assertRaisesRegex(
                    HarnessLifecycleError, "effectful recorded native Run"
                ):
                    fresh.assign(
                        attempt,
                        manifest=ordivon_harness_manifest(),
                        context_object_digest=(
                            recorded.assignment.assignment.context_object_digest
                        ),
                        tool_catalog_digest=(
                            recorded.assignment.assignment.tool_catalog_digest
                        ),
                        workspace_ref="workspace:oh5:g1",
                        source_ref="repository:ordivon-host@fixture",
                        source_digest=canonical_digest({"revision": "fixture"}),
                        required_capabilities=("tool_events",),
                        budget={"maxModelCalls": 4, "maxToolCalls": 4},
                        tool_grant=_grant("mutation"),
                    )

    def test_recorded_provider_timeout_is_reloadable_on_same_workspace_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(6_000).__next__
            runtime = _RecoveryRuntime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                host, committed, context_digest, catalog_digest = _assign(
                    storage, clock, runtime
                )
                assert committed.native_run_contract is not None
                result = OrdivonAgentLoop(
                    _FailingAdapter(AgentTurnFailureCode.TIMEOUT),
                    _NoopBridge(catalog_digest),
                    budget=RunBudget(2, 2, 65_536, 30_000),
                ).run(
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    assignment_id=committed.assignment.assignment_id,
                    context_digest=context_digest,
                    initial_messages=({"role": "user", "content": "test"},),
                )
                record_native_run_result(
                    host,
                    committed,
                    result,
                    times=NativeRunTimes(40_000, 40_010),
                )
            with HostStorage(directory) as storage:
                fresh = HarnessHost(storage, clock_ms=clock)
                recorded = fresh.load_current_run(TASK_ID)
                self.assertEqual(recorded.receipt.termination_code, "provider_timeout")
                attempt = fresh.load_attempt(TASK_ID)
                with self.assertRaisesRegex(
                    HarnessLifecycleError, "retain the same Workspace"
                ):
                    fresh.assign(
                        attempt,
                        manifest=ordivon_harness_manifest(),
                        context_object_digest=context_digest,
                        tool_catalog_digest=catalog_digest,
                        workspace_ref="workspace:oh5:g2",
                        tool_grant=_grant(),
                    )
                replacement = fresh.assign(
                    attempt,
                    manifest=ordivon_harness_manifest(),
                    context_object_digest=context_digest,
                    tool_catalog_digest=catalog_digest,
                    workspace_ref="workspace:oh5:g1",
                    source_ref="repository:ordivon-host@fixture",
                    source_digest=canonical_digest({"revision": "fixture"}),
                    required_capabilities=("tool_events",),
                    budget={"maxModelCalls": 4, "maxToolCalls": 4},
                    tool_grant=_grant(),
                )
                self.assertEqual(replacement.assignment.generation, 2)


if __name__ == "__main__":
    unittest.main()
