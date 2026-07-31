from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import stat
from typing import Protocol
import urllib.error
import urllib.request

from anc_canonical import JsonValue, canonical_bytes, loads_strict, validate_json_value

from .model import (
    AgentRunConclusion,
    AgentToolCall,
    AgentToolDefinition,
    AgentTurnAdapterError,
    AgentTurnFailureCode,
    AgentTurnRequest,
    AgentTurnResult,
)

DEFAULT_DEEPSEEK_SECRET_PATH = (
    Path.home() / ".config" / "ordivon" / "secrets" / "deepseek.json"
)
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
SUPPORTED_DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
_CONCLUSION_TOOL_NAME = "submit_run_conclusion"


def _text(value: str, label: str, *, max_bytes: int = 2_048) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


@dataclass(frozen=True, slots=True)
class DeepSeekSettings:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 90.0
    max_response_bytes: int = 4_194_304
    max_output_tokens: int = 8_192

    def __post_init__(self) -> None:
        _text(self.api_key, "DeepSeek API key", max_bytes=16_384)
        if any(character.isspace() for character in self.api_key):
            raise ValueError("DeepSeek API key must not contain whitespace")
        if self.base_url != DEFAULT_DEEPSEEK_BASE_URL:
            raise ValueError("DeepSeek base URL must use the official stable endpoint")
        if self.model not in SUPPORTED_DEEPSEEK_MODELS:
            raise ValueError(f"unsupported DeepSeek model: {self.model}")
        if (
            self.timeout_seconds <= 0
            or self.max_response_bytes < 1
            or self.max_output_tokens < 1
        ):
            raise ValueError("DeepSeek request bounds must be positive")

    @classmethod
    def from_secret_file(
        cls,
        path: str | Path = DEFAULT_DEEPSEEK_SECRET_PATH,
        *,
        timeout_seconds: float = 90.0,
        max_response_bytes: int = 4_194_304,
        max_output_tokens: int = 8_192,
    ) -> DeepSeekSettings:
        secret_path = Path(path).expanduser()
        if secret_path.is_symlink():
            raise PermissionError("DeepSeek secret file must not be a symbolic link")
        if not secret_path.is_file():
            raise FileNotFoundError(secret_path)
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        if mode & 0o077:
            raise PermissionError(
                f"DeepSeek secret permissions are too broad: {oct(mode)}; expected 0o600"
            )
        value = loads_strict(secret_path.read_bytes())
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "provider",
            "apiKey",
            "baseUrl",
            "model",
        }:
            raise ValueError("DeepSeek secret file fields differ")
        if value["schemaVersion"] != 1 or value["provider"] != "deepseek":
            raise ValueError("DeepSeek secret schema is unsupported")
        api_key = value["apiKey"]
        base_url = value["baseUrl"]
        model = value["model"]
        if not all(isinstance(item, str) for item in (api_key, base_url, model)):
            raise ValueError("DeepSeek secret values must be strings")
        assert isinstance(api_key, str)
        assert isinstance(base_url, str)
        assert isinstance(model, str)
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            max_output_tokens=max_output_tokens,
        )


class DeepSeekTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes: ...


class UrllibDeepSeekTransport:
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            detail = error.read(8_192).decode("utf-8", errors="replace")
            if error.code in {408, 504}:
                failure_code = AgentTurnFailureCode.TIMEOUT
            elif error.code == 429 or error.code >= 500:
                failure_code = AgentTurnFailureCode.UNAVAILABLE
            else:
                failure_code = AgentTurnFailureCode.REJECTED
            raise AgentTurnAdapterError(
                f"DeepSeek returned HTTP {error.code}: {detail}",
                failure_code=failure_code,
            ) from error
        except urllib.error.URLError as error:
            raise AgentTurnAdapterError(
                f"DeepSeek connection failed: {error.reason}",
                failure_code=AgentTurnFailureCode.TRANSPORT_FAILED,
            ) from error
        except TimeoutError as error:
            raise AgentTurnAdapterError(
                "DeepSeek request timed out",
                failure_code=AgentTurnFailureCode.TIMEOUT,
            ) from error
        if len(raw) > max_response_bytes:
            raise AgentTurnAdapterError("DeepSeek response exceeds the configured byte bound")
        return raw


def _conclusion_tool() -> dict[str, JsonValue]:
    string_array: dict[str, JsonValue] = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 128,
    }
    return {
        "type": "function",
        "function": {
            "name": _CONCLUSION_TOOL_NAME,
            "description": (
                "Stop this bounded Harness Run and submit a candidate result for independent "
                "Host verification. This does not complete the durable Task."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["candidate_completed", "needs_input"],
                    },
                    "summary": {"type": "string", "minLength": 1},
                    "artifact_refs": string_array,
                    "evidence_refs": string_array,
                    "unresolved_unknowns": string_array,
                },
                "required": [
                    "status",
                    "summary",
                    "artifact_refs",
                    "evidence_refs",
                    "unresolved_unknowns",
                ],
            },
        },
    }


def _provider_tool(tool: AgentToolDefinition) -> dict[str, JsonValue]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _provider_messages(
    messages: tuple[dict[str, JsonValue], ...],
) -> list[dict[str, JsonValue]]:
    translated: list[dict[str, JsonValue]] = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "user"}:
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError(f"DeepSeek {role} message content must be a string")
            translated.append({"role": role, "content": content})
            continue
        if role == "assistant":
            raw_calls = message.get("toolCalls")
            if not isinstance(raw_calls, list) or not raw_calls:
                raise ValueError("DeepSeek assistant history must retain Tool Calls")
            calls: list[dict[str, JsonValue]] = []
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict) or set(raw_call) != {
                    "toolCallId",
                    "name",
                    "arguments",
                }:
                    raise ValueError("DeepSeek assistant Tool Call history is invalid")
                call_id = raw_call["toolCallId"]
                name = raw_call["name"]
                arguments = raw_call["arguments"]
                if not isinstance(call_id, str) or not isinstance(name, str):
                    raise ValueError("DeepSeek assistant Tool Call identities are invalid")
                validate_json_value(arguments)
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": canonical_bytes(arguments).decode("utf-8"),
                        },
                    }
                )
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("DeepSeek assistant content must be a string or null")
            translated.append(
                {"role": "assistant", "content": content, "tool_calls": calls}
            )
            continue
        if role == "tool":
            tool_call_id = message.get("toolCallId")
            observation = message.get("observation")
            if not isinstance(tool_call_id, str):
                raise ValueError("DeepSeek Tool message has no Tool Call identity")
            validate_json_value(observation)
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": canonical_bytes(observation).decode("utf-8"),
                }
            )
            continue
        raise ValueError(f"unsupported DeepSeek message role: {role}")
    return translated


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"DeepSeek conclusion {label} must be a string array")
    return tuple(value)


def _parse_conclusion(arguments: dict[str, JsonValue]) -> AgentRunConclusion:
    expected = {
        "status",
        "summary",
        "artifact_refs",
        "evidence_refs",
        "unresolved_unknowns",
    }
    if set(arguments) != expected:
        raise ValueError("DeepSeek conclusion fields differ")
    status = arguments["status"]
    summary = arguments["summary"]
    if not isinstance(status, str) or not isinstance(summary, str):
        raise ValueError("DeepSeek conclusion status and summary must be strings")
    return AgentRunConclusion(
        status=status,
        summary=summary,
        artifact_refs=_string_tuple(arguments["artifact_refs"], "Artifact refs"),
        evidence_refs=_string_tuple(arguments["evidence_refs"], "evidence refs"),
        unresolved_unknowns=_string_tuple(
            arguments["unresolved_unknowns"], "unresolved unknowns"
        ),
    )


class DeepSeekTurnAdapter:
    adapter_id = "deepseek.chat-completions.non-thinking.v1"

    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        transport: DeepSeekTransport | None = None,
    ) -> None:
        self.settings = settings
        self.model_id = settings.model
        self.transport = transport or UrllibDeepSeekTransport()

    def invoke(self, request: AgentTurnRequest) -> AgentTurnResult:
        allowed_tool_names = {tool.name for tool in request.tools}
        if _CONCLUSION_TOOL_NAME in allowed_tool_names:
            raise ValueError("Runtime Tool catalog collides with the Harness conclusion Tool")
        tools = [_provider_tool(tool) for tool in request.tools]
        tools.append(_conclusion_tool())
        body_value: dict[str, JsonValue] = {
            "model": self.settings.model,
            "messages": _provider_messages(request.messages),
            "tools": tools,
            "tool_choice": "required",
            "thinking": {"type": "disabled"},
            "max_tokens": self.settings.max_output_tokens,
            "stream": False,
        }
        validate_json_value(body_value)
        body = canonical_bytes(body_value)
        raw = self.transport.post(
            f"{self.settings.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ordivon-harness-oh5/1",
            },
            body=body,
            timeout_seconds=self.settings.timeout_seconds,
            max_response_bytes=self.settings.max_response_bytes,
        )
        try:
            value = loads_strict(raw)
        except ValueError as error:
            raise ValueError("DeepSeek returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("DeepSeek response must be an object")
        validate_json_value(value)
        return self._parse_response(value, raw, allowed_tool_names=allowed_tool_names)

    def _parse_response(
        self,
        value: dict[str, JsonValue],
        raw: bytes,
        *,
        allowed_tool_names: set[str],
    ) -> AgentTurnResult:
        response_id = value.get("id")
        choices = value.get("choices")
        response_model = value.get("model")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError("DeepSeek response omitted its Model Call identity")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
        ):
            raise ValueError("DeepSeek response must contain exactly one choice")
        if not isinstance(response_model, str) or not response_model:
            raise ValueError("DeepSeek response omitted its model identity")
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message")
        if finish_reason == "insufficient_system_resource":
            raise AgentTurnAdapterError(
                "DeepSeek reported insufficient system resources",
                failure_code=AgentTurnFailureCode.UNAVAILABLE,
            )
        if not isinstance(finish_reason, str) or not isinstance(message, dict):
            raise ValueError("DeepSeek choice fields are invalid")
        if message.get("role") != "assistant":
            raise ValueError("DeepSeek response role is not assistant")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("DeepSeek assistant content must be a string or null")
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ValueError("DeepSeek Turn must call a Runtime Tool or submit a conclusion")

        runtime_calls: list[AgentToolCall] = []
        conclusion: AgentRunConclusion | None = None
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
                raise ValueError("DeepSeek Tool Call is not a function")
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not isinstance(function, dict):
                raise ValueError("DeepSeek Tool Call identity or function is invalid")
            name = function.get("name")
            raw_arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(raw_arguments, str):
                raise ValueError("DeepSeek Tool Call name or arguments are invalid")
            try:
                arguments = loads_strict(raw_arguments.encode("utf-8"))
            except ValueError as error:
                raise ValueError(
                    f"DeepSeek Tool Call {name} arguments are invalid JSON"
                ) from error
            if not isinstance(arguments, dict):
                raise ValueError(f"DeepSeek Tool Call {name} arguments must be an object")
            validate_json_value(arguments)
            if name == _CONCLUSION_TOOL_NAME:
                if conclusion is not None:
                    raise ValueError("DeepSeek returned multiple Run conclusions")
                conclusion = _parse_conclusion(arguments)
            else:
                if name not in allowed_tool_names:
                    raise ValueError(f"DeepSeek called an unavailable Tool: {name}")
                runtime_calls.append(AgentToolCall(call_id, name, dict(arguments)))

        if conclusion is not None and runtime_calls:
            raise ValueError("DeepSeek mixed Runtime Tool Calls with a Run conclusion")
        if finish_reason != "tool_calls":
            raise ValueError("DeepSeek Tool Turn has an inconsistent finish reason")

        usage_value = value.get("usage")
        usage: dict[str, JsonValue] = {}
        if isinstance(usage_value, dict):
            validate_json_value(usage_value)
            usage.update(usage_value)
        fingerprint = value.get("system_fingerprint")
        if isinstance(fingerprint, str):
            usage["systemFingerprint"] = fingerprint
        usage["providerModel"] = response_model
        usage["providerRequestMode"] = "non-thinking"
        raw_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        return AgentTurnResult(
            model_call_id=response_id,
            model_id=self.model_id,
            content=content,
            tool_calls=tuple(runtime_calls),
            conclusion=conclusion,
            usage=usage,
            finish_reason=finish_reason,
            raw_response_digest=raw_digest,
        )
