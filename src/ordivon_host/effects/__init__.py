"""Experimental executor-neutral Effect lifecycle and wire models.

The lifecycle remains package-scoped until two materially different external
consumers replace specialized Host state machines with net semantic deletion.
"""

from .lifecycle import (
    EffectLifecycleError,
    EffectLifecycleHost,
    EffectSuperseded,
)
from .models import (
    ArtifactRef,
    DispatchEnvelope,
    EffectStep,
    ObservationEnvelope,
    PreparedDispatch,
    StateRef,
    TaskOutcome,
    VerificationReceipt,
    VerificationResultItem,
)

__all__ = [
    "ArtifactRef",
    "DispatchEnvelope",
    "EffectLifecycleError",
    "EffectLifecycleHost",
    "EffectStep",
    "EffectSuperseded",
    "ObservationEnvelope",
    "PreparedDispatch",
    "StateRef",
    "TaskOutcome",
    "VerificationReceipt",
    "VerificationResultItem",
]
