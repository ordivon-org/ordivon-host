from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import HostKernel, HostStorage, TaskState, operator_handoff
from ordivon_host.domain import EventKind


class HandoffTests(unittest.TestCase):
    def test_unknown_dispatch_projects_reconcile_without_duplicate_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=itertools.count(1_000).__next__,
                    owner_id="host:test-handoff",
                )
                created = kernel.create_task(
                    event_id="event:handoff:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:handoff",
                    goal_id="goal:handoff",
                    payload={"descriptorDigest": "sha256:" + ("a" * 64)},
                    frontier=("node:handoff:dispatch",),
                ).projection
                with kernel.locked_task(
                    created.task_id,
                    expected_revision=created.revision,
                    expected_state=TaskState.READY,
                    expected_frontier=created.ready_frontier,
                ) as locked:
                    locked.commit(
                        event_id="event:handoff:unknown",
                        kind=EventKind.RUNTIME_OUTCOME_UNKNOWN,
                        payload={
                            "descriptorDigest": "sha256:" + ("a" * 64),
                            "effectDigest": "sha256:" + ("b" * 64),
                            "dispatchDigest": "sha256:" + ("c" * 64),
                            "clientRequestId": "request-handoff",
                        },
                        state=TaskState.WAITING,
                        frontier=("node:handoff:reconcile",),
                    )
                capsule = operator_handoff(storage, "task:handoff")
                self.assertEqual(capsule.task_state, TaskState.WAITING)
                self.assertEqual(capsule.dispatch_object_digest, "sha256:" + ("c" * 64))
                self.assertEqual(capsule.next_admissible, ("reconcile-existing-dispatch",))
                self.assertEqual(capsule.must_not_repeat_object_digests, ())
                pinned = operator_handoff(
                    storage,
                    "task:handoff",
                    expected_revision=created.revision + 1,
                )
                self.assertEqual(pinned, capsule)
                with self.assertRaisesRegex(
                    ValueError,
                    r"stale Operator Handoff revision: expected 1, current 2",
                ):
                    operator_handoff(
                        storage,
                        "task:handoff",
                        expected_revision=created.revision,
                    )
                self.assertEqual(
                    storage.read_task_event("task:handoff").payload_digest,
                    capsule.event_payload_digest,
                )

    def test_completed_effect_is_exposed_as_must_not_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=itertools.count(2_000).__next__,
                    owner_id="host:test-completed-handoff",
                )
                created = kernel.create_task(
                    event_id="event:completed:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:completed-handoff",
                    goal_id="goal:completed-handoff",
                    payload={},
                    frontier=("node:completed:verify",),
                ).projection
                with kernel.locked_task(
                    created.task_id,
                    expected_revision=1,
                    expected_state=TaskState.READY,
                    expected_frontier=created.ready_frontier,
                ) as locked:
                    locked.commit(
                        event_id="event:completed:verified",
                        kind=EventKind.VERIFICATION_ACCEPTED,
                        payload={
                            "effectDigest": "sha256:" + ("d" * 64),
                            "outcomeDigest": "sha256:" + ("e" * 64),
                        },
                        state=TaskState.COMPLETED,
                        frontier=(),
                    )
                capsule = operator_handoff(storage, created.task_id)
                self.assertEqual(
                    capsule.must_not_repeat_object_digests,
                    ("sha256:" + ("d" * 64),),
                )
                self.assertEqual(capsule.next_admissible, ("inspect-terminal-outcome",))


if __name__ == "__main__":
    unittest.main()
