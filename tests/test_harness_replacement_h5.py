from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from anc_canonical import canonical_digest, digest_text

REPO = Path(__file__).resolve().parents[1]
SUPPORT_PATH = REPO / "scripts/harness_replacement_h5_support.py"
FIXTURE = REPO / "fixtures/harness-replacement-repository-repair-v1"

spec = importlib.util.spec_from_file_location("h5_support", SUPPORT_PATH)
assert spec and spec.loader
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)

_REFERENCE_SOURCE = """from __future__ import annotations


def allocate_units(total: int, weights: list[int]) -> list[int]:
    \"\"\"Allocate integer units proportionally across positive integer weights.\"\"\"
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(\"total must be a non-negative integer\")
    if not isinstance(weights, list) or not weights:
        raise ValueError(\"weights must be a non-empty list\")
    if any(
        isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0
        for weight in weights
    ):
        raise ValueError(\"weights must contain positive integers\")

    weight_total = sum(weights)
    allocations = [(total * weight) // weight_total for weight in weights]
    remaining = total - sum(allocations)
    remainder_order = sorted(
        range(len(weights)),
        key=lambda index: (-(total * weights[index] % weight_total), index),
    )
    for index in remainder_order[:remaining]:
        allocations[index] += 1
    return allocations
"""


class HarnessReplacementH5FixtureTests(unittest.TestCase):
    def test_frozen_fixture_fails_before_repair(self) -> None:
        result = support.run_acceptance(FIXTURE)
        self.assertFalse(result["passed"])
        combined = result["stdout"] + result["stderr"]
        self.assertIn("FAILED", combined)
        self.assertIn("test_equal_remainders_use_input_order", combined)

    def test_reference_repair_passes_without_changing_spec_or_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / support.WORKLOAD_ID
            shutil.copytree(FIXTURE, root)
            spec_digest = support.text_digest(root / support.SPEC_PATH)
            tests_digest = support.text_digest(root / support.TEST_PATH)
            (root / support.SOURCE_PATH).write_text(_REFERENCE_SOURCE, encoding="utf-8")
            result = support.run_acceptance(root)
            self.assertTrue(result["passed"], result["stderr"])
            self.assertEqual(support.text_digest(root / support.SPEC_PATH), spec_digest)
            self.assertEqual(support.text_digest(root / support.TEST_PATH), tests_digest)

    def test_diagnosis_and_completion_bind_source_and_artifacts(self) -> None:
        revision = "a" * 40
        diagnosis = {
            "schemaVersion": 1,
            "kind": "ordivon.harness-replacement-diagnosis",
            "workloadId": support.WORKLOAD_ID,
            "baseSourceRevision": revision,
            "defect": "The implementation floors every quota and drops remaining units.",
            "evidence": [
                "The result sum is smaller than total for 10 and equal weights.",
                "The specification requires largest-remainder distribution.",
            ],
            "repairPlan": [
                "Compute floor allocations and distribute the remaining units by remainder."
            ],
            "constraints": [
                "modify allocation.py only",
                "preserve the public API",
            ],
        }
        self.assertEqual(
            support.validate_diagnosis(diagnosis, base_source_revision=revision),
            diagnosis,
        )
        diagnosis_digest = canonical_digest(diagnosis)
        source_digest = digest_text(_REFERENCE_SOURCE)
        completion = {
            "schemaVersion": 1,
            "kind": "ordivon.harness-replacement-completion",
            "workloadId": support.WORKLOAD_ID,
            "baseSourceRevision": revision,
            "diagnosisDigest": diagnosis_digest,
            "changedPaths": ["allocation.py"],
            "testCommand": support.TEST_COMMAND,
            "testResult": "passed",
            "finalSourceDigest": source_digest,
            "summary": "Implemented largest-remainder allocation and passed the frozen suite.",
        }
        self.assertEqual(
            support.validate_completion(
                completion,
                base_source_revision=revision,
                diagnosis_digest=diagnosis_digest,
                final_source_digest=source_digest,
            ),
            completion,
        )
        drift = dict(completion)
        drift["diagnosisDigest"] = canonical_digest({"wrong": True})
        with self.assertRaisesRegex(ValueError, "diagnosis digest differs"):
            support.validate_completion(
                drift,
                base_source_revision=revision,
                diagnosis_digest=diagnosis_digest,
                final_source_digest=source_digest,
            )

    def test_provider_output_parser_rejects_trailing_text(self) -> None:
        value = {"phase": "diagnose", "artifact": "a", "summary": "b", "testStatus": "failing"}
        text = json.dumps(value)
        self.assertEqual(support.parse_json_object(text), value)
        with self.assertRaisesRegex(ValueError, "after the JSON"):
            support.parse_json_object(text + " trailing")


if __name__ == "__main__":
    unittest.main()
