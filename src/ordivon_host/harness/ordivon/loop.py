from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Callable

from anc_canonical import JsonValue, canonical_bytes

from .events import HarnessTrace, TraceRecorder
from .model import (
    AgentRunConclusion,
    AgentTurnAdapter,
    AgentTurnAdapterError,
    AgentTurnFailureCode,
    AgentTurnRequest,
)
from .tools import ToolBridge, ToolBridgeError, ToolObservation


class RunStopCode(str, Enum):
    CANDIDATE_COMPLETED = "candidate_completed"
    NEEDS_INPUT = "needs_input"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_TRANSPORT_FAILED = "provider_transport_failed"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_TOOL_CALL = "invalid_tool_call"
    RUNTIME_UNKNOWN = "runtime_unknown"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_model_calls: int
    max_tool_calls: int
    max_observation_bytes: int
    max_wall_time_ms: int

    def __post_init__(self) -> None:
        if min(
            self.max_model_calls,
            self.max_tool_calls,
            self.max_observation_bytes,
            self.max_wall_time_ms,
        ) < 1:
            raise ValueError("Ordivon Harness budgets must be positive")

    def remaining(
        self,
        *,
        model_calls: int,
        tool_calls: int,
        observation_bytes: int,
        elapsed_ms: int,
    ) -> dict[str, JsonValue]:
        return {
            "modelCalls": max(0, self.max_model_calls - model_calls),
            "toolCalls": max(0, self.max_tool_calls - tool_calls),
            "observationBytes": max(0, self.max_observation_bytes - observation_bytes),
            "wallTimeMs": max(0, self.max_wall_time_ms - elapsed_ms),
        }


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    harness_run_id: str
    stop_code: RunStopCode
    trace: HarnessTrace
    conclusion: AgentRunConclusion | None
    messages: tuple[dict[str, JsonValue], ...]
    observations: tuple[ToolObservation, ...]
    model_calls: int
    tool_calls: int
    observation_bytes: int
    usage: dict[str, JsonValue]

    @property
    def candidate_completed(self) -> bool:
        return self.stop_code is RunStopCode.CANDIDATE_COMPLETED


class OrdivonAgentLoop:
    """Thin sequential OH1 loop. Host Task and Runtime Job lifecycles remain external."""

    def __init__(
        self,
        adapter: AgentTurnAdapter,
        tool_bridge: ToolBridge,
        *,
        budget: RunBudget,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.adapter = adapter
        self.tool_bridge = tool_bridge
        self.budget = budget
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def run(
        self,
        *,
        harness_run_id: str,
        assignment_id: str,
        context_digest: str,
        initial_messages: tuple[dict[str, JsonValue], ...],
        cancellation: CancellationToken | None = None,
    ) -> AgentLoopResult:
        cancellation = cancellation or CancellationToken()
        recorder = TraceRecorder(harness_run_id, clock_ms=self.clock_ms)
        started_at_ms = self.clock_ms()
        messages = [dict(message) for message in initial_messages]
        observations: list[ToolObservation] = []
        provider_usage: list[dict[str, JsonValue]] = []
        seen_model_call_ids: set[str] = set()
        model_calls = 0
        tool_calls = 0
        observation_bytes = 0
        recorder.record(
            "run_started",
            {
                "assignmentId": assignment_id,
                "contextDigest": context_digest,
                "toolCatalogDigest": self.tool_bridge.catalog_digest,
                "adapterId": self.adapter.adapter_id,
                "modelId": self.adapter.model_id,
            },
        )

        def elapsed_ms() -> int:
            return max(0, self.clock_ms() - started_at_ms)

        def stop(
            code: RunStopCode,
            *,
            conclusion: AgentRunConclusion | None = None,
            detail: str | None = None,
        ) -> AgentLoopResult:
            payload: dict[str, JsonValue] = {
                "stopCode": code.value,
                "modelCalls": model_calls,
                "toolCalls": tool_calls,
                "observationBytes": observation_bytes,
                "elapsedMs": elapsed_ms(),
            }
            if detail is not None:
                payload["detail"] = detail[:2_048]
            recorder.record("run_stopped", payload)
            return AgentLoopResult(
                harness_run_id=harness_run_id,
                stop_code=code,
                trace=recorder.freeze(),
                conclusion=conclusion,
                messages=tuple(messages),
                observations=tuple(observations),
                model_calls=model_calls,
                tool_calls=tool_calls,
                observation_bytes=observation_bytes,
                usage={
                    "modelCalls": model_calls,
                    "toolCalls": tool_calls,
                    "observationBytes": observation_bytes,
                    "providerUsage": provider_usage,
                },
            )

        while True:
            if cancellation.cancelled:
                return stop(RunStopCode.CANCELLED)
            if model_calls >= self.budget.max_model_calls or elapsed_ms() >= self.budget.max_wall_time_ms:
                return stop(RunStopCode.BUDGET_EXHAUSTED)
            sequence = model_calls + 1
            turn_id = f"turn:{harness_run_id.removeprefix('harness-run:')}:{sequence}"
            request = AgentTurnRequest(
                harness_run_id=harness_run_id,
                turn_id=turn_id,
                sequence=sequence,
                assignment_id=assignment_id,
                context_digest=context_digest,
                tool_catalog_digest=self.tool_bridge.catalog_digest,
                messages=tuple(messages),
                tools=self.tool_bridge.definitions(),
                remaining_budget=self.budget.remaining(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    observation_bytes=observation_bytes,
                    elapsed_ms=elapsed_ms(),
                ),
            )
            recorder.record(
                "model_call_started",
                {"turnId": turn_id, "requestDigest": request.digest},
            )
            try:
                result = self.adapter.invoke(request)
            except AgentTurnAdapterError as error:
                stop_code = {
                    AgentTurnFailureCode.FAILED: RunStopCode.PROVIDER_FAILED,
                    AgentTurnFailureCode.TIMEOUT: RunStopCode.PROVIDER_TIMEOUT,
                    AgentTurnFailureCode.TRANSPORT_FAILED: (
                        RunStopCode.PROVIDER_TRANSPORT_FAILED
                    ),
                    AgentTurnFailureCode.REJECTED: RunStopCode.PROVIDER_REJECTED,
                    AgentTurnFailureCode.UNAVAILABLE: RunStopCode.PROVIDER_UNAVAILABLE,
                }[error.failure_code]
                return stop(stop_code, detail=str(error))
            except (TypeError, ValueError) as error:
                return stop(RunStopCode.INVALID_MODEL_OUTPUT, detail=str(error))
            if result.model_id != self.adapter.model_id:
                return stop(
                    RunStopCode.INVALID_MODEL_OUTPUT,
                    detail="Agent Turn result model identity differs from the Adapter",
                )
            if result.model_call_id in seen_model_call_ids:
                return stop(
                    RunStopCode.INVALID_MODEL_OUTPUT,
                    detail=f"duplicate Model Call identity: {result.model_call_id}",
                )
            seen_model_call_ids.add(result.model_call_id)
            model_calls += 1
            provider_usage.append(dict(result.usage))
            recorder.record(
                "model_call_completed",
                {
                    "turnId": turn_id,
                    "modelCallId": result.model_call_id,
                    "resultDigest": result.digest,
                    "rawResponseDigest": result.raw_response_digest,
                    "finishReason": result.finish_reason,
                },
            )
            if result.conclusion is not None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": result.content,
                        "conclusion": result.conclusion.to_dict(),
                    }
                )
                if result.conclusion.status == "candidate_completed":
                    return stop(
                        RunStopCode.CANDIDATE_COMPLETED,
                        conclusion=result.conclusion,
                    )
                return stop(RunStopCode.NEEDS_INPUT, conclusion=result.conclusion)

            if tool_calls + len(result.tool_calls) > self.budget.max_tool_calls:
                return stop(RunStopCode.BUDGET_EXHAUSTED)
            messages.append(
                {
                    "role": "assistant",
                    "content": result.content,
                    "toolCalls": [call.to_dict() for call in result.tool_calls],
                }
            )
            for call in result.tool_calls:
                if cancellation.cancelled:
                    return stop(RunStopCode.CANCELLED)
                recorder.record(
                    "tool_call_proposed",
                    {
                        "toolCallId": call.tool_call_id,
                        "toolName": call.name,
                        "toolCallDigest": call.digest,
                    },
                )
                step_id = f"turn-{sequence}-tool-{tool_calls + 1}:{call.tool_call_id}"
                try:
                    observation = self.tool_bridge.execute(call, step_id=step_id)
                except ToolBridgeError as error:
                    return stop(RunStopCode.INVALID_TOOL_CALL, detail=str(error))
                tool_calls += 1
                observations.append(observation)
                if observation.status != "rejected":
                    recorder.record(
                        "tool_call_dispatched",
                        {
                            "toolCallId": call.tool_call_id,
                            "toolName": call.name,
                            "stepId": step_id,
                            "runtimeJobRef": observation.runtime_job_ref,
                        },
                    )
                encoded_size = len(canonical_bytes(observation.to_dict()))
                observation_bytes += encoded_size
                event_kind = {
                    "observed": "tool_call_observed",
                    "rejected": "tool_call_rejected",
                    "unknown": "tool_call_unknown",
                }[observation.status]
                recorder.record(
                    event_kind,
                    {
                        "toolCallId": call.tool_call_id,
                        "toolName": call.name,
                        "observationDigest": observation.digest,
                        "runtimeJobRef": observation.runtime_job_ref,
                        "reconciled": observation.reconciled,
                        "encodedBytes": encoded_size,
                    },
                )
                messages.append(observation.to_model_message())
                if observation_bytes > self.budget.max_observation_bytes:
                    return stop(RunStopCode.BUDGET_EXHAUSTED)
                if observation.status == "unknown":
                    return stop(
                        RunStopCode.RUNTIME_UNKNOWN,
                        detail=f"Tool Call {call.tool_call_id} has uncertain delivery or outcome",
                    )
