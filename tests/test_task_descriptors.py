from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host import HostKernel, HostStorage, TaskDescriptor
from ordivon_host.effects import EffectLifecycleHost
from ordivon_host.domain import EventKind


class TaskDescriptorTests(unittest.TestCase):
    def test_descriptor_is_immutable_semantic_identity_not_projection_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            clock = iter(range(1, 100)).__next__
            descriptor = TaskDescriptor(
                task_id="task:descriptor:test",
                goal_id="goal:descriptor:test",
                workload_id="ordivon.game.actor-turn.v1",
                assignee_ref="actor:engineer-01",
                provider_policy_ref="provider-policy:fixture",
                domain_ref="game-run:test",
            )
            with HostStorage(root) as storage:
                host = EffectLifecycleHost(storage, clock_ms=clock)
                created = host.create_task(
                    descriptor,
                    frontier="node:descriptor:test:decide",
                )
                self.assertEqual(storage.read_task_descriptor(created.task_id), descriptor)
                head = storage.read_task_event(created.task_id)
                self.assertIsInstance(head.data, dict)
                self.assertEqual(head.data["descriptorDigest"], descriptor.digest)
                self.assertNotEqual(
                    head.data["descriptorDigest"],
                    head.data["descriptorObjectDigest"],
                )
                self.assertEqual(storage.journal.tasks_for_goal(descriptor.goal_id), (created,))

                legacy = HostKernel(
                    storage,
                    clock_ms=clock,
                    owner_id="host:test-descriptor-legacy",
                ).create_task(
                    event_id="event:legacy:created",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:legacy:no-descriptor",
                    goal_id="goal:legacy:no-descriptor",
                    payload={"legacy": True},
                    frontier=("node:legacy:ready",),
                ).projection
                self.assertIsNone(storage.read_task_descriptor(legacy.task_id))


if __name__ == "__main__":
    unittest.main()
