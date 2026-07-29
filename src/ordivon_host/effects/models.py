from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue
from ordivon_protocol import validate_host_workload_object

from ..domain import TaskDescriptor, TaskState
from ..objects import StoredObject


@dataclass(frozen=True, slots=True)
class StateRef:
    ref: str
    digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"ref": self.ref, "digest": self.digest}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StateRef:
        return cls(ref=str(value["ref"]), digest=str(value["digest"]))


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    ref: str
    kind: str
    digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"ref": self.ref, "kind": self.kind, "digest": self.digest}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArtifactRef:
        return cls(
            ref=str(value["ref"]),
            kind=str(value["kind"]),
            digest=str(value["digest"]),
        )


@dataclass(frozen=True, slots=True)
class DispatchEnvelope:
    dispatch_id: str
    effect_id: str
    executor_id: str
    request_digest: str
    idempotency_key: str
    required_state_refs: tuple[StateRef, ...]
    expected_observation_kind: str

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.dispatch-envelope",
            "dispatchId": self.dispatch_id,
            "effectId": self.effect_id,
            "executorId": self.executor_id,
            "requestDigest": self.request_digest,
            "idempotencyKey": self.idempotency_key,
            "requiredStateRefs": [item.to_dict() for item in self.required_state_refs],
            "expectedObservationKind": self.expected_observation_kind,
        }
        validate_host_workload_object(value)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DispatchEnvelope:
        validate_host_workload_object(value)
        if value.get("kind") != "ordivon.dispatch-envelope":
            raise ValueError("wire object is not a DispatchEnvelope")
        refs = value["requiredStateRefs"]
        assert isinstance(refs, list)
        return cls(
            dispatch_id=str(value["dispatchId"]),
            effect_id=str(value["effectId"]),
            executor_id=str(value["executorId"]),
            request_digest=str(value["requestDigest"]),
            idempotency_key=str(value["idempotencyKey"]),
            required_state_refs=tuple(StateRef.from_dict(item) for item in refs),
            expected_observation_kind=str(value["expectedObservationKind"]),
        )


@dataclass(frozen=True, slots=True)
class ObservationEnvelope:
    dispatch_id: str
    executor_id: str
    status: str
    payload_digest: str
    evidence_refs: tuple[ArtifactRef, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.observation-envelope",
            "dispatchId": self.dispatch_id,
            "executorId": self.executor_id,
            "status": self.status,
            "payloadDigest": self.payload_digest,
            "evidenceRefs": [item.to_dict() for item in self.evidence_refs],
        }
        validate_host_workload_object(value)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ObservationEnvelope:
        validate_host_workload_object(value)
        if value.get("kind") != "ordivon.observation-envelope":
            raise ValueError("wire object is not an ObservationEnvelope")
        refs = value["evidenceRefs"]
        assert isinstance(refs, list)
        return cls(
            dispatch_id=str(value["dispatchId"]),
            executor_id=str(value["executorId"]),
            status=str(value["status"]),
            payload_digest=str(value["payloadDigest"]),
            evidence_refs=tuple(ArtifactRef.from_dict(item) for item in refs),
        )


@dataclass(frozen=True, slots=True)
class VerificationResultItem:
    subject_ref: str
    decision_digest: str
    status: str
    reason: str | None
    evidence_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "subjectRef": self.subject_ref,
            "decisionDigest": self.decision_digest,
            "status": self.status,
            "reason": self.reason,
            "evidenceDigest": self.evidence_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerificationResultItem:
        return cls(
            subject_ref=str(value["subjectRef"]),
            decision_digest=str(value["decisionDigest"]),
            status=str(value["status"]),
            reason=None if value["reason"] is None else str(value["reason"]),
            evidence_digest=str(value["evidenceDigest"]),
        )


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    dispatch_id: str
    method: str
    accepted: bool
    observation_digest: str
    result_items: tuple[VerificationResultItem, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.verification-receipt",
            "dispatchId": self.dispatch_id,
            "method": self.method,
            "accepted": self.accepted,
            "observationDigest": self.observation_digest,
            "resultItems": [item.to_dict() for item in self.result_items],
        }
        validate_host_workload_object(value)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerificationReceipt:
        validate_host_workload_object(value)
        if value.get("kind") != "ordivon.verification-receipt":
            raise ValueError("wire object is not a VerificationReceipt")
        items = value["resultItems"]
        assert isinstance(items, list)
        return cls(
            dispatch_id=str(value["dispatchId"]),
            method=str(value["method"]),
            accepted=bool(value["accepted"]),
            observation_digest=str(value["observationDigest"]),
            result_items=tuple(VerificationResultItem.from_dict(item) for item in items),
        )


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    task_id: str
    goal_id: str
    status: str
    verification_digest: str | None
    artifact_refs: tuple[ArtifactRef, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.task-outcome",
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "status": self.status,
            "verificationDigest": self.verification_digest,
            "artifactRefs": [item.to_dict() for item in self.artifact_refs],
        }
        validate_host_workload_object(value)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskOutcome:
        validate_host_workload_object(value)
        if value.get("kind") != "ordivon.task-outcome":
            raise ValueError("wire object is not a TaskOutcome")
        refs = value["artifactRefs"]
        assert isinstance(refs, list)
        return cls(
            task_id=str(value["taskId"]),
            goal_id=str(value["goalId"]),
            status=str(value["status"]),
            verification_digest=(
                None
                if value["verificationDigest"] is None
                else str(value["verificationDigest"])
            ),
            artifact_refs=tuple(ArtifactRef.from_dict(item) for item in refs),
        )


@dataclass(frozen=True, slots=True)
class PreparedDispatch:
    descriptor: TaskDescriptor
    task_revision: int
    effect_object: StoredObject
    request_object: StoredObject
    dispatch_object: StoredObject
    effect: dict[str, JsonValue]
    request: dict[str, JsonValue]
    dispatch: DispatchEnvelope
    reconcile_frontier: str
    verify_frontier: str
    result_frontier: str


@dataclass(frozen=True, slots=True)
class EffectStep:
    task_id: str
    revision: int
    state: TaskState
    frontier: str | None
    dispatch_id: str | None = None
    observation_digest: str | None = None
    verification_digest: str | None = None
    outcome_digest: str | None = None
    reconciled: bool = False
