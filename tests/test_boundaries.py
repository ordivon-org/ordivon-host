from __future__ import annotations

from pathlib import Path
import unittest

from anc_canonical import canonical_digest
from ordivon_host import TaskProjection
from ordivon_host.engine import DeterministicReadHost, GuardedMutationHost
from ordivon_host.engine.mutation import GuardedMutationHost as MutationPackageHost
from ordivon_host.runtime import RuntimeClient
from anc_effect_ir import EffectMode


class HostBoundaryTests(unittest.TestCase):
    def test_host_uses_promoted_protocol(self) -> None:
        self.assertEqual(canonical_digest({"mode": EffectMode.CHANGE.value})[:7], "sha256:")

    def test_workload_implementations_are_explicit_engine_imports(self) -> None:
        self.assertIs(MutationPackageHost, GuardedMutationHost)
        self.assertTrue(callable(DeterministicReadHost))
        self.assertTrue(hasattr(RuntimeClient, "call_tool"))

        import ordivon_host

        for name in (
            "CodeChangeHost",
            "CodeChangePlan",
            "DeterministicReadHost",
            "GuardedMutationHost",
            "GuardedMutationPlan",
            "ReadTaskPlan",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(ordivon_host, name))

    def test_harness_implementation_is_not_bundled_in_host(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "ordivon_host"
        self.assertFalse((source / "harness").exists())
        import ordivon_host

        self.assertFalse(hasattr(ordivon_host, "HarnessHost"))
        self.assertFalse(hasattr(ordivon_host, "EffectLifecycleHost"))
        self.assertFalse(hasattr(ordivon_host, "owner_of"))
        self.assertTrue(hasattr(ordivon_host, "ExternalExecutorCoordinator"))
        self.assertTrue(hasattr(ordivon_host, "ExternalRunBinding"))

    def test_legacy_mutation_module_is_removed(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "ordivon_host" / "engine"
        self.assertFalse((source / "mutation_task.py").exists())

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


class DurableObjectCodecTests(unittest.TestCase):
    def test_workload_plan_rejects_unknown_schema_version_explicitly(self) -> None:
        from ordivon_host.engine.code_change import CodeChangePlan
        from ordivon_host.objects import UnsupportedObjectVersion

        value = {
            "schemaVersion": 99,
            "kind": "ordivon.host-code-change-plan",
        }
        with self.assertRaises(UnsupportedObjectVersion):
            CodeChangePlan.from_dict(value)
