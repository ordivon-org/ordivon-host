from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from anc_canonical import JsonValue, canonical_bytes, canonical_digest, validate_json_value

from .domain import EventKind, TaskProjection
from .extensions import HostExtensionPort
from .objects import StoredObject

_REQUEST_FIELD = "externalExecutionRequestObjectDigest"
_BINDING_FIELD = "externalRunBindingObjectDigest"
_PROPOSAL_FIELD = "externalCompletionProposalObjectDigest"
_REQUEST_KIND = "external-execution-request"
_BINDING_KIND = "external-run-binding"
_PROPOSAL_KIND = "external-completion-proposal"
_MAX_METADATA_BYTES = 32_768


class ExternalExecutorError(RuntimeError):
    pass


class ExternalExecutionMissing(ExternalExecutorError):
    pass


class ExternalRequestConflict(ExternalExecutorError):
    pass


class ExternalObservationConflict(ExternalExecutorError):
    pass


class ExternalCompletionConflict(ExternalExecutorError):
    pass


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def _text(value: str, label: str, *, max_bytes: int = 1_024) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _identity(value: str, prefix: str, label: str) -> str:
    _text(value, label, max_bytes=500)
    if not value.startswith(prefix + ":"):
        raise ValueError(f"{label} must start with {prefix}:")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _metadata(value: dict[str, JsonValue], label: str) -> None:
    validate_json_value(value)
    if len(canonical_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"{label} exceeds {_MAX_METADATA_BYTES} canonical bytes")


def _unique(values: tuple[str, ...], label: str) -> None:
    for value in values:
        _text(value, label, max_bytes=2_048)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


class ExternalRunStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {
            ExternalRunStatus.COMPLETED,
            ExternalRunStatus.FAILED,
            ExternalRunStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class ExternalExecutionRequest:
    request_id: str
    adapter_id: str
    task_id: str
    task_revision: int
    task_attempt_ref: str
    contract_digest: str
    correlation_context: dict[str, JsonValue]
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.request_id, "external-request", "external request")
        _identity(self.adapter_id, "external-executor", "external executor")
        _identity(self.task_id, "task", "Host Task")
        if self.task_revision < 1:
            raise ValueError("external request Task revision must be positive")
        _identity(self.task_attempt_ref, "task-attempt", "Task Attempt reference")
        _digest(self.contract_digest, "external contract digest")
        _metadata(self.correlation_context, "external correlation context")
        if self.created_at_ms < 0:
            raise ValueError("external request creation time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.external-execution-request",
            "requestId": self.request_id,
            "adapterId": self.adapter_id,
            "taskId": self.task_id,
            "taskRevision": self.task_revision,
            "taskAttemptRef": self.task_attempt_ref,
            "contractDigest": self.contract_digest,
            "correlationContext": self.correlation_context,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExternalExecutionRequest:
        expected = {
            "schemaVersion",
            "kind",
            "requestId",
            "adapterId",
            "taskId",
            "taskRevision",
            "taskAttemptRef",
            "contractDigest",
            "correlationContext",
            "createdAtMs",
        }
        _exact(value, expected, "ExternalExecutionRequest")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.external-execution-request":
            raise ValueError("ExternalExecutionRequest version or kind is invalid")
        for field in (
            "requestId",
            "adapterId",
            "taskId",
            "taskAttemptRef",
            "contractDigest",
        ):
            if not isinstance(value[field], str):
                raise ValueError(f"ExternalExecutionRequest {field} must be a string")
        if type(value["taskRevision"]) is not int or type(value["createdAtMs"]) is not int:
            raise ValueError("ExternalExecutionRequest revisions and times must be integers")
        if not isinstance(value["correlationContext"], dict):
            raise ValueError("ExternalExecutionRequest correlation context must be an object")
        return cls(
            request_id=value["requestId"],
            adapter_id=value["adapterId"],
            task_id=value["taskId"],
            task_revision=value["taskRevision"],
            task_attempt_ref=value["taskAttemptRef"],
            contract_digest=value["contractDigest"],
            correlation_context=dict(value["correlationContext"]),
            created_at_ms=value["createdAtMs"],
        )


@dataclass(frozen=True, slots=True)
class ExternalRunObservation:
    foreign_run_ref: str
    status: ExternalRunStatus
    revision: int
    evidence_refs: tuple[str, ...]
    observed_at_ms: int
    metadata: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _text(self.foreign_run_ref, "foreign Run reference", max_bytes=2_048)
        if self.revision < 0:
            raise ValueError("external observation revision must be non-negative")
        _unique(self.evidence_refs, "external evidence reference")
        if self.observed_at_ms < 0:
            raise ValueError("external observation time must be non-negative")
        _metadata(self.metadata, "external observation metadata")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.external-run-observation",
            "foreignRunRef": self.foreign_run_ref,
            "status": self.status.value,
            "revision": self.revision,
            "evidenceRefs": list(self.evidence_refs),
            "observedAtMs": self.observed_at_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExternalRunObservation:
        expected = {
            "schemaVersion",
            "kind",
            "foreignRunRef",
            "status",
            "revision",
            "evidenceRefs",
            "observedAtMs",
            "metadata",
        }
        _exact(value, expected, "ExternalRunObservation")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.external-run-observation":
            raise ValueError("ExternalRunObservation version or kind is invalid")
        if not isinstance(value["foreignRunRef"], str) or not isinstance(value["status"], str):
            raise ValueError("ExternalRunObservation identity and status must be strings")
        if type(value["revision"]) is not int or type(value["observedAtMs"]) is not int:
            raise ValueError("ExternalRunObservation revision and time must be integers")
        if not isinstance(value["evidenceRefs"], list) or any(
            not isinstance(item, str) for item in value["evidenceRefs"]
        ):
            raise ValueError("ExternalRunObservation evidence refs must be strings")
        if not isinstance(value["metadata"], dict):
            raise ValueError("ExternalRunObservation metadata must be an object")
        return cls(
            foreign_run_ref=value["foreignRunRef"],
            status=ExternalRunStatus(value["status"]),
            revision=value["revision"],
            evidence_refs=tuple(value["evidenceRefs"]),
            observed_at_ms=value["observedAtMs"],
            metadata=dict(value["metadata"]),
        )


@dataclass(frozen=True, slots=True)
class ExternalCompletionProposal:
    proposal_id: str
    foreign_run_ref: str
    contract_digest: str
    summary: str
    evidence_refs: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    created_at_ms: int
    metadata: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _identity(self.proposal_id, "completion-proposal", "Completion Proposal")
        _text(self.foreign_run_ref, "foreign Run reference", max_bytes=2_048)
        _digest(self.contract_digest, "Completion Proposal contract digest")
        _text(self.summary, "Completion Proposal summary", max_bytes=8_000)
        _unique(self.evidence_refs, "Completion Proposal evidence reference")
        _unique(self.artifact_refs, "Completion Proposal Artifact reference")
        if self.created_at_ms < 0:
            raise ValueError("Completion Proposal creation time must be non-negative")
        _metadata(self.metadata, "Completion Proposal metadata")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.external-completion-proposal",
            "proposalId": self.proposal_id,
            "foreignRunRef": self.foreign_run_ref,
            "contractDigest": self.contract_digest,
            "summary": self.summary,
            "evidenceRefs": list(self.evidence_refs),
            "artifactRefs": list(self.artifact_refs),
            "createdAtMs": self.created_at_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExternalCompletionProposal:
        expected = {
            "schemaVersion",
            "kind",
            "proposalId",
            "foreignRunRef",
            "contractDigest",
            "summary",
            "evidenceRefs",
            "artifactRefs",
            "createdAtMs",
            "metadata",
        }
        _exact(value, expected, "ExternalCompletionProposal")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.external-completion-proposal":
            raise ValueError("ExternalCompletionProposal version or kind is invalid")
        for field in ("proposalId", "foreignRunRef", "contractDigest", "summary"):
            if not isinstance(value[field], str):
                raise ValueError(f"ExternalCompletionProposal {field} must be a string")
        for field in ("evidenceRefs", "artifactRefs"):
            if not isinstance(value[field], list) or any(
                not isinstance(item, str) for item in value[field]
            ):
                raise ValueError(f"ExternalCompletionProposal {field} must contain strings")
        if type(value["createdAtMs"]) is not int or not isinstance(value["metadata"], dict):
            raise ValueError("ExternalCompletionProposal time or metadata is invalid")
        return cls(
            proposal_id=value["proposalId"],
            foreign_run_ref=value["foreignRunRef"],
            contract_digest=value["contractDigest"],
            summary=value["summary"],
            evidence_refs=tuple(value["evidenceRefs"]),
            artifact_refs=tuple(value["artifactRefs"]),
            created_at_ms=value["createdAtMs"],
            metadata=dict(value["metadata"]),
        )


@dataclass(frozen=True, slots=True)
class ExternalRunBinding:
    binding_id: str
    adapter_id: str
    request_id: str
    foreign_run_ref: str
    contract_digest: str
    task_id: str
    task_attempt_ref: str
    correlation_context: dict[str, JsonValue]
    observed_status: ExternalRunStatus
    evidence_refs: tuple[str, ...]
    last_reconciled_revision: int
    last_observation_digest: str
    cancellation_requested: bool
    completion_proposal_digest: str | None
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.binding_id, "external-binding", "external Run binding")
        _identity(self.adapter_id, "external-executor", "external executor")
        _identity(self.request_id, "external-request", "external request")
        _text(self.foreign_run_ref, "foreign Run reference", max_bytes=2_048)
        _digest(self.contract_digest, "external contract digest")
        _identity(self.task_id, "task", "Host Task")
        _identity(self.task_attempt_ref, "task-attempt", "Task Attempt reference")
        _metadata(self.correlation_context, "external correlation context")
        _unique(self.evidence_refs, "external evidence reference")
        if self.last_reconciled_revision < 0:
            raise ValueError("external reconciled revision must be non-negative")
        _digest(self.last_observation_digest, "external observation digest")
        if type(self.cancellation_requested) is not bool:
            raise ValueError("external cancellation flag must be boolean")
        if self.completion_proposal_digest is not None:
            _digest(self.completion_proposal_digest, "Completion Proposal digest")
        if self.created_at_ms < 0 or self.updated_at_ms < self.created_at_ms:
            raise ValueError("external binding times are invalid")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.external-run-binding",
            "bindingId": self.binding_id,
            "adapterId": self.adapter_id,
            "requestId": self.request_id,
            "foreignRunRef": self.foreign_run_ref,
            "contractDigest": self.contract_digest,
            "taskId": self.task_id,
            "taskAttemptRef": self.task_attempt_ref,
            "correlationContext": self.correlation_context,
            "observedStatus": self.observed_status.value,
            "evidenceRefs": list(self.evidence_refs),
            "lastReconciledRevision": self.last_reconciled_revision,
            "lastObservationDigest": self.last_observation_digest,
            "cancellationRequested": self.cancellation_requested,
            "completionProposalDigest": self.completion_proposal_digest,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExternalRunBinding:
        expected = {
            "schemaVersion",
            "kind",
            "bindingId",
            "adapterId",
            "requestId",
            "foreignRunRef",
            "contractDigest",
            "taskId",
            "taskAttemptRef",
            "correlationContext",
            "observedStatus",
            "evidenceRefs",
            "lastReconciledRevision",
            "lastObservationDigest",
            "cancellationRequested",
            "completionProposalDigest",
            "createdAtMs",
            "updatedAtMs",
        }
        _exact(value, expected, "ExternalRunBinding")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.external-run-binding":
            raise ValueError("ExternalRunBinding version or kind is invalid")
        for field in (
            "bindingId",
            "adapterId",
            "requestId",
            "foreignRunRef",
            "contractDigest",
            "taskId",
            "taskAttemptRef",
            "observedStatus",
            "lastObservationDigest",
        ):
            if not isinstance(value[field], str):
                raise ValueError(f"ExternalRunBinding {field} must be a string")
        if not isinstance(value["correlationContext"], dict):
            raise ValueError("ExternalRunBinding correlation context must be an object")
        if not isinstance(value["evidenceRefs"], list) or any(
            not isinstance(item, str) for item in value["evidenceRefs"]
        ):
            raise ValueError("ExternalRunBinding evidence refs must be strings")
        if (
            type(value["lastReconciledRevision"]) is not int
            or type(value["cancellationRequested"]) is not bool
            or type(value["createdAtMs"]) is not int
            or type(value["updatedAtMs"]) is not int
        ):
            raise ValueError("ExternalRunBinding revisions, flags, and times are invalid")
        proposal_digest = value["completionProposalDigest"]
        if proposal_digest is not None and not isinstance(proposal_digest, str):
            raise ValueError("ExternalRunBinding Completion Proposal digest is invalid")
        return cls(
            binding_id=value["bindingId"],
            adapter_id=value["adapterId"],
            request_id=value["requestId"],
            foreign_run_ref=value["foreignRunRef"],
            contract_digest=value["contractDigest"],
            task_id=value["taskId"],
            task_attempt_ref=value["taskAttemptRef"],
            correlation_context=dict(value["correlationContext"]),
            observed_status=ExternalRunStatus(value["observedStatus"]),
            evidence_refs=tuple(value["evidenceRefs"]),
            last_reconciled_revision=value["lastReconciledRevision"],
            last_observation_digest=value["lastObservationDigest"],
            cancellation_requested=value["cancellationRequested"],
            completion_proposal_digest=proposal_digest,
            created_at_ms=value["createdAtMs"],
            updated_at_ms=value["updatedAtMs"],
        )


@dataclass(frozen=True, slots=True)
class ExternalExecutionSnapshot:
    projection: TaskProjection
    request: ExternalExecutionRequest | None
    request_object: StoredObject | None
    binding: ExternalRunBinding | None
    binding_object: StoredObject | None
    completion_proposal: ExternalCompletionProposal | None
    completion_proposal_object: StoredObject | None


@runtime_checkable
class ExternalExecutorAdapter(Protocol):
    adapter_id: str

    def start(self, request: ExternalExecutionRequest) -> ExternalRunObservation: ...

    def observe(self, foreign_run_ref: str) -> ExternalRunObservation: ...

    def cancel(self, foreign_run_ref: str, request_id: str) -> ExternalRunObservation: ...

    def recover(
        self,
        request: ExternalExecutionRequest,
        foreign_run_ref: str | None,
    ) -> ExternalRunObservation: ...

    def collect_completion(self, foreign_run_ref: str) -> ExternalCompletionProposal | None: ...


class ExternalExecutorCoordinator:
    """Host-owned foreign Run binding without foreign lifecycle authority.

    The immutable request is admitted before adapter delivery. Adapter observations
    update only an opaque foreign binding; they never advance, verify, complete, or
    fail the Host Task. A collected Completion Proposal is retained for a later
    Host-owned verification and completion decision.
    """

    def __init__(self, port: HostExtensionPort) -> None:
        self.port = port

    def load(self, task_id: str) -> ExternalExecutionSnapshot:
        snapshot = self.port.load(task_id)
        request, request_object = self._load_optional(
            snapshot.data.get(_REQUEST_FIELD),
            expected_kind=_REQUEST_KIND,
            decoder=ExternalExecutionRequest.from_dict,
        )
        binding, binding_object = self._load_optional(
            snapshot.data.get(_BINDING_FIELD),
            expected_kind=_BINDING_KIND,
            decoder=ExternalRunBinding.from_dict,
        )
        proposal, proposal_object = self._load_optional(
            snapshot.data.get(_PROPOSAL_FIELD),
            expected_kind=_PROPOSAL_KIND,
            decoder=ExternalCompletionProposal.from_dict,
        )
        if binding is not None and request is None:
            raise ExternalExecutionMissing("external Run Binding has no retained request")
        if proposal is not None and binding is None:
            raise ExternalExecutionMissing("external Completion Proposal has no retained binding")
        if request is not None:
            if request.task_id != task_id:
                raise ExternalRequestConflict("external request belongs to another Host Task")
            if binding is not None:
                self._validate_binding(request, binding)
            if proposal is not None:
                assert binding is not None
                self._validate_proposal(binding, proposal)
        return ExternalExecutionSnapshot(
            projection=snapshot.projection,
            request=request,
            request_object=request_object,
            binding=binding,
            binding_object=binding_object,
            completion_proposal=proposal,
            completion_proposal_object=proposal_object,
        )

    def start(
        self,
        request: ExternalExecutionRequest,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        self._validate_adapter(request.adapter_id, adapter)
        current = self.load(request.task_id)
        if current.request is None:
            if current.projection.revision != request.task_revision:
                raise ExternalRequestConflict(
                    f"Host Task revision is {current.projection.revision}, "
                    f"expected {request.task_revision}"
                )
            request_object = self.port.put_object(request.to_dict(), kind=_REQUEST_KIND)
            committed = self.port.append_preserving(
                task_id=request.task_id,
                expected_revision=current.projection.revision,
                event_id=self._event_id("request", request.digest),
                kind=EventKind("external.execution-requested"),
                updates={_REQUEST_FIELD: request_object.digest},
                remove_fields=(_BINDING_FIELD, _PROPOSAL_FIELD),
                referenced_objects=(request_object,),
                label="external execution request",
            )
            current = self.load(committed.projection.task_id)
        elif current.request != request:
            raise ExternalRequestConflict(
                "external request identity is already bound to different bytes"
            )
        if current.binding is not None:
            return current
        observation = adapter.start(request)
        return self._record_observation(
            current,
            observation,
            event_label="bound",
            event_kind=EventKind("external.run-bound"),
        )

    def observe(
        self,
        task_id: str,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        current = self._require_binding(task_id, adapter)
        assert current.binding is not None
        observation = adapter.observe(current.binding.foreign_run_ref)
        return self._record_observation(
            current,
            observation,
            event_label="observed",
            event_kind=EventKind("external.run-observed"),
        )

    def cancel(
        self,
        task_id: str,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        current = self._require_binding(task_id, adapter)
        assert current.binding is not None
        observation = adapter.cancel(
            current.binding.foreign_run_ref,
            current.binding.request_id,
        )
        return self._record_observation(
            current,
            observation,
            event_label="cancel",
            event_kind=EventKind("external.cancel-requested"),
            cancellation_requested=True,
        )

    def recover(
        self,
        task_id: str,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        current = self.load(task_id)
        if current.request is None:
            raise ExternalExecutionMissing("Host Task has no external execution request")
        self._validate_adapter(current.request.adapter_id, adapter)
        observation = adapter.recover(
            current.request,
            None if current.binding is None else current.binding.foreign_run_ref,
        )
        return self._record_observation(
            current,
            observation,
            event_label="recovered",
            event_kind=EventKind("external.run-recovered"),
        )

    def collect_completion(
        self,
        task_id: str,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        current = self._require_binding(task_id, adapter)
        assert current.binding is not None
        proposal = adapter.collect_completion(current.binding.foreign_run_ref)
        if proposal is None:
            return current
        self._validate_proposal(current.binding, proposal)
        if current.completion_proposal is not None:
            if current.completion_proposal != proposal:
                raise ExternalCompletionConflict(
                    "foreign Run is already bound to another Completion Proposal"
                )
            return current
        proposal_object = self.port.put_object(proposal.to_dict(), kind=_PROPOSAL_KIND)
        binding = replace(
            current.binding,
            completion_proposal_digest=proposal.digest,
            updated_at_ms=max(current.binding.updated_at_ms, proposal.created_at_ms),
        )
        binding_object = self.port.put_object(binding.to_dict(), kind=_BINDING_KIND)
        assert current.request_object is not None
        committed = self.port.append_preserving(
            task_id=task_id,
            expected_revision=current.projection.revision,
            event_id=self._event_id("completion", proposal.digest),
            kind=EventKind("external.completion-collected"),
            updates={
                _BINDING_FIELD: binding_object.digest,
                _PROPOSAL_FIELD: proposal_object.digest,
            },
            referenced_objects=(
                current.request_object,
                binding_object,
                proposal_object,
            ),
            label="external completion collection",
        )
        return self.load(committed.projection.task_id)

    def _record_observation(
        self,
        current: ExternalExecutionSnapshot,
        observation: ExternalRunObservation,
        *,
        event_label: str,
        event_kind: EventKind,
        cancellation_requested: bool | None = None,
    ) -> ExternalExecutionSnapshot:
        request = current.request
        if request is None:
            raise ExternalExecutionMissing("external observation has no retained request")
        if current.binding is None:
            binding = self._new_binding(request, observation)
        else:
            self._validate_observation(current.binding, observation)
            exact_replay = (
                observation.revision == current.binding.last_reconciled_revision
                and observation.digest == current.binding.last_observation_digest
            )
            needs_cancellation_admission = (
                cancellation_requested is True
                and not current.binding.cancellation_requested
            )
            if exact_replay and not needs_cancellation_admission:
                return current
            binding = replace(
                current.binding,
                observed_status=observation.status,
                evidence_refs=observation.evidence_refs,
                last_reconciled_revision=observation.revision,
                last_observation_digest=observation.digest,
                cancellation_requested=(
                    current.binding.cancellation_requested
                    if cancellation_requested is None
                    else cancellation_requested
                ),
                updated_at_ms=max(
                    current.binding.updated_at_ms,
                    observation.observed_at_ms,
                ),
            )
        if cancellation_requested is True and not binding.cancellation_requested:
            binding = replace(binding, cancellation_requested=True)
        binding_object = self.port.put_object(binding.to_dict(), kind=_BINDING_KIND)
        assert current.request_object is not None
        retained = [current.request_object, binding_object]
        if current.completion_proposal_object is not None:
            retained.append(current.completion_proposal_object)
        committed = self.port.append_preserving(
            task_id=request.task_id,
            expected_revision=current.projection.revision,
            event_id=self._event_id(event_label, observation.digest),
            kind=event_kind,
            updates={_BINDING_FIELD: binding_object.digest},
            referenced_objects=tuple(retained),
            label=f"external Run {event_label}",
        )
        return self.load(committed.projection.task_id)

    def _require_binding(
        self,
        task_id: str,
        adapter: ExternalExecutorAdapter,
    ) -> ExternalExecutionSnapshot:
        current = self.load(task_id)
        if current.request is None or current.binding is None:
            raise ExternalExecutionMissing("Host Task has no bound external Run")
        self._validate_adapter(current.request.adapter_id, adapter)
        return current

    @staticmethod
    def _new_binding(
        request: ExternalExecutionRequest,
        observation: ExternalRunObservation,
    ) -> ExternalRunBinding:
        token = canonical_digest(
            {
                "requestDigest": request.digest,
                "foreignRunRef": observation.foreign_run_ref,
            }
        )[7:31]
        return ExternalRunBinding(
            binding_id=f"external-binding:{token}",
            adapter_id=request.adapter_id,
            request_id=request.request_id,
            foreign_run_ref=observation.foreign_run_ref,
            contract_digest=request.contract_digest,
            task_id=request.task_id,
            task_attempt_ref=request.task_attempt_ref,
            correlation_context=request.correlation_context,
            observed_status=observation.status,
            evidence_refs=observation.evidence_refs,
            last_reconciled_revision=observation.revision,
            last_observation_digest=observation.digest,
            cancellation_requested=False,
            completion_proposal_digest=None,
            created_at_ms=max(request.created_at_ms, observation.observed_at_ms),
            updated_at_ms=max(request.created_at_ms, observation.observed_at_ms),
        )

    @staticmethod
    def _validate_adapter(expected: str, adapter: ExternalExecutorAdapter) -> None:
        if adapter.adapter_id != expected:
            raise ExternalRequestConflict(
                f"external Adapter is {adapter.adapter_id}, expected {expected}"
            )

    @staticmethod
    def _validate_binding(
        request: ExternalExecutionRequest,
        binding: ExternalRunBinding,
    ) -> None:
        if (
            binding.adapter_id != request.adapter_id
            or binding.request_id != request.request_id
            or binding.contract_digest != request.contract_digest
            or binding.task_id != request.task_id
            or binding.task_attempt_ref != request.task_attempt_ref
            or binding.correlation_context != request.correlation_context
        ):
            raise ExternalRequestConflict("external Run Binding differs from its request")

    @staticmethod
    def _validate_observation(
        binding: ExternalRunBinding,
        observation: ExternalRunObservation,
    ) -> None:
        if observation.foreign_run_ref != binding.foreign_run_ref:
            raise ExternalObservationConflict("external observation belongs to another Run")
        if observation.revision < binding.last_reconciled_revision:
            raise ExternalObservationConflict("external observation revision moved backwards")
        if (
            observation.revision == binding.last_reconciled_revision
            and observation.digest != binding.last_observation_digest
        ):
            raise ExternalObservationConflict(
                "same external revision produced different observation bytes"
            )

    @staticmethod
    def _validate_proposal(
        binding: ExternalRunBinding,
        proposal: ExternalCompletionProposal,
    ) -> None:
        if proposal.foreign_run_ref != binding.foreign_run_ref:
            raise ExternalCompletionConflict("Completion Proposal belongs to another Run")
        if proposal.contract_digest != binding.contract_digest:
            raise ExternalCompletionConflict("Completion Proposal contract differs")

    def _load_optional(self, digest: JsonValue | None, *, expected_kind: str, decoder):
        if digest is None:
            return None, None
        if not isinstance(digest, str):
            raise ExternalExecutionMissing("external object reference is not a digest")
        stored = self.port.inspect_object(digest)
        value = self.port.get_object(digest, expected_kind=expected_kind)
        if not isinstance(value, dict):
            raise ExternalExecutionMissing("external retained object is not an object")
        return decoder(value), stored

    @staticmethod
    def _event_id(label: str, digest: str) -> str:
        return f"event:external-{label}:{digest[7:31]}"


__all__ = [
    "ExternalCompletionConflict",
    "ExternalCompletionProposal",
    "ExternalExecutionMissing",
    "ExternalExecutionRequest",
    "ExternalExecutionSnapshot",
    "ExternalExecutorAdapter",
    "ExternalExecutorCoordinator",
    "ExternalExecutorError",
    "ExternalObservationConflict",
    "ExternalRequestConflict",
    "ExternalRunBinding",
    "ExternalRunObservation",
    "ExternalRunStatus",
]
