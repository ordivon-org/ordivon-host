from __future__ import annotations

from typing import Any

from ..runtime import McpRuntimeClient, RuntimeTransportError


class DropFirstSuccessfulToolResponse:
    def __init__(self, client: McpRuntimeClient, operation: str) -> None:
        if not operation:
            raise ValueError("fault injection operation is required")
        self.client = client
        self.operation = operation
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
        if name == self.operation and not self.response_dropped:
            self.response_dropped = True
            raise RuntimeTransportError(
                f"injected response loss after Runtime accepted {self.operation}"
            )
        return result


class DropFirstSuccessfulExecResponse(DropFirstSuccessfulToolResponse):
    def __init__(self, client: McpRuntimeClient) -> None:
        super().__init__(client, "workspace.exec")
