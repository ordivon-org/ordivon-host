from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value


_RECOVERY_TRIGGERS = {
    "host_restart",
    "process_lost",
    "operator_cancelled",
    "deadline_expired",
    "provider_state_lost",
}
_GRANT_EFFECT_CLASSES = {
    "read_only",
    "workspace_mutation_possible",
    "process_effect_possible",
}
_CATALOG_STATUSES = {"matched", "drifted", "unavailable"}
_WORKSPACE_STATUSES = {"closed", "already_absent", "not_applicable", "unknown"}
_ABANDONMENT_REASONS = {
    "host_restart",
    "process_lost",
    "operator_cancelled",
    "deadline_expired",
    "provider_state_lost",
}


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def _text(value: str, label: str, *, max_bytes: int = 2_048) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _identity(value: str, prefix: str, label: str) -> str:
    _text(value, label, max_bytes=300)
    if not value.startswith(prefix + ":"):
        raise ValueError(f"{label} must start with {prefix}:")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def native_tool_grant_effect_class(allowed_tools: tuple[str, ...]) -> str:
    tools = set(allowed_tools)
    if "run_check" in tools or "run_in_workspace" in tools:
        return "process_effect_possible"
    if "mutate_workspace" in tools:
        return "workspace_mutation_possible"
    return "read_only"


@dataclass(frozen=True, slots=True)
class NativeRunRecoveryAssessment:
    assessment_id: str
    sequence: int
    harness_run_id: str
    assignment_id: str
    assignment_generation: int
    assignment_digest: str
    trigger: str
    grant_effect_class: str
    catalog_status: str
    workspace_status: str
    workspace_evidence: dict[str, JsonValue]
    unresolved_unknowns: tuple[str, ...]
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.assessment_id, "harness-run-recovery", "Run Recovery Assessment")
        _identity(self.harness_run_id, "harness-run", "Harness Run")
        _identity(self.assignment_id, "assignment", "Assignment")
        if self.sequence < 1 or self.assignment_generation < 1:
            raise ValueError("Run Recovery sequence and Assignment generation must be positive")
        _digest(self.assignment_digest, "Run Recovery Assignment digest")
        if self.trigger not in _RECOVERY_TRIGGERS:
            raise ValueError(f"unsupported Run Recovery trigger: {self.trigger}")
        if self.grant_effect_class not in _GRANT_EFFECT_CLASSES:
            raise ValueError(
                f"unsupported Run Recovery Grant effect class: {self.grant_effect_class}"
            )
        if self.catalog_status not in _CATALOG_STATUSES:
            raise ValueError(f"unsupported Run Recovery catalog status: {self.catalog_status}")
        if self.workspace_status not in _WORKSPACE_STATUSES:
            raise ValueError(
                f"unsupported Run Recovery Workspace status: {self.workspace_status}"
            )
        validate_json_value(self.workspace_evidence)
        for value in self.unresolved_unknowns:
            _text(value, "Run Recovery unresolved UNKNOWN")
        if len(self.unresolved_unknowns) != len(set(self.unresolved_unknowns)):
            raise ValueError("Run Recovery unresolved UNKNOWN values must be unique")
        if self.created_at_ms < 0:
            raise ValueError("Run Recovery time must be non-negative")
        if self.safe_to_abandon and self.unresolved_unknowns:
            raise ValueError("safe Run Recovery cannot retain unresolved UNKNOWN state")
        if not self.safe_to_abandon and not self.unresolved_unknowns:
            raise ValueError("unsafe Run Recovery must explain its unresolved UNKNOWN state")

    @property
    def safe_to_abandon(self) -> bool:
        return (
            self.grant_effect_class == "read_only"
            and self.workspace_status in {"closed", "already_absent", "not_applicable"}
            and not self.unresolved_unknowns
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.native-run-recovery-assessment",
            "assessmentId": self.assessment_id,
            "sequence": self.sequence,
            "harnessRunId": self.harness_run_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "assignmentDigest": self.assignment_digest,
            "trigger": self.trigger,
            "grantEffectClass": self.grant_effect_class,
            "catalogStatus": self.catalog_status,
            "workspaceStatus": self.workspace_status,
            "workspaceEvidence": self.workspace_evidence,
            "unresolvedUnknowns": list(self.unresolved_unknowns),
            "safeToAbandon": self.safe_to_abandon,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NativeRunRecoveryAssessment:
        expected = {
            "schemaVersion",
            "kind",
            "assessmentId",
            "sequence",
            "harnessRunId",
            "assignmentId",
            "assignmentGeneration",
            "assignmentDigest",
            "trigger",
            "grantEffectClass",
            "catalogStatus",
            "workspaceStatus",
            "workspaceEvidence",
            "unresolvedUnknowns",
            "safeToAbandon",
            "createdAtMs",
        }
        _exact(value, expected, "NativeRunRecoveryAssessment")
        if (
            value["schemaVersion"] != 1
            or value["kind"] != "ordivon.native-run-recovery-assessment"
        ):
            raise ValueError("NativeRunRecoveryAssessment version or kind is invalid")
        for field in (
            "assessmentId",
            "harnessRunId",
            "assignmentId",
            "assignmentDigest",
            "trigger",
            "grantEffectClass",
            "catalogStatus",
            "workspaceStatus",
        ):
            if not isinstance(value[field], str):
                raise ValueError(f"NativeRunRecoveryAssessment {field} must be a string")
        for field in ("sequence", "assignmentGeneration", "createdAtMs"):
            if type(value[field]) is not int:
                raise ValueError(f"NativeRunRecoveryAssessment {field} must be an integer")
        evidence = value["workspaceEvidence"]
        unknowns = value["unresolvedUnknowns"]
        safe = value["safeToAbandon"]
        if not isinstance(evidence, dict):
            raise ValueError("NativeRunRecoveryAssessment Workspace evidence must be an object")
        if not isinstance(unknowns, list) or any(not isinstance(item, str) for item in unknowns):
            raise ValueError("NativeRunRecoveryAssessment UNKNOWN values must be strings")
        if type(safe) is not bool:
            raise ValueError("NativeRunRecoveryAssessment safeToAbandon must be a boolean")
        validate_json_value(evidence)
        decoded = cls(
            assessment_id=value["assessmentId"],
            sequence=value["sequence"],
            harness_run_id=value["harnessRunId"],
            assignment_id=value["assignmentId"],
            assignment_generation=value["assignmentGeneration"],
            assignment_digest=value["assignmentDigest"],
            trigger=value["trigger"],
            grant_effect_class=value["grantEffectClass"],
            catalog_status=value["catalogStatus"],
            workspace_status=value["workspaceStatus"],
            workspace_evidence=dict(evidence),
            unresolved_unknowns=tuple(unknowns),
            created_at_ms=value["createdAtMs"],
        )
        if decoded.safe_to_abandon is not safe:
            raise ValueError("NativeRunRecoveryAssessment safeToAbandon differs")
        return decoded


@dataclass(frozen=True, slots=True)
class NativeRunAbandonment:
    abandonment_id: str
    harness_run_id: str
    assignment_id: str
    assignment_generation: int
    assignment_digest: str
    recovery_assessment_digest: str
    recovery_assessment_object_digest: str
    reason_code: str
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.abandonment_id, "harness-run-abandonment", "Run Abandonment")
        _identity(self.harness_run_id, "harness-run", "Harness Run")
        _identity(self.assignment_id, "assignment", "Assignment")
        if self.assignment_generation < 1:
            raise ValueError("Run Abandonment Assignment generation must be positive")
        _digest(self.assignment_digest, "Run Abandonment Assignment digest")
        _digest(self.recovery_assessment_digest, "Run Recovery Assessment digest")
        _digest(
            self.recovery_assessment_object_digest,
            "Run Recovery Assessment object digest",
        )
        if self.reason_code not in _ABANDONMENT_REASONS:
            raise ValueError(f"unsupported Run Abandonment reason: {self.reason_code}")
        if self.created_at_ms < 0:
            raise ValueError("Run Abandonment time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.native-run-abandonment",
            "abandonmentId": self.abandonment_id,
            "harnessRunId": self.harness_run_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "assignmentDigest": self.assignment_digest,
            "recoveryAssessmentDigest": self.recovery_assessment_digest,
            "recoveryAssessmentObjectDigest": self.recovery_assessment_object_digest,
            "reasonCode": self.reason_code,
            "replacementAllowed": True,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NativeRunAbandonment:
        expected = {
            "schemaVersion",
            "kind",
            "abandonmentId",
            "harnessRunId",
            "assignmentId",
            "assignmentGeneration",
            "assignmentDigest",
            "recoveryAssessmentDigest",
            "recoveryAssessmentObjectDigest",
            "reasonCode",
            "replacementAllowed",
            "createdAtMs",
        }
        _exact(value, expected, "NativeRunAbandonment")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.native-run-abandonment":
            raise ValueError("NativeRunAbandonment version or kind is invalid")
        for field in (
            "abandonmentId",
            "harnessRunId",
            "assignmentId",
            "assignmentDigest",
            "recoveryAssessmentDigest",
            "recoveryAssessmentObjectDigest",
            "reasonCode",
        ):
            if not isinstance(value[field], str):
                raise ValueError(f"NativeRunAbandonment {field} must be a string")
        for field in ("assignmentGeneration", "createdAtMs"):
            if type(value[field]) is not int:
                raise ValueError(f"NativeRunAbandonment {field} must be an integer")
        if value["replacementAllowed"] is not True:
            raise ValueError("NativeRunAbandonment replacementAllowed must be true")
        return cls(
            abandonment_id=value["abandonmentId"],
            harness_run_id=value["harnessRunId"],
            assignment_id=value["assignmentId"],
            assignment_generation=value["assignmentGeneration"],
            assignment_digest=value["assignmentDigest"],
            recovery_assessment_digest=value["recoveryAssessmentDigest"],
            recovery_assessment_object_digest=value["recoveryAssessmentObjectDigest"],
            reason_code=value["reasonCode"],
            created_at_ms=value["createdAtMs"],
        )
