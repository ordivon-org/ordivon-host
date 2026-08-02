from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host import HostKernel, HostStorage, TaskDescriptor
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
                descriptor_object = storage.put_object(
                    descriptor.to_dict(), kind="task-descriptor"
                )
                created = HostKernel(
                    storage, clock_ms=clock, owner_id="host:test-descriptor"
                ).create_task(
                    event_id="event:descriptor:created",
                    kind=EventKind.TASK_CREATED,
                    task_id=descriptor.task_id,
                    goal_id=descriptor.goal_id,
                    payload={
                        "descriptorDigest": descriptor.digest,
                        "descriptorObjectDigest": descriptor_object.digest,
                    },
                    frontier=("node:descriptor:test:decide",),
                    referenced_objects=(descriptor_object,),
                ).projection
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
