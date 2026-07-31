from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Any, Callable

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..effects import ArtifactRef
from .host import CommittedHarnessAssignment
from .models import HarnessCapabilityManifest, HarnessRunReceipt


class HermesACPError(RuntimeError):
    pass


class HermesACPProtocolError(HermesACPError):
    pass


class HermesACPTimeout(HermesACPError):
    pass


class HermesACPExited(HermesACPError):
    pass


def _text(value: str, label: str, *, max_bytes: int = 100_000) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HermesACPProtocolError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HermesACPProtocolError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise HermesACPProtocolError(f"{label} must be an integer")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HermesACPProtocolError(f"{label} must be a string or null")
    return value


def _bool_capability(value: Any) -> bool:
    return isinstance(value, dict) or value is True


def _model_identity(model_id: str) -> tuple[str, str]:
    if ":" not in model_id:
        return "unknown", model_id
    provider, model = model_id.split(":", 1)
    return provider or "unknown", model or model_id


@dataclass(frozen=True, slots=True)
class HermesACPNormalizedEvent:
    kind: str
    method: str
    update_type: str | None
    observed_at_ms: int
    session_id: str
    tool_call_id: str | None
    tool_kind: str | None
    payload_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "method": self.method,
            "updateType": self.update_type,
            "observedAtMs": self.observed_at_ms,
            "sessionId": self.session_id,
            "toolCallId": self.tool_call_id,
            "toolKind": self.tool_kind,
            "payloadDigest": self.payload_digest,
        }


@dataclass(frozen=True, slots=True)
class HermesACPSession:
    session_id: str
    protocol_version: int
    agent_name: str
    agent_version: str
    model_id: str
    model: str
    model_provider: str
    cwd: str
    load_session: bool
    session_resume: bool
    session_fork: bool
    images: bool
    provenance: dict[str, JsonValue]
    provenance_digest: str
    message_start_index: int


@dataclass(frozen=True, slots=True)
class HermesACPPromptHandle:
    session: HermesACPSession
    request_id: int
    started_at_ms: int
    message_start_index: int
    update_start_index: int
    prompt_digest: str


@dataclass(frozen=True, slots=True)
class HermesACPPromptResult:
    session: HermesACPSession
    request_id: int
    status: str
    stop_reason: str
    provider_stop_reason: str
    assistant_text: str
    started_at_ms: int
    finished_at_ms: int
    duration_ms: int
    usage: dict[str, JsonValue]
    raw_message_digest: str
    raw_message_count: int
    normalized_events: tuple[HermesACPNormalizedEvent, ...]
    update_type_counts: dict[str, int]
    tool_items: tuple[dict[str, JsonValue], ...]
    thought_event_count: int
    stderr_tail: str

    def __post_init__(self) -> None:
        if self.status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unsupported Hermes ACP status: {self.status}")
        if self.stop_reason not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unsupported Hermes Harness stop reason: {self.stop_reason}")
        if self.provider_stop_reason not in {
            "end_turn",
            "max_tokens",
            "max_turn_requests",
            "refusal",
            "cancelled",
        }:
            raise ValueError(
                f"unsupported Hermes ACP stop reason: {self.provider_stop_reason}"
            )
        if self.started_at_ms < 0 or self.finished_at_ms < self.started_at_ms:
            raise ValueError("Hermes ACP result timestamps are invalid")
        if self.duration_ms != self.finished_at_ms - self.started_at_ms:
            raise ValueError("Hermes ACP result duration differs from timestamps")
        if self.thought_event_count < 0:
            raise ValueError("Hermes ACP thought event count must be non-negative")
        validate_json_value(self.usage)
        validate_json_value(self.update_type_counts)
        for item in self.tool_items:
            validate_json_value(item)

    @property
    def normalized_event_digest(self) -> str:
        return canonical_digest([event.to_dict() for event in self.normalized_events])

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.hermes-acp-prompt-result",
            "sessionId": self.session.session_id,
            "protocolVersion": self.session.protocol_version,
            "agentName": self.session.agent_name,
            "agentVersion": self.session.agent_version,
            "modelId": self.session.model_id,
            "model": self.session.model,
            "modelProvider": self.session.model_provider,
            "cwd": self.session.cwd,
            "loadSession": self.session.load_session,
            "sessionResume": self.session.session_resume,
            "sessionFork": self.session.session_fork,
            "images": self.session.images,
            "provenance": self.session.provenance,
            "provenanceDigest": self.session.provenance_digest,
            "requestId": self.request_id,
            "status": self.status,
            "stopReason": self.stop_reason,
            "providerStopReason": self.provider_stop_reason,
            "assistantText": self.assistant_text,
            "startedAtMs": self.started_at_ms,
            "finishedAtMs": self.finished_at_ms,
            "durationMs": self.duration_ms,
            "usage": self.usage,
            "rawMessageDigest": self.raw_message_digest,
            "rawMessageCount": self.raw_message_count,
            "normalizedEventDigest": self.normalized_event_digest,
            "normalizedEvents": [event.to_dict() for event in self.normalized_events],
            "updateTypeCounts": self.update_type_counts,
            "toolItems": list(self.tool_items),
            "thoughtEventCount": self.thought_event_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HermesACPPromptResult:
        if (
            value.get("schemaVersion") != 1
            or value.get("kind") != "ordivon.hermes-acp-prompt-result"
        ):
            raise ValueError("HermesACPPromptResult version or kind differs")
        required_strings = (
            "sessionId",
            "agentName",
            "agentVersion",
            "modelId",
            "model",
            "modelProvider",
            "cwd",
            "provenanceDigest",
            "status",
            "stopReason",
            "providerStopReason",
            "assistantText",
            "rawMessageDigest",
            "normalizedEventDigest",
        )
        if any(not isinstance(value.get(field), str) for field in required_strings):
            raise ValueError("HermesACPPromptResult string fields are invalid")
        for field in (
            "protocolVersion",
            "requestId",
            "startedAtMs",
            "finishedAtMs",
            "durationMs",
            "rawMessageCount",
            "thoughtEventCount",
        ):
            if type(value.get(field)) is not int:
                raise ValueError(f"HermesACPPromptResult {field} must be an integer")
        for field in ("loadSession", "sessionResume", "sessionFork", "images"):
            if type(value.get(field)) is not bool:
                raise ValueError(f"HermesACPPromptResult {field} must be boolean")
        provenance = value.get("provenance")
        usage = value.get("usage")
        update_counts = value.get("updateTypeCounts")
        events = value.get("normalizedEvents")
        tools = value.get("toolItems")
        if not all(isinstance(item, dict) for item in (provenance, usage, update_counts)):
            raise ValueError("HermesACPPromptResult object fields are invalid")
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise ValueError("HermesACPPromptResult normalized events are invalid")
        if not isinstance(tools, list) or any(not isinstance(item, dict) for item in tools):
            raise ValueError("HermesACPPromptResult Tool items are invalid")
        normalized: list[HermesACPNormalizedEvent] = []
        for event in events:
            for field in ("kind", "method", "sessionId", "payloadDigest"):
                if not isinstance(event.get(field), str):
                    raise ValueError("Hermes ACP normalized event fields are invalid")
            if type(event.get("observedAtMs")) is not int:
                raise ValueError("Hermes ACP normalized event time is invalid")
            normalized.append(
                HermesACPNormalizedEvent(
                    kind=event["kind"],
                    method=event["method"],
                    update_type=_optional_string(event.get("updateType"), "update type"),
                    observed_at_ms=event["observedAtMs"],
                    session_id=event["sessionId"],
                    tool_call_id=_optional_string(
                        event.get("toolCallId"), "Tool Call identity"
                    ),
                    tool_kind=_optional_string(event.get("toolKind"), "Tool kind"),
                    payload_digest=event["payloadDigest"],
                )
            )
        provenance_value = dict(provenance)
        if canonical_digest(provenance_value) != value["provenanceDigest"]:
            raise ValueError("Hermes ACP provenance digest differs")
        result = cls(
            session=HermesACPSession(
                session_id=value["sessionId"],
                protocol_version=value["protocolVersion"],
                agent_name=value["agentName"],
                agent_version=value["agentVersion"],
                model_id=value["modelId"],
                model=value["model"],
                model_provider=value["modelProvider"],
                cwd=value["cwd"],
                load_session=value["loadSession"],
                session_resume=value["sessionResume"],
                session_fork=value["sessionFork"],
                images=value["images"],
                provenance=provenance_value,
                provenance_digest=value["provenanceDigest"],
                message_start_index=0,
            ),
            request_id=value["requestId"],
            status=value["status"],
            stop_reason=value["stopReason"],
            provider_stop_reason=value["providerStopReason"],
            assistant_text=value["assistantText"],
            started_at_ms=value["startedAtMs"],
            finished_at_ms=value["finishedAtMs"],
            duration_ms=value["durationMs"],
            usage=dict(usage),
            raw_message_digest=value["rawMessageDigest"],
            raw_message_count=value["rawMessageCount"],
            normalized_events=tuple(normalized),
            update_type_counts={str(key): int(item) for key, item in update_counts.items()},
            tool_items=tuple(dict(item) for item in tools),
            thought_event_count=value["thoughtEventCount"],
            stderr_tail="",
        )
        if result.normalized_event_digest != value["normalizedEventDigest"]:
            raise ValueError("Hermes ACP normalized event digest differs")
        return result

    def to_harness_run_receipt(
        self,
        committed: CommittedHarnessAssignment,
        *,
        harness_run_id: str,
        runtime_job_refs: tuple[str, ...] = (),
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> HarnessRunReceipt:
        assignment = committed.assignment
        if assignment.target_harness_id != "harness:hermes-acp":
            raise ValueError("Harness Assignment does not target Hermes ACP")
        return HarnessRunReceipt(
            harness_run_id=harness_run_id,
            assignment_id=assignment.assignment_id,
            assignment_generation=assignment.generation,
            harness_id=assignment.target_harness_id,
            harness_revision=self.session.agent_version,
            manifest_digest=assignment.harness_manifest_digest,
            session_ref=f"hermes-acp-session:{self.session.session_id}",
            started_at_ms=self.started_at_ms,
            finished_at_ms=self.finished_at_ms,
            stop_reason=self.stop_reason,
            event_digest=self.raw_message_digest,
            context_digest=assignment.context_object_digest,
            tool_catalog_digest=assignment.tool_catalog_digest,
            runtime_job_refs=runtime_job_refs,
            artifact_refs=artifact_refs,
            usage={
                "provider": "hermes-acp",
                "model": self.session.model,
                "modelProvider": self.session.model_provider,
                "promptRequestId": self.request_id,
                "providerStopReason": self.provider_stop_reason,
                "tokenUsage": self.usage,
                "rawMessageCount": self.raw_message_count,
                "normalizedEventCount": len(self.normalized_events),
                "toolItemCount": len(self.tool_items),
                "thoughtEventCount": self.thought_event_count,
            },
        )


class HermesACPDriver:
    """Provider-faithful synchronous ACP v1 client for Hermes stdio."""

    def __init__(
        self,
        *,
        working_directory: str | Path,
        executable: str = "/root/.local/bin/hermes",
        acp_args: tuple[str, ...] = ("acp",),
        protocol_revision: str = "1",
        timeout_seconds: int = 240,
        environment: dict[str, str] | None = None,
        clock_ms: Callable[[], int] | None = None,
        max_messages: int = 30_000,
        max_line_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.working_directory = Path(working_directory)
        if not Path(executable).is_absolute():
            raise ValueError("Hermes ACP executable must be absolute")
        if timeout_seconds < 1:
            raise ValueError("Hermes ACP timeout must be positive")
        if max_messages < 1 or max_line_bytes < 1:
            raise ValueError("Hermes ACP message bounds must be positive")
        self.executable = executable
        self.acp_args = acp_args
        self.protocol_revision = _text(protocol_revision, "Hermes ACP revision")
        self.timeout_seconds = timeout_seconds
        self.environment = dict(environment or {})
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.max_messages = max_messages
        self.max_line_bytes = max_line_bytes
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: Queue[str | None] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=400)
        self._messages: list[dict[str, JsonValue]] = []
        self._updates: list[dict[str, JsonValue]] = []
        self._next_request_id = 1
        self._initialize_response: dict[str, JsonValue] | None = None
        self._closed = False

    def __enter__(self) -> HermesACPDriver:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr_lines)[-16_384:]

    def manifest(self) -> HarnessCapabilityManifest:
        capabilities = {}
        if self._initialize_response is not None:
            capabilities_value = self._initialize_response.get("agentCapabilities")
            if isinstance(capabilities_value, dict):
                capabilities = capabilities_value
        session_caps = capabilities.get("sessionCapabilities")
        if not isinstance(session_caps, dict):
            session_caps = {}
        prompt_caps = capabilities.get("promptCapabilities")
        if not isinstance(prompt_caps, dict):
            prompt_caps = {}
        initialized = self._initialize_response is not None
        return HarnessCapabilityManifest(
            harness_id="harness:hermes-acp",
            protocol="agent-client-protocol-jsonrpc-stdio",
            protocol_revision=self.protocol_revision,
            persistent_session=True,
            session_resume=(
                _bool_capability(session_caps.get("resume")) if initialized else True
            ),
            session_fork=(
                _bool_capability(session_caps.get("fork")) if initialized else True
            ),
            interrupt=True,
            tool_events=True,
            approval_events=True,
            usage=True,
            images=bool(prompt_caps.get("image")) if initialized else True,
            compaction=False,
            checkpoint=False,
            local_subagents=False,
            extensions=(
                "hermes.raw-provider-event-digest",
                "hermes.session-provenance",
                "hermes.thought-digest-only",
            ),
        )

    def start(self) -> dict[str, JsonValue]:
        if self._process is not None:
            if self._process.poll() is None:
                assert self._initialize_response is not None
                return dict(self._initialize_response)
            raise HermesACPExited("Hermes ACP process already exited")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(self.environment)
        try:
            self._process = subprocess.Popen(
                [self.executable, *self.acp_args],
                cwd=self.working_directory,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise HermesACPExited("failed to start Hermes ACP") from error
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            daemon=True,
            name="ordivon-hermes-acp-stdout",
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            daemon=True,
            name="ordivon-hermes-acp-stderr",
        ).start()
        result = self._request(
            "initialize",
            {
                "protocolVersion": int(self.protocol_revision),
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                    "auth": {"terminal": False},
                },
                "clientInfo": {
                    "name": "ordivon-host",
                    "title": "Ordivon Host",
                    "version": "0.1.0",
                },
            },
            timeout_seconds=min(self.timeout_seconds, 60),
        )
        if _integer(result.get("protocolVersion"), "ACP protocol version") != int(
            self.protocol_revision
        ):
            raise HermesACPProtocolError("Hermes ACP protocol revision differs")
        agent_info = _object(result.get("agentInfo"), "Hermes ACP agentInfo")
        _string(agent_info.get("name"), "Hermes ACP agent name")
        _string(agent_info.get("version"), "Hermes ACP agent version")
        self._initialize_response = dict(result)
        return dict(result)

    def start_session(self) -> HermesACPSession:
        initialize = self.start()
        message_start = len(self._messages)
        result = self._request(
            "session/new",
            {"cwd": str(self.working_directory), "mcpServers": []},
        )
        session_id = _string(result.get("sessionId"), "Hermes ACP Session identity")
        models = result.get("models")
        model_id = "unknown"
        if isinstance(models, dict) and isinstance(models.get("currentModelId"), str):
            model_id = models["currentModelId"]
        provider, model = _model_identity(model_id)
        capabilities = _object(
            initialize.get("agentCapabilities"), "Hermes ACP agent capabilities"
        )
        session_caps = capabilities.get("sessionCapabilities")
        if not isinstance(session_caps, dict):
            session_caps = {}
        prompt_caps = capabilities.get("promptCapabilities")
        if not isinstance(prompt_caps, dict):
            prompt_caps = {}
        agent_info = _object(initialize.get("agentInfo"), "Hermes ACP agentInfo")
        meta = result.get("_meta")
        provenance: dict[str, JsonValue] = {}
        if isinstance(meta, dict):
            hermes_meta = meta.get("hermes")
            if isinstance(hermes_meta, dict):
                candidate = hermes_meta.get("sessionProvenance")
                if isinstance(candidate, dict):
                    validate_json_value(candidate)
                    provenance = dict(candidate)
        return HermesACPSession(
            session_id=session_id,
            protocol_version=_integer(
                initialize.get("protocolVersion"), "Hermes ACP protocol version"
            ),
            agent_name=_string(agent_info.get("name"), "Hermes ACP agent name"),
            agent_version=_string(
                agent_info.get("version"), "Hermes ACP agent version"
            ),
            model_id=model_id,
            model=model,
            model_provider=provider,
            cwd=str(self.working_directory),
            load_session=bool(capabilities.get("loadSession")),
            session_resume=_bool_capability(session_caps.get("resume")),
            session_fork=_bool_capability(session_caps.get("fork")),
            images=bool(prompt_caps.get("image")),
            provenance=provenance,
            provenance_digest=canonical_digest(provenance),
            message_start_index=message_start,
        )

    def set_session_mode(
        self,
        session_id: str,
        mode_id: str,
    ) -> dict[str, JsonValue]:
        _text(session_id, "Hermes ACP Session identity")
        _text(mode_id, "Hermes ACP Session mode")
        return self._request(
            "session/set_mode",
            {"sessionId": session_id, "modeId": mode_id},
        )

    def start_prompt(self, session: HermesACPSession, prompt: str) -> HermesACPPromptHandle:
        _text(prompt, "Hermes ACP prompt")
        request_id = self._next_request_id
        self._next_request_id += 1
        started_at_ms = self.clock_ms()
        params: dict[str, JsonValue] = {
            "sessionId": session.session_id,
            "prompt": [{"type": "text", "text": prompt}],
        }
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/prompt",
                "params": params,
            }
        )
        return HermesACPPromptHandle(
            session=session,
            request_id=request_id,
            started_at_ms=started_at_ms,
            message_start_index=session.message_start_index,
            update_start_index=len(self._updates),
            prompt_digest=canonical_digest(params),
        )

    def wait_prompt(
        self,
        handle: HermesACPPromptHandle,
        *,
        timeout_seconds: int | None = None,
    ) -> HermesACPPromptResult:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout < 1:
            raise ValueError("Hermes ACP wait timeout must be positive")
        result = self._wait_response(handle.request_id, timeout, "session/prompt")
        return self._build_result(handle, result)

    def run_prompt(self, prompt: str) -> HermesACPPromptResult:
        session = self.start_session()
        return self.wait_prompt(self.start_prompt(session, prompt))

    def cancel(self, session_id: str) -> None:
        _text(session_id, "Hermes ACP Session identity")
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": session_id},
            }
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def _request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, JsonValue]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        return self._wait_response(request_id, timeout, method)

    def _wait_response(
        self, request_id: int, timeout_seconds: int, method: str
    ) -> dict[str, JsonValue]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HermesACPTimeout(f"Hermes ACP request {method} timed out")
            message = self._receive_message(remaining)
            if message.get("id") != request_id or "method" in message:
                continue
            error = message.get("error")
            if error is not None:
                raise HermesACPProtocolError(
                    f"Hermes ACP request {method} failed: {error}"
                )
            return _object(message.get("result"), f"{method} result")

    def _send(self, message: dict[str, JsonValue]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise HermesACPExited("Hermes ACP is not running")
        validate_json_value(message)
        try:
            process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise HermesACPExited("Hermes ACP stdin closed") from error

    def _receive_message(self, timeout_seconds: float) -> dict[str, JsonValue]:
        try:
            raw = self._stdout_queue.get(timeout=timeout_seconds)
        except Empty as error:
            raise HermesACPTimeout("Hermes ACP produced no message") from error
        if raw is None:
            returncode = None if self._process is None else self._process.poll()
            raise HermesACPExited(
                f"Hermes ACP stdout closed with return code {returncode}: {self.stderr_tail}"
            )
        if len(raw.encode("utf-8")) > self.max_line_bytes:
            raise HermesACPProtocolError("Hermes ACP message exceeds bound")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise HermesACPProtocolError("Hermes ACP emitted invalid JSON") from error
        if not isinstance(parsed, dict):
            raise HermesACPProtocolError("Hermes ACP message must be an object")
        validate_json_value(parsed)
        message = dict(parsed)
        if message.get("jsonrpc") != "2.0":
            raise HermesACPProtocolError("Hermes ACP message is not JSON-RPC 2.0")
        if len(self._messages) >= self.max_messages:
            raise HermesACPProtocolError("Hermes ACP message bound exceeded")
        self._messages.append(message)
        method = message.get("method")
        if isinstance(method, str) and "id" in message:
            self._reject_server_request(message)
        if method == "session/update" and "id" not in message:
            self._updates.append(message)
        return message

    def _reject_server_request(self, message: dict[str, JsonValue]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if request_id is not None:
            try:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": "Ordivon H4 does not admit ACP client requests",
                        },
                    }
                )
            except HermesACPError:
                pass
        raise HermesACPProtocolError(
            f"unexpected Hermes ACP server request: {method}"
        )

    def _build_result(
        self,
        handle: HermesACPPromptHandle,
        response: dict[str, JsonValue],
    ) -> HermesACPPromptResult:
        provider_stop_reason = _string(
            response.get("stopReason"), "Hermes ACP stop reason"
        )
        status, stop_reason = {
            "end_turn": ("completed", "completed"),
            "cancelled": ("interrupted", "interrupted"),
            "max_tokens": ("failed", "failed"),
            "max_turn_requests": ("failed", "failed"),
            "refusal": ("failed", "failed"),
        }.get(provider_stop_reason, ("failed", "failed"))
        usage_value = response.get("usage")
        usage: dict[str, JsonValue] = {}
        if isinstance(usage_value, dict):
            validate_json_value(usage_value)
            usage = dict(usage_value)
        updates = self._updates[handle.update_start_index :]
        raw_messages = self._messages[handle.message_start_index :]
        assistant_parts: list[str] = []
        normalized: list[HermesACPNormalizedEvent] = [
            HermesACPNormalizedEvent(
                kind="run_started",
                method="session/prompt",
                update_type=None,
                observed_at_ms=handle.started_at_ms,
                session_id=handle.session.session_id,
                tool_call_id=None,
                tool_kind=None,
                payload_digest=handle.prompt_digest,
            )
        ]
        update_counts: Counter[str] = Counter()
        thought_count = 0
        tool_order: list[str] = []
        tools: dict[str, dict[str, JsonValue]] = {}
        for message in updates:
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            if params.get("sessionId") != handle.session.session_id:
                continue
            update = params.get("update")
            if not isinstance(update, dict):
                continue
            update_type = update.get("sessionUpdate")
            if not isinstance(update_type, str):
                update_type = "unknown"
            update_counts[update_type] += 1
            observed_at_ms = self.clock_ms()
            tool_call_id = (
                update.get("toolCallId")
                if isinstance(update.get("toolCallId"), str)
                else None
            )
            tool_kind = (
                update.get("kind") if isinstance(update.get("kind"), str) else None
            )
            kind: str | None = None
            if update_type == "agent_message_chunk":
                kind = "message_delta"
                content = update.get("content")
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    assistant_parts.append(content["text"])
            elif update_type == "agent_thought_chunk":
                kind = "thought_observed"
                thought_count += 1
            elif update_type == "tool_call":
                kind = "tool_started"
                if tool_call_id is not None:
                    if tool_call_id not in tools:
                        tool_order.append(tool_call_id)
                    tools[tool_call_id] = self._summarize_tool_update(update)
            elif update_type == "tool_call_update":
                status_value = update.get("status")
                kind = (
                    "tool_finished"
                    if status_value in {"completed", "failed"}
                    else "tool_updated"
                )
                if tool_call_id is not None:
                    if tool_call_id not in tools:
                        tool_order.append(tool_call_id)
                        tools[tool_call_id] = {
                            "id": tool_call_id,
                            "title": "unknown",
                        }
                    tools[tool_call_id].update(self._summarize_tool_update(update))
            elif update_type == "usage_update":
                kind = "usage_observed"
            if kind is not None:
                normalized.append(
                    HermesACPNormalizedEvent(
                        kind=kind,
                        method="session/update",
                        update_type=update_type,
                        observed_at_ms=observed_at_ms,
                        session_id=handle.session.session_id,
                        tool_call_id=tool_call_id,
                        tool_kind=tool_kind,
                        payload_digest=canonical_digest(update),
                    )
                )
        finished_at_ms = self.clock_ms()
        normalized.append(
            HermesACPNormalizedEvent(
                kind="run_stopped",
                method="session/prompt",
                update_type=None,
                observed_at_ms=finished_at_ms,
                session_id=handle.session.session_id,
                tool_call_id=None,
                tool_kind=None,
                payload_digest=canonical_digest(response),
            )
        )
        return HermesACPPromptResult(
            session=handle.session,
            request_id=handle.request_id,
            status=status,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
            assistant_text="".join(assistant_parts).strip(),
            started_at_ms=handle.started_at_ms,
            finished_at_ms=finished_at_ms,
            duration_ms=finished_at_ms - handle.started_at_ms,
            usage=usage,
            raw_message_digest=canonical_digest(raw_messages),
            raw_message_count=len(raw_messages),
            normalized_events=tuple(normalized),
            update_type_counts=dict(sorted(update_counts.items())),
            tool_items=tuple(tools[item] for item in tool_order),
            thought_event_count=thought_count,
            stderr_tail=self.stderr_tail,
        )

    @staticmethod
    def _summarize_tool_update(update: dict[str, Any]) -> dict[str, JsonValue]:
        tool_call_id = _string(update.get("toolCallId"), "Hermes Tool Call identity")
        summary: dict[str, JsonValue] = {"id": tool_call_id}
        for field in ("title", "kind", "status"):
            value = update.get(field)
            if isinstance(value, str):
                summary[field] = value
        locations = update.get("locations")
        if isinstance(locations, list):
            safe_locations: list[JsonValue] = []
            for location in locations:
                if not isinstance(location, dict):
                    continue
                path = location.get("path")
                if not isinstance(path, str):
                    continue
                safe_location: dict[str, JsonValue] = {"path": path}
                line = location.get("line")
                if type(line) is int:
                    safe_location["line"] = line
                safe_locations.append(safe_location)
            summary["locations"] = safe_locations
        raw_input = update.get("rawInput")
        if raw_input is not None:
            validate_json_value(raw_input)
            summary["rawInputDigest"] = canonical_digest(raw_input)
        raw_output = update.get("rawOutput")
        if raw_output is not None:
            validate_json_value(raw_output)
            summary["rawOutputDigest"] = canonical_digest(raw_output)
        content = update.get("content")
        if isinstance(content, list):
            content_kinds: Counter[str] = Counter()
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("type"), str):
                    content_kinds[item["type"]] += 1
            summary["contentKinds"] = dict(sorted(content_kinds.items()))
            summary["contentDigest"] = canonical_digest(content)
            summary["fileEditCount"] = content_kinds.get("diff", 0)
        return summary

    def _read_stdout(self, stream) -> None:
        try:
            for line in stream:
                self._stdout_queue.put(line.rstrip("\n"))
        finally:
            self._stdout_queue.put(None)

    def _read_stderr(self, stream) -> None:
        for line in stream:
            self._stderr_lines.append(line)
