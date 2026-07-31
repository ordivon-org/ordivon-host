#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from anc_canonical import canonical_digest

from ordivon_host.harness import CodexAppServerDriver, HermesACPDriver

from harness_replacement_h5_support import (
    COMPLETION_PATH,
    DIAGNOSIS_PATH,
    SOURCE_PATH,
    SPEC_PATH,
    TEST_COMMAND,
    TEST_PATH,
    WORKLOAD_ID,
    WORKLOAD_RELATIVE,
    git_status,
    parse_json_object,
    provider_output_schema,
    read_json,
    run_acceptance,
    semantic_digest,
    text_digest,
    validate_completion,
    validate_diagnosis,
)

_RESULT_PREFIX = "ORDIVON_H5_WORKER_RESULT="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one provider-faithful H5 diagnosis or repair Assignment."
    )
    parser.add_argument("--provider", choices=("codex", "hermes"), required=True)
    parser.add_argument("--phase", choices=("diagnose", "repair"), required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--base-source-revision", required=True)
    parser.add_argument("--diagnosis-digest")
    parser.add_argument("--timeout-seconds", type=int, default=420)
    return parser.parse_args()


def _prompt(
    *,
    phase: str,
    provider: str,
    base_source_revision: str,
    diagnosis_digest: str | None,
) -> str:
    shared = (
        f"You are executing phase `{phase}` of frozen workload `{WORKLOAD_ID}`. "
        f"The repository base source revision is `{base_source_revision}`. "
        "Work only inside the current directory. Read SPEC.md, allocation.py, and "
        "test_allocation.py directly. Do not use web, memory, delegation, MCP, or skills. "
        f"Use the exact acceptance command `{TEST_COMMAND}`. "
    )
    if phase == "diagnose":
        return shared + (
            "Do not modify SPEC.md, allocation.py, or test_allocation.py. Run the tests to "
            "observe the frozen defect. Create artifacts/diagnosis.json as one JSON object "
            "with exactly these keys: schemaVersion=1, "
            "kind='ordivon.harness-replacement-diagnosis', "
            f"workloadId='{WORKLOAD_ID}', baseSourceRevision='{base_source_revision}', "
            "defect (string), evidence (2-6 unique strings), repairPlan (1-5 unique "
            "strings), constraints (2-5 unique strings). constraints must include exactly "
            "the phrases 'modify allocation.py only' and 'preserve the public API'. "
            "After writing the Artifact, return only a compact JSON object with phase "
            "'diagnose', artifact 'artifacts/diagnosis.json', a summary, and testStatus "
            "'failing'."
        )
    if diagnosis_digest is None:
        raise ValueError("repair phase requires diagnosis digest")
    return shared + (
        "Read artifacts/diagnosis.json as external evidence. Modify only allocation.py; "
        "do not modify SPEC.md, test_allocation.py, or diagnosis.json. Implement the "
        "specified largest-remainder method, run the exact acceptance command until it "
        "passes, and create artifacts/completion.json as one JSON object with exactly "
        "these keys: schemaVersion=1, kind='ordivon.harness-replacement-completion', "
        f"workloadId='{WORKLOAD_ID}', baseSourceRevision='{base_source_revision}', "
        f"diagnosisDigest='{diagnosis_digest}', changedPaths=['allocation.py'], "
        f"testCommand='{TEST_COMMAND}', testResult='passed', finalSourceDigest equal to "
        "the sha256 text digest of the final allocation.py using the form sha256:<64 hex>, "
        "and summary (string). After writing the Artifact, return only a compact JSON "
        "object with phase 'repair', artifact 'artifacts/completion.json', a summary, and "
        "testStatus 'passed'."
    )


def _optional_provider_response(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    try:
        value = parse_json_object(text)
    except (json.JSONDecodeError, ValueError):
        return None
    required = {"phase", "artifact", "summary", "testStatus"}
    return value if set(value) == required else None


def _run_codex(
    root: Path,
    prompt: str,
    timeout: int,
) -> tuple[Any, dict[str, Any] | None, Any]:
    driver = CodexAppServerDriver(
        working_directory=root,
        timeout_seconds=timeout,
        approval_policy="never",
        sandbox="workspace-write",
        ephemeral=True,
        base_instructions=(
            "You are a bounded repository-repair Harness inside one isolated Runtime "
            "Workspace. Respect the exact file and Artifact boundaries in the prompt."
        ),
        developer_instructions=(
            "Use local shell and file tools only. Return only the requested JSON object."
        ),
    )
    with driver:
        result = driver.run_turn(prompt, output_schema=provider_output_schema())
        manifest = driver.manifest()
    return result, _optional_provider_response(result.assistant_text), manifest


def _run_hermes(
    root: Path,
    prompt: str,
    timeout: int,
) -> tuple[Any, dict[str, Any] | None, Any]:
    driver = HermesACPDriver(working_directory=root, timeout_seconds=timeout)
    with driver:
        session = driver.start_session()
        driver.set_session_mode(session.session_id, "accept_edits")
        handle = driver.start_prompt(session, prompt)
        result = driver.wait_prompt(handle)
        manifest = driver.manifest()
    return result, _optional_provider_response(result.assistant_text), manifest


def _provider_summary(provider: str, result: Any) -> dict[str, Any]:
    if provider == "codex":
        return {
            "provider": provider,
            "status": result.status,
            "stopReason": result.stop_reason,
            "threadId": result.thread.thread_id,
            "turnId": result.turn_id,
            "model": result.thread.model,
            "modelProvider": result.thread.model_provider,
            "rawMessageDigest": result.raw_message_digest,
            "rawMessageCount": result.raw_message_count,
            "normalizedEventDigest": result.normalized_event_digest,
            "toolItems": list(result.tool_items),
            "usage": result.usage,
            "result": result.to_dict(),
        }
    return {
        "provider": provider,
        "status": result.status,
        "stopReason": result.stop_reason,
        "providerStopReason": result.provider_stop_reason,
        "sessionId": result.session.session_id,
        "sessionProvenance": result.session.provenance,
        "sessionProvenanceDigest": result.session.provenance_digest,
        "model": result.session.model,
        "modelProvider": result.session.model_provider,
        "rawMessageDigest": result.raw_message_digest,
        "rawMessageCount": result.raw_message_count,
        "normalizedEventDigest": result.normalized_event_digest,
        "updateTypeCounts": result.update_type_counts,
        "thoughtEventCount": result.thought_event_count,
        "toolItems": list(result.tool_items),
        "usage": result.usage,
        "result": result.to_dict(),
    }


def main() -> None:
    args = parse_args()
    repository_root = Path(args.working_directory).resolve()
    root = repository_root / WORKLOAD_RELATIVE
    if not root.is_dir():
        raise SystemExit(f"missing frozen H5 workload: {root}")
    before = {
        "spec": text_digest(root / SPEC_PATH),
        "source": text_digest(root / SOURCE_PATH),
        "tests": text_digest(root / TEST_PATH),
        "diagnosis": (
            None if not (root / DIAGNOSIS_PATH).exists() else text_digest(root / DIAGNOSIS_PATH)
        ),
    }
    prompt = _prompt(
        phase=args.phase,
        provider=args.provider,
        base_source_revision=args.base_source_revision,
        diagnosis_digest=args.diagnosis_digest,
    )
    if args.provider == "codex":
        provider_result, provider_final_response, manifest = _run_codex(
            root, prompt, args.timeout_seconds
        )
    else:
        provider_result, provider_final_response, manifest = _run_hermes(
            root, prompt, args.timeout_seconds
        )

    independent_test = run_acceptance(root)
    after = {
        "spec": text_digest(root / SPEC_PATH),
        "source": text_digest(root / SOURCE_PATH),
        "tests": text_digest(root / TEST_PATH),
        "diagnosis": (
            None if not (root / DIAGNOSIS_PATH).exists() else text_digest(root / DIAGNOSIS_PATH)
        ),
    }
    checks: dict[str, bool] = {
        "providerCompleted": provider_result.status == "completed",
        "specUnchanged": after["spec"] == before["spec"],
        "testsUnchanged": after["tests"] == before["tests"],
    }
    artifact_value: dict[str, Any]
    artifact_path: Path
    if args.phase == "diagnose":
        artifact_path = root / DIAGNOSIS_PATH
        artifact_value = validate_diagnosis(
            read_json(artifact_path),
            base_source_revision=args.base_source_revision,
        )
        final_response = {
            "phase": "diagnose",
            "artifact": str(DIAGNOSIS_PATH),
            "summary": artifact_value["defect"],
            "testStatus": "failing",
        }
        checks.update(
            {
                "sourceUnchanged": after["source"] == before["source"],
                "diagnosisCreated": artifact_path.is_file(),
                "completionAbsent": not (root / COMPLETION_PATH).exists(),
                "frozenTestsStillFail": independent_test["passed"] is False,
            }
        )
    else:
        if args.diagnosis_digest is None:
            raise SystemExit("repair phase requires diagnosis digest")
        if after["diagnosis"] != before["diagnosis"]:
            raise SystemExit("repair phase modified diagnosis Artifact")
        artifact_path = root / COMPLETION_PATH
        final_source_digest = text_digest(root / SOURCE_PATH)
        artifact_value = validate_completion(
            read_json(artifact_path),
            base_source_revision=args.base_source_revision,
            diagnosis_digest=args.diagnosis_digest,
            final_source_digest=final_source_digest,
        )
        final_response = {
            "phase": "repair",
            "artifact": str(COMPLETION_PATH),
            "summary": artifact_value["summary"],
            "testStatus": "passed",
        }
        checks.update(
            {
                "sourceChanged": after["source"] != before["source"],
                "diagnosisUnchanged": after["diagnosis"] == before["diagnosis"],
                "completionCreated": artifact_path.is_file(),
                "acceptancePassed": independent_test["passed"] is True,
            }
        )
    provider_final_response_usable = provider_final_response == final_response
    if not all(checks.values()):
        raise SystemExit(f"H5 worker checks failed: {checks}")

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "ordivon.harness-replacement-h5-worker-result",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "workloadId": WORKLOAD_ID,
        "phase": args.phase,
        "provider": args.provider,
        "baseSourceRevision": args.base_source_revision,
        "manifest": manifest.to_dict(),
        "providerSummary": _provider_summary(args.provider, provider_result),
        "providerFinalResponse": provider_final_response,
        "providerFinalResponseUsable": provider_final_response_usable,
        "finalResponseSource": (
            "provider" if provider_final_response_usable else "verified-artifact"
        ),
        "finalResponse": final_response,
        "artifactPath": str(artifact_path.relative_to(repository_root)),
        "artifactValue": artifact_value,
        "artifactSemanticDigest": semantic_digest(artifact_value),
        "artifactTextDigest": text_digest(artifact_path),
        "sourceTextDigestBefore": before["source"],
        "sourceTextDigestAfter": after["source"],
        "independentTest": independent_test,
        "gitStatus": list(git_status(root)),
        "checks": checks,
    }
    payload["payloadDigest"] = canonical_digest(payload)
    print(
        _RESULT_PREFIX
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
