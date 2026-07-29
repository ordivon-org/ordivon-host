from __future__ import annotations

from dataclasses import dataclass

from anc_canonical import JsonValue, canonical_digest

from .domain import EventKind, TaskState
from .storage import HostStorage


@dataclass(frozen=True, slots=True)
class OperatorHandoffCapsule:
    task_id: str
    goal_id: str
    task_state: TaskState
    task_revision: int
    event_kind: EventKind
    event_payload_digest: str
    ready_frontier: tuple[str, ...]
    descriptor_digest: str | None
    proposal_digest: str | None
    decision_request_id: str | None
    child_task_id: str | None
    dispatch_object_digest: str | None
    backend_job_id: str | None
    outcome_object_digest: str | None
    must_not_repeat_object_digests: tuple[str, ...]
    next_admissible: tuple[str, ...]

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.operator-handoff-capsule",
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "taskState": self.task_state.value,
            "taskRevision": self.task_revision,
            "eventKind": self.event_kind.value,
            "eventPayloadDigest": self.event_payload_digest,
            "readyFrontier": list(self.ready_frontier),
            "descriptorDigest": self.descriptor_digest,
            "proposalDigest": self.proposal_digest,
            "decisionRequestId": self.decision_request_id,
            "childTaskId": self.child_task_id,
            "dispatchObjectDigest": self.dispatch_object_digest,
            "backendJobId": self.backend_job_id,
            "outcomeObjectDigest": self.outcome_object_digest,
            "mustNotRepeatObjectDigests": list(self.must_not_repeat_object_digests),
            "nextAdmissible": list(self.next_admissible),
        }


def operator_handoff(storage: HostStorage, task_id: str) -> OperatorHandoffCapsule:
    snapshot = storage.read_task_event(task_id)
    data = snapshot.data if isinstance(snapshot.data, dict) else {}
    descriptor_digest = data.get("descriptorDigest")
    if descriptor_digest is not None and not isinstance(descriptor_digest, str):
        descriptor_digest = None
    proposal_digest = data.get("proposalDigest")
    if proposal_digest is not None and not isinstance(proposal_digest, str):
        proposal_digest = None
    decision_request_id = data.get("decisionRequestId")
    if decision_request_id is not None and not isinstance(decision_request_id, str):
        decision_request_id = None
    child_task_id = data.get("childTaskId")
    if child_task_id is not None and not isinstance(child_task_id, str):
        child_task_id = None
    dispatch_digest = data.get("dispatchDigest")
    if dispatch_digest is not None and not isinstance(dispatch_digest, str):
        dispatch_digest = None
    job_id = data.get("jobId")
    if job_id is not None and not isinstance(job_id, str):
        job_id = None
    outcome_digest = data.get("outcomeDigest")
    if outcome_digest is not None and not isinstance(outcome_digest, str):
        outcome_digest = None
    must_not_repeat: list[str] = []
    effect_digest = data.get("effectDigest")
    if isinstance(effect_digest, str) and snapshot.event_kind in {
        EventKind.EFFECT_DISPATCH_OBSERVED,
        EventKind.VERIFICATION_RECORDED,
        EventKind.VERIFICATION_ACCEPTED,
        EventKind.TASK_OUTCOME_RECORDED,
        EventKind.TASK_STATE_CHANGED,
    }:
        must_not_repeat.append(effect_digest)
    if snapshot.event_kind in {EventKind.EFFECT_OUTCOME_UNKNOWN, EventKind.RUNTIME_OUTCOME_UNKNOWN}:
        next_admissible = ("reconcile-existing-dispatch",)
    elif snapshot.projection.state is TaskState.BLOCKED and decision_request_id is not None:
        next_admissible = ("resolve-decision-request",)
    elif snapshot.projection.ready_frontier:
        next_admissible = snapshot.projection.ready_frontier
    elif snapshot.projection.state.terminal:
        next_admissible = ("inspect-terminal-outcome",)
    else:
        next_admissible = ("inspect-current-task",)
    return OperatorHandoffCapsule(
        task_id=snapshot.projection.task_id,
        goal_id=snapshot.projection.goal_id,
        task_state=snapshot.projection.state,
        task_revision=snapshot.projection.revision,
        event_kind=snapshot.event_kind,
        event_payload_digest=snapshot.payload_digest,
        ready_frontier=snapshot.projection.ready_frontier,
        descriptor_digest=descriptor_digest,
        proposal_digest=proposal_digest,
        decision_request_id=decision_request_id,
        child_task_id=child_task_id,
        dispatch_object_digest=dispatch_digest,
        backend_job_id=job_id,
        outcome_object_digest=outcome_digest,
        must_not_repeat_object_digests=tuple(must_not_repeat),
        next_admissible=tuple(next_admissible),
    )
