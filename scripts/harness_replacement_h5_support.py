from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from anc_canonical import canonical_digest, digest_text

WORKLOAD_ID = "harness-replacement-repository-repair-v1"
WORKLOAD_RELATIVE = Path("fixtures") / WORKLOAD_ID
SPEC_PATH = Path("SPEC.md")
SOURCE_PATH = Path("allocation.py")
TEST_PATH = Path("test_allocation.py")
DIAGNOSIS_PATH = Path("artifacts/diagnosis.json")
COMPLETION_PATH = Path("artifacts/completion.json")
TEST_COMMAND = "PYTHONDONTWRITEBYTECODE=1 python -m unittest -v test_allocation.py"


def parse_json_object(text: str) -> dict[str, Any]:
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
        raise ValueError("provider response contains text after the JSON object")
    if not isinstance(parsed, dict):
        raise ValueError("provider response is not a JSON object")
    return parsed


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def text_digest(path: Path) -> str:
    return digest_text(path.read_text(encoding="utf-8"))


def semantic_digest(value: dict[str, Any]) -> str:
    return canonical_digest(value)


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{label} fields differ; missing={missing}, extra={extra}")


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _bounded_strings(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must contain {minimum} to {maximum} strings")
    result = [_nonempty(item, label) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} entries must be unique")
    return result


def validate_diagnosis(
    value: dict[str, Any],
    *,
    base_source_revision: str,
) -> dict[str, Any]:
    expected = {
        "schemaVersion",
        "kind",
        "workloadId",
        "baseSourceRevision",
        "defect",
        "evidence",
        "repairPlan",
        "constraints",
    }
    _exact(value, expected, "diagnosis")
    if value["schemaVersion"] != 1:
        raise ValueError("diagnosis schemaVersion differs")
    if value["kind"] != "ordivon.harness-replacement-diagnosis":
        raise ValueError("diagnosis kind differs")
    if value["workloadId"] != WORKLOAD_ID:
        raise ValueError("diagnosis workload identity differs")
    if value["baseSourceRevision"] != base_source_revision:
        raise ValueError("diagnosis source revision differs")
    _nonempty(value["defect"], "diagnosis defect")
    _bounded_strings(value["evidence"], "diagnosis evidence", minimum=2, maximum=6)
    _bounded_strings(value["repairPlan"], "diagnosis repair plan", minimum=1, maximum=5)
    constraints = _bounded_strings(
        value["constraints"], "diagnosis constraints", minimum=2, maximum=5
    )
    required_constraints = {"modify allocation.py only", "preserve the public API"}
    if not required_constraints.issubset(set(constraints)):
        raise ValueError("diagnosis constraints omit frozen repair boundaries")
    return value


def validate_completion(
    value: dict[str, Any],
    *,
    base_source_revision: str,
    diagnosis_digest: str,
    final_source_digest: str,
) -> dict[str, Any]:
    expected = {
        "schemaVersion",
        "kind",
        "workloadId",
        "baseSourceRevision",
        "diagnosisDigest",
        "changedPaths",
        "testCommand",
        "testResult",
        "finalSourceDigest",
        "summary",
    }
    _exact(value, expected, "completion")
    if value["schemaVersion"] != 1:
        raise ValueError("completion schemaVersion differs")
    if value["kind"] != "ordivon.harness-replacement-completion":
        raise ValueError("completion kind differs")
    if value["workloadId"] != WORKLOAD_ID:
        raise ValueError("completion workload identity differs")
    if value["baseSourceRevision"] != base_source_revision:
        raise ValueError("completion source revision differs")
    if value["diagnosisDigest"] != diagnosis_digest:
        raise ValueError("completion diagnosis digest differs")
    if value["changedPaths"] != [str(SOURCE_PATH)]:
        raise ValueError("completion changed paths differ")
    if value["testCommand"] != TEST_COMMAND:
        raise ValueError("completion test command differs")
    if value["testResult"] != "passed":
        raise ValueError("completion test result differs")
    if value["finalSourceDigest"] != final_source_digest:
        raise ValueError("completion final source digest differs")
    _nonempty(value["summary"], "completion summary")
    return value


def run_acceptance(root: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["/usr/bin/python3", "-m", "unittest", "-v", str(TEST_PATH)],
        cwd=root,
        env={**dict(__import__("os").environ), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    return {
        "command": TEST_COMMAND,
        "returnCode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "passed": process.returncode == 0,
    }


def git_status(root: Path) -> tuple[str, ...]:
    process = subprocess.run(
        [
            "/usr/bin/git",
            "status",
            "--short",
            "--untracked-files=all",
            "--",
            ".",
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=30,
    )
    return tuple(line for line in process.stdout.splitlines() if line.strip())


def provider_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase", "artifact", "summary", "testStatus"],
        "properties": {
            "phase": {"type": "string", "enum": ["diagnose", "repair"]},
            "artifact": {"type": "string"},
            "summary": {"type": "string"},
            "testStatus": {"type": "string", "enum": ["failing", "passed"]},
        },
    }
