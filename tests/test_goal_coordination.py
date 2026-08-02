from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host import (
    CoordinationSuperseded,
    EventKind,
    GoalCoordinatorHost,
    HostKernel,
    HostStorage,
    TaskDescriptor,
    TaskState,
    VerificationReceipt,
    VerificationResultItem,
)


class Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


class GoalCoordinationTests(unittest.TestCase):
    def test_goal_snapshot_and_partial_result_application_resume_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            clock = Clock()
            goal_id = "goal:station-zero:rescue"
            actor_ids = ("engineer-01", "medic-01", "security-01")
            task_ids = tuple(f"task:station-zero:actor:{actor}" for actor in actor_ids)
            coordinator_id = "task:station-zero:coordinator"

            with HostStorage(root) as storage:
                kernel = HostKernel(
                    storage, clock_ms=clock, owner_id="host:test-goal-coordination"
                )
                descriptors = [
                    TaskDescriptor(
                        task_id=task_id,
                        goal_id=goal_id,
                        workload_id="ordivon.game.actor-turn.v1",
                        assignee_ref=f"actor:{actor}",
                        provider_policy_ref="provider-policy:fixture-team-v1",
                        domain_ref="game-run:station-zero",
                    )
                    for actor, task_id in zip(actor_ids, task_ids, strict=True)
                ]
                descriptors.append(
                    TaskDescriptor(
                        task_id=coordinator_id,
                        goal_id=goal_id,
                        workload_id="ordivon.game.team-coordinator.v1",
                        assignee_ref=None,
                        provider_policy_ref=None,
                        domain_ref="game-run:station-zero",
                    )
                )
                for descriptor in descriptors:
                    descriptor_object = storage.put_object(
                        descriptor.to_dict(), kind="task-descriptor"
                    )
                    actor = descriptor.assignee_ref.removeprefix("actor:") if descriptor.assignee_ref else "coordinator"
                    kernel.create_task(
                        event_id=f"event:{descriptor.task_id}:created",
                        kind=EventKind.TASK_CREATED,
                        task_id=descriptor.task_id,
                        goal_id=descriptor.goal_id,
                        payload={
                            "descriptorDigest": descriptor.digest,
                            "descriptorObjectDigest": descriptor_object.digest,
                        },
                        frontier=(f"node:station-zero:{actor}:{'collect' if actor == 'coordinator' else 'decide'}",),
                        referenced_objects=(descriptor_object,),
                    )
                coordinator = GoalCoordinatorHost(storage, clock_ms=clock)
                frozen = coordinator.snapshot(goal_id)
                self.assertEqual(
                    tuple(item.task_id for item in frozen.tasks),
                    tuple(sorted((*task_ids, coordinator_id))),
                )
                self.assertEqual(storage.journal.tasks_for_goal(goal_id), tuple(
                    storage.journal.get_task(task_id)
                    for task_id in sorted((*task_ids, coordinator_id))
                ))

                results = tuple(
                    VerificationResultItem(
                        subject_ref=task_id,
                        decision_digest=canonical_digest({"taskId": task_id, "decision": "wait"}),
                        status="succeeded" if task_id != task_ids[2] else "not-selected",
                        reason=None if task_id != task_ids[2] else "resource-conflict",
                        evidence_digest=canonical_digest({"worldRevision": 1, "taskId": task_id}),
                    )
                    for task_id in task_ids
                )
                verification = VerificationReceipt(
                    dispatch_id="dispatch:station-zero:team:r0",
                    method="game-tick-intent-receipts.v1",
                    accepted=True,
                    observation_digest=canonical_digest({"worldEvent": "r1"}),
                    result_items=results,
                )
                first_ref = frozen.task(task_ids[0])
                first = coordinator.apply_verification_result(
                    task_ref=first_ref,
                    verification=verification,
                    next_frontier="node:station-zero:engineer-01:decide",
                    event_id="event:station-zero:engineer:r1-result",
                )
                duplicate = coordinator.apply_verification_result(
                    task_ref=first_ref,
                    verification=verification,
                    next_frontier="node:station-zero:engineer-01:decide",
                    event_id="event:station-zero:engineer:r1-result",
                )
                self.assertEqual(first, duplicate)
                coordinator.apply_verification_result(
                    task_ref=frozen.task(task_ids[1]),
                    verification=verification,
                    next_frontier="node:station-zero:medic-01:decide",
                    event_id="event:station-zero:medic:r1-result",
                )

            with HostStorage(root) as storage:
                fresh = GoalCoordinatorHost(storage, clock_ms=clock)
                third = fresh.apply_verification_result(
                    task_ref=frozen.task(task_ids[2]),
                    verification=verification,
                    next_frontier="node:station-zero:security-01:decide",
                    event_id="event:station-zero:security:r1-result",
                )
                self.assertEqual(third.state, TaskState.READY)
                self.assertEqual(third.revision, frozen.task(task_ids[2]).revision + 1)
                self.assertEqual(
                    storage.journal.get_task(coordinator_id).revision,
                    frozen.task(coordinator_id).revision,
                )
                with self.assertRaises(CoordinationSuperseded):
                    fresh.assert_current(frozen)


if __name__ == "__main__":
    unittest.main()
