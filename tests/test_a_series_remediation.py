from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import itertools
from pathlib import Path
import stat
import tempfile
import threading
import unittest

from anc_canonical import canonical_digest

from ordivon_host.effects import EffectLifecycleError, EffectLifecycleHost
from ordivon_host import (
    CoordinationError,
    DispatchEnvelope,
    EventKind,
    GoalCoordinatorHost,
    HostKernel,
    HostStorage,
    ObservationEnvelope,
    StateRef,
    TaskDescriptor,
    TaskOutcome,
    TaskProjection,
    TaskState,
    VerificationReceipt,
    VerificationResultItem,
    assess_recovery,
)
from ordivon_host.config import read_token_file
from ordivon_host.journal import EventConflict, LeaseConflict
from ordivon_host.kernel import TaskStateMismatch


class ManualClock:
    def __init__(self, value: int = 1) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class FailedExecutor:
    executor_id = "executor:a-series-failure"

    def deliver(self, dispatch, request):
        return ObservationEnvelope(
            dispatch_id=dispatch.dispatch_id,
            executor_id=self.executor_id,
            status="failed",
            payload_digest=canonical_digest({"failed": True}),
        )

    def observe(self, dispatch, request):
        return None


class ASeriesRemediationTests(unittest.TestCase):
    def test_lease_takeover_fences_old_writer_before_durable_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock(10)
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=clock,
                    owner_id="host:lease-old",
                    lease_ttl_ms=1,
                )
                created = kernel.create_task(
                    event_id="event:lease-fence:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:lease-fence",
                    goal_id="goal:lease-fence",
                    payload={},
                    frontier=("node:lease-fence:start",),
                ).projection
                with self.assertRaisesRegex(LeaseConflict, "superseded, or expired"):
                    with kernel.locked_task(
                        created.task_id,
                        expected_revision=created.revision,
                        expected_state=TaskState.READY,
                        expected_frontier=created.ready_frontier,
                    ) as locked:
                        clock.value = 100
                        takeover = storage.journal.acquire_lease(
                            created.task_id,
                            owner_id="host:lease-new",
                            now_ms=clock.value,
                            ttl_ms=100,
                        )
                        locked.commit(
                            event_id="event:lease-fence:stale-commit",
                            kind=EventKind.TASK_FRONTIER_CHANGED,
                            payload={},
                            frontier=("node:lease-fence:stale",),
                        )
                current = storage.journal.get_task(created.task_id)
                self.assertEqual(current, created)
                self.assertEqual(storage.journal.event_count(created.task_id), 1)
                self.assertEqual(storage.journal.lease_records(), (takeover,))

    def test_noncreation_event_cannot_bypass_kernel_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                first = TaskProjection(
                    task_id="task:lease-bypass",
                    goal_id="goal:lease-bypass",
                    state=TaskState.READY,
                    active_node_id=None,
                    ready_frontier=("node:lease-bypass:start",),
                    revision=1,
                    updated_at_ms=1,
                )
                storage.record_task_event(
                    event_id="event:lease-bypass:create",
                    kind=EventKind.TASK_CREATED,
                    payload={},
                    projection=first,
                    expected_revision=0,
                )
                with self.assertRaisesRegex(LeaseConflict, "requires an exact live lease"):
                    storage.record_task_event(
                        event_id="event:lease-bypass:advance",
                        kind=EventKind.TASK_FRONTIER_CHANGED,
                        payload={},
                        projection=replace(
                            first,
                            ready_frontier=("node:lease-bypass:next",),
                            revision=2,
                            updated_at_ms=2,
                        ),
                        expected_revision=1,
                    )
                self.assertEqual(storage.journal.get_task(first.task_id), first)

    def test_extension_event_is_thread_stable_and_core_typos_fail_closed(self) -> None:
        for value in (
            "task.creatd",
            "effect.dispatch-preparedd",
            "runtime.dispatch-observedd",
            "cognition.context-compiledd",
            "wakeup.scheduled",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EventKind(value)

        barrier = threading.Barrier(32)

        def construct(_: int) -> EventKind:
            barrier.wait(timeout=5)
            return EventKind("harness.concurrent-stable")

        with ThreadPoolExecutor(max_workers=32) as pool:
            values = tuple(pool.map(construct, range(32)))
        self.assertEqual(len({id(value) for value in values}), 1)
        self.assertIs(values[0], EventKind("harness.concurrent-stable"))

    def test_failed_generic_effect_cannot_claim_completed_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1).__next__
            descriptor = TaskDescriptor(
                task_id="task:effect-failure",
                goal_id="goal:effect-failure",
                workload_id="audit.effect.v1",
            )
            request = {"operation": "fail"}
            effect = {
                "schemaVersion": 1,
                "kind": "audit.effect",
                "effectId": "effect:a-series:failure",
            }
            dispatch = DispatchEnvelope(
                dispatch_id="dispatch:a-series:failure",
                effect_id="effect:a-series:failure",
                executor_id=FailedExecutor.executor_id,
                request_digest=canonical_digest(request),
                idempotency_key="a-series-failure",
                required_state_refs=(
                    StateRef(
                        "state:a-series",
                        canonical_digest({"revision": 1}),
                    ),
                ),
                expected_observation_kind="audit.failure-observation.v1",
            )
            with HostStorage(directory) as storage:
                host = EffectLifecycleHost(storage, clock_ms=clock)
                host.create_task(descriptor, frontier="node:effect-failure:prepare")
                prepared = host.prepare(
                    task_id=descriptor.task_id,
                    prepare_frontier="node:effect-failure:prepare",
                    reconcile_frontier="node:effect-failure:reconcile",
                    verify_frontier="node:effect-failure:verify",
                    result_frontier="node:effect-failure:result",
                    effect=effect,
                    request=request,
                    dispatch=dispatch,
                )
                blocked = host.deliver(prepared, FailedExecutor())
                self.assertEqual(blocked.state, TaskState.BLOCKED)
                with self.assertRaisesRegex(
                    EffectLifecycleError,
                    "cannot claim successful completion",
                ):
                    host.complete(
                        descriptor.task_id,
                        TaskOutcome(
                            task_id=descriptor.task_id,
                            goal_id=descriptor.goal_id,
                            status="completed",
                            verification_digest=None,
                        ),
                    )
                current = storage.journal.get_task(descriptor.task_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, blocked.revision)
                self.assertEqual(current.state, TaskState.BLOCKED)

    def test_experimental_effect_recovery_is_explicitly_manual(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1).__next__
            descriptor = TaskDescriptor(
                task_id="task:effect-recovery-manual",
                goal_id="goal:effect-recovery-manual",
                workload_id="ordivon.game.actor-turn.v1",
            )
            request = {"operation": "unknown"}
            effect = {
                "schemaVersion": 1,
                "kind": "audit.effect",
                "effectId": "effect:a-series:manual-recovery",
            }
            dispatch = DispatchEnvelope(
                dispatch_id="dispatch:a-series:manual-recovery",
                effect_id="effect:a-series:manual-recovery",
                executor_id=FailedExecutor.executor_id,
                request_digest=canonical_digest(request),
                idempotency_key="a-series-manual-recovery",
                required_state_refs=(),
                expected_observation_kind="audit.observation.v1",
            )
            with HostStorage(directory) as storage:
                host = EffectLifecycleHost(storage, clock_ms=clock)
                host.create_task(descriptor, frontier="node:effect-manual:prepare")
                host.prepare(
                    task_id=descriptor.task_id,
                    prepare_frontier="node:effect-manual:prepare",
                    reconcile_frontier="node:effect-manual:reconcile",
                    verify_frontier="node:effect-manual:verify",
                    result_frontier="node:effect-manual:result",
                    effect=effect,
                    request=request,
                    dispatch=dispatch,
                )
                assessment = assess_recovery(storage, descriptor.task_id)
                self.assertEqual(assessment.workload, "experimental-effect-lifecycle")
                self.assertFalse(assessment.automatic)
                self.assertEqual(assessment.action.value, "manual-stage")
                self.assertIn("executor identity", assessment.reason)

    def test_terminal_task_cannot_reopen_under_same_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1).__next__
            descriptor = TaskDescriptor(
                task_id="task:terminal-fence",
                goal_id="goal:terminal-fence",
                workload_id="audit.terminal.v1",
            )
            with HostStorage(directory) as storage:
                EffectLifecycleHost(storage, clock_ms=clock).create_task(
                    descriptor,
                    frontier="node:terminal-fence:start",
                )
                coordinator = GoalCoordinatorHost(storage, clock_ms=clock)
                first = coordinator.snapshot(descriptor.goal_id).task(descriptor.task_id)
                completed = coordinator.transition_task(
                    task_ref=first,
                    event_id="event:terminal-fence:complete",
                    kind=EventKind.TASK_STATE_CHANGED,
                    payload={},
                    state=TaskState.COMPLETED,
                    frontier=(),
                )
                terminal = coordinator.snapshot(descriptor.goal_id).task(descriptor.task_id)
                with self.assertRaisesRegex(TaskStateMismatch, "terminal Task"):
                    coordinator.transition_task(
                        task_ref=terminal,
                        event_id="event:terminal-fence:reopen",
                        kind=EventKind.TASK_STATE_CHANGED,
                        payload={},
                        state=TaskState.READY,
                        frontier=("node:terminal-fence:again",),
                    )
                current = storage.journal.get_task(descriptor.task_id)
                self.assertEqual(current, completed)

    def test_rejected_joint_verification_cannot_advance_actor_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1).__next__
            descriptor = TaskDescriptor(
                task_id="task:coordination-rejected",
                goal_id="goal:coordination-rejected",
                workload_id="audit.coordination.v1",
            )
            with HostStorage(directory) as storage:
                EffectLifecycleHost(storage, clock_ms=clock).create_task(
                    descriptor,
                    frontier="node:coordination-rejected:start",
                )
                coordinator = GoalCoordinatorHost(storage, clock_ms=clock)
                task_ref = coordinator.snapshot(descriptor.goal_id).task(
                    descriptor.task_id
                )
                receipt = VerificationReceipt(
                    dispatch_id="dispatch:coordination-rejected",
                    method="audit.joint-verification.v1",
                    accepted=False,
                    observation_digest=canonical_digest({"observation": 1}),
                    result_items=(
                        VerificationResultItem(
                            subject_ref=descriptor.task_id,
                            decision_digest=canonical_digest({"decision": 1}),
                            status="succeeded",
                            reason=None,
                            evidence_digest=canonical_digest({"evidence": 1}),
                        ),
                    ),
                )
                with self.assertRaisesRegex(
                    CoordinationError,
                    "rejected joint Verification",
                ):
                    coordinator.apply_verification_result(
                        task_ref=task_ref,
                        verification=receipt,
                        next_frontier="node:coordination-rejected:next",
                        event_id="event:coordination-rejected:apply",
                    )
                self.assertEqual(
                    storage.journal.get_task(descriptor.task_id).revision,
                    task_ref.revision,
                )

    def test_dangling_causal_event_is_rejected_before_history_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=itertools.count(1).__next__,
                    owner_id="host:causal-fence",
                )
                with self.assertRaisesRegex(EventConflict, "does not exist"):
                    kernel.create_task(
                        event_id="event:causal-fence:create",
                        kind=EventKind.TASK_CREATED,
                        task_id="task:causal-fence",
                        goal_id="goal:causal-fence",
                        payload={},
                        frontier=("node:causal-fence:start",),
                        caused_by_event_id="event:causal-fence:missing",
                    )
                self.assertEqual(storage.journal.event_count(), 0)

    def test_state_and_token_files_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            with HostStorage(root) as storage:
                stored = storage.put_object({"private": True}, kind="private-audit")
                object_path = root / "objects" / f"{stored.digest[7:]}.json"
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((root / "objects").stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((root / "host.sqlite3").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(object_path.stat().st_mode), 0o600)

            token = Path(directory) / "runtime.token"
            token.write_text("secret-token\n")
            token.chmod(0o644)
            with self.assertRaises(PermissionError):
                read_token_file(token)
            token.chmod(0o600)
            self.assertEqual(read_token_file(token), "secret-token")


if __name__ == "__main__":
    unittest.main()
