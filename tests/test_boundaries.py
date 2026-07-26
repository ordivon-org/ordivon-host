from __future__ import annotations

import unittest

from anc_canonical import canonical_digest
from ordivon_host import ComponentOwner, TaskProjection, owner_of
from ordivon_semantics import EffectState


class HostBoundaryTests(unittest.TestCase):
    def test_ownership_is_executable(self) -> None:
        self.assertEqual(owner_of("goal"), ComponentOwner.HOST)
        self.assertEqual(owner_of("job"), ComponentOwner.RUNTIME)
        self.assertEqual(owner_of("protocol"), ComponentOwner.COMPUTING)
        self.assertEqual(owner_of("provider-session"), ComponentOwner.PROVIDER)

    def test_unknown_objects_fail_closed(self) -> None:
        with self.assertRaises(KeyError):
            owner_of("generic-agent-state")

    def test_incubator_only_uses_promoted_protocol(self) -> None:
        self.assertEqual(canonical_digest({"state": EffectState.UNKNOWN.value})[:7], "sha256:")

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
