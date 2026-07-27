from __future__ import annotations

from typing import Any

from anc_canonical import JsonValue, canonical_digest

from .client import RuntimeClient
from .errors import RuntimeProtocolError


def find_jobs_by_client_request(
    runtime: RuntimeClient,
    client_request_id: str,
    *,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    if not client_request_id:
        raise ValueError("Runtime clientRequestId is required")
    filtered = tool_accepts_property(runtime, "task.list", "clientRequestId")
    cursor: dict[str, JsonValue] | None = None
    seen_cursors: set[str] = set()
    matches: list[dict[str, Any]] = []
    for _ in range(max_pages):
        arguments: dict[str, Any] = {"limit": 100}
        if filtered:
            arguments["clientRequestId"] = client_request_id
        if cursor is not None:
            arguments["cursor"] = cursor
        page = runtime.call_tool("task.list", arguments)
        jobs = page.get("jobs")
        if not isinstance(jobs, list):
            raise RuntimeProtocolError("task.list omitted jobs")
        for job in jobs:
            if not isinstance(job, dict):
                raise RuntimeProtocolError("task.list returned a non-object Job")
            observed = job.get("clientRequestId")
            if filtered and observed != client_request_id:
                raise RuntimeProtocolError(
                    "filtered task.list returned another clientRequestId"
                )
            if observed == client_request_id:
                matches.append(job)
        next_cursor = page.get("nextCursor")
        if next_cursor is None:
            return matches
        if not isinstance(next_cursor, dict):
            raise RuntimeProtocolError("task.list returned an invalid cursor")
        typed: dict[str, JsonValue] = {}
        for key, value in next_cursor.items():
            if not isinstance(key, str) or not isinstance(value, (str, int)):
                raise RuntimeProtocolError("task.list cursor fields are invalid")
            typed[key] = value
        digest = canonical_digest(typed)
        if digest in seen_cursors:
            raise RuntimeProtocolError("task.list repeated a pagination cursor")
        seen_cursors.add(digest)
        cursor = typed
    raise RuntimeProtocolError("task.list pagination exceeded the Host bound")


def tool_accepts_property(
    runtime: RuntimeClient,
    tool_name: str,
    property_name: str,
) -> bool:
    runtime.initialize()
    for tool in runtime.list_tools():
        if tool.get("name") != tool_name:
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            raise RuntimeProtocolError(f"{tool_name} input schema is not an object")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeProtocolError(f"{tool_name} input schema omitted properties")
        return property_name in properties
    raise RuntimeProtocolError(f"Runtime Tool catalog omitted {tool_name}")
