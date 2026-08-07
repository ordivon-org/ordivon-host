from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue

from .context import CandidateAction, CompiledContext, DecisionKind


class ActionSelectionAdmissionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActionSelection:
    context_digest: str
    action_id: str
    kind: DecisionKind
    effect_id: str | None
    binding_id: str | None
    dispatch_id: str | None
    required_world_digest: str | None
    rationale: str

    def __post_init__(self) -> None:
        if (
            len(self.context_digest) != 71
            or not self.context_digest.startswith("sha256:")
            or any(
                character not in "0123456789abcdef"
                for character in self.context_digest[7:]
            )
        ):
            raise ValueError("ActionSelection context digest is invalid")
        CandidateAction(
            action_id=self.action_id,
            kind=self.kind,
            summary=self.rationale,
            effect_id=self.effect_id,
            binding_id=self.binding_id,
            dispatch_id=self.dispatch_id,
            required_world_digest=self.required_world_digest,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.action-selection",
            "contextDigest": self.context_digest,
            "actionId": self.action_id,
            "actionKind": self.kind.value,
            "effectId": self.effect_id,
            "bindingId": self.binding_id,
            "dispatchId": self.dispatch_id,
            "requiredWorldDigest": self.required_world_digest,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActionSelection:
        expected = {
            "schemaVersion",
            "kind",
            "contextDigest",
            "actionId",
            "actionKind",
            "effectId",
            "bindingId",
            "dispatchId",
            "requiredWorldDigest",
            "rationale",
        }
        if (
            set(value) != expected
            or value.get("schemaVersion") != 1
            or value.get("kind") != "ordivon.action-selection"
        ):
            raise ValueError("ActionSelection fields, version, or kind differ")
        required_strings = ("contextDigest", "actionId", "actionKind", "rationale")
        if any(not isinstance(value[field], str) for field in required_strings):
            raise ValueError("ActionSelection required fields must be strings")
        for field in (
            "effectId",
            "bindingId",
            "dispatchId",
            "requiredWorldDigest",
        ):
            if value[field] is not None and not isinstance(value[field], str):
                raise ValueError(f"{field} must be null or a string")
        return cls(
            context_digest=value["contextDigest"],
            action_id=value["actionId"],
            kind=DecisionKind(value["actionKind"]),
            effect_id=value["effectId"],
            binding_id=value["bindingId"],
            dispatch_id=value["dispatchId"],
            required_world_digest=value["requiredWorldDigest"],
            rationale=value["rationale"],
        )


@dataclass(frozen=True, slots=True)
class AdmittedActionSelection:
    context_digest: str
    action: CandidateAction
    rationale: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.admitted-action-selection",
            "contextDigest": self.context_digest,
            "action": self.action.to_dict(),
            "rationale": self.rationale,
        }


class ActionSelectionAdmission:
    def admit(
        self,
        context: CompiledContext,
        selection: ActionSelection,
        *,
        current_world_digest: str,
        completed_effect_ids: tuple[str, ...],
        unresolved_dispatch_ids: tuple[str, ...],
    ) -> AdmittedActionSelection:
        if selection.context_digest != context.digest:
            raise ActionSelectionAdmissionError("action selection targets another Context")
        raw_actions = context.payload.get("allowedActions")
        if not isinstance(raw_actions, list):
            raise ActionSelectionAdmissionError("CompiledContext has no allowed action list")
        exact = [
            value
            for value in raw_actions
            if isinstance(value, dict)
            and value.get("actionId") == selection.action_id
            and value.get("kind") == selection.kind.value
            and value.get("effectId") == selection.effect_id
            and value.get("bindingId") == selection.binding_id
            and value.get("dispatchId") == selection.dispatch_id
            and value.get("requiredWorldDigest") == selection.required_world_digest
        ]
        if len(exact) != 1:
            raise ActionSelectionAdmissionError("action selection is not one exact allowed action")
        raw = exact[0]
        summary = raw.get("summary")
        if not isinstance(summary, str):
            raise ActionSelectionAdmissionError("allowed action summary is invalid")
        action = CandidateAction(
            action_id=selection.action_id,
            kind=selection.kind,
            summary=summary,
            effect_id=selection.effect_id,
            binding_id=selection.binding_id,
            dispatch_id=selection.dispatch_id,
            required_world_digest=selection.required_world_digest,
        )
        forbidden = context.payload.get("forbiddenEffects")
        if not isinstance(forbidden, list) or any(
            not isinstance(value, str) for value in forbidden
        ):
            raise ActionSelectionAdmissionError("CompiledContext forbidden Effects are invalid")
        completed = set(completed_effect_ids) | set(forbidden)
        if action.effect_id is not None and action.effect_id in completed:
            raise ActionSelectionAdmissionError("selection attempted to repeat a completed Effect")
        if (
            action.required_world_digest is not None
            and action.required_world_digest != current_world_digest
        ):
            raise ActionSelectionAdmissionError("candidate world requirement is stale")
        unresolved = set(unresolved_dispatch_ids)
        if action.kind is DecisionKind.OBSERVE_DISPATCH:
            if action.dispatch_id not in unresolved:
                raise ActionSelectionAdmissionError("observe selection targets another Dispatch")
        elif unresolved and action.kind in {
            DecisionKind.PROPOSE_EFFECT,
            DecisionKind.FINISH_CANDIDATE,
        }:
            raise ActionSelectionAdmissionError(
                "unresolved Dispatch forbids another Effect or completion"
            )
        return AdmittedActionSelection(context.digest, action, selection.rationale)
