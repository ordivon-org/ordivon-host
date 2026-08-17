from __future__ import annotations

from typing import Any, Literal

from .errors import RuntimeProtocolError

RuntimeObservationClass = Literal[
    "active",
    "succeeded",
    "failed",
    "reconciliation_required",
    "unknown",
]

_TERMINAL_FAILURES = {"failed", "timed_out", "cancelled"}
_DELIVERY_CLASSES = {"in_progress", "committed", "reconciliation_required", "unknown"}


def classify_runtime_job_observation(payload: dict[str, Any]) -> RuntimeObservationClass:
    """Classify one Runtime Job without using the coarse compatibility ``status`` field.

    Host state transitions must follow Runtime's exact physical-execution and delivery
    semantics.  ``status`` remains useful for display/history only; it is intentionally
    not consulted here because a terminal resolution can still require reconciliation.
    """

    execution_terminal = payload.get("executionTerminal")
    execution_disposition = payload.get("executionDisposition")
    delivery_disposition = payload.get("deliveryDisposition")
    recovery_required = payload.get("recoveryRequired")
    result_available = payload.get("resultAvailable")
    semantic_completion_evaluated = payload.get("semanticCompletionEvaluated")

    if not isinstance(execution_terminal, bool):
        raise RuntimeProtocolError("Runtime Job observation omitted executionTerminal")
    if execution_disposition is not None and not isinstance(execution_disposition, str):
        raise RuntimeProtocolError("Runtime executionDisposition is invalid")
    if delivery_disposition not in _DELIVERY_CLASSES:
        raise RuntimeProtocolError("Runtime deliveryDisposition is invalid")
    if not isinstance(recovery_required, bool):
        raise RuntimeProtocolError("Runtime Job observation omitted recoveryRequired")
    if not isinstance(result_available, bool):
        raise RuntimeProtocolError("Runtime Job observation omitted resultAvailable")
    if semantic_completion_evaluated is not False:
        raise RuntimeProtocolError(
            "Runtime must not claim Host/domain semantic completion"
        )

    if recovery_required or delivery_disposition == "reconciliation_required":
        return "reconciliation_required"

    if delivery_disposition == "unknown":
        if not execution_terminal or execution_disposition != "lost" or not result_available:
            raise RuntimeProtocolError("Runtime unknown delivery projection is inconsistent")
        return "unknown"

    if delivery_disposition == "in_progress":
        if execution_terminal or execution_disposition is not None or result_available:
            raise RuntimeProtocolError("Runtime in-progress projection is inconsistent")
        return "active"

    # From here delivery is committed and no recovery is required.
    if not execution_terminal or execution_disposition is None or not result_available:
        raise RuntimeProtocolError("Runtime committed terminal projection is incomplete")
    if execution_disposition == "succeeded":
        return "succeeded"
    if execution_disposition in _TERMINAL_FAILURES:
        return "failed"
    raise RuntimeProtocolError(
        "Runtime committed delivery carries an unsupported terminal disposition"
    )
