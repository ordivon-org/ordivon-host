from __future__ import annotations

import json
import unittest

from anc_canonical import canonical_digest
from ordivon_protocol import vector_text
from ordivon_protocol.host_workload import (
    WorkloadAdmissionError,
    WorkloadValidationError,
    admit_model_decision,
    validate_host_workload_object,
)



class HostWorkloadContractTests(unittest.TestCase):
    def test_host_consumes_every_normative_workload_vector(self) -> None:
        document = json.loads(vector_text("host-workload-vectors-v1.json"))
        for case in document["cases"]:
            with self.subTest(case=case["caseId"]):
                expected = case["expected"]
                if case["operation"] == "validate":
                    if expected["accepted"]:
                        validate_host_workload_object(case["input"])
                        self.assertEqual(canonical_digest(case["input"]), expected["digest"])
                    else:
                        with self.assertRaises(WorkloadValidationError):
                            validate_host_workload_object(case["input"])
                    continue
                arguments = case["arguments"]
                if expected["accepted"]:
                    admitted = admit_model_decision(
                        arguments["context"],
                        arguments["decision"],
                        current_state_refs=arguments["currentStateRefs"],
                        completed_effect_ids=tuple(arguments["completedEffectIds"]),
                        unresolved_dispatch_ids=tuple(arguments["unresolvedDispatchIds"]),
                    )
                    self.assertEqual(admitted, expected["admitted"])
                    self.assertEqual(canonical_digest(admitted), expected["digest"])
                else:
                    with self.assertRaises(WorkloadAdmissionError) as captured:
                        admit_model_decision(
                            arguments["context"],
                            arguments["decision"],
                            current_state_refs=arguments["currentStateRefs"],
                            completed_effect_ids=tuple(arguments["completedEffectIds"]),
                            unresolved_dispatch_ids=tuple(arguments["unresolvedDispatchIds"]),
                        )
                    self.assertEqual(captured.exception.code, expected["code"])



if __name__ == "__main__":
    unittest.main()
