from __future__ import annotations

from dataclasses import dataclass

from ...effects import ArtifactRef
from ..host import CommittedHarnessAssignment, HarnessHost, RecordedHarnessRun
from ..models import HarnessRunReceipt
from .loop import AgentLoopResult, RunStopCode
from .manifest import ORDIVON_HARNESS_PROTOCOL_REVISION

_STOP_CLASS = {
    RunStopCode.CANDIDATE_COMPLETED: "completed",
    RunStopCode.NEEDS_INPUT: "interrupted",
    RunStopCode.BUDGET_EXHAUSTED: "interrupted",
    RunStopCode.CANCELLED: "cancelled",
    RunStopCode.PROVIDER_FAILED: "failed",
    RunStopCode.PROVIDER_TIMEOUT: "failed",
    RunStopCode.PROVIDER_TRANSPORT_FAILED: "failed",
    RunStopCode.PROVIDER_REJECTED: "failed",
    RunStopCode.PROVIDER_UNAVAILABLE: "failed",
    RunStopCode.INVALID_TOOL_CALL: "failed",
    RunStopCode.RUNTIME_UNKNOWN: "unknown",
    RunStopCode.INVALID_MODEL_OUTPUT: "failed",
}


@dataclass(frozen=True, slots=True)
class NativeRunTimes:
    started_at_ms: int
    finished_at_ms: int

    def __post_init__(self) -> None:
        if self.started_at_ms < 0 or self.finished_at_ms < self.started_at_ms:
            raise ValueError("native Harness Run times are invalid")


def build_native_run_receipt(
    committed: CommittedHarnessAssignment,
    result: AgentLoopResult,
    *,
    times: NativeRunTimes,
    harness_revision: str = ORDIVON_HARNESS_PROTOCOL_REVISION,
) -> HarnessRunReceipt:
    native = committed.native_run_contract
    if native is None:
        raise ValueError("native Run receipt requires a NativeHarnessRunContract")
    if result.harness_run_id != native.harness_run_id:
        raise ValueError("Agent Loop result belongs to another native Harness Run")
    jobs = sorted(
        {
            observation.runtime_job_ref
            for observation in result.observations
            if observation.runtime_job_ref is not None
        }
    )
    artifacts: dict[str, ArtifactRef] = {}
    for observation in result.observations:
        for ref in observation.artifact_refs:
            retained = artifacts.get(ref.ref)
            if retained is not None and retained != ref:
                raise ValueError("one Artifact identity resolves to conflicting evidence")
            artifacts[ref.ref] = ref
    return HarnessRunReceipt(
        harness_run_id=native.harness_run_id,
        assignment_id=committed.assignment.assignment_id,
        assignment_generation=committed.assignment.generation,
        harness_id=committed.assignment.target_harness_id,
        harness_revision=harness_revision,
        manifest_digest=committed.manifest.digest,
        session_ref=None,
        started_at_ms=times.started_at_ms,
        finished_at_ms=times.finished_at_ms,
        stop_reason=_STOP_CLASS[result.stop_code],
        event_digest=result.trace.digest,
        context_digest=committed.assignment.context_object_digest,
        tool_catalog_digest=committed.assignment.tool_catalog_digest,
        runtime_job_refs=tuple(jobs),
        artifact_refs=tuple(artifacts[key] for key in sorted(artifacts)),
        usage=result.usage,
        termination_code=result.stop_code.value,
        continuation_ref=None,
    )


def record_native_run_result(
    host: HarnessHost,
    committed: CommittedHarnessAssignment,
    result: AgentLoopResult,
    *,
    times: NativeRunTimes,
) -> RecordedHarnessRun:
    receipt = build_native_run_receipt(committed, result, times=times)
    return host.record_run(
        committed,
        receipt,
        trace=result.trace.to_dict(),
        observations=tuple(item.to_dict() for item in result.observations),
        conclusion=None if result.conclusion is None else result.conclusion.to_dict(),
    )
