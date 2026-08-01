from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import ClassVar, Iterator


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
    TASK = "task"


_CORE_EVENT_VALUES = {
    "TASK_CREATED": "task.created",
    "TASK_STATE_CHANGED": "task.state-changed",
    "TASK_FRONTIER_CHANGED": "task.frontier-changed",
    "COGNITION_CONTEXT_COMPILED": "cognition.context-compiled",
    "COGNITION_INVOCATION_PREPARED": "cognition.invocation-prepared",
    "COGNITION_DECISION_ADMITTED": "cognition.decision-admitted",
    "COGNITION_PROPOSAL_RESOLVED": "cognition.proposal-resolved",
    "EFFECT_DISPATCH_PREPARED": "effect.dispatch-prepared",
    "EFFECT_OUTCOME_UNKNOWN": "effect.outcome-unknown",
    "EFFECT_DISPATCH_OBSERVED": "effect.dispatch-observed",
    "VERIFICATION_RECORDED": "verification.recorded",
    "TASK_OUTCOME_RECORDED": "task.outcome-recorded",
    "TASK_RESULT_APPLIED": "task.result-applied",
    "RUNTIME_LINKED": "runtime.linked",
    "RUNTIME_DISPATCH_PREPARED": "runtime.dispatch-prepared",
    "RUNTIME_OUTCOME_UNKNOWN": "runtime.outcome-unknown",
    "RUNTIME_DISPATCH_OBSERVED": "runtime.dispatch-observed",
    "VERIFICATION_ACCEPTED": "verification.accepted",
}
_CORE_NAMES_BY_VALUE = {value: name for name, value in _CORE_EVENT_VALUES.items()}
_RESERVED_EVENT_NAMESPACES = frozenset(
    {"task", "cognition", "effect", "verification", "runtime", "wakeup"}
)


class _EventKindMeta(type):
    def __iter__(cls) -> Iterator[EventKind]:
        return iter(cls._core_values)


class EventKind(str, metaclass=_EventKindMeta):
    """Immutable interned Event kind with fail-closed Host namespaces.

    Host core events are declared below. Other lowercase namespaces remain available
    to independently versioned components such as Harness, without mutating an Enum
    class or accepting misspellings in Host-owned namespaces.
    """

    _cache: ClassVar[dict[str, EventKind]] = {}
    _lock: ClassVar[RLock] = RLock()
    _core_values: ClassVar[tuple[EventKind, ...]] = ()

    def __new__(cls, value: str) -> EventKind:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or "." not in value
            or len(value.encode("utf-8")) > 200
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in value
            )
        ):
            raise ValueError(f"invalid Event kind: {value!r}")
        namespace = value.split(".", 1)[0]
        if namespace in _RESERVED_EVENT_NAMESPACES and value not in _CORE_NAMES_BY_VALUE:
            raise ValueError(f"unknown Host core Event kind: {value}")
        with cls._lock:
            existing = cls._cache.get(value)
            if existing is not None:
                return existing
            member = str.__new__(cls, value)
            cls._cache[value] = member
            return member

    @property
    def value(self) -> str:
        return str(self)

    @property
    def name(self) -> str:
        return _CORE_NAMES_BY_VALUE.get(str(self), "EXTENSION")

    def __repr__(self) -> str:
        return f"EventKind({str(self)!r})"


for _event_name, _event_value in _CORE_EVENT_VALUES.items():
    setattr(EventKind, _event_name, EventKind(_event_value))
EventKind._core_values = tuple(
    getattr(EventKind, name) for name in _CORE_EVENT_VALUES
)


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
        if not isinstance(self.kind, EventKind):
            raise ValueError("Host Event kind must be an EventKind")
        _digest(self.payload_digest)
        if self.recorded_at_ms < 0:
            raise ValueError("event time must be non-negative")
        if self.caused_by_event_id is not None:
            _identifier(self.caused_by_event_id, "event")
