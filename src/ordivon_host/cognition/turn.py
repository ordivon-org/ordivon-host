from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib

from anc_canonical import JsonValue

from ..domain import EventKind, TaskProjection, TaskState
from ..journal import JournalCorruption
from ..kernel import HostKernel, worker_owner_id
from ..objects import ObjectCorrupt, StoredObject
from ..storage import HostStorage
from ..providers import (
    ModelInvocationIntent,
    ModelInvocationObservation,
    ModelInvocationReceipt,
)
from .context import CognitionRequest, CompiledContext, ContextCompiler
from .decision import DecisionAdmission, ModelDecision


class CognitionTurnError(RuntimeError):
    pass


class CognitionSuperseded(CognitionTurnError):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionState:
    world_digest: str
    completed_effect_ids: tuple[str, ...]
    unresolved_dispatch_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            len(self.world_digest) != 71
            or not self.world_digest.startswith("sha256:")
            or any(
                character not in "0123456789abcdef"
                for character in self.world_digest[7:]
            )
        ):
            raise ValueError("Admission world digest is invalid")
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
        if len(self.unresolved_dispatch_ids) != len(
            set(self.unresolved_dispatch_ids)
        ):
            raise ValueError("unresolved Dispatch identities must be unique")


@dataclass(frozen=True, slots=True)
class PreparedCognition:
    task_id: str
    task_revision: int
    decision_node_id: str
    context_object: StoredObject
    context: CompiledContext


@dataclass(frozen=True, slots=True)
class PreparedInvocation:
    prepared: PreparedCognition
    task_revision: int
    intent_object: StoredObject
    intent: ModelInvocationIntent


@dataclass(frozen=True, slots=True)
class CognitionTurnReceipt:
    task_id: str
    revision: int
    adapter_id: str
    invocation_id: str
    invocation_intent_digest: str
    invocation_observation_digest: str
    invocation_receipt_digest: str
    context_digest: str
    context_object_digest: str
    decision_object_digest: str
    admission_object_digest: str
    selected_action_id: str
    selected_node_id: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.cognition-turn-receipt",
            "taskId": self.task_id,
            "revision": self.revision,
            "adapterId": self.adapter_id,
            "invocationId": self.invocation_id,
            "invocationIntentDigest": self.invocation_intent_digest,
            "invocationObservationDigest": self.invocation_observation_digest,
            "invocationReceiptDigest": self.invocation_receipt_digest,
            "contextDigest": self.context_digest,
            "contextObjectDigest": self.context_object_digest,
            "decisionObjectDigest": self.decision_object_digest,
            "admissionObjectDigest": self.admission_object_digest,
            "selectedActionId": self.selected_action_id,
            "selectedNodeId": self.selected_node_id,
        }


class CognitionTurnHost:
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
            owner_id=owner_id or worker_owner_id("host:cognition-v1"),
            lease_ttl_ms=lease_ttl_ms,
        )
        self.compiler = ContextCompiler()
        self.admission = DecisionAdmission()

    def prepare(
        self,
        *,
        task_id: str,
        decision_node_id: str,
        request: CognitionRequest,
        token_budget: int,
    ) -> PreparedCognition:
        if request.task_id != task_id:
            raise ValueError("CognitionRequest belongs to another Task")
        context = self.compiler.compile(request, token_budget=token_budget)
        return self.prepare_compiled(
            task_id=task_id,
            decision_node_id=decision_node_id,
            context=context,
            token_budget=token_budget,
        )

    def prepare_compiled(
        self,
        *,
        task_id: str,
        decision_node_id: str,
        context: CompiledContext,
        token_budget: int,
    ) -> PreparedCognition:
        payload_task_id = context.payload.get("taskId")
        if payload_task_id != task_id:
            raise ValueError("CompiledContext belongs to another Task")
        current = self._require_decision_frontier(task_id, decision_node_id)
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is EventKind.COGNITION_CONTEXT_COMPILED:
            existing = self._load_prepared_snapshot(task_id, snapshot)
            if existing.decision_node_id != decision_node_id:
                raise CognitionTurnError("prepared Context targets another decision node")
            if existing.context.digest != context.digest:
                raise CognitionTurnError(
                    "Task already has another prepared Context at this frontier"
                )
            return existing

        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(decision_node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ) as locked:
            context_object = self.storage.put_object(
                context.to_dict(), kind="compiled-context"
            )
            projection = locked.commit(
                event_id=self._event_id(
                    task_id, "context", locked.projection.revision + 1
                ),
                kind=EventKind.COGNITION_CONTEXT_COMPILED,
                payload={
                    "decisionNodeId": decision_node_id,
                    "contextDigest": context.digest,
                    "contextObjectDigest": context_object.digest,
                    "tokenBudget": token_budget,
                },
                referenced_objects=(context_object,),
            ).projection
            return PreparedCognition(
                task_id=task_id,
                task_revision=projection.revision,
                decision_node_id=decision_node_id,
                context_object=context_object,
                context=context,
            )

    def load_prepared(self, task_id: str) -> PreparedCognition:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is not EventKind.COGNITION_CONTEXT_COMPILED:
            raise CognitionTurnError("Task head is not a prepared Cognition Context")
        return self._load_prepared_snapshot(task_id, snapshot)

    def prepare_invocation(
        self,
        prepared: PreparedCognition,
        *,
        gateway_id: str,
    ) -> PreparedInvocation:
        if not gateway_id or gateway_id != gateway_id.strip():
            raise ValueError("model gateway identity is required")
        snapshot = self.storage.read_task_event(prepared.task_id)
        if snapshot.event_kind is EventKind.COGNITION_INVOCATION_PREPARED:
            existing = self._load_invocation_snapshot(prepared.task_id, snapshot)
            if (
                existing.prepared.context.digest != prepared.context.digest
                or existing.intent.gateway_id != gateway_id
            ):
                raise CognitionSuperseded(
                    "another model invocation is already prepared at this frontier"
                )
            return existing
        with self.kernel.locked_task(
            prepared.task_id,
            expected_revision=prepared.task_revision,
            expected_state=TaskState.READY,
            expected_frontier=(prepared.decision_node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ) as locked:
            latest = self.load_prepared(prepared.task_id)
            if (
                latest.task_revision != prepared.task_revision
                or latest.context.digest != prepared.context.digest
                or latest.context_object.digest != prepared.context_object.digest
            ):
                raise CognitionSuperseded("prepared Context changed before invocation")
            invocation_id = (
                f"invocation:{prepared.task_id.removeprefix('task:')}:"
                f"context-r{prepared.task_revision}"
            )
            intent = ModelInvocationIntent(
                invocation_id=invocation_id,
                task_id=prepared.task_id,
                context_digest=prepared.context.digest,
                context_object_digest=prepared.context_object.digest,
                gateway_id=gateway_id,
            )
            intent_object = self.storage.put_object(
                intent.to_dict(), kind="model-invocation-intent"
            )
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.task_id,
                    "invocation",
                    locked.projection.revision + 1,
                ),
                kind=EventKind.COGNITION_INVOCATION_PREPARED,
                payload={
                    "decisionNodeId": prepared.decision_node_id,
                    "contextDigest": prepared.context.digest,
                    "contextObjectDigest": prepared.context_object.digest,
                    "invocationId": invocation_id,
                    "gatewayId": gateway_id,
                    "intentObjectDigest": intent_object.digest,
                },
                state=TaskState.WAITING,
                frontier=(prepared.decision_node_id,),
                referenced_objects=(prepared.context_object, intent_object),
            ).projection
        return PreparedInvocation(
            prepared=prepared,
            task_revision=projection.revision,
            intent_object=intent_object,
            intent=intent,
        )

    def load_invocation(self, task_id: str) -> PreparedInvocation:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is not EventKind.COGNITION_INVOCATION_PREPARED:
            raise CognitionTurnError("Task head is not a prepared model invocation")
        return self._load_invocation_snapshot(task_id, snapshot)

    def admit_decision(
        self,
        invocation: PreparedInvocation,
        decision: ModelDecision,
        *,
        evidence: dict[str, JsonValue] | None = None,
        state_reader: Callable[[], AdmissionState],
    ) -> CognitionTurnReceipt:
        """Admit externally executed cognition against a durable invocation intent.

        Host deliberately does not invoke a Provider here. The caller must persist the
        invocation with ``prepare_invocation`` before external execution, then return the
        resulting semantic decision and non-secret evidence for Host admission.
        """
        return self._admit_invocation(
            invocation,
            decision,
            evidence={**(evidence or {}), "externalDecision": True},
            state_reader=state_reader,
        )

    def _admit_invocation(
        self,
        invocation: PreparedInvocation,
        decision: ModelDecision,
        *,
        evidence: dict[str, JsonValue],
        state_reader: Callable[[], AdmissionState],
    ) -> CognitionTurnReceipt:
        prepared = invocation.prepared
        with self.kernel.locked_task(
            prepared.task_id,
            expected_revision=invocation.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.decision_node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ) as locked:
            latest = self._load_invocation_snapshot(prepared.task_id, locked.snapshot)
            if latest.intent != invocation.intent:
                raise CognitionSuperseded("model invocation changed before admission")
            state = state_reader()
            admitted = self.admission.admit(
                prepared.context,
                decision,
                current_world_digest=state.world_digest,
                completed_effect_ids=state.completed_effect_ids,
                unresolved_dispatch_ids=state.unresolved_dispatch_ids,
            )
            decision_object = self.storage.put_object(
                decision.to_dict(), kind="model-decision"
            )
            observation = ModelInvocationObservation(
                invocation_id=invocation.intent.invocation_id,
                gateway_id=invocation.intent.gateway_id,
                decision_object_digest=decision_object.digest,
                evidence=evidence,
            )
            observation_object = self.storage.put_object(
                observation.to_dict(), kind="model-invocation-observation"
            )
            admission_object = self.storage.put_object(
                admitted.to_dict(), kind="admitted-decision"
            )
            invocation_receipt = ModelInvocationReceipt(
                invocation_id=invocation.intent.invocation_id,
                intent_object_digest=invocation.intent_object.digest,
                observation_object_digest=observation_object.digest,
                admission_object_digest=admission_object.digest,
            )
            invocation_receipt_object = self.storage.put_object(
                invocation_receipt.to_dict(), kind="model-invocation-receipt"
            )
            selected_node_id = self._selected_node(
                prepared.task_id, admitted.action.action_id
            )
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.task_id,
                    "decision",
                    locked.projection.revision + 1,
                ),
                kind=EventKind.COGNITION_DECISION_ADMITTED,
                payload={
                    "decisionNodeId": prepared.decision_node_id,
                    "selectedNodeId": selected_node_id,
                    "selectedActionId": admitted.action.action_id,
                    "adapterId": invocation.intent.gateway_id,
                    "invocationId": invocation.intent.invocation_id,
                    "intentObjectDigest": invocation.intent_object.digest,
                    "observationObjectDigest": observation_object.digest,
                    "invocationReceiptDigest": invocation_receipt_object.digest,
                    "contextDigest": prepared.context.digest,
                    "contextObjectDigest": prepared.context_object.digest,
                    "decisionObjectDigest": decision_object.digest,
                    "admissionObjectDigest": admission_object.digest,
                },
                state=TaskState.READY,
                frontier=(selected_node_id,),
                referenced_objects=(
                    prepared.context_object,
                    invocation.intent_object,
                    decision_object,
                    observation_object,
                    admission_object,
                    invocation_receipt_object,
                ),
            ).projection
            return CognitionTurnReceipt(
                task_id=prepared.task_id,
                revision=projection.revision,
                adapter_id=invocation.intent.gateway_id,
                invocation_id=invocation.intent.invocation_id,
                invocation_intent_digest=invocation.intent_object.digest,
                invocation_observation_digest=observation_object.digest,
                invocation_receipt_digest=invocation_receipt_object.digest,
                context_digest=prepared.context.digest,
                context_object_digest=prepared.context_object.digest,
                decision_object_digest=decision_object.digest,
                admission_object_digest=admission_object.digest,
                selected_action_id=admitted.action.action_id,
                selected_node_id=selected_node_id,
            )

    def _load_invocation_snapshot(
        self,
        task_id: str,
        snapshot,
    ) -> PreparedInvocation:
        data = snapshot.data
        if not isinstance(data, dict):
            raise JournalCorruption("model invocation event data must be an object")
        object_digest = data.get("intentObjectDigest")
        if not isinstance(object_digest, str):
            raise JournalCorruption("model invocation intent digest is invalid")
        value = self.storage.objects.get(
            object_digest, expected_kind="model-invocation-intent"
        )
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "kind",
            "invocationId",
            "taskId",
            "contextDigest",
            "contextObjectDigest",
            "gatewayId",
        }:
            raise ObjectCorrupt("model invocation intent fields differ")
        try:
            intent = ModelInvocationIntent(
                invocation_id=str(value["invocationId"]),
                task_id=str(value["taskId"]),
                context_digest=str(value["contextDigest"]),
                context_object_digest=str(value["contextObjectDigest"]),
                gateway_id=str(value["gatewayId"]),
            )
        except ValueError as error:
            raise ObjectCorrupt("model invocation intent is invalid") from error
        context_value = self.storage.objects.get(
            intent.context_object_digest, expected_kind="compiled-context"
        )
        if not isinstance(context_value, dict):
            raise ObjectCorrupt("model invocation Context must be an envelope")
        try:
            context = CompiledContext.from_dict(context_value)
        except ValueError as error:
            raise ObjectCorrupt("model invocation Context is invalid") from error
        decision_node_id = data.get("decisionNodeId")
        if (
            intent.task_id != task_id
            or context.digest != intent.context_digest
            or not isinstance(decision_node_id, str)
        ):
            raise JournalCorruption("model invocation identities differ")
        prepared = PreparedCognition(
            task_id=task_id,
            task_revision=snapshot.projection.revision - 1,
            decision_node_id=decision_node_id,
            context_object=self.storage.objects.inspect(intent.context_object_digest),
            context=context,
        )
        return PreparedInvocation(
            prepared=prepared,
            task_revision=snapshot.projection.revision,
            intent_object=self.storage.objects.inspect(object_digest),
            intent=intent,
        )

    def _load_prepared_snapshot(
        self,
        task_id: str,
        snapshot,
    ) -> PreparedCognition:
        data = snapshot.data
        if not isinstance(data, dict):
            raise JournalCorruption("prepared Cognition event data must be an object")
        decision_node_id = data.get("decisionNodeId")
        context_digest = data.get("contextDigest")
        object_digest = data.get("contextObjectDigest")
        if not all(
            isinstance(value, str)
            for value in (decision_node_id, context_digest, object_digest)
        ):
            raise JournalCorruption("prepared Cognition identities are invalid")
        value = self.storage.objects.get(
            object_digest,
            expected_kind="compiled-context",
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("CompiledContext object must be an envelope")
        try:
            context = CompiledContext.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("CompiledContext object is invalid") from error
        if context.digest != context_digest:
            raise JournalCorruption("prepared Context digest differs from event head")
        object_ref = self.storage.objects.inspect(object_digest)
        return PreparedCognition(
            task_id=task_id,
            task_revision=snapshot.projection.revision,
            decision_node_id=decision_node_id,
            context_object=object_ref,
            context=context,
        )

    def _require_decision_frontier(
        self,
        task_id: str,
        decision_node_id: str,
    ) -> TaskProjection:
        return self.kernel.current_snapshot(
            task_id,
            expected_state=TaskState.READY,
            expected_frontier=(decision_node_id,),
            label="Cognition",
            error_factory=self._kernel_error,
        ).projection

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return CognitionSuperseded(message)
        if category == "frontier":
            return CognitionTurnError("Task is not at the requested decision frontier")
        if category == "state":
            return CognitionTurnError("Cognition requires a ready Task")
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
