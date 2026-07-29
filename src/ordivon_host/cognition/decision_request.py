from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from anc_canonical import JsonValue, canonical_digest


def _identity(value: str, prefix: str) -> None:
    if not value.startswith(prefix + ":") or value != value.strip():
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


class DecisionResponseKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_ref: str
    digest: str
    summary: str

    def __post_init__(self) -> None:
        _identity(self.evidence_ref, "evidence")
        _digest(self.digest)
        if not self.summary or self.summary != self.summary.strip():
            raise ValueError("evidence summary is required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"evidenceRef": self.evidence_ref, "digest": self.digest, "summary": self.summary}


@dataclass(frozen=True, slots=True)
class EvidenceRichDecisionRequest:
    request_id: str
    task_id: str
    proposal_digest: str
    recipient_ref: str
    reason_code: str
    summary: str
    alternatives: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...]
    unresolved_claims: tuple[str, ...]
    consequence_class: str
    reversibility: str
    authority_impact: str
    budget_impact: str
    cost_of_delay: str
    world_revision: str
    expires_at_ms: int | None
    allowed_responses: tuple[DecisionResponseKind, ...] = (
        DecisionResponseKind.APPROVE,
        DecisionResponseKind.REJECT,
        DecisionResponseKind.MODIFY,
    )

    def __post_init__(self) -> None:
        _identity(self.request_id, "decision-request")
        _identity(self.task_id, "task")
        _digest(self.proposal_digest)
        if ":" not in self.recipient_ref or self.recipient_ref != self.recipient_ref.strip():
            raise ValueError("DecisionRequest recipient must be a typed identity")
        for value, label in (
            (self.reason_code, "reason code"),
            (self.summary, "summary"),
            (self.consequence_class, "consequence class"),
            (self.reversibility, "reversibility"),
            (self.authority_impact, "authority impact"),
            (self.budget_impact, "budget impact"),
            (self.cost_of_delay, "cost of delay"),
            (self.world_revision, "world revision"),
        ):
            if not value or value != value.strip():
                raise ValueError(f"DecisionRequest {label} is required")
        if not self.alternatives or len(self.alternatives) != len(set(self.alternatives)):
            raise ValueError("DecisionRequest alternatives must be non-empty and unique")
        if not self.evidence:
            raise ValueError("evidence-rich DecisionRequest requires evidence")
        if len(self.unresolved_claims) != len(set(self.unresolved_claims)):
            raise ValueError("unresolved Claims must be unique")
        if self.expires_at_ms is not None and self.expires_at_ms < 0:
            raise ValueError("DecisionRequest expiry must be non-negative")
        if not self.allowed_responses or len(self.allowed_responses) != len(set(self.allowed_responses)):
            raise ValueError("DecisionRequest responses must be non-empty and unique")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 2,
            "kind": "ordivon.evidence-rich-decision-request",
            "requestId": self.request_id,
            "taskId": self.task_id,
            "proposalDigest": self.proposal_digest,
            "recipientRef": self.recipient_ref,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "alternatives": list(self.alternatives),
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolvedClaims": list(self.unresolved_claims),
            "consequenceClass": self.consequence_class,
            "reversibility": self.reversibility,
            "authorityImpact": self.authority_impact,
            "budgetImpact": self.budget_impact,
            "costOfDelay": self.cost_of_delay,
            "worldRevision": self.world_revision,
            "expiresAtMs": self.expires_at_ms,
            "allowedResponses": [item.value for item in self.allowed_responses],
        }


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    response_id: str
    request_id: str
    request_digest: str
    responder_ref: str
    response: DecisionResponseKind
    world_revision: str
    recorded_at_ms: int
    rationale: str
    replacement_proposal_digest: str | None = None

    def __post_init__(self) -> None:
        _identity(self.response_id, "decision-response")
        _identity(self.request_id, "decision-request")
        _digest(self.request_digest)
        if ":" not in self.responder_ref or self.responder_ref != self.responder_ref.strip():
            raise ValueError("DecisionResponse responder must be typed")
        if not self.world_revision or not self.rationale:
            raise ValueError("DecisionResponse revision and rationale are required")
        if self.recorded_at_ms < 0:
            raise ValueError("DecisionResponse time must be non-negative")
        if self.replacement_proposal_digest is not None:
            _digest(self.replacement_proposal_digest)
        if self.response is DecisionResponseKind.MODIFY and self.replacement_proposal_digest is None:
            raise ValueError("modify response requires a replacement Proposal digest")
        if self.response is not DecisionResponseKind.MODIFY and self.replacement_proposal_digest is not None:
            raise ValueError("only modify response may carry a replacement Proposal")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.decision-response",
            "responseId": self.response_id,
            "requestId": self.request_id,
            "requestDigest": self.request_digest,
            "responderRef": self.responder_ref,
            "response": self.response.value,
            "worldRevision": self.world_revision,
            "recordedAtMs": self.recorded_at_ms,
            "rationale": self.rationale,
            "replacementProposalDigest": self.replacement_proposal_digest,
        }


@dataclass(frozen=True, slots=True)
class DecisionRequestLifecycle:
    request: EvidenceRichDecisionRequest
    revision: int = 1
    response: DecisionResponse | None = None
    revoked_at_ms: int | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("DecisionRequest lifecycle revision must be positive")
        if (self.revoked_at_ms is None) != (self.revocation_reason is None):
            raise ValueError("DecisionRequest revocation time and reason must appear together")
        if self.revoked_at_ms is not None and self.revoked_at_ms < 0:
            raise ValueError("DecisionRequest revocation time must be non-negative")
        if self.response is not None:
            if self.response.request_id != self.request.request_id:
                raise ValueError("DecisionResponse targets another request")
            if self.response.request_digest != self.request.digest:
                raise ValueError("DecisionResponse targets another request revision")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def respond(
        self,
        response: DecisionResponse,
        *,
        current_world_revision: str,
        now_ms: int,
    ) -> DecisionRequestLifecycle:
        if self.response is not None:
            if self.response == response:
                return self
            raise ValueError("DecisionRequest already has another response")
        if self.revoked_at_ms is not None:
            raise ValueError("DecisionRequest is revoked")
        if self.request.expires_at_ms is not None and now_ms > self.request.expires_at_ms:
            raise ValueError("DecisionRequest is expired")
        if self.request.world_revision != current_world_revision:
            raise ValueError("DecisionRequest world revision is stale")
        if response.world_revision != current_world_revision:
            raise ValueError("DecisionResponse world revision is stale")
        if response.responder_ref != self.request.recipient_ref:
            raise ValueError("DecisionResponse came from another participant")
        if response.response not in self.request.allowed_responses:
            raise ValueError("DecisionResponse kind is not allowed")
        return replace(self, revision=self.revision + 1, response=response)

    def revoke(self, *, now_ms: int, reason: str) -> DecisionRequestLifecycle:
        if self.response is not None:
            raise ValueError("responded DecisionRequest cannot be revoked retroactively")
        if self.revoked_at_ms is not None:
            if self.revoked_at_ms == now_ms and self.revocation_reason == reason:
                return self
            raise ValueError("DecisionRequest already has another revocation")
        if now_ms < 0 or not reason or reason != reason.strip():
            raise ValueError("DecisionRequest revocation is invalid")
        return replace(
            self,
            revision=self.revision + 1,
            revoked_at_ms=now_ms,
            revocation_reason=reason,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.decision-request-lifecycle",
            "revision": self.revision,
            "request": self.request.to_dict(),
            "response": None if self.response is None else self.response.to_dict(),
            "revokedAtMs": self.revoked_at_ms,
            "revocationReason": self.revocation_reason,
        }
