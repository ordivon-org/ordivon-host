from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import EventKind, HostStorage, TaskState
from ordivon_host.kernel import (
    HostKernel,
    HostKernelError,
    TaskFrontierMismatch,
    TaskRevisionMismatch,
    worker_owner_id,
)


TASK_ID = "task:kernel-test"
GOAL_ID = "goal:kernel-test"


def kernel(storage: HostStorage) -> HostKernel:
    return HostKernel(
        storage,
        clock_ms=itertools.count(100).__next__,
        owner_id="host:kernel-test",
        lease_ttl_ms=1_000,
    )


class HostKernelTests(unittest.TestCase):
    def test_default_worker_identity_is_instance_unique(self) -> None:
        first = worker_owner_id("host:test-worker")
        second = worker_owner_id("host:test-worker")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("host:test-worker:pid-"))

    def test_create_lock_commit_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                created = control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                self.assertEqual(created.projection.revision, 1)
                with control.locked_task(
                    TASK_ID,
                    expected_revision=1,
                    expected_state=TaskState.READY,
                    expected_frontier=("node:kernel:one",),
                    label="kernel",
                ) as locked:
                    committed = locked.commit(
                        event_id="event:kernel:r2",
                        kind=EventKind.TASK_FRONTIER_CHANGED,
                        payload={"stage": "advanced"},
                        frontier=("node:kernel:two",),
                    )
                self.assertEqual(committed.projection.revision, 2)
                self.assertEqual(
                    committed.projection.ready_frontier,
                    ("node:kernel:two",),
                )

            with HostStorage(directory) as reopened:
                projection = reopened.journal.get_task(TASK_ID)
                self.assertEqual(projection, committed.projection)
                self.assertEqual(reopened.rebuild_task(TASK_ID), committed.projection)

    def test_revision_and_frontier_mismatch_fail_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                with self.assertRaisesRegex(TaskRevisionMismatch, "revision is 1"):
                    with control.locked_task(TASK_ID, expected_revision=2):
                        self.fail("stale revision entered the critical section")
                with self.assertRaises(TaskFrontierMismatch):
                    control.current_snapshot(
                        TASK_ID,
                        expected_frontier=("node:kernel:other",),
                    )
                self.assertEqual(storage.journal.event_count(TASK_ID), 1)

    def test_workload_error_factory_preserves_public_exception(self) -> None:
        class WorkloadSuperseded(RuntimeError):
            pass

        def errors(category: str, message: str) -> Exception:
            if category == "revision":
                return WorkloadSuperseded(message)
            return RuntimeError(message)

        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                with self.assertRaisesRegex(WorkloadSuperseded, "expected 2"):
                    control.current_snapshot(
                        TASK_ID,
                        expected_revision=2,
                        error_factory=errors,
                    )

    def test_locked_transition_commits_at_most_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                with control.locked_task(TASK_ID) as locked:
                    locked.commit(
                        event_id="event:kernel:r2",
                        kind=EventKind.TASK_STATE_CHANGED,
                        payload={"stage": "completed"},
                        state=TaskState.COMPLETED,
                        frontier=(),
                    )
                    with self.assertRaisesRegex(HostKernelError, "only once"):
                        locked.commit(
                            event_id="event:kernel:r3",
                            kind=EventKind.TASK_STATE_CHANGED,
                            payload={"stage": "invalid"},
                        )
                self.assertEqual(storage.journal.event_count(TASK_ID), 2)

    def test_lease_is_released_when_transition_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with control.locked_task(TASK_ID):
                        raise RuntimeError("injected transition failure")
                lease = storage.journal.acquire_lease(
                    TASK_ID,
                    owner_id="host:after-failure",
                    now_ms=1_000,
                    ttl_ms=1_000,
                )
                storage.journal.release_lease(lease)

    def test_running_transition_preserves_active_node_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = kernel(storage)
                control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "running"},
                    state=TaskState.RUNNING,
                    active_node_id="node:kernel:active",
                )
                with control.locked_task(TASK_ID) as locked:
                    advanced = locked.commit(
                        event_id="event:kernel:r2",
                        kind=EventKind.TASK_FRONTIER_CHANGED,
                        payload={"stage": "still-running"},
                    )
                self.assertEqual(advanced.projection.state, TaskState.RUNNING)
                self.assertEqual(
                    advanced.projection.active_node_id,
                    "node:kernel:active",
                )

    def test_timestamp_never_regresses(self) -> None:
        values = iter((50, 40, 40))
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                control = HostKernel(
                    storage,
                    clock_ms=values.__next__,
                    owner_id="host:clock-test",
                )
                created = control.create_task(
                    event_id="event:kernel:r1",
                    kind=EventKind.TASK_CREATED,
                    task_id=TASK_ID,
                    goal_id=GOAL_ID,
                    payload={"stage": "created"},
                    frontier=("node:kernel:one",),
                )
                with control.locked_task(TASK_ID) as locked:
                    advanced = locked.commit(
                        event_id="event:kernel:r2",
                        kind=EventKind.TASK_FRONTIER_CHANGED,
                        payload={"stage": "advanced"},
                    )
                self.assertEqual(created.projection.updated_at_ms, 50)
                self.assertEqual(advanced.projection.updated_at_ms, 51)


if __name__ == "__main__":
    unittest.main()
