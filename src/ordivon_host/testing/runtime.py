from __future__ import annotations

from dataclasses import dataclass, field
import re
import subprocess
import time
from typing import Any

from anc_canonical import JsonValue, canonical_digest

from ..runtime import (
    McpRuntimeClient,
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)

_SERVICE = re.compile(r"^[A-Za-z0-9_.@-]+$")


@dataclass(frozen=True, slots=True)
class RuntimeClientFactory:
    endpoint: str
    token: str = field(repr=False)
    client_prefix: str
    client_version: str = "0.2.0"

    def client(self, label: str, *, initialize: bool = False) -> McpRuntimeClient:
        client = McpRuntimeClient(
            self.endpoint,
            self.token,
            client_name=f"{self.client_prefix}-{label}",
            client_version=self.client_version,
        )
        if initialize:
            client.initialize()
        return client


def jobs_for_request(
    client: McpRuntimeClient,
    client_request_id: str,
) -> list[dict[str, JsonValue]]:
    filtered = _supports_client_request_filter(client)
    cursor: dict[str, JsonValue] | None = None
    seen_cursors: set[str] = set()
    matches: list[dict[str, JsonValue]] = []
    for _ in range(100):
        arguments: dict[str, Any] = {"limit": 100}
        if filtered:
            arguments["clientRequestId"] = client_request_id
        if cursor is not None:
            arguments["cursor"] = cursor
        page = client.call_tool("task.list", arguments)
        jobs = page.get("jobs")
        if not isinstance(jobs, list):
            raise AssertionError("task.list omitted jobs")
        for job in jobs:
            if not isinstance(job, dict):
                raise AssertionError("task.list returned a non-object Job")
            observed_request_id = job.get("clientRequestId")
            if filtered and observed_request_id != client_request_id:
                raise AssertionError(
                    "filtered task.list returned another clientRequestId"
                )
            if observed_request_id == client_request_id:
                matches.append(job)
        next_cursor = page.get("nextCursor")
        if next_cursor is None:
            return matches
        if not isinstance(next_cursor, dict):
            raise AssertionError("task.list returned an invalid cursor")
        digest = canonical_digest(next_cursor)
        if digest in seen_cursors:
            raise AssertionError("task.list repeated a pagination cursor")
        seen_cursors.add(digest)
        cursor = next_cursor
    raise AssertionError("task.list pagination exceeded the scenario bound")


def workspace_absent(client: McpRuntimeClient, workspace_id: str) -> bool:
    try:
        client.call_tool(
            "workspace.get",
            {"schemaVersion": 1, "workspaceId": workspace_id},
        )
    except RuntimeToolRejected as error:
        return (
            error.detail.code == "INVALID_REQUEST"
            and error.detail.field == "workspaceId"
            and error.detail.commit_state == "not_committed"
        )
    return False


def service_state(service: str) -> dict[str, JsonValue]:
    _validate_service(service)
    output = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            service,
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "ExecMainStartTimestampMonotonic",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return {
        "mainPid": int(values.get("MainPID", "0")),
        "activeState": values.get("ActiveState", ""),
        "subState": values.get("SubState", ""),
        "startTimestampMonotonic": values.get(
            "ExecMainStartTimestampMonotonic", ""
        ),
    }


def restart_runtime(service: str) -> None:
    _validate_service(service)
    subprocess.run(["/usr/bin/systemctl", "restart", service], check=True)


def wait_runtime_ready(
    service: str,
    factory: RuntimeClientFactory,
    *,
    timeout_seconds: float = 20.0,
) -> None:
    _validate_service(service)
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        active = (
            subprocess.run(
                ["/usr/bin/systemctl", "is-active", "--quiet", service],
                check=False,
            ).returncode
            == 0
        )
        if active:
            probe = factory.client("readiness")
            probe.timeout_seconds = 1.0
            try:
                probe.initialize()
                return
            except (RuntimeTransportError, RuntimeProtocolError) as error:
                last_error = error
        time.sleep(0.05)
    raise RuntimeError(f"Runtime did not become MCP-ready: {service}: {last_error}")


def _supports_client_request_filter(client: McpRuntimeClient) -> bool:
    for tool in client.list_tools():
        if tool.get("name") != "task.list":
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            raise AssertionError("task.list input schema is not an object")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise AssertionError("task.list input schema omitted properties")
        return "clientRequestId" in properties
    raise AssertionError("Runtime Tool catalog omitted task.list")


def _validate_service(service: str) -> None:
    if not _SERVICE.fullmatch(service):
        raise ValueError("service name contains unsupported characters")
