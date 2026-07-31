from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from anc_canonical import JsonValue, canonical_digest, validate_json_value


_EVENT_KINDS = {
    "run_started",
    "model_call_started",
    "model_call_completed",
    "tool_call_proposed",
    "tool_call_dispatched",
    "tool_call_observed",
    "tool_call_rejected",
    "tool_call_unknown",
    "run_stopped",
}


@dataclass(frozen=True, slots=True)
class HarnessRunEvent:
    sequence: int
    kind: str
    occurred_at_ms: int
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("Harness event sequence must be positive")
        if self.kind not in _EVENT_KINDS:
            raise ValueError(f"unsupported Harness event kind: {self.kind}")
        if self.occurred_at_ms < 0:
            raise ValueError("Harness event time must be non-negative")
        validate_json_value(self.payload)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "occurredAtMs": self.occurred_at_ms,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class HarnessTrace:
    harness_run_id: str
    events: tuple[HarnessRunEvent, ...]

    def __post_init__(self) -> None:
        if not self.harness_run_id or self.harness_run_id != self.harness_run_id.strip():
            raise ValueError("Harness Run identity must be non-empty and trimmed")
        if tuple(event.sequence for event in self.events) != tuple(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("Harness event sequence must be contiguous")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-trace",
            "harnessRunId": self.harness_run_id,
            "events": [event.to_dict() for event in self.events],
        }


class TraceRecorder:
    def __init__(self, harness_run_id: str, *, clock_ms: Callable[[], int]) -> None:
        if not harness_run_id or harness_run_id != harness_run_id.strip():
            raise ValueError("Harness Run identity must be non-empty and trimmed")
        self.harness_run_id = harness_run_id
        self.clock_ms = clock_ms
        self._events: list[HarnessRunEvent] = []
        self._last_time = -1

    def record(self, kind: str, payload: dict[str, JsonValue]) -> HarnessRunEvent:
        observed = self.clock_ms()
        if observed < 0:
            raise ValueError("Harness clock returned a negative time")
        occurred_at_ms = max(observed, self._last_time)
        event = HarnessRunEvent(
            sequence=len(self._events) + 1,
            kind=kind,
            occurred_at_ms=occurred_at_ms,
            payload=dict(payload),
        )
        self._events.append(event)
        self._last_time = occurred_at_ms
        return event

    def freeze(self) -> HarnessTrace:
        return HarnessTrace(self.harness_run_id, tuple(self._events))
