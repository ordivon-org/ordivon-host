from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage, RecoveryAction, TaskState, assess_recovery


class RecoveryProjectionTests(unittest.TestCase):
    def test_nonterminal_unknown_workload_is_never_automatic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                HostKernel(storage, clock_ms=itertools.count(1).__next__, owner_id="host:test:recovery").create_task(
                    event_id="event:recovery:create", kind=EventKind.TASK_CREATED,
                    task_id="task:recovery", goal_id="goal:recovery", payload={},
                    state=TaskState.READY, frontier=("node:recovery:continue",),
                )
                assessment=assess_recovery(storage,"task:recovery")
                self.assertEqual(assessment.action, RecoveryAction.UNSUPPORTED)
                self.assertFalse(assessment.automatic)

    def test_terminal_task_requires_no_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                kernel=HostKernel(storage, clock_ms=itertools.count(1).__next__, owner_id="host:test:recovery")
                created=kernel.create_task(
                    event_id="event:recovery:create", kind=EventKind.TASK_CREATED,
                    task_id="task:recovery", goal_id="goal:recovery", payload={},
                    state=TaskState.READY, frontier=("node:recovery:continue",),
                ).projection
                with kernel.locked_task(created.task_id, expected_revision=created.revision, expected_state=created.state) as locked:
                    locked.commit(event_id="event:recovery:complete", kind=EventKind.TASK_STATE_CHANGED, payload={}, state=TaskState.COMPLETED, frontier=())
                assessment=assess_recovery(storage,"task:recovery")
                self.assertEqual(assessment.action, RecoveryAction.NONE)
                self.assertFalse(assessment.automatic)


if __name__ == "__main__":
    unittest.main()
