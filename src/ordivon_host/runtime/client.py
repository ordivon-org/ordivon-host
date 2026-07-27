from __future__ import annotations

from typing import Any, Protocol


class RuntimeClient(Protocol):
    def initialize(self) -> dict[str, Any]: ...

    def list_tools(self) -> tuple[dict[str, Any], ...]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...
