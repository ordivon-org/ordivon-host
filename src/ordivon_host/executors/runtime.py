from __future__ import annotations

from typing import Any

from anc_canonical import JsonValue, canonical_digest

from ..effects.models import ArtifactRef, DispatchEnvelope, ObservationEnvelope
from ..runtime import (
    RuntimeClient,
    RuntimeClientError,
    RuntimeProtocolError,
    RuntimeToolRejected,
    find_jobs_by_client_request,
)
from .base import DeliveryUncertain


class RuntimeEffectExecutor:
    executor_id = "executor:ordivon-runtime-v1"

    def __init__(self, runtime: RuntimeClient, *, observe_wait_ms: int = 30_000) -> None:
        if observe_wait_ms < 0 or observe_wait_ms > 30_000:
            raise ValueError("Runtime observe wait must be between 0 and 30000")
        self.runtime = runtime
        self.observe_wait_ms = observe_wait_ms

    def deliver(
        self,
        dispatch: DispatchEnvelope,
        request: dict[str, JsonValue],
    ) -> ObservationEnvelope:
        self._require_dispatch(dispatch)
        operation, arguments = self._request(request)
        try:
            payload = self.runtime.call_tool(operation, arguments)
        except RuntimeToolRejected as error:
            if error.detail.commit_state == "not_committed":
                raise
            raise DeliveryUncertain(str(error)) from error
        except RuntimeClientError as error:
            raise DeliveryUncertain(str(error)) from error
        return self._observation(dispatch, payload)

    def observe(
        self,
        dispatch: DispatchEnvelope,
        request: dict[str, JsonValue],
    ) -> ObservationEnvelope | None:
        self._require_dispatch(dispatch)
        self._request(request)
        jobs = find_jobs_by_client_request(self.runtime, dispatch.idempotency_key)
        if not jobs:
            return None
        job_ids = {job.get("jobId") for job in jobs}
        if len(job_ids) != 1 or None in job_ids:
            raise RuntimeProtocolError(
                "one idempotency key resolved to conflicting Runtime Jobs"
            )
        job_id = next(iter(job_ids))
        if not isinstance(job_id, str):
            raise RuntimeProtocolError("Runtime Job identity is invalid")
        payload = self.runtime.call_tool(
            "task.observe",
            {
                "schemaVersion": 1,
                "jobId": job_id,
                "waitMs": self.observe_wait_ms,
                "stdoutTailBytes": 4_096,
                "stderrTailBytes": 4_096,
            },
        )
        return self._observation(dispatch, payload)

    def _require_dispatch(self, dispatch: DispatchEnvelope) -> None:
        if dispatch.executor_id != self.executor_id:
            raise ValueError("Dispatch targets another executor")

    @staticmethod
    def _request(
        request: dict[str, JsonValue],
    ) -> tuple[str, dict[str, Any]]:
        if set(request) != {"operation", "arguments"}:
            raise ValueError("Runtime executor request fields differ")
        operation = request["operation"]
        arguments = request["arguments"]
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError("Runtime executor request is invalid")
        return operation, dict(arguments)

    def _observation(
        self,
        dispatch: DispatchEnvelope,
        payload: dict[str, Any],
    ) -> ObservationEnvelope:
        status = payload.get("status")
        if not isinstance(status, str):
            raise RuntimeProtocolError("Runtime observation omitted status")
        normalized = {
            "queued": "accepted",
            "working": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "timed_out": "failed",
            "cancelled": "failed",
            "lost": "unknown",
            "orphaned": "unknown",
            "unknown": "unknown",
        }.get(status)
        if normalized is None:
            raise RuntimeProtocolError(f"unsupported Runtime status: {status}")
        typed: dict[str, JsonValue] = dict(payload)
        evidence: tuple[ArtifactRef, ...] = ()
        job_id = payload.get("jobId")
        if isinstance(job_id, str):
            evidence = (
                ArtifactRef(
                    ref=f"runtime-job:{job_id}",
                    kind="runtime-job-observation",
                    digest=canonical_digest(typed),
                ),
            )
        return ObservationEnvelope(
            dispatch_id=dispatch.dispatch_id,
            executor_id=self.executor_id,
            status=normalized,
            payload_digest=canonical_digest(typed),
            evidence_refs=evidence,
        )
