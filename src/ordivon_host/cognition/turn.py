from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib

from anc_canonical import JsonValue

from ..domain import EventKind, TaskProjection, TaskState
from ..journal import JournalCorruption
from ..kernel import HostKernel
from ..objects import ObjectCorrupt, StoredObject
from ..storage import HostStorage
from .adapters import ModelAdapter
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
class CognitionTurnReceipt:
    task_id: str
    revision: int
    adapter_id: str
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
        owner_id: str = "host:cognition-v0",
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if not owner_id or lease_ttl_ms < 1:
            raise ValueError("Cognition Host owner and lease TTL are required")
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id,
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
        current = self._require_decision_frontier(task_id, decision_node_id)
        context = self.compiler.compile(request, token_budget=token_budget)
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

    def decide(
        self,
        prepared: PreparedCognition,
        adapter: ModelAdapter,
        *,
        state_reader: Callable[[], AdmissionState],
    ) -> CognitionTurnReceipt:
        decision = adapter.decide(prepared.context)
        return self.admit_decision(
            prepared,
            decision,
            adapter_id=adapter.adapter_id,
            state_reader=state_reader,
        )

    def admit_decision(
        self,
        prepared: PreparedCognition,
        decision: ModelDecision,
        *,
        adapter_id: str,
        state_reader: Callable[[], AdmissionState],
    ) -> CognitionTurnReceipt:
        if not adapter_id or adapter_id != adapter_id.strip():
            raise ValueError("Cognition adapter identity is required")
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
                raise CognitionSuperseded("prepared Context was replaced before admission")
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
            admission_object = self.storage.put_object(
                admitted.to_dict(), kind="admitted-decision"
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
                    "adapterId": adapter_id,
                    "contextDigest": prepared.context.digest,
                    "contextObjectDigest": prepared.context_object.digest,
                    "decisionObjectDigest": decision_object.digest,
                    "admissionObjectDigest": admission_object.digest,
                },
                frontier=(selected_node_id,),
                referenced_objects=(
                    prepared.context_object,
                    decision_object,
                    admission_object,
                ),
            ).projection
            return CognitionTurnReceipt(
                task_id=prepared.task_id,
                revision=projection.revision,
                adapter_id=adapter_id,
                context_digest=prepared.context.digest,
                context_object_digest=prepared.context_object.digest,
                decision_object_digest=decision_object.digest,
                admission_object_digest=admission_object.digest,
                selected_action_id=admitted.action.action_id,
                selected_node_id=selected_node_id,
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
