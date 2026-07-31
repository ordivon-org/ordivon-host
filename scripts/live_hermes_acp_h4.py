#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os

from anc_canonical import JsonValue, canonical_digest

from ordivon_host import (
    ArtifactRef,
    EventKind,
    HarnessHost,
    HostKernel,
    HostStorage,
    build_harness_workspace_exec_request,
    operator_handoff,
)
from ordivon_host.harness import HermesACPDriver, HermesACPPromptResult
from ordivon_host.runtime import (
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
_RESULT_PREFIX = "ORDIVON_H4_RESULT="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one real Runtime-managed Hermes ACP H4 Harness Assignment."
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ORDIVON_MCP_ENDPOINT", "http://127.0.0.1:8897/mcp"),
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--target",
        default="src/ordivon_host/harness/runtime_refs.py",
    )
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def _observe_terminal(client, job_id: str) -> dict[str, JsonValue]:
    for _ in range(16):
        observation = client.call_tool(
            "task.observe",
            {
                "schemaVersion": 1,
                "jobId": job_id,
                "waitMs": 30_000,
                "waitUntil": "terminal",
                "stdoutTailBytes": 65_536,
                "stderrTailBytes": 65_536,
            },
        )
        if observation.get("status") in _TERMINAL:
            return observation
    raise AssertionError("Hermes ACP H4 Runtime Job did not become terminal")


def _artifact(observation: dict[str, JsonValue], kind: str) -> dict[str, JsonValue]:
    artifacts = observation.get("artifacts")
    if not isinstance(artifacts, list):
        raise AssertionError("Runtime observation omitted Artifacts")
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("kind") == kind
    ]
    if len(matches) != 1:
        raise AssertionError(f"Runtime retained {len(matches)} {kind} Artifacts")
    return matches[0]


def _read_artifact_text(client, job_id: str, artifact_id: str) -> tuple[str, str]:
    offset = 0
    chunks: list[str] = []
    observed_digest: str | None = None
    for _ in range(64):
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
            raise AssertionError("artifact.read digest changed across pages")
        observed_digest = digest
        chunks.append(content)
        offset = next_offset
        if eof:
            return "".join(chunks), digest
    raise AssertionError("Runtime Artifact exceeded the bounded page count")


def _read_json_artifact(
    client, job_id: str, artifact: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    artifact_id = artifact.get("artifactId")
    if not isinstance(artifact_id, str):
        raise AssertionError("Runtime Artifact omitted identity")
    text, digest = _read_artifact_text(client, job_id, artifact_id)
    if artifact.get("digest") != digest:
        raise AssertionError("Runtime Artifact digest differs from artifact.read")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise AssertionError("Runtime JSON Artifact is not an object")
    return value


def _parse_worker_result(stdout: str) -> dict[str, JsonValue]:
    lines = [line for line in stdout.splitlines() if line.startswith(_RESULT_PREFIX)]
    if len(lines) != 1:
        raise AssertionError(f"Hermes ACP worker emitted {len(lines)} result records")
    value = json.loads(lines[0][len(_RESULT_PREFIX) :])
    if not isinstance(value, dict):
        raise AssertionError("Hermes ACP worker result is not an object")
    payload_digest = value.get("payloadDigest")
    payload = dict(value)
    payload.pop("payloadDigest", None)
    if payload_digest != canonical_digest(payload):
        raise AssertionError("Hermes ACP worker result digest differs")
    checks = value.get("checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or not all(item is True for item in checks.values())
    ):
        raise AssertionError(f"Hermes ACP worker checks failed: {checks}")
    return value


def _runtime_artifact_refs(
    observation: dict[str, JsonValue],
) -> tuple[ArtifactRef, ...]:
    artifacts = observation.get("artifacts")
    if not isinstance(artifacts, list):
        raise AssertionError("Runtime observation omitted Artifacts")
    refs: list[ArtifactRef] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = artifact.get("artifactId")
        kind = artifact.get("kind")
        digest = artifact.get("digest")
        if (
            isinstance(artifact_id, str)
            and isinstance(kind, str)
            and isinstance(digest, str)
        ):
            refs.append(
                ArtifactRef(
                    ref=f"runtime-artifact:{artifact_id}",
                    kind=kind,
                    digest=digest,
                )
            )
    if not refs:
        raise AssertionError("Runtime observation retained no usable Artifacts")
    return tuple(refs)


def main() -> None:
    args = parse_args()
    identity = ScenarioIdentity.create("live-hermes-acp-h4")
    token = load_scenario_token()
    state_root = scenario_state_root(
        args.state_root,
        prefix="hermes-acp-h4",
        identity=identity,
    )
    factory = RuntimeClientFactory(
        args.endpoint,
        token,
        "ordivon-host-live-hermes-acp-h4",
    )
    workspace_id = identity.workspace_id
    task_id = identity.task_id
    goal_id = identity.goal_id
    run_id = f"harness-run:{identity.token}:hermes:1"
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
        manifest = HermesACPDriver(working_directory=args.source_repo).manifest()
        source_digest = canonical_digest(
            {
                "repository": args.source_repo,
                "sourceRevision": args.source_revision,
            }
        )
        context_value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.hermes-acp-h4-live-context",
            "target": args.target,
            "sourceRevision": args.source_revision,
            "runtimeCatalogDigest": catalog.digest,
            "clientCapabilities": {
                "fsReadTextFile": False,
                "fsWriteTextFile": False,
                "terminal": False,
                "terminalAuth": False,
            },
            "providerToolConstraint": "read_file-only",
        }
        objective_digest = canonical_digest(
            {
                "objective": "run one provider-faithful Hermes ACP repository inspection"
            }
        )
        acceptance_digest = canonical_digest(
            {
                "acceptance": [
                    "Hermes ACP Session identity and provenance retained",
                    "one read-only Tool observation retained",
                    "usage and raw provider digest retained",
                    "thought text excluded while thought event digests remain",
                    "Runtime owns the physical process tree",
                    "Host records HarnessRunReceipt without completing the Task",
                ]
            }
        )
        with HostStorage(state_root) as storage:
            HostKernel(
                storage,
                clock_ms=scenario_clock_ms,
                owner_id="host:live-hermes-acp-h4-task-create",
            ).create_task(
                event_id=f"event:{identity.token}:create",
                kind=EventKind.TASK_CREATED,
                task_id=task_id,
                goal_id=goal_id,
                payload={"workloadId": "hermes-acp-h4-live-v1"},
                frontier=(f"node:{identity.token}:run",),
            )
            context = storage.put_object(context_value, kind="compiled-context")
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            attempt = host.start_attempt(
                task_id,
                objective_digest=objective_digest,
                acceptance_criteria_digest=acceptance_digest,
            )
            assignment = host.assign(
                attempt,
                manifest=manifest,
                context_object_digest=context.digest,
                tool_catalog_digest=catalog.digest,
                workspace_ref=workspace_id,
                source_ref="repository:ordivon-host",
                source_digest=source_digest,
                required_capabilities=(
                    "persistent_session",
                    "session_resume",
                    "session_fork",
                    "interrupt",
                    "tool_events",
                    "usage",
                ),
                budget={"runtimeJobs": 1, "modelTurns": 1},
            )
            request = build_harness_workspace_exec_request(
                assignment,
                harness_run_id=run_id,
                step_id="hermes-acp-read-only-inspection",
                executable="/root/.local/bin/uv",
                args=(
                    "run",
                    "python",
                    "scripts/hermes_acp_h4_worker.py",
                    "--working-directory",
                    ".",
                    "--target",
                    args.target,
                    "--timeout-seconds",
                    "300",
                ),
                env={
                    "NO_COLOR": "1",
                    "PYTHONUNBUFFERED": "1",
                    "UV_NO_PROGRESS": "1",
                },
                timeout_ms=420_000,
                stdout_limit_bytes=524_288,
                stderr_limit_bytes=524_288,
                wait_ms=30_000,
                stdout_tail_bytes=65_536,
                stderr_tail_bytes=65_536,
            )

        delivery_client = factory.client("delivery", initialize=True)
        first = delivery_client.call_tool("workspace.exec", request)
        job_id = first.get("jobId")
        if not isinstance(job_id, str):
            raise AssertionError("workspace.exec omitted Runtime Job identity")
        observation = (
            first
            if first.get("status") in _TERMINAL
            and isinstance(first.get("artifacts"), list)
            else _observe_terminal(delivery_client, job_id)
        )
        if observation.get("status") != "succeeded":
            raise AssertionError(f"Hermes ACP H4 Runtime Job failed: {observation}")

        stdout_artifact = _artifact(observation, "stdout")
        stdout_id = stdout_artifact.get("artifactId")
        if not isinstance(stdout_id, str):
            raise AssertionError("Runtime stdout Artifact omitted identity")
        stdout, stdout_digest = _read_artifact_text(
            factory.client("stdout-read", initialize=True),
            job_id,
            stdout_id,
        )
        if stdout_artifact.get("digest") != stdout_digest:
            raise AssertionError("Runtime stdout Artifact digest differs")
        worker_result = _parse_worker_result(stdout)
        prompt_value = worker_result.get("promptResult")
        if not isinstance(prompt_value, dict):
            raise AssertionError("Hermes ACP worker omitted Prompt result")
        prompt_result = HermesACPPromptResult.from_dict(prompt_value)

        terminal_artifact = _artifact(observation, "terminal_evidence")
        terminal_evidence = _read_json_artifact(
            factory.client("terminal-read", initialize=True),
            job_id,
            terminal_artifact,
        )
        execution = request.get("execution")
        if not isinstance(execution, dict):
            raise AssertionError("H4 Runtime request omitted execution")
        references = execution.get("foreignReferences")
        if not isinstance(references, list):
            raise AssertionError("H4 Runtime request omitted Host references")

        fresh_jobs = jobs_for_request(
            factory.client("fresh-recovery", initialize=True),
            str(request["clientRequestId"]),
        )
        if len(fresh_jobs) != 1 or fresh_jobs[0].get("jobId") != job_id:
            raise AssertionError("fresh Host client did not recover the H4 Runtime Job")

        with HostStorage(state_root) as storage:
            worker_object = storage.put_object(
                worker_result,
                kind="hermes-acp-h4-worker-result",
            )
            recovered_assignment = HarnessHost(
                storage, clock_ms=scenario_clock_ms
            ).load_current_assignment(task_id)
            artifacts = _runtime_artifact_refs(observation) + (
                ArtifactRef(
                    ref=f"host-object:{worker_object.digest}",
                    kind="hermes-acp-h4-worker-result",
                    digest=canonical_digest(worker_result),
                ),
            )
            receipt = prompt_result.to_harness_run_receipt(
                recovered_assignment,
                harness_run_id=run_id,
                runtime_job_refs=(job_id,),
                artifact_refs=artifacts,
            )
            recorded = HarnessHost(
                storage, clock_ms=scenario_clock_ms
            ).record_run(recovered_assignment, receipt)
            handoff = operator_handoff(storage, task_id)

        worker_checks = worker_result.get("checks")
        baseline = worker_result.get("baselineComparison")
        counts = prompt_result.update_type_counts
        edit_tools = [
            item
            for item in prompt_result.tool_items
            if item.get("kind") in {"edit", "delete", "move"}
            or int(item.get("fileEditCount", 0)) > 0
        ]
        checks = {
            "runtimeJobSucceeded": observation.get("status") == "succeeded",
            "runtimeOwnedProcessTree": terminal_evidence.get("processTreeDisposition")
            == "terminal_clean",
            "terminalEvidenceRetainedHostReferences": terminal_evidence.get(
                "foreignReferences"
            )
            == references,
            "terminalEvidenceBoundSource": terminal_evidence.get("sourceRevision")
            == args.source_revision,
            "freshClientRecoveredJob": len(fresh_jobs) == 1
            and fresh_jobs[0].get("jobId") == job_id,
            "workerChecksPassed": isinstance(worker_checks, dict)
            and bool(worker_checks)
            and all(value is True for value in worker_checks.values()),
            "hermesPromptCompleted": prompt_result.status == "completed"
            and prompt_result.provider_stop_reason == "end_turn",
            "sessionIdentityRetained": bool(prompt_result.session.session_id),
            "sessionProvenanceRetained": bool(
                prompt_result.session.provenance_digest
            ),
            "toolObservationRetained": counts.get("tool_call", 0) >= 1
            and bool(prompt_result.tool_items),
            "terminalPromptAfterTool": prompt_result.provider_stop_reason == "end_turn"
            and counts.get("tool_call", 0) >= 1,
            "noEditToolObserved": not edit_tools,
            "usageObserved": bool(prompt_result.usage)
            and counts.get("usage_update", 0) >= 1,
            "rawProviderDigestRetained": prompt_result.raw_message_digest.startswith(
                "sha256:"
            ),
            "thoughtTextExcluded": all(
                set(event.to_dict())
                == {
                    "kind",
                    "method",
                    "updateType",
                    "observedAtMs",
                    "sessionId",
                    "toolCallId",
                    "toolKind",
                    "payloadDigest",
                }
                for event in prompt_result.normalized_events
                if event.kind == "thought_observed"
            ),
            "hostRecordedHarnessRun": recorded.receipt.harness_run_id == run_id
            and recorded.receipt.runtime_job_refs == (job_id,),
            "handoffProjectsHarnessRun": handoff.harness_run_id == run_id,
            "taskNotSemanticallyCompleted": handoff.task_state.value == "waiting"
            and handoff.outcome_object_digest is None,
            "oneShotBaselineRemainsDistinct": isinstance(baseline, dict)
            and baseline.get("hermesCliOneShot", {}).get(
                "persistentSessionRetained"
            )
            is False
            and baseline.get("hermesACP", {}).get("persistentSessionRetained")
            is True,
            "noRuntimeSemanticCompletionClaim": (
                "semanticCompletion" not in terminal_evidence
                and "taskOutcome" not in terminal_evidence
            ),
        }
        if not all(checks.values()):
            raise AssertionError(f"Hermes ACP H4 live checks failed: {checks}")

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
            raise AssertionError("Hermes ACP H4 Runtime Workspace remained open")

        receipt_value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.hermes-acp-h4-live-receipt",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "implementationSourceRevision": args.source_revision,
            "taskId": task_id,
            "goalId": goal_id,
            "taskAttemptId": assignment.attempt.task_attempt_id,
            "assignmentId": assignment.assignment.assignment_id,
            "assignmentGeneration": assignment.assignment.generation,
            "harnessRunId": run_id,
            "manifest": manifest.to_dict(),
            "workspace": workspace,
            "runtimeCatalogDigest": catalog.digest,
            "request": request,
            "runtime": {
                "jobId": job_id,
                "attemptId": terminal_evidence.get("attemptId"),
                "status": observation.get("status"),
                "matchingJobs": fresh_jobs,
                "artifacts": observation.get("artifacts"),
                "terminalEvidence": terminal_evidence,
                "workspaceClose": closed,
                "workspaceClosed": closed_confirmed,
            },
            "provider": worker_result,
            "host": {
                "recordedRun": recorded.receipt.to_dict(),
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
