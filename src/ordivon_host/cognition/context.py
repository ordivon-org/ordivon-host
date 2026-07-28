from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import ceil
from typing import Any

from ..objects.codecs import decode_versioned_object

from anc_canonical import (
    JsonValue,
    canonical_bytes,
    canonical_digest,
    validate_json_value,
)


class ContextCompileError(RuntimeError):
    pass


class BlockKind(StrEnum):
    GOAL = "goal"
    TASK = "task"
    WORLD = "world"
    DECISION = "decision"
    EVIDENCE = "evidence"
    DISPATCH = "dispatch"
    CONSTRAINT = "constraint"
    READY_ACTION = "ready-action"


class Freshness(StrEnum):
    CURRENT = "current"
    CHECKPOINT = "checkpoint"
    HISTORICAL = "historical"


def _validate_digest(value: str) -> None:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("digest must be sha256:<64 lowercase hex>")


def _validate_identity(value: str, prefix: str) -> None:
    if (
        not value.startswith(prefix + ":")
        or value != value.strip()
        or len(value.encode("utf-8")) > 300
    ):
        raise ValueError(f"identity must start with {prefix}: and be bounded")


def estimate_tokens(value: JsonValue) -> int:
    return max(1, ceil(len(canonical_bytes(value)) / 4))


@dataclass(frozen=True, slots=True)
class ContextBlock:
    block_id: str
    kind: BlockKind
    priority: int
    required: bool
    freshness: Freshness
    source_digest: str
    payload: JsonValue

    def __post_init__(self) -> None:
        _validate_identity(self.block_id, "context-block")
        if not 0 <= self.priority <= 100:
            raise ValueError("ContextBlock priority must be in [0, 100]")
        _validate_digest(self.source_digest)
        validate_json_value(self.payload)

    @property
    def payload_digest(self) -> str:
        return canonical_digest(self.payload)

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.payload)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "blockId": self.block_id,
            "kind": self.kind.value,
            "priority": self.priority,
            "required": self.required,
            "freshness": self.freshness.value,
            "sourceDigest": self.source_digest,
            "payloadDigest": self.payload_digest,
            "estimatedTokens": self.estimated_tokens,
            "payload": self.payload,
        }


class DecisionKind(StrEnum):
    INSPECT_WORLD = "inspect-world"
    PROPOSE_EFFECT = "propose-effect"
    OBSERVE_DISPATCH = "observe-dispatch"
    VERIFY_RESULT = "verify-result"
    REQUEST_HUMAN = "request-human"
    WAIT = "wait"
    FINISH_CANDIDATE = "finish-candidate"


@dataclass(frozen=True, slots=True)
class CandidateAction:
    action_id: str
    kind: DecisionKind
    summary: str
    effect_id: str | None = None
    binding_id: str | None = None
    dispatch_id: str | None = None
    required_world_digest: str | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.action_id, "action")
        if self.effect_id is not None:
            _validate_identity(self.effect_id, "effect")
        if self.binding_id is not None:
            _validate_identity(self.binding_id, "binding")
        if self.dispatch_id is not None:
            _validate_identity(self.dispatch_id, "dispatch")
        if not self.summary or self.summary != self.summary.strip():
            raise ValueError("Candidate action summary is required")
        if self.required_world_digest is not None:
            _validate_digest(self.required_world_digest)
        if self.kind is DecisionKind.PROPOSE_EFFECT:
            if self.effect_id is None or self.binding_id is None or self.dispatch_id is not None:
                raise ValueError("propose-effect requires Effect and Binding only")
        elif self.kind is DecisionKind.OBSERVE_DISPATCH:
            if self.dispatch_id is None or self.effect_id is not None or self.binding_id is not None:
                raise ValueError("observe-dispatch requires Dispatch only")
        elif any(
            value is not None
            for value in (self.effect_id, self.binding_id, self.dispatch_id)
        ):
            raise ValueError(f"{self.kind.value} cannot carry execution identities")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "actionId": self.action_id,
            "kind": self.kind.value,
            "summary": self.summary,
            "effectId": self.effect_id,
            "bindingId": self.binding_id,
            "dispatchId": self.dispatch_id,
            "requiredWorldDigest": self.required_world_digest,
        }


@dataclass(frozen=True, slots=True)
class CognitionRequest:
    task_id: str
    world_digest: str
    blocks: tuple[ContextBlock, ...]
    candidates: tuple[CandidateAction, ...]
    forbidden_effect_ids: tuple[str, ...] = ()
    unresolved_dispatch_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.task_id, "task")
        _validate_digest(self.world_digest)
        for effect_id in self.forbidden_effect_ids:
            _validate_identity(effect_id, "effect")
        for dispatch_id in self.unresolved_dispatch_ids:
            _validate_identity(dispatch_id, "dispatch")
        if not 2 <= len(self.candidates) <= 8:
            raise ValueError("Cognition requires between 2 and 8 candidate actions")
        block_ids = [block.block_id for block in self.blocks]
        action_ids = [action.action_id for action in self.candidates]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("ContextBlock identities must be unique")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Candidate action identities must be unique")
        if len(self.forbidden_effect_ids) != len(set(self.forbidden_effect_ids)):
            raise ValueError("forbidden Effect identities must be unique")
        if len(self.unresolved_dispatch_ids) != len(set(self.unresolved_dispatch_ids)):
            raise ValueError("unresolved Dispatch identities must be unique")
        for action in self.candidates:
            if action.effect_id is not None and action.effect_id in self.forbidden_effect_ids:
                raise ValueError("a forbidden Effect cannot be an allowed candidate")
            if (
                action.required_world_digest is not None
                and action.required_world_digest != self.world_digest
            ):
                raise ValueError("candidate world requirement is already stale")


@dataclass(frozen=True, slots=True)
class ContextManifest:
    token_budget: int
    estimated_tokens: int
    selected_block_ids: tuple[str, ...]
    omitted_block_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tokenBudget": self.token_budget,
            "estimatedTokens": self.estimated_tokens,
            "selectedBlockIds": list(self.selected_block_ids),
            "omittedBlockIds": list(self.omitted_block_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ContextManifest:
        expected = {
            "tokenBudget",
            "estimatedTokens",
            "selectedBlockIds",
            "omittedBlockIds",
        }
        if set(value) != expected:
            raise ValueError("ContextManifest fields differ")
        token_budget = value["tokenBudget"]
        estimated_tokens = value["estimatedTokens"]
        selected = value["selectedBlockIds"]
        omitted = value["omittedBlockIds"]
        if type(token_budget) is not int or type(estimated_tokens) is not int:
            raise ValueError("ContextManifest token counts must be integers")
        if token_budget < 1 or estimated_tokens < 1 or estimated_tokens > token_budget:
            raise ValueError("ContextManifest token counts are invalid")
        if not isinstance(selected, list) or any(
            not isinstance(item, str) for item in selected
        ):
            raise ValueError("ContextManifest selected blocks must be strings")
        if not isinstance(omitted, list) or any(
            not isinstance(item, str) for item in omitted
        ):
            raise ValueError("ContextManifest omitted blocks must be strings")
        if len(selected) != len(set(selected)) or len(omitted) != len(set(omitted)):
            raise ValueError("ContextManifest block identities must be unique")
        if set(selected) & set(omitted):
            raise ValueError("ContextManifest selected and omitted blocks overlap")
        return cls(
            token_budget=token_budget,
            estimated_tokens=estimated_tokens,
            selected_block_ids=tuple(selected),
            omitted_block_ids=tuple(omitted),
        )


@dataclass(frozen=True, slots=True)
class CompiledContext:
    payload: dict[str, JsonValue]
    manifest: ContextManifest

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload)

    @property
    def byte_length(self) -> int:
        return len(canonical_bytes(self.payload))

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.payload)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.compiled-context-envelope",
            "digest": self.digest,
            "byteLength": self.byte_length,
            "manifest": self.manifest.to_dict(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CompiledContext:
        return decode_versioned_object(
            value,
            expected_kind="ordivon.compiled-context-envelope",
            decoders={1: cls._from_dict_v1},
            label="CompiledContext",
        )

    @classmethod
    def _from_dict_v1(cls, value: dict[str, object]) -> CompiledContext:
        expected = {
            "schemaVersion",
            "kind",
            "digest",
            "byteLength",
            "manifest",
            "payload",
        }
        if set(value) != expected:
            raise ValueError("CompiledContext envelope fields differ")
        if (
            value["schemaVersion"] != 1
            or value["kind"] != "ordivon.compiled-context-envelope"
        ):
            raise ValueError("CompiledContext envelope version or kind is invalid")
        digest = value["digest"]
        byte_length = value["byteLength"]
        raw_manifest = value["manifest"]
        raw_payload = value["payload"]
        if not isinstance(digest, str) or type(byte_length) is not int:
            raise ValueError("CompiledContext digest and byte length are invalid")
        if not isinstance(raw_manifest, dict) or not isinstance(raw_payload, dict):
            raise ValueError("CompiledContext manifest and payload must be objects")
        validate_json_value(raw_payload)
        manifest = ContextManifest.from_dict(raw_manifest)
        context = cls(payload=dict(raw_payload), manifest=manifest)
        if context.digest != digest or context.byte_length != byte_length:
            raise ValueError("CompiledContext digest or byte length differs")
        if context.estimated_tokens != manifest.estimated_tokens:
            raise ValueError("CompiledContext token estimate differs from manifest")
        blocks = context.payload.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("CompiledContext blocks must be a list")
        selected: list[str] = []
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("blockId"), str):
                raise ValueError("CompiledContext block identity is invalid")
            selected.append(block["blockId"])
        if tuple(selected) != manifest.selected_block_ids:
            raise ValueError("CompiledContext blocks differ from manifest")
        return context


class ContextCompiler:
    def compile(self, request: CognitionRequest, *, token_budget: int) -> CompiledContext:
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
        required_payload = self._payload(request, selected)
        if estimate_tokens(required_payload) > token_budget:
            raise ContextCompileError("required Context blocks exceed the token budget")
        omitted: list[ContextBlock] = []
        for block in optional:
            candidate = [*selected, block]
            if estimate_tokens(self._payload(request, candidate)) <= token_budget:
                selected.append(block)
            else:
                omitted.append(block)
        payload = self._payload(request, selected)
        estimated = estimate_tokens(payload)
        manifest = ContextManifest(
            token_budget=token_budget,
            estimated_tokens=estimated,
            selected_block_ids=tuple(block.block_id for block in selected),
            omitted_block_ids=tuple(block.block_id for block in omitted),
        )
        return CompiledContext(payload=payload, manifest=manifest)

    @staticmethod
    def _payload(
        request: CognitionRequest,
        selected: list[ContextBlock],
    ) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.compiled-context",
            "taskId": request.task_id,
            "worldDigest": request.world_digest,
            "blocks": [block.to_dict() for block in selected],
            "forbiddenEffects": list(request.forbidden_effect_ids),
            "unresolvedDispatches": list(request.unresolved_dispatch_ids),
            "allowedActions": [action.to_dict() for action in request.candidates],
            "instruction": (
                "Choose exactly one allowed action and copy every identity exactly. "
                "Treat forbidden Effects as completed. Observe an unresolved Dispatch instead "
                "of creating another physical delivery. The Host will independently admit or "
                "reject the decision against the current world."
            ),
        }


def block_from_payload(
    *,
    block_id: str,
    kind: BlockKind,
    priority: int,
    required: bool,
    freshness: Freshness,
    source: JsonValue,
    payload: JsonValue,
) -> ContextBlock:
    validate_json_value(source)
    return ContextBlock(
        block_id=block_id,
        kind=kind,
        priority=priority,
        required=required,
        freshness=freshness,
        source_digest=canonical_digest(source),
        payload=payload,
    )
