from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from anc_canonical import JsonValue

from ..authority import CapabilityProfileAuthorizer
from ..domain import EventKind, RepositoryResolver, StaticRepositoryResolver, TaskProjection, TaskState
from ..engine.read_task import DeterministicReadHost
from ..journal import JournalCorruption
from ..kernel import worker_owner_id
from ..objects import ObjectCorrupt
from ..providers import (
    ModelInvocationOutputObservation,
    ModelInvocationReceipt,
)
from ..storage import HostStorage
from ..runtime import RuntimeClient
from .context import CompiledContext
from .proposal import (
    ActionProposal,
    DecisionRequest,
    LoweredReadProposal,
    OpenCognitionRequest,
    OpenContextCompiler,
    ProposalRejection,
    ProposalResolutionKind,
    RepositoryReadProposalCompiler,
)
from .turn import CognitionSuperseded, CognitionTurnHost, PreparedCognition, PreparedInvocation


class ProposalGateway(Protocol):
    gateway_id: str

    def invoke(self, context: CompiledContext) -> ActionProposal: ...

    def evidence_metadata(self) -> dict[str, JsonValue] | None: ...


class ScriptedProposalGateway:
    gateway_id = "scripted-open-proposal-v1"

    def __init__(self, builder: Callable[[CompiledContext], ActionProposal]) -> None:
        self.builder = builder

    def invoke(self, context: CompiledContext) -> ActionProposal:
        return self.builder(context)

    def evidence_metadata(self) -> dict[str, JsonValue]:
        return {"gateway": "scripted-open-proposal", "physicalProviderCall": False}


@dataclass(frozen=True, slots=True)
class OpenProposalReceipt:
    task_id: str
    revision: int
    proposal_digest: str
    proposal_object_digest: str
    resolution_kind: ProposalResolutionKind
    resolution_object_digest: str
    invocation_id: str
    invocation_receipt_digest: str
    output_observation_digest: str
    child_task_id: str | None
    decision_request_id: str | None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.open-proposal-receipt",
            "taskId": self.task_id,
            "revision": self.revision,
            "proposalDigest": self.proposal_digest,
            "proposalObjectDigest": self.proposal_object_digest,
            "resolutionKind": self.resolution_kind.value,
            "resolutionObjectDigest": self.resolution_object_digest,
            "invocationId": self.invocation_id,
            "invocationReceiptDigest": self.invocation_receipt_digest,
            "outputObservationDigest": self.output_observation_digest,
            "childTaskId": self.child_task_id,
            "decisionRequestId": self.decision_request_id,
        }


class OpenProposalHost:
    """Persist one open proposal and lower only the proven repository-read slice."""

    def __init__(
        self,
        storage: HostStorage,
        runtime: RuntimeClient,
        *,
        clock_ms: Callable[[], int],
        repository_resolver: RepositoryResolver | None = None,
        profiles: CapabilityProfileAuthorizer | None = None,
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        self.storage = storage
        self.runtime = runtime
        self.clock_ms = clock_ms
        self.repository_resolver = repository_resolver or StaticRepositoryResolver({})
        self.profiles = profiles or CapabilityProfileAuthorizer()
        self.cognition = CognitionTurnHost(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:open-proposal-v1"),
            lease_ttl_ms=lease_ttl_ms,
        )
        self.context_compiler = OpenContextCompiler()
        self.proposal_compiler = RepositoryReadProposalCompiler(self.profiles)

    def create_task(
        self,
        *,
        task_id: str,
        goal_id: str,
        proposal_node_id: str,
    ) -> TaskProjection:
        existing = self.storage.journal.get_task(task_id)
        if existing is not None:
            if existing.goal_id != goal_id:
                raise ValueError("open proposal Task identity is bound to another Goal")
            return existing
        return self.cognition.kernel.create_task(
            event_id=self._event_id(task_id, "create", 1),
            kind=EventKind.TASK_CREATED,
            task_id=task_id,
            goal_id=goal_id,
            payload={"cognitionProfile": "open-proposal-v1"},
            frontier=(proposal_node_id,),
        ).projection

    def prepare(
        self,
        *,
        task_id: str,
        proposal_node_id: str,
        request: OpenCognitionRequest,
        token_budget: int,
    ) -> PreparedCognition:
        if request.task_id != task_id:
            raise ValueError("OpenCognitionRequest belongs to another Task")
        context = self.context_compiler.compile(request, token_budget=token_budget)
        return self.cognition.prepare_compiled(
            task_id=task_id,
            decision_node_id=proposal_node_id,
            context=context,
            token_budget=token_budget,
        )

    def propose(
        self,
        prepared: PreparedCognition,
        gateway: ProposalGateway,
    ) -> OpenProposalReceipt:
        gateway_id = getattr(gateway, "gateway_id", None)
        if not isinstance(gateway_id, str) or not gateway_id:
            raise ValueError("proposal gateway identity is required")
        invocation = self.cognition.prepare_invocation(prepared, gateway_id=gateway_id)
        proposal = gateway.invoke(prepared.context)
        evidence = gateway.evidence_metadata() or {}
        if not isinstance(evidence, dict):
            raise ValueError("proposal gateway evidence must be an object")
        return self.admit_proposal(invocation, proposal, evidence=evidence)

    def admit_proposal(
        self,
        invocation: PreparedInvocation,
        proposal: ActionProposal,
        *,
        evidence: dict[str, JsonValue] | None = None,
    ) -> OpenProposalReceipt:
        existing = self._existing_receipt(invocation.prepared.task_id, proposal)
        if existing is not None:
            return existing
        prepared = invocation.prepared
        with self.cognition.kernel.locked_task(
            prepared.task_id,
            expected_revision=invocation.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.decision_node_id,),
            label="Open proposal",
            error_factory=self._kernel_error,
        ) as locked:
            latest = self.cognition.load_invocation(prepared.task_id)
            if latest.intent != invocation.intent:
                raise CognitionSuperseded("model invocation changed before proposal admission")
            token = proposal.digest[7:23]
            parent_token = prepared.task_id.removeprefix("task:")
            child_task_id = f"task:{parent_token}:read-{token}"
            workspace_id = f"host-proposal-read-{token}"
            resolution = self.proposal_compiler.compile(
                prepared.context,
                proposal,
                goal_id=locked.projection.goal_id,
                child_task_id=child_task_id,
                workspace_id=workspace_id,
            )
            proposal_object = self.storage.put_object(
                proposal.to_dict(), kind="action-proposal"
            )
            output_observation = ModelInvocationOutputObservation(
                invocation_id=invocation.intent.invocation_id,
                gateway_id=invocation.intent.gateway_id,
                output_kind="action-proposal",
                output_object_digest=proposal_object.digest,
                evidence=evidence or {},
            )
            output_object = self.storage.put_object(
                output_observation.to_dict(),
                kind="model-invocation-output-observation",
            )
            if isinstance(resolution, LoweredReadProposal):
                resolution_kind = ProposalResolutionKind.LOWERED
                resolution_object = self.storage.put_object(
                    resolution.to_dict(), kind="lowered-proposal"
                )
                read_host = DeterministicReadHost(
                    self.storage,
                    self.runtime,
                    clock_ms=self.clock_ms,
                    repository_resolver=self.repository_resolver,
                    authorizer=self.profiles.bind(resolution.capability_profile_id),
                    owner_id=worker_owner_id("host:open-proposal-read-v1"),
                )
                read_host.create(resolution.plan)
                next_state = TaskState.WAITING
                next_frontier = (f"node:{parent_token}:await:{token}",)
                retained_child_id: str | None = resolution.plan.task_id
                decision_request_id: str | None = None
            elif isinstance(resolution, DecisionRequest):
                resolution_kind = ProposalResolutionKind.DECISION_REQUEST
                resolution_object = self.storage.put_object(
                    resolution.to_dict(), kind="decision-request"
                )
                next_state = TaskState.BLOCKED
                next_frontier = (f"node:{parent_token}:decision-request:{token}",)
                retained_child_id = None
                decision_request_id = resolution.request_id
            elif isinstance(resolution, ProposalRejection):
                resolution_kind = ProposalResolutionKind.REJECTED
                resolution_object = self.storage.put_object(
                    resolution.to_dict(), kind="proposal-rejection"
                )
                next_state = TaskState.READY
                next_frontier = (prepared.decision_node_id,)
                retained_child_id = None
                decision_request_id = None
            else:
                raise TypeError("unsupported proposal resolution")
            invocation_receipt = ModelInvocationReceipt(
                invocation_id=invocation.intent.invocation_id,
                intent_object_digest=invocation.intent_object.digest,
                observation_object_digest=output_object.digest,
                admission_object_digest=resolution_object.digest,
            )
            invocation_receipt_object = self.storage.put_object(
                invocation_receipt.to_dict(), kind="model-invocation-receipt"
            )
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.task_id,
                    "proposal",
                    locked.projection.revision + 1,
                ),
                kind=EventKind.COGNITION_PROPOSAL_RESOLVED,
                payload={
                    "proposalDigest": proposal.digest,
                    "proposalObjectDigest": proposal_object.digest,
                    "resolutionKind": resolution_kind.value,
                    "resolutionObjectDigest": resolution_object.digest,
                    "invocationId": invocation.intent.invocation_id,
                    "gatewayId": invocation.intent.gateway_id,
                    "intentObjectDigest": invocation.intent_object.digest,
                    "outputObservationDigest": output_object.digest,
                    "invocationReceiptDigest": invocation_receipt_object.digest,
                    "childTaskId": retained_child_id,
                    "decisionRequestId": decision_request_id,
                },
                state=next_state,
                frontier=next_frontier,
                referenced_objects=(
                    prepared.context_object,
                    invocation.intent_object,
                    proposal_object,
                    output_object,
                    resolution_object,
                    invocation_receipt_object,
                ),
            ).projection
            return OpenProposalReceipt(
                task_id=prepared.task_id,
                revision=projection.revision,
                proposal_digest=proposal.digest,
                proposal_object_digest=proposal_object.digest,
                resolution_kind=resolution_kind,
                resolution_object_digest=resolution_object.digest,
                invocation_id=invocation.intent.invocation_id,
                invocation_receipt_digest=invocation_receipt_object.digest,
                output_observation_digest=output_object.digest,
                child_task_id=retained_child_id,
                decision_request_id=decision_request_id,
            )

    def reconcile(self, task_id: str) -> TaskProjection:
        current = self.storage.journal.get_task(task_id)
        if current is None:
            raise KeyError(f"unknown open proposal Task: {task_id}")
        if current.state.terminal:
            return current
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is not EventKind.COGNITION_PROPOSAL_RESOLVED:
            return current
        data = snapshot.data
        if not isinstance(data, dict) or data.get("resolutionKind") != "lowered":
            return current
        child_task_id = data.get("childTaskId")
        if not isinstance(child_task_id, str):
            raise JournalCorruption("lowered proposal omitted child Task identity")
        child = self.storage.journal.get_task(child_task_id)
        if child is None:
            raise JournalCorruption("lowered proposal child Task is missing")
        if not child.state.terminal:
            return current
        with self.cognition.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.WAITING,
            expected_frontier=current.ready_frontier,
            label="Open proposal reconciliation",
            error_factory=self._kernel_error,
        ) as locked:
            child_snapshot = self.storage.read_task_event(child_task_id)
            child_outcome = None
            child_outcome_object = None
            if isinstance(child_snapshot.data, dict):
                value = child_snapshot.data.get("outcomeDigest")
                if isinstance(value, str):
                    child_outcome = value
                    child_outcome_object = self.storage.objects.inspect(value)
            outcome = self.storage.put_object(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.open-proposal-outcome",
                    "taskId": task_id,
                    "childTaskId": child_task_id,
                    "childTaskState": child.state.value,
                    "childTaskRevision": child.revision,
                    "childOutcomeDigest": child_outcome,
                },
                kind="task-outcome",
            )
            terminal = {
                TaskState.COMPLETED: TaskState.COMPLETED,
                TaskState.FAILED: TaskState.FAILED,
                TaskState.CANCELLED: TaskState.CANCELLED,
            }[child.state]
            return locked.commit(
                event_id=self._event_id(
                    task_id,
                    "reconcile",
                    locked.projection.revision + 1,
                ),
                kind=EventKind.TASK_STATE_CHANGED,
                payload={
                    "childTaskId": child_task_id,
                    "childTaskRevision": child.revision,
                    "outcomeDigest": outcome.digest,
                },
                state=terminal,
                frontier=(),
                referenced_objects=(
                    (outcome,)
                    if child_outcome_object is None
                    else (child_outcome_object, outcome)
                ),
            ).projection

    def _existing_receipt(
        self,
        task_id: str,
        proposal: ActionProposal,
    ) -> OpenProposalReceipt | None:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind is not EventKind.COGNITION_PROPOSAL_RESOLVED:
            return None
        data = snapshot.data
        if not isinstance(data, dict):
            raise ObjectCorrupt("proposal resolution event data must be an object")
        if data.get("proposalDigest") != proposal.digest:
            raise CognitionSuperseded("Task already resolved another ActionProposal")
        required = (
            "proposalObjectDigest",
            "resolutionKind",
            "resolutionObjectDigest",
            "invocationId",
            "invocationReceiptDigest",
            "outputObservationDigest",
        )
        if any(not isinstance(data.get(field), str) for field in required):
            raise ObjectCorrupt("proposal resolution receipt fields are invalid")
        child_task_id = data.get("childTaskId")
        decision_request_id = data.get("decisionRequestId")
        if child_task_id is not None and not isinstance(child_task_id, str):
            raise ObjectCorrupt("proposal child Task identity is invalid")
        if decision_request_id is not None and not isinstance(decision_request_id, str):
            raise ObjectCorrupt("DecisionRequest identity is invalid")
        return OpenProposalReceipt(
            task_id=task_id,
            revision=snapshot.projection.revision,
            proposal_digest=proposal.digest,
            proposal_object_digest=data["proposalObjectDigest"],
            resolution_kind=ProposalResolutionKind(data["resolutionKind"]),
            resolution_object_digest=data["resolutionObjectDigest"],
            invocation_id=data["invocationId"],
            invocation_receipt_digest=data["invocationReceiptDigest"],
            output_observation_digest=data["outputObservationDigest"],
            child_task_id=child_task_id,
            decision_request_id=decision_request_id,
        )

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return CognitionSuperseded(message)
        return JournalCorruption(message)

    @staticmethod
    def _event_id(task_id: str, stage: str, revision: int) -> str:
        token = task_id.removeprefix("task:")
        return f"event:{token}:open-{stage}:r{revision}"
