#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from anc_canonical import canonical_digest

from ordivon_host.harness import HermesACPDriver


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded Hermes ACP H4 repository-inspection prompt."
    )
    parser.add_argument("--working-directory", default=".")
    parser.add_argument(
        "--target",
        default="src/ordivon_host/harness/runtime_refs.py",
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    return parser.parse_args()


def _parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1]).strip()
            if value.startswith("json\n"):
                value = value[5:].lstrip()
    decoder = json.JSONDecoder()
    parsed, offset = decoder.raw_decode(value)
    if value[offset:].strip():
        raise ValueError("Hermes ACP final response contains text after the JSON object")
    if not isinstance(parsed, dict):
        raise ValueError("Hermes ACP final response is not a JSON object")
    return parsed


def _thought_payload_is_digest_only(result: dict[str, Any]) -> bool:
    events = result.get("normalizedEvents")
    if not isinstance(events, list):
        return False
    allowed = {
        "kind",
        "method",
        "updateType",
        "observedAtMs",
        "sessionId",
        "toolCallId",
        "toolKind",
        "payloadDigest",
    }
    for event in events:
        if not isinstance(event, dict) or set(event) != allowed:
            return False
        if event.get("kind") == "thought_observed":
            if not isinstance(event.get("payloadDigest"), str):
                return False
            if any(key in event for key in ("text", "content", "reasoning")):
                return False
    return True


def main() -> None:
    args = parse_args()
    root = Path(args.working_directory).resolve()
    target = args.target
    prompt = (
        f"Inspect only `{target}` in the current repository. "
        "You must use the read_file tool exactly for that file; do not rely only on prior "
        "context. Do not use terminal, process, search_files, write_file, patch, web, "
        "browser, memory, skills, todo, execute_code, session_search, or delegation. "
        "Do not modify any file. After the read succeeds, return only one compact JSON "
        "object with exactly these keys: file, purpose, invariants, observedTool, "
        "conclusion. file must equal the target path; invariants must contain three to "
        "five strings; observedTool must equal read_file. Do not wrap the JSON in prose."
    )
    driver = HermesACPDriver(
        working_directory=root,
        timeout_seconds=args.timeout_seconds,
    )
    with driver:
        result = driver.run_prompt(prompt)
        manifest = driver.manifest()
    try:
        structured = _parse_json_object(result.assistant_text)
    except (json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"Hermes ACP returned invalid structured output: {error}") from error
    required = {"file", "purpose", "invariants", "observedTool", "conclusion"}
    invariants = structured.get("invariants")
    tools = list(result.tool_items)
    read_tools = [item for item in tools if item.get("kind") == "read"]
    edit_tools = [
        item
        for item in tools
        if item.get("kind") in {"edit", "delete", "move"}
        or int(item.get("fileEditCount", 0)) > 0
    ]
    target_reads = [
        item
        for item in read_tools
        if any(
            isinstance(location, dict) and location.get("path") == target
            for location in item.get("locations", [])
        )
    ]
    result_value = result.to_dict()
    checks = {
        "promptCompleted": result.status == "completed",
        "providerEndedTurn": result.provider_stop_reason == "end_turn",
        "sessionIdentityRetained": bool(result.session.session_id),
        "sessionProvenanceRetained": bool(result.session.provenance_digest),
        "readToolObserved": bool(read_tools),
        "targetReadObserved": bool(target_reads),
        "allToolsReadOnly": bool(tools)
        and len(read_tools) == len(tools)
        and not edit_tools,
        "noFileEditContent": all(int(item.get("fileEditCount", 0)) == 0 for item in tools),
        "structuredFieldsExact": set(structured) == required,
        "structuredOutputMatchesTarget": structured.get("file") == target,
        "structuredOutputNamesTool": structured.get("observedTool") == "read_file",
        "structuredInvariantsBounded": isinstance(invariants, list)
        and 3 <= len(invariants) <= 5
        and all(isinstance(item, str) and item.strip() for item in invariants),
        "usageObserved": bool(result.usage)
        and isinstance(result.usage.get("totalTokens"), int),
        "rawProviderDigestRetained": result.raw_message_digest.startswith("sha256:"),
        "thoughtPayloadDigestOnly": _thought_payload_is_digest_only(result_value),
        "interruptCapabilityAdvertised": manifest.interrupt,
        "resumeCapabilityAdvertised": manifest.session_resume,
        "forkCapabilityAdvertised": manifest.session_fork,
    }
    if not all(checks.values()):
        raise SystemExit(f"Hermes ACP H4 checks failed: {checks}")
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "ordivon.hermes-acp-h4-worker-result",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "manifest": manifest.to_dict(),
        "promptResult": result_value,
        "structuredOutput": structured,
        "checks": checks,
        "baselineComparison": {
            "hermesCliOneShot": {
                "persistentSessionRetained": False,
                "providerSessionIdentity": False,
                "interrupt": False,
                "toolLifecycleEvents": False,
                "rawProviderEventDigest": False,
                "thoughtDigestOnly": False,
                "processModel": "one isolated subprocess per invocation",
            },
            "hermesACP": {
                "persistentSessionRetained": True,
                "providerSessionIdentity": True,
                "interrupt": True,
                "toolLifecycleEvents": True,
                "rawProviderEventDigest": True,
                "thoughtDigestOnly": True,
                "processModel": "one ACP process can host multiple prompts",
            },
        },
    }
    payload["payloadDigest"] = canonical_digest(payload)
    print(
        "ORDIVON_H4_RESULT="
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
