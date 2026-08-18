from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class McpClientError(RuntimeError):
    pass


class McpTransportError(McpClientError):
    pass


class McpProtocolError(McpClientError):
    pass


@dataclass(frozen=True, slots=True)
class McpErrorDetail:
    code: str
    message: str
    field: str | None
    retryable: bool
    retry_class: str | None
    commit_state: str | None
    origin: str | None
    trace_id: str | None
    raw: dict[str, Any]


class McpToolRejected(McpClientError):
    def __init__(self, operation: str, detail: McpErrorDetail) -> None:
        super().__init__(f"{operation} rejected [{detail.code}]: {detail.message}")
        self.operation = operation
        self.detail = detail
