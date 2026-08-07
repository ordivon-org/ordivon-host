from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import HostStorage, RecoveryAction, TaskReconciler, assess_recovery
from ordivon_host.cognition import CognitionTurnHost
from ordivon_host.domain import StaticRepositoryResolver, TaskState
from tests.test_cognition_turn import (
    DECISION_NODE,
    TASK_ID as COGNITION_TASK_ID,
    cognition_request,
    create_task as create_cognition_task,
)
from tests.test_mutation_task import (
    FakeMutationRuntime,
    host as mutation_host,
    plan as mutation_plan,
)
from tests.test_read_task import FakeRuntime as FakeReadRuntime
from tests.test_read_task import host as read_host
from tests.test_read_task import plan as read_plan


RESOLVER = StaticRepositoryResolver(
    {
        "repository:ordivon-computing": "/root/projects/ordivon-computing",
        "repository:ordivon-host": "/root/projects/ordivon-host",
    }
)


def reconciler(storage: HostStorage, runtime) -> TaskReconciler:
    return TaskReconciler(
        storage,
        runtime,
        clock_ms=itertools.count(50_000).__next__,
        repository_resolver=RESOLVER,
    )


class RecoveryTests(unittest.TestCase):
    def test_read_assessment_and_one_shot_advance(self) -> None:
        runtime = FakeReadRuntime()
        task_plan = read_plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                read_host(storage, runtime).create(task_plan)
                assessment = assess_recovery(storage, task_plan.task_id)
                self.assertEqual(assessment.action, RecoveryAction.ADVANCE_READ)
                self.assertTrue(assessment.automatic)
                result = reconciler(storage, runtime).reconcile(task_plan.task_id)
                self.assertTrue(result.changed)
                self.assertEqual(result.after.revision, 2)
                self.assertEqual(result.after.frontier, "node:read-runtime-readme:read")

    def test_prepared_dispatch_without_runtime_job_is_safe_noop(self) -> None:
        runtime = FakeMutationRuntime()
        task_plan = mutation_plan("prepared-no-job")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = mutation_host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                runner.prepare(task_plan.task_id)
                assessment = assess_recovery(storage, task_plan.task_id)
                self.assertEqual(
                    assessment.action, RecoveryAction.OBSERVE_RUNTIME_DISPATCH
                )
                result = reconciler(storage, runtime).reconcile(task_plan.task_id)
                self.assertFalse(result.changed)
                self.assertEqual(result.after.state, TaskState.WAITING)
                self.assertEqual(runtime.physical_deliveries, 0)

    def test_response_loss_recovery_observes_original_runtime_job(self) -> None:
        runtime = FakeMutationRuntime()
        runtime.drop_first_success = True
        task_plan = mutation_plan("generic-recovery")
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = mutation_host(storage, runtime)
                runner.create(task_plan)
                runner.open_workspace(task_plan.task_id)
                prepared = runner.prepare(task_plan.task_id)
                unknown = runner.deliver(prepared)
                self.assertEqual(unknown.state, TaskState.WAITING)
                self.assertTrue(runtime.response_dropped)
                result = reconciler(storage, runtime).reconcile(task_plan.task_id)
                self.assertTrue(result.changed)
                self.assertEqual(result.after.state, TaskState.VERIFYING)
                self.assertEqual(runtime.physical_deliveries, 1)

    def test_cognition_invocation_is_never_automatic(self) -> None:
        runtime = FakeReadRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_cognition_task(storage)
                turn = CognitionTurnHost(
                    storage, clock_ms=itertools.count(100).__next__
                )
                prepared = turn.prepare(
                    task_id=COGNITION_TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                turn.prepare_invocation(prepared, gateway_id="gateway:test")
                assessment = assess_recovery(storage, COGNITION_TASK_ID)
                self.assertEqual(assessment.action, RecoveryAction.EXTERNAL_COGNITION_REQUIRED)
                self.assertFalse(assessment.automatic)
                result = reconciler(storage, runtime).reconcile(COGNITION_TASK_ID)
                self.assertFalse(result.changed)
                self.assertEqual(result.after.revision, assessment.revision)

    def test_terminal_task_is_noop(self) -> None:
        runtime = FakeReadRuntime()
        task_plan = read_plan()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                runner = read_host(storage, runtime)
                runner.create(task_plan)
                runner.run(task_plan.task_id)
                assessment = assess_recovery(storage, task_plan.task_id)
                self.assertEqual(assessment.action, RecoveryAction.NONE)
                self.assertFalse(assessment.automatic)


if __name__ == "__main__":
    unittest.main()
