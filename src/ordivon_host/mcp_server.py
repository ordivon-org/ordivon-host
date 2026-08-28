from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import importlib.metadata
import ipaddress
import json
import os
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from anc_canonical import canonical_digest
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema
from starlette.requests import ClientDisconnect

from .board import HostMessageBoard
from .config import load_config, read_private_token_file
from .continuity import ExternalContinuityHost
from .continuity_models import (
    EXTERNAL_CONTINUITY_WORKLOAD_ID,
    WorkingCheckpoint,
    validate_writer_label,
)
from .domain import TaskState
from .handoff import operator_handoff
from .news import HostDailyNews
from .journal import (
    EventConflict,
    JournalCorruption,
    LeaseConflict,
    LeaseHeld,
    RevisionConflict,
)
from .journal.migrations import schema_version
from .kernel import TaskRevisionMismatch
from .objects import ObjectCorrupt
from .ops import doctor_state, inspect_deployment
from .storage import HostStorage

DEFAULT_HOST_MCP_BIND = "127.0.0.1"
DEFAULT_HOST_MCP_PORT = 8898
DEFAULT_HOST_MCP_TOKEN_FILE = Path("/etc/ordivon/host-mcp.token")
DEFAULT_HOST_MCP_BODY_LIMIT_BYTES = 1_048_576
MIN_HOST_MCP_TOKEN_CHARACTERS = 32
HOST_MCP_SURFACE_VERSION = 4
HOST_MCP_TOOL_NAMES = (
    "host.status",
    "board.list",
    "board.post",
    "news.list",
    "news.read",
    "news.publish",
    "task.observe",
    "task.list",
    "task.resume",
    "task.adopt",
    "task.checkpoint",
)


def _tool_schema_identity(descriptors: Sequence[dict[str, Any]]) -> dict[str, object]:
    selected: list[dict[str, object]] = []
    for descriptor in descriptors:
        name = descriptor.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Host MCP Tool descriptor has no name")
        selected.append(
            {
                "name": name,
                "inputSchema": descriptor.get("inputSchema"),
                "outputSchema": descriptor.get("outputSchema"),
            }
        )
    selected.sort(key=lambda item: str(item["name"]))
    digest = canonical_digest(selected)
    return {
        "surfaceVersion": HOST_MCP_SURFACE_VERSION,
        "toolCount": len(selected),
        "toolNames": [str(item["name"]) for item in selected],
        "schemaDigest": digest,
        "schemaRevision": f"mcp-schema:{digest[7:]}",
    }


def _registered_tool_schema_identity(server: MCPServer) -> dict[str, object]:
    # MCPServer.list_tools() is async; registration itself is synchronous. Use the
    # pinned SDK ToolManager only to project the exact schemas that tools/list will
    # expose, then verify this projection against wire tools/list in tests/deploy.
    registered = server._tool_manager.list_tools()
    descriptors = [
        {
            "name": tool.name,
            "inputSchema": tool.parameters,
            "outputSchema": tool.output_schema,
        }
        for tool in registered
    ]
    names = tuple(descriptor["name"] for descriptor in descriptors)
    if names != HOST_MCP_TOOL_NAMES:
        raise RuntimeError(f"Host MCP registered Tool order differs: {names}")
    return _tool_schema_identity(descriptors)

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


class ToolArgumentError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


NewsSection = Literal[
    "today", "deep_story", "radar", "research", "industry", "market",
    "slow_variable", "anomaly", "unresolved", "judgment", "catalyst"
]


class NewsEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceType: Literal["official", "company", "paper", "regulatory", "news", "dataset", "other"]
    sourceId: str = Field(min_length=1, max_length=2048)
    publisher: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    publishedAtMs: int | None = None


class NewsItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itemId: str = Field(min_length=1, max_length=256)
    section: NewsSection
    category: str = Field(min_length=1, max_length=64)
    headline: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=4096)
    novelty: str | None = Field(default=None, max_length=2048)
    threadKey: str | None = Field(default=None, max_length=256)
    continuationOf: str | None = Field(default=None, max_length=256)
    status: Literal["new", "followup", "correction", "closed"]
    importance: int = Field(ge=1, le=5)
    confidence: Literal["high", "medium", "low"] | None = None
    eventAtMs: int | None = None
    publishedAtMs: int | None = None
    observedAtMs: int | None = None
    evidence: list[NewsEvidenceInput] = Field(min_length=1, max_length=16)


class NewsEditionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    kind: Literal["ordivon.host-news-edition"]
    truthRole: Literal["external-news-projection-not-world-truth"]
    editionId: str = Field(min_length=6, max_length=512, pattern=r"^news:")
    editionDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field(min_length=1, max_length=128)
    generatedAtMs: int
    coverageStartMs: int | None = None
    coverageEndMs: int | None = None
    marketCutoffMs: int | None = None
    producerLabel: str = Field(min_length=1, max_length=128)
    renderedBrief: str | None = Field(default=None, max_length=40000)
    items: list[NewsItemInput] = Field(min_length=1, max_length=64)


class WorkingCheckpointRuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspaceId: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Runtime Workspace navigation hint only; revalidate exact Runtime state before use "
            "or any physical carrier disposition. The hint does not authorize Workspace "
            "retention or closure."
        ),
    )
    relevantJobIds: list[str] = Field(
        max_length=64,
        description="Runtime Job navigation hints relevant to this semantic checkpoint.",
    )
    observedHeadRevision: str | None = Field(
        max_length=512,
        description="Last observed source/Git revision hint; not current Git truth.",
    )


class WorkingCheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    kind: Literal["ordivon.host-working-checkpoint"]
    truthRole: Literal["semantic-working-claim"]
    taskId: str = Field(
        min_length=6,
        max_length=4096,
        pattern=r"^task:",
        description="Exact Host Task identity; must equal the taskId Tool argument.",
    )
    objective: str = Field(min_length=1, max_length=4096)
    frontier: str = Field(min_length=1, max_length=4096)
    established: list[str] = Field(max_length=64)
    unresolved: list[str] = Field(max_length=64)
    rejected: list[str] = Field(max_length=64)
    constraints: list[str] = Field(max_length=64)
    nextActions: list[str] = Field(max_length=64)
    runtime: WorkingCheckpointRuntimeInput | None = Field(
        description=(
            "Optional physical navigation hints. Host does not treat them as Runtime or Git truth, "
            "and they do not authorize physical Workspace retention or closure."
        )
    )


def _working_checkpoint_input_schema() -> dict[str, Any]:
    schema = WorkingCheckpointInput.model_json_schema()
    definitions = schema.pop("$defs", {})
    runtime = schema["properties"]["runtime"]
    if not isinstance(runtime, dict):
        raise TypeError("WorkingCheckpoint runtime schema is not an object")
    variants = runtime.get("anyOf")
    if not isinstance(variants, list):
        raise TypeError("WorkingCheckpoint runtime schema has no variants")
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            continue
        reference = variant.get("$ref")
        if reference == "#/$defs/WorkingCheckpointRuntimeInput":
            definition = definitions.get("WorkingCheckpointRuntimeInput")
            if not isinstance(definition, dict):
                raise TypeError("WorkingCheckpoint runtime definition is missing")
            variants[index] = definition
    return schema


def _working_checkpoint_patch_schema() -> dict[str, Any]:
    full = _working_checkpoint_input_schema()
    properties = full["properties"]
    allowed = (
        "objective",
        "frontier",
        "established",
        "unresolved",
        "rejected",
        "constraints",
        "nextActions",
        "runtime",
    )
    return {
        "type": "object",
        "title": "WorkingCheckpointPatch",
        "description": (
            "Patch the WorkingCheckpoint at expectedRevision. Omitted fields are inherited "
            "from that exact revision; present fields replace the complete field value."
        ),
        "additionalProperties": False,
        "minProperties": 1,
        "properties": {name: properties[name] for name in allowed},
    }


WorkingCheckpointWireInput = Annotated[
    dict[str, Any],
    WithJsonSchema(_working_checkpoint_input_schema()),
]

WorkingCheckpointUpdateInput = Annotated[
    dict[str, Any],
    WithJsonSchema(
        {
            "oneOf": [
                _working_checkpoint_input_schema(),
                _working_checkpoint_patch_schema(),
            ],
            "description": (
                "Either a complete WorkingCheckpoint or a revision-bound patch. "
                "A patch may continue open continuity and inherits omitted fields from "
                "expectedRevision; a new complete/abandon transition requires the full checkpoint."
            ),
        }
    ),
]

HostStatusDetailInput = Annotated[
    str,
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["summary", "integrity", "history"],
            "description": (
                "summary is cheap; integrity runs full local Host Doctor checks; history also "
                "validates every retained Event. Runtime is not proxied by this Tool."
            ),
        }
    ),
]


ContinuityDispositionInput = Annotated[
    str,
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["continue", "complete", "abandon"],
            "description": (
                "Lifecycle of Host continuity tracking only; complete/abandon do not assert "
                "an external domain outcome."
            ),
        }
    ),
]

WriterLabelInput = Annotated[
    str | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 128},
                {"type": "null"},
            ],
            "description": (
                "Optional self-asserted writer provenance for this Host continuity write. "
                "It is persisted with the admitted revision, not inside WorkingCheckpoint "
                "semantic content, and is not authenticated identity or authority."
            ),
        }
    ),
]


@dataclass(frozen=True, slots=True)
class HostMcpSettings:
    state_root: Path
    token_file: Path = DEFAULT_HOST_MCP_TOKEN_FILE
    bind_host: str = DEFAULT_HOST_MCP_BIND
    port: int = DEFAULT_HOST_MCP_PORT
    body_limit_bytes: int = DEFAULT_HOST_MCP_BODY_LIMIT_BYTES
    public_origin: str | None = None
    trust_cf_access: bool = False
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
        if self.public_origin is not None:
            _public_origin_host(self.public_origin)
        if type(self.trust_cf_access) is not bool:
            raise ValueError("Host MCP trust_cf_access must be a boolean")
        if self.trust_cf_access and self.public_origin is None:
            raise ValueError("Host MCP Cloudflare Access trust requires public_origin")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Host MCP log level is invalid")

    @property
    def endpoint(self) -> str:
        host = f"[{self.bind_host}]" if ":" in self.bind_host else self.bind_host
        return f"http://{host}:{self.port}/mcp"

    @property
    def public_endpoint(self) -> str | None:
        if self.public_origin is None:
            return None
        return f"{self.public_origin}/mcp"


class BearerAuthApp:
    """Exact static Bearer authentication around the SDK-owned MCP ASGI app."""

    def __init__(
        self,
        app: AsgiApp,
        token: str,
        *,
        body_limit_bytes: int,
        trust_cf_access: bool = False,
    ) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("utf-8")
        self._body_limit_bytes = body_limit_bytes
        self._trust_cf_access = trust_cf_access

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        authorization = b""
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"authorization":
                authorization = raw_value
                break
        cf_access_assertion = b""
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"cf-access-jwt-assertion":
                cf_access_assertion = raw_value
                break
        authorized = hmac.compare_digest(authorization, self._expected) or (
            self._trust_cf_access and bool(cf_access_assertion)
        )
        if not authorized:
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
        try:
            await self.app(scope, receive, send)
        except ClientDisconnect:
            # The peer hung up mid-request (common when the public tunnel
            # flakes); there is no response to deliver, so swallow instead of
            # letting uvicorn log a full ASGI traceback for a routine drop.
            return


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
        description=(
            "Durable Host continuity plus Host-owned Journal/CAS operational authority for "
            "external Agents."
        ),
        instructions=(
            "Treat task.* as compatibility names for Host continuity primitives, not as a "
            "cross-owner work ontology. host.status continuity counts and task.list/task.observe "
            "TaskProjection lifecycle states describe Host tracking only: READY/open continuity "
            "does not mean actionable NOW, priority, owner standing, or current domain truth. "
            "Host does not compute a cross-owner current-work or priority portfolio. board.* is "
            "a durable collaboration surface only: messages are not Tasks, priority, authority, "
            "owner standing, or domain truth. news.* is a durable external-news publication "
            "projection only: Host preserves exact editions/revisions and source pointers but does "
            "not validate external-world truth or convert news into Tasks/owner standing. Use task.list only to discover continuity, "
            "task.resume to recover one exact known "
            "continuation point, and revalidate current physical/domain facts at their owning "
            "authority. task.checkpoint changes continuity state only; exact retry after response "
            "loss is safe."
        ),
        version=_package_version(),
        log_level=settings.log_level,
    )
    server_interface: dict[str, object] = {}

    @server.tool(
        name="host.status",
        title="Observe Host status",
        description=(
            "Return one compact Host operational snapshot: Journal/schema/task counts, current "
            "deployment identity, continuity-tracking counts, and bounded recent Host Task "
            "activity. Continuity counts are not active-work counts or priority. detail=integrity "
            "adds full local Host Doctor checks; detail=history additionally validates all retained "
            "Event history. Runtime is deliberately not proxied by this Tool."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def host_status(
        detail: HostStatusDetailInput = "summary",
        recentLimit: int = 5,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _host_status(
                settings.state_root,
                detail=detail,
                recent_limit=recentLimit,
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _global_integrity_scope(detail)},
            write=False,
        )

    @server.tool(
        name="board.list",
        title="Read Host message board",
        description=(
            "Read durable Host-global collaboration messages. Omit afterSequence to receive the "
            "newest bounded window in chronological order, or pass the last observed sequence for "
            "incremental polling. Messages are collaboration records only: they are not Tasks, "
            "priority, execution authority, owner standing, authenticated identity, or domain truth. "
            "This read validates Journal invariants plus the CAS objects it consumes; it does not "
            "claim unrelated CAS objects are globally healthy."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def board_list(
        afterSequence: int | None = None,
        limit: int = 50,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _list_board_messages(
                settings.state_root, after_sequence=afterSequence, limit=limit
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="board.post",
        title="Post to Host message board",
        description=(
            "Persist one Host-global collaboration message. clientMessageId is caller-chosen and "
            "provides exact replay identity after response loss. authorLabel is explicitly "
            "self-asserted, not authenticated identity. A message does not create a Task or grant "
            "priority, execution authority, owner standing, or domain truth."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def board_post(
        clientMessageId: str,
        authorLabel: str,
        message: str,
        messageKind: Literal["note", "question", "proposal", "warning", "reply"] = "note",
        topic: str | None = None,
        replyToClientMessageId: str | None = None,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _post_board_message(
                settings.state_root,
                client_message_id=clientMessageId,
                author_label=authorLabel,
                message=message,
                message_kind=messageKind,
                topic=topic,
                reply_to_client_message_id=replyToClientMessageId,
            ),
            server_interface=server_interface,
            write=True,
        )

    @server.tool(
        name="news.list",
        title="List Host daily news editions",
        description=(
            "List bounded daily external-news edition headers with stable query-bound cursor paging. "
            "This is publication inventory only, not external-world truth or a priority surface. "
            "This read validates Journal invariants and the objects it consumes, not unrelated Host CAS health."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def news_list(
        limit: int = 30,
        cursor: str | None = None,
        fromDate: str | None = None,
        toDate: str | None = None,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _list_news(
                settings.state_root, limit=limit, cursor=cursor, from_date=fromDate, to_date=toDate
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="news.read",
        title="Read one Host daily news edition",
        description=(
            "Read the latest or one exact revision of a durable external-news edition, optionally "
            "filtered by section/category/thread key. The default omits the long rendered brief and "
            "returns structured items. External claims remain source claims requiring revalidation. "
            "This read validates Journal invariants plus the selected edition object, not unrelated Host CAS health."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def news_read(
        editionId: str | None = None,
        revision: int | None = None,
        sections: list[NewsSection] | None = None,
        categories: list[str] | None = None,
        threadKeys: list[str] | None = None,
        includeRenderedBrief: bool = False,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _read_news(
                settings.state_root,
                edition_id=editionId, revision=revision,
                sections=() if sections is None else tuple(sections),
                categories=() if categories is None else tuple(categories),
                thread_keys=() if threadKeys is None else tuple(threadKeys),
                include_rendered_brief=includeRenderedBrief,
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="news.publish",
        title="Publish one Host daily news edition",
        description=(
            "Persist one complete structured external-news edition revision. clientPublishId provides "
            "exact replay after response loss; expectedRevision fences corrections. Host preserves "
            "publication bytes and source pointers but does not validate external claims or create Tasks."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def news_publish(
        clientPublishId: str,
        editionId: str,
        expectedRevision: int,
        edition: NewsEditionInput,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _publish_news(
                settings.state_root, client_publish_id=clientPublishId, edition_id=editionId,
                expected_revision=expectedRevision, edition=edition.model_dump(),
            ),
            server_interface=server_interface, write=True,
        )

    @server.tool(
        name="task.observe",
        title="Observe one Host task",
        description=(
            "Return a compact revision-fenced observation for any Host Task: projection, workload "
            "identity, current head metadata, handoff, recovery assessment when applicable, "
            "external-continuity checkpoint preview, recorded self-asserted writer provenance when "
            "present, and a bounded recent Event timeline. The TaskProjection is Host lifecycle "
            "mechanics only; READY does not establish actionable "
            "NOW work, priority, owner standing, or current domain truth. This does not return raw "
            "Event payload data and never invokes Runtime, Harness, or a Provider. This read "
            "validates Journal invariants plus the CAS objects it consumes; unrelated CAS health "
            "remains a global status/Doctor concern."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_observe(
        taskId: str,
        expectedRevision: int | None = None,
        eventLimit: int = 5,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _observe_task(
                settings.state_root,
                task_id=taskId,
                expected_revision=expectedRevision,
                event_limit=eventLimit,
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="task.list",
        title="List Host tasks",
        description=(
            "Discover resumable external-continuity Tasks, optionally scoped to one Goal, using "
            "an opaque query-bound stable cursor. This is a continuity inventory, not a current-work "
            "or priority surface: projection.state=READY means only that Host continuity remains "
            "open at its continue frontier. It does not establish actionable NOW work, priority, "
            "owner standing, or current domain truth. Each item includes the current Host projection, "
            "creation time, and bounded semantic checkpoint preview. When the exact current "
            "WorkingCheckpoint carries a Runtime workspaceId, semanticSummary.runtimeNavigationHint "
            "exposes that Host-retained navigation hint only. runtimeWorkspaceId optionally filters "
            "by exact current Host-retained workspace hint for structural claimant discovery; it does "
            "not establish Runtime currentness, "
            "semantic claimant standing, or unclaimed status when absent. It also does not authorize "
            "physical Workspace retention or closure; revalidate exact Runtime state before any "
            "carrier disposition. Missing Runtime mechanics is not a Human decision requirement. "
            "Non-terminal continuity is the default; includeTerminal opts into history. This "
            "operation validates Journal invariants plus the CAS objects needed for visible rows; "
            "it does not claim unrelated CAS objects are healthy. This projection never invokes "
            "Runtime, Harness, a Provider, or a cross-owner portfolio authority."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def task_list(
        goalId: str | None = None,
        runtimeWorkspaceId: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        includeTerminal: bool = False,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _list_host_tasks(
                settings.state_root,
                goal_id=goalId,
                runtime_workspace_id=runtimeWorkspaceId,
                limit=limit,
                cursor=cursor,
                include_terminal=includeTerminal,
            ),
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="task.resume",
        title="Resume external work",
        description=(
            "Recover one exact external-continuity point as TaskProjection + "
            "OperatorHandoffCapsule + WorkingCheckpoint bound to that Task revision. This resumes "
            "Host semantic continuity only: frontier/nextActions are retained working claims, not "
            "automatic current work admission or priority. expectedRevision is an optional stale-read "
            "fence. Any retained Runtime hint is navigation only and does not authorize Workspace "
            "retention or closure; revalidate exact Runtime state before any physical carrier "
            "disposition. Missing Runtime mechanics is not a Human decision requirement. This "
            "never validates Runtime/Git/domain truth and never invokes another system. It "
            "validates Journal invariants plus the CAS objects required for this exact continuity "
            "point, not unrelated Host CAS health."
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
            server_interface=server_interface,
            result_meta={"ordivon/hostIntegrityScope": _operation_local_integrity_scope()},
            write=False,
        )

    @server.tool(
        name="task.adopt",
        title="Adopt external work",
        description=(
            "Create or recover one explicit external-continuity Task and its initial "
            "WorkingCheckpoint. Adoption opens Host continuity only; it does not admit cross-owner "
            "work priority, domain standing, or execution authority. Optional writerLabel is "
            "self-asserted provenance for the admitted revision, not authenticated identity. Exact "
            "replay with the same taskId, goalId, and checkpoint converges after response loss; a "
            "different initial semantic claim fails closed."
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
        initialCheckpoint: WorkingCheckpointWireInput,
        writerLabel: WriterLabelInput = None,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _adopt_task(
                settings.state_root,
                task_id=taskId,
                goal_id=goalId,
                checkpoint_value=initialCheckpoint,
                writer_label=writerLabel,
            ),
            server_interface=server_interface,
            write=True,
        )

    @server.tool(
        name="task.checkpoint",
        title="Checkpoint external work",
        description=(
            "Commit a new WorkingCheckpoint against one exact Task revision. This mutates Host "
            "continuity tracking only; continue/complete/abandon do not assert current work "
            "priority, owner standing, or external domain outcome. If the original response was "
            "lost, replay the identical checkpoint with the original expectedRevision: Host returns "
            "admission=existing when that exact transition is already current. checkpoint accepts "
            "either a full WorkingCheckpoint or a revision-bound patch. Patches inherit omitted "
            "fields only for continuityDisposition=continue; a new complete/abandon transition "
            "requires a full WorkingCheckpoint so no inherited field is frozen accidentally. "
            "Optional writerLabel is self-asserted provenance stored outside WorkingCheckpoint and "
            "is never inherited by a patch. Exact replay after response loss preserves the writer "
            "already recorded by the committed revision; different or stale claims fail closed."
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
        checkpoint: WorkingCheckpointUpdateInput,
        continuityDisposition: ContinuityDispositionInput = "continue",
        writerLabel: WriterLabelInput = None,
    ) -> CallToolResult:
        return await _run_tool(
            lambda: _checkpoint_task(
                settings.state_root,
                task_id=taskId,
                expected_revision=expectedRevision,
                checkpoint_value=checkpoint,
                disposition=continuityDisposition,
                writer_label=writerLabel,
            ),
            server_interface=server_interface,
            write=True,
        )

    server_interface.update(_registered_tool_schema_identity(server))
    return server


def build_authenticated_app(settings: HostMcpSettings, token: str) -> BearerAuthApp:
    server = build_mcp_server(settings)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.body_limit_bytes,
        transport_security=_transport_security(settings),
        host=settings.bind_host,
    )
    return BearerAuthApp(
        app,
        token,
        body_limit_bytes=settings.body_limit_bytes,
        trust_cf_access=settings.trust_cf_access,
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
        "publicEndpoint": settings.public_endpoint,
        "trustCloudflareAccess": settings.trust_cf_access,
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
        "--public-origin",
        help=(
            "optional canonical HTTPS origin accepted by MCP DNS-rebinding protection "
            "when this loopback listener is reached through a reverse proxy"
        ),
    )
    parser.add_argument(
        "--trust-cf-access",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "trust a non-empty cf-access-jwt-assertion only when this loopback origin is "
            "exclusively reachable through an operator-owned Cloudflare Access application"
        ),
    )
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
    public_origin = args.public_origin or os.environ.get("ORDIVON_HOST_MCP_PUBLIC_ORIGIN")
    trust_cf_access = (
        args.trust_cf_access
        if args.trust_cf_access is not None
        else _env_bool("ORDIVON_HOST_MCP_TRUST_CF_ACCESS", False)
    )
    log_level = args.log_level or os.environ.get("ORDIVON_HOST_MCP_LOG_LEVEL", "INFO").upper()
    return HostMcpSettings(
        state_root=Path(state_root),
        token_file=Path(token_file),
        bind_host=bind_host,
        port=port,
        body_limit_bytes=body_limit,
        public_origin=public_origin,
        trust_cf_access=trust_cf_access,
        log_level=log_level,
    )


DISCOVERY_PREVIEW_MAX_BYTES = 512


def _discovery_preview(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= DISCOVERY_PREVIEW_MAX_BYTES:
        return value, False
    preview = encoded[:DISCOVERY_PREVIEW_MAX_BYTES].decode("utf-8", errors="ignore")
    return preview, True


def _item_previews(values: tuple[str, ...], *, limit: int = 3) -> dict[str, object]:
    visible = []
    for value in values[:limit]:
        preview, truncated = _discovery_preview(value)
        visible.append({"text": preview, "truncated": truncated})
    return {
        "items": visible,
        "total": len(values),
        "more": len(values) > limit,
    }


def _task_cursor(
    created_at_ms: int,
    task_id: str,
    *,
    goal_id: str | None,
    runtime_workspace_id: str | None,
    include_terminal: bool,
) -> str:
    payload = json.dumps(
        {
            "v": 2,
            "createdAtMs": created_at_ms,
            "taskId": task_id,
            "goalId": goal_id,
            "runtimeWorkspaceId": runtime_workspace_id,
            "includeTerminal": include_terminal,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_task_cursor(
    value: str | None,
    *,
    goal_id: str | None,
    runtime_workspace_id: str | None,
    include_terminal: bool,
) -> tuple[int, str] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ToolArgumentError("cursor", "task.list cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
        payload = json.loads(decoded)
    except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as error:
        raise ToolArgumentError("cursor", "task.list cursor is invalid") from error
    if not isinstance(payload, dict):
        raise ToolArgumentError("cursor", "task.list cursor is invalid")
    version = payload.get("v")
    legacy = version == 1
    expected_fields = (
        {"v", "createdAtMs", "taskId", "goalId", "includeTerminal"}
        if legacy
        else {
            "v",
            "createdAtMs",
            "taskId",
            "goalId",
            "runtimeWorkspaceId",
            "includeTerminal",
        }
    )
    if (
        version not in {1, 2}
        or set(payload) != expected_fields
        or type(payload.get("createdAtMs")) is not int
        or payload["createdAtMs"] < 0
        or not isinstance(payload.get("taskId"), str)
        or not payload["taskId"].startswith("task:")
        or payload["taskId"] != payload["taskId"].strip()
        or (payload.get("goalId") is not None and not isinstance(payload.get("goalId"), str))
        or (
            not legacy
            and payload.get("runtimeWorkspaceId") is not None
            and not isinstance(payload.get("runtimeWorkspaceId"), str)
        )
        or type(payload.get("includeTerminal")) is not bool
    ):
        raise ToolArgumentError("cursor", "task.list cursor is invalid")
    cursor_workspace_id = None if legacy else payload["runtimeWorkspaceId"]
    if (
        payload["goalId"] != goal_id
        or cursor_workspace_id != runtime_workspace_id
        or payload["includeTerminal"] is not include_terminal
    ):
        raise ToolArgumentError(
            "cursor", "task.list cursor does not match the current query scope"
        )
    return payload["createdAtMs"], payload["taskId"]


def _operation_local_integrity_scope() -> dict[str, object]:
    return {
        "scope": "operation-local",
        "journal": "global-schema-and-relational-invariants",
        "cas": "objects-consumed-by-this-operation",
        "globalCasHealthClaimed": False,
    }


def _global_integrity_scope(detail: str) -> dict[str, object]:
    if detail == "summary":
        return {
            "scope": "startup-global",
            "journal": "global-schema-and-relational-invariants",
            "cas": "startup-critical-retained-objects",
            "doctor": None,
            "globalCasHealthClaimed": False,
        }
    return {
        "scope": "global",
        "journal": "global-schema-and-relational-invariants",
        "cas": "all-retained-cas-objects",
        "doctor": "full-history" if detail == "history" else "full-current",
        "globalCasHealthClaimed": True,
    }


def _current_deployment_identity() -> dict[str, object]:
    try:
        raw = inspect_deployment()
    except (OSError, ValueError) as error:
        return {"status": "unavailable", "reason": _bounded_message(error)}
    module_path = Path(__file__).resolve()
    release_path = Path(str(raw["currentRelease"])).resolve()
    if not module_path.is_relative_to(release_path):
        return {
            "status": "unbound",
            "reason": "running Host MCP module is not loaded from installed current release",
        }
    return {
        "status": "observed",
        "releaseId": raw["releaseId"],
        "deployedRevision": raw["deployedRevision"],
    }


def _host_status(
    state_root: Path,
    *,
    detail: str,
    recent_limit: int,
) -> dict[str, object]:
    if detail not in {"summary", "integrity", "history"}:
        raise ToolArgumentError(
            "detail", "host.status detail must be summary, integrity, or history"
        )
    if type(recent_limit) is not int or recent_limit < 0 or recent_limit > 20:
        raise ToolArgumentError("recentLimit", "recentLimit must be in [0, 20]")
    observed_at_ms = _wall_clock_ms()
    deployment = _current_deployment_identity()

    with HostStorage(state_root, update_validation_cache=False) as storage:
        states = storage.journal.task_counts_by_state()
        task_count = storage.journal.task_count()
        terminal_count = sum(
            count for state, count in states.items() if TaskState(state).terminal
        )
        lease_count = int(
            storage.journal.connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        )
        authority = {
            "journalSchema": schema_version(storage.journal.connection),
            "events": storage.journal.event_count(),
            "objectRefs": storage.journal.object_ref_count(),
            "validatedObjects": storage.journal.object_validation_count(),
            "tasks": task_count,
            "terminalTasks": terminal_count,
            "tasksByState": states,
            "leases": lease_count,
            "startupValidation": {
                "cachedObjects": storage.validation_summary.cached_objects,
                "hashedObjects": storage.validation_summary.hashed_objects,
                "taskHeads": storage.validation_summary.task_heads,
                "full": storage.validation_summary.full,
                "cacheUpdated": False,
            },
        }
        rows = storage.journal.connection.execute(
            "SELECT e.stream_id, e.stream_revision, e.event_kind, e.payload_digest, "
            "e.caused_by_event_id, e.recorded_at_ms, p.state "
            "FROM events e JOIN task_projection p ON p.task_id = e.stream_id "
            "WHERE e.stream_kind = 'task' ORDER BY e.sequence DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()
        recent = [
            {
                "taskId": str(row["stream_id"]),
                "revision": int(row["stream_revision"]),
                "eventKind": str(row["event_kind"]),
                "recordedAtMs": int(row["recorded_at_ms"]),
                "ageMs": max(0, observed_at_ms - int(row["recorded_at_ms"])),
                "payloadDigest": str(row["payload_digest"]),
                "causedByEventId": row["caused_by_event_id"],
                "currentState": str(row["state"]),
            }
            for row in rows
        ]
        board_summary = {
            "messages": storage.journal.board_message_count(),
            "lastSequence": storage.journal.board_last_sequence(),
            "truthRole": "durable-collaboration-messages",
        }
        latest_news = storage.journal.news_edition_pointer(edition_id=None, revision=None)
        news_summary = {
            "editions": storage.journal.news_edition_count(),
            "publications": storage.journal.news_publication_count(),
            "latestEditionId": None if latest_news is None else latest_news.edition_id,
            "latestRevision": None if latest_news is None else latest_news.revision,
            "truthRole": "external-news-projection-not-world-truth",
        }
        continuity_counts = {"active": 0, "terminal": 0}
        for task_id in storage.journal.task_ids():
            descriptor = storage.read_task_descriptor(task_id)
            if descriptor is None or descriptor.workload_id != EXTERNAL_CONTINUITY_WORKLOAD_ID:
                continue
            projection = storage.journal.get_task(task_id)
            if projection is None:
                raise RuntimeError("Task disappeared during Host status projection")
            key = "terminal" if projection.state.terminal else "active"
            continuity_counts[key] += 1

    doctor = None
    if detail != "summary":
        doctor = doctor_state(
            state_root,
            check_history=detail == "history",
        )

    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-status",
        "observedAtMs": observed_at_ms,
        "detail": detail,
        "interface": {
            "surfaceVersion": HOST_MCP_SURFACE_VERSION,
            "toolCount": len(HOST_MCP_TOOL_NAMES),
            "toolNames": list(HOST_MCP_TOOL_NAMES),
            "readTools": [
                "host.status",
                "board.list",
                "news.list",
                "news.read",
                "task.observe",
                "task.list",
                "task.resume",
            ],
            "writeTools": ["board.post", "news.publish", "task.adopt", "task.checkpoint"],
            "runtimeProxy": False,
        },
        "authority": authority,
        "board": board_summary,
        "news": news_summary,
        "deployment": deployment,
        "continuity": continuity_counts,
        "recentActivity": recent,
        "doctor": doctor,
        "truthBoundary": {
            "host": "authoritative for Host Journal/CAS and continuity projection",
            "deployment": "read-only installed release identity projection",
            "runtime": "not checked; Runtime remains independent physical authority",
        },
    }


def _list_board_messages(
    state_root: Path, *, after_sequence: int | None, limit: int
) -> dict[str, object]:
    if after_sequence is not None and (
        type(after_sequence) is not int or after_sequence < 0
    ):
        raise ToolArgumentError(
            "afterSequence", "board.list afterSequence must be null or non-negative"
        )
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ToolArgumentError("limit", "board.list limit must be in [1, 100]")
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
        return HostMessageBoard(storage).list(
            after_sequence=after_sequence, limit=limit
        )


def _post_board_message(
    state_root: Path,
    *,
    client_message_id: str,
    author_label: str,
    message: str,
    message_kind: str,
    topic: str | None,
    reply_to_client_message_id: str | None,
) -> dict[str, object]:
    with HostStorage(state_root) as storage:
        try:
            return HostMessageBoard(storage).post(
                client_message_id=client_message_id,
                author_label=author_label,
                message_kind=message_kind,
                message=message,
                topic=topic,
                reply_to_client_message_id=reply_to_client_message_id,
                recorded_at_ms=_wall_clock_ms(),
            ).to_dict()
        except EventConflict as error:
            field = (
                "replyToClientMessageId"
                if "reply target" in str(error)
                else "clientMessageId"
            )
            raise ToolArgumentError(field, str(error)) from error


def _list_news(
    state_root: Path, *, limit: int, cursor: str | None, from_date: str | None, to_date: str | None
) -> dict[str, object]:
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
        return HostDailyNews(storage).list(
            limit=limit, cursor=cursor, from_date=from_date, to_date=to_date
        )


def _read_news(
    state_root: Path, *, edition_id: str | None, revision: int | None,
    sections: tuple[str, ...], categories: tuple[str, ...], thread_keys: tuple[str, ...],
    include_rendered_brief: bool,
) -> dict[str, object]:
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
        return HostDailyNews(storage).read(
            edition_id=edition_id, revision=revision, sections=sections, categories=categories,
            thread_keys=thread_keys, include_rendered_brief=include_rendered_brief,
        )


def _publish_news(
    state_root: Path, *, client_publish_id: str, edition_id: str, expected_revision: int,
    edition: dict[str, Any],
) -> dict[str, object]:
    with HostStorage(state_root) as storage:
        return HostDailyNews(storage).publish(
            client_publish_id=client_publish_id, edition_id=edition_id,
            expected_revision=expected_revision, edition=edition, recorded_at_ms=_wall_clock_ms(),
        ).to_dict()


def _observe_task(
    state_root: Path,
    *,
    task_id: str,
    expected_revision: int | None,
    event_limit: int,
) -> dict[str, object]:
    if not task_id.startswith("task:") or task_id != task_id.strip():
        raise ToolArgumentError("taskId", "Task identity must start with task:")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 1
    ):
        raise ToolArgumentError(
            "expectedRevision", "expectedRevision must be a positive integer"
        )
    if type(event_limit) is not int or event_limit < 0 or event_limit > 20:
        raise ToolArgumentError("eventLimit", "eventLimit must be in [0, 20]")

    observed_at_ms = _wall_clock_ms()
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
        snapshot = storage.read_task_event(task_id)
        projection = snapshot.projection
        if expected_revision is not None and projection.revision != expected_revision:
            raise TaskRevisionMismatch(
                f"Task revision is {projection.revision}, expected {expected_revision}"
            )
        descriptor = storage.read_task_descriptor(task_id)
        workload_id = None if descriptor is None else descriptor.workload_id
        handoff = operator_handoff(
            storage, task_id, expected_revision=projection.revision
        )
        head_row = storage.journal.connection.execute(
            "SELECT event_id, event_kind, payload_digest, caused_by_event_id, recorded_at_ms "
            "FROM events WHERE stream_id = ? AND stream_revision = ?",
            (task_id, projection.revision),
        ).fetchone()
        if head_row is None:
            raise JournalCorruption(f"Task head Event is missing: {task_id}")
        event_rows = storage.journal.connection.execute(
            "SELECT stream_revision, event_kind, payload_digest, caused_by_event_id, "
            "recorded_at_ms FROM events WHERE stream_id = ? "
            "ORDER BY stream_revision DESC LIMIT ?",
            (task_id, event_limit),
        ).fetchall()
        timeline = [
            {
                "revision": int(row["stream_revision"]),
                "eventKind": str(row["event_kind"]),
                "recordedAtMs": int(row["recorded_at_ms"]),
                "payloadDigest": str(row["payload_digest"]),
                "causedByEventId": row["caused_by_event_id"],
            }
            for row in event_rows
        ]

        external = workload_id == EXTERNAL_CONTINUITY_WORKLOAD_ID
        continuity = None
        recovery = None
        continuity_host = (
            ExternalContinuityHost(storage, clock_ms=_wall_clock_ms)
            if external
            else None
        )
        if continuity_host is not None:
            for item in timeline:
                record_at_revision = continuity_host.checkpoint_at_revision(
                    task_id, int(item["revision"])
                )
                if record_at_revision is not None:
                    item["writerLabel"] = record_at_revision.writer_label
                    item["writerIdentityRole"] = (
                        "self-asserted-label"
                        if record_at_revision.writer_label is not None
                        else "unrecorded"
                    )
            record = continuity_host.checkpoint_at_revision(
                task_id, projection.revision
            )
            if record is not None:
                objective_preview, objective_truncated = _discovery_preview(
                    record.checkpoint.objective
                )
                frontier_preview, frontier_truncated = _discovery_preview(
                    record.checkpoint.frontier
                )
                continuity = {
                    "checkpointRevision": record.task_revision,
                    "checkpointDigest": record.checkpoint_digest,
                    "writerLabel": record.writer_label,
                    "writerIdentityRole": (
                        "self-asserted-label"
                        if record.writer_label is not None
                        else "unrecorded"
                    ),
                    "objectivePreview": objective_preview,
                    "objectiveTruncated": objective_truncated,
                    "frontierPreview": frontier_preview,
                    "frontierTruncated": frontier_truncated,
                    "nextActions": _item_previews(record.checkpoint.next_actions),
                    "unresolved": _item_previews(record.checkpoint.unresolved),
                }

        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-task-observation",
            "observedAtMs": observed_at_ms,
            "activityAgeMs": max(0, observed_at_ms - projection.updated_at_ms),
            "projection": projection.to_dict(),
            "workloadId": workload_id,
            "externalContinuity": external,
            "head": {
                "eventId": str(head_row["event_id"]),
                "eventKind": str(head_row["event_kind"]),
                "recordedAtMs": int(head_row["recorded_at_ms"]),
                "payloadDigest": str(head_row["payload_digest"]),
                "causedByEventId": head_row["caused_by_event_id"],
            },
            "handoff": handoff.to_dict(),
            "handoffDigest": handoff.digest,
            "recovery": recovery,
            "continuity": continuity,
            "recentEvents": timeline,
            "truthBoundary": (
                "semantic continuity only; revalidate Runtime/Git/domain facts with their owner"
                if external
                else "Host Task metadata/recovery projection only; external owners remain authoritative"
            ),
        }


def _list_host_tasks(
    state_root: Path,
    *,
    goal_id: str | None,
    runtime_workspace_id: str | None = None,
    limit: int,
    cursor: str | None = None,
    include_terminal: bool = False,
) -> dict[str, object]:
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ToolArgumentError("limit", "task.list limit must be in [1, 100]")
    if goal_id is not None and (
        not goal_id.startswith("goal:") or goal_id != goal_id.strip()
    ):
        raise ToolArgumentError("goalId", "Goal identity must start with goal:")
    if runtime_workspace_id is not None and (
        not runtime_workspace_id
        or runtime_workspace_id != runtime_workspace_id.strip()
        or len(runtime_workspace_id) > 512
    ):
        raise ToolArgumentError(
            "runtimeWorkspaceId",
            "runtimeWorkspaceId must be null or 1-512 trimmed characters",
        )
    after = _parse_task_cursor(
        cursor,
        goal_id=goal_id,
        runtime_workspace_id=runtime_workspace_id,
        include_terminal=include_terminal,
    )
    matches: list[tuple[int, dict[str, object]]] = []
    scan_after = after
    scan_batch = 256
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
        while len(matches) <= limit:
            clauses: list[str] = []
            params: list[object] = []
            if goal_id is not None:
                clauses.append("p.goal_id = ?")
                params.append(goal_id)
            if not include_terminal:
                clauses.append("p.state NOT IN ('completed', 'failed', 'cancelled')")
            if scan_after is not None:
                created_at_ms, task_id = scan_after
                clauses.append(
                    "(s.created_at_ms < ? OR (s.created_at_ms = ? AND p.task_id > ?))"
                )
                params.extend((created_at_ms, created_at_ms, task_id))
            where = "" if not clauses else " WHERE " + " AND ".join(clauses)
            descriptor_filter = (
                " EXISTS (SELECT 1 FROM events e "
                "JOIN event_object_refs r ON r.event_id = e.event_id AND r.role = 'reference' "
                "JOIN object_refs o ON o.digest = r.digest AND o.kind = 'task-descriptor' "
                "WHERE e.stream_id = p.task_id AND e.stream_revision = 1)"
            )
            where = (
                " WHERE " + descriptor_filter
                if not clauses
                else where + " AND" + descriptor_filter
            )
            rows = storage.journal.connection.execute(
                "SELECT p.task_id, s.created_at_ms FROM task_projection p "
                "JOIN streams s ON s.stream_id = p.task_id"
                + where
                + " ORDER BY s.created_at_ms DESC, p.task_id LIMIT ?",
                (*params, scan_batch),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                task_id = str(row["task_id"])
                created_at_ms = int(row["created_at_ms"])
                descriptor = storage.read_task_descriptor(task_id)
                if (
                    descriptor is None
                    or descriptor.workload_id != EXTERNAL_CONTINUITY_WORKLOAD_ID
                ):
                    continue
                task = storage.journal.get_task(task_id)
                if task is None:
                    raise RuntimeError("Task disappeared during list projection")
                if not include_terminal and task.state.terminal:
                    continue
                checkpoint = ExternalContinuityHost(
                    storage, clock_ms=_wall_clock_ms
                ).checkpoint_at_revision(task_id, task.revision)
                semantic_summary = None
                if runtime_workspace_id is not None and (
                    checkpoint is None
                    or checkpoint.checkpoint.runtime is None
                    or checkpoint.checkpoint.runtime.workspace_id != runtime_workspace_id
                ):
                    continue
                if checkpoint is not None:
                    objective_preview, objective_truncated = _discovery_preview(
                        checkpoint.checkpoint.objective
                    )
                    frontier_preview, frontier_truncated = _discovery_preview(
                        checkpoint.checkpoint.frontier
                    )
                    runtime_navigation_hint = None
                    if checkpoint.checkpoint.runtime is not None:
                        runtime_navigation_hint = {
                            "workspaceId": checkpoint.checkpoint.runtime.workspace_id,
                            "truthRole": "host-retained-runtime-navigation-hint",
                            "interpretation": (
                                "navigation hint from this exact current Host WorkingCheckpoint only; "
                                "Runtime currentness and semantic claimant standing are not validated; "
                                "the hint does not authorize physical Workspace retention or closure; "
                                "revalidate exact Runtime state before any carrier disposition; "
                                "missing Runtime mechanics is not a Human decision requirement"
                            ),
                        }
                    semantic_summary = {
                        "objectivePreview": objective_preview,
                        "objectiveTruncated": objective_truncated,
                        "frontierPreview": frontier_preview,
                        "frontierTruncated": frontier_truncated,
                        "checkpointRevision": checkpoint.task_revision,
                        "checkpointDigest": checkpoint.checkpoint_digest,
                        "runtimeNavigationHint": runtime_navigation_hint,
                    }
                matches.append(
                    (
                        created_at_ms,
                        {
                            "projection": task.to_dict(),
                            "createdAtMs": created_at_ms,
                            "workloadId": descriptor.workload_id,
                            "externalContinuity": True,
                            "semanticSummary": semantic_summary,
                        },
                    )
                )
                if len(matches) > limit:
                    break
            if len(matches) > limit or len(rows) < scan_batch:
                break
            last = rows[-1]
            scan_after = (int(last["created_at_ms"]), str(last["task_id"]))

    has_more = len(matches) > limit
    visible = matches[:limit]
    next_cursor = None
    if has_more and visible:
        created_at_ms, item = visible[-1]
        next_cursor = _task_cursor(
            created_at_ms,
            str(item["projection"]["taskId"]),
            goal_id=goal_id,
            runtime_workspace_id=runtime_workspace_id,
            include_terminal=include_terminal,
        )
    return {
        "schemaVersion": 2,
        "kind": "ordivon.host-task-list",
        "scope": "external-continuity",
        "tasks": [item for _, item in visible],
        "hasMore": has_more,
        "nextCursor": next_cursor,
    }


def _resume_task(
    state_root: Path,
    *,
    task_id: str,
    expected_revision: int | None,
) -> dict[str, object]:
    if not task_id.startswith("task:") or task_id != task_id.strip():
        raise ToolArgumentError("taskId", "Task identity must start with task:")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 1
    ):
        raise ToolArgumentError(
            "expectedRevision", "expectedRevision must be a positive integer"
        )
    with HostStorage(
        state_root, validation_mode="targeted", update_validation_cache=False
    ) as storage:
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
    writer_label: str | None = None,
) -> dict[str, object]:
    if not task_id.startswith("task:") or task_id != task_id.strip():
        raise ToolArgumentError("taskId", "Task identity must start with task:")
    if not goal_id.startswith("goal:") or goal_id != goal_id.strip():
        raise ToolArgumentError("goalId", "Goal identity must start with goal:")
    try:
        writer_label = validate_writer_label(writer_label)
    except ValueError as error:
        raise ToolArgumentError("writerLabel", str(error)) from error
    try:
        checkpoint = WorkingCheckpoint.from_dict(checkpoint_value)
    except (ValueError, TypeError) as error:
        raise ToolArgumentError("initialCheckpoint", str(error)) from error
    if checkpoint.task_id != task_id:
        raise ToolArgumentError(
            "initialCheckpoint.taskId",
            "WorkingCheckpoint taskId must equal the taskId Tool argument",
        )
    with HostStorage(state_root) as storage:
        return ExternalContinuityHost(storage, clock_ms=_wall_clock_ms).adopt(
            task_id=task_id,
            goal_id=goal_id,
            initial_checkpoint=checkpoint,
            writer_label=writer_label,
        ).to_dict()


def _checkpoint_task(
    state_root: Path,
    *,
    task_id: str,
    expected_revision: int,
    checkpoint_value: dict[str, Any],
    disposition: str = "continue",
    writer_label: str | None = None,
) -> dict[str, object]:
    if not task_id.startswith("task:") or task_id != task_id.strip():
        raise ToolArgumentError("taskId", "Task identity must start with task:")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ToolArgumentError(
            "expectedRevision", "expectedRevision must be a positive integer"
        )
    try:
        writer_label = validate_writer_label(writer_label)
    except ValueError as error:
        raise ToolArgumentError("writerLabel", str(error)) from error
    if disposition not in {"continue", "complete", "abandon"}:
        raise ToolArgumentError(
            "continuityDisposition",
            "continuityDisposition must be continue, complete, or abandon",
        )
    if not isinstance(checkpoint_value, dict) or not checkpoint_value:
        raise ToolArgumentError(
            "checkpoint", "checkpoint must be a non-empty full checkpoint or patch"
        )
    full_markers = {"schemaVersion", "kind", "truthRole", "taskId"}
    is_full = bool(full_markers & set(checkpoint_value))
    with HostStorage(state_root) as storage:
        continuity = ExternalContinuityHost(storage, clock_ms=_wall_clock_ms)
        if disposition != "continue" and not is_full:
            current = storage.journal.get_task(task_id)
            if current is not None and current.revision == expected_revision:
                raise ToolArgumentError(
                    "checkpoint",
                    "new terminal continuity transition requires a complete WorkingCheckpoint",
                )
        if not is_full:
            allowed = {
                "objective",
                "frontier",
                "established",
                "unresolved",
                "rejected",
                "constraints",
                "nextActions",
                "runtime",
            }
            if not set(checkpoint_value).issubset(allowed):
                raise ToolArgumentError(
                    "checkpoint",
                    "checkpoint patch contains unsupported fields",
                )
            base_record = continuity.checkpoint_at_revision(
                task_id, expected_revision
            )
            if base_record is None:
                raise ToolArgumentError(
                    "checkpoint",
                    "expectedRevision has no WorkingCheckpoint to inherit from",
                )
            candidate = base_record.checkpoint.to_dict()
            candidate.update(checkpoint_value)
            try:
                checkpoint = WorkingCheckpoint.from_dict(candidate)
            except (ValueError, TypeError) as error:
                raise ToolArgumentError("checkpoint", str(error)) from error
        else:
            try:
                checkpoint = WorkingCheckpoint.from_dict(checkpoint_value)
            except (ValueError, TypeError) as error:
                raise ToolArgumentError("checkpoint", str(error)) from error
            if checkpoint.task_id != task_id:
                raise ToolArgumentError(
                    "checkpoint.taskId",
                    "WorkingCheckpoint taskId must equal the taskId Tool argument",
                )
        return continuity.checkpoint(
            task_id=task_id,
            expected_revision=expected_revision,
            checkpoint=checkpoint,
            disposition=disposition,
            writer_label=writer_label,
        ).to_dict()


async def _run_tool(
    operation: Callable[[], dict[str, object]],
    *,
    write: bool,
    server_interface: dict[str, object] | None = None,
    result_meta: dict[str, object] | None = None,
) -> CallToolResult:
    try:
        result = await asyncio.to_thread(operation)
    except Exception as error:
        return _error_result(
            error,
            write=write,
            server_interface=server_interface,
            result_meta=result_meta,
        )
    return _success_result(
        result, server_interface=server_interface, result_meta=result_meta
    )


def _success_result(
    value: dict[str, object],
    *,
    server_interface: dict[str, object] | None = None,
    result_meta: dict[str, object] | None = None,
) -> CallToolResult:
    envelope = dict(value)
    if server_interface:
        envelope["serverInterface"] = dict(server_interface)
    return CallToolResult(
        meta=None if result_meta is None else dict(result_meta),
        content=[TextContent(text=_json_text(envelope))],
        structuredContent=envelope,
        isError=False,
    )


def _error_result(
    error: Exception,
    *,
    write: bool,
    server_interface: dict[str, object] | None = None,
    result_meta: dict[str, object] | None = None,
) -> CallToolResult:
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
    elif isinstance(error, ToolArgumentError):
        code = "INVALID_ARGUMENT"
        message = _bounded_message(error)
        field = error.field
        retry_class = "fix_request"
        commit_state = "not_committed"
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
    if server_interface:
        envelope["serverInterface"] = dict(server_interface)
    return CallToolResult(
        meta=None if result_meta is None else dict(result_meta),
        content=[TextContent(text=_json_text(envelope))],
        structuredContent=envelope,
        isError=True,
    )


def _transport_security(settings: HostMcpSettings) -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]
    if settings.public_origin is not None:
        allowed_hosts.append(_public_origin_host(settings.public_origin))
        allowed_origins.append(settings.public_origin)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _public_origin_host(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"https://{parsed.netloc}"
    ):
        raise ValueError(
            "Host MCP public origin must be one canonical HTTPS origin without path/query/fragment"
        )
    return parsed.netloc


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


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


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
        return "0.2.0"


def _bounded_message(error: Exception) -> str:
    message = str(error).strip() or type(error).__name__
    return message[:512]


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
