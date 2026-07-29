from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComponentOwner(StrEnum):
    HOST = "host"
    RUNTIME = "runtime"
    COMPUTING = "computing"
    PROVIDER = "provider"
    GIT = "git"


@dataclass(frozen=True, slots=True)
class OwnershipRule:
    object_kind: str
    owner: ComponentOwner
    reason: str

    def __post_init__(self) -> None:
        if not self.object_kind or self.object_kind != self.object_kind.strip():
            raise ValueError("object kind must be non-empty and trimmed")
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("ownership reason must be non-empty and trimmed")


_RULES = (
    OwnershipRule("goal", ComponentOwner.HOST, "The Host preserves participant purpose and commitments across cognition processes."),
    OwnershipRule("task-node", ComponentOwner.HOST, "The Host coordinates semantic progress and readiness."),
    OwnershipRule("host-event", ComponentOwner.HOST, "The Host event stream is the semantic control truth."),
    OwnershipRule("context-manifest", ComponentOwner.HOST, "Context is compiled from durable task state."),
    OwnershipRule("model-invocation", ComponentOwner.HOST, "Providers are replaceable cognition transports."),
    OwnershipRule("effect-proposal", ComponentOwner.HOST, "The Host compiles or admits proposals into explicit commitments before execution."),
    OwnershipRule("effect-binding", ComponentOwner.HOST, "The Host binds semantic intent to a current Tool contract."),
    OwnershipRule("verification-receipt", ComponentOwner.HOST, "Task completion is independent from Runtime termination."),
    OwnershipRule("workspace", ComponentOwner.RUNTIME, "The Runtime owns physical workspace identity and isolation."),
    OwnershipRule("job", ComponentOwner.RUNTIME, "The Runtime owns committed physical execution."),
    OwnershipRule("runtime-attempt", ComponentOwner.RUNTIME, "The Runtime records each physical delivery attempt."),
    OwnershipRule("artifact-bytes", ComponentOwner.RUNTIME, "The Runtime retains physical execution evidence."),
    OwnershipRule("protocol", ComponentOwner.COMPUTING, "Computing owns reference semantics and conformance."),
    OwnershipRule("provider-session", ComponentOwner.PROVIDER, "A provider session is disposable transport state."),
    OwnershipRule("source-revision", ComponentOwner.GIT, "Git is the source-history authority."),
)

_RULE_BY_KIND = {rule.object_kind: rule for rule in _RULES}
if len(_RULE_BY_KIND) != len(_RULES):
    raise RuntimeError("ownership object kinds must be unique")


def owner_of(object_kind: str) -> ComponentOwner:
    try:
        return _RULE_BY_KIND[object_kind].owner
    except KeyError as error:
        raise KeyError(f"unowned Host boundary object: {object_kind}") from error
