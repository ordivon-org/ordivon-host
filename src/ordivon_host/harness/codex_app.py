from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
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


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerProtocolError(CodexAppServerError):
    pass


class CodexAppServerTimeout(CodexAppServerError):
    pass


class CodexAppServerExited(CodexAppServerError):
    pass


def _text(value: str, label: str, *, max_bytes: int = 100_000) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CodexAppServerProtocolError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CodexAppServerProtocolError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise CodexAppServerProtocolError(f"{label} must be an integer")
    return value


@dataclass(frozen=True, slots=True)
class CodexAppNormalizedEvent:
    kind: str
    method: str
    emitted_at_ms: int
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    item_type: str | None
    payload_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "method": self.method,
            "emittedAtMs": self.emitted_at_ms,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "itemId": self.item_id,
            "itemType": self.item_type,
            "payloadDigest": self.payload_digest,
        }


@dataclass(frozen=True, slots=True)
class CodexAppThread:
    thread_id: str
    session_id: str
    model: str
    model_provider: str
    cli_version: str
    cwd: str
    approval_policy: JsonValue
    sandbox: dict[str, JsonValue]
    ephemeral: bool


@dataclass(frozen=True, slots=True)
class CodexAppTurnHandle:
    thread: CodexAppThread
    turn_id: str
    message_start_index: int
    notification_start_index: int


@dataclass(frozen=True, slots=True)
class CodexAppTurnResult:
    thread: CodexAppThread
    turn_id: str
    status: str
    stop_reason: str
    assistant_text: str
    started_at_ms: int
    finished_at_ms: int
    duration_ms: int | None
    usage: dict[str, JsonValue]
    raw_message_digest: str
    raw_message_count: int
    normalized_events: tuple[CodexAppNormalizedEvent, ...]
    provider_method_counts: dict[str, int]
    item_type_counts: dict[str, int]
    tool_items: tuple[dict[str, JsonValue], ...]
    stderr_tail: str

    def __post_init__(self) -> None:
        if self.status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unsupported Codex turn status: {self.status}")
        if self.stop_reason not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unsupported Harness stop reason: {self.stop_reason}")
        if self.started_at_ms < 0 or self.finished_at_ms < self.started_at_ms:
            raise ValueError("Codex App Server result timestamps are invalid")
        validate_json_value(self.usage)
        validate_json_value(self.provider_method_counts)
        validate_json_value(self.item_type_counts)
        for item in self.tool_items:
            validate_json_value(item)

    @property
    def normalized_event_digest(self) -> str:
        return canonical_digest([event.to_dict() for event in self.normalized_events])

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.codex-app-turn-result",
            "threadId": self.thread.thread_id,
            "sessionId": self.thread.session_id,
            "turnId": self.turn_id,
            "model": self.thread.model,
            "modelProvider": self.thread.model_provider,
            "cliVersion": self.thread.cli_version,
            "cwd": self.thread.cwd,
            "approvalPolicy": self.thread.approval_policy,
            "sandbox": self.thread.sandbox,
            "ephemeral": self.thread.ephemeral,
            "status": self.status,
            "stopReason": self.stop_reason,
            "assistantText": self.assistant_text,
            "startedAtMs": self.started_at_ms,
            "finishedAtMs": self.finished_at_ms,
            "durationMs": self.duration_ms,
            "usage": self.usage,
            "rawMessageDigest": self.raw_message_digest,
            "rawMessageCount": self.raw_message_count,
            "normalizedEventDigest": self.normalized_event_digest,
            "normalizedEvents": [event.to_dict() for event in self.normalized_events],
            "providerMethodCounts": self.provider_method_counts,
            "itemTypeCounts": self.item_type_counts,
            "toolItems": list(self.tool_items),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CodexAppTurnResult:
        if value.get("schemaVersion") != 1 or value.get("kind") != "ordivon.codex-app-turn-result":
            raise ValueError("CodexAppTurnResult version or kind differs")
        required_strings = (
            "threadId",
            "sessionId",
            "turnId",
            "model",
            "modelProvider",
            "cliVersion",
            "cwd",
            "status",
            "stopReason",
            "assistantText",
            "rawMessageDigest",
            "normalizedEventDigest",
        )
        if any(not isinstance(value.get(field), str) for field in required_strings):
            raise ValueError("CodexAppTurnResult string fields are invalid")
        for field in ("startedAtMs", "finishedAtMs", "rawMessageCount"):
            if type(value.get(field)) is not int:
                raise ValueError(f"CodexAppTurnResult {field} must be an integer")
        duration = value.get("durationMs")
        if duration is not None and type(duration) is not int:
            raise ValueError("CodexAppTurnResult duration must be an integer or null")
        if type(value.get("ephemeral")) is not bool:
            raise ValueError("CodexAppTurnResult ephemeral must be boolean")
        sandbox = value.get("sandbox")
        usage = value.get("usage")
        method_counts = value.get("providerMethodCounts")
        item_counts = value.get("itemTypeCounts")
        events = value.get("normalizedEvents")
        tool_items = value.get("toolItems")
        if not all(isinstance(item, dict) for item in (sandbox, usage, method_counts, item_counts)):
            raise ValueError("CodexAppTurnResult object fields are invalid")
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise ValueError("CodexAppTurnResult normalized events are invalid")
        if not isinstance(tool_items, list) or any(not isinstance(item, dict) for item in tool_items):
            raise ValueError("CodexAppTurnResult Tool items are invalid")
        normalized: list[CodexAppNormalizedEvent] = []
        for event in events:
            event_strings = ("kind", "method", "payloadDigest")
            if any(not isinstance(event.get(field), str) for field in event_strings):
                raise ValueError("Codex App normalized event fields are invalid")
            if type(event.get("emittedAtMs")) is not int:
                raise ValueError("Codex App normalized event time is invalid")
            optional = {}
            for field in ("threadId", "turnId", "itemId", "itemType"):
                item = event.get(field)
                if item is not None and not isinstance(item, str):
                    raise ValueError(f"Codex App normalized event {field} is invalid")
                optional[field] = item
            normalized.append(
                CodexAppNormalizedEvent(
                    kind=event["kind"],
                    method=event["method"],
                    emitted_at_ms=event["emittedAtMs"],
                    thread_id=optional["threadId"],
                    turn_id=optional["turnId"],
                    item_id=optional["itemId"],
                    item_type=optional["itemType"],
                    payload_digest=event["payloadDigest"],
                )
            )
        result = cls(
            thread=CodexAppThread(
                thread_id=value["threadId"],
                session_id=value["sessionId"],
                model=value["model"],
                model_provider=value["modelProvider"],
                cli_version=value["cliVersion"],
                cwd=value["cwd"],
                approval_policy=value.get("approvalPolicy"),
                sandbox=dict(sandbox),
                ephemeral=value["ephemeral"],
            ),
            turn_id=value["turnId"],
            status=value["status"],
            stop_reason=value["stopReason"],
            assistant_text=value["assistantText"],
            started_at_ms=value["startedAtMs"],
            finished_at_ms=value["finishedAtMs"],
            duration_ms=duration,
            usage=dict(usage),
            raw_message_digest=value["rawMessageDigest"],
            raw_message_count=value["rawMessageCount"],
            normalized_events=tuple(normalized),
            provider_method_counts={str(key): int(item) for key, item in method_counts.items()},
            item_type_counts={str(key): int(item) for key, item in item_counts.items()},
            tool_items=tuple(dict(item) for item in tool_items),
            stderr_tail="",
        )
        if result.normalized_event_digest != value["normalizedEventDigest"]:
            raise ValueError("CodexAppTurnResult normalized event digest differs")
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
        if assignment.target_harness_id != "harness:codex-app-server":
            raise ValueError("Harness Assignment does not target Codex App Server")
        return HarnessRunReceipt(
            harness_run_id=harness_run_id,
            assignment_id=assignment.assignment_id,
            assignment_generation=assignment.generation,
            harness_id=assignment.target_harness_id,
            harness_revision=self.thread.cli_version,
            manifest_digest=assignment.harness_manifest_digest,
            session_ref=f"codex-thread:{self.thread.thread_id}",
            started_at_ms=self.started_at_ms,
            finished_at_ms=self.finished_at_ms,
            stop_reason=self.stop_reason,
            event_digest=self.raw_message_digest,
            context_digest=assignment.context_object_digest,
            tool_catalog_digest=assignment.tool_catalog_digest,
            runtime_job_refs=runtime_job_refs,
            artifact_refs=artifact_refs,
            usage={
                "provider": "codex-app-server",
                "model": self.thread.model,
                "modelProvider": self.thread.model_provider,
                "turnId": self.turn_id,
                "tokenUsage": self.usage,
                "rawMessageCount": self.raw_message_count,
                "normalizedEventCount": len(self.normalized_events),
                "toolItemCount": len(self.tool_items),
            },
        )


class CodexAppServerDriver:
    """Provider-faithful synchronous driver for Codex App Server v2 over stdio."""

    def __init__(
        self,
        *,
        working_directory: str | Path,
        executable: str = "/usr/bin/codex",
        app_server_args: tuple[str, ...] = (
            "app-server",
            "--stdio",
            "-c",
            "analytics.enabled=false",
        ),
        protocol_revision: str = "0.145.0",
        timeout_seconds: int = 180,
        model: str | None = None,
        approval_policy: str = "never",
        sandbox: str = "read-only",
        ephemeral: bool = True,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        clock_ms: Callable[[], int] | None = None,
        max_messages: int = 20_000,
        max_line_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.working_directory = Path(working_directory)
        if not Path(executable).is_absolute():
            raise ValueError("Codex App Server executable must be absolute")
        if timeout_seconds < 1:
            raise ValueError("Codex App Server timeout must be positive")
        if approval_policy not in {"never", "untrusted", "on-request"}:
            raise ValueError("unsupported Codex approval policy")
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("unsupported Codex sandbox")
        if max_messages < 1 or max_line_bytes < 1:
            raise ValueError("Codex App Server bounds must be positive")
        self.executable = executable
        self.app_server_args = app_server_args
        self.protocol_revision = _text(protocol_revision, "Codex protocol revision")
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.approval_policy = approval_policy
        self.sandbox = sandbox
        self.ephemeral = ephemeral
        self.base_instructions = base_instructions
        self.developer_instructions = developer_instructions
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.max_messages = max_messages
        self.max_line_bytes = max_line_bytes
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: Queue[str | None] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=200)
        self._messages: list[dict[str, JsonValue]] = []
        self._notifications: list[dict[str, JsonValue]] = []
        self._next_request_id = 1
        self._initialize_response: dict[str, JsonValue] | None = None
        self._closed = False

    def __enter__(self) -> CodexAppServerDriver:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def manifest(self) -> HarnessCapabilityManifest:
        return HarnessCapabilityManifest(
            harness_id="harness:codex-app-server",
            protocol="codex-app-server-v2-stdio",
            protocol_revision=self.protocol_revision,
            persistent_session=True,
            session_resume=True,
            session_fork=True,
            interrupt=True,
            tool_events=True,
            approval_events=True,
            usage=True,
            images=True,
            compaction=True,
            checkpoint=False,
            local_subagents=False,
            extensions=("codex.raw-provider-event-digest",),
        )

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr_lines)[-8_192:]

    def start(self) -> dict[str, JsonValue]:
        if self._process is not None:
            if self._process.poll() is None:
                assert self._initialize_response is not None
                return dict(self._initialize_response)
            raise CodexAppServerExited("Codex App Server process already exited")
        self.working_directory.mkdir(parents=True, exist_ok=True)
        try:
            self._process = subprocess.Popen(
                [self.executable, *self.app_server_args],
                cwd=self.working_directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise CodexAppServerExited("failed to start Codex App Server") from error
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            daemon=True,
            name="ordivon-codex-app-stdout",
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            daemon=True,
            name="ordivon-codex-app-stderr",
        ).start()
        result = self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "ordivon-host",
                    "title": "Ordivon Host",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": False,
                    "requestAttestation": False,
                    "optOutNotificationMethods": [],
                },
            },
            timeout_seconds=min(self.timeout_seconds, 30),
        )
        _string(result.get("userAgent"), "initialize userAgent")
        _string(result.get("codexHome"), "initialize codexHome")
        self._send({"method": "initialized"})
        self._initialize_response = dict(result)
        return dict(result)

    def start_thread(self) -> CodexAppThread:
        self.start()
        message_start = len(self._messages)
        params: dict[str, JsonValue] = {
            "cwd": str(self.working_directory),
            "approvalPolicy": self.approval_policy,
            "sandbox": self.sandbox,
            "ephemeral": self.ephemeral,
            "threadSource": "appServer",
        }
        if self.model is not None:
            params["model"] = self.model
        if self.base_instructions is not None:
            params["baseInstructions"] = self.base_instructions
        if self.developer_instructions is not None:
            params["developerInstructions"] = self.developer_instructions
        result = self._request("thread/start", params)
        thread_value = _object(result.get("thread"), "thread/start thread")
        thread = CodexAppThread(
            thread_id=_string(thread_value.get("id"), "Codex thread ID"),
            session_id=_string(thread_value.get("sessionId"), "Codex session ID"),
            model=_string(result.get("model"), "Codex model"),
            model_provider=_string(result.get("modelProvider"), "Codex model provider"),
            cli_version=_string(thread_value.get("cliVersion"), "Codex CLI version"),
            cwd=_string(result.get("cwd"), "Codex thread cwd"),
            approval_policy=result.get("approvalPolicy"),
            sandbox=_object(result.get("sandbox"), "Codex sandbox"),
            ephemeral=bool(thread_value.get("ephemeral")),
        )
        setattr(self, f"_thread_message_start_{thread.thread_id}", message_start)
        return thread

    def start_turn(
        self,
        thread: CodexAppThread,
        prompt: str,
        *,
        output_schema: dict[str, JsonValue] | None = None,
    ) -> CodexAppTurnHandle:
        _text(prompt, "Codex turn prompt")
        if output_schema is not None:
            validate_json_value(output_schema)
        notification_start = len(self._notifications)
        message_start = getattr(
            self,
            f"_thread_message_start_{thread.thread_id}",
            len(self._messages),
        )
        params: dict[str, JsonValue] = {
            "threadId": thread.thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        result = self._request("turn/start", params)
        turn = _object(result.get("turn"), "turn/start turn")
        turn_id = _string(turn.get("id"), "Codex turn ID")
        return CodexAppTurnHandle(
            thread=thread,
            turn_id=turn_id,
            message_start_index=message_start,
            notification_start_index=notification_start,
        )

    def wait_turn(
        self,
        handle: CodexAppTurnHandle,
        *,
        timeout_seconds: int | None = None,
    ) -> CodexAppTurnResult:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout < 1:
            raise ValueError("Codex wait timeout must be positive")
        deadline = time.monotonic() + timeout
        terminal: dict[str, JsonValue] | None = self._find_terminal_notification(handle)
        while terminal is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerTimeout(
                    f"Codex turn {handle.turn_id} did not complete within {timeout} seconds"
                )
            self._receive_message(remaining)
            terminal = self._find_terminal_notification(handle)
        return self._build_result(handle, terminal)

    def run_turn(
        self,
        prompt: str,
        *,
        output_schema: dict[str, JsonValue] | None = None,
    ) -> CodexAppTurnResult:
        thread = self.start_thread()
        handle = self.start_turn(thread, prompt, output_schema=output_schema)
        return self.wait_turn(handle)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        _text(thread_id, "Codex thread ID")
        _text(turn_id, "Codex turn ID")
        self._request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout_seconds=min(self.timeout_seconds, 30),
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
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
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
        self._send({"id": request_id, "method": method, "params": params})
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexAppServerTimeout(
                    f"Codex App Server request {method} timed out"
                )
            message = self._receive_message(remaining)
            if message.get("id") != request_id:
                continue
            if "method" in message:
                self._reject_server_request(message)
            error = message.get("error")
            if error is not None:
                raise CodexAppServerProtocolError(
                    f"Codex App Server request {method} failed: {error}"
                )
            return _object(message.get("result"), f"{method} result")

    def _send(self, message: dict[str, JsonValue]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexAppServerExited("Codex App Server is not running")
        validate_json_value(message)
        try:
            process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise CodexAppServerExited("Codex App Server stdin closed") from error

    def _receive_message(self, timeout_seconds: float) -> dict[str, JsonValue]:
        try:
            raw = self._stdout_queue.get(timeout=timeout_seconds)
        except Empty as error:
            raise CodexAppServerTimeout("Codex App Server produced no message") from error
        if raw is None:
            returncode = None if self._process is None else self._process.poll()
            raise CodexAppServerExited(
                f"Codex App Server stdout closed with return code {returncode}: {self.stderr_tail}"
            )
        if len(raw.encode("utf-8")) > self.max_line_bytes:
            raise CodexAppServerProtocolError("Codex App Server message exceeds bound")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CodexAppServerProtocolError(
                "Codex App Server emitted invalid JSON"
            ) from error
        if not isinstance(parsed, dict):
            raise CodexAppServerProtocolError("Codex App Server message must be an object")
        validate_json_value(parsed)
        message = dict(parsed)
        if len(self._messages) >= self.max_messages:
            raise CodexAppServerProtocolError("Codex App Server message bound exceeded")
        self._messages.append(message)
        method = message.get("method")
        if isinstance(method, str) and "id" not in message:
            self._notifications.append(message)
        elif isinstance(method, str) and "id" in message:
            self._reject_server_request(message)
        return message

    def _reject_server_request(self, message: dict[str, JsonValue]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if request_id is not None:
            try:
                self._send(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": "Ordivon H3 does not admit Codex server requests",
                        },
                    }
                )
            except CodexAppServerError:
                pass
        raise CodexAppServerProtocolError(
            f"unexpected Codex server request: {method}"
        )

    def _find_terminal_notification(
        self, handle: CodexAppTurnHandle
    ) -> dict[str, JsonValue] | None:
        for message in self._notifications[handle.notification_start_index :]:
            if message.get("method") != "turn/completed":
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            turn = params.get("turn")
            if isinstance(turn, dict) and turn.get("id") == handle.turn_id:
                return message
        return None

    def _build_result(
        self,
        handle: CodexAppTurnHandle,
        terminal_message: dict[str, JsonValue],
    ) -> CodexAppTurnResult:
        raw_messages = self._messages[handle.message_start_index :]
        notifications = self._notifications[handle.notification_start_index :]
        terminal_params = _object(terminal_message.get("params"), "turn/completed params")
        terminal_turn = _object(terminal_params.get("turn"), "turn/completed turn")
        status = _string(terminal_turn.get("status"), "Codex turn status")
        if status not in {"completed", "interrupted", "failed"}:
            raise CodexAppServerProtocolError(
                f"Codex turn completed with unsupported status {status}"
            )
        assistant_text = ""
        usage: dict[str, JsonValue] = {}
        started_at_ms: int | None = None
        finished_at_ms: int | None = None
        normalized: list[CodexAppNormalizedEvent] = []
        provider_methods: Counter[str] = Counter()
        item_types: Counter[str] = Counter()
        tool_items: list[dict[str, JsonValue]] = []
        tool_types = {
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "dynamicToolCall",
            "collabAgentToolCall",
        }
        for message in notifications:
            method_value = message.get("method")
            if not isinstance(method_value, str):
                continue
            provider_methods[method_value] += 1
            params = message.get("params")
            if not isinstance(params, dict):
                params = {}
            emitted_at = message.get("emittedAtMs")
            emitted_at_ms = emitted_at if type(emitted_at) is int else self.clock_ms()
            thread_id = params.get("threadId") if isinstance(params.get("threadId"), str) else None
            turn_id = params.get("turnId") if isinstance(params.get("turnId"), str) else None
            item = params.get("item") if isinstance(params.get("item"), dict) else None
            item_id = item.get("id") if item and isinstance(item.get("id"), str) else None
            item_type = item.get("type") if item and isinstance(item.get("type"), str) else None
            if item_type is not None:
                item_types[item_type] += 1
            kind: str | None = None
            if method_value == "turn/started":
                kind = "run_started"
                started_at_ms = emitted_at_ms
            elif method_value == "item/agentMessage/delta":
                kind = "message_delta"
            elif method_value == "item/started" and item_type in tool_types:
                kind = "tool_started"
            elif method_value == "item/completed" and item_type in tool_types:
                kind = "tool_finished"
                assert item is not None
                tool_items.append(self._summarize_tool_item(item))
            elif method_value == "thread/tokenUsage/updated":
                kind = "usage_observed"
                token_usage = params.get("tokenUsage")
                if isinstance(token_usage, dict):
                    validate_json_value(token_usage)
                    usage = dict(token_usage)
            elif method_value == "turn/completed":
                kind = "run_stopped"
                finished_at_ms = emitted_at_ms
            if method_value == "item/completed" and item_type == "agentMessage":
                assert item is not None
                text = item.get("text")
                if isinstance(text, str):
                    assistant_text = text
            if kind is not None:
                normalized.append(
                    CodexAppNormalizedEvent(
                        kind=kind,
                        method=method_value,
                        emitted_at_ms=emitted_at_ms,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_id=item_id,
                        item_type=item_type,
                        payload_digest=canonical_digest(params),
                    )
                )
        if started_at_ms is None:
            started_at = terminal_turn.get("startedAt")
            started_at_ms = (
                _integer(started_at, "Codex turn startedAt") * 1_000
                if started_at is not None
                else self.clock_ms()
            )
        if finished_at_ms is None:
            completed_at = terminal_turn.get("completedAt")
            finished_at_ms = (
                _integer(completed_at, "Codex turn completedAt") * 1_000
                if completed_at is not None
                else self.clock_ms()
            )
        duration = terminal_turn.get("durationMs")
        duration_ms = duration if type(duration) is int else None
        raw_digest = canonical_digest(raw_messages)
        return CodexAppTurnResult(
            thread=handle.thread,
            turn_id=handle.turn_id,
            status=status,
            stop_reason=status,
            assistant_text=assistant_text,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
            duration_ms=duration_ms,
            usage=usage,
            raw_message_digest=raw_digest,
            raw_message_count=len(raw_messages),
            normalized_events=tuple(normalized),
            provider_method_counts=dict(sorted(provider_methods.items())),
            item_type_counts=dict(sorted(item_types.items())),
            tool_items=tuple(tool_items),
            stderr_tail=self.stderr_tail,
        )

    @staticmethod
    def _summarize_tool_item(item: dict[str, Any]) -> dict[str, JsonValue]:
        item_type = _string(item.get("type"), "Codex tool item type")
        summary: dict[str, JsonValue] = {
            "type": item_type,
            "id": _string(item.get("id"), "Codex tool item ID"),
        }
        for field in (
            "command",
            "cwd",
            "status",
            "exitCode",
            "durationMs",
            "server",
            "tool",
            "namespace",
            "success",
        ):
            value = item.get(field)
            if value is not None and isinstance(value, (str, int, bool)):
                summary[field] = value
        changes = item.get("changes")
        if isinstance(changes, list):
            summary["changeCount"] = len(changes)
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
