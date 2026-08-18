from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .mcp_errors import (
    McpErrorDetail,
    McpProtocolError,
    McpToolRejected,
    McpTransportError,
)

MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-06-18"
# Public compatibility constant retained for callers that explicitly selected
# the original Host transport. New code should use DEFAULT_PROTOCOL_VERSION.
PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSION
DEFAULT_PROTOCOL_VERSION = MODERN_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class McpTransportProfile:
    profile_id: str
    protocol_version: str
    stateful_sessions: bool
    server_initiated_requests: bool
    multi_message_sse: bool
    resumable_sse: bool


ORDIVON_MODERN_MCP_PROFILE = McpTransportProfile(
    profile_id="ordivon.mcp-modern-http.v1",
    protocol_version=MODERN_PROTOCOL_VERSION,
    stateful_sessions=False,
    server_initiated_requests=False,
    multi_message_sse=False,
    resumable_sse=False,
)

ORDIVON_LEGACY_STATELESS_MCP_PROFILE = McpTransportProfile(
    profile_id="ordivon.mcp-legacy-stateless-http.v1",
    protocol_version=LEGACY_PROTOCOL_VERSION,
    stateful_sessions=False,
    server_initiated_requests=False,
    multi_message_sse=False,
    resumable_sse=False,
)

ORDIVON_LEGACY_SESSION_MCP_PROFILE = McpTransportProfile(
    profile_id="ordivon.mcp-legacy-session-http.v1",
    protocol_version=LEGACY_PROTOCOL_VERSION,
    stateful_sessions=True,
    server_initiated_requests=False,
    multi_message_sse=False,
    resumable_sse=False,
)

# Compatibility exports retain their original 2025-06-18 semantics. The client
# default is modern through ORDIVON_MODERN_MCP_PROFILE, not through alias mutation.
ORDIVON_STATELESS_MCP_PROFILE = ORDIVON_LEGACY_STATELESS_MCP_PROFILE
ORDIVON_SESSION_MCP_PROFILE = ORDIVON_LEGACY_SESSION_MCP_PROFILE


def parse_http_response(content_type: str, body: bytes) -> dict[str, Any]:
    if not body:
        raise McpProtocolError("MCP response body is empty")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise McpProtocolError("MCP response is not UTF-8") from error
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
            raise McpProtocolError("SSE response contained no non-empty data event")
        if len(events) != 1:
            raise McpProtocolError(
                "Ordivon bounded MCP profile requires exactly one SSE data event"
            )
        text = events[0]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise McpProtocolError("MCP response is not valid JSON") from error
    if not isinstance(value, dict):
        raise McpProtocolError("MCP response envelope must be an object")
    return value


class McpTestClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 45.0,
        max_response_bytes: int = 2_097_152,
        client_name: str = "ordivon-host-test",
        client_version: str = "0.2.0",
        profile: McpTransportProfile = ORDIVON_MODERN_MCP_PROFILE,
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
        if profile.server_initiated_requests or profile.multi_message_sse or profile.resumable_sse:
            raise ValueError("McpTestClient supports bounded request/response MCP only")
        if profile.protocol_version == MODERN_PROTOCOL_VERSION and profile.stateful_sessions:
            raise ValueError("modern MCP profile cannot require transport Sessions")
        self.endpoint = endpoint
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.client_name = client_name
        self.client_version = client_version
        self.profile = profile
        self._initialized: dict[str, Any] | None = None
        self._session_id: str | None = None
        self._request_id = 0
        self._request_id_lock = threading.Lock()

    @property
    def modern(self) -> bool:
        return self.profile.protocol_version == MODERN_PROTOCOL_VERSION

    def initialize(self) -> dict[str, Any]:
        if self._initialized is not None:
            return dict(self._initialized)
        if self.modern:
            discovered = self.request("server/discover", {})
            supported = discovered.get("supportedVersions")
            if not isinstance(supported, list) or not all(
                isinstance(version, str) for version in supported
            ):
                raise McpProtocolError("server/discover omitted supportedVersions")
            if self.profile.protocol_version not in supported:
                raise McpProtocolError(
                    "Runtime discovery does not support the requested MCP protocol version"
                )
            metadata = discovered.get("_meta")
            if not isinstance(metadata, dict):
                raise McpProtocolError("server/discover omitted response metadata")
            server_info = metadata.get("io.modelcontextprotocol/serverInfo")
            if not isinstance(server_info, dict):
                raise McpProtocolError("server/discover omitted serverInfo metadata")
            if self._session_id is not None:
                raise McpProtocolError("modern Runtime discovery created a Session")
            normalized = dict(discovered)
            normalized["protocolVersion"] = self.profile.protocol_version
            normalized["serverInfo"] = dict(server_info)
            self._initialized = normalized
            return dict(normalized)

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
            raise McpProtocolError("initialize omitted serverInfo")
        if result.get("protocolVersion") != self.profile.protocol_version:
            raise McpProtocolError("Runtime negotiated another MCP protocol version")
        if self.profile.stateful_sessions and self._session_id is None:
            raise McpProtocolError("Runtime initialize omitted MCP Session identity")
        self._notify_initialized()
        self._initialized = dict(result)
        return dict(result)

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        result = self.request("tools/list", {})
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise McpProtocolError("tools/list omitted the Tool array")
        tools: list[dict[str, Any]] = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise McpProtocolError("Tool catalog contains a non-object descriptor")
            tools.append(dict(raw))
        return tuple(tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not name or not isinstance(arguments, dict):
            raise ValueError("Tool name and argument object are required")
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        structured = result.get("structuredContent")
        if result.get("isError") is True:
            raw_error = structured.get("error") if isinstance(structured, dict) else None
            if not isinstance(raw_error, dict):
                raise McpProtocolError(f"Tool {name} returned an unstructured error")
            raise McpToolRejected(name, _error_detail(raw_error))
        if not isinstance(structured, dict):
            raise McpProtocolError(f"Tool {name} returned no structuredContent object")
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
        request_params = dict(params or {})
        if self.modern:
            request_params["_meta"] = self._metadata()
        if request_params:
            envelope["params"] = request_params
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            method="POST",
            headers=self._headers(method, request_params),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
                content_type = response.headers.get("Content-Type", "")
                session_id = response.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace")
            raise McpTransportError(f"HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise McpTransportError(str(error)) from error
        if len(body) > self.max_response_bytes:
            raise McpProtocolError("MCP response exceeds the configured byte limit")
        if self.modern and session_id is not None:
            raise McpProtocolError("modern Runtime response unexpectedly created a Session")
        if not self.modern and method == "initialize" and session_id is not None:
            if not session_id or session_id != session_id.strip():
                raise McpProtocolError("Runtime returned an invalid MCP Session identity")
            self._session_id = session_id
        message = parse_http_response(content_type, body)
        if message.get("jsonrpc") != "2.0":
            raise McpProtocolError("MCP response has an invalid JSON-RPC version")
        if message.get("id") != request_id:
            raise McpProtocolError(
                f"MCP response id differs for {method}: {message.get('id')!r}"
            )
        if "error" in message:
            raise McpProtocolError(f"MCP {method} failed: {message['error']!r}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(f"MCP {method} returned no object result")
        return result

    def _metadata(self) -> dict[str, Any]:
        return {
            "io.modelcontextprotocol/protocolVersion": self.profile.protocol_version,
            "io.modelcontextprotocol/clientInfo": {
                "name": self.client_name,
                "version": self.client_version,
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        }

    def _headers(self, method: str, params: dict[str, Any]) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.profile.protocol_version,
        }
        if self.modern:
            headers["Mcp-Method"] = method
            if method == "tools/call":
                name = params.get("name")
                if isinstance(name, str):
                    headers["Mcp-Name"] = name
        elif self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _notify_initialized(self) -> None:
        envelope = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            method="POST",
            headers=self._headers("notifications/initialized", {}),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace")
            raise McpTransportError(
                f"MCP initialized notification HTTP {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise McpTransportError(str(error)) from error
        if status not in {200, 202, 204}:
            raise McpProtocolError(
                f"MCP initialized notification returned HTTP {status}"
            )
        if len(body) > self.max_response_bytes:
            raise McpProtocolError(
                "MCP initialized notification exceeds the configured byte limit"
            )

    def _next_request_id(self) -> int:
        with self._request_id_lock:
            self._request_id += 1
            return self._request_id


def _error_detail(raw: dict[str, Any]) -> McpErrorDetail:
    def optional_string(name: str) -> str | None:
        value = raw.get(name)
        return value if isinstance(value, str) else None

    return McpErrorDetail(
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
