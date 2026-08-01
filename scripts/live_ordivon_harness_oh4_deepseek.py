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
    HostKernel,
    HostStorage,
    StateRef,
    TaskContract,
    TaskState,
    ToolGrant,
)
from ordivon_host.cognition import (
    BlockKind,
    CompiledContext,
    ContextBlock,
    Freshness,
)
from ordivon_host.harness.ordivon import (
    DEFAULT_DEEPSEEK_SECRET_PATH,
    DeepSeekSettings,
    DeepSeekTurnAdapter,
    HarnessContextCompiler,
    HarnessContextRequest,
    NativeRunTimes,
    OrdivonAgentLoop,
    OrdivonInputCompiler,
    RunBudget,
    RuntimeToolBridge,
    discover_harness_runtime_catalog,
    harness_context_object_digest,
    ordivon_harness_manifest,
    record_native_run_result,
)
from ordivon_host.runtime import McpRuntimeClient

TASK_ID = "task:oh4-live-readme"
GOAL_ID = "goal:oh4-live-readme"
FRONTIER = "node:oh4-live-readme:work"
EXPECTED_HEADING = "# Ordivon Host"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the OH4 Host-integrated DeepSeek native Harness dogfood against "
            "Ordivon Runtime."
        )
    )
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path("/root/projects/ordivon-host"),
    )
    parser.add_argument(
        "--source-revision",
        help="Git revision to open; defaults to source repository main",
    )
    parser.add_argument(
        "--runtime-endpoint",
        default=os.environ.get("ORDIVON_RUNTIME_ENDPOINT"),
    )
    parser.add_argument(
        "--deepseek-secret",
        type=Path,
        default=DEFAULT_DEEPSEEK_SECRET_PATH,
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        help="Optional Host state root to retain after the run",
    )
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--max-model-calls", type=int, default=4)
    parser.add_argument("--max-tool-calls", type=int, default=4)
    return parser.parse_args()


def _runtime_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit
    bind = os.environ.get("ORDIVON_BIND", "127.0.0.1:8897")
    port = bind.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}/mcp"


def _git_revision(repo: Path, revision: str | None) -> str:
    target = revision or "main"
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", target],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = completed.stdout.strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"Git returned an invalid revision: {value!r}")
    return value


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


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
        owner_id="host:oh4-live-task-create",
    ).create_task(
        event_id="event:oh4-live-readme:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "ordivon-native-harness-oh4-live-readme"},
        frontier=(FRONTIER,),
    )


def _task_contract(source_revision: str, source_digest: str) -> TaskContract:
    return TaskContract(
        contract_id="task-contract:oh4-live-readme:v1",
        task_id=TASK_ID,
        objective={
            "summary": (
                "Read README.md through Ordivon Runtime and report its exact first Markdown "
                "heading. Do not rely on prior knowledge."
            ),
            "target": {
                "kind": "repository-file",
                "relativePath": "README.md",
                "sourceRevision": source_revision,
            },
        },
        acceptance_criteria={
            "checks": [
                {
                    "kind": "runtime-observation-exists",
                    "tool": "read_workspace",
                    "relativePath": "README.md",
                },
                {
                    "kind": "first-markdown-heading",
                    "expected": EXPECTED_HEADING,
                },
            ]
        },
        constraints=(
            "Do not mutate the Workspace.",
            "Use read_workspace before submitting candidate_completed.",
            "Do not invent Artifact, evidence, Job, Tool Call, or completion identities.",
        ),
        resource_refs=(
            StateRef(
                ref=f"repository:ordivon-host@{source_revision}",
                digest=source_digest,
            ),
        ),
        consequence_policy_ref="policy:read-only-workspace-v1",
    )


@dataclass(frozen=True, slots=True)
class LiveRunEvidence:
    source_revision: str
    runtime_catalog_digest: str
    task_contract_digest: str
    task_contract_object_digest: str
    context_object_digest: str
    tool_grant_digest: str
    tool_grant_object_digest: str
    assignment_id: str
    assignment_digest: str
    native_run_contract_digest: str
    native_run_contract_object_digest: str
    harness_run_id: str
    run_receipt_digest: str
    run_receipt_object_digest: str
    termination_code: str
    trace_digest: str
    trace_object_digest: str
    observation_object_digests: tuple[str, ...]
    completion_proposal_digest: str
    completion_verification_digest: str
    completion_verification_object_digest: str
    completion_decision_digest: str
    outcome_digest: str
    final_task_revision: int
    model_calls: int
    tool_calls: int
    observed_heading: str | None
    accepted: bool
    usage: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-oh4-live-evidence",
            "sourceRevision": self.source_revision,
            "runtimeCatalogDigest": self.runtime_catalog_digest,
            "taskContractDigest": self.task_contract_digest,
            "taskContractObjectDigest": self.task_contract_object_digest,
            "contextObjectDigest": self.context_object_digest,
            "toolGrantDigest": self.tool_grant_digest,
            "toolGrantObjectDigest": self.tool_grant_object_digest,
            "assignmentId": self.assignment_id,
            "assignmentDigest": self.assignment_digest,
            "nativeRunContractDigest": self.native_run_contract_digest,
            "nativeRunContractObjectDigest": self.native_run_contract_object_digest,
            "harnessRunId": self.harness_run_id,
            "runReceiptDigest": self.run_receipt_digest,
            "runReceiptObjectDigest": self.run_receipt_object_digest,
            "terminationCode": self.termination_code,
            "traceDigest": self.trace_digest,
            "traceObjectDigest": self.trace_object_digest,
            "observationObjectDigests": list(self.observation_object_digests),
            "completionProposalDigest": self.completion_proposal_digest,
            "completionVerificationDigest": self.completion_verification_digest,
            "completionVerificationObjectDigest": (
                self.completion_verification_object_digest
            ),
            "completionDecisionDigest": self.completion_decision_digest,
            "outcomeDigest": self.outcome_digest,
            "finalTaskRevision": self.final_task_revision,
            "modelCalls": self.model_calls,
            "toolCalls": self.tool_calls,
            "observedHeading": self.observed_heading,
            "expectedHeading": EXPECTED_HEADING,
            "accepted": self.accepted,
            "usage": self.usage,
        }


def _write_evidence(path: Path, evidence: LiveRunEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run_with_state_root(
    args: argparse.Namespace,
    *,
    state_root: Path,
) -> LiveRunEvidence:
    source_repo = args.source_repo.expanduser().resolve()
    source_revision = _git_revision(source_repo, args.source_revision)
    source_digest = canonical_digest(
        {"sourceRepo": str(source_repo), "sourceRevision": source_revision}
    )
    runtime_token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not runtime_token:
        raise RuntimeError("ORDIVON_BEARER_TOKEN is not set")
    runtime = McpRuntimeClient(
        _runtime_endpoint(args.runtime_endpoint),
        runtime_token,
        client_name="ordivon-harness-oh4-live",
        client_version="0.1.0",
    )
    runtime.initialize()
    opened = runtime.call_tool(
        "workspace.open",
        {
            "schemaVersion": 1,
            "sourceRepo": str(source_repo),
            "sourceRevision": source_revision,
        },
    )
    workspace_id = opened.get("workspaceId")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise RuntimeError("workspace.open omitted Workspace identity")

    try:
        catalog = discover_harness_runtime_catalog(runtime)
        contract = _task_contract(source_revision, source_digest)
        grant = ToolGrant(
            tool_grant_id="tool-grant:oh4-live-readme:read-only",
            allowed_tools=("read_workspace",),
            read_path_rules=("README.md",),
        )
        with HostStorage(state_root) as storage:
            _create_task(storage)
            host = HarnessHost(storage, clock_ms=_clock_ms)
            attempt = host.start_attempt(TASK_ID, task_contract=contract)
            request = HarnessContextRequest(
                task_contract=contract,
                blocks=(
                    ContextBlock(
                        block_id="context-block:oh4-live:readme",
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
            context = HarnessContextCompiler().compile(
                attempt.descriptor,
                request,
                token_budget=8_000,
            )
            context_object = storage.put_object(
                context.to_dict(), kind="compiled-context"
            )
            if context_object.digest != harness_context_object_digest(context):
                raise RuntimeError("Host CAS Context identity differs from compiler identity")
            host.assign(
                attempt,
                manifest=ordivon_harness_manifest(),
                context_object_digest=context_object.digest,
                tool_catalog_digest=catalog.digest,
            tool_catalog=catalog,
                workspace_ref=workspace_id,
                source_ref=f"repository:ordivon-host@{source_revision}",
                source_digest=source_digest,
                required_capabilities=("tool_events", "usage"),
                budget={
                    "maxModelCalls": args.max_model_calls,
                    "maxToolCalls": args.max_tool_calls,
                    "maxObservationBytes": 262_144,
                },
                tool_grant=grant,
            )

        with HostStorage(state_root) as storage:
            host = HarnessHost(storage, clock_ms=_clock_ms)
            committed = host.load_current_assignment(TASK_ID)
            context_value = storage.objects.get(
                committed.assignment.context_object_digest,
                expected_kind="compiled-context",
            )
            if not isinstance(context_value, dict):
                raise RuntimeError("persisted CompiledContext is not an object")
            context = CompiledContext.from_dict(context_value)
            compiled_input = OrdivonInputCompiler().compile(committed, context)
            assert committed.native_run_contract is not None
            started_at_ms = _clock_ms()
            result = OrdivonAgentLoop(
                DeepSeekTurnAdapter(
                    DeepSeekSettings.from_secret_file(args.deepseek_secret)
                ),
                RuntimeToolBridge(
                    committed,
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    runtime=runtime,
                ),
                budget=RunBudget(
                    args.max_model_calls,
                    args.max_tool_calls,
                    262_144,
                    120_000,
                ),
            ).run(
                harness_run_id=compiled_input.harness_run_id,
                assignment_id=committed.assignment.assignment_id,
                context_digest=committed.assignment.context_object_digest,
                initial_messages=compiled_input.initial_messages,
            )
            recorded = record_native_run_result(
                host,
                committed,
                result,
                times=NativeRunTimes(started_at_ms, _clock_ms()),
            )

        with HostStorage(state_root) as storage:
            host = HarnessHost(storage, clock_ms=_clock_ms)
            recorded = host.load_current_run(TASK_ID)
            proposed = host.propose_native_completion(recorded)
            observed_heading: str | None = None

            def verify(proposal):
                nonlocal observed_heading
                current = host.load_current_run(proposal.task_id)
                for item in current.observation_objects:
                    observation = storage.objects.get(
                        item.digest,
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
                        "method": "persisted-runtime-observation-heading-v1",
                        "observedHeading": observed_heading,
                        "expectedHeading": EXPECTED_HEADING,
                        "accepted": accepted,
                    },
                )

            decision = host.adjudicate_completion(
                proposed,
                artifact_exists=lambda _: False,
                acceptance_verifier=verify,
                verification_method="persisted-runtime-observation-heading-v1",
            )
            verification = host.load_completion_verification(TASK_ID)
            head = storage.read_task_event(TASK_ID)
            if not isinstance(head.data, dict):
                raise RuntimeError("final Host event data is not an object")
            if decision.outcome_digest is None:
                raise RuntimeError("accepted CompletionDecision omitted TaskOutcome")
            trace_object = recorded.trace_object
            native = recorded.assignment.native_run_contract
            task_contract_object = recorded.assignment.task_contract_object
            grant_object = recorded.assignment.tool_grant_object
            native_object = recorded.assignment.native_run_contract_object
            if any(
                item is None
                for item in (
                    trace_object,
                    native,
                    task_contract_object,
                    grant_object,
                    native_object,
                )
            ):
                raise RuntimeError("native Run retained incomplete Host objects")
            assert trace_object is not None
            assert native is not None
            assert task_contract_object is not None
            assert grant_object is not None
            assert native_object is not None
            verification_object_digest = head.data.get(
                "completionVerificationObjectDigest"
            )
            if not isinstance(verification_object_digest, str):
                raise RuntimeError("final Host state omitted CompletionVerification object")
            evidence = LiveRunEvidence(
                source_revision=source_revision,
                runtime_catalog_digest=catalog.digest,
                task_contract_digest=contract.digest,
                task_contract_object_digest=task_contract_object.digest,
                context_object_digest=recorded.assignment.assignment.context_object_digest,
                tool_grant_digest=grant.digest,
                tool_grant_object_digest=grant_object.digest,
                assignment_id=recorded.assignment.assignment.assignment_id,
                assignment_digest=recorded.assignment.assignment.digest,
                native_run_contract_digest=native.digest,
                native_run_contract_object_digest=native_object.digest,
                harness_run_id=recorded.receipt.harness_run_id,
                run_receipt_digest=recorded.receipt.digest,
                run_receipt_object_digest=recorded.receipt_object.digest,
                termination_code=recorded.receipt.termination_code or "",
                trace_digest=recorded.receipt.event_digest,
                trace_object_digest=trace_object.digest,
                observation_object_digests=tuple(
                    item.digest for item in recorded.observation_objects
                ),
                completion_proposal_digest=proposed.proposal.digest,
                completion_verification_digest=verification.digest,
                completion_verification_object_digest=verification_object_digest,
                completion_decision_digest=decision.decision.digest,
                outcome_digest=decision.outcome_digest,
                final_task_revision=decision.task_revision,
                model_calls=result.model_calls,
                tool_calls=result.tool_calls,
                observed_heading=observed_heading,
                accepted=(
                    decision.decision.accepted
                    and decision.task_state == TaskState.COMPLETED.value
                    and verification.accepted
                    and observed_heading == EXPECTED_HEADING
                ),
                usage=result.usage,
            )

        with HostStorage(state_root) as storage:
            final_host = HarnessHost(storage, clock_ms=_clock_ms)
            final_verification = final_host.load_completion_verification(TASK_ID)
            final_run = final_host.load_current_run(TASK_ID)
            projection = storage.journal.get_task(TASK_ID)
            if (
                projection is None
                or projection.state is not TaskState.COMPLETED
                or not final_verification.accepted
                or final_run.receipt.digest != evidence.run_receipt_digest
            ):
                raise RuntimeError("fresh Host failed final OH4 recovery verification")
        return evidence
    finally:
        runtime.call_tool(
            "workspace.close",
            {"schemaVersion": 1, "workspaceId": workspace_id, "force": True},
        )


def run(args: argparse.Namespace) -> LiveRunEvidence:
    if args.state_root is not None:
        state_root = args.state_root.expanduser().resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        if any(state_root.iterdir()):
            raise RuntimeError("OH4 live state root must be empty")
        return _run_with_state_root(args, state_root=state_root)
    with tempfile.TemporaryDirectory(prefix="ordivon-oh4-live-") as directory:
        return _run_with_state_root(args, state_root=Path(directory))


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
