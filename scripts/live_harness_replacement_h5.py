#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from anc_canonical import JsonValue, canonical_digest, digest_text

from ordivon_host import (
    ArtifactRef,
    CodexAppServerDriver,
    CodexAppTurnResult,
    EventKind,
    HarnessCapabilityManifest,
    HarnessHost,
    HarnessRunReceipt,
    HermesACPDriver,
    HermesACPPromptResult,
    HostKernel,
    HostStorage,
    build_harness_workspace_exec_request,
    operator_handoff,
)
from ordivon_host.runtime import RuntimeTransportError
from ordivon_host.runtime import (
    discover_execution_runtime_catalog,
    ensure_workspace,
    ensure_workspace_closed,
)
from ordivon_host.testing import (
    DropFirstSuccessfulExecResponse,
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

from harness_replacement_h5_support import (
    COMPLETION_PATH,
    DIAGNOSIS_PATH,
    SOURCE_PATH,
    TEST_COMMAND,
    WORKLOAD_ID,
    WORKLOAD_RELATIVE,
    semantic_digest,
    validate_completion,
    validate_diagnosis,
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
_RESULT_PREFIX = "ORDIVON_H5_WORKER_RESULT="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run both real H5 cross-provider replacement trajectories and the "
            "first stale/missing/response-loss fault slice."
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
    for _ in range(24):
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
    raise AssertionError(f"Runtime Job {job_id} did not become terminal")


def _artifact(
    observation: dict[str, JsonValue],
    kind: str,
) -> dict[str, JsonValue]:
    artifacts = observation.get("artifacts")
    if not isinstance(artifacts, list):
        raise AssertionError("Runtime observation omitted Artifacts")
    matches = [
        value
        for value in artifacts
        if isinstance(value, dict) and value.get("kind") == kind
    ]
    if len(matches) != 1:
        raise AssertionError(f"Runtime retained {len(matches)} {kind} Artifacts")
    return matches[0]


def _read_artifact_text(client, job_id: str, artifact_id: str) -> tuple[str, str]:
    chunks: list[str] = []
    offset = 0
    observed_digest: str | None = None
    for _ in range(128):
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
            raise AssertionError("Runtime Artifact digest changed between pages")
        chunks.append(content)
        observed_digest = digest
        offset = next_offset
        if eof:
            return "".join(chunks), digest
    raise AssertionError("Runtime Artifact exceeded the bounded page count")


def _read_json_artifact(
    client,
    job_id: str,
    artifact: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    artifact_id = artifact.get("artifactId")
    expected_digest = artifact.get("digest")
    if not isinstance(artifact_id, str) or not isinstance(expected_digest, str):
        raise AssertionError("Runtime Artifact identity is invalid")
    text, digest = _read_artifact_text(client, job_id, artifact_id)
    if digest != expected_digest:
        raise AssertionError("Runtime Artifact digest differs from artifact.read")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise AssertionError("Runtime JSON Artifact is not an object")
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
        if all(isinstance(item, str) for item in (artifact_id, kind, digest)):
            refs.append(
                ArtifactRef(
                    ref=f"runtime-artifact:{artifact_id}",
                    kind=str(kind),
                    digest=str(digest),
                )
            )
    if not refs:
        raise AssertionError("Runtime Job retained no usable Artifacts")
    return tuple(refs)


def _parse_worker_result(stdout: str) -> dict[str, JsonValue]:
    records = [line for line in stdout.splitlines() if line.startswith(_RESULT_PREFIX)]
    if len(records) != 1:
        raise AssertionError(f"H5 worker emitted {len(records)} result records")
    value = json.loads(records[0][len(_RESULT_PREFIX) :])
    if not isinstance(value, dict):
        raise AssertionError("H5 worker result is not an object")
    digest = value.get("payloadDigest")
    payload = dict(value)
    payload.pop("payloadDigest", None)
    if digest != canonical_digest(payload):
        raise AssertionError("H5 worker payload digest differs")
    checks = value.get("checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or not all(result is True for result in checks.values())
    ):
        raise AssertionError(f"H5 worker checks failed: {checks}")
    return value


def _workspace_read(client, workspace_id: str, relative_path: str) -> tuple[str, str]:
    value = client.call_tool(
        "workspace.read",
        {
            "schemaVersion": 1,
            "workspaceId": workspace_id,
            "relativePath": relative_path,
            "mode": "FULL",
            "offset": 0,
            "maxBytes": 1_048_576,
        },
    )
    content = value.get("content")
    digest = value.get("digest")
    if not isinstance(content, str) or not isinstance(digest, str):
        raise AssertionError("workspace.read omitted content or digest")
    if digest_text(content) != digest:
        raise AssertionError("workspace.read digest differs from content")
    return content, digest


def _provider_manifest(provider: str, working_directory: str) -> HarnessCapabilityManifest:
    if provider == "codex":
        return CodexAppServerDriver(working_directory=working_directory).manifest()
    if provider == "hermes":
        return HermesACPDriver(working_directory=working_directory).manifest()
    raise ValueError(f"unsupported H5 provider: {provider}")


def _provider_result(provider: str, worker: dict[str, JsonValue]):
    summary = worker.get("providerSummary")
    if not isinstance(summary, dict):
        raise AssertionError("H5 worker omitted Provider summary")
    value = summary.get("result")
    if not isinstance(value, dict):
        raise AssertionError("H5 worker omitted Provider result")
    if provider == "codex":
        return CodexAppTurnResult.from_dict(value)
    return HermesACPPromptResult.from_dict(value)


def _compact_provider(worker: dict[str, JsonValue]) -> dict[str, JsonValue]:
    summary = worker.get("providerSummary")
    if not isinstance(summary, dict):
        raise AssertionError("H5 worker omitted Provider summary")
    result: dict[str, JsonValue] = {
        key: value
        for key, value in summary.items()
        if key != "result"
    }
    return result


def _run_worker_job(
    factory: RuntimeClientFactory,
    committed,
    *,
    provider: str,
    phase: str,
    source_revision: str,
    harness_run_id: str,
    diagnosis_digest: str | None = None,
    inject_response_loss: bool = False,
) -> dict[str, Any]:
    args = [
        "run",
        "python",
        "scripts/harness_replacement_h5_worker.py",
        "--provider",
        provider,
        "--phase",
        phase,
        "--working-directory",
        ".",
        "--base-source-revision",
        source_revision,
        "--timeout-seconds",
        "480",
    ]
    if diagnosis_digest is not None:
        args.extend(("--diagnosis-digest", diagnosis_digest))
    request = build_harness_workspace_exec_request(
        committed,
        harness_run_id=harness_run_id,
        step_id=f"h5-{phase}-{provider}",
        executable="/root/.local/bin/uv",
        args=tuple(args),
        env={
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            "UV_NO_PROGRESS": "1",
        },
        timeout_ms=600_000,
        stdout_limit_bytes=2_097_152,
        stderr_limit_bytes=1_048_576,
        wait_ms=30_000,
        stdout_tail_bytes=65_536,
        stderr_tail_bytes=65_536,
    )
    response_lost = False
    dispatch_calls = 1
    if inject_response_loss:
        lossy = DropFirstSuccessfulExecResponse(
            factory.client(f"{phase}-{provider}-lossy", initialize=True)
        )
        try:
            lossy.call_tool("workspace.exec", request)
        except RuntimeTransportError:
            response_lost = True
        else:
            raise AssertionError("H5 response-loss injector did not drop the response")
        dispatch_calls = lossy.calls.count("workspace.exec")
        jobs = jobs_for_request(
            factory.client(f"{phase}-{provider}-recover", initialize=True),
            str(request["clientRequestId"]),
        )
        if len(jobs) != 1 or not isinstance(jobs[0].get("jobId"), str):
            raise AssertionError("fresh Host did not recover exactly one H5 Runtime Job")
        job_id = str(jobs[0]["jobId"])
    else:
        first = factory.client(
            f"{phase}-{provider}-delivery", initialize=True
        ).call_tool("workspace.exec", request)
        job_id_value = first.get("jobId")
        if not isinstance(job_id_value, str):
            raise AssertionError("workspace.exec omitted H5 Runtime Job identity")
        job_id = job_id_value
        jobs = jobs_for_request(
            factory.client(f"{phase}-{provider}-audit", initialize=True),
            str(request["clientRequestId"]),
        )
    observation = _observe_terminal(
        factory.client(f"{phase}-{provider}-observe", initialize=True),
        job_id,
    )
    if observation.get("status") != "succeeded":
        raise AssertionError(f"H5 {phase} Runtime Job failed: {observation}")
    if len(jobs) != 1 or jobs[0].get("jobId") != job_id:
        raise AssertionError("H5 request identity resolved to another Runtime Job set")
    stdout_artifact = _artifact(observation, "stdout")
    stdout_id = stdout_artifact.get("artifactId")
    if not isinstance(stdout_id, str):
        raise AssertionError("H5 stdout Artifact omitted identity")
    stdout, stdout_digest = _read_artifact_text(
        factory.client(f"{phase}-{provider}-stdout", initialize=True),
        job_id,
        stdout_id,
    )
    if stdout_artifact.get("digest") != stdout_digest:
        raise AssertionError("H5 stdout Artifact digest differs")
    worker = _parse_worker_result(stdout)
    terminal_artifact = _artifact(observation, "terminal_evidence")
    terminal = _read_json_artifact(
        factory.client(f"{phase}-{provider}-terminal", initialize=True),
        job_id,
        terminal_artifact,
    )
    execution = request.get("execution")
    if not isinstance(execution, dict):
        raise AssertionError("H5 Runtime request omitted execution")
    references = execution.get("foreignReferences")
    if not isinstance(references, list):
        raise AssertionError("H5 Runtime request omitted Host references")
    if terminal.get("foreignReferences") != references:
        raise AssertionError("H5 Terminal Evidence changed Host references")
    if terminal.get("sourceRevision") != source_revision:
        raise AssertionError("H5 Terminal Evidence changed source revision")
    if terminal.get("processTreeDisposition") != "terminal_clean":
        raise AssertionError("H5 Runtime process tree is not terminal clean")
    if "semanticCompletion" in terminal or "taskOutcome" in terminal:
        raise AssertionError("H5 Runtime claimed semantic Task completion")
    return {
        "request": request,
        "jobId": job_id,
        "matchingJobs": jobs,
        "observation": observation,
        "terminalEvidence": terminal,
        "worker": worker,
        "responseLost": response_lost,
        "dispatchCalls": dispatch_calls,
    }


def _run_independent_acceptance(
    factory: RuntimeClientFactory,
    committed,
    *,
    harness_run_id: str,
    source_revision: str,
) -> dict[str, Any]:
    request = build_harness_workspace_exec_request(
        committed,
        harness_run_id=harness_run_id,
        step_id="h5-independent-acceptance",
        executable="/usr/bin/python3",
        args=("-m", "unittest", "-v", "test_allocation.py"),
        cwd_relative=str(WORKLOAD_RELATIVE),
        env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
        timeout_ms=120_000,
        stdout_limit_bytes=524_288,
        stderr_limit_bytes=524_288,
        wait_ms=30_000,
        stdout_tail_bytes=65_536,
        stderr_tail_bytes=65_536,
    )
    first = factory.client("independent-acceptance", initialize=True).call_tool(
        "workspace.exec", request
    )
    job_id = first.get("jobId")
    if not isinstance(job_id, str):
        raise AssertionError("independent acceptance omitted Runtime Job identity")
    observation = _observe_terminal(
        factory.client("independent-acceptance-observe", initialize=True),
        job_id,
    )
    if observation.get("status") != "succeeded":
        raise AssertionError(f"independent acceptance failed: {observation}")
    jobs = jobs_for_request(
        factory.client("independent-acceptance-audit", initialize=True),
        str(request["clientRequestId"]),
    )
    if len(jobs) != 1 or jobs[0].get("jobId") != job_id:
        raise AssertionError("independent acceptance created multiple Runtime Jobs")
    terminal = _read_json_artifact(
        factory.client("independent-acceptance-terminal", initialize=True),
        job_id,
        _artifact(observation, "terminal_evidence"),
    )
    execution = request.get("execution")
    if not isinstance(execution, dict):
        raise AssertionError("independent acceptance request omitted execution")
    if terminal.get("foreignReferences") != execution.get("foreignReferences"):
        raise AssertionError("independent acceptance changed Host references")
    if terminal.get("sourceRevision") != source_revision:
        raise AssertionError("independent acceptance changed source revision")
    return {
        "request": request,
        "jobId": job_id,
        "matchingJobs": jobs,
        "observation": observation,
        "terminalEvidence": terminal,
    }


def _host_object_artifact(
    stored,
    *,
    kind: str,
    semantic: dict[str, JsonValue],
) -> ArtifactRef:
    return ArtifactRef(
        ref=f"host-object:{stored.digest}",
        kind=kind,
        digest=canonical_digest(semantic),
    )


def _run_trajectory(
    *,
    order: tuple[str, str],
    source_repo: str,
    source_revision: str,
    factory: RuntimeClientFactory,
    state_root: Path,
    inject_response_loss: bool,
) -> dict[str, JsonValue]:
    first_provider, second_provider = order
    label = f"{first_provider}-to-{second_provider}"
    identity = ScenarioIdentity.create(f"h5-{label}")
    workspace_id = identity.workspace_id
    task_id = identity.task_id
    goal_id = identity.goal_id
    first_run_id = f"harness-run:{identity.token}:{first_provider}:diagnose"
    second_run_id = f"harness-run:{identity.token}:{second_provider}:repair"
    completed = False
    try:
        setup = factory.client(f"{label}-setup", initialize=True)
        catalog = discover_execution_runtime_catalog(setup)
        workspace = ensure_workspace(
            setup,
            workspace_id=workspace_id,
            source_repo=source_repo,
            source_revision=source_revision,
        )
        source_binding_digest = canonical_digest(
            {"repository": source_repo, "sourceRevision": source_revision}
        )
        objective_digest = canonical_digest(
            {
                "workloadId": WORKLOAD_ID,
                "objective": "diagnose, replace Harness, repair, and verify",
            }
        )
        acceptance_digest = canonical_digest(
            {
                "workloadId": WORKLOAD_ID,
                "acceptance": [
                    "diagnosis Artifact is valid and retained",
                    "replacement uses a fresh Assignment generation and Context",
                    "allocation.py satisfies frozen tests",
                    "completion Artifact binds diagnosis and final source",
                    "Host alone commits TaskOutcome",
                ],
            }
        )
        trajectory_root = state_root / label
        trajectory_root.mkdir(parents=True, exist_ok=True)
        with HostStorage(trajectory_root) as storage:
            HostKernel(
                storage,
                clock_ms=scenario_clock_ms,
                owner_id=f"host:h5:{label}:create",
            ).create_task(
                event_id=f"event:{identity.token}:create",
                kind=EventKind.TASK_CREATED,
                task_id=task_id,
                goal_id=goal_id,
                payload={"workloadId": WORKLOAD_ID, "providerOrder": list(order)},
                frontier=(f"node:{identity.token}:repair",),
            )
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            attempt = host.start_attempt(
                task_id,
                objective_digest=objective_digest,
                acceptance_criteria_digest=acceptance_digest,
            )
            first_context = storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.h5-assignment-context",
                    "workloadId": WORKLOAD_ID,
                    "generation": 1,
                    "phase": "diagnose",
                    "sourceRevision": source_revision,
                    "provider": first_provider,
                    "priorArtifacts": [],
                },
                kind="compiled-context",
            )
            first_assignment = host.assign(
                attempt,
                manifest=_provider_manifest(first_provider, source_repo),
                context_object_digest=first_context.digest,
                tool_catalog_digest=catalog.digest,
                workspace_ref=workspace_id,
                source_ref="repository:ordivon-host",
                source_digest=source_binding_digest,
                required_capabilities=(
                    "persistent_session",
                    "interrupt",
                    "tool_events",
                    "usage",
                ),
                budget={"modelTurns": 1, "runtimeJobs": 1, "phase": "diagnose"},
            )

        first_runtime = _run_worker_job(
            factory,
            first_assignment,
            provider=first_provider,
            phase="diagnose",
            source_revision=source_revision,
            harness_run_id=first_run_id,
        )
        diagnosis_relative = str(WORKLOAD_RELATIVE / DIAGNOSIS_PATH)
        diagnosis_text, diagnosis_text_digest = _workspace_read(
            factory.client(f"{label}-diagnosis-read", initialize=True),
            workspace_id,
            diagnosis_relative,
        )
        diagnosis_value = json.loads(diagnosis_text)
        if not isinstance(diagnosis_value, dict):
            raise AssertionError("diagnosis Artifact is not an object")
        validate_diagnosis(diagnosis_value, base_source_revision=source_revision)
        first_worker = first_runtime["worker"]
        if diagnosis_value != first_worker.get("artifactValue"):
            raise AssertionError("workspace diagnosis differs from worker evidence")
        if diagnosis_text_digest != first_worker.get("artifactTextDigest"):
            raise AssertionError("workspace diagnosis text digest differs")
        diagnosis_semantic_digest = semantic_digest(diagnosis_value)
        if diagnosis_semantic_digest != first_worker.get("artifactSemanticDigest"):
            raise AssertionError("workspace diagnosis semantic digest differs")

        with HostStorage(trajectory_root) as storage:
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            current_first = host.load_current_assignment(task_id)
            first_result = _provider_result(first_provider, first_worker)
            first_worker_object = storage.put_object(
                first_worker, kind="h5-worker-result"
            )
            diagnosis_object = storage.put_object(
                diagnosis_value, kind="h5-diagnosis"
            )
            diagnosis_ref = _host_object_artifact(
                diagnosis_object,
                kind="diagnosis",
                semantic=diagnosis_value,
            )
            first_receipt = first_result.to_harness_run_receipt(
                current_first,
                harness_run_id=first_run_id,
                runtime_job_refs=(str(first_runtime["jobId"]),),
                artifact_refs=(
                    *_runtime_artifact_refs(first_runtime["observation"]),
                    _host_object_artifact(
                        first_worker_object,
                        kind="h5-worker-result",
                        semantic=first_worker,
                    ),
                    diagnosis_ref,
                ),
            )
            recorded_first = host.record_run(current_first, first_receipt)
            stale_proposal = host.propose_completion(
                recorded_first,
                summary="The diagnosis Harness incorrectly claims full Task completion.",
                acceptance_results={
                    "phase": "diagnose",
                    "claimedComplete": True,
                    "provider": first_provider,
                },
                evidence_refs=(diagnosis_ref,),
                artifact_refs=(diagnosis_ref,),
                usage={"fault": "stale-generation"},
            )
            second_context = storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.h5-assignment-context",
                    "workloadId": WORKLOAD_ID,
                    "generation": 2,
                    "phase": "repair",
                    "sourceRevision": source_revision,
                    "provider": second_provider,
                    "diagnosis": diagnosis_value,
                    "diagnosisSemanticDigest": diagnosis_semantic_digest,
                    "diagnosisObjectDigest": diagnosis_object.digest,
                    "priorRun": {
                        "provider": first_provider,
                        "harnessRunId": first_run_id,
                        "runtimeJobId": first_runtime["jobId"],
                        "workerPayloadDigest": first_worker["payloadDigest"],
                    },
                },
                kind="compiled-context",
            )
            second_assignment = host.assign(
                host.load_attempt(task_id),
                manifest=_provider_manifest(second_provider, source_repo),
                context_object_digest=second_context.digest,
                tool_catalog_digest=catalog.digest,
                workspace_ref=workspace_id,
                source_ref="repository:ordivon-host",
                source_digest=source_binding_digest,
                prior_artifact_refs=(diagnosis_ref,),
                required_capabilities=(
                    "persistent_session",
                    "interrupt",
                    "tool_events",
                    "usage",
                ),
                budget={"modelTurns": 1, "runtimeJobs": 2, "phase": "repair"},
            )
            before_stale_jobs = jobs_for_request(
                factory.client(f"{label}-stale-before", initialize=True),
                str(first_runtime["request"]["clientRequestId"]),
            )

            def stale_verifier(_):
                raise AssertionError("stale CompletionProposal verifier must not run")

            stale_decision = host.adjudicate_completion(
                stale_proposal,
                artifact_exists=lambda ref: ref == diagnosis_ref,
                acceptance_verifier=stale_verifier,
            )
            after_stale_jobs = jobs_for_request(
                factory.client(f"{label}-stale-after", initialize=True),
                str(first_runtime["request"]["clientRequestId"]),
            )
            if stale_decision.decision.reason_code != "stale_assignment":
                raise AssertionError("old Assignment proposal was not rejected as stale")
            if stale_decision.outcome is not None:
                raise AssertionError("stale CompletionProposal produced TaskOutcome")
            if before_stale_jobs != after_stale_jobs:
                raise AssertionError("stale adjudication changed Runtime Job evidence")
            current_second = host.load_current_assignment(task_id)

        second_runtime = _run_worker_job(
            factory,
            current_second,
            provider=second_provider,
            phase="repair",
            source_revision=source_revision,
            harness_run_id=second_run_id,
            diagnosis_digest=diagnosis_semantic_digest,
            inject_response_loss=inject_response_loss,
        )
        completion_relative = str(WORKLOAD_RELATIVE / COMPLETION_PATH)
        source_relative = str(WORKLOAD_RELATIVE / SOURCE_PATH)
        completion_text, completion_text_digest = _workspace_read(
            factory.client(f"{label}-completion-read", initialize=True),
            workspace_id,
            completion_relative,
        )
        source_text, final_source_digest = _workspace_read(
            factory.client(f"{label}-source-read", initialize=True),
            workspace_id,
            source_relative,
        )
        completion_value = json.loads(completion_text)
        if not isinstance(completion_value, dict):
            raise AssertionError("completion Artifact is not an object")
        validate_completion(
            completion_value,
            base_source_revision=source_revision,
            diagnosis_digest=diagnosis_semantic_digest,
            final_source_digest=final_source_digest,
        )
        second_worker = second_runtime["worker"]
        if completion_value != second_worker.get("artifactValue"):
            raise AssertionError("workspace completion differs from worker evidence")
        if completion_text_digest != second_worker.get("artifactTextDigest"):
            raise AssertionError("workspace completion text digest differs")
        if final_source_digest != second_worker.get("sourceTextDigestAfter"):
            raise AssertionError("workspace source digest differs from worker evidence")
        independent = _run_independent_acceptance(
            factory,
            current_second,
            harness_run_id=second_run_id,
            source_revision=source_revision,
        )
        diff = factory.client(f"{label}-diff", initialize=True).call_tool(
            "workspace.diff",
            {
                "schemaVersion": 1,
                "workspaceId": workspace_id,
                "maxBytes": 1_048_576,
            },
        )
        diff_text = diff.get("diff")
        untracked = diff.get("untrackedPaths")
        allocation_path = str(WORKLOAD_RELATIVE / SOURCE_PATH)
        diagnosis_path = str(WORKLOAD_RELATIVE / DIAGNOSIS_PATH)
        completion_path = str(WORKLOAD_RELATIVE / COMPLETION_PATH)
        allocation_marker = f"diff --git a/{allocation_path} b/{allocation_path}"
        if not isinstance(diff_text, str) or allocation_marker not in diff_text:
            raise AssertionError("H5 Runtime diff omitted allocation.py repair")
        required_untracked = {diagnosis_path, completion_path}
        allowed_environment_byproducts = {"uv.lock"}
        if not isinstance(untracked, list) or not required_untracked.issubset(
            set(untracked)
        ):
            raise AssertionError("H5 Runtime diff omitted required Artifact files")
        unexpected_untracked = (
            set(untracked) - required_untracked - allowed_environment_byproducts
        )
        if unexpected_untracked:
            raise AssertionError(
                f"H5 Runtime retained unexpected untracked files: {unexpected_untracked}"
            )
        if any(
            path in diff_text or path in set(untracked)
            for path in (
                str(WORKLOAD_RELATIVE / "SPEC.md"),
                str(WORKLOAD_RELATIVE / "test_allocation.py"),
            )
        ):
            raise AssertionError("H5 changed frozen specification or tests")

        with HostStorage(trajectory_root) as storage:
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            current_second = host.load_current_assignment(task_id)
            second_result = _provider_result(second_provider, second_worker)
            second_worker_object = storage.put_object(
                second_worker, kind="h5-worker-result"
            )
            completion_object = storage.put_object(
                completion_value, kind="h5-completion"
            )
            final_source_object = storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.h5-final-source-evidence",
                    "relativePath": source_relative,
                    "contentDigest": final_source_digest,
                    "content": source_text,
                },
                kind="h5-final-source-evidence",
            )
            diagnosis_ref = _host_object_artifact(
                diagnosis_object,
                kind="diagnosis",
                semantic=diagnosis_value,
            )
            completion_ref = _host_object_artifact(
                completion_object,
                kind="completion",
                semantic=completion_value,
            )
            source_ref = ArtifactRef(
                ref=f"host-object:{final_source_object.digest}",
                kind="final-source",
                digest=final_source_digest,
            )
            second_receipt = second_result.to_harness_run_receipt(
                current_second,
                harness_run_id=second_run_id,
                runtime_job_refs=(
                    str(second_runtime["jobId"]),
                    str(independent["jobId"]),
                ),
                artifact_refs=(
                    *_runtime_artifact_refs(second_runtime["observation"]),
                    *_runtime_artifact_refs(independent["observation"]),
                    _host_object_artifact(
                        second_worker_object,
                        kind="h5-worker-result",
                        semantic=second_worker,
                    ),
                    diagnosis_ref,
                    completion_ref,
                    source_ref,
                ),
            )
            recorded_second = host.record_run(current_second, second_receipt)
            proposal = host.propose_completion(
                recorded_second,
                summary=(
                    f"{second_provider} repaired the frozen workload after replacing "
                    f"{first_provider}; independent Runtime acceptance passed."
                ),
                acceptance_results={
                    "workloadId": WORKLOAD_ID,
                    "providerOrder": list(order),
                    "tests": "passed",
                    "testCommand": TEST_COMMAND,
                    "changedPaths": [str(SOURCE_PATH)],
                    "diagnosisDigest": diagnosis_semantic_digest,
                    "completionDigest": semantic_digest(completion_value),
                    "finalSourceDigest": final_source_digest,
                    "responseLossRecovered": inject_response_loss,
                },
                evidence_refs=(diagnosis_ref, completion_ref, source_ref),
                artifact_refs=(completion_ref, source_ref),
                usage={
                    "modelCalls": 2,
                    "providerOrder": list(order),
                    "runtimeJobs": 3,
                },
            )
            known_refs = {diagnosis_ref, completion_ref, source_ref}

            def acceptance_verifier(candidate):
                results = candidate.acceptance_results
                accepted = (
                    results.get("workloadId") == WORKLOAD_ID
                    and results.get("providerOrder") == list(order)
                    and results.get("tests") == "passed"
                    and results.get("changedPaths") == [str(SOURCE_PATH)]
                    and results.get("diagnosisDigest") == diagnosis_semantic_digest
                    and results.get("completionDigest")
                    == semantic_digest(completion_value)
                    and results.get("finalSourceDigest") == final_source_digest
                )
                return (
                    accepted,
                    None if accepted else "H5 acceptance evidence differs",
                    {
                        "accepted": accepted,
                        "workloadId": WORKLOAD_ID,
                        "providerOrder": list(order),
                        "independentRuntimeJobId": independent["jobId"],
                        "finalSourceDigest": final_source_digest,
                    },
                )

            accepted = host.adjudicate_completion(
                proposal,
                artifact_exists=lambda ref: ref in known_refs,
                acceptance_verifier=acceptance_verifier,
            )
            if not accepted.decision.accepted or accepted.outcome is None:
                raise AssertionError("current H5 CompletionProposal was not accepted")
            handoff = operator_handoff(storage, task_id)
            current_attempt = host.load_attempt(task_id)

        checks: dict[str, bool] = {
            "sameTaskAttemptAcrossReplacement": current_attempt.descriptor.task_attempt_id
            == first_assignment.attempt.task_attempt_id
            == second_assignment.attempt.task_attempt_id,
            "generationAdvanced": first_assignment.assignment.generation == 1
            and second_assignment.assignment.generation == 2,
            "freshContextCompiled": first_assignment.assignment.context_object_digest
            != second_assignment.assignment.context_object_digest,
            "providerSessionNotTaskIdentity": first_receipt.session_ref
            != second_receipt.session_ref
            and first_receipt.session_ref
            not in {
                task_id,
                current_attempt.descriptor.task_attempt_id,
                second_assignment.assignment.assignment_id,
            },
            "diagnosisPassedAsArtifact": diagnosis_ref
            in second_assignment.assignment.prior_artifact_refs,
            "staleProposalRejected": stale_decision.decision.reason_code
            == "stale_assignment"
            and stale_decision.outcome is None,
            "staleDecisionDispatchedNoRuntimeWork": before_stale_jobs
            == after_stale_jobs,
            "repairRuntimeSucceeded": second_runtime["observation"].get("status")
            == "succeeded",
            "responseLossBehaviorMatchesTrajectory": bool(
                second_runtime["responseLost"]
            )
            == inject_response_loss,
            "noBlindRedispatch": int(second_runtime["dispatchCalls"]) == 1
            and len(second_runtime["matchingJobs"]) == 1,
            "independentAcceptanceSucceeded": independent["observation"].get(
                "status"
            )
            == "succeeded",
            "completionBindsDiagnosis": completion_value.get("diagnosisDigest")
            == diagnosis_semantic_digest,
            "completionBindsSource": completion_value.get("finalSourceDigest")
            == final_source_digest,
            "frozenWorkloadBoundaryPreserved": allocation_marker in diff_text
            and required_untracked.issubset(set(untracked))
            and not unexpected_untracked,
            "runtimeEnvironmentByproductsBounded": set(untracked)
            - required_untracked
            <= allowed_environment_byproducts,
            "hostCommittedTaskOutcome": accepted.decision.accepted
            and accepted.task_state == "completed"
            and accepted.outcome is not None,
            "handoffIsTerminal": handoff.task_state.value == "completed"
            and handoff.outcome_object_digest is not None,
            "runtimeNeverClaimedSemanticCompletion": all(
                "semanticCompletion" not in evidence
                and "taskOutcome" not in evidence
                for evidence in (
                    first_runtime["terminalEvidence"],
                    second_runtime["terminalEvidence"],
                    independent["terminalEvidence"],
                )
            ),
        }
        if not all(checks.values()):
            raise AssertionError(f"H5 {label} checks failed: {checks}")

        closed = ensure_workspace_closed(
            factory.client(f"{label}-close", initialize=True),
            workspace_id,
            force=True,
        )
        closed_confirmed = workspace_absent(
            factory.client(f"{label}-close-audit", initialize=True),
            workspace_id,
        )
        if not closed_confirmed:
            raise AssertionError(f"H5 {label} Runtime Workspace remained open")
        completed = True
        return {
            "schemaVersion": 1,
            "kind": "ordivon.h5-replacement-trajectory",
            "label": label,
            "providerOrder": list(order),
            "taskId": task_id,
            "goalId": goal_id,
            "taskAttemptId": current_attempt.descriptor.task_attempt_id,
            "workspace": workspace,
            "assignments": [
                {
                    "assignmentId": first_assignment.assignment.assignment_id,
                    "generation": first_assignment.assignment.generation,
                    "provider": first_provider,
                    "contextObjectDigest": first_assignment.assignment.context_object_digest,
                },
                {
                    "assignmentId": second_assignment.assignment.assignment_id,
                    "generation": second_assignment.assignment.generation,
                    "provider": second_provider,
                    "contextObjectDigest": second_assignment.assignment.context_object_digest,
                    "priorArtifactRefs": [
                        ref.to_dict()
                        for ref in second_assignment.assignment.prior_artifact_refs
                    ],
                },
            ],
            "runs": [
                {
                    "harnessRunId": first_run_id,
                    "provider": first_provider,
                    "sessionRef": first_receipt.session_ref,
                    "runtimeJobIds": list(first_receipt.runtime_job_refs),
                    "providerSummary": _compact_provider(first_worker),
                    "workerPayloadDigest": first_worker["payloadDigest"],
                    "runtimeTerminalEvidence": first_runtime["terminalEvidence"],
                },
                {
                    "harnessRunId": second_run_id,
                    "provider": second_provider,
                    "sessionRef": second_receipt.session_ref,
                    "runtimeJobIds": list(second_receipt.runtime_job_refs),
                    "providerSummary": _compact_provider(second_worker),
                    "workerPayloadDigest": second_worker["payloadDigest"],
                    "runtimeTerminalEvidence": second_runtime["terminalEvidence"],
                },
            ],
            "diagnosis": {
                "value": diagnosis_value,
                "semanticDigest": diagnosis_semantic_digest,
                "textDigest": diagnosis_text_digest,
                "artifactRef": diagnosis_ref.to_dict(),
            },
            "completion": {
                "value": completion_value,
                "semanticDigest": semantic_digest(completion_value),
                "textDigest": completion_text_digest,
                "artifactRef": completion_ref.to_dict(),
                "finalSourceDigest": final_source_digest,
            },
            "faults": {
                "staleGeneration": stale_decision.decision.to_dict(),
                "responseLossInjected": inject_response_loss,
                "responseLost": second_runtime["responseLost"],
                "dispatchCalls": second_runtime["dispatchCalls"],
                "matchingJobs": second_runtime["matchingJobs"],
            },
            "runtimeDiff": diff,
            "host": {
                "acceptedDecision": accepted.decision.to_dict(),
                "outcome": accepted.outcome.to_dict(),
                "handoff": handoff.to_dict(),
            },
            "workspaceClose": closed,
            "workspaceClosed": closed_confirmed,
            "checks": checks,
        }
    finally:
        if not completed:
            try:
                ensure_workspace_closed(
                    factory.client(f"{label}-failure-cleanup", initialize=True),
                    workspace_id,
                    force=True,
                )
            except Exception:
                pass


def _run_missing_artifact_fault(
    *,
    source_repo: str,
    source_revision: str,
    factory: RuntimeClientFactory,
    state_root: Path,
) -> dict[str, JsonValue]:
    identity = ScenarioIdentity.create("h5-missing-artifact")
    workspace_id = identity.workspace_id
    task_id = identity.task_id
    goal_id = identity.goal_id
    run_id = f"harness-run:{identity.token}:physical-success"
    root = state_root / "missing-artifact"
    root.mkdir(parents=True, exist_ok=True)
    completed = False
    try:
        setup = factory.client("missing-artifact-setup", initialize=True)
        catalog = discover_execution_runtime_catalog(setup)
        workspace = ensure_workspace(
            setup,
            workspace_id=workspace_id,
            source_repo=source_repo,
            source_revision=source_revision,
        )
        with HostStorage(root) as storage:
            HostKernel(
                storage,
                clock_ms=scenario_clock_ms,
                owner_id="host:h5:missing-artifact:create",
            ).create_task(
                event_id=f"event:{identity.token}:create",
                kind=EventKind.TASK_CREATED,
                task_id=task_id,
                goal_id=goal_id,
                payload={"workloadId": WORKLOAD_ID, "fault": "missing-artifact"},
                frontier=(f"node:{identity.token}:verify",),
            )
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            attempt = host.start_attempt(
                task_id,
                objective_digest=canonical_digest({"fault": "missing-artifact"}),
                acceptance_criteria_digest=canonical_digest(
                    {"requiredArtifact": "completion.json"}
                ),
            )
            context = storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.h5-missing-artifact-context",
                },
                kind="compiled-context",
            )
            assignment = host.assign(
                attempt,
                manifest=HarnessCapabilityManifest(
                    harness_id="harness:h5-missing-artifact-probe",
                    protocol="ordivon.h5-probe",
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
                source_digest=canonical_digest(
                    {"repository": source_repo, "sourceRevision": source_revision}
                ),
                required_capabilities=(
                    "persistent_session",
                    "interrupt",
                    "tool_events",
                    "usage",
                ),
                budget={"runtimeJobs": 1},
            )
        request = build_harness_workspace_exec_request(
            assignment,
            harness_run_id=run_id,
            step_id="h5-physical-success-without-artifact",
            executable="/usr/bin/python3",
            args=("-c", "print('physical success without completion artifact')"),
            timeout_ms=30_000,
            stdout_limit_bytes=65_536,
            stderr_limit_bytes=65_536,
            wait_ms=30_000,
        )
        first = factory.client("missing-artifact-delivery", initialize=True).call_tool(
            "workspace.exec", request
        )
        job_id = first.get("jobId")
        if not isinstance(job_id, str):
            raise AssertionError("missing-artifact Runtime Job omitted identity")
        observation = _observe_terminal(
            factory.client("missing-artifact-observe", initialize=True), job_id
        )
        if observation.get("status") != "succeeded":
            raise AssertionError("missing-artifact physical Runtime Job did not succeed")
        terminal_artifact = _artifact(observation, "terminal_evidence")
        terminal = _read_json_artifact(
            factory.client("missing-artifact-terminal", initialize=True),
            job_id,
            terminal_artifact,
        )
        missing_ref = ArtifactRef(
            ref=f"workspace-artifact:{workspace_id}:artifacts/completion.json",
            kind="completion",
            digest=canonical_digest({"missing": "completion.json"}),
        )
        with HostStorage(root) as storage:
            host = HarnessHost(storage, clock_ms=scenario_clock_ms)
            current = host.load_current_assignment(task_id)
            receipt = HarnessRunReceipt(
                harness_run_id=run_id,
                assignment_id=current.assignment.assignment_id,
                assignment_generation=current.assignment.generation,
                harness_id=current.assignment.target_harness_id,
                harness_revision="h5-missing-artifact-probe-v1",
                manifest_digest=current.assignment.harness_manifest_digest,
                session_ref=f"probe-session:{identity.token}",
                started_at_ms=current.assignment.created_at_ms,
                finished_at_ms=scenario_clock_ms(),
                stop_reason="completed",
                event_digest=canonical_digest(
                    {"jobId": job_id, "terminalEvidence": terminal_artifact["digest"]}
                ),
                context_digest=current.assignment.context_object_digest,
                tool_catalog_digest=current.assignment.tool_catalog_digest,
                runtime_job_refs=(job_id,),
                artifact_refs=_runtime_artifact_refs(observation),
                usage={"runtimeJobs": 1, "processExit": 0},
            )
            recorded = host.record_run(current, receipt)
            proposal = host.propose_completion(
                recorded,
                summary="Physical process succeeded but completion Artifact is absent.",
                acceptance_results={"processExit": 0},
                artifact_refs=(missing_ref,),
            )

            def forbidden_verifier(_):
                raise AssertionError("missing Artifact must reject before verifier")

            decision = host.adjudicate_completion(
                proposal,
                artifact_exists=lambda _: False,
                acceptance_verifier=forbidden_verifier,
            )
            handoff = operator_handoff(storage, task_id)
        checks = {
            "runtimeJobSucceeded": observation.get("status") == "succeeded",
            "requiredArtifactAbsent": decision.decision.reason_code
            == "missing_artifact",
            "acceptanceVerifierSkipped": decision.outcome is None,
            "taskRemainsContinuable": decision.task_state == "waiting"
            and handoff.task_state.value == "waiting",
            "runtimeMadeNoSemanticClaim": "semanticCompletion" not in terminal
            and "taskOutcome" not in terminal,
        }
        if not all(checks.values()):
            raise AssertionError(f"H5 missing-artifact checks failed: {checks}")
        closed = ensure_workspace_closed(
            factory.client("missing-artifact-close", initialize=True),
            workspace_id,
            force=True,
        )
        closed_confirmed = workspace_absent(
            factory.client("missing-artifact-close-audit", initialize=True),
            workspace_id,
        )
        if not closed_confirmed:
            raise AssertionError("missing-artifact Runtime Workspace remained open")
        completed = True
        return {
            "schemaVersion": 1,
            "kind": "ordivon.h5-missing-artifact-fault",
            "taskId": task_id,
            "goalId": goal_id,
            "workspace": workspace,
            "runtimeJobId": job_id,
            "runtimeObservation": observation,
            "terminalEvidence": terminal,
            "missingArtifactRef": missing_ref.to_dict(),
            "decision": decision.decision.to_dict(),
            "handoff": handoff.to_dict(),
            "workspaceClose": closed,
            "workspaceClosed": closed_confirmed,
            "checks": checks,
        }
    finally:
        if not completed:
            try:
                ensure_workspace_closed(
                    factory.client("missing-artifact-failure-cleanup", initialize=True),
                    workspace_id,
                    force=True,
                )
            except Exception:
                pass


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    root_identity = ScenarioIdentity.create("h5-root")
    state_root = scenario_state_root(
        args.state_root,
        prefix="h5",
        identity=root_identity,
    )
    factory = RuntimeClientFactory(
        args.endpoint,
        token,
        "ordivon-host-live-h5",
    )
    completed = False
    try:
        codex_to_hermes = _run_trajectory(
            order=("codex", "hermes"),
            source_repo=args.source_repo,
            source_revision=args.source_revision,
            factory=factory,
            state_root=state_root,
            inject_response_loss=True,
        )
        hermes_to_codex = _run_trajectory(
            order=("hermes", "codex"),
            source_repo=args.source_repo,
            source_revision=args.source_revision,
            factory=factory,
            state_root=state_root,
            inject_response_loss=False,
        )
        missing_artifact = _run_missing_artifact_fault(
            source_repo=args.source_repo,
            source_revision=args.source_revision,
            factory=factory,
            state_root=state_root,
        )
        checks = {
            "bothReplacementOrdersCompleted": all(
                trajectory["host"]["acceptedDecision"]["accepted"] is True
                for trajectory in (codex_to_hermes, hermes_to_codex)
            ),
            "bothOrdersPreservedOneTaskAttempt": all(
                trajectory["checks"]["sameTaskAttemptAcrossReplacement"] is True
                for trajectory in (codex_to_hermes, hermes_to_codex)
            ),
            "bothOrdersAdvancedAssignmentGeneration": all(
                trajectory["checks"]["generationAdvanced"] is True
                for trajectory in (codex_to_hermes, hermes_to_codex)
            ),
            "bothOrdersRejectedStaleCompletion": all(
                trajectory["faults"]["staleGeneration"]["reasonCode"]
                == "stale_assignment"
                for trajectory in (codex_to_hermes, hermes_to_codex)
            ),
            "responseLossRecoveredWithoutRedispatch": codex_to_hermes["faults"][
                "responseLost"
            ]
            is True
            and codex_to_hermes["faults"]["dispatchCalls"] == 1
            and len(codex_to_hermes["faults"]["matchingJobs"]) == 1,
            "missingArtifactPreventedFalseCompletion": missing_artifact["decision"][
                "reasonCode"
            ]
            == "missing_artifact"
            and missing_artifact["handoff"]["taskState"] == "waiting",
            "allWorkspacesClosed": codex_to_hermes["workspaceClosed"] is True
            and hermes_to_codex["workspaceClosed"] is True
            and missing_artifact["workspaceClosed"] is True,
        }
        if not all(checks.values()):
            raise AssertionError(f"H5 portfolio checks failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.harness-replacement-h5-live-receipt",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "implementationSourceRevision": args.source_revision,
            "workloadId": WORKLOAD_ID,
            "trajectories": [codex_to_hermes, hermes_to_codex],
            "faults": {
                "staleGeneration": [
                    codex_to_hermes["faults"]["staleGeneration"],
                    hermes_to_codex["faults"]["staleGeneration"],
                ],
                "missingArtifact": missing_artifact,
                "responseLoss": {
                    "trajectory": codex_to_hermes["label"],
                    "responseLost": codex_to_hermes["faults"]["responseLost"],
                    "dispatchCalls": codex_to_hermes["faults"]["dispatchCalls"],
                    "matchingJobs": codex_to_hermes["faults"]["matchingJobs"],
                },
            },
            "checks": checks,
            "stateRoot": str(state_root) if args.keep_state else None,
        }
        completed = True
        emit_receipt(receipt)
    finally:
        cleanup_state_root(state_root, keep=args.keep_state and completed)


if __name__ == "__main__":
    main()
