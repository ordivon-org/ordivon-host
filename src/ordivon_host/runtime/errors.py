from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class RuntimeClientError(RuntimeError):
    pass


class RuntimeTransportError(RuntimeClientError):
    pass


class RuntimeProtocolError(RuntimeClientError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeErrorDetail:
    code: str
    message: str
    field: str | None
    retryable: bool
    retry_class: str | None
    commit_state: str | None
    origin: str | None
    trace_id: str | None
    raw: dict[str, Any]


class RuntimeToolRejected(RuntimeClientError):
    def __init__(self, operation: str, detail: RuntimeErrorDetail) -> None:
        super().__init__(f"{operation} rejected [{detail.code}]: {detail.message}")
        self.operation = operation
        self.detail = detail
