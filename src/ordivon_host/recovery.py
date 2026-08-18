from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from anc_canonical import JsonValue

from .domain import TaskState
from .storage import HostStorage, TaskEventSnapshot


class RecoveryAction(StrEnum):
    NONE = "none"
    ADVANCE_READ = "advance-read"
    OBSERVE_RUNTIME_DISPATCH = "observe-runtime-dispatch"
    COGNITION_RESULT_REQUIRED = "cognition-result-required"
    MANUAL_STAGE = "manual-stage"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    task_id: str
    state: TaskState
    revision: int
    workload: str
    event_kind: str
    frontier: str | None
    action: RecoveryAction
    automatic: bool
    reason: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "taskId": self.task_id,
            "state": self.state.value,
            "revision": self.revision,
            "workload": self.workload,
            "eventKind": self.event_kind,
            "frontier": self.frontier,
            "action": self.action.value,
            "automatic": self.automatic,
            "reason": self.reason,
        }


def assess_recovery(storage: HostStorage, task_id: str) -> RecoveryAssessment:
    """Project conservative Host-local recovery posture without invoking another owner.

    Current Host production authority contains only external-continuity Tasks. Runtime,
    Harness and domain recovery are owned by those systems; Host does not reconstruct
    historical workload executors from generic Task state.
    """
    snapshot = storage.read_task_event(task_id)
    projection = snapshot.projection
    frontier = projection.ready_frontier[0] if len(projection.ready_frontier) == 1 else None
    workload = _workload(storage, snapshot)
    if projection.state.terminal:
        return RecoveryAssessment(
            task_id, projection.state, projection.revision, workload,
            snapshot.event_kind.value, frontier, RecoveryAction.NONE, False,
            "Task is already terminal",
        )
    return RecoveryAssessment(
        task_id, projection.state, projection.revision, workload,
        snapshot.event_kind.value, frontier, RecoveryAction.UNSUPPORTED, False,
        "Host owns durable Task continuity only; resume or re-observe the current owner before acting",
    )


def _workload(storage: HostStorage, snapshot: TaskEventSnapshot) -> str:
    descriptor = storage.read_task_descriptor(snapshot.projection.task_id)
    return descriptor.workload_id if descriptor is not None else "unknown"
