from __future__ import annotations

from dataclasses import replace
import itertools
import tempfile
import unittest
from typing import Any

from anc_canonical import canonical_digest

from ordivon_host import (
    ArtifactRef,
    EventKind,
    GrantedExecutionCheck,
    HarnessHost,
    HarnessRunReceipt,
    HostKernel,
    HostStorage,
    TaskContract,
    TaskState,
    ToolGrant,
)
from ordivon_host.cognition import BlockKind, ContextBlock, Freshness
from ordivon_host.harness import NativeHarnessRunContract
from ordivon_host.harness.ordivon import (
    AgentRunConclusion,
    AgentToolCall,
    AgentTurnResult,
    HarnessContextCompiler,
    HarnessContextRequest,
    NativeRunTimes,
    OrdivonAgentLoop,
    OrdivonInputCompiler,
    RunBudget,
    RuntimeToolBridge,
    ScriptedTurnAdapter,
    ToolBridgeError,
    build_native_run_receipt,
    discover_harness_runtime_catalog,
    ordivon_harness_manifest,
    record_native_run_result,
)

TASK_ID = "task:oh4-native"
GOAL_ID = "goal:oh4-native"
FRONTIER = "node:oh4-native:work"
REQUIRED_RUNTIME_TOOLS = (
    "artifact.read",
    "task.list",
    "task.observe",
    "workspace.diff",
    "workspace.exec",
    "workspace.mutate",
    "workspace.read",
)


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": "test"}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        values: list[dict[str, Any]] = []
        for name in REQUIRED_RUNTIME_TOOLS:
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
                    "execution": (
                        "asynchronous" if name == "workspace.exec" else "synchronous"
                    ),
                }
            )
        return tuple(values)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "workspace.read":
            content = "# Ordivon Host\n\nNative Harness fixture.\n"
            return {"content": content, "digest": canonical_digest(content)}
        if name == "workspace.exec":
            return {
                "jobId": "job:oh4-check",
                "status": "working",
                "artifacts": [
                    {
                        "artifactId": "artifact:oh4-stdout",
                        "kind": "stdout",
                        "digest": canonical_digest("check output"),
                    }
                ],
            }
        if name == "task.observe":
            return {
                "jobId": arguments["jobId"],
                "status": "succeeded",
                "artifacts": [
                    {
                        "artifactId": "artifact:oh4-stdout",
                        "kind": "stdout",
                        "digest": canonical_digest("check output"),
                    }
                ],
            }
        if name == "artifact.read":
            return {
                "content": "check output",
                "digest": canonical_digest("check output"),
            }
        if name == "task.list":
            return {"jobs": [], "nextCursor": None}
        return {"ok": True}


def _create_task(storage: HostStorage, clock) -> None:
    HostKernel(
        storage,
        clock_ms=clock,
        owner_id="host:oh4-task-create",
    ).create_task(
        event_id="event:oh4-native:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "ordivon-native-harness-oh4"},
        frontier=(FRONTIER,),
    )


def _contract() -> TaskContract:
    return TaskContract(
        contract_id="task-contract:oh4-native:v1",
        task_id=TASK_ID,
        objective={
            "summary": "Read README.md and report its exact first Markdown heading.",
            "target": {"kind": "repository-file", "relativePath": "README.md"},
        },
        acceptance_criteria={
            "checks": [
                {
                    "kind": "first-markdown-heading",
                    "relativePath": "README.md",
                    "expected": "# Ordivon Host",
                }
            ]
        },
        constraints=("Do not mutate the Workspace.",),
        consequence_policy_ref="policy:read-only-workspace-v1",
    )


def _grant(*, include_check: bool = False) -> ToolGrant:
    tools = ["read_workspace"]
    checks: tuple[GrantedExecutionCheck, ...] = ()
    if include_check:
        tools.extend(("run_check", "observe_job", "read_artifact"))
        checks = (
            GrantedExecutionCheck(
                check_id="check:oh4-unit-tests",
                executable="/usr/bin/python3",
                args=("-m", "unittest", "discover", "-s", "tests"),
                timeout_ms=120_000,
            ),
        )
    return ToolGrant(
        tool_grant_id=(
            "tool-grant:oh4-native:checks"
            if include_check
            else "tool-grant:oh4-native:read-only"
        ),
        allowed_tools=tuple(tools),
        read_path_rules=("README.md",),
        execution_checks=checks,
    )


def _prepare_native(
    storage: HostStorage,
    clock,
    runtime: _Runtime,
    *,
    grant: ToolGrant | None = None,
):
    host = HarnessHost(storage, clock_ms=clock)
    contract = _contract()
    attempt = host.start_attempt(TASK_ID, task_contract=contract)
    request = HarnessContextRequest(
        task_contract=contract,
        blocks=(
            ContextBlock(
                block_id="context-block:oh4-native:readme",
                kind=BlockKind.TASK,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source_digest=canonical_digest(
                    {"relativePath": "README.md", "sourceRevision": "fixture"}
                ),
                payload={"relativePath": "README.md", "mode": "FULL"},
            ),
        ),
    )
    context = HarnessContextCompiler().compile(
        attempt.descriptor,
        request,
        token_budget=4_000,
    )
    context_object = storage.put_object(context.to_dict(), kind="compiled-context")
    catalog = discover_harness_runtime_catalog(runtime)
    committed = host.assign(
        attempt,
        manifest=ordivon_harness_manifest(),
        context_object_digest=context_object.digest,
        tool_catalog_digest=catalog.digest,
            tool_catalog=catalog,
        workspace_ref="workspace:oh4-native",
        source_ref="repository:ordivon-host@fixture",
        source_digest=canonical_digest({"revision": "fixture"}),
        required_capabilities=("tool_events", "usage"),
        budget={"maxModelCalls": 4, "maxToolCalls": 4},
        tool_grant=grant or _grant(),
    )
    return host, committed, context


def _scripted_adapter() -> ScriptedTurnAdapter:
    return ScriptedTurnAdapter(
        (
            AgentTurnResult(
                model_call_id="model-call:oh4:1",
                model_id="ordivon.scripted-model.v1",
                content=None,
                tool_calls=(
                    AgentToolCall(
                        tool_call_id="tool-call:oh4:readme",
                        name="read_workspace",
                        arguments={"relativePath": "README.md"},
                    ),
                ),
                conclusion=None,
                usage={"inputTokens": 100, "outputTokens": 10},
                finish_reason="tool_calls",
                raw_response_digest=canonical_digest({"response": 1}),
            ),
            AgentTurnResult(
                model_call_id="model-call:oh4:2",
                model_id="ordivon.scripted-model.v1",
                content=None,
                tool_calls=(),
                conclusion=AgentRunConclusion(
                    status="candidate_completed",
                    summary="The observed first heading is # Ordivon Host.",
                    artifact_refs=(),
                    evidence_refs=("invented:model-advisory-ref",),
                    unresolved_unknowns=(),
                ),
                usage={"inputTokens": 200, "outputTokens": 20},
                finish_reason="tool_calls",
                raw_response_digest=canonical_digest({"response": 2}),
            ),
        )
    )


def _run_native(committed, context, runtime: _Runtime):
    assert committed.native_run_contract is not None
    compiled = OrdivonInputCompiler().compile(committed, context)
    loop_clock = itertools.count(20_000).__next__
    result = OrdivonAgentLoop(
        _scripted_adapter(),
        RuntimeToolBridge(
            committed,
            harness_run_id=committed.native_run_contract.harness_run_id,
            runtime=runtime,
        ),
        budget=RunBudget(4, 4, 262_144, 120_000),
        clock_ms=loop_clock,
    ).run(
        harness_run_id=compiled.harness_run_id,
        assignment_id=committed.assignment.assignment_id,
        context_digest=committed.assignment.context_object_digest,
        initial_messages=compiled.initial_messages,
    )
    return result


class OH4ContractModelTests(unittest.TestCase):
    def test_contract_models_round_trip_and_v1_receipt_remains_readable(self) -> None:
        contract = _contract()
        grant = _grant(include_check=True)
        self.assertEqual(TaskContract.from_dict(contract.to_dict()), contract)
        self.assertEqual(ToolGrant.from_dict(grant.to_dict()), grant)
        native = NativeHarnessRunContract(
            harness_run_id="harness-run:oh4-roundtrip",
            assignment_id="assignment:oh4-roundtrip:g1",
            assignment_generation=1,
            assignment_digest=canonical_digest({"assignment": 1}),
            harness_manifest_digest=canonical_digest({"manifest": 1}),
            task_contract_digest=contract.digest,
            task_contract_object_digest=canonical_digest({"contractObject": 1}),
            context_object_digest=canonical_digest({"contextObject": 1}),
            tool_catalog_digest=canonical_digest({"catalog": 1}),
            tool_grant_digest=grant.digest,
            tool_grant_object_digest=canonical_digest({"grantObject": 1}),
            created_at_ms=1,
        )
        self.assertEqual(NativeHarnessRunContract.from_dict(native.to_dict()), native)
        receipt = HarnessRunReceipt(
            harness_run_id=native.harness_run_id,
            assignment_id=native.assignment_id,
            assignment_generation=1,
            harness_id="ordivon-harness-v0",
            harness_revision="oh4",
            manifest_digest=native.harness_manifest_digest,
            session_ref=None,
            started_at_ms=1,
            finished_at_ms=2,
            stop_reason="completed",
            event_digest=canonical_digest({"trace": 1}),
            context_digest=native.context_object_digest,
            tool_catalog_digest=native.tool_catalog_digest,
            runtime_job_refs=(),
            artifact_refs=(),
            usage={},
            termination_code="candidate_completed",
        )
        self.assertEqual(receipt.to_dict()["schemaVersion"], 2)
        self.assertEqual(HarnessRunReceipt.from_dict(receipt.to_dict()), receipt)
        legacy = receipt.to_dict()
        legacy["schemaVersion"] = 1
        del legacy["terminationCode"]
        del legacy["continuationRef"]
        decoded = HarnessRunReceipt.from_dict(legacy)
        self.assertIsNone(decoded.termination_code)
        self.assertIsNone(decoded.continuation_ref)

    def test_native_assignment_reopens_with_exact_contract_grant_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            runtime = _Runtime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _, committed, _ = _prepare_native(storage, clock, runtime)
                assert committed.native_run_contract is not None
                run_id = committed.native_run_contract.harness_run_id
                contract_digest = committed.task_contract.digest
                grant_digest = committed.tool_grant.digest
            with HostStorage(directory) as reopened:
                fresh = HarnessHost(reopened, clock_ms=clock)
                retained = fresh.load_current_assignment(TASK_ID)
                self.assertEqual(retained.task_contract.digest, contract_digest)
                self.assertEqual(retained.tool_grant.digest, grant_digest)
                self.assertEqual(
                    retained.native_run_contract.harness_run_id,
                    run_id,
                )
                self.assertEqual(
                    reopened.journal.get_task(TASK_ID).state,
                    TaskState.WAITING,
                )


class OH4ToolGrantTests(unittest.TestCase):
    def test_tool_grant_filters_tools_paths_checks_jobs_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            runtime = _Runtime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _, committed, _ = _prepare_native(
                    storage,
                    clock,
                    runtime,
                    grant=_grant(include_check=True),
                )
                assert committed.native_run_contract is not None
                bridge = RuntimeToolBridge(
                    committed,
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    runtime=runtime,
                )
                self.assertEqual(
                    tuple(tool.name for tool in bridge.definitions()),
                    ("read_workspace", "run_check", "observe_job", "read_artifact"),
                )
                calls_before = len(runtime.calls)
                with self.assertRaises(ToolBridgeError):
                    bridge.execute(
                        AgentToolCall(
                            "tool-call:oh4:path-escape",
                            "read_workspace",
                            {"relativePath": "../secret"},
                        ),
                        step_id="turn-1-tool-1",
                    )
                self.assertEqual(len(runtime.calls), calls_before)
                with self.assertRaisesRegex(ToolBridgeError, "not granted"):
                    bridge.execute(
                        AgentToolCall(
                            "tool-call:oh4:opaque",
                            "run_in_workspace",
                            {"executable": "/usr/bin/true"},
                        ),
                        step_id="turn-1-tool-2",
                    )
                observation = bridge.execute(
                    AgentToolCall(
                        "tool-call:oh4:check",
                        "run_check",
                        {"checkId": "check:oh4-unit-tests"},
                    ),
                    step_id="turn-1-tool-3",
                )
                self.assertEqual(observation.runtime_job_ref, "job:oh4-check")
                operation, request = runtime.calls[-1]
                self.assertEqual(operation, "workspace.exec")
                self.assertEqual(
                    request["execution"]["executable"],
                    "/usr/bin/python3",
                )
                self.assertEqual(
                    [item["type"] for item in request["execution"]["foreignReferences"]],
                    [
                        "assignment",
                        "harness_run",
                        "native_run_contract",
                        "task",
                        "task_attempt",
                        "task_contract",
                        "tool_grant",
                    ],
                )
                bridge.execute(
                    AgentToolCall(
                        "tool-call:oh4:observe",
                        "observe_job",
                        {"jobId": "job:oh4-check"},
                    ),
                    step_id="turn-2-tool-1",
                )
                bridge.execute(
                    AgentToolCall(
                        "tool-call:oh4:artifact",
                        "read_artifact",
                        {
                            "jobId": "job:oh4-check",
                            "artifactId": "artifact:oh4-stdout",
                        },
                    ),
                    step_id="turn-2-tool-2",
                )
                with self.assertRaisesRegex(ToolBridgeError, "created by this Run"):
                    bridge.execute(
                        AgentToolCall(
                            "tool-call:oh4:foreign-job",
                            "observe_job",
                            {"jobId": "job:foreign"},
                        ),
                        step_id="turn-3-tool-1",
                    )
                with self.assertRaisesRegex(ToolBridgeError, "observed in this Run"):
                    bridge.execute(
                        AgentToolCall(
                            "tool-call:oh4:foreign-artifact",
                            "read_artifact",
                            {
                                "jobId": "job:foreign",
                                "artifactId": "artifact:foreign",
                            },
                        ),
                        step_id="turn-3-tool-2",
                    )


class OH4NativeLifecycleTests(unittest.TestCase):
    def test_missing_host_object_evidence_is_a_rejection_not_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_500).__next__
            runtime = _Runtime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                host, committed, context = _prepare_native(storage, clock, runtime)
                result = _run_native(committed, context, runtime)
                recorded = record_native_run_result(
                    host,
                    committed,
                    result,
                    times=NativeRunTimes(20_000, 20_010),
                )
                missing_digest = canonical_digest({"missing": "host evidence"})
                proposed = host.propose_completion(
                    recorded,
                    summary="Missing Host evidence fixture.",
                    acceptance_results={},
                    evidence_refs=(
                        ArtifactRef(
                            ref=f"host-object:{missing_digest}",
                            kind="harness-trace",
                            digest=missing_digest,
                        ),
                    ),
                )
                decision = host.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda _: True,
                    acceptance_verifier=lambda _: (
                        True,
                        None,
                        {"accepted": True},
                    ),
                )
                self.assertFalse(decision.decision.accepted)
                self.assertEqual(
                    decision.decision.reason_code,
                    "missing_artifact",
                )

    def test_trace_observation_verification_and_outcome_survive_fresh_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            runtime = _Runtime()
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                host, committed, context = _prepare_native(storage, clock, runtime)
                result = _run_native(committed, context, runtime)
                receipt = build_native_run_receipt(
                    committed,
                    result,
                    times=NativeRunTimes(20_000, 20_010),
                )
                tampered = replace(receipt, runtime_job_refs=("job:invented",))
                with self.assertRaisesRegex(ValueError, "not derived"):
                    host.record_run(
                        committed,
                        tampered,
                        trace=result.trace.to_dict(),
                        observations=tuple(
                            item.to_dict() for item in result.observations
                        ),
                        conclusion=result.conclusion.to_dict(),
                    )
                recorded = record_native_run_result(
                    host,
                    committed,
                    result,
                    times=NativeRunTimes(20_000, 20_010),
                )
                self.assertIsNotNone(recorded.trace_object)
                self.assertEqual(len(recorded.observation_objects), 1)

            with HostStorage(directory) as reopened:
                fresh = HarnessHost(reopened, clock_ms=clock)
                recovered = fresh.load_current_run(TASK_ID)
                self.assertEqual(
                    recovered.receipt.termination_code,
                    "candidate_completed",
                )
                proposed = fresh.propose_native_completion(recovered)
                self.assertTrue(
                    all(
                        item.ref.startswith("host-object:")
                        for item in proposed.proposal.evidence_refs
                    )
                )
                self.assertNotIn(
                    "invented:model-advisory-ref",
                    {item.ref for item in proposed.proposal.evidence_refs},
                )

                def verify(proposal):
                    current = fresh.load_current_run(proposal.task_id)
                    headings: list[str] = []
                    for item in current.observation_objects:
                        observation = reopened.objects.get(
                            item.digest,
                            expected_kind="harness-tool-observation",
                        )
                        content = observation["structuredContent"].get("content")
                        if isinstance(content, str):
                            headings.extend(
                                line.strip()
                                for line in content.splitlines()
                                if line.strip().startswith("# ")
                            )
                    accepted = headings[:1] == ["# Ordivon Host"]
                    return (
                        accepted,
                        None if accepted else "README heading differs",
                        {
                            "method": "persisted-observation-heading-v1",
                            "observedHeadings": headings,
                            "accepted": accepted,
                        },
                    )

                decision = fresh.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda _: False,
                    acceptance_verifier=verify,
                    verification_method="persisted-observation-heading-v1",
                )
                self.assertTrue(decision.decision.accepted)
                self.assertEqual(decision.task_state, TaskState.COMPLETED.value)
                verification = fresh.load_completion_verification(TASK_ID)
                self.assertTrue(verification.accepted)
                self.assertEqual(
                    decision.decision.verification_digest,
                    verification.digest,
                )
                decision_digest = decision.decision.digest

            with HostStorage(directory) as reopened_again:
                final_host = HarnessHost(reopened_again, clock_ms=clock)
                verification = final_host.load_completion_verification(TASK_ID)
                self.assertTrue(verification.accepted)
                proposal = final_host.load_proposed_completion(TASK_ID)
                replay = final_host.adjudicate_completion(
                    proposal,
                    artifact_exists=lambda _: False,
                    acceptance_verifier=lambda _: (
                        False,
                        "must not rerun",
                        {"accepted": False},
                    ),
                )
                self.assertEqual(replay.decision.digest, decision_digest)
                self.assertEqual(
                    reopened_again.journal.get_task(TASK_ID).state,
                    TaskState.COMPLETED,
                )


if __name__ == "__main__":
    unittest.main()
