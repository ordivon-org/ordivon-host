from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
QA_PATH = REPO / "evals" / "harness-repository-repair-001" / "qa.py"
TASK_PATH = QA_PATH.parent / "task.json"
EVIDENCE_PATH = QA_PATH.parent / "evidence" / "r2-task-qa.json"

spec = importlib.util.spec_from_file_location("harness_repair_qa", QA_PATH)
assert spec and spec.loader
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class HarnessRepositoryRepairEvaluationTests(unittest.TestCase):
    def test_task_definition_and_digests_validate(self) -> None:
        task = qa.validate_task()
        self.assertEqual(task["taskId"], "HARNESS-REPO-REPAIR-001")
        self.assertEqual(task["reproducibility"], {"cleanRebuildTrials": 3, "requiredAgreement": 3})
        self.assertEqual(task["integrity"]["payloadDigest"], qa.payload_digest(task))

    def test_full_task_qa_gate(self) -> None:
        result = qa.run_qa()
        self.assertTrue(result["passed"])
        self.assertEqual(result["agreement"], 3)
        self.assertEqual(result["outcomes"]["baseline"], (False, False))
        self.assertEqual(result["outcomes"]["oracle"], (True, True))
        self.assertEqual(result["outcomes"]["floor-only"], (False, False))
        self.assertEqual(result["outcomes"]["visible-suite-overfit"], (True, False))
        self.assertTrue(result["hiddenVerifierOutsideWorkspace"])
        self.assertTrue(result["oracleOutsideWorkspace"])

    def test_task_json_is_stable_json(self) -> None:
        value = json.loads(TASK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(value["kind"], "ordivon.evaluation-task")

    def test_committed_qa_evidence_matches_current_gate(self) -> None:
        evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(qa.run_qa()))
        self.assertEqual(evidence, current)
        self.assertEqual(evidence["integrity"]["payloadDigest"], qa.payload_digest(evidence))


if __name__ == "__main__":
    unittest.main()
