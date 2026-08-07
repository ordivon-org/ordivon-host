from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

import ordivon_host.cognition as cognition
from ordivon_host import EventKind, RecoveryAction
from ordivon_host.cognition import CognitionHost, OpenProposalHost

ROOT = Path(__file__).resolve().parents[1]


class HostH3CognitionBoundaryTests(unittest.TestCase):
    def test_public_cognition_surface_is_semantic_not_provider_shaped(self) -> None:
        required = {
            "ActionSelection",
            "CognitionExecutionEvidence",
            "CognitionHost",
            "CognitionResultKind",
            "CognitionWorkRequest",
            "PreparedCognitionRequest",
            "ClosedChoiceContextRequest",
            "OpenContextRequest",
        }
        self.assertTrue(required.issubset(set(cognition.__all__)))
        for legacy in (
            "ModelDecision",
            "ModelInvocationIntent",
            "PreparedInvocation",
            "PreparedCognition",
            "CognitionTurnHost",
            "CognitionRequest",
            "OpenCognitionRequest",
            "ScriptedPreferenceAdapter",
        ):
            self.assertNotIn(legacy, cognition.__all__)
            self.assertFalse(hasattr(cognition, legacy))
        self.assertFalse(hasattr(CognitionHost, "prepare_invocation"))
        self.assertFalse(hasattr(CognitionHost, "admit_decision"))
        self.assertFalse(hasattr(OpenProposalHost, "prepare_invocation"))

    def test_provider_execution_packages_are_deleted(self) -> None:
        self.assertFalse((ROOT / "src/ordivon_host/providers").exists())
        self.assertFalse((ROOT / "src/ordivon_host/legacy_provider_execution").exists())
        self.assertFalse((ROOT / "src/ordivon_host/cognition/adapters.py").exists())
        self.assertFalse((ROOT / "src/ordivon_host/cognition/proposal_adapters.py").exists())

    def test_current_cognition_writer_contains_no_provider_protocol_vocabulary(self) -> None:
        forbidden = (
            "ModelInvocation",
            "gatewayId",
            "gateway_id",
            "adapterId",
            "adapter_id",
            "invocationId",
            "prepare_invocation",
            "model-invocation",
        )
        for relative in (
            "src/ordivon_host/cognition/turn.py",
            "src/ordivon_host/cognition/proposal_turn.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, source, relative)

    def test_only_new_cognition_event_heads_are_valid(self) -> None:
        self.assertEqual(EventKind.COGNITION_REQUESTED.value, "cognition.requested")
        self.assertEqual(
            EventKind.COGNITION_SELECTION_ADMITTED.value,
            "cognition.selection-admitted",
        )
        self.assertEqual(
            EventKind.COGNITION_PROPOSAL_RESOLVED.value,
            "cognition.proposal-resolved",
        )
        for old in (
            "cognition.context-compiled",
            "cognition.invocation-prepared",
            "cognition.decision-admitted",
        ):
            with self.assertRaises(ValueError):
                EventKind(old)

    def test_recovery_requests_a_semantic_result_not_provider_execution(self) -> None:
        self.assertEqual(
            RecoveryAction.COGNITION_RESULT_REQUIRED.value,
            "cognition-result-required",
        )
        self.assertFalse(hasattr(RecoveryAction, "EXTERNAL_COGNITION_REQUIRED"))

    def test_fresh_cognition_import_has_no_provider_execution_modules(self) -> None:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys; import ordivon_host.cognition; "
                    "print(json.dumps([name for name in sys.modules "
                    "if name.startswith('ordivon_host.providers') "
                    "or name.startswith('ordivon_host.legacy_provider_execution')]))"
                ),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(probe.stdout), [])


if __name__ == "__main__":
    unittest.main()
