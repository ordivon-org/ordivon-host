#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from anc_canonical import canonical_digest

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = REPO / "evidence/codex-app-h3-live-64ab44b-20260731.json"
EXPECTED_SOURCE_REVISION = "64ab44be667fa172027150a152b2f4660538ef00"
EXPECTED_REFERENCE_TYPES = ["assignment", "harness_run", "task", "task_attempt"]
EXPECTED_CHECKS = {
    "codexTurnCompleted",
    "freshClientRecoveredJob",
    "handoffProjectsHarnessRun",
    "hostRecordedHarnessRun",
    "noRuntimeSemanticCompletionClaim",
    "oneShotBaselineRemainsDistinct",
    "rawProviderDigestRetained",
    "runtimeJobSucceeded",
    "runtimeOwnedProcessTree",
    "taskNotSemanticallyCompleted",
    "terminalEvidenceBoundSource",
    "terminalEvidenceRetainedHostReferences",
    "threadIdentityRetained",
    "toolLifecycleObserved",
    "turnIdentityRetained",
    "usageObserved",
    "workerChecksPassed",
}
EXPECTED_WORKER_CHECKS = {
    "commandExecutionObserved",
    "commandSucceeded",
    "interruptCapabilityAdvertised",
    "noFileChange",
    "rawProviderDigestRetained",
    "resumeCapabilityAdvertised",
    "structuredOutputMatchesTarget",
    "targetMentionedByCommand",
    "turnCompleted",
    "usageObserved",
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


def validate_receipt(path: str | Path = DEFAULT_RECEIPT) -> dict[str, Any]:
    receipt_path = Path(path)
    receipt = _object(json.loads(receipt_path.read_text(encoding="utf-8")), "receipt")
    if receipt.get("schemaVersion") != 1:
        raise ValueError("receipt schemaVersion differs")
    if receipt.get("kind") != "ordivon.codex-app-h3-live-receipt":
        raise ValueError("receipt kind differs")

    integrity = _object(receipt.get("integrity"), "integrity")
    if integrity.get("algorithm") != "sha256":
        raise ValueError("receipt integrity algorithm differs")
    if integrity.get("canonicalization") != "ordivon-canonical-json-v1":
        raise ValueError("receipt canonicalization differs")
    payload = copy.deepcopy(receipt)
    payload.pop("integrity")
    if integrity.get("payloadDigest") != canonical_digest(payload):
        raise ValueError("receipt integrity digest differs")
    _sha256(receipt.get("sourceReceiptPayloadDigest"), "source receipt payload digest")
    _all_true(receipt.get("checks"), EXPECTED_CHECKS, "live checks")

    source_revision = _string(
        receipt.get("implementationSourceRevision"), "implementation source revision"
    )
    if source_revision != EXPECTED_SOURCE_REVISION:
        raise ValueError("implementation source revision differs")
    if receipt.get("assignmentGeneration") != 1:
        raise ValueError("H3 Assignment generation differs")

    manifest = _object(receipt.get("manifest"), "Harness manifest")
    if manifest.get("harnessId") != "harness:codex-app-server":
        raise ValueError("Harness manifest identity differs")
    if manifest.get("protocol") != "codex-app-server-v2-stdio":
        raise ValueError("Harness protocol differs")
    if manifest.get("protocolRevision") != "0.145.0":
        raise ValueError("Harness protocol revision differs")
    for field in (
        "persistentSession",
        "sessionResume",
        "sessionFork",
        "interrupt",
        "toolEvents",
        "approvalEvents",
        "usage",
    ):
        if manifest.get(field) is not True:
            raise ValueError(f"Harness manifest capability {field} differs")

    provider = _object(receipt.get("provider"), "provider summary")
    if provider.get("kind") != "ordivon.codex-app-h3-provider-summary":
        raise ValueError("provider summary kind differs")
    _sha256(provider.get("workerPayloadDigest"), "worker payload digest")
    _all_true(provider.get("workerChecks"), EXPECTED_WORKER_CHECKS, "worker checks")
    target = _string(provider.get("target"), "provider target")
    structured = _object(provider.get("structuredOutput"), "structured output")
    if structured.get("file") != target:
        raise ValueError("structured output target differs")
    command_text = _string(structured.get("observedCommand"), "observed command")
    if target not in command_text:
        raise ValueError("structured output command omitted target")

    baseline = _object(provider.get("baselineComparison"), "baseline comparison")
    one_shot = _object(baseline.get("codexCliOneShot"), "one-shot baseline")
    app_server = _object(baseline.get("codexAppServer"), "App Server baseline")
    for field in (
        "persistentSessionRetained",
        "providerThreadIdentity",
        "interrupt",
        "toolLifecycleEvents",
        "rawProviderEventDigest",
    ):
        if one_shot.get(field) is not False or app_server.get(field) is not True:
            raise ValueError(f"baseline distinction {field} differs")

    turn = _object(provider.get("turn"), "provider Turn")
    thread_id = _string(turn.get("threadId"), "Codex Thread identity")
    session_id = _string(turn.get("sessionId"), "Codex Session identity")
    turn_id = _string(turn.get("turnId"), "Codex Turn identity")
    if thread_id != session_id:
        raise ValueError("H3 Thread and Session identity differ")
    if turn.get("cliVersion") != "0.145.0":
        raise ValueError("Codex CLI version differs")
    if turn.get("status") != "completed" or turn.get("stopReason") != "completed":
        raise ValueError("Codex Turn did not complete")
    _sha256(turn.get("rawMessageDigest"), "raw provider message digest")
    _sha256(turn.get("normalizedEventDigest"), "normalized event digest")
    if type(turn.get("rawMessageCount")) is not int or turn["rawMessageCount"] < 1:
        raise ValueError("raw provider message count is invalid")
    usage = _object(turn.get("usage"), "Codex usage")
    total_usage = _object(usage.get("total"), "Codex total usage")
    if type(total_usage.get("totalTokens")) is not int or total_usage["totalTokens"] < 1:
        raise ValueError("Codex token usage is invalid")
    method_counts = _object(turn.get("providerMethodCounts"), "provider method counts")
    if method_counts.get("turn/completed") != 1:
        raise ValueError("provider turn/completed count differs")
    if not isinstance(method_counts.get("thread/tokenUsage/updated"), int) or method_counts["thread/tokenUsage/updated"] < 1:
        raise ValueError("provider usage event was not retained")
    if not isinstance(method_counts.get("item/started"), int) or method_counts["item/started"] < 1:
        raise ValueError("provider item/started lifecycle is missing")
    if not isinstance(method_counts.get("item/completed"), int) or method_counts["item/completed"] < 1:
        raise ValueError("provider item/completed lifecycle is missing")
    item_counts = _object(turn.get("itemTypeCounts"), "provider item counts")
    if item_counts.get("commandExecution", 0) < 2:
        raise ValueError("commandExecution lifecycle count differs")
    if item_counts.get("fileChange", 0) != 0:
        raise ValueError("H3 unexpectedly retained a fileChange")
    tool_items = _array(turn.get("toolItems"), "provider Tool items")
    if len(tool_items) != 1:
        raise ValueError("H3 did not retain exactly one completed Tool item")
    tool_item = _object(tool_items[0], "provider Tool item")
    if tool_item.get("type") != "commandExecution" or tool_item.get("exitCode") != 0:
        raise ValueError("H3 commandExecution did not succeed")
    if target not in _string(tool_item.get("command"), "provider Tool command"):
        raise ValueError("provider Tool command omitted target")

    request = _object(receipt.get("request"), "Runtime request")
    execution = _object(request.get("execution"), "Runtime execution")
    references = _array(execution.get("foreignReferences"), "Host references")
    reference_types = [
        _object(reference, "Host reference").get("type") for reference in references
    ]
    if reference_types != EXPECTED_REFERENCE_TYPES:
        raise ValueError("Host reference order or types differ")
    if any(reference.get("namespace") != "ordivon.host" for reference in references):
        raise ValueError("Host reference namespace differs")

    runtime = _object(receipt.get("runtime"), "Runtime evidence")
    if runtime.get("status") != "succeeded" or runtime.get("workspaceClosed") is not True:
        raise ValueError("Runtime Job or Workspace disposition differs")
    job_id = _string(runtime.get("jobId"), "Runtime Job identity")
    attempt_id = _string(runtime.get("attemptId"), "Runtime Attempt identity")
    terminal = _object(runtime.get("terminalEvidence"), "Terminal Evidence")
    if terminal.get("jobId") != job_id or terminal.get("attemptId") != attempt_id:
        raise ValueError("Terminal Evidence Job or Attempt differs")
    if terminal.get("foreignReferences") != references:
        raise ValueError("Terminal Evidence Host references differ")
    if terminal.get("sourceRevision") != source_revision:
        raise ValueError("Terminal Evidence source revision differs")
    if terminal.get("executionDisposition") != "succeeded":
        raise ValueError("Runtime execution disposition differs")
    if terminal.get("processTreeDisposition") != "terminal_clean":
        raise ValueError("Runtime process tree was not terminal clean")
    if "semanticCompletion" in terminal or "taskOutcome" in terminal:
        raise ValueError("Runtime Terminal Evidence claims semantic completion")
    jobs = _array(runtime.get("matchingJobs"), "matching Runtime Jobs")
    if len(jobs) != 1:
        raise ValueError("H3 did not retain exactly one Runtime Job")
    matching_job = _object(jobs[0], "matching Runtime Job")
    if matching_job.get("jobId") != job_id or matching_job.get("attemptId") != attempt_id:
        raise ValueError("freshly recovered Runtime Job differs")
    if matching_job.get("clientRequestId") != request.get("clientRequestId"):
        raise ValueError("freshly recovered request identity differs")
    artifacts = _array(runtime.get("artifacts"), "Runtime Artifacts")
    artifact_kinds = {
        _object(artifact, "Runtime Artifact").get("kind") for artifact in artifacts
    }
    if not {"stdout", "stderr", "execution_result", "terminal_evidence"}.issubset(
        artifact_kinds
    ):
        raise ValueError("Runtime Artifact set is incomplete")

    host = _object(receipt.get("host"), "Host evidence")
    recorded = _object(host.get("recordedRun"), "HarnessRunReceipt")
    run_id = _string(receipt.get("harnessRunId"), "Harness Run identity")
    if recorded.get("harnessRunId") != run_id:
        raise ValueError("recorded Harness Run identity differs")
    if recorded.get("assignmentId") != receipt.get("assignmentId"):
        raise ValueError("recorded Assignment identity differs")
    if recorded.get("assignmentGeneration") != receipt.get("assignmentGeneration"):
        raise ValueError("recorded Assignment generation differs")
    if recorded.get("sessionRef") != f"codex-thread:{thread_id}":
        raise ValueError("recorded Codex Thread reference differs")
    if recorded.get("eventDigest") != turn.get("rawMessageDigest"):
        raise ValueError("recorded provider event digest differs")
    if recorded.get("runtimeJobRefs") != [job_id]:
        raise ValueError("recorded Runtime Job reference differs")
    if recorded.get("stopReason") != "completed":
        raise ValueError("recorded Harness stop reason differs")
    recorded_usage = _object(recorded.get("usage"), "recorded Harness usage")
    if recorded_usage.get("turnId") != turn_id or recorded_usage.get("model") != turn.get("model"):
        raise ValueError("recorded provider identity differs")
    recorded_artifacts = _array(recorded.get("artifactRefs"), "recorded Artifacts")
    if not any(
        isinstance(item, dict)
        and item.get("kind") == "codex-app-h3-worker-result"
        and str(item.get("ref", "")).startswith("host-object:sha256:")
        for item in recorded_artifacts
    ):
        raise ValueError("Host CAS worker-result reference is missing")
    if not any(
        isinstance(item, dict) and item.get("kind") == "terminal_evidence"
        for item in recorded_artifacts
    ):
        raise ValueError("recorded Terminal Evidence reference is missing")

    handoff = _object(host.get("handoff"), "Operator handoff")
    if handoff.get("harnessRunId") != run_id:
        raise ValueError("Operator handoff Harness Run differs")
    if handoff.get("taskState") != "waiting" or handoff.get("outcomeObjectDigest") is not None:
        raise ValueError("H3 incorrectly committed semantic Task completion")

    return {
        "receipt": str(receipt_path),
        "payloadDigest": integrity["payloadDigest"],
        "sourceReceiptPayloadDigest": receipt["sourceReceiptPayloadDigest"],
        "jobId": job_id,
        "attemptId": attempt_id,
        "threadId": thread_id,
        "turnId": turn_id,
        "model": turn.get("model"),
        "rawMessageCount": turn["rawMessageCount"],
        "toolCommand": tool_item["command"],
        "taskState": handoff["taskState"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the compact committed Codex App Server H3 receipt."
    )
    parser.add_argument("receipt", nargs="?", default=str(DEFAULT_RECEIPT))
    args = parser.parse_args()
    print(json.dumps(validate_receipt(args.receipt), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
