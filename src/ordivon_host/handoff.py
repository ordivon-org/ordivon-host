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
    task_attempt_id: str | None
    assignment_id: str | None
    assignment_generation: int | None
    harness_run_id: str | None
    completion_proposal_id: str | None
    completion_decision_id: str | None
    must_not_repeat_object_digests: tuple[str, ...]
    next_admissible: tuple[str, ...]

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 2,
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
            "taskAttemptId": self.task_attempt_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "harnessRunId": self.harness_run_id,
            "completionProposalId": self.completion_proposal_id,
            "completionDecisionId": self.completion_decision_id,
            "mustNotRepeatObjectDigests": list(self.must_not_repeat_object_digests),
            "nextAdmissible": list(self.next_admissible),
        }


def _optional_string(data: dict[str, JsonValue], field: str) -> str | None:
    value = data.get(field)
    return value if isinstance(value, str) else None


def _optional_int(data: dict[str, JsonValue], field: str) -> int | None:
    value = data.get(field)
    return value if type(value) is int else None


def operator_handoff(storage: HostStorage, task_id: str) -> OperatorHandoffCapsule:
    snapshot = storage.read_task_event(task_id)
    data = snapshot.data if isinstance(snapshot.data, dict) else {}
    descriptor_digest = _optional_string(data, "descriptorDigest")
    proposal_digest = _optional_string(data, "proposalDigest")
    decision_request_id = _optional_string(data, "decisionRequestId")
    child_task_id = _optional_string(data, "childTaskId")
    dispatch_digest = _optional_string(data, "dispatchDigest")
    job_id = _optional_string(data, "jobId")
    outcome_digest = _optional_string(data, "outcomeDigest")
    task_attempt_id = _optional_string(data, "taskAttemptId")
    assignment_id = _optional_string(data, "assignmentId")
    assignment_generation = _optional_int(data, "assignmentGeneration")
    harness_run_id = _optional_string(data, "harnessRunId")
    completion_proposal_id = _optional_string(data, "completionProposalId")
    completion_decision_id = _optional_string(data, "completionDecisionId")
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
    elif snapshot.event_kind is EventKind.HARNESS_ASSIGNMENT_COMMITTED:
        next_admissible = ("run-current-harness-assignment",)
    elif snapshot.event_kind is EventKind.HARNESS_RUN_RECOVERY_RECORDED:
        next_admissible = (
            ("abandon-current-harness-run",)
            if data.get("harnessRunRecoverySafeToAbandon") is True
            else ("reconcile-current-harness-run-unknown",)
        )
    elif snapshot.event_kind is EventKind.HARNESS_RUN_ABANDONED:
        next_admissible = ("replace-harness-assignment",)
    elif snapshot.event_kind is EventKind.HARNESS_RUN_RECORDED:
        termination = data.get("harnessRunTerminationCode")
        replacement_allowed = data.get("harnessRunReplacementAllowed")
        if termination == "runtime_unknown":
            next_admissible = ("reconcile-current-harness-run-unknown",)
        elif termination == "candidate_completed":
            next_admissible = (
                ("replace-harness-or-propose-completion",)
                if replacement_allowed is not False
                else ("propose-completion-from-current-harness-run",)
            )
        elif replacement_allowed is False:
            next_admissible = ("verify-current-harness-run-before-replacement",)
        else:
            next_admissible = ("replace-harness-assignment",)
    elif snapshot.event_kind is EventKind.COMPLETION_PROPOSED:
        next_admissible = ("adjudicate-completion-proposal",)
    elif (
        snapshot.event_kind is EventKind.COMPLETION_DECIDED
        and data.get("completionAccepted") is False
    ):
        next_admissible = ("continue-current-harness-assignment",)
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
        task_attempt_id=task_attempt_id,
        assignment_id=assignment_id,
        assignment_generation=assignment_generation,
        harness_run_id=harness_run_id,
        completion_proposal_id=completion_proposal_id,
        completion_decision_id=completion_decision_id,
        must_not_repeat_object_digests=tuple(must_not_repeat),
        next_admissible=tuple(next_admissible),
    )
