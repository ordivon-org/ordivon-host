from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

from anc_canonical import JsonValue, canonical_digest, validate_json_value
from anc_effect_ir import CapabilityRequirement

from ..authority import CapabilityDecision, CapabilityDenied, CapabilityProfileAuthorizer
from ..engine.read_task import ReadTaskPlan
from .context import CompiledContext, ContextBlock, ContextManifest, estimate_tokens


class ProposalIntent(StrEnum):
    OBSERVE = "observe"
    CHANGE = "change"


class Reversibility(StrEnum):
    REVERSIBLE = "reversible"
    CONDITIONALLY_REVERSIBLE = "conditionally-reversible"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class ConsequenceClass(StrEnum):
    PRIVATE_REVERSIBLE = "private-reversible"
    SHARED_REVERSIBLE = "shared-reversible"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class ProposalResolutionKind(StrEnum):
    LOWERED = "lowered"
    DECISION_REQUEST = "decision-request"
    REJECTED = "rejected"


def _identity(value: str, prefix: str | None = None) -> None:
    if not value or value != value.strip() or ":" not in value:
        raise ValueError("identity must be non-empty, typed, and trimmed")
    if prefix is not None and not value.startswith(prefix + ":"):
        raise ValueError(f"identity must start with {prefix}:")
    if len(value.encode("utf-8")) > 300:
        raise ValueError("identity exceeds 300 UTF-8 bytes")


def _digest(value: str) -> None:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("digest must be sha256:<64 lowercase hex>")


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    resource_ref: str
    revision: str
    owner_ref: str

    def __post_init__(self) -> None:
        _identity(self.resource_ref)
        if not self.revision or self.revision != self.revision.strip():
            raise ValueError("resource revision is required")
        _identity(self.owner_ref)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "resourceRef": self.resource_ref,
            "revision": self.revision,
            "ownerRef": self.owner_ref,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResourceBinding:
        if set(value) != {"resourceRef", "revision", "ownerRef"} or any(
            not isinstance(value[field], str)
            for field in ("resourceRef", "revision", "ownerRef")
        ):
            raise ValueError("ResourceBinding fields are invalid")
        return cls(value["resourceRef"], value["revision"], value["ownerRef"])


@dataclass(frozen=True, slots=True)
class OpenContextRequest:
    task_id: str
    world_digest: str
    blocks: tuple[ContextBlock, ...]
    capability_profile_id: str
    responsible_participant_ref: str
    resources: tuple[ResourceBinding, ...]
    forbidden_effect_ids: tuple[str, ...] = ()
    unresolved_dispatch_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.task_id, "task")
        _digest(self.world_digest)
        _identity(self.capability_profile_id, "profile")
        _identity(self.responsible_participant_ref)
        if not self.resources:
            raise ValueError("open cognition requires at least one resource binding")
        resource_refs = [resource.resource_ref for resource in self.resources]
        if len(resource_refs) != len(set(resource_refs)):
            raise ValueError("open cognition resource identities must be unique")
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("ContextBlock identities must be unique")
        for effect_id in self.forbidden_effect_ids:
            _identity(effect_id, "effect")
        for dispatch_id in self.unresolved_dispatch_ids:
            _identity(dispatch_id, "dispatch")
        if len(self.forbidden_effect_ids) != len(set(self.forbidden_effect_ids)):
            raise ValueError("forbidden Effect identities must be unique")
        if len(self.unresolved_dispatch_ids) != len(set(self.unresolved_dispatch_ids)):
            raise ValueError("unresolved Dispatch identities must be unique")


class OpenContextCompiler:
    """Compile resources and constraints without enumerating the next action."""

    def compile(
        self,
        request: OpenContextRequest,
        *,
        token_budget: int,
    ) -> CompiledContext:
        if token_budget < 1:
            raise ValueError("Context token budget must be positive")
        required = sorted(
            (block for block in request.blocks if block.required),
            key=lambda block: block.block_id,
        )
        optional = sorted(
            (block for block in request.blocks if not block.required),
            key=lambda block: (-block.priority, block.estimated_tokens, block.block_id),
        )
        selected = list(required)
        if estimate_tokens(self._payload(request, selected)) > token_budget:
            raise ValueError("required open Context blocks exceed the token budget")
        omitted: list[ContextBlock] = []
        for block in optional:
            candidate = [*selected, block]
            if estimate_tokens(self._payload(request, candidate)) <= token_budget:
                selected.append(block)
            else:
                omitted.append(block)
        payload = self._payload(request, selected)
        manifest = ContextManifest(
            token_budget=token_budget,
            estimated_tokens=estimate_tokens(payload),
            selected_block_ids=tuple(block.block_id for block in selected),
            omitted_block_ids=tuple(block.block_id for block in omitted),
        )
        return CompiledContext(payload=payload, manifest=manifest)

    @staticmethod
    def _payload(
        request: OpenContextRequest,
        selected: list[ContextBlock],
    ) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.open-compiled-context",
            "taskId": request.task_id,
            "worldDigest": request.world_digest,
            "blocks": [block.to_dict() for block in selected],
            "capabilityProfileId": request.capability_profile_id,
            "responsibleParticipantRef": request.responsible_participant_ref,
            "availableResources": [resource.to_dict() for resource in request.resources],
            "forbiddenEffects": list(request.forbidden_effect_ids),
            "unresolvedDispatches": list(request.unresolved_dispatch_ids),
            "proposalContract": {
                "kind": "ordivon.action-proposal-v1",
                "modelSuppliesExecutionIdentities": False,
                "hostCompilesEffectBindingDispatch": True,
            },
            "instruction": (
                "Propose one useful next action from the Goal, current evidence, and available "
                "resources. Do not invent Effect, Binding, Dispatch, Tool request, authority, or "
                "completion identities. State target, intent, expected result, consequence, "
                "reversibility, and verification plan; the Host will compile or reject it."
            ),
        }


@dataclass(frozen=True, slots=True)
class ProposalTarget:
    kind: str
    resource_ref: str
    revision: str
    selector: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.kind or self.kind != self.kind.strip():
            raise ValueError("proposal target kind is required")
        _identity(self.resource_ref)
        if not self.revision or self.revision != self.revision.strip():
            raise ValueError("proposal target revision is required")
        validate_json_value(self.selector)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "resourceRef": self.resource_ref,
            "revision": self.revision,
            "selector": self.selector,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProposalTarget:
        if set(value) != {"kind", "resourceRef", "revision", "selector"}:
            raise ValueError("ProposalTarget fields differ")
        if any(not isinstance(value[field], str) for field in ("kind", "resourceRef", "revision")):
            raise ValueError("ProposalTarget identities must be strings")
        selector = value["selector"]
        if not isinstance(selector, dict):
            raise ValueError("ProposalTarget selector must be an object")
        validate_json_value(selector)
        return cls(value["kind"], value["resourceRef"], value["revision"], dict(selector))


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: str
    task_id: str
    context_digest: str
    intent: ProposalIntent
    target: ProposalTarget
    rationale: str
    preconditions: tuple[str, ...]
    affected_resource_refs: tuple[str, ...]
    affected_participant_refs: tuple[str, ...]
    reversibility: Reversibility
    consequence_class: ConsequenceClass
    requested_profile_id: str
    candidate_method: str | None
    expected_result: str
    verification_plan: str

    def __post_init__(self) -> None:
        _identity(self.proposal_id, "proposal")
        _identity(self.task_id, "task")
        _digest(self.context_digest)
        _identity(self.requested_profile_id, "profile")
        if not self.rationale or self.rationale != self.rationale.strip():
            raise ValueError("proposal rationale is required")
        if not self.expected_result or self.expected_result != self.expected_result.strip():
            raise ValueError("proposal expected result is required")
        if not self.verification_plan or self.verification_plan != self.verification_plan.strip():
            raise ValueError("proposal verification plan is required")
        if self.candidate_method is not None and (
            not self.candidate_method or self.candidate_method != self.candidate_method.strip()
        ):
            raise ValueError("candidate method must be null or non-empty")
        if any(not value or value != value.strip() for value in self.preconditions):
            raise ValueError("proposal preconditions must be non-empty strings")
        for value in (*self.affected_resource_refs, *self.affected_participant_refs):
            _identity(value)
        if len(self.affected_resource_refs) != len(set(self.affected_resource_refs)):
            raise ValueError("affected resource identities must be unique")
        if len(self.affected_participant_refs) != len(set(self.affected_participant_refs)):
            raise ValueError("affected participant identities must be unique")
        if self.target.resource_ref not in self.affected_resource_refs:
            raise ValueError("proposal target must be listed as an affected resource")
        if (
            self.consequence_class is ConsequenceClass.PRIVATE_REVERSIBLE
            and self.reversibility is not Reversibility.REVERSIBLE
        ):
            raise ValueError("private-reversible proposal must declare reversible")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.action-proposal",
            "proposalId": self.proposal_id,
            "taskId": self.task_id,
            "contextDigest": self.context_digest,
            "intent": self.intent.value,
            "target": self.target.to_dict(),
            "rationale": self.rationale,
            "preconditions": list(self.preconditions),
            "affectedResourceRefs": list(self.affected_resource_refs),
            "affectedParticipantRefs": list(self.affected_participant_refs),
            "reversibility": self.reversibility.value,
            "consequenceClass": self.consequence_class.value,
            "requestedProfileId": self.requested_profile_id,
            "candidateMethod": self.candidate_method,
            "expectedResult": self.expected_result,
            "verificationPlan": self.verification_plan,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActionProposal:
        expected = {
            "schemaVersion",
            "kind",
            "proposalId",
            "taskId",
            "contextDigest",
            "intent",
            "target",
            "rationale",
            "preconditions",
            "affectedResourceRefs",
            "affectedParticipantRefs",
            "reversibility",
            "consequenceClass",
            "requestedProfileId",
            "candidateMethod",
            "expectedResult",
            "verificationPlan",
        }
        if set(value) != expected or value.get("schemaVersion") != 1 or value.get("kind") != "ordivon.action-proposal":
            raise ValueError("ActionProposal fields, version, or kind differ")
        string_fields = (
            "proposalId",
            "taskId",
            "contextDigest",
            "intent",
            "rationale",
            "reversibility",
            "consequenceClass",
            "requestedProfileId",
            "expectedResult",
            "verificationPlan",
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("ActionProposal required fields must be strings")
        target = value["target"]
        preconditions = value["preconditions"]
        resources = value["affectedResourceRefs"]
        participants = value["affectedParticipantRefs"]
        method = value["candidateMethod"]
        if not isinstance(target, dict):
            raise ValueError("ActionProposal target must be an object")
        for collection in (preconditions, resources, participants):
            if not isinstance(collection, list) or any(not isinstance(item, str) for item in collection):
                raise ValueError("ActionProposal collections must be lists of strings")
        if method is not None and not isinstance(method, str):
            raise ValueError("ActionProposal candidate method must be null or string")
        return cls(
            proposal_id=value["proposalId"],
            task_id=value["taskId"],
            context_digest=value["contextDigest"],
            intent=ProposalIntent(value["intent"]),
            target=ProposalTarget.from_dict(target),
            rationale=value["rationale"],
            preconditions=tuple(preconditions),
            affected_resource_refs=tuple(resources),
            affected_participant_refs=tuple(participants),
            reversibility=Reversibility(value["reversibility"]),
            consequence_class=ConsequenceClass(value["consequenceClass"]),
            requested_profile_id=value["requestedProfileId"],
            candidate_method=method,
            expected_result=value["expectedResult"],
            verification_plan=value["verificationPlan"],
        )


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    request_id: str
    task_id: str
    proposal_digest: str
    recipient_ref: str
    reason_code: str
    summary: str
    allowed_responses: tuple[str, ...] = ("approve", "reject", "modify")

    def __post_init__(self) -> None:
        _identity(self.request_id, "decision-request")
        _identity(self.task_id, "task")
        _digest(self.proposal_digest)
        _identity(self.recipient_ref)
        if not self.reason_code or self.reason_code != self.reason_code.strip():
            raise ValueError("DecisionRequest reason code is required")
        if not self.summary or self.summary != self.summary.strip():
            raise ValueError("DecisionRequest summary is required")
        if not self.allowed_responses or any(
            not response or response != response.strip() for response in self.allowed_responses
        ):
            raise ValueError("DecisionRequest responses are required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.decision-request",
            "requestId": self.request_id,
            "taskId": self.task_id,
            "proposalDigest": self.proposal_digest,
            "recipientRef": self.recipient_ref,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "allowedResponses": list(self.allowed_responses),
        }


@dataclass(frozen=True, slots=True)
class ProposalRejection:
    proposal_digest: str
    code: str
    message: str

    def __post_init__(self) -> None:
        _digest(self.proposal_digest)
        if not self.code or self.code != self.code.strip():
            raise ValueError("proposal rejection code is required")
        if not self.message or self.message != self.message.strip():
            raise ValueError("proposal rejection message is required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.proposal-rejection",
            "proposalDigest": self.proposal_digest,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class LoweredReadProposal:
    proposal_digest: str
    capability_profile_id: str
    capability_decision: CapabilityDecision
    plan: ReadTaskPlan

    def __post_init__(self) -> None:
        _digest(self.proposal_digest)
        _identity(self.capability_profile_id, "profile")
        if not self.capability_decision.allowed:
            raise ValueError("lowered proposal requires an allowed CapabilityDecision")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.lowered-read-proposal",
            "proposalDigest": self.proposal_digest,
            "capabilityProfileId": self.capability_profile_id,
            "capabilityDecision": self.capability_decision.to_dict(),
            "readTaskPlan": self.plan.to_dict(),
        }


ProposalResolution: TypeAlias = LoweredReadProposal | DecisionRequest | ProposalRejection


class RepositoryReadProposalCompiler:
    """Lower one open repository observation proposal into the proven read slice."""

    def __init__(self, profiles: CapabilityProfileAuthorizer) -> None:
        self.profiles = profiles

    def compile(
        self,
        context: CompiledContext,
        proposal: ActionProposal,
        *,
        goal_id: str,
        child_task_id: str,
        workspace_id: str,
    ) -> ProposalResolution:
        if proposal.context_digest != context.digest:
            return self._reject(proposal, "wrong_context", "proposal targets another Context")
        payload = context.payload
        if payload.get("kind") != "ordivon.open-compiled-context":
            return self._reject(proposal, "closed_context", "Context is not open-proposal mode")
        if proposal.task_id != payload.get("taskId"):
            return self._reject(proposal, "wrong_task", "proposal targets another Task")
        profile_id = payload.get("capabilityProfileId")
        responsible = payload.get("responsibleParticipantRef")
        if not isinstance(profile_id, str) or not isinstance(responsible, str):
            return self._reject(proposal, "invalid_context", "Context responsibility metadata is invalid")
        if proposal.requested_profile_id != profile_id:
            return self._reject(proposal, "wrong_profile", "proposal requests another capability profile")
        resources = payload.get("availableResources")
        if not isinstance(resources, list):
            return self._reject(proposal, "invalid_context", "Context resource bindings are invalid")
        bindings: dict[str, ResourceBinding] = {}
        try:
            for raw in resources:
                if not isinstance(raw, dict):
                    raise ValueError("resource binding must be an object")
                binding = ResourceBinding.from_dict(raw)
                bindings[binding.resource_ref] = binding
        except ValueError:
            return self._reject(proposal, "invalid_context", "Context resource binding is invalid")
        binding = bindings.get(proposal.target.resource_ref)
        if binding is None:
            return self._reject(proposal, "unknown_resource", "proposal targets an unavailable resource")
        if binding.revision != proposal.target.revision:
            return self._reject(proposal, "stale_resource", "proposal resource revision is stale")
        if proposal.intent is not ProposalIntent.OBSERVE or proposal.target.kind != "repository-file":
            return self._reject(proposal, "unsupported_intent", "first open slice supports repository observation only")
        if proposal.candidate_method not in {None, "workspace.read"}:
            return self._reject(proposal, "unsupported_method", "candidate method cannot lower to workspace.read")
        foreign_participants = [
            participant
            for participant in proposal.affected_participant_refs
            if participant != responsible
        ]
        if (
            proposal.consequence_class is not ConsequenceClass.PRIVATE_REVERSIBLE
            or proposal.reversibility is not Reversibility.REVERSIBLE
            or binding.owner_ref != responsible
            or foreign_participants
        ):
            return DecisionRequest(
                request_id=f"decision-request:{proposal.digest[7:23]}",
                task_id=proposal.task_id,
                proposal_digest=proposal.digest,
                recipient_ref=binding.owner_ref,
                reason_code="responsible-participant-commitment-required",
                summary=(
                    "The proposal affects shared, foreign-owned, or non-reversible resources "
                    "and requires an explicit commitment from the responsible participant."
                ),
            )
        selector = proposal.target.selector
        if set(selector) - {"relativePath", "maxBytes"}:
            return self._reject(proposal, "unsupported_selector", "repository selector fields are unsupported")
        relative_path = selector.get("relativePath")
        max_bytes = selector.get("maxBytes", 4_194_304)
        if not isinstance(relative_path, str) or type(max_bytes) is not int:
            return self._reject(proposal, "invalid_selector", "repository path or byte bound is invalid")
        try:
            profile = self.profiles.profile(profile_id)
            object_scope = (
                f"world_object:repository-file:{binding.resource_ref}/{relative_path}"
            )
            decision = self.profiles.authorize(
                CapabilityRequirement(
                    profile.principal_id,
                    "anc.object.read.v1",
                    object_scope,
                ),
                profile_id=profile_id,
            )
            plan = ReadTaskPlan(
                task_id=child_task_id,
                goal_id=goal_id,
                workspace_id=workspace_id,
                repository=_repository_ref(binding),
                relative_path=relative_path,
                max_bytes=max_bytes,
                principal_id=profile.principal_id,
            )
        except (CapabilityDenied, ValueError) as error:
            return self._reject(proposal, "capability_denied", str(error))
        return LoweredReadProposal(
            proposal_digest=proposal.digest,
            capability_profile_id=profile_id,
            capability_decision=decision,
            plan=plan,
        )

    @staticmethod
    def _reject(proposal: ActionProposal, code: str, message: str) -> ProposalRejection:
        return ProposalRejection(proposal.digest, code, message)


def _repository_ref(binding: ResourceBinding):
    from ..domain import RepositoryRef

    return RepositoryRef(binding.resource_ref, binding.revision)
