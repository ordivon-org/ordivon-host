from __future__ import annotations

from typing import Protocol

from anc_canonical import JsonValue

from ..effects.models import DispatchEnvelope, ObservationEnvelope


class DeliveryUncertain(RuntimeError):
    """The executor may have committed the request, so blind redelivery is forbidden."""


class EffectExecutor(Protocol):
    executor_id: str

    def deliver(
        self,
        dispatch: DispatchEnvelope,
        request: dict[str, JsonValue],
    ) -> ObservationEnvelope: ...

    def observe(
        self,
        dispatch: DispatchEnvelope,
        request: dict[str, JsonValue],
    ) -> ObservationEnvelope | None: ...
