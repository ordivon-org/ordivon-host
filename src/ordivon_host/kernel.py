from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from typing import TypeAlias
import uuid

from anc_canonical import JsonValue

from .domain import EventAdmission, EventKind, TaskProjection, TaskState
from .objects import StoredObject
from .storage import HostStorage, TaskEventSnapshot

ErrorFactory: TypeAlias = Callable[[str, str], Exception]


def worker_owner_id(component_id: str) -> str:
    """Return one process-instance lease owner, not a reusable component label."""
    if not component_id or component_id != component_id.strip():
        raise ValueError("Host component identity is required")
    return f"{component_id}:pid-{os.getpid()}:{uuid.uuid4().hex}"


class HostKernelError(RuntimeError):
    pass


class TaskMissing(HostKernelError):
    pass


class TaskRevisionMismatch(HostKernelError):
    pass


class TaskStateMismatch(HostKernelError):
    pass


class TaskFrontierMismatch(HostKernelError):
    pass


class TaskProjectionDrift(HostKernelError):
    pass


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    admission: EventAdmission
    projection: TaskProjection


class LockedTask:
    def __init__(self, kernel: HostKernel, snapshot: TaskEventSnapshot) -> None:
        self.kernel = kernel
        self.snapshot = snapshot
        self._committed = False

    @property
    def projection(self) -> TaskProjection:
        return self.snapshot.projection

    def commit(
        self,
        *,
        event_id: str,
        kind: EventKind,
        payload: JsonValue,
        state: TaskState | None = None,
        frontier: tuple[str, ...] | None = None,
        active_node_id: str | None = None,
        caused_by_event_id: str | None = None,
        referenced_objects: tuple[StoredObject, ...] = (),
    ) -> TransitionReceipt:
        if self._committed:
            raise HostKernelError("one locked Task transition may commit only once")
        projection = self.kernel.next_projection(
            self.snapshot.projection,
            state=state,
            frontier=frontier,
            active_node_id=active_node_id,
        )
        admission = self.kernel.storage.record_task_event(
            event_id=event_id,
            kind=kind,
            payload=payload,
            projection=projection,
            expected_revision=self.snapshot.projection.revision,
            caused_by_event_id=caused_by_event_id,
            referenced_objects=referenced_objects,
        )
        self._committed = True
        return TransitionReceipt(admission=admission, projection=projection)


class HostKernel:
    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if not owner_id or owner_id != owner_id.strip():
            raise ValueError("HostKernel owner identity is required")
        if lease_ttl_ms < 1:
            raise ValueError("HostKernel lease TTL must be positive")
        self.storage = storage
        self.clock_ms = clock_ms
        self.owner_id = owner_id
        self.lease_ttl_ms = lease_ttl_ms

    def create_task(
        self,
        *,
        event_id: str,
        kind: EventKind,
        task_id: str,
        goal_id: str,
        payload: JsonValue,
        state: TaskState = TaskState.READY,
        frontier: tuple[str, ...] = (),
        active_node_id: str | None = None,
        caused_by_event_id: str | None = None,
        referenced_objects: tuple[StoredObject, ...] = (),
    ) -> TransitionReceipt:
        projection = TaskProjection(
            task_id=task_id,
            goal_id=goal_id,
            state=state,
            active_node_id=active_node_id,
            ready_frontier=frontier,
            revision=1,
            updated_at_ms=self.timestamp(None),
        )
        admission = self.storage.record_task_event(
            event_id=event_id,
            kind=kind,
            payload=payload,
            projection=projection,
            expected_revision=0,
            caused_by_event_id=caused_by_event_id,
            referenced_objects=referenced_objects,
        )
        return TransitionReceipt(admission=admission, projection=projection)

    def current_snapshot(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: TaskState | None = None,
        expected_frontier: tuple[str, ...] | None = None,
        label: str = "Host",
        error_factory: ErrorFactory | None = None,
    ) -> TaskEventSnapshot:
        current = self.storage.journal.get_task(task_id)
        if current is None:
            self._raise(
                error_factory,
                "missing",
                f"unknown {label} Task: {task_id}",
                TaskMissing,
            )
        assert current is not None
        self._expect(
            current,
            expected_revision=expected_revision,
            expected_state=expected_state,
            expected_frontier=expected_frontier,
            label=label,
            error_factory=error_factory,
        )
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.projection != current:
            self._raise(
                error_factory,
                "projection",
                f"{label} Task projection changed before Host transition",
                TaskProjectionDrift,
            )
        return snapshot

    @contextmanager
    def locked_task(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: TaskState | None = None,
        expected_frontier: tuple[str, ...] | None = None,
        label: str = "Host",
        error_factory: ErrorFactory | None = None,
    ) -> Iterator[LockedTask]:
        lease = self.storage.journal.acquire_lease(
            task_id,
            owner_id=self.owner_id,
            now_ms=self.clock_ms(),
            ttl_ms=self.lease_ttl_ms,
        )
        try:
            snapshot = self.current_snapshot(
                task_id,
                expected_revision=expected_revision,
                expected_state=expected_state,
                expected_frontier=expected_frontier,
                label=label,
                error_factory=error_factory,
            )
            yield LockedTask(self, snapshot)
        finally:
            self.storage.journal.release_lease(lease)

    def next_projection(
        self,
        current: TaskProjection,
        *,
        state: TaskState | None = None,
        frontier: tuple[str, ...] | None = None,
        active_node_id: str | None = None,
    ) -> TaskProjection:
        next_state = current.state if state is None else state
        next_frontier = current.ready_frontier if frontier is None else frontier
        if next_state is TaskState.RUNNING and active_node_id is None:
            active_node_id = current.active_node_id
        elif next_state is not TaskState.RUNNING:
            active_node_id = None
        return TaskProjection(
            task_id=current.task_id,
            goal_id=current.goal_id,
            state=next_state,
            active_node_id=active_node_id,
            ready_frontier=next_frontier,
            revision=current.revision + 1,
            updated_at_ms=self.timestamp(current.updated_at_ms),
        )

    def timestamp(self, previous: int | None) -> int:
        value = self.clock_ms()
        if value < 0:
            raise ValueError("Host clock returned a negative timestamp")
        return value if previous is None else max(value, previous + 1)

    def _expect(
        self,
        current: TaskProjection,
        *,
        expected_revision: int | None,
        expected_state: TaskState | None,
        expected_frontier: tuple[str, ...] | None,
        label: str,
        error_factory: ErrorFactory | None,
    ) -> None:
        if expected_revision is not None and current.revision != expected_revision:
            self._raise(
                error_factory,
                "revision",
                f"Task revision is {current.revision}, expected {expected_revision}",
                TaskRevisionMismatch,
            )
        if expected_state is not None and current.state is not expected_state:
            self._raise(
                error_factory,
                "state",
                f"{label} requires a {expected_state.value} Task",
                TaskStateMismatch,
            )
        if expected_frontier is not None and current.ready_frontier != expected_frontier:
            self._raise(
                error_factory,
                "frontier",
                f"Task is not at the requested {label} frontier",
                TaskFrontierMismatch,
            )

    @staticmethod
    def _raise(
        factory: ErrorFactory | None,
        category: str,
        message: str,
        default_type: type[Exception],
    ) -> None:
        if factory is None:
            raise default_type(message)
        raise factory(category, message)
