from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import hmac
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .config import load_config, read_private_token_file
from .continuity import ExternalContinuityHost
from .continuity_models import EXTERNAL_CONTINUITY_WORKLOAD_ID, WorkingCheckpoint
from .journal import (
    EventConflict,
    JournalCorruption,
    LeaseConflict,
    LeaseHeld,
    RevisionConflict,
)
from .kernel import TaskRevisionMismatch
from .objects import ObjectCorrupt
from .ops import list_tasks
from .storage import HostStorage

DEFAULT_HOST_MCP_BIND = "127.0.0.1"
DEFAULT_HOST_MCP_PORT = 8898
DEFAULT_HOST_MCP_TOKEN_FILE = Path("/etc/ordivon/host-mcp.token")
DEFAULT_HOST_MCP_BODY_LIMIT_BYTES = 1_048_576
MIN_HOST_MCP_TOKEN_CHARACTERS = 32

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HostMcpSettings:
    state_root: Path
    token_file: Path = DEFAULT_HOST_MCP_TOKEN_FILE
    bind_host: str = DEFAULT_HOST_MCP_BIND
    port: int = DEFAULT_HOST_MCP_PORT
    body_limit_bytes: int = DEFAULT_HOST_MCP_BODY_LIMIT_BYTES
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.state_root.is_absolute():
            raise ValueError("Host MCP state root must be absolute")
        if not self.token_file.is_absolute():
            raise ValueError("Host MCP token file must be absolute")
        _require_loopback(self.bind_host)
        if type(self.port) is not int or self.port < 1 or self.port > 65_535:
            raise ValueError("Host MCP port must be in [1, 65535]")
        if type(self.body_limit_bytes) is not int or self.body_limit_bytes < 1:
            raise ValueError("Host MCP body limit must be a positive integer")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Host MCP log level is invalid")

    @property
    def endpoint(self) -> str:
        host = f"[{self.bind_host}]" if ":" in self.bind_host else self.bind_host
        return f"http://{host}:{self.port}/mcp"


class BearerAuthApp:
    """Exact static Bearer authentication around the SDK-owned MCP ASGI app."""

    def __init__(self, app: AsgiApp, token: str, *, body_limit_bytes: int) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("utf-8")
        self._body_limit_bytes = body_limit_bytes

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        authorization = b""
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"authorization":
                authorization = raw_value
                break
        if not hmac.compare_digest(authorization, self._expected):
            within_limit = await _drain_http_request(
                receive, max_bytes=self._body_limit_bytes
            )
            if not within_limit:
                await _http_error(send, 413, "request_too_large", close=True)
                return
            await _http_error(
                send, 401, "unauthorized", authenticate=True
            )
            return
        await self.app(scope, receive, send)


async def _drain_http_request(receive: Receive, *, max_bytes: int) -> bool:
    total = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return True
        if message.get("type") != "http.request":
            continue
        body = message.get("body", b"")
        if not isinstance(body, bytes):
            return False
        total += len(body)
        if total > max_bytes:
            return False
        if message.get("more_body") is not True:
            return True


async def _http_error(
    send: Send,
    status: int,
    code: str,
    *,
    authenticate: bool = False,
    close: bool = False,
) -> None:
    body = json.dumps({"error": code}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if authenticate:
        headers.append((b"www-authenticate", b"Bearer"))
    if close:
        headers.append((b"connection", b"close"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def build_mcp_server(settings: HostMcpSettings) -> MCPServer:
    server = MCPServer(
        name="ordivon-host-mcp",
        title="Ordivon Host",
        description="Durable semantic continuity and Task authority for external Agents.",
        instructions=(
            "Use task.list to discover recent Host Tasks. Use task.resume only for "
            "ordivon.host.external-continuity.v1 Tasks. WorkingCheckpoint is a semantic "
            "working claim, not Runtime, Git, or domain truth: revalidate physical/current "
            "facts at their owning authority before continuing. task.adopt and "
            "task.checkpoint are revision-safe and exact retry after response loss is allowed."
        ),
        version=_package_version(),
        log_level=settings.log_level,
    )

    @server.tool(
        name="task.list",
        title="List Host tasks",
        description=(
            "List recent Host Tasks, optionally scoped to one Goal. Each item includes the "
            "current projection plus workload identity when a durable TaskDescriptor exists. "
            "This is a projection-only read and never invokes Runtime, Harness, or a Provider."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_list(goalId: str | None = None, limit: int = 50) -> CallToolResult:
        return await _run_tool(
            lambda: _list_host_tasks(settings.state_root, goal_id=goalId, limit=limit),
            write=False,
        )

    @server.tool(
        name="task.resume",
        title="Resume external work",
        description=(
            "Read one external-continuity Task as TaskProjection + OperatorHandoffCapsule + "
            "latest WorkingCheckpoint. expectedRevision is an optional stale-read fence. "
            "This never validates Runtime/Git/domain truth and never invokes another system."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_resume(
        taskId: str,
        expectedRevision: int | None = None,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _resume_task(
                settings.state_root,
                task_id=taskId,
                expected_revision=expectedRevision,
            ),
            write=False,
        )

    @server.tool(
        name="task.adopt",
        title="Adopt external work",
        description=(
            "Create or recover one explicit external-continuity Task and its initial "
            "WorkingCheckpoint. Exact replay with the same taskId, goalId, and checkpoint "
            "converges after response loss; a different initial semantic claim fails closed."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_adopt(
        taskId: str,
        goalId: str,
        initialCheckpoint: dict[str, Any],
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _adopt_task(
                settings.state_root,
                task_id=taskId,
                goal_id=goalId,
                checkpoint_value=initialCheckpoint,
            ),
            write=True,
        )

    @server.tool(
        name="task.checkpoint",
        title="Checkpoint external work",
        description=(
            "Commit a new WorkingCheckpoint against one exact Task revision. If the original "
            "response was lost, replay the identical checkpoint with the original "
            "expectedRevision: Host returns admission=existing when that exact transition is "
            "already current. Different or stale claims fail closed."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_checkpoint(
        taskId: str,
        expectedRevision: int,
        checkpoint: dict[str, Any],
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _checkpoint_task(
                settings.state_root,
                task_id=taskId,
                expected_revision=expectedRevision,
                checkpoint_value=checkpoint,
            ),
            write=True,
        )

    return server


def build_authenticated_app(settings: HostMcpSettings, token: str) -> BearerAuthApp:
    server = build_mcp_server(settings)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.body_limit_bytes,
        host=settings.bind_host,
    )
    return BearerAuthApp(
        app, token, body_limit_bytes=settings.body_limit_bytes
    )


def check_settings(settings: HostMcpSettings) -> dict[str, object]:
    _read_host_mcp_token(settings.token_file)
    database = settings.state_root / "host.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(
            f"Host authority is not initialized: {database}; run ordivon-host init first"
        )
    with HostStorage(settings.state_root) as storage:
        storage.journal.validate_invariants()
        tasks = storage.journal.task_count()
        events = storage.journal.event_count()
    return {
        "status": "ok",
        "endpoint": settings.endpoint,
        "stateRoot": str(settings.state_root),
        "tokenFile": str(settings.token_file),
        "tokenFilePrivate": True,
        "bodyLimitBytes": settings.body_limit_bytes,
        "tasks": tasks,
        "events": events,
        "protocolTransport": "sdk-streamable-http-stateless-json",
    }


def run_server(settings: HostMcpSettings) -> None:
    token = _read_host_mcp_token(settings.token_file)
    check_settings(settings)
    app = build_authenticated_app(settings, token)
    import uvicorn

    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ordivon-host-mcp",
        description="Authenticated loopback MCP projection for Ordivon Host continuity.",
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int)
    parser.add_argument("--body-limit-bytes", type=int)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration, private token, initialized Host authority, and Journal",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _settings_from_args(args)
        if args.check:
            print(json.dumps(check_settings(settings), sort_keys=True))
            return 0
        run_server(settings)
        return 0
    except (OSError, ValueError, JournalCorruption, ObjectCorrupt) as error:
        print(f"ordivon-host-mcp: {error}", file=sys.stderr)
        return 2


def _settings_from_args(args: argparse.Namespace) -> HostMcpSettings:
    config = load_config(args.config)
    state_root = args.state_root or config.state_root
    token_file = args.token_file or Path(
        os.environ.get("ORDIVON_HOST_MCP_TOKEN_FILE", str(DEFAULT_HOST_MCP_TOKEN_FILE))
    )
    bind_host = args.bind or os.environ.get("ORDIVON_HOST_MCP_BIND", DEFAULT_HOST_MCP_BIND)
    port = args.port if args.port is not None else _env_int(
        "ORDIVON_HOST_MCP_PORT", DEFAULT_HOST_MCP_PORT
    )
    body_limit = (
        args.body_limit_bytes
        if args.body_limit_bytes is not None
        else _env_int(
            "ORDIVON_HOST_MCP_BODY_LIMIT_BYTES", DEFAULT_HOST_MCP_BODY_LIMIT_BYTES
        )
    )
    log_level = args.log_level or os.environ.get("ORDIVON_HOST_MCP_LOG_LEVEL", "INFO").upper()
    return HostMcpSettings(
        state_root=Path(state_root),
        token_file=Path(token_file),
        bind_host=bind_host,
        port=port,
        body_limit_bytes=body_limit,
        log_level=log_level,
    )


def _list_host_tasks(
    state_root: Path,
    *,
    goal_id: str | None,
    limit: int,
) -> dict[str, object]:
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError("task.list limit must be in [1, 100]")
    with HostStorage(state_root) as storage:
        tasks = list_tasks(storage, goal_id=goal_id, limit=limit)
        items: list[dict[str, object]] = []
        for task in tasks:
            descriptor = storage.read_task_descriptor(task.task_id)
            workload_id = None if descriptor is None else descriptor.workload_id
            items.append(
                {
                    "projection": task.to_dict(),
                    "workloadId": workload_id,
                    "externalContinuity": workload_id == EXTERNAL_CONTINUITY_WORKLOAD_ID,
                }
            )
    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-task-list",
        "tasks": items,
    }


def _resume_task(
    state_root: Path,
    *,
    task_id: str,
    expected_revision: int | None,
) -> dict[str, object]:
    with HostStorage(state_root) as storage:
        return ExternalContinuityHost(storage, clock_ms=_wall_clock_ms).resume(
            task_id,
            expected_revision=expected_revision,
        ).to_dict()


def _adopt_task(
    state_root: Path,
    *,
    task_id: str,
    goal_id: str,
    checkpoint_value: dict[str, Any],
) -> dict[str, object]:
    checkpoint = WorkingCheckpoint.from_dict(checkpoint_value)
    with HostStorage(state_root) as storage:
        return ExternalContinuityHost(storage, clock_ms=_wall_clock_ms).adopt(
            task_id=task_id,
            goal_id=goal_id,
            initial_checkpoint=checkpoint,
        ).to_dict()


def _checkpoint_task(
    state_root: Path,
    *,
    task_id: str,
    expected_revision: int,
    checkpoint_value: dict[str, Any],
) -> dict[str, object]:
    checkpoint = WorkingCheckpoint.from_dict(checkpoint_value)
    with HostStorage(state_root) as storage:
        return ExternalContinuityHost(storage, clock_ms=_wall_clock_ms).checkpoint(
            task_id=task_id,
            expected_revision=expected_revision,
            checkpoint=checkpoint,
        ).to_dict()


async def _run_tool(
    operation: Callable[[], dict[str, object]],
    *,
    write: bool,
) -> CallToolResult:
    try:
        result = await asyncio.to_thread(operation)
    except Exception as error:
        return _error_result(error, write=write)
    return _success_result(result)


def _success_result(value: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=_json_text(value))],
        structuredContent=value,
        isError=False,
    )


def _error_result(error: Exception, *, write: bool) -> CallToolResult:
    code = "HOST_INTERNAL"
    message = "Host MCP operation failed"
    retryable = False
    retry_class = "inspect"
    commit_state = "unknown" if write else "not_committed"
    field: str | None = None

    if isinstance(error, KeyError):
        code = "TASK_NOT_FOUND"
        message = _bounded_message(error)
        retry_class = "never"
        commit_state = "not_committed"
    elif isinstance(error, (RevisionConflict, TaskRevisionMismatch, EventConflict)):
        code = "REVISION_CONFLICT"
        message = _bounded_message(error)
        retry_class = "resume_task"
        commit_state = "not_committed"
    elif isinstance(error, (LeaseHeld, LeaseConflict)):
        code = "TASK_BUSY"
        message = _bounded_message(error)
        retryable = True
        retry_class = "retry_same_request"
        commit_state = "not_committed"
    elif isinstance(error, (JournalCorruption, ObjectCorrupt)):
        code = "HOST_STATE_CORRUPT"
        message = _bounded_message(error)
        retry_class = "operator_repair"
        commit_state = "not_committed"
    elif isinstance(error, sqlite3.OperationalError):
        code = "HOST_STORAGE_BUSY"
        message = "Host storage could not complete the request"
        retryable = True
        retry_class = "resume_then_retry" if write else "retry_same_request"
    elif isinstance(error, (ValueError, TypeError)):
        code = "INVALID_ARGUMENT"
        message = _bounded_message(error)
        retry_class = "fix_request"
        commit_state = "not_committed"
    elif isinstance(error, FileNotFoundError):
        code = "HOST_STATE_UNAVAILABLE"
        message = "Host authority is not initialized or is unavailable"
        retry_class = "operator_repair"
        commit_state = "not_committed"

    envelope = {
        "error": {
            "code": code,
            "message": message,
            "field": field,
            "retryable": retryable,
            "retryClass": retry_class,
            "commitState": commit_state,
            "origin": "host-mcp",
        }
    }
    return CallToolResult(
        content=[TextContent(text=_json_text(envelope))],
        structuredContent=envelope,
        isError=True,
    )


def _read_host_mcp_token(path: Path) -> str:
    token = read_private_token_file(path, label="Host MCP token")
    if len(token) < MIN_HOST_MCP_TOKEN_CHARACTERS:
        raise ValueError(
            f"Host MCP token must contain at least {MIN_HOST_MCP_TOKEN_CHARACTERS} characters"
        )
    return token


def _require_loopback(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("Host MCP bind must be a literal loopback IP address") from error
    if not address.is_loopback:
        raise ValueError("Host MCP bind must remain loopback-only")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _package_version() -> str:
    try:
        return importlib.metadata.version("ordivon-host")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - source-only import
        return "0.1.2"


def _bounded_message(error: Exception) -> str:
    message = str(error).strip() or type(error).__name__
    return message[:512]


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
