from __future__ import annotations

import hashlib
from collections.abc import Callable

from .continuity_models import (
    EXTERNAL_CONTINUITY_WORKLOAD_ID,
    WORKING_CHECKPOINT_OBJECT_KIND,
    CheckpointReceipt,
    ExternalContinuityResume,
    WorkingCheckpoint,
    WorkingCheckpointRecord,
)
from .domain import EventAdmission, EventKind, TaskDescriptor, TaskProjection, TaskState
from .handoff import operator_handoff
from .journal import EventConflict, JournalCorruption, LeaseHeld, RevisionConflict
from .kernel import HostKernel, TaskRevisionMismatch, worker_owner_id
from .objects import ObjectCorrupt
from .storage import HostStorage, TaskEventSnapshot

_CHECKPOINT_EVENT_KIND = EventKind.TASK_CONTEXT_CHECKPOINTED


class ExternalContinuityHost:
    """Durable semantic continuity for work driven outside Host execution.

    WorkingCheckpoint is a semantic working claim. Runtime, Git, and domain
    authorities remain responsible for revalidating physical/current truth.
    """

    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("external-continuity"),
            lease_ttl_ms=lease_ttl_ms,
        )

    def adopt(
        self,
        *,
        task_id: str,
        goal_id: str,
        initial_checkpoint: WorkingCheckpoint,
    ) -> ExternalContinuityResume:
        if initial_checkpoint.task_id != task_id:
            raise ValueError("initial WorkingCheckpoint Task identity differs")
        descriptor = self._descriptor(task_id, goal_id)
        current = self.storage.journal.get_task(task_id)
        if current is None:
            descriptor_object = self.storage.put_object(
                descriptor.to_dict(), kind="task-descriptor"
            )
            checkpoint_object = self.storage.put_object(
                initial_checkpoint.to_dict(), kind=WORKING_CHECKPOINT_OBJECT_KIND
            )
            try:
                self.kernel.create_task(
                    event_id=self._event_id(task_id, "adopt", 1),
                    kind=EventKind.TASK_CREATED,
                    task_id=task_id,
                    goal_id=goal_id,
                    payload={
                        "descriptorDigest": descriptor.digest,
                        "descriptorObjectDigest": descriptor_object.digest,
                        "checkpointDigest": initial_checkpoint.digest,
                        "checkpointObjectDigest": checkpoint_object.digest,
                    },
                    state=TaskState.READY,
                    frontier=(self._continue_node(task_id),),
                    referenced_objects=(descriptor_object, checkpoint_object),
                )
            except (EventConflict, RevisionConflict):
                # A concurrent adopter may have created the same explicit Task.
                # Validate the resulting durable identity instead of treating the
                # creation race itself as semantic disagreement.
                pass
            current = self._require_external_task(task_id, expected_goal_id=goal_id)
        else:
            current = self._require_external_task(task_id, expected_goal_id=goal_id)

        if current.revision == 1:
            seeded = self._checkpoint_at_revision(task_id, 1)
            if (
                seeded is not None
                and seeded.checkpoint_digest != initial_checkpoint.digest
            ):
                raise RevisionConflict(
                    "existing external-continuity Task seed checkpoint differs"
                )
            try:
                self.checkpoint(
                    task_id=task_id,
                    expected_revision=1,
                    checkpoint=initial_checkpoint,
                )
            except (LeaseHeld, RevisionConflict, TaskRevisionMismatch):
                # If another adopter completed revision 2, identical adoption
                # converges. If it has not completed yet, the contention remains
                # visible and the caller can retry/resume without hidden waiting.
                current = self._require_external_task(task_id, expected_goal_id=goal_id)
                initial = self._checkpoint_at_revision(task_id, 2)
                if (
                    current.revision < 2
                    or initial is None
                    or initial.checkpoint_digest != initial_checkpoint.digest
                ):
                    raise
        else:
            initial = self._initial_checkpoint(task_id)
            if initial is None:
                raise RevisionConflict(
                    "existing external-continuity Task has no initial checkpoint"
                )
            if initial.checkpoint_digest != initial_checkpoint.digest:
                raise RevisionConflict(
                    "existing external-continuity Task initial checkpoint differs"
                )
        return self.resume(task_id)

    def checkpoint(
        self,
        *,
        task_id: str,
        expected_revision: int,
        checkpoint: WorkingCheckpoint,
        disposition: str = "continue",
    ) -> CheckpointReceipt:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected checkpoint revision must be a positive integer")
        if checkpoint.task_id != task_id:
            raise ValueError("WorkingCheckpoint Task identity differs")
        target_state = self._disposition_state(disposition)
        current = self._require_external_task(task_id)
        if current.revision != expected_revision:
            if current.revision == expected_revision + 1:
                existing = self._checkpoint_at_revision(task_id, current.revision)
                if (
                    existing is not None
                    and existing.checkpoint_digest == checkpoint.digest
                    and current.state is target_state
                ):
                    return CheckpointReceipt(EventAdmission.EXISTING, current, existing)
            raise RevisionConflict(
                f"Task revision is {current.revision}, expected {expected_revision}"
            )
        expected_frontier = (self._continue_node(task_id),)
        if current.state is not TaskState.READY or current.ready_frontier != expected_frontier:
            raise ValueError(
                "external-continuity Task must remain READY at its continue frontier"
            )

        next_revision = expected_revision + 1
        with self.kernel.locked_task(
            task_id,
            expected_revision=expected_revision,
            expected_state=TaskState.READY,
            expected_frontier=expected_frontier,
            label="external-continuity",
        ) as locked:
            # Write the immutable checkpoint only after this worker owns the Task
            # transition lease. A competing stale writer must not leave an orphan
            # CAS object merely because it lost revision admission.
            checkpoint_object = self.storage.put_object(
                checkpoint.to_dict(), kind=WORKING_CHECKPOINT_OBJECT_KIND
            )
            descriptor_digest = self.storage.task_descriptor_digest(task_id)
            descriptor_object_digest = self.storage.task_descriptor_object_digest(task_id)
            terminal = target_state.terminal
            transition = locked.commit(
                event_id=self._event_id(task_id, "checkpoint", next_revision),
                kind=_CHECKPOINT_EVENT_KIND,
                payload={
                    "descriptorDigest": descriptor_digest,
                    "descriptorObjectDigest": descriptor_object_digest,
                    "checkpointDigest": checkpoint.digest,
                    "checkpointObjectDigest": checkpoint_object.digest,
                },
                state=target_state,
                frontier=() if terminal else expected_frontier,
                referenced_objects=(checkpoint_object,),
            )
        return CheckpointReceipt(
            admission=transition.admission,
            projection=transition.projection,
            record=WorkingCheckpointRecord(
                checkpoint=checkpoint,
                checkpoint_digest=checkpoint.digest,
                checkpoint_object_digest=checkpoint_object.digest,
                task_revision=transition.projection.revision,
            ),
        )

    def latest_checkpoint(self, task_id: str) -> WorkingCheckpointRecord | None:
        self._require_external_task(task_id)
        snapshot = self.storage.read_latest_task_event_of_kind(
            task_id, _CHECKPOINT_EVENT_KIND
        )
        if snapshot is not None:
            return self._checkpoint_from_snapshot(snapshot)
        return self._checkpoint_at_revision(task_id, 1)

    def resume(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
    ) -> ExternalContinuityResume:
        projection = self._require_external_task(task_id)
        if expected_revision is not None and projection.revision != expected_revision:
            raise RevisionConflict(
                f"Task revision is {projection.revision}, expected {expected_revision}"
            )
        handoff = operator_handoff(
            self.storage,
            task_id,
            expected_revision=projection.revision,
        )
        return ExternalContinuityResume(
            projection=projection,
            handoff=handoff,
            checkpoint=self._checkpoint_at_revision(task_id, projection.revision),
        )

    def checkpoint_at_revision(
        self, task_id: str, revision: int
    ) -> WorkingCheckpointRecord | None:
        if type(revision) is not int or revision < 1:
            raise ValueError("checkpoint revision must be a positive integer")
        self._require_external_task(task_id)
        return self._checkpoint_at_revision(task_id, revision)

    def _checkpoint_at_revision(
        self, task_id: str, revision: int
    ) -> WorkingCheckpointRecord | None:
        snapshot = self.storage.read_task_event_at_revision(task_id, revision)
        if snapshot is None:
            return None
        if snapshot.event_kind is _CHECKPOINT_EVENT_KIND:
            return self._checkpoint_from_snapshot(snapshot)
        if snapshot.event_kind is EventKind.TASK_CREATED and isinstance(
            snapshot.data, dict
        ) and {"checkpointDigest", "checkpointObjectDigest"}.issubset(snapshot.data):
            return self._checkpoint_from_snapshot(snapshot)
        return None

    def _initial_checkpoint(self, task_id: str) -> WorkingCheckpointRecord | None:
        seeded = self._checkpoint_at_revision(task_id, 1)
        if seeded is not None:
            return seeded
        return self._checkpoint_at_revision(task_id, 2)

    def _checkpoint_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> WorkingCheckpointRecord:
        if not isinstance(snapshot.data, dict):
            raise JournalCorruption("WorkingCheckpoint Event payload is not an object")
        expected = {
            "descriptorDigest",
            "descriptorObjectDigest",
            "checkpointDigest",
            "checkpointObjectDigest",
        }
        if set(snapshot.data) != expected:
            raise JournalCorruption("WorkingCheckpoint Event payload fields differ")
        checkpoint_digest = snapshot.data["checkpointDigest"]
        checkpoint_object_digest = snapshot.data["checkpointObjectDigest"]
        if not isinstance(checkpoint_digest, str) or not isinstance(
            checkpoint_object_digest, str
        ):
            raise JournalCorruption("WorkingCheckpoint Event digests are invalid")
        value = self.storage.objects.get(
            checkpoint_object_digest,
            expected_kind=WORKING_CHECKPOINT_OBJECT_KIND,
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("WorkingCheckpoint CAS object must be an object")
        try:
            checkpoint = WorkingCheckpoint.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("WorkingCheckpoint CAS object is invalid") from error
        if checkpoint.task_id != snapshot.projection.task_id:
            raise JournalCorruption("WorkingCheckpoint Task identity differs")
        if checkpoint.digest != checkpoint_digest:
            raise JournalCorruption("WorkingCheckpoint semantic digest differs")
        return WorkingCheckpointRecord(
            checkpoint=checkpoint,
            checkpoint_digest=checkpoint_digest,
            checkpoint_object_digest=checkpoint_object_digest,
            task_revision=snapshot.projection.revision,
        )

    def _require_external_task(
        self, task_id: str, *, expected_goal_id: str | None = None
    ) -> TaskProjection:
        projection = self.storage.journal.get_task(task_id)
        if projection is None:
            raise KeyError(f"unknown Task: {task_id}")
        descriptor = self.storage.read_task_descriptor(task_id)
        if descriptor is None:
            raise ValueError("external-continuity Task requires a durable descriptor")
        if descriptor != self._descriptor(task_id, projection.goal_id):
            raise ValueError("Task is not an external-continuity workload")
        if expected_goal_id is not None and projection.goal_id != expected_goal_id:
            raise ValueError("existing external-continuity Goal identity differs")
        return projection

    @staticmethod
    def _disposition_state(disposition: str) -> TaskState:
        if disposition == "continue":
            return TaskState.READY
        if disposition == "complete":
            return TaskState.COMPLETED
        if disposition == "abandon":
            return TaskState.CANCELLED
        raise ValueError(
            "external-continuity disposition must be continue, complete, or abandon"
        )

    @staticmethod
    def _descriptor(task_id: str, goal_id: str) -> TaskDescriptor:
        return TaskDescriptor(
            task_id=task_id,
            goal_id=goal_id,
            workload_id=EXTERNAL_CONTINUITY_WORKLOAD_ID,
        )

    @staticmethod
    def _continue_node(task_id: str) -> str:
        token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
        return f"node:external-continuity:{token}:continue"

    @staticmethod
    def _event_id(task_id: str, stage: str, revision: int) -> str:
        token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
        return f"event:external-continuity:{token}:{stage}:r{revision}"
