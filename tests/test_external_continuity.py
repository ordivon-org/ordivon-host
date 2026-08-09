from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import ordivon_host.continuity as continuity_module
from ordivon_host.continuity import ExternalContinuityHost
from ordivon_host.continuity_models import (
    EXTERNAL_CONTINUITY_WORKLOAD_ID,
    WORKING_CHECKPOINT_OBJECT_KIND,
    WorkingCheckpoint,
    WorkingCheckpointRuntime,
)
from ordivon_host.domain import EventAdmission, EventKind, TaskState
from ordivon_host.journal import JournalCorruption, LeaseHeld, RevisionConflict
from ordivon_host.kernel import HostKernel, TaskRevisionMismatch
from ordivon_host.ops.gc import plan_gc
from ordivon_host.ops.history import validate_history
from ordivon_host.storage import HostStorage


class FixedClock:
    def __init__(self) -> None:
        self.value = 1_000
        self.lock = threading.Lock()

    def __call__(self) -> int:
        with self.lock:
            self.value += 1
            return self.value


def checkpoint(task_id: str, suffix: str = "initial") -> WorkingCheckpoint:
    return WorkingCheckpoint(
        task_id=task_id,
        objective="preserve semantic continuity across external Agent sessions",
        frontier=f"inspect {suffix} Runtime truth",
        established=("Host owns semantic continuity",),
        unresolved=(f"revalidate {suffix} physical state",),
        rejected=("conversation transcript as authority",),
        constraints=("Runtime truth overrides this checkpoint",),
        next_actions=(f"inspect {suffix} workspace",),
        runtime=WorkingCheckpointRuntime(
            workspace_id="security-c2-d0-20260808",
            relevant_job_ids=(f"job:{suffix}",),
            observed_head_revision=f"head-{suffix}",
        ),
    )


class ExternalContinuityTests(unittest.TestCase):
    def test_checkpoint_round_trip_is_bounded_semantic_claim(self) -> None:
        value = checkpoint("task:external-continuity:model")
        decoded = WorkingCheckpoint.from_dict(value.to_dict())
        self.assertEqual(decoded, value)
        self.assertEqual(value.to_dict()["truthRole"], "semantic-working-claim")
        self.assertTrue(value.digest.startswith("sha256:"))
        with self.assertRaises(ValueError):
            WorkingCheckpoint(
                task_id=value.task_id,
                objective="x" * 70_000,
                frontier="continue",
            )

    def test_adopt_resume_and_checkpoint_keep_ready_continuity_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            task_id = "task:ordivon-security:c2-d0"
            clock = FixedClock()
            with HostStorage(root) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                resumed = host.adopt(
                    task_id=task_id,
                    goal_id="goal:ordivon-security",
                    initial_checkpoint=checkpoint(task_id),
                )
                self.assertEqual(resumed.projection.revision, 2)
                self.assertEqual(resumed.projection.state, TaskState.READY)
                self.assertEqual(len(resumed.projection.ready_frontier), 1)
                self.assertIsNotNone(resumed.checkpoint)
                assert resumed.checkpoint is not None
                self.assertEqual(resumed.checkpoint.task_revision, 2)
                self.assertEqual(
                    storage.read_task_descriptor(task_id).workload_id,
                    EXTERNAL_CONTINUITY_WORKLOAD_ID,
                )
                self.assertEqual(storage.journal.event_count(task_id), 2)
                self.assertEqual(resumed.extension_namespaces, ())
                self.assertEqual(resumed.to_dict()["extensionNamespaces"], [])
                head = storage.read_task_event(task_id)
                self.assertEqual(head.event_kind, EventKind.TASK_CONTEXT_CHECKPOINTED)
                pointer = storage.journal.task_event_at_revision(task_id, 2)
                assert pointer is not None
                refs = storage.journal.event_object_references(pointer.event_id)
                self.assertEqual(
                    sum(ref.role == "reference" for ref in refs),
                    1,
                )

                updated = checkpoint(task_id, "updated")
                receipt = host.checkpoint(
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint=updated,
                )
                self.assertEqual(receipt.admission, EventAdmission.CREATED)
                self.assertEqual(receipt.projection.revision, 3)
                self.assertEqual(receipt.projection.state, TaskState.READY)
                latest = host.resume(task_id, expected_revision=3)
                assert latest.checkpoint is not None
                self.assertEqual(latest.checkpoint.checkpoint, updated)
                self.assertEqual(latest.handoff.task_revision, 3)
                self.assertEqual(
                    latest.handoff.next_admissible,
                    ("continue-external-work",),
                )
                self.assertNotIn(
                    latest.projection.ready_frontier[0],
                    latest.handoff.next_admissible,
                )

    def test_resume_binds_checkpoint_to_projection_revision_under_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            task_id = "task:external-continuity:resume-race"
            clock = FixedClock()
            with HostStorage(root) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(task_id, "old"),
                )
                original_handoff = continuity_module.operator_handoff
                raced = [False]

                def racing_handoff(
                    handoff_storage: HostStorage,
                    handoff_task_id: str,
                    *,
                    expected_revision: int | None = None,
                ):
                    capsule = original_handoff(
                        handoff_storage,
                        handoff_task_id,
                        expected_revision=expected_revision,
                    )
                    if not raced[0]:
                        raced[0] = True
                        with HostStorage(root) as other_storage:
                            ExternalContinuityHost(
                                other_storage, clock_ms=clock
                            ).checkpoint(
                                task_id=task_id,
                                expected_revision=2,
                                checkpoint=checkpoint(task_id, "new"),
                            )
                    return capsule

                with mock.patch.object(
                    continuity_module, "operator_handoff", racing_handoff
                ):
                    resumed = host.resume(task_id, expected_revision=2)

                self.assertEqual(resumed.projection.revision, 2)
                self.assertEqual(resumed.handoff.task_revision, 2)
                assert resumed.checkpoint is not None
                self.assertEqual(resumed.checkpoint.task_revision, 2)
                self.assertEqual(
                    resumed.checkpoint.checkpoint.frontier,
                    checkpoint(task_id, "old").frontier,
                )
                current = host.resume(task_id)
                self.assertEqual(current.projection.revision, 3)
                assert current.checkpoint is not None
                self.assertEqual(current.checkpoint.task_revision, 3)

    def test_resume_extension_namespaces_are_revision_fenced_under_race(self) -> None:
        from ordivon_host import HostExtensionPort

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            task_id = "task:external-continuity:routing-race"
            clock = FixedClock()
            with HostStorage(root) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(task_id, "routing-race"),
                )
                original_handoff = continuity_module.operator_handoff
                raced = [False]

                def racing_handoff(
                    handoff_storage: HostStorage,
                    handoff_task_id: str,
                    *,
                    expected_revision: int | None = None,
                ):
                    capsule = original_handoff(
                        handoff_storage,
                        handoff_task_id,
                        expected_revision=expected_revision,
                    )
                    if not raced[0]:
                        raced[0] = True
                        with HostStorage(root) as other_storage:
                            port = HostExtensionPort(
                                other_storage,
                                HostKernel(
                                    other_storage,
                                    clock_ms=clock,
                                    owner_id="host:routing-race:world",
                                ),
                            )
                            port.append_preserving(
                                task_id=task_id,
                                expected_revision=2,
                                event_id="event:routing-race:world",
                                kind=EventKind("world.outcome-unknown"),
                                updates={"worldOutcomeState": "unknown"},
                            )
                    return capsule

                with mock.patch.object(
                    continuity_module, "operator_handoff", racing_handoff
                ):
                    resumed = host.resume(task_id, expected_revision=2)

                self.assertEqual(resumed.projection.revision, 2)
                self.assertEqual(resumed.handoff.task_revision, 2)
                self.assertEqual(resumed.extension_namespaces, ())

                current = host.resume(task_id)
                self.assertEqual(current.projection.revision, 3)
                self.assertEqual(current.extension_namespaces, ("world",))

    def test_resume_keeps_namespace_visible_when_owner_updates_after_target_revision(self) -> None:
        from ordivon_host import HostExtensionPort

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            task_id = "task:external-continuity:routing-owner-update"
            clock = FixedClock()
            with HostStorage(root) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(task_id, "routing-owner-update"),
                )
                port = HostExtensionPort(
                    storage,
                    HostKernel(
                        storage,
                        clock_ms=clock,
                        owner_id="host:routing-owner-update:first",
                    ),
                )
                first = port.append_preserving(
                    task_id=task_id,
                    expected_revision=2,
                    event_id="event:routing-owner-update:world:r3",
                    kind=EventKind("world.outcome-unknown"),
                    updates={"worldOutcomeState": "unknown"},
                )
                self.assertEqual(first.projection.revision, 3)
                original_handoff = continuity_module.operator_handoff
                raced = [False]

                def racing_handoff(
                    handoff_storage: HostStorage,
                    handoff_task_id: str,
                    *,
                    expected_revision: int | None = None,
                ):
                    capsule = original_handoff(
                        handoff_storage,
                        handoff_task_id,
                        expected_revision=expected_revision,
                    )
                    if not raced[0]:
                        raced[0] = True
                        with HostStorage(root) as other_storage:
                            other_port = HostExtensionPort(
                                other_storage,
                                HostKernel(
                                    other_storage,
                                    clock_ms=clock,
                                    owner_id="host:routing-owner-update:second",
                                ),
                            )
                            other_port.append_preserving(
                                task_id=task_id,
                                expected_revision=3,
                                event_id="event:routing-owner-update:world:r4",
                                kind=EventKind("world.outcome-reconciled"),
                                updates={"worldOutcomeState": "succeeded"},
                            )
                    return capsule

                with mock.patch.object(
                    continuity_module, "operator_handoff", racing_handoff
                ):
                    resumed = host.resume(task_id, expected_revision=3)

                self.assertEqual(resumed.projection.revision, 3)
                self.assertEqual(resumed.extension_namespaces, ("world",))
                current = host.resume(task_id)
                self.assertEqual(current.projection.revision, 4)
                self.assertEqual(current.extension_namespaces, ("world",))

    def test_checkpoint_response_loss_retry_returns_existing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:response-loss"
            clock = FixedClock()
            with HostStorage(directory) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(task_id),
                )
                update = checkpoint(task_id, "response-loss")
                first = host.checkpoint(
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint=update,
                )
                retry = host.checkpoint(
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint=update,
                )
                self.assertEqual(first.admission, EventAdmission.CREATED)
                self.assertEqual(retry.admission, EventAdmission.EXISTING)
                self.assertEqual(first.record.checkpoint_digest, retry.record.checkpoint_digest)
                self.assertEqual(storage.journal.event_count(task_id), 3)
                with self.assertRaises(RevisionConflict):
                    host.checkpoint(
                        task_id=task_id,
                        expected_revision=2,
                        checkpoint=checkpoint(task_id, "different"),
                    )

    def test_final_checkpoint_can_complete_or_abandon_continuity_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FixedClock()
            with HostStorage(directory) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                completed_id = "task:external-continuity:complete"
                host.adopt(
                    task_id=completed_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(completed_id),
                )
                final = checkpoint(completed_id, "final")
                first = host.checkpoint(
                    task_id=completed_id,
                    expected_revision=2,
                    checkpoint=final,
                    disposition="complete",
                )
                self.assertEqual(first.projection.state, TaskState.COMPLETED)
                self.assertEqual(first.projection.ready_frontier, ())
                replay = host.checkpoint(
                    task_id=completed_id,
                    expected_revision=2,
                    checkpoint=final,
                    disposition="complete",
                )
                self.assertEqual(replay.admission, EventAdmission.EXISTING)
                resumed = host.resume(completed_id, expected_revision=3)
                self.assertEqual(resumed.projection.state, TaskState.COMPLETED)
                self.assertEqual(resumed.handoff.next_admissible, ())
                assert resumed.checkpoint is not None
                self.assertEqual(resumed.checkpoint.checkpoint, final)
                with self.assertRaises(RevisionConflict):
                    host.checkpoint(
                        task_id=completed_id,
                        expected_revision=2,
                        checkpoint=final,
                        disposition="abandon",
                    )

                abandoned_id = "task:external-continuity:abandon"
                host.adopt(
                    task_id=abandoned_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(abandoned_id),
                )
                abandoned = host.checkpoint(
                    task_id=abandoned_id,
                    expected_revision=2,
                    checkpoint=checkpoint(abandoned_id, "abandoned"),
                    disposition="abandon",
                )
                self.assertEqual(abandoned.projection.state, TaskState.CANCELLED)
                self.assertEqual(abandoned.projection.ready_frontier, ())
                validate_history(storage)

    def test_adopt_retry_recovers_creation_and_rejects_different_initial_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:adopt-retry"
            clock = FixedClock()
            initial = checkpoint(task_id)
            with HostStorage(directory) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                first = host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=initial,
                )
                retry = host.adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=initial,
                )
                self.assertEqual(first.projection.revision, 2)
                self.assertEqual(retry.projection.revision, 2)
                self.assertEqual(storage.journal.event_count(task_id), 2)
                with self.assertRaises(RevisionConflict):
                    host.adopt(
                        task_id=task_id,
                        goal_id="goal:external-continuity",
                        initial_checkpoint=checkpoint(task_id, "different-initial"),
                    )

    def test_competing_identical_adoptions_converge_without_duplicate_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:adopt-race"
            goal_id = "goal:external-continuity"
            clock = FixedClock()
            initial = checkpoint(task_id)
            with HostStorage(directory):
                pass
            barrier = threading.Barrier(2)

            def adopt(worker: int) -> str:
                barrier.wait(timeout=5)
                try:
                    with HostStorage(directory) as storage:
                        host = ExternalContinuityHost(
                            storage, clock_ms=clock, owner_id=f"host:adopt-race:{worker}"
                        )
                        result = host.adopt(
                            task_id=task_id,
                            goal_id=goal_id,
                            initial_checkpoint=initial,
                        )
                        return f"ok:{result.projection.revision}"
                except (LeaseHeld, RevisionConflict, TaskRevisionMismatch):
                    return "retry"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(adopt, range(2)))
            self.assertTrue(any(value == "ok:2" for value in outcomes), outcomes)

            with HostStorage(directory) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                recovered = host.adopt(
                    task_id=task_id,
                    goal_id=goal_id,
                    initial_checkpoint=initial,
                )
                self.assertEqual(recovered.projection.revision, 2)
                self.assertEqual(storage.journal.event_count(task_id), 2)
                latest = host.latest_checkpoint(task_id)
                assert latest is not None
                self.assertEqual(latest.checkpoint_digest, initial.digest)
                storage.journal.validate_invariants()

    def test_competing_checkpoints_commit_one_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:race"
            clock = FixedClock()
            with HostStorage(directory) as storage:
                ExternalContinuityHost(storage, clock_ms=clock).adopt(
                    task_id=task_id,
                    goal_id="goal:external-continuity",
                    initial_checkpoint=checkpoint(task_id),
                )

            barrier = threading.Barrier(2)

            def write(suffix: str) -> str:
                barrier.wait(timeout=5)
                try:
                    with HostStorage(directory) as storage:
                        host = ExternalContinuityHost(storage, clock_ms=clock)
                        host.checkpoint(
                            task_id=task_id,
                            expected_revision=2,
                            checkpoint=checkpoint(task_id, suffix),
                        )
                    return "created"
                except (LeaseHeld, RevisionConflict, TaskRevisionMismatch):
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = sorted(pool.map(write, ("a", "b")))
            self.assertEqual(outcomes, ["conflict", "created"])
            with HostStorage(directory) as storage:
                self.assertEqual(storage.journal.get_task(task_id).revision, 3)
                self.assertEqual(storage.journal.event_count(task_id), 3)
                self.assertEqual(plan_gc(directory, storage=storage)["orphanedObjects"], [])
                storage.journal.validate_invariants()

    def test_new_adopt_seed_survives_crash_before_revision_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            task_id = "task:external-continuity:seed-crash"
            goal_id = "goal:external-continuity"
            clock = FixedClock()
            initial = checkpoint(task_id, "seed survives")
            with HostStorage(state_root) as storage:
                host = ExternalContinuityHost(storage, clock_ms=clock)
                with mock.patch.object(
                    ExternalContinuityHost,
                    "checkpoint",
                    side_effect=RuntimeError("synthetic crash after rev1"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                        host.adopt(
                            task_id=task_id,
                            goal_id=goal_id,
                            initial_checkpoint=initial,
                        )
                current = storage.journal.get_task(task_id)
                assert current is not None
                self.assertEqual(current.revision, 1)
                seeded = host.resume(task_id, expected_revision=1)
                assert seeded.checkpoint is not None
                self.assertEqual(seeded.checkpoint.task_revision, 1)
                self.assertEqual(seeded.checkpoint.checkpoint, initial)
                self.assertEqual(
                    seeded.handoff.next_admissible, ("continue-external-work",)
                )
                validate_history(storage)

            with HostStorage(state_root) as storage:
                fresh = ExternalContinuityHost(storage, clock_ms=clock)
                with self.assertRaises(RevisionConflict):
                    fresh.adopt(
                        task_id=task_id,
                        goal_id=goal_id,
                        initial_checkpoint=checkpoint(task_id, "different seed"),
                    )
                recovered = fresh.adopt(
                    task_id=task_id,
                    goal_id=goal_id,
                    initial_checkpoint=initial,
                )
                self.assertEqual(recovered.projection.revision, 2)
                assert recovered.checkpoint is not None
                self.assertEqual(recovered.checkpoint.task_revision, 2)
                self.assertEqual(recovered.checkpoint.checkpoint, initial)
                self.assertEqual(storage.journal.event_count(task_id), 2)
                validate_history(storage)

    def test_adopt_recovers_after_creation_before_initial_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:partial-adopt"
            goal_id = "goal:external-continuity"
            clock = FixedClock()
            with HostStorage(directory) as storage:
                descriptor = ExternalContinuityHost._descriptor(task_id, goal_id)
                descriptor_object = storage.put_object(
                    descriptor.to_dict(), kind="task-descriptor"
                )
                HostKernel(
                    storage, clock_ms=clock, owner_id="host:partial-adopt"
                ).create_task(
                    event_id=ExternalContinuityHost._event_id(task_id, "adopt", 1),
                    kind=EventKind.TASK_CREATED,
                    task_id=task_id,
                    goal_id=goal_id,
                    payload={
                        "descriptorDigest": descriptor.digest,
                        "descriptorObjectDigest": descriptor_object.digest,
                    },
                    state=TaskState.READY,
                    frontier=(ExternalContinuityHost._continue_node(task_id),),
                    referenced_objects=(descriptor_object,),
                )
                self.assertEqual(storage.journal.get_task(task_id).revision, 1)
                resumed = ExternalContinuityHost(
                    storage, clock_ms=clock
                ).adopt(
                    task_id=task_id,
                    goal_id=goal_id,
                    initial_checkpoint=checkpoint(task_id),
                )
                self.assertEqual(resumed.projection.revision, 2)
                self.assertIsNotNone(resumed.checkpoint)
                self.assertEqual(storage.journal.event_count(task_id), 2)

    def test_continuity_core_does_not_import_runtime_or_harness(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "src"
            / "ordivon_host"
            / "continuity.py"
        ).read_text()
        self.assertNotIn(".runtime", source)
        self.assertNotIn("ordivon_harness", source)

    def test_history_doctor_rejects_semantically_wrong_checkpoint_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:external-continuity:bad-history"
            goal_id = "goal:external-continuity"
            clock = FixedClock()
            value = checkpoint(task_id)
            with HostStorage(directory) as storage:
                descriptor = ExternalContinuityHost._descriptor(task_id, goal_id)
                descriptor_object = storage.put_object(
                    descriptor.to_dict(), kind="task-descriptor"
                )
                HostKernel(
                    storage, clock_ms=clock, owner_id="host:bad-history-create"
                ).create_task(
                    event_id=ExternalContinuityHost._event_id(task_id, "adopt", 1),
                    kind=EventKind.TASK_CREATED,
                    task_id=task_id,
                    goal_id=goal_id,
                    payload={
                        "descriptorDigest": descriptor.digest,
                        "descriptorObjectDigest": descriptor_object.digest,
                    },
                    state=TaskState.READY,
                    frontier=(ExternalContinuityHost._continue_node(task_id),),
                    referenced_objects=(descriptor_object,),
                )
                checkpoint_object = storage.put_object(
                    value.to_dict(), kind=WORKING_CHECKPOINT_OBJECT_KIND
                )
                with HostKernel(
                    storage, clock_ms=clock, owner_id="host:bad-history-checkpoint"
                ).locked_task(
                    task_id,
                    expected_revision=1,
                    expected_state=TaskState.READY,
                    expected_frontier=(
                        ExternalContinuityHost._continue_node(task_id),
                    ),
                ) as locked:
                    locked.commit(
                        event_id=ExternalContinuityHost._event_id(
                            task_id, "checkpoint", 2
                        ),
                        kind=EventKind.TASK_CONTEXT_CHECKPOINTED,
                        payload={
                            "descriptorDigest": descriptor.digest,
                            "descriptorObjectDigest": descriptor_object.digest,
                            "checkpointDigest": "sha256:" + ("0" * 64),
                            "checkpointObjectDigest": checkpoint_object.digest,
                        },
                        referenced_objects=(checkpoint_object,),
                    )

            # Physical/CAS reopen remains valid; only semantic deep history is wrong.
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(
                    JournalCorruption, "WorkingCheckpoint identity differs"
                ):
                    validate_history(storage)

    def test_external_continuity_refuses_ordinary_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "task:ordinary"
            clock = FixedClock()
            with HostStorage(directory) as storage:
                HostKernel(storage, clock_ms=clock, owner_id="host:test").create_task(
                    event_id="event:ordinary:create",
                    kind=EventKind.TASK_CREATED,
                    task_id=task_id,
                    goal_id="goal:ordinary",
                    payload={"ordinary": True},
                    frontier=("node:ordinary:continue",),
                )
                host = ExternalContinuityHost(storage, clock_ms=clock)
                with self.assertRaisesRegex(
                    ValueError, "requires a durable descriptor"
                ):
                    host.resume(task_id)


if __name__ == "__main__":
    unittest.main()
