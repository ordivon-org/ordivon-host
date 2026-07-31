from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from anc_canonical import JsonValue, canonical_digest, validate_json_value


def _text(value: str, label: str, *, max_bytes: int = 2_000) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _text(self.name, "Tool name", max_bytes=120)
        _text(self.description, "Tool description", max_bytes=1_000)
        validate_json_value(self.input_schema)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    tool_call_id: str
    name: str
    arguments: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _text(self.tool_call_id, "Tool Call identity", max_bytes=300)
        _text(self.name, "Tool Call name", max_bytes=120)
        validate_json_value(self.arguments)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "toolCallId": self.tool_call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


_CONCLUSION_STATUSES = {"candidate_completed", "needs_input"}


@dataclass(frozen=True, slots=True)
class AgentRunConclusion:
    status: str
    summary: str
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    unresolved_unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _CONCLUSION_STATUSES:
            raise ValueError(f"unsupported Agent conclusion status: {self.status}")
        _text(self.summary, "Agent conclusion summary", max_bytes=8_000)
        for values, label in (
            (self.artifact_refs, "Artifact reference"),
            (self.evidence_refs, "evidence reference"),
            (self.unresolved_unknowns, "unresolved unknown"),
        ):
            for value in values:
                _text(value, label, max_bytes=500)
            if len(values) != len(set(values)):
                raise ValueError(f"{label} values must be unique")
        if self.status == "candidate_completed" and self.unresolved_unknowns:
            raise ValueError("candidate completion cannot retain unresolved unknowns")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "summary": self.summary,
            "artifactRefs": list(self.artifact_refs),
            "evidenceRefs": list(self.evidence_refs),
            "unresolvedUnknowns": list(self.unresolved_unknowns),
        }


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    harness_run_id: str
    turn_id: str
    sequence: int
    assignment_id: str
    context_digest: str
    tool_catalog_digest: str
    messages: tuple[dict[str, JsonValue], ...]
    tools: tuple[AgentToolDefinition, ...]
    remaining_budget: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _text(self.harness_run_id, "Harness Run identity", max_bytes=300)
        _text(self.turn_id, "Agent turn identity", max_bytes=300)
        _text(self.assignment_id, "Assignment identity", max_bytes=300)
        if self.sequence < 1:
            raise ValueError("Agent turn sequence must be positive")
        _digest(self.context_digest, "Agent turn Context digest")
        _digest(self.tool_catalog_digest, "Agent turn Tool catalog digest")
        for message in self.messages:
            validate_json_value(message)
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Agent turn Tool names must be unique")
        validate_json_value(self.remaining_budget)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.agent-turn-request",
            "harnessRunId": self.harness_run_id,
            "turnId": self.turn_id,
            "sequence": self.sequence,
            "assignmentId": self.assignment_id,
            "contextDigest": self.context_digest,
            "toolCatalogDigest": self.tool_catalog_digest,
            "messages": list(self.messages),
            "tools": [tool.to_dict() for tool in self.tools],
            "remainingBudget": self.remaining_budget,
        }


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    model_call_id: str
    model_id: str
    content: str | None
    tool_calls: tuple[AgentToolCall, ...]
    conclusion: AgentRunConclusion | None
    usage: dict[str, JsonValue]
    finish_reason: str
    raw_response_digest: str

    def __post_init__(self) -> None:
        _text(self.model_call_id, "Model Call identity", max_bytes=300)
        _text(self.model_id, "model identity", max_bytes=300)
        if self.content is not None and len(self.content.encode("utf-8")) > 1_048_576:
            raise ValueError("Agent turn content exceeds one MiB")
        call_ids = [call.tool_call_id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Agent turn Tool Call identities must be unique")
        if self.conclusion is not None and self.tool_calls:
            raise ValueError("Agent turn cannot request Tools and conclude simultaneously")
        if not self.tool_calls and self.conclusion is None:
            raise ValueError("Agent turn must request a Tool or provide a conclusion")
        validate_json_value(self.usage)
        _text(self.finish_reason, "model finish reason", max_bytes=300)
        _digest(self.raw_response_digest, "raw model response digest")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.agent-turn-result",
            "modelCallId": self.model_call_id,
            "modelId": self.model_id,
            "content": self.content,
            "toolCalls": [call.to_dict() for call in self.tool_calls],
            "conclusion": None if self.conclusion is None else self.conclusion.to_dict(),
            "usage": self.usage,
            "finishReason": self.finish_reason,
            "rawResponseDigest": self.raw_response_digest,
        }


class AgentTurnFailureCode(str, Enum):
    FAILED = "provider_failed"
    TIMEOUT = "provider_timeout"
    TRANSPORT_FAILED = "provider_transport_failed"
    REJECTED = "provider_rejected"
    UNAVAILABLE = "provider_unavailable"


class AgentTurnAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_code: AgentTurnFailureCode = AgentTurnFailureCode.FAILED,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class AgentTurnAdapter(Protocol):
    adapter_id: str
    model_id: str

    def invoke(self, request: AgentTurnRequest) -> AgentTurnResult: ...


class ScriptedTurnAdapter:
    """Deterministic OH1 adapter. It never calls a physical model provider."""

    adapter_id = "ordivon.scripted-turn-adapter.v1"
    model_id = "ordivon.scripted-model.v1"

    def __init__(self, results: tuple[AgentTurnResult, ...]) -> None:
        if not results:
            raise ValueError("ScriptedTurnAdapter requires at least one result")
        self._results = results
        self._index = 0
        self.requests: list[AgentTurnRequest] = []

    def invoke(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.requests.append(request)
        if self._index >= len(self._results):
            raise AgentTurnAdapterError("ScriptedTurnAdapter has no remaining result")
        result = self._results[self._index]
        self._index += 1
        return result
