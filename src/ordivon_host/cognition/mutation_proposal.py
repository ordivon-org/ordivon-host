from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from anc_canonical import JsonValue
from anc_effect_ir import CapabilityRequirement

from ..authority import CapabilityDecision, CapabilityDenied, CapabilityProfileAuthorizer
from ..engine import GuardedMutationPlan
from .context import CompiledContext
from .proposal import (
    ActionProposal,
    ConsequenceClass,
    DecisionRequest,
    ProposalIntent,
    ProposalRejection,
    ResourceBinding,
    Reversibility,
)


@dataclass(frozen=True, slots=True)
class LoweredMutationProposal:
    proposal_digest: str
    capability_profile_id: str
    capability_decision: CapabilityDecision
    plan: GuardedMutationPlan

    def __post_init__(self) -> None:
        if (
            len(self.proposal_digest) != 71
            or not self.proposal_digest.startswith("sha256:")
        ):
            raise ValueError("lowered mutation proposal digest is invalid")
        if not self.capability_profile_id.startswith("profile:"):
            raise ValueError("lowered mutation capability profile is invalid")
        if not self.capability_decision.allowed:
            raise ValueError("lowered mutation requires an admitted capability")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.lowered-mutation-proposal",
            "proposalDigest": self.proposal_digest,
            "capabilityProfileId": self.capability_profile_id,
            "capabilityDecision": self.capability_decision.to_dict(),
            "mutationTaskPlan": self.plan.to_dict(),
        }


MutationProposalResolution: TypeAlias = (
    LoweredMutationProposal | DecisionRequest | ProposalRejection
)


class RepositoryMutationProposalCompiler:
    """Lower one bounded repository-file change into the proven mutation Host.

    This compiler is intentionally not the default OpenProposalHost lowerer. It is
    a Host-local Round 1 workload adapter used to test open proposal, consequence
    admission, version binding, UNKNOWN recovery, and fresh-process continuation.
    """

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
        source_repo: str,
    ) -> MutationProposalResolution:
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
        if proposal.intent is not ProposalIntent.CHANGE or proposal.target.kind != "repository-file":
            return self._reject(proposal, "unsupported_intent", "mutation slice supports repository-file change only")
        if proposal.candidate_method not in {None, "guarded-mutation", "workspace.exec"}:
            return self._reject(proposal, "unsupported_method", "candidate method cannot lower to guarded mutation")
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
                    "The proposed source change affects shared, foreign-owned, or "
                    "non-reversible resources and requires an explicit commitment from "
                    "the responsible participant."
                ),
            )
        selector = proposal.target.selector
        if set(selector) != {"relativePath", "content"}:
            return self._reject(
                proposal,
                "unsupported_selector",
                "mutation selector requires exactly relativePath and content",
            )
        relative_path = selector.get("relativePath")
        content = selector.get("content")
        if not isinstance(relative_path, str) or not isinstance(content, str):
            return self._reject(proposal, "invalid_selector", "mutation path and content must be strings")
        source = Path(source_repo)
        if not source.is_absolute():
            return self._reject(proposal, "invalid_source_repo", "mutation source repository must be absolute")
        try:
            profile = self.profiles.profile(profile_id)
            decision = self.profiles.authorize(
                CapabilityRequirement(
                    profile.principal_id,
                    "anc.source.change.v1",
                    f"world_object:repository:{binding.resource_ref}",
                ),
                profile_id=profile_id,
            )
            plan = GuardedMutationPlan(
                task_id=child_task_id,
                goal_id=goal_id,
                workspace_id=workspace_id,
                source_repo=str(source),
                source_revision=binding.revision,
                relative_path=relative_path,
                content=content,
                principal_id=profile.principal_id,
            )
        except (CapabilityDenied, ValueError) as error:
            return self._reject(proposal, "capability_denied", str(error))
        return LoweredMutationProposal(
            proposal_digest=proposal.digest,
            capability_profile_id=profile_id,
            capability_decision=decision,
            plan=plan,
        )

    @staticmethod
    def _reject(
        proposal: ActionProposal,
        code: str,
        message: str,
    ) -> ProposalRejection:
        return ProposalRejection(proposal.digest, code, message)
