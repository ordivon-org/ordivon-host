from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from anc_canonical import canonical_digest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/check_harness_replacement_h5_receipt.py"
RECEIPT = REPO / "evidence/harness-replacement-h5-live-76420e4-20260731.json"

spec = importlib.util.spec_from_file_location("ordivon_h5_receipt", SCRIPT)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _write_tampered(value: dict, root: str) -> Path:
    payload = copy.deepcopy(value)
    payload.pop("integrity")
    value["integrity"]["payloadDigest"] = canonical_digest(payload)
    path = Path(root) / "tampered.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class HarnessReplacementH5ReceiptTests(unittest.TestCase):
    def test_committed_receipt_proves_both_orders_and_three_faults(self) -> None:
        result = checker.validate_receipt(RECEIPT)
        self.assertEqual(result["implementationSourceRevision"], checker.EXPECTED_SOURCE_REVISION)
        self.assertEqual(len(result["trajectories"]), 2)
        self.assertEqual(
            {item["label"] for item in result["trajectories"]},
            {"codex-to-hermes", "hermes-to-codex"},
        )
        self.assertEqual(result["missingArtifact"]["taskState"], "waiting")
        for trajectory in result["trajectories"]:
            self.assertEqual(
                trajectory["providerFinalSources"],
                [
                    "provider" if trajectory["providerOrder"][0] == "codex" else "verified-artifact",
                    "provider" if trajectory["providerOrder"][1] == "codex" else "verified-artifact",
                ],
            )

    def test_assignment_generation_drift_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        receipt["trajectories"][0]["assignments"][1]["generation"] = 3
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "Assignment generation"):
                checker.validate_receipt(path)

    def test_completion_diagnosis_binding_drift_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        receipt["trajectories"][0]["completion"]["value"]["diagnosisDigest"] = (
            "sha256:" + "0" * 64
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "diagnosis digest differs"):
                checker.validate_receipt(path)

    def test_response_loss_redispatch_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        receipt["trajectories"][0]["faults"]["dispatchCalls"] = 2
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "dispatch count"):
                checker.validate_receipt(path)

    def test_runtime_semantic_completion_claim_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        receipt["trajectories"][0]["runs"][1]["runtimeTerminalEvidence"][
            "semanticCompletion"
        ] = True
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "semantic completion"):
                checker.validate_receipt(path)

    def test_missing_artifact_false_acceptance_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        fault = receipt["faults"]["missingArtifact"]
        fault["decision"]["accepted"] = True
        fault["decision"]["reasonCode"] = "accepted"
        fault["handoff"]["taskState"] = "completed"
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "missing Artifact"):
                checker.validate_receipt(path)

    def test_hermes_final_response_source_drift_is_rejected(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        run = receipt["trajectories"][0]["runs"][1]
        run["providerFinalResponseUsable"] = True
        run["finalResponseSource"] = "provider"
        run["providerFinalResponse"] = copy.deepcopy(run["canonicalFinalResponse"])
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(receipt, temporary)
            with self.assertRaisesRegex(ValueError, "Hermes provider final response"):
                checker.validate_receipt(path)


if __name__ == "__main__":
    unittest.main()
