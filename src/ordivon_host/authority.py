from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anc_effect_ir import CapabilityRequirement


class CapabilityDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    principal_id: str
    action_id: str
    object_scope: str
    policy_id: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.capability-decision",
            "principalId": self.principal_id,
            "actionId": self.action_id,
            "objectScope": self.object_scope,
            "policyId": self.policy_id,
            "allowed": self.allowed,
            "reason": self.reason,
        }


class CapabilityAuthorizer(Protocol):
    def authorize(self, requirement: CapabilityRequirement) -> CapabilityDecision: ...


class TrustedLocalAuthorizer:
    """Explicit trusted-local policy; broad by design, but never implicit."""

    def __init__(
        self,
        *,
        principal_id: str = "principal:local-owner",
        policy_id: str = "policy:trusted-local-owner-v1",
    ) -> None:
        self.principal_id = principal_id
        self.policy_id = policy_id

    def authorize(self, requirement: CapabilityRequirement) -> CapabilityDecision:
        allowed = (
            requirement.principal_id == self.principal_id
            and requirement.action_id == "anc.source.change.v1"
            and requirement.object_scope.startswith("world_object:repository:")
        )
        decision = CapabilityDecision(
            principal_id=requirement.principal_id,
            action_id=requirement.action_id,
            object_scope=requirement.object_scope,
            policy_id=self.policy_id,
            allowed=allowed,
            reason=(
                "trusted local owner may change a repository"
                if allowed
                else "requirement is outside trusted-local source-change policy"
            ),
        )
        if not allowed:
            raise CapabilityDenied(decision.reason)
        return decision
