from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from anc_canonical import JsonValue, canonical_digest
from anc_tool_contract import ToolContract, normalize_mcp_tool_contract


class ToolCatalogClient(Protocol):
    def list_tools(self) -> tuple[dict[str, Any], ...]: ...


_RUNTIME_OPERATIONS = (
    "workspace.close",
    "workspace.get",
    "workspace.open",
    "workspace.read",
)

_READ_PROFILE: dict[str, JsonValue] = {
    "semanticAction": "anc.object.read.v1",
    "execution": "synchronous",
    "completion": "accepted-verification",
    "effectClass": "observe",
    "idempotencySupport": "natural",
    "correlation": "receipt",
    "cancellation": "unsupported",
    "evidence": ["observation", "version"],
    "capabilityClass": "workspace-file-read",
}


_EXECUTION_RUNTIME_OPERATIONS = (
    "task.list",
    "task.observe",
    "workspace.close",
    "workspace.exec",
    "workspace.get",
    "workspace.open",
    "workspace.read",
)

_EXEC_PROFILE: dict[str, JsonValue] = {
    "semanticAction": "anc.execution.launch.v1",
    "execution": "asynchronous",
    "completion": "accepted-verification",
    "effectClass": "change",
    "idempotencySupport": "keyed",
    "correlation": "stable-key",
    "cancellation": "supported",
    "evidence": ["observation", "artifact"],
    "capabilityClass": "workspace-execution",
}


@dataclass(frozen=True, slots=True)
class RuntimeCatalog:
    revision: str
    digest: str
    read_contract: ToolContract
    operations: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "providerId": "ordivon-runtime",
            "revision": self.revision,
            "digest": self.digest,
            "operations": list(self.operations),
            "readContract": self.read_contract.to_dict(),
        }


def discover_runtime_catalog(client: ToolCatalogClient) -> RuntimeCatalog:
    catalog: dict[str, dict[str, Any]] = {}
    for raw in client.list_tools():
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Runtime Tool descriptor has no operation name")
        if name in catalog:
            raise ValueError(f"Runtime Tool catalog repeats operation: {name}")
        catalog[name] = raw
    missing = [operation for operation in _RUNTIME_OPERATIONS if operation not in catalog]
    if missing:
        raise ValueError(f"Runtime Tool catalog is missing operations: {missing}")
    selected: JsonValue = [
        {
            "name": catalog[operation]["name"],
            "inputSchema": catalog[operation].get("inputSchema"),
            "outputSchema": catalog[operation].get("outputSchema"),
            "execution": catalog[operation].get("execution"),
        }
        for operation in _RUNTIME_OPERATIONS
    ]
    digest = canonical_digest(selected)
    revision = f"mcp-catalog:{digest[7:]}"
    read_contract = normalize_mcp_tool_contract(
        catalog["workspace.read"],
        provider_id="ordivon-runtime",
        revision=revision,
        semantics=_READ_PROFILE,
    )
    return RuntimeCatalog(
        revision=revision,
        digest=digest,
        read_contract=read_contract,
        operations=_RUNTIME_OPERATIONS,
    )


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeCatalog:
    revision: str
    digest: str
    exec_contract: ToolContract
    operations: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "providerId": "ordivon-runtime",
            "revision": self.revision,
            "digest": self.digest,
            "operations": list(self.operations),
            "execContract": self.exec_contract.to_dict(),
        }


def discover_execution_runtime_catalog(
    client: ToolCatalogClient,
) -> ExecutionRuntimeCatalog:
    catalog: dict[str, dict[str, Any]] = {}
    for raw in client.list_tools():
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Runtime Tool descriptor has no operation name")
        if name in catalog:
            raise ValueError(f"Runtime Tool catalog repeats operation: {name}")
        catalog[name] = raw
    missing = [
        operation
        for operation in _EXECUTION_RUNTIME_OPERATIONS
        if operation not in catalog
    ]
    if missing:
        raise ValueError(f"Runtime execution catalog is missing operations: {missing}")
    selected: JsonValue = [
        {
            "name": catalog[operation]["name"],
            "inputSchema": catalog[operation].get("inputSchema"),
            "outputSchema": catalog[operation].get("outputSchema"),
            "execution": catalog[operation].get("execution"),
        }
        for operation in _EXECUTION_RUNTIME_OPERATIONS
    ]
    digest = canonical_digest(selected)
    revision = f"mcp-catalog:{digest[7:]}"
    exec_contract = normalize_mcp_tool_contract(
        catalog["workspace.exec"],
        provider_id="ordivon-runtime",
        revision=revision,
        semantics=_EXEC_PROFILE,
    )
    return ExecutionRuntimeCatalog(
        revision=revision,
        digest=digest,
        exec_contract=exec_contract,
        operations=_EXECUTION_RUNTIME_OPERATIONS,
    )
