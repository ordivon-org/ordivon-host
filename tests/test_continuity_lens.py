from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host import (
    ExternalContinuityHost,
    HostStorage,
    WorkingCheckpoint,
    WorkingCheckpointRuntime,
)
from ordivon_host.continuity_lens import build_continuity_lens


class ContinuityLensTests(unittest.TestCase):
    def test_compact_lens_preserves_hint_and_revision_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            now = 0

            def clock_ms() -> int:
                nonlocal now
                now += 1
                return now

            with HostStorage(state) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock_ms)
                host.adopt(
                    task_id="task:lens:now",
                    goal_id="goal:lens:a",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:lens:now",
                        objective="perform one bounded owner-local pilot",
                        frontier=(
                            "ATTENTION=NOW\n"
                            "CARRIER=RETAIN\n"
                            "WAKE=owner requests the next pilot episode\n"
                            "Run the exact local experiment."
                        ),
                        runtime=WorkingCheckpointRuntime(
                            workspace_id="ws-lens-now",
                            observed_head_revision="a" * 40,
                        ),
                    ),
                )
                host.adopt(
                    task_id="task:lens:wait",
                    goal_id="goal:lens:a",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:lens:wait",
                        objective="wait for one external event",
                        frontier=(
                            "ATTENTION=WAIT_EVENT / no manufactured trigger.\n"
                            "CARRIER=CLOSE_CLEAN\n"
                            "WAKE=external event arrives"
                        ),
                    ),
                )
                host.adopt(
                    task_id="task:lens:unspecified",
                    goal_id="goal:lens:b",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:lens:unspecified",
                        objective="preserve a legacy checkpoint without hints",
                        frontier="Legacy frontier remains resumable.",
                    ),
                )

                lens = build_continuity_lens(storage, item_limit=2)
                self.assertEqual(lens["kind"], "ordivon.host-continuity-lens")
                self.assertEqual(
                    lens["truthRole"],
                    "derived-non-authoritative-continuity-navigation",
                )
                self.assertEqual(lens["summary"]["scopeCount"], 3)
                self.assertEqual(lens["summary"]["matchingCount"], 3)
                self.assertEqual(len(lens["items"]), 2)
                self.assertTrue(lens["hasMore"])

                attention = {
                    item["attentionHint"]: item["count"]
                    for item in lens["summary"]["byAttention"]
                }
                carrier = {
                    item["carrierHint"]: item["count"]
                    for item in lens["summary"]["byCarrier"]
                }
                self.assertEqual(
                    attention, {"NOW": 1, "UNSPECIFIED": 1, "WAIT_EVENT": 1}
                )
                self.assertEqual(
                    carrier, {"CLOSE_CLEAN": 1, "RETAIN": 1, "UNSPECIFIED": 1}
                )
                for item in lens["items"]:
                    self.assertEqual(item["revision"], item["checkpointRevision"])
                    self.assertTrue(item["checkpointDigest"].startswith("sha256:"))
                    self.assertGreater(item["checkpointCanonicalBytes"], 0)

                selected = build_continuity_lens(
                    storage,
                    goal_id="goal:lens:a",
                    attention="NOW",
                    carrier="RETAIN",
                    item_limit=20,
                )
                self.assertEqual(selected["summary"]["scopeCount"], 2)
                self.assertEqual(selected["summary"]["matchingCount"], 1)
                self.assertEqual(
                    selected["items"][0]["taskId"], "task:lens:now"
                )
                self.assertEqual(
                    selected["items"][0]["runtimeWorkspaceId"], "ws-lens-now"
                )
                self.assertEqual(
                    selected["items"][0]["wakeHint"],
                    "owner requests the next pilot episode",
                )

    def test_lens_rejects_priority_like_free_text_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(ValueError, "uppercase token"):
                    build_continuity_lens(storage, attention="do this first")
                with self.assertRaisesRegex(ValueError, "carrier filter"):
                    build_continuity_lens(storage, carrier="DELETE")


if __name__ == "__main__":
    unittest.main()
