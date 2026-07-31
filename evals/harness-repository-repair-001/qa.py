#!/usr/bin/env python3
"""Run the R2 admission gate for HARNESS-REPO-REPAIR-001."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


REPO = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parent
TASK_PATH = ROOT / "task.json"
FIXTURE = REPO / "fixtures" / "harness-replacement-repository-repair-v1"
ORACLE = ROOT / "oracle" / "allocation.py"
KNOWN_INVALID = {
    "floor-only": ROOT / "known-invalid" / "floor-only.py",
    "visible-suite-overfit": ROOT / "known-invalid" / "visible-suite-overfit.py",
}
HIDDEN_VERIFIER = ROOT / "verifier" / "test_outcome.py"
VISIBLE_FILES = ("SPEC.md", "allocation.py", "test_allocation.py")
PROTECTED_FILES = ("SPEC.md", "test_allocation.py")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def payload_digest(document: dict[str, Any]) -> str:
    payload = dict(document)
    payload.pop("integrity", None)
    return _sha256_bytes(_canonical(payload))


def environment_digest() -> str:
    manifest = [
        {"path": name, "digest": file_digest(FIXTURE / name)}
        for name in VISIBLE_FILES
    ]
    return _sha256_bytes(_canonical(manifest))


def write_task_digests() -> None:
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    task["initialState"]["environmentDigest"] = environment_digest()
    task["oracle"]["digest"] = file_digest(ORACLE)
    task["integrity"] = {
        "algorithm": "sha256",
        "canonicalization": "ordivon-canonical-json-v1",
        "payloadDigest": payload_digest(task),
    }
    TASK_PATH.write_text(
        json.dumps(task, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate_task() -> dict[str, Any]:
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    expected = {
        "schemaVersion",
        "kind",
        "taskId",
        "taskVersion",
        "family",
        "objective",
        "initialState",
        "allowedCapabilities",
        "budgetProfile",
        "acceptanceContract",
        "oracle",
        "expertTimeEstimateMinutes",
        "reproducibility",
        "knownLimitations",
        "labels",
        "integrity",
    }
    if set(task) != expected:
        raise ValueError("Task Definition fields differ from Track R v1")
    if task["schemaVersion"] != 1 or task["kind"] != "ordivon.evaluation-task":
        raise ValueError("Task Definition identity differs")
    if task["taskId"] != "HARNESS-REPO-REPAIR-001" or task["taskVersion"] != 1:
        raise ValueError("Task identity differs")
    if task["initialState"]["fixturePath"] != "fixtures/harness-replacement-repository-repair-v1":
        raise ValueError("Task fixture path differs")
    if task["initialState"]["environmentDigest"] != environment_digest():
        raise ValueError("Task environment digest differs")
    if task["oracle"]["path"] != "evals/harness-repository-repair-001/oracle/allocation.py":
        raise ValueError("Task oracle path differs")
    if task["oracle"]["digest"] != file_digest(ORACLE):
        raise ValueError("Task oracle digest differs")
    if task["reproducibility"] != {"cleanRebuildTrials": 3, "requiredAgreement": 3}:
        raise ValueError("Task reproducibility gate differs")
    if task["integrity"] != {
        "algorithm": "sha256",
        "canonicalization": "ordivon-canonical-json-v1",
        "payloadDigest": payload_digest(task),
    }:
        raise ValueError("Task integrity differs")
    if not HIDDEN_VERIFIER.is_file() or HIDDEN_VERIFIER.is_relative_to(FIXTURE):
        raise ValueError("hidden verifier is missing or inside the model-visible fixture")
    if not ORACLE.is_file() or ORACLE.is_relative_to(FIXTURE):
        raise ValueError("oracle is missing or inside the model-visible fixture")
    return task


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    return {
        "returnCode": process.returncode,
        "passed": process.returncode == 0,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def _tree_manifest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): file_digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_candidate(label: str, source: Path | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / "workspace"
        shutil.copytree(FIXTURE, workspace)
        before = _tree_manifest(workspace)
        protected = {name: file_digest(workspace / name) for name in PROTECTED_FILES}
        if source is not None:
            shutil.copyfile(source, workspace / "allocation.py")

        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "ORDIVON_EVAL_WORKSPACE": str(workspace),
        }
        visible = _run(
            ["/usr/bin/python3", "-m", "unittest", "-v", "test_allocation.py"],
            cwd=workspace,
            env=env,
        )
        hidden = _run(
            ["/usr/bin/python3", str(HIDDEN_VERIFIER)],
            cwd=workspace,
            env=env,
        )
        after = _tree_manifest(workspace)
        changed = sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        if source is not None and "allocation.py" not in changed:
            raise AssertionError(f"{label}: candidate source was not applied")
        unexpected = [
            path
            for path in changed
            if path != "allocation.py" and not path.startswith("artifacts/")
        ]
        if unexpected:
            raise AssertionError(f"{label}: unexpected changed paths: {unexpected}")
        for name, digest in protected.items():
            if file_digest(workspace / name) != digest:
                raise AssertionError(f"{label}: protected file changed: {name}")
        return {
            "label": label,
            "visiblePassed": visible["passed"],
            "hiddenPassed": hidden["passed"],
            "changedPaths": changed,
        }


def run_qa() -> dict[str, Any]:
    task = validate_task()
    trials = task["reproducibility"]["cleanRebuildTrials"]
    rounds: list[dict[str, Any]] = []
    for index in range(1, trials + 1):
        cases = {
            "baseline": run_candidate("baseline", None),
            "oracle": run_candidate("oracle", ORACLE),
            "floor-only": run_candidate("floor-only", KNOWN_INVALID["floor-only"]),
            "visible-suite-overfit": run_candidate(
                "visible-suite-overfit",
                KNOWN_INVALID["visible-suite-overfit"],
            ),
        }
        expected = {
            "baseline": (False, False),
            "oracle": (True, True),
            "floor-only": (False, False),
            "visible-suite-overfit": (True, False),
        }
        for label, expectation in expected.items():
            observed = (cases[label]["visiblePassed"], cases[label]["hiddenPassed"])
            if observed != expectation:
                raise AssertionError(
                    f"round {index} {label}: expected {expectation}, observed {observed}"
                )
        rounds.append({"round": index, "cases": cases})

    signatures = [
        {
            label: (case["visiblePassed"], case["hiddenPassed"])
            for label, case in round_result["cases"].items()
        }
        for round_result in rounds
    ]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise AssertionError("clean rebuild outcomes disagree")
    result = {
        "schemaVersion": 1,
        "kind": "ordivon.evaluation-task-qa-result",
        "taskId": task["taskId"],
        "taskVersion": task["taskVersion"],
        "sourceRevision": task["initialState"]["sourceRevision"],
        "taskDefinitionDigest": task["integrity"]["payloadDigest"],
        "environmentDigest": task["initialState"]["environmentDigest"],
        "oracleDigest": task["oracle"]["digest"],
        "hiddenVerifierDigest": file_digest(HIDDEN_VERIFIER),
        "knownInvalidDigests": {
            label: file_digest(path) for label, path in sorted(KNOWN_INVALID.items())
        },
        "cleanRebuildTrials": trials,
        "requiredAgreement": task["reproducibility"]["requiredAgreement"],
        "agreement": len(signatures),
        "outcomes": signatures[0],
        "hiddenVerifierOutsideWorkspace": not HIDDEN_VERIFIER.is_relative_to(FIXTURE),
        "oracleOutsideWorkspace": not ORACLE.is_relative_to(FIXTURE),
        "passed": True,
    }
    result["integrity"] = {
        "algorithm": "sha256",
        "canonicalization": "ordivon-canonical-json-v1",
        "payloadDigest": payload_digest(result),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-digests", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.write_digests:
        write_task_digests()
    result = run_qa()
    rendered = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
