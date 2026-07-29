from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .errors import (
    RuntimeErrorDetail,
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)

PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True, slots=True)
class McpTransportProfile:
    profile_id: str
    protocol_version: str
    stateful_sessions: bool
    server_initiated_requests: bool
    multi_message_sse: bool
    resumable_sse: bool


ORDIVON_STATELESS_MCP_PROFILE = McpTransportProfile(
    profile_id="ordivon.mcp-stateless-http.v1",
    protocol_version=PROTOCOL_VERSION,
    stateful_sessions=False,
    server_initiated_requests=False,
    multi_message_sse=False,
    resumable_sse=False,
)


ORDIVON_SESSION_MCP_PROFILE = McpTransportProfile(
    profile_id="ordivon.mcp-session-http.v1",
    protocol_version=PROTOCOL_VERSION,
    stateful_sessions=True,
    server_initiated_requests=False,
    multi_message_sse=False,
    resumable_sse=False,
)


def parse_http_response(content_type: str, body: bytes) -> dict[str, Any]:
    if not body:
        raise RuntimeProtocolError("MCP response body is empty")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeProtocolError("MCP response is not UTF-8") from error
    if "text/event-stream" in content_type.lower():
        events: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if not line:
                if current:
                    events.append("\n".join(current))
                    current = []
                continue
            if line.startswith("data:"):
                current.append(line[5:].lstrip())
        if current:
            events.append("\n".join(current))
        events = [event for event in events if event.strip()]
        if not events:
            raise RuntimeProtocolError("SSE response contained no non-empty data event")
        if len(events) != 1:
            raise RuntimeProtocolError(
                "Ordivon stateless MCP profile requires exactly one SSE data event"
            )
        text = events[0]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeProtocolError("MCP response is not valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeProtocolError("MCP response envelope must be an object")
    return value


class McpRuntimeClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 45.0,
        max_response_bytes: int = 2_097_152,
        client_name: str = "ordivon-host",
        client_version: str = "0.0.1",
        profile: McpTransportProfile = ORDIVON_SESSION_MCP_PROFILE,
    ) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Runtime MCP endpoint must be an absolute HTTP(S) URL")
        if not token:
            raise ValueError("Runtime bearer token must not be empty")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("Runtime timeout and response limit must be positive")
        if not client_name or not client_version:
            raise ValueError("MCP client identity is required")
        self.endpoint = endpoint
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        if profile.server_initiated_requests or profile.multi_message_sse or profile.resumable_sse:
            raise ValueError("McpRuntimeClient supports bounded request/response MCP only")
        self.client_name = client_name
        self.client_version = client_version
        self.profile = profile
        self._initialized: dict[str, Any] | None = None
        self._session_id: str | None = None
        self._request_id = 0
        self._request_id_lock = threading.Lock()

    def initialize(self) -> dict[str, Any]:
        if self._initialized is not None:
            return dict(self._initialized)
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.profile.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": self.client_name,
                    "version": self.client_version,
                },
            },
        )
        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict):
            raise RuntimeProtocolError("initialize omitted serverInfo")
        if result.get("protocolVersion") != self.profile.protocol_version:
            raise RuntimeProtocolError(
                "Runtime negotiated another MCP protocol version"
            )
        if self.profile.stateful_sessions and self._session_id is None:
            raise RuntimeProtocolError("Runtime initialize omitted MCP Session identity")
        self._notify_initialized()
        self._initialized = dict(result)
        return dict(result)

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        result = self.request("tools/list", {})
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise RuntimeProtocolError("tools/list omitted the Tool array")
        tools: list[dict[str, Any]] = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise RuntimeProtocolError("Tool catalog contains a non-object descriptor")
            tools.append(dict(raw))
        return tuple(tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not name or not isinstance(arguments, dict):
            raise ValueError("Tool name and argument object are required")
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        structured = result.get("structuredContent")
        if result.get("isError") is True:
            raw_error = structured.get("error") if isinstance(structured, dict) else None
            if not isinstance(raw_error, dict):
                raise RuntimeProtocolError(
                    f"Tool {name} returned an unstructured error"
                )
            raise RuntimeToolRejected(name, _error_detail(raw_error))
        if not isinstance(structured, dict):
            raise RuntimeProtocolError(
                f"Tool {name} returned no structuredContent object"
            )
        return structured

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_request_id()
        envelope: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            envelope["params"] = params
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                body = response.read(self.max_response_bytes + 1)
                content_type = response.headers.get("Content-Type", "")
                session_id = response.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace")
            raise RuntimeTransportError(f"HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeTransportError(str(error)) from error
        if len(body) > self.max_response_bytes:
            raise RuntimeProtocolError("MCP response exceeds the configured byte limit")
        if method == "initialize" and session_id is not None:
            if not session_id or session_id != session_id.strip():
                raise RuntimeProtocolError("Runtime returned an invalid MCP Session identity")
            self._session_id = session_id
        message = parse_http_response(content_type, body)
        if message.get("jsonrpc") != "2.0":
            raise RuntimeProtocolError("MCP response has an invalid JSON-RPC version")
        if message.get("id") != request_id:
            raise RuntimeProtocolError(
                f"MCP response id differs for {method}: {message.get('id')!r}"
            )
        if "error" in message:
            raise RuntimeProtocolError(f"MCP {method} failed: {message['error']!r}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeProtocolError(f"MCP {method} returned no object result")
        return result

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.profile.protocol_version,
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _notify_initialized(self) -> None:
        envelope = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                body = response.read(self.max_response_bytes + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace")
            raise RuntimeTransportError(
                f"MCP initialized notification HTTP {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeTransportError(str(error)) from error
        if status not in {200, 202, 204}:
            raise RuntimeProtocolError(
                f"MCP initialized notification returned HTTP {status}"
            )
        if len(body) > self.max_response_bytes:
            raise RuntimeProtocolError(
                "MCP initialized notification exceeds the configured byte limit"
            )

    def _next_request_id(self) -> int:
        with self._request_id_lock:
            self._request_id += 1
            return self._request_id


def _error_detail(raw: dict[str, Any]) -> RuntimeErrorDetail:
    def optional_string(name: str) -> str | None:
        value = raw.get(name)
        return value if isinstance(value, str) else None

    return RuntimeErrorDetail(
        code=str(raw.get("code", "TOOL_ERROR")),
        message=str(raw.get("message", "Runtime Tool failed")),
        field=optional_string("field"),
        retryable=raw.get("retryable") is True,
        retry_class=optional_string("retryClass"),
        commit_state=optional_string("commitState"),
        origin=optional_string("origin"),
        trace_id=optional_string("traceId"),
        raw=dict(raw),
    )
