from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anc_effect_ir import CapabilityRequirement


OWNER_TRUSTED_PROFILE_ID = "profile:owner-trusted-local-v1"
PUBLIC_BOUNDED_PROFILE_ID = "profile:public-bounded-v1"


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


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    profile_id: str
    principal_id: str
    action_prefixes: tuple[str, ...]
    object_scope_prefixes: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        if not self.profile_id.startswith("profile:") or self.profile_id != self.profile_id.strip():
            raise ValueError("capability profile identity must start with profile:")
        if not self.principal_id.startswith("principal:") or self.principal_id != self.principal_id.strip():
            raise ValueError("capability profile principal must start with principal:")
        if not self.action_prefixes or any(not value or value != value.strip() for value in self.action_prefixes):
            raise ValueError("capability profile action prefixes are required")
        if not self.object_scope_prefixes or any(
            not value or value != value.strip() for value in self.object_scope_prefixes
        ):
            raise ValueError("capability profile object-scope prefixes are required")
        if not self.description or self.description != self.description.strip():
            raise ValueError("capability profile description is required")

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.capability-profile",
            "profileId": self.profile_id,
            "principalId": self.principal_id,
            "actionPrefixes": list(self.action_prefixes),
            "objectScopePrefixes": list(self.object_scope_prefixes),
            "description": self.description,
        }


class CapabilityProfileAuthorizer:
    """Resolve semantic capability against one explicit operating profile.

    Profiles constrain which principal and resource family can be used. They do not
    decide whether a proposed external consequence is acceptable; proposal lowering
    owns that separate decision before an Effect is compiled.
    """

    def __init__(self, profiles: tuple[CapabilityProfile, ...] | None = None) -> None:
        configured = profiles or default_capability_profiles()
        self._profiles = {profile.profile_id: profile for profile in configured}
        if len(self._profiles) != len(configured):
            raise ValueError("capability profile identities must be unique")

    def profile(self, profile_id: str) -> CapabilityProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as error:
            raise CapabilityDenied(f"unknown capability profile: {profile_id}") from error

    def authorize(
        self,
        requirement: CapabilityRequirement,
        *,
        profile_id: str,
    ) -> CapabilityDecision:
        profile = self.profile(profile_id)
        principal_matches = requirement.principal_id == profile.principal_id
        action_matches = any(
            requirement.action_id.startswith(prefix) for prefix in profile.action_prefixes
        )
        scope_matches = any(
            requirement.object_scope.startswith(prefix)
            for prefix in profile.object_scope_prefixes
        )
        allowed = principal_matches and action_matches and scope_matches
        if not principal_matches:
            reason = "capability principal differs from the selected profile"
        elif not action_matches:
            reason = "semantic action is outside the selected capability profile"
        elif not scope_matches:
            reason = "resource scope is outside the selected capability profile"
        else:
            reason = "capability is admitted by the selected explicit profile"
        decision = CapabilityDecision(
            principal_id=requirement.principal_id,
            action_id=requirement.action_id,
            object_scope=requirement.object_scope,
            policy_id=profile.profile_id,
            allowed=allowed,
            reason=reason,
        )
        if not allowed:
            raise CapabilityDenied(decision.reason)
        return decision

    def bind(self, profile_id: str) -> BoundCapabilityAuthorizer:
        self.profile(profile_id)
        return BoundCapabilityAuthorizer(self, profile_id)


@dataclass(frozen=True, slots=True)
class BoundCapabilityAuthorizer:
    profiles: CapabilityProfileAuthorizer
    profile_id: str

    def authorize(self, requirement: CapabilityRequirement) -> CapabilityDecision:
        return self.profiles.authorize(requirement, profile_id=self.profile_id)


class TrustedLocalAuthorizer:
    """Compatibility policy for the two previously proven repository workloads.

    New open-proposal workloads select an explicit CapabilityProfile instead of
    inheriting this historical default.
    """

    def __init__(
        self,
        *,
        principal_id: str = "principal:local-owner",
        policy_id: str = "policy:trusted-local-owner-v1",
    ) -> None:
        self.principal_id = principal_id
        self.policy_id = policy_id

    def authorize(self, requirement: CapabilityRequirement) -> CapabilityDecision:
        source_change = (
            requirement.action_id == "anc.source.change.v1"
            and requirement.object_scope.startswith("world_object:repository:")
        )
        repository_read = (
            requirement.action_id == "anc.object.read.v1"
            and requirement.object_scope.startswith(
                "world_object:repository-file:repository:"
            )
        )
        allowed = requirement.principal_id == self.principal_id and (
            source_change or repository_read
        )
        decision = CapabilityDecision(
            principal_id=requirement.principal_id,
            action_id=requirement.action_id,
            object_scope=requirement.object_scope,
            policy_id=self.policy_id,
            allowed=allowed,
            reason=(
                "legacy trusted-local repository workload is admitted"
                if allowed
                else "requirement is outside the legacy trusted-local repository policy"
            ),
        )
        if not allowed:
            raise CapabilityDenied(decision.reason)
        return decision


def default_capability_profiles() -> tuple[CapabilityProfile, ...]:
    return (
        CapabilityProfile(
            profile_id=OWNER_TRUSTED_PROFILE_ID,
            principal_id="principal:local-owner",
            action_prefixes=("anc.",),
            object_scope_prefixes=(
                "world_object:repository:",
                "world_object:repository-file:repository:",
            ),
            description=(
                "The local owner may use Agent semantic actions on private repository "
                "resources; consequence admission remains a separate Host decision."
            ),
        ),
        CapabilityProfile(
            profile_id=PUBLIC_BOUNDED_PROFILE_ID,
            principal_id="principal:local-owner",
            action_prefixes=("anc.object.read.",),
            object_scope_prefixes=(
                "world_object:repository-file:repository:",
            ),
            description="Public-bounded profile permits repository observation only.",
        ),
    )
