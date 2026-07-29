from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any
import unittest

from ordivon_host.runtime import (
    McpRuntimeClient,
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] | None = None


def json_response(request: dict[str, Any], result: dict[str, Any]) -> Response:
    return Response(
        200,
        "application/json",
        json.dumps(
            {"jsonrpc": "2.0", "id": request["id"], "result": result},
            separators=(",", ":"),
        ).encode(),
    )


@contextmanager
def scripted_server(
    scripts: list[Callable[[dict[str, Any]], Response]],
) -> Iterator[tuple[str, list[dict[str, Any]], list[dict[str, str]]]]:
    requests: list[dict[str, Any]] = []
    headers: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            requests.append(request)
            headers.append({key.lower(): value for key, value in self.headers.items()})
            if request.get("method") == "notifications/initialized":
                response = Response(202, "text/plain", b"")
            elif not scripts:
                response = Response(500, "text/plain", b"unexpected request")
            else:
                response = scripts.pop(0)(request)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            extra_headers = dict(response.headers or {})
            if (
                request.get("method") == "initialize"
                and "Mcp-Session-Id" not in extra_headers
            ):
                extra_headers["Mcp-Session-Id"] = "session:test"
            for key, value in extra_headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/mcp", requests, headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class McpRuntimeClientTests(unittest.TestCase):
    def test_initialize_list_and_call_tool(self) -> None:
        scripts = [
            lambda request: json_response(
                request,
                {"protocolVersion": "2025-06-18", "serverInfo": {"name": "ordivon-runtime-mcp", "version": "1"}},
            ),
            lambda request: json_response(
                request,
                {"tools": [{"name": "workspace.read", "inputSchema": {}}]},
            ),
            lambda request: json_response(
                request,
                {"isError": False, "structuredContent": {"content": "hello"}},
            ),
        ]
        with scripted_server(scripts) as (endpoint, requests, headers):
            client = McpRuntimeClient(endpoint, "secret")
            initialized = client.initialize()
            tools = client.list_tools()
            result = client.call_tool("workspace.read", {"schemaVersion": 1})
        self.assertEqual(initialized["serverInfo"]["name"], "ordivon-runtime-mcp")
        self.assertEqual(tools[0]["name"], "workspace.read")
        self.assertEqual(result, {"content": "hello"})
        self.assertEqual(
            [request["id"] for request in requests if "id" in request],
            [1, 2, 3],
        )
        self.assertEqual(
            [request["method"] for request in requests],
            ["initialize", "notifications/initialized", "tools/list", "tools/call"],
        )
        self.assertEqual(headers[0]["authorization"], "Bearer secret")
        self.assertEqual(headers[0]["mcp-protocol-version"], "2025-06-18")
        self.assertEqual(headers[1]["mcp-session-id"], "session:test")
        self.assertEqual(headers[2]["mcp-session-id"], "session:test")

    def test_sse_response_is_decoded(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            message = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "runtime"}},
                }
            )
            return Response(200, "text/event-stream", f"event: message\ndata: {message}\n\n".encode())

        with scripted_server([respond]) as (endpoint, _, _):
            result = McpRuntimeClient(endpoint, "secret").initialize()
        self.assertEqual(result["serverInfo"]["name"], "runtime")

    def test_session_profile_requires_session_identity(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            return Response(
                200,
                "application/json",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "serverInfo": {"name": "runtime"},
                        },
                    }
                ).encode(),
                headers={"Mcp-Session-Id": ""},
            )

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeProtocolError, "Session identity"):
                McpRuntimeClient(endpoint, "secret").initialize()

    def test_response_id_mismatch_fails_closed(self) -> None:
        def respond(_: dict[str, Any]) -> Response:
            return Response(
                200,
                "application/json",
                b'{"jsonrpc":"2.0","id":999,"result":{"protocolVersion":"2025-06-18","serverInfo":{}}}',
            )

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeProtocolError, "response id differs"):
                McpRuntimeClient(endpoint, "secret").initialize()

    def test_structured_tool_error_is_preserved(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            return json_response(
                request,
                {
                    "isError": True,
                    "structuredContent": {
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": "missing workspace",
                            "field": "workspaceId",
                            "retryable": False,
                            "retryClass": "never",
                            "commitState": "not_committed",
                            "origin": "runtime_core",
                            "traceId": "trace-1",
                        }
                    },
                },
            )

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaises(RuntimeToolRejected) as captured:
                McpRuntimeClient(endpoint, "secret").call_tool(
                    "workspace.get", {"workspaceId": "missing"}
                )
        detail = captured.exception.detail
        self.assertEqual(detail.code, "INVALID_REQUEST")
        self.assertEqual(detail.commit_state, "not_committed")
        self.assertEqual(detail.field, "workspaceId")
        self.assertFalse(detail.retryable)

    def test_protocol_version_mismatch_fails_closed(self) -> None:
        with scripted_server([
            lambda request: json_response(
                request,
                {"protocolVersion": "2024-11-05", "serverInfo": {"name": "runtime"}},
            )
        ]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeProtocolError, "another MCP"):
                McpRuntimeClient(endpoint, "secret").initialize()

    def test_empty_sse_heartbeat_is_ignored(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            message = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "runtime"},
                    },
                }
            )
            return Response(
                200,
                "text/event-stream",
                f"data:\n\ndata: {message}\n\n".encode(),
            )

        with scripted_server([respond]) as (endpoint, _, _):
            result = McpRuntimeClient(endpoint, "secret").initialize()
        self.assertEqual(result["serverInfo"]["name"], "runtime")

    def test_multiple_sse_messages_are_outside_stateless_profile(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            one = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}})
            two = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}})
            return Response(
                200,
                "text/event-stream",
                f"data: {one}\n\ndata: {two}\n\n".encode(),
            )

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeProtocolError, "exactly one"):
                McpRuntimeClient(endpoint, "secret").initialize()

    def test_http_failure_is_transport_error(self) -> None:
        def respond(_: dict[str, Any]) -> Response:
            return Response(503, "text/plain", b"offline")

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeTransportError, "HTTP 503"):
                McpRuntimeClient(endpoint, "secret").initialize()

    def test_response_byte_limit_is_enforced(self) -> None:
        def respond(request: dict[str, Any]) -> Response:
            return json_response(
                request,
                {"protocolVersion": "2025-06-18", "serverInfo": {"name": "x" * 100}},
            )

        with scripted_server([respond]) as (endpoint, _, _):
            with self.assertRaisesRegex(RuntimeProtocolError, "byte limit"):
                McpRuntimeClient(
                    endpoint,
                    "secret",
                    max_response_bytes=32,
                ).initialize()


if __name__ == "__main__":
    unittest.main()
