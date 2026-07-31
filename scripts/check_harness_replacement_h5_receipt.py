#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any

from anc_canonical import canonical_digest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from harness_replacement_h5_support import (  # noqa: E402
    COMPLETION_PATH,
    DIAGNOSIS_PATH,
    SOURCE_PATH,
    WORKLOAD_ID,
    WORKLOAD_RELATIVE,
    validate_completion,
    validate_diagnosis,
)

REPO = SCRIPT_DIR.parent
DEFAULT_RECEIPT = REPO / "evidence/harness-replacement-h5-live-76420e4-20260731.json"
EXPECTED_SOURCE_REVISION = "76420e4f1ab2d20799b09aad6497f195bd951aa7"
EXPECTED_SOURCE_RECEIPT_DIGEST = (
    "sha256:8b8f96f8844ded3512e37fac36342c188e2e774015855b1d639bffb9f9fe3387"
)
EXPECTED_TOP_CHECKS = {
    "allWorkspacesClosed",
    "bothOrdersAdvancedAssignmentGeneration",
    "bothOrdersPreservedOneTaskAttempt",
    "bothOrdersRejectedStaleCompletion",
    "bothReplacementOrdersCompleted",
    "missingArtifactPreventedFalseCompletion",
    "responseLossRecoveredWithoutRedispatch",
}
EXPECTED_TRAJECTORY_CHECKS = {
    "completionBindsDiagnosis",
    "completionBindsSource",
    "diagnosisPassedAsArtifact",
    "freshContextCompiled",
    "frozenWorkloadBoundaryPreserved",
    "generationAdvanced",
    "handoffIsTerminal",
    "hostCommittedTaskOutcome",
    "independentAcceptanceSucceeded",
    "noBlindRedispatch",
    "providerSessionNotTaskIdentity",
    "repairRuntimeSucceeded",
    "responseLossBehaviorMatchesTrajectory",
    "runtimeEnvironmentByproductsBounded",
    "runtimeNeverClaimedSemanticCompletion",
    "sameTaskAttemptAcrossReplacement",
    "staleDecisionDispatchedNoRuntimeWork",
    "staleProposalRejected",
}
EXPECTED_MISSING_ARTIFACT_CHECKS = {
    "acceptanceVerifierSkipped",
    "requiredArtifactAbsent",
    "runtimeJobSucceeded",
    "runtimeMadeNoSemanticClaim",
    "taskRemainsContinuable",
}
EXPECTED_ORDERS = {
    "codex-to-hermes": ("codex", "hermes"),
    "hermes-to-codex": ("hermes", "codex"),
}
EXPECTED_MODELS = {
    "codex": ("openai", "gpt-5.6-sol"),
    "hermes": ("deepseek", "deepseek-v4-pro"),
}
EXPECTED_REFERENCE_TYPES = ["assignment", "harness_run", "task", "task_attempt"]
FORBIDDEN_COMPACT_KEYS = {
    "assistantText",
    "normalizedEvents",
    "reasoning_content",
    "thoughtText",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _sha256(value: Any, label: str) -> str:
    text = _string(value, label)
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return text


def _all_true(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    checks = _object(value, label)
    if set(checks) != expected:
        raise ValueError(f"{label} set differs")
    failed = sorted(name for name, result in checks.items() if result is not True)
    if failed:
        raise ValueError(f"{label} failed: {failed}")
    return checks


def _walk_keys(value: Any, path: str = "receipt") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_COMPACT_KEYS & set(value)
        if forbidden:
            raise ValueError(f"{path} retains forbidden compact fields: {sorted(forbidden)}")
        for key, item in value.items():
            _walk_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_keys(item, f"{path}[{index}]")


def _validate_reference(
    value: Any,
    *,
    reference_type: str,
    expected_id: str,
    label: str,
) -> dict[str, Any]:
    reference = _object(value, label)
    if reference.get("namespace") != "ordivon.host":
        raise ValueError(f"{label} namespace differs")
    if reference.get("type") != reference_type:
        raise ValueError(f"{label} type differs")
    if reference.get("id") != expected_id:
        raise ValueError(f"{label} identity differs")
    _sha256(reference.get("digest"), f"{label} digest")
    return reference


def _validate_final_response(
    run: dict[str, Any],
    *,
    provider: str,
    phase: str,
) -> None:
    canonical = _object(run.get("canonicalFinalResponse"), "canonical final response")
    expected_artifact = str(DIAGNOSIS_PATH if phase == "diagnose" else COMPLETION_PATH)
    expected_status = "failing" if phase == "diagnose" else "passed"
    if set(canonical) != {"phase", "artifact", "summary", "testStatus"}:
        raise ValueError("canonical final response fields differ")
    if canonical.get("phase") != phase:
        raise ValueError("canonical final response phase differs")
    if canonical.get("artifact") != expected_artifact:
        raise ValueError("canonical final response Artifact differs")
    if canonical.get("testStatus") != expected_status:
        raise ValueError("canonical final response test status differs")
    _string(canonical.get("summary"), "canonical final response summary")

    provider_response = run.get("providerFinalResponse")
    if provider == "codex":
        if run.get("providerFinalResponseUsable") is not True:
            raise ValueError("Codex final response must remain usable evidence")
        if run.get("finalResponseSource") != "provider":
            raise ValueError("Codex final response source differs")
        response = _object(provider_response, "Codex provider final response")
        for field in ("phase", "artifact", "testStatus"):
            if response.get(field) != canonical.get(field):
                raise ValueError(f"Codex provider final response {field} differs")
        _string(response.get("summary"), "Codex provider final response summary")
    else:
        if provider_response is not None:
            raise ValueError("Hermes provider final response should be absent in H5 evidence")
        if run.get("providerFinalResponseUsable") is not False:
            raise ValueError("Hermes final response usability differs")
        if run.get("finalResponseSource") != "verified-artifact":
            raise ValueError("Hermes final response source differs")


def _validate_provider_summary(
    run: dict[str, Any],
    *,
    provider: str,
) -> None:
    summary = _object(run.get("providerSummary"), f"{provider} provider summary")
    if "result" in summary:
        raise ValueError("compact provider summary retains full Provider result")
    if summary.get("provider") != provider:
        raise ValueError("provider summary identity differs")
    if summary.get("status") != "completed" or summary.get("stopReason") != "completed":
        raise ValueError("Provider Run did not complete")
    expected_provider, expected_model = EXPECTED_MODELS[provider]
    if summary.get("modelProvider") != expected_provider:
        raise ValueError("Provider model namespace differs")
    if summary.get("model") != expected_model:
        raise ValueError("Provider model identity differs")
    _sha256(summary.get("rawMessageDigest"), "raw Provider message digest")
    _sha256(summary.get("normalizedEventDigest"), "normalized event digest")
    _integer(summary.get("rawMessageCount"), "raw Provider message count", minimum=1)
    tools = _array(summary.get("toolItems"), "Provider Tool items")
    if not tools:
        raise ValueError("Provider Tool evidence is empty")
    usage = _object(summary.get("usage"), "Provider usage")

    session_ref = _string(run.get("sessionRef"), "Provider Session reference")
    if provider == "codex":
        if not session_ref.startswith("codex-thread:"):
            raise ValueError("Codex Session reference differs")
        thread_id = _string(summary.get("threadId"), "Codex Thread identity")
        _string(summary.get("turnId"), "Codex Turn identity")
        if session_ref != f"codex-thread:{thread_id}":
            raise ValueError("Codex Thread reference differs")
        total = _object(usage.get("total"), "Codex total usage")
        _integer(total.get("totalTokens"), "Codex total tokens", minimum=1)
    else:
        if summary.get("providerStopReason") != "end_turn":
            raise ValueError("Hermes Provider stop reason differs")
        session_id = _string(summary.get("sessionId"), "Hermes Session identity")
        if session_ref != f"hermes-acp-session:{session_id}":
            raise ValueError("Hermes Session reference differs")
        provenance = _object(summary.get("sessionProvenance"), "Hermes Session provenance")
        if canonical_digest(provenance) != summary.get("sessionProvenanceDigest"):
            raise ValueError("Hermes Session provenance digest differs")
        for field in ("acpSessionId", "currentHermesSessionId", "rootHermesSessionId"):
            if provenance.get(field) != session_id:
                raise ValueError(f"Hermes Session provenance {field} differs")
        thought_events = _integer(
            summary.get("thoughtEventCount"), "Hermes thought event count", minimum=1
        )
        updates = _object(summary.get("updateTypeCounts"), "Hermes update counts")
        if updates.get("agent_thought_chunk") != thought_events:
            raise ValueError("Hermes thought event and update counts differ")
        _integer(usage.get("thoughtTokens"), "Hermes thought tokens", minimum=1)
        _integer(usage.get("totalTokens"), "Hermes total tokens", minimum=1)


def _validate_runtime_evidence(
    run: dict[str, Any],
    *,
    assignment: dict[str, Any],
    task_id: str,
    task_attempt_id: str,
) -> None:
    job_ids = _array(run.get("runtimeJobIds"), "Runtime Job identities")
    if not job_ids or any(not isinstance(job, str) or not job.startswith("job-") for job in job_ids):
        raise ValueError("Runtime Job identities are invalid")
    terminal = _object(run.get("runtimeTerminalEvidence"), "Runtime Terminal Evidence")
    if terminal.get("jobId") != job_ids[0]:
        raise ValueError("Runtime Terminal Evidence Job differs")
    _string(terminal.get("attemptId"), "Runtime Attempt identity")
    if terminal.get("sourceRevision") != EXPECTED_SOURCE_REVISION:
        raise ValueError("Runtime source revision differs")
    if terminal.get("executionDisposition") != "succeeded":
        raise ValueError("Runtime execution disposition differs")
    if terminal.get("processTreeDisposition") != "terminal_clean":
        raise ValueError("Runtime process tree was not terminal clean")
    if "semanticCompletion" in terminal or "taskOutcome" in terminal:
        raise ValueError("Runtime Terminal Evidence claims semantic completion")
    references = _array(terminal.get("foreignReferences"), "Runtime Host references")
    types = [_object(reference, "Runtime Host reference").get("type") for reference in references]
    if types != EXPECTED_REFERENCE_TYPES:
        raise ValueError("Runtime Host reference types or order differ")
    assignment_ref = _validate_reference(
        references[0],
        reference_type="assignment",
        expected_id=_string(assignment.get("assignmentId"), "Assignment identity"),
        label="Runtime Assignment reference",
    )
    if assignment_ref.get("generation") != str(assignment.get("generation")):
        raise ValueError("Runtime Assignment reference generation differs")
    _validate_reference(
        references[1],
        reference_type="harness_run",
        expected_id=_string(run.get("harnessRunId"), "Harness Run identity"),
        label="Runtime Harness Run reference",
    )
    _validate_reference(
        references[2],
        reference_type="task",
        expected_id=task_id,
        label="Runtime Task reference",
    )
    _validate_reference(
        references[3],
        reference_type="task_attempt",
        expected_id=task_attempt_id,
        label="Runtime Task Attempt reference",
    )


def _validate_trajectory(value: Any) -> dict[str, Any]:
    trajectory = _object(value, "replacement trajectory")
    label = _string(trajectory.get("label"), "trajectory label")
    if label not in EXPECTED_ORDERS:
        raise ValueError(f"unexpected trajectory label: {label}")
    expected_order = EXPECTED_ORDERS[label]
    if trajectory.get("providerOrder") != list(expected_order):
        raise ValueError(f"{label} provider order differs")
    _all_true(trajectory.get("checks"), EXPECTED_TRAJECTORY_CHECKS, f"{label} checks")
    if trajectory.get("workspaceClosed") is not True:
        raise ValueError(f"{label} Workspace remained open")
    if trajectory.get("runtimeEnvironmentByproducts") != ["uv.lock"]:
        raise ValueError(f"{label} Runtime environment byproducts differ")

    task_id = _string(trajectory.get("taskId"), f"{label} Task identity")
    task_attempt_id = _string(
        trajectory.get("taskAttemptId"), f"{label} Task Attempt identity"
    )
    assignments = _array(trajectory.get("assignments"), f"{label} Assignments")
    if len(assignments) != 2:
        raise ValueError(f"{label} must retain exactly two Assignments")
    first = _object(assignments[0], f"{label} first Assignment")
    second = _object(assignments[1], f"{label} second Assignment")
    if first.get("provider") != expected_order[0] or second.get("provider") != expected_order[1]:
        raise ValueError(f"{label} Assignment provider order differs")
    if first.get("generation") != 1 or second.get("generation") != 2:
        raise ValueError(f"{label} Assignment generation differs")
    if first.get("contextObjectDigest") == second.get("contextObjectDigest"):
        raise ValueError(f"{label} replacement reused the old Context")
    _sha256(first.get("contextObjectDigest"), f"{label} first Context digest")
    _sha256(second.get("contextObjectDigest"), f"{label} second Context digest")
    for assignment in assignments:
        assignment_id = _string(assignment.get("assignmentId"), f"{label} Assignment identity")
        if task_attempt_id.replace("task-attempt:", "") .split(":1", 1)[0] not in assignment_id:
            raise ValueError(f"{label} Assignment is not bound to the Task Attempt")

    diagnosis = _object(trajectory.get("diagnosis"), f"{label} diagnosis")
    diagnosis_value = _object(diagnosis.get("value"), f"{label} diagnosis value")
    validate_diagnosis(
        diagnosis_value,
        base_source_revision=EXPECTED_SOURCE_REVISION,
    )
    diagnosis_digest = canonical_digest(diagnosis_value)
    if diagnosis.get("semanticDigest") != diagnosis_digest:
        raise ValueError(f"{label} diagnosis semantic digest differs")
    _sha256(diagnosis.get("textDigest"), f"{label} diagnosis text digest")
    diagnosis_ref = _object(diagnosis.get("artifactRef"), f"{label} diagnosis reference")
    if diagnosis_ref.get("kind") != "diagnosis" or diagnosis_ref.get("digest") != diagnosis_digest:
        raise ValueError(f"{label} diagnosis Artifact reference differs")
    if second.get("priorArtifactRefs") != [diagnosis_ref]:
        raise ValueError(f"{label} replacement Assignment omitted diagnosis evidence")

    completion = _object(trajectory.get("completion"), f"{label} completion")
    completion_value = _object(completion.get("value"), f"{label} completion value")
    final_source_digest = _sha256(
        completion.get("finalSourceDigest"), f"{label} final source digest"
    )
    validate_completion(
        completion_value,
        base_source_revision=EXPECTED_SOURCE_REVISION,
        diagnosis_digest=diagnosis_digest,
        final_source_digest=final_source_digest,
    )
    completion_digest = canonical_digest(completion_value)
    if completion.get("semanticDigest") != completion_digest:
        raise ValueError(f"{label} completion semantic digest differs")
    _sha256(completion.get("textDigest"), f"{label} completion text digest")
    completion_ref = _object(
        completion.get("artifactRef"), f"{label} completion reference"
    )
    if completion_ref.get("kind") != "completion" or completion_ref.get("digest") != completion_digest:
        raise ValueError(f"{label} completion Artifact reference differs")

    runs = _array(trajectory.get("runs"), f"{label} Harness Runs")
    if len(runs) != 2:
        raise ValueError(f"{label} must retain exactly two Harness Runs")
    for index, run_value in enumerate(runs):
        run = _object(run_value, f"{label} Harness Run {index + 1}")
        provider = expected_order[index]
        phase = "diagnose" if index == 0 else "repair"
        if run.get("provider") != provider:
            raise ValueError(f"{label} Harness Run provider differs")
        _sha256(run.get("workerPayloadDigest"), f"{label} worker payload digest")
        _validate_provider_summary(run, provider=provider)
        _validate_final_response(run, provider=provider, phase=phase)
        _validate_runtime_evidence(
            run,
            assignment=assignments[index],
            task_id=task_id,
            task_attempt_id=task_attempt_id,
        )
        session_ref = _string(run.get("sessionRef"), f"{label} Session reference")
        if session_ref in {
            task_id,
            task_attempt_id,
            str(assignments[index].get("assignmentId")),
        }:
            raise ValueError(f"{label} Provider Session became Host work identity")

    runtime_diff = _object(trajectory.get("runtimeDiff"), f"{label} Runtime diff")
    diff_text = _string(runtime_diff.get("diff"), f"{label} Runtime diff text")
    allocation_path = str(WORKLOAD_RELATIVE / SOURCE_PATH)
    diagnosis_path = str(WORKLOAD_RELATIVE / DIAGNOSIS_PATH)
    completion_path = str(WORKLOAD_RELATIVE / COMPLETION_PATH)
    marker = f"diff --git a/{allocation_path} b/{allocation_path}"
    if diff_text.count("diff --git ") != 1 or marker not in diff_text:
        raise ValueError(f"{label} Runtime diff changed a non-workload source")
    if str(WORKLOAD_RELATIVE / "SPEC.md") in diff_text:
        raise ValueError(f"{label} Runtime diff changed SPEC.md")
    if str(WORKLOAD_RELATIVE / "test_allocation.py") in diff_text:
        raise ValueError(f"{label} Runtime diff changed frozen tests")
    if set(_array(runtime_diff.get("untrackedPaths"), f"{label} untracked paths")) != {
        diagnosis_path,
        completion_path,
        "uv.lock",
    }:
        raise ValueError(f"{label} Runtime untracked paths differ")

    faults = _object(trajectory.get("faults"), f"{label} fault evidence")
    stale = _object(faults.get("staleGeneration"), f"{label} stale decision")
    if stale.get("accepted") is not False or stale.get("reasonCode") != "stale_assignment":
        raise ValueError(f"{label} stale Assignment was not rejected")
    if faults.get("dispatchCalls") != 1:
        raise ValueError(f"{label} Runtime dispatch count differs")
    matching = _array(faults.get("matchingJobs"), f"{label} matching Runtime Jobs")
    if len(matching) != 1:
        raise ValueError(f"{label} did not retain exactly one matching Runtime Job")
    if matching[0].get("jobId") != runs[1]["runtimeJobIds"][0]:
        raise ValueError(f"{label} response-loss recovery found another Job")
    expected_loss = label == "codex-to-hermes"
    if faults.get("responseLossInjected") is not expected_loss:
        raise ValueError(f"{label} response-loss injection flag differs")
    if faults.get("responseLost") is not expected_loss:
        raise ValueError(f"{label} response-loss observation differs")

    host = _object(trajectory.get("host"), f"{label} Host evidence")
    decision = _object(host.get("acceptedDecision"), f"{label} accepted decision")
    if decision.get("accepted") is not True or decision.get("reasonCode") != "accepted":
        raise ValueError(f"{label} current CompletionProposal was not accepted")
    outcome = _object(host.get("outcome"), f"{label} TaskOutcome")
    if outcome.get("kind") != "ordivon.task-outcome" or outcome.get("status") != "completed":
        raise ValueError(f"{label} TaskOutcome differs")
    if outcome.get("taskId") != task_id:
        raise ValueError(f"{label} TaskOutcome Task differs")
    if outcome.get("verificationDigest") != decision.get("verificationDigest"):
        raise ValueError(f"{label} TaskOutcome verification digest differs")
    outcome_refs = _array(outcome.get("artifactRefs"), f"{label} TaskOutcome Artifacts")
    if len(outcome_refs) != 2:
        raise ValueError(f"{label} TaskOutcome Artifact set differs")
    if outcome_refs[0] != completion_ref:
        raise ValueError(f"{label} TaskOutcome completion Artifact differs")
    if outcome_refs[1].get("kind") != "final-source" or outcome_refs[1].get("digest") != final_source_digest:
        raise ValueError(f"{label} TaskOutcome final source Artifact differs")
    handoff = _object(host.get("handoff"), f"{label} terminal handoff")
    if handoff.get("taskState") != "completed" or handoff.get("outcomeObjectDigest") is None:
        raise ValueError(f"{label} terminal handoff differs")
    if handoff.get("taskAttemptId") != task_attempt_id:
        raise ValueError(f"{label} terminal handoff Task Attempt differs")
    if handoff.get("assignmentGeneration") != 2:
        raise ValueError(f"{label} terminal handoff Assignment generation differs")
    if handoff.get("harnessRunId") != runs[1].get("harnessRunId"):
        raise ValueError(f"{label} terminal handoff Harness Run differs")

    return {
        "label": label,
        "providerOrder": list(expected_order),
        "taskId": task_id,
        "taskAttemptId": task_attempt_id,
        "finalSourceDigest": final_source_digest,
        "diagnosisDigest": diagnosis_digest,
        "completionDigest": completion_digest,
        "runtimeJobIds": [job for run in runs for job in run["runtimeJobIds"]],
        "providerFinalSources": [run["finalResponseSource"] for run in runs],
    }


def _validate_missing_artifact(value: Any) -> dict[str, Any]:
    fault = _object(value, "missing-Artifact fault")
    _all_true(
        fault.get("checks"),
        EXPECTED_MISSING_ARTIFACT_CHECKS,
        "missing-Artifact checks",
    )
    if fault.get("workspaceClosed") is not True:
        raise ValueError("missing-Artifact Workspace remained open")
    decision = _object(fault.get("decision"), "missing-Artifact decision")
    if decision.get("accepted") is not False or decision.get("reasonCode") != "missing_artifact":
        raise ValueError("missing Artifact did not prevent false completion")
    handoff = _object(fault.get("handoff"), "missing-Artifact handoff")
    if handoff.get("taskState") != "waiting" or handoff.get("outcomeObjectDigest") is not None:
        raise ValueError("missing-Artifact Task is not continuable")
    terminal = _object(fault.get("terminalEvidence"), "missing-Artifact Terminal Evidence")
    if terminal.get("executionDisposition") != "succeeded":
        raise ValueError("missing-Artifact physical process did not succeed")
    if terminal.get("processTreeDisposition") != "terminal_clean":
        raise ValueError("missing-Artifact process tree was not terminal clean")
    if terminal.get("sourceRevision") != EXPECTED_SOURCE_REVISION:
        raise ValueError("missing-Artifact source revision differs")
    if "semanticCompletion" in terminal or "taskOutcome" in terminal:
        raise ValueError("missing-Artifact Runtime evidence claims semantic completion")
    job_id = _string(fault.get("runtimeJobId"), "missing-Artifact Runtime Job")
    if terminal.get("jobId") != job_id:
        raise ValueError("missing-Artifact Terminal Evidence Job differs")
    observation = _object(fault.get("runtimeObservation"), "missing-Artifact observation")
    if observation.get("jobId") != job_id or observation.get("status") != "succeeded":
        raise ValueError("missing-Artifact Runtime observation differs")
    missing_ref = _object(fault.get("missingArtifactRef"), "missing Artifact reference")
    if missing_ref.get("kind") != "completion":
        raise ValueError("missing Artifact kind differs")
    _sha256(missing_ref.get("digest"), "missing Artifact digest")
    return {"jobId": job_id, "taskState": handoff["taskState"]}


def validate_receipt(path: str | Path = DEFAULT_RECEIPT) -> dict[str, Any]:
    receipt_path = Path(path)
    receipt = _object(json.loads(receipt_path.read_text(encoding="utf-8")), "receipt")
    if receipt.get("schemaVersion") != 1:
        raise ValueError("receipt schemaVersion differs")
    if receipt.get("kind") != "ordivon.harness-replacement-h5-live-receipt":
        raise ValueError("receipt kind differs")
    if receipt.get("workloadId") != WORKLOAD_ID:
        raise ValueError("receipt workload identity differs")
    if receipt.get("implementationSourceRevision") != EXPECTED_SOURCE_REVISION:
        raise ValueError("implementation source revision differs")
    if receipt.get("sourceReceiptPayloadDigest") != EXPECTED_SOURCE_RECEIPT_DIGEST:
        raise ValueError("source receipt payload digest differs")
    _all_true(receipt.get("checks"), EXPECTED_TOP_CHECKS, "portfolio checks")

    integrity = _object(receipt.get("integrity"), "receipt integrity")
    if integrity.get("algorithm") != "sha256":
        raise ValueError("receipt integrity algorithm differs")
    if integrity.get("canonicalization") != "ordivon-canonical-json-v1":
        raise ValueError("receipt canonicalization differs")
    payload = copy.deepcopy(receipt)
    payload.pop("integrity")
    if integrity.get("payloadDigest") != canonical_digest(payload):
        raise ValueError("receipt integrity digest differs")
    _walk_keys(receipt)

    trajectories = _array(receipt.get("trajectories"), "replacement trajectories")
    if len(trajectories) != 2:
        raise ValueError("receipt must retain exactly two replacement trajectories")
    summaries = [_validate_trajectory(value) for value in trajectories]
    if {summary["label"] for summary in summaries} != set(EXPECTED_ORDERS):
        raise ValueError("replacement trajectory set differs")
    if summaries[0]["finalSourceDigest"] == summaries[1]["finalSourceDigest"]:
        raise ValueError("H5 failed to retain distinct accepted source implementations")

    faults = _object(receipt.get("faults"), "portfolio fault evidence")
    stale = _array(faults.get("staleGeneration"), "portfolio stale decisions")
    if len(stale) != 2 or any(
        not isinstance(value, dict)
        or value.get("accepted") is not False
        or value.get("reasonCode") != "stale_assignment"
        for value in stale
    ):
        raise ValueError("portfolio stale-generation evidence differs")
    response_loss = _object(faults.get("responseLoss"), "portfolio response-loss evidence")
    if response_loss.get("trajectory") != "codex-to-hermes":
        raise ValueError("response-loss trajectory differs")
    if response_loss.get("responseLost") is not True:
        raise ValueError("response-loss fault was not observed")
    if response_loss.get("dispatchCalls") != 1:
        raise ValueError("response-loss recovery redispatched Runtime work")
    if len(_array(response_loss.get("matchingJobs"), "response-loss matching Jobs")) != 1:
        raise ValueError("response-loss recovery found multiple Runtime Jobs")
    missing = _validate_missing_artifact(faults.get("missingArtifact"))

    return {
        "receipt": str(receipt_path),
        "payloadDigest": integrity["payloadDigest"],
        "sourceReceiptPayloadDigest": receipt["sourceReceiptPayloadDigest"],
        "implementationSourceRevision": receipt["implementationSourceRevision"],
        "trajectories": summaries,
        "missingArtifact": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the committed Host H5 replacement and fault receipt."
    )
    parser.add_argument("receipt", nargs="?", default=str(DEFAULT_RECEIPT))
    args = parser.parse_args()
    print(json.dumps(validate_receipt(args.receipt), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
