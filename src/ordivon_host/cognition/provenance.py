from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from .context import BlockKind, ContextBlock, Freshness


class TrustClass(StrEnum):
    AUTHORITATIVE = "authoritative"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    ADVERSARIAL = "adversarial"


class ClaimStatus(StrEnum):
    FACT = "fact"
    CLAIM = "claim"
    INSTRUCTION = "instruction"
    OBSERVATION = "observation"


class SelectionMethod(StrEnum):
    DIRECT = "direct"
    RETRIEVAL = "retrieval"
    SUMMARY = "summary"
    ARTIFACT_JOIN = "artifact-join"


def _identity(value: str, prefix: str) -> None:
    if not value.startswith(prefix + ":") or value != value.strip():
        raise ValueError(f"identity must start with {prefix}:")
    if len(value.encode("utf-8")) > 300:
        raise ValueError("identity exceeds 300 UTF-8 bytes")


@dataclass(frozen=True, slots=True)
class ContextSourceBinding:
    source_ref: str
    source_revision: str
    payload_digest: str
    observed_at_ms: int
    trust_class: TrustClass
    claim_status: ClaimStatus
    selection_method: SelectionMethod
    invalidation_keys: tuple[str, ...]
    selected_by: str
    material_omissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.source_ref, "source")
        _identity(self.selected_by, "selector")
        if not self.source_revision or self.source_revision != self.source_revision.strip():
            raise ValueError("source revision is required")
        if (
            len(self.payload_digest) != 71
            or not self.payload_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in self.payload_digest[7:])
        ):
            raise ValueError("payload digest must be sha256:<64 lowercase hex>")
        if self.observed_at_ms < 0:
            raise ValueError("source observation time must be non-negative")
        if not self.invalidation_keys or any(
            not key or key != key.strip() for key in self.invalidation_keys
        ):
            raise ValueError("at least one invalidation key is required")
        if len(self.invalidation_keys) != len(set(self.invalidation_keys)):
            raise ValueError("invalidation keys must be unique")
        if len(self.material_omissions) != len(set(self.material_omissions)):
            raise ValueError("material omissions must be unique")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.context-source-binding",
            "sourceRef": self.source_ref,
            "sourceRevision": self.source_revision,
            "payloadDigest": self.payload_digest,
            "observedAtMs": self.observed_at_ms,
            "trustClass": self.trust_class.value,
            "claimStatus": self.claim_status.value,
            "selectionMethod": self.selection_method.value,
            "invalidationKeys": list(self.invalidation_keys),
            "selectedBy": self.selected_by,
            "materialOmissions": list(self.material_omissions),
        }


@dataclass(frozen=True, slots=True)
class ContextInvalidationState:
    revisions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.revisions]
        if len(keys) != len(set(keys)):
            raise ValueError("invalidation revision keys must be unique")
        if any(not key or not revision for key, revision in self.revisions):
            raise ValueError("invalidation revisions must be non-empty")

    @classmethod
    def from_mapping(cls, revisions: Mapping[str, str]) -> ContextInvalidationState:
        return cls(tuple(sorted((str(key), str(value)) for key, value in revisions.items())))

    def value(self, key: str) -> str | None:
        return dict(self.revisions).get(key)


@dataclass(frozen=True, slots=True)
class SourceValidity:
    source_ref: str
    valid: bool
    reasons: tuple[str, ...]


def evaluate_source(
    binding: ContextSourceBinding,
    *,
    bound_revisions: Mapping[str, str],
    current_revisions: Mapping[str, str],
) -> SourceValidity:
    reasons: list[str] = []
    for key in binding.invalidation_keys:
        expected = bound_revisions.get(key)
        current = current_revisions.get(key)
        if expected is None:
            reasons.append(f"missing-bound-revision:{key}")
        elif current is None:
            reasons.append(f"missing-current-revision:{key}")
        elif expected != current:
            reasons.append(f"revision-changed:{key}")
    return SourceValidity(binding.source_ref, not reasons, tuple(reasons))


def provenance_block(
    *,
    block_id: str,
    kind: BlockKind,
    priority: int,
    required: bool,
    freshness: Freshness,
    payload: JsonValue,
    binding: ContextSourceBinding,
) -> ContextBlock:
    validate_json_value(payload)
    payload_digest = canonical_digest(payload)
    if binding.payload_digest != payload_digest:
        raise ValueError("Context source payload differs from its binding")
    wrapped: JsonValue = {
        "sourceBinding": binding.to_dict(),
        "content": payload,
    }
    return ContextBlock(
        block_id=block_id,
        kind=kind,
        priority=priority,
        required=required,
        freshness=freshness,
        source_digest=binding.digest,
        payload=wrapped,
    )
