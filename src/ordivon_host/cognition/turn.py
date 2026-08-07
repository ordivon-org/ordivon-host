from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from types import MappingProxyType
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..domain import EventKind, TaskProjection, TaskState
from ..journal import JournalCorruption
from ..kernel import HostKernel, worker_owner_id
from ..objects import ObjectCorrupt, StoredObject
from ..storage import HostStorage
from .context import ClosedChoiceContextCompiler, ClosedChoiceContextRequest, CompiledContext
from .decision import ActionSelection, ActionSelectionAdmission


class CognitionError(RuntimeError):
    pass


class CognitionRequestSuperseded(CognitionError):
    pass


class CognitionResultKind(StrEnum):
    ACTION_SELECTION = "action-selection"
    ACTION_PROPOSAL = "action-proposal"


def _digest(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _typed_ref(value: str, label: str, *, max_bytes: int = 1_024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or ":" not in value
        or len(value.encode("utf-8")) > max_bytes
    ):
        raise ValueError(f"{label} must be a bounded typed reference")
    return value


@dataclass(frozen=True, slots=True)
class AdmissionState:
    world_digest: str
    completed_effect_ids: tuple[str, ...]
    unresolved_dispatch_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _digest(self.world_digest, "Admission world digest")
        if any(
            not value.startswith("effect:") or value != value.strip()
            for value in self.completed_effect_ids
        ):
            raise ValueError("completed Effect identities are invalid")
        if any(
            not value.startswith("dispatch:") or value != value.strip()
            for value in self.unresolved_dispatch_ids
        ):
            raise ValueError("unresolved Dispatch identities are invalid")
        if len(self.completed_effect_ids) != len(set(self.completed_effect_ids)):
            raise ValueError("completed Effect identities must be unique")
        if len(self.unresolved_dispatch_ids) != len(set(self.unresolved_dispatch_ids)):
            raise ValueError("unresolved Dispatch identities must be unique")


@dataclass(frozen=True, slots=True)
class CognitionExecutionEvidence:
    """Provider-neutral provenance for one semantic cognition result."""

    source_ref: str
    evidence_refs: tuple[str, ...] = ()
    source_contract_digest: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _typed_ref(self.source_ref, "cognition source")
        for ref in self.evidence_refs:
            _typed_ref(ref, "cognition evidence reference")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("cognition evidence references must be unique")
        if self.source_contract_digest is not None:
            _digest(self.source_contract_digest, "cognition source contract digest")
        validate_json_value(dict(self.metadata))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.cognition-execution-evidence",
            "sourceRef": self.source_ref,
            "sourceContractDigest": self.source_contract_digest,
            "evidenceRefs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CognitionExecutionEvidence:
        expected = {
            "schemaVersion",
            "kind",
            "sourceRef",
            "sourceContractDigest",
            "evidenceRefs",
            "metadata",
        }
        if (
            set(value) != expected
            or value.get("schemaVersion") != 1
            or value.get("kind") != "ordivon.cognition-execution-evidence"
        ):
            raise ValueError("CognitionExecutionEvidence fields, version, or kind differ")
        if not isinstance(value["sourceRef"], str):
            raise ValueError("CognitionExecutionEvidence sourceRef must be a string")
        contract = value["sourceContractDigest"]
        if contract is not None and not isinstance(contract, str):
            raise ValueError(
                "CognitionExecutionEvidence sourceContractDigest must be a string or null"
            )
        refs = value["evidenceRefs"]
        if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
            raise ValueError("CognitionExecutionEvidence evidenceRefs must contain strings")
        metadata = value["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("CognitionExecutionEvidence metadata must be an object")
        return cls(
            source_ref=value["sourceRef"],
            source_contract_digest=contract,
            evidence_refs=tuple(refs),
            metadata=dict(metadata),
        )


@dataclass(frozen=True, slots=True)
class CognitionWorkRequest:
    request_id: str
    task_id: str
    task_revision: int
    node_id: str
    result_kind: CognitionResultKind
    context_digest: str
    context_object_digest: str

    def __post_init__(self) -> None:
        if (
            not self.request_id.startswith("cognition-request:")
            or self.request_id != self.request_id.strip()
        ):
            raise ValueError(
                "Cognition Work Request identity must start with cognition-request:"
            )
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("Cognition Work Request Task identity is invalid")
        if self.task_revision < 1:
            raise ValueError("Cognition Work Request revision must be positive")
        _typed_ref(self.node_id, "Cognition node")
        _digest(self.context_digest, "Cognition Context digest")
        _digest(self.context_object_digest, "Cognition Context object digest")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.cognition-work-request",
            "requestId": self.request_id,
            "taskId": self.task_id,
            "taskRevision": self.task_revision,
            "nodeId": self.node_id,
            "resultKind": self.result_kind.value,
            "contextDigest": self.context_digest,
            "contextObjectDigest": self.context_object_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CognitionWorkRequest:
        expected = {
            "schemaVersion",
            "kind",
            "requestId",
            "taskId",
            "taskRevision",
            "nodeId",
            "resultKind",
            "contextDigest",
            "contextObjectDigest",
        }
        if (
            set(value) != expected
            or value.get("schemaVersion") != 1
            or value.get("kind") != "ordivon.cognition-work-request"
        ):
            raise ValueError("CognitionWorkRequest fields, version, or kind differ")
        for key in (
            "requestId",
            "taskId",
            "nodeId",
            "resultKind",
            "contextDigest",
            "contextObjectDigest",
        ):
            if not isinstance(value[key], str):
                raise ValueError(f"CognitionWorkRequest {key} must be a string")
        if type(value["taskRevision"]) is not int:
            raise ValueError("CognitionWorkRequest taskRevision must be an integer")
        return cls(
            request_id=value["requestId"],
            task_id=value["taskId"],
            task_revision=value["taskRevision"],
            node_id=value["nodeId"],
            result_kind=CognitionResultKind(value["resultKind"]),
            context_digest=value["contextDigest"],
            context_object_digest=value["contextObjectDigest"],
        )


@dataclass(frozen=True, slots=True)
class PreparedCognitionRequest:
    request: CognitionWorkRequest
    request_object: StoredObject
    context_object: StoredObject
    context: CompiledContext


@dataclass(frozen=True, slots=True)
class CognitionAdmissionReceipt:
    task_id: str
    revision: int
    request_id: str
    request_object_digest: str
    context_digest: str
    context_object_digest: str
    selection_object_digest: str
    evidence_object_digest: str
    admission_object_digest: str
    selected_action_id: str
    selected_node_id: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.cognition-admission-receipt",
            "taskId": self.task_id,
            "revision": self.revision,
            "requestId": self.request_id,
            "requestObjectDigest": self.request_object_digest,
            "contextDigest": self.context_digest,
            "contextObjectDigest": self.context_object_digest,
            "selectionObjectDigest": self.selection_object_digest,
            "evidenceObjectDigest": self.evidence_object_digest,
            "admissionObjectDigest": self.admission_object_digest,
            "selectedActionId": self.selected_action_id,
            "selectedNodeId": self.selected_node_id,
        }


class CognitionHost:
    """Host-owned semantic cognition request and admission boundary."""

    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if owner_id is not None and (not owner_id or owner_id != owner_id.strip()):
            raise ValueError("explicit Host owner identity must be trimmed")
        if lease_ttl_ms < 1:
            raise ValueError("Cognition Host lease TTL must be positive")
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:cognition-v2"),
            lease_ttl_ms=lease_ttl_ms,
        )
        self.compiler = ClosedChoiceContextCompiler()
        self.admission = ActionSelectionAdmission()

    def request_selection(
        self,
        *,
        task_id: str,
        node_id: str,
        context_request: ClosedChoiceContextRequest,
        token_budget: int,
    ) -> PreparedCognitionRequest:
        if context_request.task_id != task_id:
            raise ValueError("ClosedChoiceContextRequest belongs to another Task")
        context = self.compiler.compile(context_request, token_budget=token_budget)
        return self.request_compiled(
            task_id=task_id,
            node_id=node_id,
            context=context,
            result_kind=CognitionResultKind.ACTION_SELECTION,
            token_budget=token_budget,
        )

    def request_compiled(
        self,
        *,
        task_id: str,
        node_id: str,
        context: CompiledContext,
        result_kind: CognitionResultKind,
        token_budget: int,
    ) -> PreparedCognitionRequest:
        if context.payload.get("taskId") != task_id:
            raise ValueError("CompiledContext belongs to another Task")
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is EventKind.COGNITION_REQUESTED:
            existing = self._load_request_snapshot(task_id, snapshot)
            if (
                existing.request.node_id != node_id
                or existing.request.result_kind is not result_kind
                or existing.context.digest != context.digest
            ):
                raise CognitionError("Task already waits on another Cognition Work Request")
            return existing
        current = self._require_ready_frontier(task_id, node_id)
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ) as locked:
            next_revision = locked.projection.revision + 1
            context_object = self.storage.put_object(
                context.to_dict(), kind="compiled-context"
            )
            request_id = (
                f"cognition-request:{task_id.removeprefix('task:')}:r{next_revision}"
            )
            request = CognitionWorkRequest(
                request_id=request_id,
                task_id=task_id,
                task_revision=next_revision,
                node_id=node_id,
                result_kind=result_kind,
                context_digest=context.digest,
                context_object_digest=context_object.digest,
            )
            request_object = self.storage.put_object(
                request.to_dict(), kind="cognition-work-request"
            )
            projection = locked.commit(
                event_id=self._event_id(task_id, "requested", next_revision),
                kind=EventKind.COGNITION_REQUESTED,
                payload={
                    "requestId": request_id,
                    "requestObjectDigest": request_object.digest,
                    "nodeId": node_id,
                    "resultKind": result_kind.value,
                    "contextDigest": context.digest,
                    "contextObjectDigest": context_object.digest,
                    "tokenBudget": token_budget,
                },
                state=TaskState.WAITING,
                frontier=(node_id,),
                referenced_objects=(context_object, request_object),
            ).projection
            if projection.revision != request.task_revision:
                raise AssertionError(
                    "Cognition request revision differs from committed Task revision"
                )
            return PreparedCognitionRequest(
                request=request,
                request_object=request_object,
                context_object=context_object,
                context=context,
            )

    def load_request(self, task_id: str) -> PreparedCognitionRequest:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is not EventKind.COGNITION_REQUESTED:
            raise CognitionError("Task head is not a Cognition Work Request")
        return self._load_request_snapshot(task_id, snapshot)

    def admit_selection(
        self,
        prepared: PreparedCognitionRequest,
        selection: ActionSelection,
        *,
        evidence: CognitionExecutionEvidence,
        state_reader: Callable[[], AdmissionState],
    ) -> CognitionAdmissionReceipt:
        if prepared.request.result_kind is not CognitionResultKind.ACTION_SELECTION:
            raise ValueError("Cognition Work Request does not accept an ActionSelection")
        with self.kernel.locked_task(
            prepared.request.task_id,
            expected_revision=prepared.request.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.request.node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ) as locked:
            latest = self._load_request_snapshot(
                prepared.request.task_id, locked.snapshot
            )
            if latest.request != prepared.request:
                raise CognitionRequestSuperseded(
                    "Cognition Work Request changed before admission"
                )
            state = state_reader()
            admitted = self.admission.admit(
                prepared.context,
                selection,
                current_world_digest=state.world_digest,
                completed_effect_ids=state.completed_effect_ids,
                unresolved_dispatch_ids=state.unresolved_dispatch_ids,
            )
            selection_object = self.storage.put_object(
                selection.to_dict(), kind="action-selection"
            )
            evidence_object = self.storage.put_object(
                evidence.to_dict(), kind="cognition-execution-evidence"
            )
            admission_object = self.storage.put_object(
                admitted.to_dict(), kind="admitted-action-selection"
            )
            selected_node_id = self._selected_node(
                prepared.request.task_id, admitted.action.action_id
            )
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.request.task_id,
                    "selection",
                    locked.projection.revision + 1,
                ),
                kind=EventKind.COGNITION_SELECTION_ADMITTED,
                payload={
                    "requestId": prepared.request.request_id,
                    "requestObjectDigest": prepared.request_object.digest,
                    "nodeId": prepared.request.node_id,
                    "selectedNodeId": selected_node_id,
                    "selectedActionId": admitted.action.action_id,
                    "contextDigest": prepared.context.digest,
                    "contextObjectDigest": prepared.context_object.digest,
                    "selectionObjectDigest": selection_object.digest,
                    "evidenceObjectDigest": evidence_object.digest,
                    "admissionObjectDigest": admission_object.digest,
                },
                state=TaskState.READY,
                frontier=(selected_node_id,),
                referenced_objects=(
                    prepared.context_object,
                    prepared.request_object,
                    selection_object,
                    evidence_object,
                    admission_object,
                ),
            ).projection
            return CognitionAdmissionReceipt(
                task_id=prepared.request.task_id,
                revision=projection.revision,
                request_id=prepared.request.request_id,
                request_object_digest=prepared.request_object.digest,
                context_digest=prepared.context.digest,
                context_object_digest=prepared.context_object.digest,
                selection_object_digest=selection_object.digest,
                evidence_object_digest=evidence_object.digest,
                admission_object_digest=admission_object.digest,
                selected_action_id=admitted.action.action_id,
                selected_node_id=selected_node_id,
            )

    def _load_request_snapshot(
        self,
        task_id: str,
        snapshot,
    ) -> PreparedCognitionRequest:
        data = snapshot.data
        if not isinstance(data, dict):
            raise JournalCorruption("Cognition Work Request event data must be an object")
        request_digest = data.get("requestObjectDigest")
        if not isinstance(request_digest, str):
            raise JournalCorruption("Cognition Work Request object digest is invalid")
        raw_request = self.storage.objects.get(
            request_digest, expected_kind="cognition-work-request"
        )
        if not isinstance(raw_request, dict):
            raise ObjectCorrupt("Cognition Work Request object must be an envelope")
        try:
            request = CognitionWorkRequest.from_dict(raw_request)
        except ValueError as error:
            raise ObjectCorrupt("Cognition Work Request object is invalid") from error
        if (
            request.task_id != task_id
            or request.task_revision != snapshot.projection.revision
        ):
            raise JournalCorruption(
                "Cognition Work Request Task identity or revision differs"
            )
        raw_context = self.storage.objects.get(
            request.context_object_digest, expected_kind="compiled-context"
        )
        if not isinstance(raw_context, dict):
            raise ObjectCorrupt("Cognition Context must be an envelope")
        try:
            context = CompiledContext.from_dict(raw_context)
        except ValueError as error:
            raise ObjectCorrupt("Cognition Context is invalid") from error
        if context.digest != request.context_digest:
            raise JournalCorruption("Cognition Context digest differs from Work Request")
        if (
            data.get("requestId") != request.request_id
            or data.get("nodeId") != request.node_id
            or data.get("resultKind") != request.result_kind.value
            or data.get("contextDigest") != request.context_digest
            or data.get("contextObjectDigest") != request.context_object_digest
        ):
            raise JournalCorruption("Cognition Work Request event identities differ")
        return PreparedCognitionRequest(
            request=request,
            request_object=self.storage.objects.inspect(request_digest),
            context_object=self.storage.objects.inspect(request.context_object_digest),
            context=context,
        )

    def _require_ready_frontier(self, task_id: str, node_id: str) -> TaskProjection:
        return self.kernel.current_snapshot(
            task_id,
            expected_state=TaskState.READY,
            expected_frontier=(node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ).projection

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return CognitionRequestSuperseded(message)
        if category == "frontier":
            return CognitionError("Task is not at the requested cognition frontier")
        if category == "state":
            return CognitionError("Cognition requires the expected Task state")
        return JournalCorruption(message)

    @staticmethod
    def _event_id(task_id: str, stage: str, revision: int) -> str:
        token = task_id.removeprefix("task:")
        return f"event:{token}:cognition-{stage}:r{revision}"

    @staticmethod
    def _selected_node(task_id: str, action_id: str) -> str:
        token = task_id.removeprefix("task:")
        digest = hashlib.sha256(action_id.encode("utf-8")).hexdigest()[:16]
        return f"node:{token}:selected:{digest}"


__all__ = [
    "AdmissionState",
    "CognitionAdmissionReceipt",
    "CognitionError",
    "CognitionExecutionEvidence",
    "CognitionHost",
    "CognitionRequestSuperseded",
    "CognitionResultKind",
    "CognitionWorkRequest",
    "PreparedCognitionRequest",
]
