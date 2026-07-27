from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def _identifier(value: str, prefix: str) -> str:
    if not value.startswith(prefix + ":") or value != value.strip():
        raise ValueError(f"identity must start with {prefix}:")
    if len(value.encode("utf-8")) > 300:
        raise ValueError("identity exceeds 300 UTF-8 bytes")
    return value


def _digest(value: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("digest must be sha256:<64 lowercase hex>")
    return value


class StreamKind(StrEnum):
    GOAL = "goal"
    TASK = "task"


class EventKind(StrEnum):
    TASK_CREATED = "task.created"
    TASK_STATE_CHANGED = "task.state-changed"
    TASK_FRONTIER_CHANGED = "task.frontier-changed"
    COGNITION_CONTEXT_COMPILED = "cognition.context-compiled"
    COGNITION_DECISION_ADMITTED = "cognition.decision-admitted"
    RUNTIME_LINKED = "runtime.linked"
    WAKEUP_SCHEDULED = "wakeup.scheduled"


class EventAdmission(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


@dataclass(frozen=True, slots=True)
class HostEvent:
    event_id: str
    stream_id: str
    stream_kind: StreamKind
    kind: EventKind
    payload_digest: str
    recorded_at_ms: int
    caused_by_event_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event")
        _identifier(self.stream_id, self.stream_kind.value)
        _digest(self.payload_digest)
        if self.recorded_at_ms < 0:
            raise ValueError("event time must be non-negative")
        if self.caused_by_event_id is not None:
            _identifier(self.caused_by_event_id, "event")
