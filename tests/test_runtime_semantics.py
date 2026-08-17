from __future__ import annotations

import unittest

from ordivon_host.runtime import RuntimeProtocolError, classify_runtime_job_observation


class RuntimeSemanticsContractTests(unittest.TestCase):
    def test_status_only_projection_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeProtocolError, "executionTerminal"):
            classify_runtime_job_observation({"status": "succeeded"})

    def test_exact_committed_success_is_consumable(self) -> None:
        self.assertEqual(
            classify_runtime_job_observation(
                {
                    "status": "succeeded",
                    "executionTerminal": True,
                    "executionDisposition": "succeeded",
                    "deliveryDisposition": "committed",
                    "recoveryRequired": False,
                    "semanticCompletionEvaluated": False,
                    "resultAvailable": True,
                }
            ),
            "succeeded",
        )


if __name__ == "__main__":
    unittest.main()
