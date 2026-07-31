#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from anc_canonical import canonical_digest

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = REPO / "evidence/hermes-acp-h4-live-3d9a559-20260731.json"
EXPECTED_SOURCE_REVISION = "3d9a55904c735b388d8acf617262f0322174ba9a"
EXPECTED_REFERENCE_TYPES = ["assignment", "harness_run", "task", "task_attempt"]
EXPECTED_CHECKS = {
    "freshClientRecoveredJob",
    "handoffProjectsHarnessRun",
    "hermesPromptCompleted",
    "hostRecordedHarnessRun",
    "noEditToolObserved",
    "noRuntimeSemanticCompletionClaim",
    "oneShotBaselineRemainsDistinct",
    "rawProviderDigestRetained",
    "runtimeJobSucceeded",
    "runtimeOwnedProcessTree",
    "sessionIdentityRetained",
    "sessionProvenanceRetained",
    "taskNotSemanticallyCompleted",
    "terminalEvidenceBoundSource",
    "terminalEvidenceRetainedHostReferences",
    "terminalPromptAfterTool",
    "thoughtTextExcluded",
    "toolObservationRetained",
    "usageObserved",
    "workerChecksPassed",
}
EXPECTED_WORKER_CHECKS = {
    "allToolsReadOnly",
    "forkCapabilityAdvertised",
    "interruptCapabilityAdvertised",
    "noFileEditContent",
    "promptCompleted",
    "providerEndedTurn",
    "rawProviderDigestRetained",
    "readToolObserved",
    "resumeCapabilityAdvertised",
    "sessionIdentityRetained",
    "sessionProvenanceRetained",
    "structuredFieldsExact",
    "structuredInvariantsBounded",
    "structuredOutputMatchesTarget",
    "structuredOutputNamesTool",
    "targetReadObserved",
    "thoughtPayloadDigestOnly",
    "usageObserved",
}
EXPECTED_PROMPT_FIELDS = {
    "agentName",
    "agentVersion",
    "cwd",
    "durationMs",
    "finishedAtMs",
    "images",
    "loadSession",
    "model",
    "modelId",
    "modelProvider",
    "normalizedEventDigest",
    "protocolVersion",
    "provenance",
    "provenanceDigest",
    "providerStopReason",
    "rawMessageCount",
    "rawMessageDigest",
    "requestId",
    "sessionFork",
    "sessionId",
    "sessionResume",
    "startedAtMs",
    "status",
    "stopReason",
    "thoughtEventCount",
    "toolItems",
    "updateTypeCounts",
    "usage",
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


def validate_receipt(path: str | Path = DEFAULT_RECEIPT) -> dict[str, Any]:
    receipt_path = Path(path)
    receipt = _object(json.loads(receipt_path.read_text(encoding="utf-8")), "receipt")
    if receipt.get("schemaVersion") != 1:
        raise ValueError("receipt schemaVersion differs")
    if receipt.get("kind") != "ordivon.hermes-acp-h4-live-receipt":
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
        raise ValueError("H4 Assignment generation differs")

    manifest = _object(receipt.get("manifest"), "Harness manifest")
    if manifest.get("harnessId") != "harness:hermes-acp":
        raise ValueError("Harness manifest identity differs")
    if manifest.get("protocol") != "agent-client-protocol-jsonrpc-stdio":
        raise ValueError("Harness protocol differs")
    if manifest.get("protocolRevision") != "1":
        raise ValueError("Harness protocol revision differs")
    for field in (
        "persistentSession",
        "sessionResume",
        "sessionFork",
        "interrupt",
        "toolEvents",
        "approvalEvents",
        "usage",
        "images",
    ):
        if manifest.get(field) is not True:
            raise ValueError(f"Harness manifest capability {field} differs")
    for field in ("compaction", "checkpoint", "localSubagents"):
        if manifest.get(field) is not False:
            raise ValueError(f"Harness manifest unsupported capability {field} differs")
    extensions = _array(manifest.get("extensions"), "Harness extensions")
    if set(extensions) != {
        "hermes.raw-provider-event-digest",
        "hermes.session-provenance",
        "hermes.thought-digest-only",
    }:
        raise ValueError("Hermes Harness extensions differ")

    provider = _object(receipt.get("provider"), "provider summary")
    if provider.get("kind") != "ordivon.hermes-acp-h4-provider-summary":
        raise ValueError("provider summary kind differs")
    _sha256(provider.get("workerPayloadDigest"), "worker payload digest")
    _all_true(provider.get("workerChecks"), EXPECTED_WORKER_CHECKS, "worker checks")
    target = _string(provider.get("target"), "provider target")
    structured = _object(provider.get("structuredOutput"), "structured output")
    if set(structured) != {"file", "purpose", "invariants", "observedTool", "conclusion"}:
        raise ValueError("structured output fields differ")
    if structured.get("file") != target or structured.get("observedTool") != "read_file":
        raise ValueError("structured output target or Tool differs")
    invariants = _array(structured.get("invariants"), "structured invariants")
    if not 3 <= len(invariants) <= 5 or any(not isinstance(item, str) or not item.strip() for item in invariants):
        raise ValueError("structured invariants are invalid")

    baseline = _object(provider.get("baselineComparison"), "baseline comparison")
    one_shot = _object(baseline.get("hermesCliOneShot"), "one-shot baseline")
    acp = _object(baseline.get("hermesACP"), "Hermes ACP baseline")
    for field in (
        "persistentSessionRetained",
        "providerSessionIdentity",
        "interrupt",
        "toolLifecycleEvents",
        "rawProviderEventDigest",
        "thoughtDigestOnly",
    ):
        if one_shot.get(field) is not False or acp.get(field) is not True:
            raise ValueError(f"baseline distinction {field} differs")

    prompt = _object(provider.get("prompt"), "provider Prompt")
    if set(prompt) != EXPECTED_PROMPT_FIELDS:
        raise ValueError("provider Prompt fields differ")
    session_id = _string(prompt.get("sessionId"), "Hermes ACP Session identity")
    if prompt.get("protocolVersion") != 1:
        raise ValueError("Hermes ACP protocol version differs")
    if prompt.get("agentName") != "hermes-agent" or prompt.get("agentVersion") != "0.18.0":
        raise ValueError("Hermes Agent identity differs")
    if prompt.get("modelId") != "deepseek:deepseek-v4-pro":
        raise ValueError("Hermes model identity differs")
    if prompt.get("modelProvider") != "deepseek" or prompt.get("model") != "deepseek-v4-pro":
        raise ValueError("Hermes model projection differs")
    for field in ("loadSession", "sessionResume", "sessionFork", "images"):
        if prompt.get(field) is not True:
            raise ValueError(f"Hermes Session capability {field} differs")
    if prompt.get("status") != "completed" or prompt.get("stopReason") != "completed":
        raise ValueError("Hermes Prompt did not complete")
    if prompt.get("providerStopReason") != "end_turn":
        raise ValueError("Hermes Provider stop reason differs")
    started = _integer(prompt.get("startedAtMs"), "Prompt start")
    finished = _integer(prompt.get("finishedAtMs"), "Prompt finish")
    duration = _integer(prompt.get("durationMs"), "Prompt duration")
    if finished - started != duration:
        raise ValueError("Hermes Prompt duration differs")
    _sha256(prompt.get("rawMessageDigest"), "raw provider message digest")
    _sha256(prompt.get("normalizedEventDigest"), "normalized event digest")
    _integer(prompt.get("rawMessageCount"), "raw provider message count", minimum=1)
    thought_count = _integer(prompt.get("thoughtEventCount"), "thought event count", minimum=1)

    provenance = _object(prompt.get("provenance"), "Session provenance")
    if canonical_digest(provenance) != prompt.get("provenanceDigest"):
        raise ValueError("Session provenance digest differs")
    if provenance.get("acpSessionId") != session_id:
        raise ValueError("Session provenance ACP identity differs")
    if provenance.get("currentHermesSessionId") != session_id:
        raise ValueError("Session provenance current identity differs")
    if provenance.get("rootHermesSessionId") != session_id:
        raise ValueError("Session provenance root identity differs")
    if provenance.get("sessionKind") != "root" or provenance.get("compressionDepth") != 0:
        raise ValueError("Session provenance root shape differs")

    usage = _object(prompt.get("usage"), "Hermes usage")
    total_tokens = _integer(usage.get("totalTokens"), "Hermes total tokens", minimum=1)
    if _integer(usage.get("thoughtTokens"), "Hermes thought tokens") != thought_count:
        raise ValueError("Hermes thought event and token counts differ")
    if total_tokens != _integer(usage.get("inputTokens"), "Hermes input tokens") + _integer(
        usage.get("outputTokens"), "Hermes output tokens"
    ):
        raise ValueError("Hermes total token accounting differs")

    counts = _object(prompt.get("updateTypeCounts"), "ACP update counts")
    if _integer(counts.get("tool_call"), "Tool observation count", minimum=1) < 1:
        raise ValueError("Hermes Tool observation is missing")
    if _integer(counts.get("usage_update"), "usage update count", minimum=1) < 1:
        raise ValueError("Hermes usage update is missing")
    if _integer(counts.get("agent_thought_chunk"), "thought update count") != thought_count:
        raise ValueError("Hermes thought update count differs")
    if _integer(counts.get("agent_message_chunk"), "message update count", minimum=1) < 1:
        raise ValueError("Hermes final message stream is missing")
    if "tool_call_update" in counts and _integer(counts["tool_call_update"], "Tool completion update count") < 1:
        raise ValueError("Hermes Tool completion update count is invalid")

    tools = _array(prompt.get("toolItems"), "provider Tool items")
    if len(tools) != 1:
        raise ValueError("H4 did not retain exactly one Tool observation")
    tool = _object(tools[0], "provider Tool observation")
    if tool.get("kind") != "read":
        raise ValueError("Hermes Tool observation is not read-only")
    locations = _array(tool.get("locations"), "Tool locations")
    if not any(isinstance(location, dict) and location.get("path") == target for location in locations):
        raise ValueError("Hermes Tool observation omitted target")
    if int(tool.get("fileEditCount", 0)) != 0:
        raise ValueError("Hermes Tool observation retained a file edit")
    if any(field in tool for field in ("rawInput", "rawOutput", "content")):
        raise ValueError("Hermes Tool observation retained raw Tool content")

    request = _object(receipt.get("request"), "Runtime request")
    execution = _object(request.get("execution"), "Runtime execution")
    references = _array(execution.get("foreignReferences"), "Host references")
    reference_types = [_object(reference, "Host reference").get("type") for reference in references]
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
        raise ValueError("H4 did not retain exactly one Runtime Job")
    matching_job = _object(jobs[0], "matching Runtime Job")
    if matching_job.get("jobId") != job_id or matching_job.get("attemptId") != attempt_id:
        raise ValueError("freshly recovered Runtime Job differs")
    if matching_job.get("clientRequestId") != request.get("clientRequestId"):
        raise ValueError("freshly recovered request identity differs")
    artifacts = _array(runtime.get("artifacts"), "Runtime Artifacts")
    artifact_kinds = {_object(artifact, "Runtime Artifact").get("kind") for artifact in artifacts}
    if not {"stdout", "stderr", "execution_result", "terminal_evidence"}.issubset(artifact_kinds):
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
    if recorded.get("sessionRef") != f"hermes-acp-session:{session_id}":
        raise ValueError("recorded Hermes Session reference differs")
    if recorded.get("eventDigest") != prompt.get("rawMessageDigest"):
        raise ValueError("recorded provider event digest differs")
    if recorded.get("runtimeJobRefs") != [job_id]:
        raise ValueError("recorded Runtime Job reference differs")
    if recorded.get("stopReason") != "completed":
        raise ValueError("recorded Harness stop reason differs")
    recorded_usage = _object(recorded.get("usage"), "recorded Harness usage")
    if recorded_usage.get("promptRequestId") != prompt.get("requestId"):
        raise ValueError("recorded Prompt request identity differs")
    if recorded_usage.get("model") != prompt.get("model"):
        raise ValueError("recorded model identity differs")
    if recorded_usage.get("thoughtEventCount") != thought_count:
        raise ValueError("recorded thought event count differs")
    recorded_artifacts = _array(recorded.get("artifactRefs"), "recorded Artifacts")
    if not any(
        isinstance(item, dict)
        and item.get("kind") == "hermes-acp-h4-worker-result"
        and str(item.get("ref", "")).startswith("host-object:sha256:")
        for item in recorded_artifacts
    ):
        raise ValueError("Host CAS worker-result reference is missing")
    if not any(isinstance(item, dict) and item.get("kind") == "terminal_evidence" for item in recorded_artifacts):
        raise ValueError("recorded Terminal Evidence reference is missing")

    handoff = _object(host.get("handoff"), "Operator handoff")
    if handoff.get("harnessRunId") != run_id:
        raise ValueError("Operator handoff Harness Run differs")
    if handoff.get("taskState") != "waiting" or handoff.get("outcomeObjectDigest") is not None:
        raise ValueError("H4 incorrectly committed semantic Task completion")

    return {
        "receipt": str(receipt_path),
        "payloadDigest": integrity["payloadDigest"],
        "sourceReceiptPayloadDigest": receipt["sourceReceiptPayloadDigest"],
        "jobId": job_id,
        "attemptId": attempt_id,
        "sessionId": session_id,
        "modelId": prompt["modelId"],
        "rawMessageCount": prompt["rawMessageCount"],
        "thoughtEventCount": thought_count,
        "toolTitle": tool.get("title"),
        "taskState": handoff["taskState"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the compact committed Hermes ACP H4 receipt."
    )
    parser.add_argument("receipt", nargs="?", default=str(DEFAULT_RECEIPT))
    args = parser.parse_args()
    print(json.dumps(validate_receipt(args.receipt), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
