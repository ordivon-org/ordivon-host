from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value
from anc_tool_contract import (
    CancellationKind,
    CompletionKind,
    CorrelationKind,
    EffectClass,
    ExecutionKind,
    IdempotencySupport,
    ToolContract,
)


if TYPE_CHECKING:
    from .ordivon.model import AgentToolDefinition


class NativeToolRecoveryConsequence(StrEnum):
    OBSERVATION_ONLY = "observation-only"
    WORKSPACE_CHANGE_POSSIBLE = "workspace-change-possible"
    PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE = "process-or-external-effect-possible"
    UNKNOWN = "unknown"


_RECOVERY_RANK = {
    NativeToolRecoveryConsequence.OBSERVATION_ONLY: 0,
    NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE: 1,
    NativeToolRecoveryConsequence.PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE: 2,
    NativeToolRecoveryConsequence.UNKNOWN: 3,
}


def _text(value: str, label: str, *, max_bytes: int = 2_048) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def _tool_contract_from_dict(value: dict[str, Any]) -> ToolContract:
    expected = {
        "schemaVersion",
        "kind",
        "contractId",
        "revision",
        "providerId",
        "operation",
        "semanticAction",
        "inputSchema",
        "outputSchema",
        "execution",
        "completion",
        "effectClass",
        "idempotencySupport",
        "correlation",
        "cancellation",
        "evidence",
        "capabilityClass",
    }
    _exact(value, expected, "ToolContract")
    if value["schemaVersion"] != 1 or value["kind"] != "anc.tool-contract":
        raise ValueError("ToolContract version or kind is invalid")
    strings = (
        "contractId",
        "revision",
        "providerId",
        "operation",
        "semanticAction",
        "execution",
        "completion",
        "effectClass",
        "idempotencySupport",
        "correlation",
        "cancellation",
        "capabilityClass",
    )
    if any(not isinstance(value[field], str) for field in strings):
        raise ValueError("ToolContract identity or semantic fields must be strings")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or any(
        not isinstance(item, str) for item in evidence
    ):
        raise ValueError("ToolContract evidence must contain strings")
    validate_json_value(value["inputSchema"])
    validate_json_value(value["outputSchema"])
    return ToolContract(
        contract_id=value["contractId"],
        revision=value["revision"],
        provider_id=value["providerId"],
        operation=value["operation"],
        semantic_action=value["semanticAction"],
        input_schema=value["inputSchema"],
        output_schema=value["outputSchema"],
        execution=ExecutionKind(value["execution"]),
        completion=CompletionKind(value["completion"]),
        effect_class=EffectClass(value["effectClass"]),
        idempotency_support=IdempotencySupport(value["idempotencySupport"]),
        correlation=CorrelationKind(value["correlation"]),
        cancellation=CancellationKind(value["cancellation"]),
        evidence=tuple(evidence),
        capability_class=value["capabilityClass"],
    )


@dataclass(frozen=True, slots=True)
class NativeToolSpec:
    description: str
    contract: ToolContract
    runtime_operations: tuple[str, ...]
    recovery_consequence: NativeToolRecoveryConsequence

    def __post_init__(self) -> None:
        _text(self.description, "native Tool description", max_bytes=1_000)
        for operation in self.runtime_operations:
            _text(operation, "native Tool Runtime operation", max_bytes=200)
        if not self.runtime_operations or len(self.runtime_operations) != len(
            set(self.runtime_operations)
        ):
            raise ValueError(
                "native Tool Runtime operations must be non-empty and unique"
            )
        if (
            self.contract.effect_class is EffectClass.OPAQUE
            and self.recovery_consequence
            is NativeToolRecoveryConsequence.OBSERVATION_ONLY
        ):
            raise ValueError(
                "opaque Tool cannot have observation-only recovery consequence"
            )
        if (
            self.contract.execution is ExecutionKind.ASYNCHRONOUS
            and self.contract.correlation is CorrelationKind.NONE
        ):
            raise ValueError("asynchronous Tool requires correlation")
        if (
            self.contract.idempotency_support is IdempotencySupport.KEYED
            and self.contract.correlation is not CorrelationKind.STABLE_KEY
        ):
            raise ValueError("keyed Tool idempotency requires stable-key correlation")

    @property
    def name(self) -> str:
        return self.contract.operation

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.native-tool-spec",
            "description": self.description,
            "contract": self.contract.to_dict(),
            "runtimeOperations": list(self.runtime_operations),
            "recoveryConsequence": self.recovery_consequence.value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NativeToolSpec:
        expected = {
            "schemaVersion",
            "kind",
            "description",
            "contract",
            "runtimeOperations",
            "recoveryConsequence",
        }
        _exact(value, expected, "NativeToolSpec")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.native-tool-spec":
            raise ValueError("NativeToolSpec version or kind is invalid")
        if not isinstance(value["description"], str):
            raise ValueError("NativeToolSpec description must be a string")
        contract = value["contract"]
        operations = value["runtimeOperations"]
        consequence = value["recoveryConsequence"]
        if not isinstance(contract, dict):
            raise ValueError("NativeToolSpec contract must be an object")
        if not isinstance(operations, list) or any(
            not isinstance(item, str) for item in operations
        ):
            raise ValueError("NativeToolSpec Runtime operations must be strings")
        if not isinstance(consequence, str):
            raise ValueError("NativeToolSpec recovery consequence must be a string")
        return cls(
            description=value["description"],
            contract=_tool_contract_from_dict(contract),
            runtime_operations=tuple(operations),
            recovery_consequence=NativeToolRecoveryConsequence(consequence),
        )


@dataclass(frozen=True, slots=True)
class NativeToolCatalogSnapshot:
    runtime_descriptors: tuple[dict[str, JsonValue], ...]
    tools: tuple[NativeToolSpec, ...]

    def __post_init__(self) -> None:
        runtime_names: list[str] = []
        for descriptor in self.runtime_descriptors:
            validate_json_value(descriptor)
            if set(descriptor) != {"name", "inputSchema", "outputSchema", "execution"}:
                raise ValueError("native Tool Runtime descriptor fields differ")
            name = descriptor.get("name")
            if not isinstance(name, str):
                raise ValueError("native Tool Runtime descriptor name must be a string")
            _text(name, "native Tool Runtime descriptor name", max_bytes=200)
            runtime_names.append(name)
        if not runtime_names or len(runtime_names) != len(set(runtime_names)):
            raise ValueError(
                "native Tool Runtime descriptors must be non-empty and unique"
            )
        tool_names = [tool.name for tool in self.tools]
        if not tool_names or len(tool_names) != len(set(tool_names)):
            raise ValueError("native Tool specifications must be non-empty and unique")
        available = set(runtime_names)
        for tool in self.tools:
            missing = sorted(set(tool.runtime_operations) - available)
            if missing:
                raise ValueError(
                    f"native Tool {tool.name} references missing Runtime operations: {missing}"
                )

    @property
    def semantic_digest(self) -> str:
        return canonical_digest(
            {
                "schemaVersion": 1,
                "kind": "ordivon.native-tool-catalog-semantics",
                "runtimeDescriptors": list(self.runtime_descriptors),
                "tools": [tool.to_dict() for tool in self.tools],
            }
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def revision(self) -> str:
        return f"harness-runtime-catalog:{self.semantic_digest[7:]}"

    @property
    def runtime_operations(self) -> tuple[str, ...]:
        return tuple(str(item["name"]) for item in self.runtime_descriptors)

    @property
    def model_tools(self) -> tuple[AgentToolDefinition, ...]:
        from .ordivon.model import AgentToolDefinition

        return tuple(
            AgentToolDefinition(
                tool.name,
                tool.description,
                dict(tool.contract.input_schema),
            )
            for tool in self.tools
        )

    def tool(self, name: str) -> NativeToolSpec:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"native Tool has no semantic specification: {name}")

    def aggregate_recovery_consequence(
        self, allowed_tools: tuple[str, ...]
    ) -> NativeToolRecoveryConsequence:
        if not allowed_tools:
            return NativeToolRecoveryConsequence.OBSERVATION_ONLY
        result = NativeToolRecoveryConsequence.OBSERVATION_ONLY
        for name in allowed_tools:
            consequence = self.tool(name).recovery_consequence
            if _RECOVERY_RANK[consequence] > _RECOVERY_RANK[result]:
                result = consequence
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-runtime-catalog",
            "revision": self.revision,
            "semanticDigest": self.semantic_digest,
            "runtimeDescriptors": list(self.runtime_descriptors),
            "tools": [tool.to_dict() for tool in self.tools],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NativeToolCatalogSnapshot:
        expected = {
            "schemaVersion",
            "kind",
            "revision",
            "semanticDigest",
            "runtimeDescriptors",
            "tools",
        }
        _exact(value, expected, "NativeToolCatalogSnapshot")
        if (
            value["schemaVersion"] != 1
            or value["kind"] != "ordivon.harness-runtime-catalog"
        ):
            raise ValueError("NativeToolCatalogSnapshot version or kind is invalid")
        descriptors = value["runtimeDescriptors"]
        tools = value["tools"]
        if not isinstance(descriptors, list) or any(
            not isinstance(item, dict) for item in descriptors
        ):
            raise ValueError(
                "NativeToolCatalogSnapshot Runtime descriptors must be objects"
            )
        if not isinstance(tools, list) or any(
            not isinstance(item, dict) for item in tools
        ):
            raise ValueError("NativeToolCatalogSnapshot Tools must be objects")
        decoded = cls(
            runtime_descriptors=tuple(dict(item) for item in descriptors),
            tools=tuple(NativeToolSpec.from_dict(item) for item in tools),
        )
        if value["revision"] != decoded.revision:
            raise ValueError("NativeToolCatalogSnapshot revision differs")
        if value["semanticDigest"] != decoded.semantic_digest:
            raise ValueError("NativeToolCatalogSnapshot semantic digest differs")
        return decoded


@dataclass(frozen=True, slots=True)
class _ToolSemantics:
    runtime_operations: tuple[str, ...]
    semantic_action: str
    execution: ExecutionKind
    completion: CompletionKind
    effect_class: EffectClass
    idempotency_support: IdempotencySupport
    correlation: CorrelationKind
    cancellation: CancellationKind
    evidence: tuple[str, ...]
    capability_class: str
    recovery_consequence: NativeToolRecoveryConsequence


_TOOL_SEMANTICS: dict[str, _ToolSemantics] = {
    "read_workspace": _ToolSemantics(
        ("workspace.read",),
        "anc.object.read.v1",
        ExecutionKind.SYNCHRONOUS,
        CompletionKind.RESPONSE,
        EffectClass.OBSERVE,
        IdempotencySupport.NATURAL,
        CorrelationKind.RECEIPT,
        CancellationKind.UNSUPPORTED,
        ("observation", "version"),
        "workspace-file-read",
        NativeToolRecoveryConsequence.OBSERVATION_ONLY,
    ),
    "mutate_workspace": _ToolSemantics(
        ("workspace.mutate",),
        "anc.source.change.v1",
        ExecutionKind.SYNCHRONOUS,
        CompletionKind.ACCEPTED_VERIFICATION,
        EffectClass.CHANGE,
        IdempotencySupport.NONE,
        CorrelationKind.RECEIPT,
        CancellationKind.UNSUPPORTED,
        ("observation", "diff", "version"),
        "workspace-source-change",
        NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE,
    ),
    "diff_workspace": _ToolSemantics(
        ("workspace.diff",),
        "anc.object.read.v1",
        ExecutionKind.SYNCHRONOUS,
        CompletionKind.RESPONSE,
        EffectClass.OBSERVE,
        IdempotencySupport.NATURAL,
        CorrelationKind.RECEIPT,
        CancellationKind.UNSUPPORTED,
        ("observation", "diff"),
        "workspace-diff-read",
        NativeToolRecoveryConsequence.OBSERVATION_ONLY,
    ),
    "run_check": _ToolSemantics(
        ("workspace.exec",),
        "anc.execution.launch.v1",
        ExecutionKind.ASYNCHRONOUS,
        CompletionKind.ACCEPTED_VERIFICATION,
        EffectClass.CHANGE,
        IdempotencySupport.KEYED,
        CorrelationKind.STABLE_KEY,
        CancellationKind.SUPPORTED,
        ("observation", "artifact", "exit-status"),
        "workspace-execution-check",
        NativeToolRecoveryConsequence.PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE,
    ),
    "run_in_workspace": _ToolSemantics(
        ("workspace.exec",),
        "anc.execution.launch.v1",
        ExecutionKind.ASYNCHRONOUS,
        CompletionKind.ACCEPTED_VERIFICATION,
        EffectClass.OPAQUE,
        IdempotencySupport.KEYED,
        CorrelationKind.STABLE_KEY,
        CancellationKind.SUPPORTED,
        ("observation", "artifact"),
        "workspace-opaque-execution",
        NativeToolRecoveryConsequence.PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE,
    ),
    "observe_job": _ToolSemantics(
        ("task.observe",),
        "anc.object.read.v1",
        ExecutionKind.SYNCHRONOUS,
        CompletionKind.RESPONSE,
        EffectClass.OBSERVE,
        IdempotencySupport.NATURAL,
        CorrelationKind.RECEIPT,
        CancellationKind.UNSUPPORTED,
        ("observation", "artifact", "status"),
        "runtime-job-observation",
        NativeToolRecoveryConsequence.OBSERVATION_ONLY,
    ),
    "read_artifact": _ToolSemantics(
        ("artifact.read",),
        "anc.object.read.v1",
        ExecutionKind.SYNCHRONOUS,
        CompletionKind.RESPONSE,
        EffectClass.OBSERVE,
        IdempotencySupport.NATURAL,
        CorrelationKind.RECEIPT,
        CancellationKind.UNSUPPORTED,
        ("artifact", "digest"),
        "runtime-artifact-read",
        NativeToolRecoveryConsequence.OBSERVATION_ONLY,
    ),
}


def build_native_tool_catalog_snapshot(
    runtime_descriptors: tuple[dict[str, JsonValue], ...],
    model_tools: tuple[AgentToolDefinition, ...],
) -> NativeToolCatalogSnapshot:
    names = tuple(tool.name for tool in model_tools)
    if set(names) != set(_TOOL_SEMANTICS) or len(names) != len(_TOOL_SEMANTICS):
        raise ValueError(
            "native model Tool surface differs from the complete semantic catalog"
        )
    runtime = {str(item["name"]): item for item in runtime_descriptors}
    runtime_revision = canonical_digest(list(runtime_descriptors))
    specs: list[NativeToolSpec] = []
    for tool in model_tools:
        semantics = _TOOL_SEMANTICS.get(tool.name)
        if semantics is None:
            raise ValueError(f"native Tool has no explicit semantics: {tool.name}")
        missing = sorted(set(semantics.runtime_operations) - set(runtime))
        if missing:
            raise ValueError(
                f"native Tool {tool.name} has missing Runtime operations: {missing}"
            )
        output_schema: JsonValue = None
        if len(semantics.runtime_operations) == 1:
            output_schema = runtime[semantics.runtime_operations[0]].get("outputSchema")
        contract = ToolContract(
            contract_id=f"tool-contract:ordivon-harness/{tool.name}",
            revision=f"harness-native-tool-contract:{runtime_revision[7:]}",
            provider_id="ordivon-harness",
            operation=tool.name,
            semantic_action=semantics.semantic_action,
            input_schema=tool.input_schema,
            output_schema=output_schema,
            execution=semantics.execution,
            completion=semantics.completion,
            effect_class=semantics.effect_class,
            idempotency_support=semantics.idempotency_support,
            correlation=semantics.correlation,
            cancellation=semantics.cancellation,
            evidence=semantics.evidence,
            capability_class=semantics.capability_class,
        )
        specs.append(
            NativeToolSpec(
                description=tool.description,
                contract=contract,
                runtime_operations=semantics.runtime_operations,
                recovery_consequence=semantics.recovery_consequence,
            )
        )
    return NativeToolCatalogSnapshot(runtime_descriptors, tuple(specs))


def recovery_consequence_from_persisted(
    value: str,
) -> NativeToolRecoveryConsequence:
    aliases = {
        "read_only": NativeToolRecoveryConsequence.OBSERVATION_ONLY,
        "workspace_mutation_possible": (
            NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE
        ),
        "process_effect_possible": (
            NativeToolRecoveryConsequence.PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE
        ),
    }
    if value in aliases:
        return aliases[value]
    return NativeToolRecoveryConsequence(value)


def legacy_grant_recovery_consequence(
    allowed_tools: tuple[str, ...],
) -> NativeToolRecoveryConsequence:
    unknown = sorted(set(allowed_tools) - set(_TOOL_SEMANTICS))
    if unknown:
        raise ValueError(f"legacy native Tool Grant contains unknown Tools: {unknown}")
    result = NativeToolRecoveryConsequence.OBSERVATION_ONLY
    for name in allowed_tools:
        consequence = _TOOL_SEMANTICS[name].recovery_consequence
        if _RECOVERY_RANK[consequence] > _RECOVERY_RANK[result]:
            result = consequence
    return result
