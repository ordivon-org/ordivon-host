from __future__ import annotations

from .context import CompiledContext, DecisionKind
from .decision import ActionSelection


class ScriptedActionSelector:
    """Pure deterministic action-selection source for tests and bounded local policy.

    It performs no Provider, subprocess, network, Tool, or session work. Callers may use
    it outside the Host admission boundary to produce an ActionSelection fixture.
    """

    def __init__(self, preferred_kinds: tuple[DecisionKind, ...]) -> None:
        if not preferred_kinds:
            raise ValueError("scripted selector requires at least one preferred action kind")
        self.preferred_kinds = preferred_kinds

    def evidence_metadata(self) -> dict[str, object]:
        return {"sourceKind": "scripted-policy"}

    def select(self, context: CompiledContext) -> ActionSelection:
        raw_actions = context.payload.get("allowedActions")
        if not isinstance(raw_actions, list) or len(raw_actions) < 2:
            raise ValueError("scripted selector requires multiple allowed actions")
        for preferred in self.preferred_kinds:
            for raw in raw_actions:
                if isinstance(raw, dict) and raw.get("kind") == preferred.value:
                    return _selection_from_action(
                        context,
                        raw,
                        rationale=f"Selected preferred action kind {preferred.value}.",
                    )
        raise ValueError("no candidate matches the scripted preference order")


def _selection_from_action(
    context: CompiledContext,
    raw: dict[str, object],
    *,
    rationale: str,
) -> ActionSelection:
    return ActionSelection(
        context_digest=context.digest,
        action_id=str(raw.get("actionId")),
        kind=DecisionKind(str(raw.get("kind"))),
        effect_id=raw.get("effectId") if isinstance(raw.get("effectId"), str) else None,
        binding_id=(raw.get("bindingId") if isinstance(raw.get("bindingId"), str) else None),
        dispatch_id=(raw.get("dispatchId") if isinstance(raw.get("dispatchId"), str) else None),
        required_world_digest=(
            raw.get("requiredWorldDigest")
            if isinstance(raw.get("requiredWorldDigest"), str)
            else None
        ),
        rationale=rationale,
    )



__all__ = ["ScriptedActionSelector"]
