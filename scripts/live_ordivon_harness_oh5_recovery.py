#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from anc_canonical import JsonValue, canonical_digest

from ordivon_host import (
    EventKind,
    HarnessHost,
    HarnessSuperseded,
    HostKernel,
    HostStorage,
    StateRef,
    TaskContract,
    TaskState,
    ToolGrant,
)
from ordivon_host.cognition import BlockKind, CompiledContext, ContextBlock, Freshness
from ordivon_host.harness import NativeRunRecoveryController
from ordivon_host.harness.ordivon import (
    DEFAULT_DEEPSEEK_SECRET_PATH,
    AgentRunConclusion,
    AgentToolCall,
    AgentTurnResult,
    DeepSeekSettings,
    DeepSeekTurnAdapter,
    HarnessContextCompiler,
    HarnessContextRequest,
    NativeRunTimes,
    OrdivonAgentLoop,
    OrdivonInputCompiler,
    RunBudget,
    RuntimeToolBridge,
    ScriptedTurnAdapter,
    discover_harness_runtime_catalog,
    harness_context_object_digest,
    ordivon_harness_manifest,
    record_native_run_result,
)
from ordivon_host.runtime import McpRuntimeClient

TASK_ID = "task:oh5-live-recovery"
GOAL_ID = "goal:oh5-live-recovery"
FRONTIER = "node:oh5-live-recovery:work"
EXPECTED_HEADING = "# Ordivon Host"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise OH5 read-only process-loss recovery, safe abandonment, stale-result "
            "rejection, replacement, and live DeepSeek completion."
        )
    )
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path("/root/projects/ordivon-host"),
    )
    parser.add_argument("--source-revision")
    parser.add_argument(
        "--runtime-endpoint",
        default=os.environ.get("ORDIVON_RUNTIME_ENDPOINT"),
    )
    parser.add_argument(
        "--deepseek-secret",
        type=Path,
        default=DEFAULT_DEEPSEEK_SECRET_PATH,
    )
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    return parser.parse_args()


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _runtime_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit
    bind = os.environ.get("ORDIVON_BIND", "127.0.0.1:8897")
    return f"http://127.0.0.1:{bind.rsplit(':', 1)[-1]}/mcp"


def _git_revision(repo: Path, revision: str | None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", revision or "main"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = completed.stdout.strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"Git returned an invalid revision: {value!r}")
    return value


def _object_count(root: Path) -> int:
    return len(tuple((root / "objects").glob("*.json")))


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped
    return None


def _create_task(storage: HostStorage) -> None:
    HostKernel(
        storage,
        clock_ms=_clock_ms,
        owner_id="host:oh5-live-create",
    ).create_task(
        event_id="event:oh5-live-recovery:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "ordivon-native-harness-oh5-live-recovery"},
        frontier=(FRONTIER,),
    )


def _task_contract(source_revision: str, source_digest: str) -> TaskContract:
    return TaskContract(
        contract_id="task-contract:oh5-live-recovery:v1",
        task_id=TASK_ID,
        objective={
            "summary": (
                "Read README.md through Ordivon Runtime and report its exact first Markdown "
                "heading after recovering one lost read-only Harness process."
            ),
            "target": {
                "kind": "repository-file",
                "relativePath": "README.md",
                "sourceRevision": source_revision,
            },
        },
        acceptance_criteria={
            "checks": [
                {"kind": "safe-read-only-run-abandonment", "generation": 1},
                {"kind": "replacement-generation", "generation": 2},
                {
                    "kind": "first-markdown-heading",
                    "expected": EXPECTED_HEADING,
                },
            ]
        },
        constraints=(
            "Both Harness generations are read-only.",
            "The first generation must not write a HarnessRunReceipt.",
            "The first Workspace must be confirmed closed before abandonment.",
            "The stale first-generation result must not be accepted after abandonment.",
        ),
        resource_refs=(
            StateRef(
                ref=f"repository:ordivon-host@{source_revision}",
                digest=source_digest,
            ),
        ),
        consequence_policy_ref="policy:read-only-workspace-v1",
    )


def _context_request(contract: TaskContract, source_revision: str) -> HarnessContextRequest:
    return HarnessContextRequest(
        task_contract=contract,
        blocks=(
            ContextBlock(
                block_id="context-block:oh5-live:readme",
                kind=BlockKind.TASK,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source_digest=canonical_digest(
                    {
                        "sourceRevision": source_revision,
                        "relativePath": "README.md",
                    }
                ),
                payload={
                    "relativePath": "README.md",
                    "readMode": "FULL",
                    "maxBytes": 65_536,
                },
            ),
        ),
    )


def _read_only_grant(generation: int) -> ToolGrant:
    return ToolGrant(
        tool_grant_id=f"tool-grant:oh5-live-recovery:g{generation}:read-only",
        allowed_tools=("read_workspace",),
        read_path_rules=("README.md",),
    )


def _open_workspace(
    runtime: McpRuntimeClient,
    source_repo: Path,
    source_revision: str,
    workspace_id: str,
) -> None:
    value = runtime.call_tool(
        "workspace.open",
        {
            "schemaVersion": 1,
            "sourceRepo": str(source_repo),
            "sourceRevision": source_revision,
            "workspaceId": workspace_id,
        },
    )
    if value.get("workspaceId") != workspace_id:
        raise RuntimeError("Runtime opened another Workspace identity")


def _lost_generation_adapter() -> ScriptedTurnAdapter:
    return ScriptedTurnAdapter(
        (
            AgentTurnResult(
                model_call_id="model-call:oh5-live-lost:1",
                model_id="ordivon.scripted-model.v1",
                content=None,
                tool_calls=(
                    AgentToolCall(
                        "tool-call:oh5-live-lost:read",
                        "read_workspace",
                        {"relativePath": "README.md", "maxBytes": 65_536},
                    ),
                ),
                conclusion=None,
                usage={"inputTokens": 1, "outputTokens": 1},
                finish_reason="tool_calls",
                raw_response_digest=canonical_digest({"lostGeneration": 1}),
            ),
            AgentTurnResult(
                model_call_id="model-call:oh5-live-lost:2",
                model_id="ordivon.scripted-model.v1",
                content=None,
                tool_calls=(),
                conclusion=AgentRunConclusion(
                    status="candidate_completed",
                    summary="Lost read-only generation observed the README heading.",
                ),
                usage={"inputTokens": 1, "outputTokens": 1},
                finish_reason="tool_calls",
                raw_response_digest=canonical_digest({"lostGeneration": 2}),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class LiveOH5Evidence:
    source_revision: str
    runtime_catalog_digest: str
    task_contract_digest: str
    context_object_digest: str
    generation_one_assignment_id: str
    generation_one_run_id: str
    generation_one_tool_calls: int
    recovery_assessment_digest: str
    recovery_assessment_object_digest: str
    recovery_workspace_status: str
    abandonment_digest: str
    abandonment_object_digest: str
    stale_result_rejected: bool
    stale_result_object_count_unchanged: bool
    generation_two_assignment_id: str
    generation_two_run_id: str
    generation_two_receipt_digest: str
    generation_two_trace_digest: str
    generation_two_model_calls: int
    generation_two_tool_calls: int
    completion_verification_digest: str
    completion_decision_digest: str
    outcome_digest: str
    observed_heading: str | None
    final_task_revision: int
    final_task_state: str
    accepted: bool
    usage: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-oh5-live-recovery-evidence",
            "sourceRevision": self.source_revision,
            "runtimeCatalogDigest": self.runtime_catalog_digest,
            "taskContractDigest": self.task_contract_digest,
            "contextObjectDigest": self.context_object_digest,
            "generationOneAssignmentId": self.generation_one_assignment_id,
            "generationOneRunId": self.generation_one_run_id,
            "generationOneToolCalls": self.generation_one_tool_calls,
            "recoveryAssessmentDigest": self.recovery_assessment_digest,
            "recoveryAssessmentObjectDigest": self.recovery_assessment_object_digest,
            "recoveryWorkspaceStatus": self.recovery_workspace_status,
            "abandonmentDigest": self.abandonment_digest,
            "abandonmentObjectDigest": self.abandonment_object_digest,
            "staleResultRejected": self.stale_result_rejected,
            "staleResultObjectCountUnchanged": self.stale_result_object_count_unchanged,
            "generationTwoAssignmentId": self.generation_two_assignment_id,
            "generationTwoRunId": self.generation_two_run_id,
            "generationTwoReceiptDigest": self.generation_two_receipt_digest,
            "generationTwoTraceDigest": self.generation_two_trace_digest,
            "generationTwoModelCalls": self.generation_two_model_calls,
            "generationTwoToolCalls": self.generation_two_tool_calls,
            "completionVerificationDigest": self.completion_verification_digest,
            "completionDecisionDigest": self.completion_decision_digest,
            "outcomeDigest": self.outcome_digest,
            "observedHeading": self.observed_heading,
            "expectedHeading": EXPECTED_HEADING,
            "finalTaskRevision": self.final_task_revision,
            "finalTaskState": self.final_task_state,
            "accepted": self.accepted,
            "usage": self.usage,
        }


def _write_evidence(path: Path, evidence: LiveOH5Evidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run(args: argparse.Namespace, state_root: Path) -> LiveOH5Evidence:
    source_repo = args.source_repo.expanduser().resolve()
    source_revision = _git_revision(source_repo, args.source_revision)
    source_digest = canonical_digest(
        {"sourceRepo": str(source_repo), "sourceRevision": source_revision}
    )
    token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not token:
        raise RuntimeError("ORDIVON_BEARER_TOKEN is not set")
    runtime = McpRuntimeClient(
        _runtime_endpoint(args.runtime_endpoint),
        token,
        client_name="ordivon-harness-oh5-live",
        client_version="0.1.0",
    )
    runtime.initialize()
    catalog = discover_harness_runtime_catalog(runtime)
    contract = _task_contract(source_revision, source_digest)
    workspace_one = "ordivon-harness-oh5-live-g1"
    workspace_two = "ordivon-harness-oh5-live-g2"
    _open_workspace(runtime, source_repo, source_revision, workspace_one)

    with HostStorage(state_root) as storage:
        _create_task(storage)
        host = HarnessHost(storage, clock_ms=_clock_ms)
        attempt = host.start_attempt(TASK_ID, task_contract=contract)
        context = HarnessContextCompiler().compile(
            attempt.descriptor,
            _context_request(contract, source_revision),
            token_budget=8_000,
        )
        context_object = storage.put_object(context.to_dict(), kind="compiled-context")
        if context_object.digest != harness_context_object_digest(context):
            raise RuntimeError("CompiledContext CAS identity differs")
        generation_one = host.assign(
            attempt,
            manifest=ordivon_harness_manifest(),
            context_object_digest=context_object.digest,
            tool_catalog_digest=catalog.digest,
            workspace_ref=workspace_one,
            source_ref=f"repository:ordivon-host@{source_revision}",
            source_digest=source_digest,
            required_capabilities=("tool_events", "usage"),
            budget={"maxModelCalls": 4, "maxToolCalls": 4},
            tool_grant=_read_only_grant(1),
        )
        assert generation_one.native_run_contract is not None
        compiled_one = OrdivonInputCompiler().compile(generation_one, context)
        lost_result = OrdivonAgentLoop(
            _lost_generation_adapter(),
            RuntimeToolBridge(
                generation_one,
                harness_run_id=generation_one.native_run_contract.harness_run_id,
                runtime=runtime,
            ),
            budget=RunBudget(4, 4, 262_144, 120_000),
        ).run(
            harness_run_id=compiled_one.harness_run_id,
            assignment_id=generation_one.assignment.assignment_id,
            context_digest=generation_one.assignment.context_object_digest,
            initial_messages=compiled_one.initial_messages,
        )
        if not lost_result.candidate_completed or lost_result.tool_calls != 1:
            raise RuntimeError("generation one did not exercise the intended lost read path")

    with HostStorage(state_root) as storage:
        host = HarnessHost(storage, clock_ms=_clock_ms)
        recovered = NativeRunRecoveryController(host, runtime).recover(
            TASK_ID,
            trigger="process_lost",
        )
        if recovered.abandonment is None:
            raise RuntimeError("read-only generation was not safely abandoned")
        objects_before = _object_count(state_root)
        stale_result_rejected = False
        try:
            record_native_run_result(
                host,
                generation_one,
                lost_result,
                times=NativeRunTimes(_clock_ms(), _clock_ms()),
            )
        except HarnessSuperseded:
            stale_result_rejected = True
        objects_after = _object_count(state_root)
        stale_object_count_unchanged = objects_before == objects_after
        if not stale_result_rejected or not stale_object_count_unchanged:
            raise RuntimeError("stale generation-one result was not rejected before CAS writes")
        attempt = host.load_attempt(TASK_ID)
        retained_context = storage.objects.get(
            generation_one.assignment.context_object_digest,
            expected_kind="compiled-context",
        )
        if not isinstance(retained_context, dict):
            raise RuntimeError("retained CompiledContext is invalid")
        context = CompiledContext.from_dict(retained_context)
        _open_workspace(runtime, source_repo, source_revision, workspace_two)
        generation_two = host.assign(
            attempt,
            manifest=ordivon_harness_manifest(),
            context_object_digest=generation_one.assignment.context_object_digest,
            tool_catalog_digest=catalog.digest,
            workspace_ref=workspace_two,
            source_ref=f"repository:ordivon-host@{source_revision}",
            source_digest=source_digest,
            required_capabilities=("tool_events", "usage"),
            budget={"maxModelCalls": 4, "maxToolCalls": 4},
            tool_grant=_read_only_grant(2),
        )
        if generation_two.assignment.generation != 2:
            raise RuntimeError("replacement Assignment did not advance to generation two")
        recovery = recovered.recovery
        abandonment = recovered.abandonment

    try:
        with HostStorage(state_root) as storage:
            host = HarnessHost(storage, clock_ms=_clock_ms)
            generation_two = host.load_current_assignment(TASK_ID)
            context_value = storage.objects.get(
                generation_two.assignment.context_object_digest,
                expected_kind="compiled-context",
            )
            if not isinstance(context_value, dict):
                raise RuntimeError("generation-two Context is invalid")
            context = CompiledContext.from_dict(context_value)
            compiled_two = OrdivonInputCompiler().compile(generation_two, context)
            assert generation_two.native_run_contract is not None
            started = _clock_ms()
            result_two = OrdivonAgentLoop(
                DeepSeekTurnAdapter(
                    DeepSeekSettings.from_secret_file(args.deepseek_secret)
                ),
                RuntimeToolBridge(
                    generation_two,
                    harness_run_id=generation_two.native_run_contract.harness_run_id,
                    runtime=runtime,
                ),
                budget=RunBudget(4, 4, 262_144, 120_000),
            ).run(
                harness_run_id=compiled_two.harness_run_id,
                assignment_id=generation_two.assignment.assignment_id,
                context_digest=generation_two.assignment.context_object_digest,
                initial_messages=compiled_two.initial_messages,
            )
            recorded = record_native_run_result(
                host,
                generation_two,
                result_two,
                times=NativeRunTimes(started, _clock_ms()),
            )

        with HostStorage(state_root) as storage:
            host = HarnessHost(storage, clock_ms=_clock_ms)
            recorded = host.load_current_run(TASK_ID)
            proposed = host.propose_native_completion(recorded)
            observed_heading: str | None = None

            def verify(proposal):
                nonlocal observed_heading
                current = host.load_current_run(proposal.task_id)
                for retained in current.observation_objects:
                    observation = storage.objects.get(
                        retained.digest,
                        expected_kind="harness-tool-observation",
                    )
                    if not isinstance(observation, dict):
                        continue
                    structured = observation.get("structuredContent")
                    if not isinstance(structured, dict):
                        continue
                    content = structured.get("content")
                    if isinstance(content, str):
                        observed_heading = _first_heading(content)
                        if observed_heading is not None:
                            break
                accepted = observed_heading == EXPECTED_HEADING
                return (
                    accepted,
                    None if accepted else "README heading differs",
                    {
                        "method": "oh5-generation-two-observation-v1",
                        "observedHeading": observed_heading,
                        "expectedHeading": EXPECTED_HEADING,
                        "accepted": accepted,
                    },
                )

            decision = host.adjudicate_completion(
                proposed,
                artifact_exists=lambda _: False,
                acceptance_verifier=verify,
                verification_method="oh5-generation-two-observation-v1",
            )
            verification = host.load_completion_verification(TASK_ID)
            projection = storage.journal.get_task(TASK_ID)
            if projection is None or decision.outcome_digest is None:
                raise RuntimeError("final OH5 state is incomplete")
            evidence = LiveOH5Evidence(
                source_revision=source_revision,
                runtime_catalog_digest=catalog.digest,
                task_contract_digest=contract.digest,
                context_object_digest=generation_one.assignment.context_object_digest,
                generation_one_assignment_id=generation_one.assignment.assignment_id,
                generation_one_run_id=generation_one.native_run_contract.harness_run_id,
                generation_one_tool_calls=lost_result.tool_calls,
                recovery_assessment_digest=recovery.assessment.digest,
                recovery_assessment_object_digest=recovery.assessment_object.digest,
                recovery_workspace_status=recovery.assessment.workspace_status,
                abandonment_digest=abandonment.abandonment.digest,
                abandonment_object_digest=abandonment.abandonment_object.digest,
                stale_result_rejected=stale_result_rejected,
                stale_result_object_count_unchanged=stale_object_count_unchanged,
                generation_two_assignment_id=generation_two.assignment.assignment_id,
                generation_two_run_id=generation_two.native_run_contract.harness_run_id,
                generation_two_receipt_digest=recorded.receipt.digest,
                generation_two_trace_digest=recorded.receipt.event_digest,
                generation_two_model_calls=result_two.model_calls,
                generation_two_tool_calls=result_two.tool_calls,
                completion_verification_digest=verification.digest,
                completion_decision_digest=decision.decision.digest,
                outcome_digest=decision.outcome_digest,
                observed_heading=observed_heading,
                final_task_revision=projection.revision,
                final_task_state=projection.state.value,
                accepted=(
                    decision.decision.accepted
                    and projection.state is TaskState.COMPLETED
                    and verification.accepted
                    and observed_heading == EXPECTED_HEADING
                ),
                usage=result_two.usage,
            )

        with HostStorage(state_root) as storage:
            final_host = HarnessHost(storage, clock_ms=_clock_ms)
            final_run = final_host.load_current_run(TASK_ID)
            final_verification = final_host.load_completion_verification(TASK_ID)
            projection = storage.journal.get_task(TASK_ID)
            if (
                projection is None
                or projection.state is not TaskState.COMPLETED
                or not final_verification.accepted
                or final_run.receipt.digest != evidence.generation_two_receipt_digest
            ):
                raise RuntimeError("fresh Host failed final OH5 recovery verification")
        return evidence
    finally:
        try:
            runtime.call_tool(
                "workspace.close",
                {
                    "schemaVersion": 1,
                    "workspaceId": workspace_two,
                    "force": True,
                },
            )
        except Exception:
            pass


def run(args: argparse.Namespace) -> LiveOH5Evidence:
    if args.state_root is not None:
        root = args.state_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise RuntimeError("OH5 live state root must be empty")
        return _run(args, root)
    with tempfile.TemporaryDirectory(prefix="ordivon-oh5-live-") as directory:
        return _run(args, Path(directory))


def main() -> int:
    args = parse_args()
    try:
        evidence = run(args)
        if args.evidence_out is not None:
            _write_evidence(args.evidence_out, evidence)
        print(json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if evidence.accepted else 1
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
