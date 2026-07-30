#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os

from anc_canonical import JsonValue, canonical_digest

from ordivon_host import (
    ArtifactRef,
    EventKind,
    HarnessCapabilityManifest,
    HarnessHost,
    HarnessRunReceipt,
    HostKernel,
    HostStorage,
    build_harness_workspace_exec_request,
    operator_handoff,
)
from ordivon_host.runtime import (
    RuntimeToolRejected,
    discover_execution_runtime_catalog,
    ensure_workspace,
    ensure_workspace_closed,
)
from ordivon_host.testing import (
    RuntimeClientFactory,
    ScenarioIdentity,
    cleanup_state_root,
    emit_receipt,
    jobs_for_request,
    load_scenario_token,
    scenario_clock_ms,
    scenario_state_root,
    workspace_absent,
)

_TERMINAL = {
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "lost",
    "orphaned",
    "unknown",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emit one real Host H2 workspace.exec request with canonical "
            "ordivon.host references and verify Runtime R2 behavior."
        )
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ORDIVON_MCP_ENDPOINT", "http://127.0.0.1:8897/mcp"),
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def _observe_terminal(client, job_id: str) -> dict[str, JsonValue]:
    for _ in range(10):
        observation = client.call_tool(
            "task.observe",
            {
                "schemaVersion": 1,
                "jobId": job_id,
                "waitMs": 30_000,
                "waitUntil": "terminal",
                "stdoutTailBytes": 16_384,
                "stderrTailBytes": 16_384,
            },
        )
        status = observation.get("status")
        if status in _TERMINAL:
            return observation
    raise AssertionError("Runtime Job did not become terminal within the observation bound")


def _terminal_artifact(observation: dict[str, JsonValue]) -> dict[str, JsonValue]:
    artifacts = observation.get("artifacts")
    if not isinstance(artifacts, list):
        raise AssertionError("Runtime observation omitted Artifacts")
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("kind") == "terminal_evidence"
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Runtime observation retained {len(matches)} terminal-evidence Artifacts"
        )
    return matches[0]


def _read_json_artifact(client, job_id: str, artifact_id: str) -> dict[str, JsonValue]:
    offset = 0
    chunks: list[str] = []
    observed_digest: str | None = None
    for _ in range(16):
        page = client.call_tool(
            "artifact.read",
            {
                "schemaVersion": 1,
                "jobId": job_id,
                "artifactId": artifact_id,
                "offset": offset,
                "maxBytes": 1_048_576,
            },
        )
        content = page.get("content")
        next_offset = page.get("nextOffset")
        digest = page.get("digest")
        eof = page.get("eof")
        if (
            not isinstance(content, str)
            or type(next_offset) is not int
            or not isinstance(digest, str)
            or type(eof) is not bool
        ):
            raise AssertionError("artifact.read returned an invalid page")
        if observed_digest is not None and observed_digest != digest:
            raise AssertionError("artifact.read digest changed between pages")
        observed_digest = digest
        chunks.append(content)
        offset = next_offset
        if eof:
            value = json.loads("".join(chunks))
            if not isinstance(value, dict):
                raise AssertionError("terminal evidence is not an object")
            return value
    raise AssertionError("terminal evidence exceeded the bounded artifact pages")


def _expect_idempotency_conflict(client, request: dict[str, JsonValue]) -> str:
    try:
        client.call_tool("workspace.exec", request)
    except RuntimeToolRejected as error:
        if (
            error.detail.code != "IDEMPOTENCY_CONFLICT"
            or error.detail.commit_state != "not_committed"
        ):
            raise AssertionError(
                f"unexpected Runtime conflict response: {error.detail.raw}"
            ) from error
        return error.detail.code
    raise AssertionError("changed Host binding was admitted under the old request identity")


def main() -> None:
    args = parse_args()
    identity = ScenarioIdentity.create("live-harness-h2")
    token = load_scenario_token()
    state_root = scenario_state_root(
        args.state_root,
        prefix="harness-h2",
        identity=identity,
    )
    factory = RuntimeClientFactory(
        args.endpoint,
        token,
        "ordivon-host-live-harness-h2",
    )
    workspace_id = identity.workspace_id
    task_id = identity.task_id
    goal_id = identity.goal_id
    run_id = f"harness-run:{identity.token}:1"
    completed = False
    try:
        setup_client = factory.client("setup", initialize=True)
        catalog = discover_execution_runtime_catalog(setup_client)
        workspace = ensure_workspace(
            setup_client,
            workspace_id=workspace_id,
            source_repo=args.source_repo,
            source_revision=args.source_revision,
        )
        source_digest = canonical_digest(
            {
                "repository": args.source_repo,
                "sourceRevision": args.source_revision,
            }
        )
        objective_digest = canonical_digest(
            {"objective": "prove Host H2 to Runtime R2 correlation"}
        )
        acceptance_digest = canonical_digest(
            {
                "acceptance": [
                    "exact replay returns one Job",
                    "binding drift conflicts",
                    "terminal evidence retains Host references",
                ]
            }
        )
        with HostStorage(state_root) as storage:
            HostKernel(
                storage,
                clock_ms=scenario_clock_ms,
                owner_id="host:live-harness-h2-task-create",
            ).create_task(
                event_id=f"event:{identity.token}:create",
                kind=EventKind.TASK_CREATED,
                task_id=task_id,
                goal_id=goal_id,
                payload={"workloadId": "harness-h2-runtime-r2-live-v1"},
                frontier=(f"node:{identity.token}:run",),
            )
            context = storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.harness-h2-live-context",
                    "sourceRevision": args.source_revision,
                    "runtimeCatalogDigest": catalog.digest,
                },
                kind="compiled-context",
            )
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            attempt = host.start_attempt(
                task_id,
                objective_digest=objective_digest,
                acceptance_criteria_digest=acceptance_digest,
            )
            assignment = host.assign(
                attempt,
                manifest=HarnessCapabilityManifest(
                    harness_id="harness:host-h2-live-probe",
                    protocol="ordivon.host-h2-probe",
                    protocol_revision="1",
                    persistent_session=True,
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
                ),
                context_object_digest=context.digest,
                tool_catalog_digest=catalog.digest,
                workspace_ref=workspace_id,
                source_ref="repository:ordivon-host",
                source_digest=source_digest,
                required_capabilities=(
                    "persistent_session",
                    "interrupt",
                    "tool_events",
                ),
                budget={"runtimeJobs": 1},
            )
            request = build_harness_workspace_exec_request(
                assignment,
                harness_run_id=run_id,
                step_id="runtime-correlation-probe",
                executable="/usr/bin/python3",
                args=(
                    "-c",
                    (
                        "import json; "
                        "print(json.dumps({'h2': True, 'probe': 'runtime-correlation'}, "
                        "sort_keys=True))"
                    ),
                ),
                wait_ms=30_000,
            )

        delivery_client = factory.client("delivery", initialize=True)
        first = delivery_client.call_tool("workspace.exec", request)
        job_id = first.get("jobId")
        if not isinstance(job_id, str):
            raise AssertionError("workspace.exec omitted Runtime Job identity")
        terminal = first if first.get("status") in _TERMINAL else _observe_terminal(
            delivery_client, job_id
        )
        if terminal.get("status") != "succeeded":
            raise AssertionError(f"H2 Runtime probe failed: {terminal}")

        replay_client = factory.client("exact-replay", initialize=True)
        replay = replay_client.call_tool("workspace.exec", request)
        if replay.get("jobId") != job_id:
            raise AssertionError("exact H2 replay returned another Runtime Job")

        execution = request.get("execution")
        if not isinstance(execution, dict):
            raise AssertionError("H2 request omitted execution")
        references = execution.get("foreignReferences")
        if not isinstance(references, list):
            raise AssertionError("H2 request omitted foreignReferences")

        generation_drift = deepcopy(request)
        drift_execution = generation_drift["execution"]
        assert isinstance(drift_execution, dict)
        drift_references = drift_execution["foreignReferences"]
        assert isinstance(drift_references, list)
        assignment_reference = next(
            item
            for item in drift_references
            if isinstance(item, dict) and item.get("type") == "assignment"
        )
        assignment_reference["generation"] = str(
            int(str(assignment_reference["generation"])) + 1
        )
        generation_conflict = _expect_idempotency_conflict(
            factory.client("generation-conflict", initialize=True),
            generation_drift,
        )

        digest_drift = deepcopy(request)
        digest_execution = digest_drift["execution"]
        assert isinstance(digest_execution, dict)
        digest_references = digest_execution["foreignReferences"]
        assert isinstance(digest_references, list)
        digest_assignment = next(
            item
            for item in digest_references
            if isinstance(item, dict) and item.get("type") == "assignment"
        )
        digest_assignment["digest"] = canonical_digest(
            {"drift": "assignment-digest"}
        )
        digest_conflict = _expect_idempotency_conflict(
            factory.client("digest-conflict", initialize=True),
            digest_drift,
        )

        observation = _observe_terminal(
            factory.client("terminal-observation", initialize=True),
            job_id,
        )
        terminal_artifact = _terminal_artifact(observation)
        artifact_id = terminal_artifact.get("artifactId")
        artifact_digest = terminal_artifact.get("digest")
        if not isinstance(artifact_id, str) or not isinstance(artifact_digest, str):
            raise AssertionError("terminal Artifact identity is invalid")
        terminal_evidence = _read_json_artifact(
            factory.client("artifact-read", initialize=True),
            job_id,
            artifact_id,
        )

        fresh_client = factory.client("fresh-recovery", initialize=True)
        jobs = jobs_for_request(fresh_client, str(request["clientRequestId"]))
        if len(jobs) != 1 or jobs[0].get("jobId") != job_id:
            raise AssertionError("fresh Host client did not recover the original Runtime Job")

        with HostStorage(state_root) as storage:
            recovered_assignment = HarnessHost(
                storage, clock_ms=scenario_clock_ms
            ).load_current_assignment(task_id)
            receipt = HarnessRunReceipt(
                harness_run_id=run_id,
                assignment_id=recovered_assignment.assignment.assignment_id,
                assignment_generation=recovered_assignment.assignment.generation,
                harness_id=recovered_assignment.assignment.target_harness_id,
                harness_revision="host-h2-live-probe-v1",
                manifest_digest=recovered_assignment.assignment.harness_manifest_digest,
                session_ref=f"session:{identity.token}:probe",
                started_at_ms=recovered_assignment.assignment.created_at_ms,
                finished_at_ms=scenario_clock_ms(),
                stop_reason="completed",
                event_digest=canonical_digest(
                    {
                        "runtimeJobId": job_id,
                        "terminalEvidenceDigest": artifact_digest,
                    }
                ),
                context_digest=recovered_assignment.assignment.context_object_digest,
                tool_catalog_digest=recovered_assignment.assignment.tool_catalog_digest,
                runtime_job_refs=(job_id,),
                artifact_refs=(
                    ArtifactRef(
                        ref=f"runtime-artifact:{artifact_id}",
                        kind="terminal_evidence",
                        digest=artifact_digest,
                    ),
                ),
                usage={"runtimeJobs": 1},
            )
            recorded = HarnessHost(
                storage, clock_ms=scenario_clock_ms
            ).record_run(recovered_assignment, receipt)
            handoff = operator_handoff(storage, task_id)

        expected_references = [
            dict(reference) if isinstance(reference, dict) else reference
            for reference in references
        ]
        checks = {
            "oneRuntimeJob": len(jobs) == 1,
            "exactReplayReturnedOriginalJob": replay.get("jobId") == job_id,
            "assignmentGenerationConflict": generation_conflict
            == "IDEMPOTENCY_CONFLICT",
            "assignmentDigestConflict": digest_conflict == "IDEMPOTENCY_CONFLICT",
            "terminalEvidenceRetainedExactReferences": terminal_evidence.get(
                "foreignReferences"
            )
            == expected_references,
            "terminalEvidenceBoundJob": terminal_evidence.get("jobId") == job_id,
            "terminalEvidenceBoundAttempt": isinstance(
                terminal_evidence.get("attemptId"), str
            ),
            "terminalEvidenceBoundWorkspace": terminal_evidence.get("workspaceId")
            == workspace_id,
            "terminalEvidenceBoundSource": terminal_evidence.get("sourceRevision")
            == args.source_revision,
            "noSemanticCompletionClaim": (
                "semanticCompletion" not in terminal_evidence
                and "taskOutcome" not in terminal_evidence
            ),
            "freshClientRecoveredOriginalJob": len(jobs) == 1
            and jobs[0].get("jobId") == job_id,
            "hostRecordedRuntimeJob": recorded.receipt.runtime_job_refs == (job_id,),
            "handoffProjectsHarnessRun": handoff.harness_run_id == run_id,
        }
        if not all(checks.values()):
            raise AssertionError(f"H2/R2 live checks failed: {checks}")

        closed = ensure_workspace_closed(
            factory.client("close", initialize=True),
            workspace_id,
            force=True,
        )
        closed_confirmed = workspace_absent(
            factory.client("close-audit", initialize=True),
            workspace_id,
        )
        if not closed_confirmed:
            raise AssertionError("H2 live Runtime Workspace remained open")

        receipt_value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-h2-runtime-r2-live-receipt",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "implementationSourceRevision": args.source_revision,
            "taskId": task_id,
            "goalId": goal_id,
            "taskAttemptId": assignment.attempt.task_attempt_id,
            "assignmentId": assignment.assignment.assignment_id,
            "assignmentGeneration": assignment.assignment.generation,
            "harnessRunId": run_id,
            "workspace": workspace,
            "runtimeCatalogDigest": catalog.digest,
            "request": request,
            "runtime": {
                "jobId": job_id,
                "attemptId": terminal_evidence.get("attemptId"),
                "firstStatus": first.get("status"),
                "terminalStatus": observation.get("status"),
                "matchingJobs": jobs,
                "terminalArtifact": terminal_artifact,
                "terminalEvidence": terminal_evidence,
                "workspaceClose": closed,
                "workspaceClosed": closed_confirmed,
            },
            "host": {
                "recordedRunDigest": recorded.receipt.digest,
                "recordedRunRevision": recorded.task_revision,
                "handoff": handoff.to_dict(),
            },
            "checks": checks,
            "stateRoot": str(state_root) if args.keep_state else None,
        }
        completed = True
        emit_receipt(receipt_value)
    finally:
        if not completed:
            try:
                ensure_workspace_closed(
                    factory.client("failure-cleanup", initialize=True),
                    workspace_id,
                    force=True,
                )
            except Exception:
                pass
        cleanup_state_root(state_root, keep=args.keep_state)


if __name__ == "__main__":
    main()
