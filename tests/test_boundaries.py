from __future__ import annotations

from pathlib import Path
import unittest

from anc_canonical import canonical_digest
from anc_effect_ir import EffectMode
from ordivon_host import TaskProjection


class HostBoundaryTests(unittest.TestCase):
    def test_host_uses_promoted_protocol(self) -> None:
        self.assertEqual(canonical_digest({"mode": EffectMode.CHANGE.value})[:7], "sha256:")

    def test_historical_execution_owners_are_not_bundled_in_host_surface(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "ordivon_host"
        self.assertFalse((source / "harness").exists())
        self.assertFalse((source / "engine").exists())
        import ordivon_host
        for name in (
            "HarnessHost",
            "EffectLifecycleHost",
            "TaskReconciler",
            "RecoveryResult",
            "ExternalExecutorCoordinator",
            "GoalCoordinatorHost",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(ordivon_host, name))

    def test_cognition_surface_is_context_only(self) -> None:
        import ordivon_host.cognition as cognition
        self.assertTrue(hasattr(cognition, "ContextBlock"))
        self.assertTrue(hasattr(cognition, "BlockKind"))
        self.assertTrue(hasattr(cognition, "Freshness"))
        for name in ("CognitionHost", "OpenProposalHost", "RepositoryMutationProposalCompiler"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cognition, name))

    def test_projection_decoder_rejects_coerced_revision_types(self) -> None:
        value = {
            "taskId": "task:strict",
            "goalId": "goal:strict",
            "state": "ready",
            "activeNodeId": None,
            "readyFrontier": ["node:inspect"],
            "revision": "1",
            "updatedAtMs": 1,
        }
        with self.assertRaisesRegex(ValueError, "must be integers"):
            TaskProjection.from_dict(value)
        value["revision"] = True
        with self.assertRaisesRegex(ValueError, "must be integers"):
            TaskProjection.from_dict(value)


if __name__ == "__main__":
    unittest.main()
