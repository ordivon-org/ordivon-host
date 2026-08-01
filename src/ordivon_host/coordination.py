from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anc_canonical import JsonValue, canonical_digest

from .domain import EventKind, TaskDescriptor, TaskProjection, TaskState
from .effects.models import VerificationReceipt, VerificationResultItem
from .journal import JournalCorruption
from .kernel import HostKernel, worker_owner_id
from .objects import StoredObject
from .storage import HostStorage


class CoordinationError(RuntimeError):
    pass


class CoordinationSuperseded(CoordinationError):
    pass


@dataclass(frozen=True, slots=True)
class TaskRevisionRef:
    task_id: str
    revision: int
    state: TaskState
    head_payload_digest: str
    descriptor_digest: str
    descriptor_object_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "taskId": self.task_id,
            "revision": self.revision,
            "state": self.state.value,
            "headPayloadDigest": self.head_payload_digest,
            "descriptorDigest": self.descriptor_digest,
        }


@dataclass(frozen=True, slots=True)
class GoalSnapshot:
    goal_id: str
    tasks: tuple[TaskRevisionRef, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.goal-task-snapshot",
            "goalId": self.goal_id,
            "tasks": [item.to_dict() for item in self.tasks],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def task(self, task_id: str) -> TaskRevisionRef:
        for item in self.tasks:
            if item.task_id == task_id:
                return item
        raise KeyError(f"Task is absent from Goal snapshot: {task_id}")


class GoalCoordinatorHost:
    """Small Goal-scoped coordination primitives; intentionally not a scheduler or DAG."""

    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if owner_id is not None and (not owner_id or owner_id != owner_id.strip()):
            raise ValueError("explicit coordinator owner identity must be trimmed")
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:goal-coordinator-v1"),
            lease_ttl_ms=lease_ttl_ms,
        )

    def snapshot(self, goal_id: str) -> GoalSnapshot:
        if not goal_id.startswith("goal:"):
            raise ValueError("Goal identity must start with goal:")
        refs: list[TaskRevisionRef] = []
        for projection in self.storage.journal.tasks_for_goal(goal_id):
            head = self.storage.journal.get_task_head(projection.task_id)
            if head is None:
                raise JournalCorruption(f"Task has no event head: {projection.task_id}")
            descriptor_digest = self.storage.task_descriptor_digest(projection.task_id)
            descriptor_object_digest = self.storage.task_descriptor_object_digest(
                projection.task_id
            )
            refs.append(
                TaskRevisionRef(
                    task_id=projection.task_id,
                    revision=projection.revision,
                    state=projection.state,
                    head_payload_digest=head.payload_digest,
                    descriptor_digest=descriptor_digest,
                    descriptor_object_digest=descriptor_object_digest,
                )
            )
        return GoalSnapshot(goal_id=goal_id, tasks=tuple(refs))

    def assert_current(self, snapshot: GoalSnapshot) -> None:
        current = self.snapshot(snapshot.goal_id)
        if current != snapshot:
            raise CoordinationSuperseded("Goal Task set or revision changed")

    def transition_task(
        self,
        *,
        task_ref: TaskRevisionRef,
        event_id: str,
        kind: EventKind,
        payload: dict[str, JsonValue],
        state: TaskState,
        frontier: tuple[str, ...],
        referenced_objects: tuple[StoredObject, ...] = (),
    ) -> TaskProjection:
        descriptor = self.storage.read_task_descriptor(task_ref.task_id)
        if descriptor is None:
            raise CoordinationError("coordinated Task has no TaskDescriptor")
        descriptor_object = self.storage.objects.inspect(
            task_ref.descriptor_object_digest
        )
        with self.kernel.locked_task(
            task_ref.task_id,
            expected_revision=task_ref.revision,
            expected_state=task_ref.state,
            label="Goal coordination",
            error_factory=self._kernel_error,
        ) as locked:
            current_head = self.storage.journal.get_task_head(task_ref.task_id)
            if (
                current_head is None
                or current_head.payload_digest != task_ref.head_payload_digest
            ):
                raise CoordinationSuperseded("Task head changed before coordination")
            projection = locked.commit(
                event_id=event_id,
                kind=kind,
                payload={
                    "descriptorDigest": task_ref.descriptor_digest,
                    "descriptorObjectDigest": descriptor_object.digest,
                    **payload,
                },
                state=state,
                frontier=frontier,
                referenced_objects=(descriptor_object, *referenced_objects),
            ).projection
            return projection

    def apply_verification_result(
        self,
        *,
        task_ref: TaskRevisionRef,
        verification: VerificationReceipt,
        next_frontier: str,
        event_id: str,
    ) -> TaskProjection:
        if not next_frontier.startswith("node:"):
            raise ValueError("coordinated Task frontier must start with node:")
        if not verification.accepted:
            raise CoordinationError(
                "rejected joint Verification cannot advance an Actor Task"
            )
        result = self._result_for(verification, task_ref.task_id)
        verification_object = self.storage.put_object(
            verification.to_dict(), kind="verification-receipt"
        )
        current = self.storage.journal.get_task(task_ref.task_id)
        if current is None:
            raise KeyError(f"unknown Task: {task_ref.task_id}")
        if current.revision == task_ref.revision + 1:
            snapshot = self.storage.read_task_event(task_ref.task_id)
            data = snapshot.data
            if (
                snapshot.event_kind is EventKind.TASK_RESULT_APPLIED
                and isinstance(data, dict)
                and data.get("verificationDigest") == verification_object.digest
                and data.get("resultItem") == result.to_dict()
            ):
                return current
        if current.revision != task_ref.revision:
            raise CoordinationSuperseded("Actor Task changed before result application")
        state = (
            TaskState.READY
            if result.status in {"succeeded", "not-selected"}
            else TaskState.BLOCKED
        )
        frontier = (next_frontier,)
        return self.transition_task(
            task_ref=task_ref,
            event_id=event_id,
            kind=EventKind.TASK_RESULT_APPLIED,
            payload={
                "verificationDigest": verification_object.digest,
                "resultItem": result.to_dict(),
            },
            state=state,
            frontier=frontier,
            referenced_objects=(verification_object,),
        )

    @staticmethod
    def descriptor_for(
        *,
        task_id: str,
        goal_id: str,
        workload_id: str,
        assignee_ref: str | None,
        provider_policy_ref: str | None,
        domain_ref: str | None,
        configuration_digests: tuple[str, ...] = (),
    ) -> TaskDescriptor:
        return TaskDescriptor(
            task_id=task_id,
            goal_id=goal_id,
            workload_id=workload_id,
            assignee_ref=assignee_ref,
            provider_policy_ref=provider_policy_ref,
            domain_ref=domain_ref,
            configuration_digests=configuration_digests,
        )

    @staticmethod
    def _result_for(
        verification: VerificationReceipt,
        task_id: str,
    ) -> VerificationResultItem:
        matches = [item for item in verification.result_items if item.subject_ref == task_id]
        if len(matches) != 1:
            raise CoordinationError(
                "Verification must contain exactly one result for the Actor Task"
            )
        return matches[0]

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category in {"revision", "state", "frontier"}:
            return CoordinationSuperseded(message)
        return JournalCorruption(message)
