#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from anc_canonical import canonical_digest

from ordivon_host.harness import CodexAppServerDriver


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded Codex App Server H3 repository-inspection turn."
    )
    parser.add_argument("--working-directory", default=".")
    parser.add_argument(
        "--target",
        default="src/ordivon_host/harness/runtime_refs.py",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


def output_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "file",
            "purpose",
            "invariants",
            "observedCommand",
            "conclusion",
        ],
        "properties": {
            "file": {"type": "string"},
            "purpose": {"type": "string"},
            "invariants": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {"type": "string"},
            },
            "observedCommand": {"type": "string"},
            "conclusion": {"type": "string"},
        },
    }


def main() -> None:
    args = parse_args()
    root = Path(args.working_directory).resolve()
    target = args.target
    prompt = (
        f"Inspect only `{target}` in the current repository. "
        "Use a shell command to read the file; do not rely only on prior context. "
        "Do not modify any file, do not use web search, and do not call MCP tools. "
        "Return the requested JSON describing the file's purpose, three to five "
        "important invariants, the exact command you used, and a concise conclusion."
    )
    driver = CodexAppServerDriver(
        working_directory=root,
        timeout_seconds=args.timeout_seconds,
        approval_policy="never",
        sandbox="read-only",
        ephemeral=True,
        base_instructions=(
            "You are a bounded read-only repository inspection Harness. "
            "Never modify files and never request expanded permissions."
        ),
        developer_instructions=(
            "Use one local shell read command and return only the output-schema object."
        ),
    )
    with driver:
        result = driver.run_turn(prompt, output_schema=output_schema())
        manifest = driver.manifest()
    try:
        structured = json.loads(result.assistant_text)
    except json.JSONDecodeError as error:
        raise SystemExit(f"Codex App Server returned non-JSON final output: {error}") from error
    if not isinstance(structured, dict):
        raise SystemExit("Codex App Server final output is not an object")
    command_items = [item for item in result.tool_items if item.get("type") == "commandExecution"]
    file_changes = [item for item in result.tool_items if item.get("type") == "fileChange"]
    checks = {
        "turnCompleted": result.status == "completed",
        "commandExecutionObserved": bool(command_items),
        "commandSucceeded": any(item.get("exitCode") == 0 for item in command_items),
        "targetMentionedByCommand": any(target in str(item.get("command")) for item in command_items),
        "noFileChange": not file_changes,
        "structuredOutputMatchesTarget": structured.get("file") == target,
        "usageObserved": bool(result.usage),
        "rawProviderDigestRetained": result.raw_message_digest.startswith("sha256:"),
        "interruptCapabilityAdvertised": manifest.interrupt,
        "resumeCapabilityAdvertised": manifest.session_resume,
    }
    if not all(checks.values()):
        raise SystemExit(f"Codex App Server H3 checks failed: {checks}")
    payload = {
        "schemaVersion": 1,
        "kind": "ordivon.codex-app-h3-worker-result",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "manifest": manifest.to_dict(),
        "turnResult": result.to_dict(),
        "structuredOutput": structured,
        "checks": checks,
        "baselineComparison": {
            "codexCliOneShot": {
                "persistentSessionRetained": False,
                "providerThreadIdentity": False,
                "interrupt": False,
                "toolLifecycleEvents": False,
                "rawProviderEventDigest": False,
                "processModel": "one subprocess per invocation",
            },
            "codexAppServer": {
                "persistentSessionRetained": True,
                "providerThreadIdentity": True,
                "interrupt": True,
                "toolLifecycleEvents": True,
                "rawProviderEventDigest": True,
                "processModel": "one App Server process can host multiple turns",
            },
        },
    }
    payload["payloadDigest"] = canonical_digest(payload)
    print(
        "ORDIVON_H3_RESULT="
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
