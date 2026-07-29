from __future__ import annotations

from dataclasses import dataclass

from anc_canonical import JsonValue, validate_json_value


def _digest(value: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("invocation digest must be sha256:<64 lowercase hex>")
    return value


@dataclass(frozen=True, slots=True)
class ModelInvocationIntent:
    invocation_id: str
    task_id: str
    context_digest: str
    context_object_digest: str
    gateway_id: str

    def __post_init__(self) -> None:
        if not self.invocation_id.startswith("invocation:"):
            raise ValueError("model invocation identity must start with invocation:")
        if not self.task_id.startswith("task:"):
            raise ValueError("model invocation Task identity is invalid")
        _digest(self.context_digest)
        _digest(self.context_object_digest)
        if not self.gateway_id or self.gateway_id != self.gateway_id.strip():
            raise ValueError("model gateway identity is required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.model-invocation-intent",
            "invocationId": self.invocation_id,
            "taskId": self.task_id,
            "contextDigest": self.context_digest,
            "contextObjectDigest": self.context_object_digest,
            "gatewayId": self.gateway_id,
        }


@dataclass(frozen=True, slots=True)
class ModelInvocationObservation:
    invocation_id: str
    gateway_id: str
    decision_object_digest: str
    evidence: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.invocation_id.startswith("invocation:"):
            raise ValueError("model invocation observation identity is invalid")
        if not self.gateway_id:
            raise ValueError("model invocation observation gateway is required")
        _digest(self.decision_object_digest)
        validate_json_value(self.evidence)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.model-invocation-observation",
            "invocationId": self.invocation_id,
            "gatewayId": self.gateway_id,
            "decisionObjectDigest": self.decision_object_digest,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ModelInvocationOutputObservation:
    invocation_id: str
    gateway_id: str
    output_kind: str
    output_object_digest: str
    evidence: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.invocation_id.startswith("invocation:"):
            raise ValueError("model invocation output identity is invalid")
        if not self.gateway_id or self.gateway_id != self.gateway_id.strip():
            raise ValueError("model invocation output gateway is required")
        if not self.output_kind or self.output_kind != self.output_kind.strip():
            raise ValueError("model invocation output kind is required")
        _digest(self.output_object_digest)
        validate_json_value(self.evidence)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.model-invocation-output-observation",
            "invocationId": self.invocation_id,
            "gatewayId": self.gateway_id,
            "outputKind": self.output_kind,
            "outputObjectDigest": self.output_object_digest,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ModelInvocationReceipt:
    invocation_id: str
    intent_object_digest: str
    observation_object_digest: str
    admission_object_digest: str

    def __post_init__(self) -> None:
        if not self.invocation_id.startswith("invocation:"):
            raise ValueError("model invocation receipt identity is invalid")
        for value in (
            self.intent_object_digest,
            self.observation_object_digest,
            self.admission_object_digest,
        ):
            _digest(value)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.model-invocation-receipt",
            "invocationId": self.invocation_id,
            "intentObjectDigest": self.intent_object_digest,
            "observationObjectDigest": self.observation_object_digest,
            "admissionObjectDigest": self.admission_object_digest,
        }
