from __future__ import annotations

from typing import Any

from ..runtime import McpRuntimeClient, RuntimeTransportError


class DropFirstSuccessfulExecResponse:
    def __init__(self, client: McpRuntimeClient) -> None:
        self.client = client
        self.calls: list[str] = []
        self.response_dropped = False

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return self.client.initialize()

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        return self.client.list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        result = self.client.call_tool(name, arguments)
        if name == "workspace.exec" and not self.response_dropped:
            self.response_dropped = True
            raise RuntimeTransportError(
                "injected response loss after Runtime accepted workspace.exec"
            )
        return result
