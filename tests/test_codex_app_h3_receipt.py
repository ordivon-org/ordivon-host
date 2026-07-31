from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from anc_canonical import canonical_digest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/check_codex_app_h3_receipt.py"
RECEIPT = REPO / "evidence/codex-app-h3-live-64ab44b-20260731.json"

spec = importlib.util.spec_from_file_location("ordivon_codex_app_h3_receipt", SCRIPT)
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


class CodexAppH3ReceiptTests(unittest.TestCase):
    def test_committed_receipt_satisfies_provider_and_runtime_boundary(self) -> None:
        result = checker.validate_receipt(RECEIPT)
        self.assertEqual(result["model"], "gpt-5.6-sol")
        self.assertEqual(result["taskState"], "waiting")
        self.assertGreater(result["rawMessageCount"], 1)
        self.assertIn("runtime_refs.py", result["toolCommand"])
        self.assertTrue(str(result["jobId"]).startswith("job-"))
        self.assertTrue(str(result["attemptId"]).startswith("attempt-"))

    def test_tool_failure_is_rejected_after_integrity_recomputation(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(receipt)
        tampered["provider"]["turn"]["toolItems"][0]["exitCode"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(tampered, temporary)
            with self.assertRaisesRegex(ValueError, "commandExecution did not succeed"):
                checker.validate_receipt(path)

    def test_semantic_completion_drift_is_rejected_after_integrity_recomputation(self) -> None:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(receipt)
        tampered["host"]["handoff"]["taskState"] = "completed"
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_tampered(tampered, temporary)
            with self.assertRaisesRegex(ValueError, "semantic Task completion"):
                checker.validate_receipt(path)


if __name__ == "__main__":
    unittest.main()
