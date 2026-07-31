from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..effects import ArtifactRef, StateRef

_NATIVE_TOOL_NAMES = {
    "read_workspace",
    "mutate_workspace",
    "diff_workspace",
    "run_check",
    "run_in_workspace",
    "observe_job",
    "read_artifact",
}


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def _identity(value: str, prefix: str, label: str) -> str:
    if not value.startswith(prefix + ":") or value != value.strip():
        raise ValueError(f"{label} identity must start with {prefix}:")
    if len(value.encode("utf-8")) > 300:
        raise ValueError(f"{label} identity exceeds 300 UTF-8 bytes")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _text(value: str, label: str, *, max_bytes: int = 2_048) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _unique_text(values: tuple[str, ...], label: str) -> None:
    for value in values:
        _text(value, label)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def _relative_path(value: str, label: str) -> str:
    _text(value, label, max_bytes=1_024)
    if "\\" in value or value.startswith("/"):
        raise ValueError(f"{label} must be a POSIX relative path")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must not contain empty, dot, or parent segments")
    return value


def _path_rule(value: str, label: str) -> str:
    _text(value, label, max_bytes=1_024)
    if value == "**":
        return value
    if value.endswith("/**"):
        _relative_path(value[:-3], label)
        return value
    return _relative_path(value, label)


def _artifact_refs(values: tuple[ArtifactRef, ...], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        _text(value.ref, f"{label} ref")
        _text(value.kind, f"{label} kind")
        _digest(value.digest, f"{label} digest")
        if value.ref in seen:
            raise ValueError(f"{label} refs must be unique")
        seen.add(value.ref)


@dataclass(frozen=True, slots=True)
class TaskContract:
    contract_id: str
    task_id: str
    objective: dict[str, JsonValue]
    acceptance_criteria: dict[str, JsonValue]
    constraints: tuple[str, ...] = ()
    resource_refs: tuple[StateRef, ...] = ()
    consequence_policy_ref: str | None = None

    def __post_init__(self) -> None:
        _identity(self.contract_id, "task-contract", "Task Contract")
        _identity(self.task_id, "task", "Task")
        if not self.objective or not self.acceptance_criteria:
            raise ValueError("Task Contract objective and acceptance criteria must be non-empty")
        validate_json_value(self.objective)
        validate_json_value(self.acceptance_criteria)
        _unique_text(self.constraints, "Task Contract constraint")
        refs = [item.ref for item in self.resource_refs]
        if len(refs) != len(set(refs)):
            raise ValueError("Task Contract resource refs must be unique")
        for item in self.resource_refs:
            _text(item.ref, "Task Contract resource ref")
            _digest(item.digest, "Task Contract resource digest")
        if self.consequence_policy_ref is not None:
            _text(self.consequence_policy_ref, "Task Contract consequence policy")

    @property
    def objective_digest(self) -> str:
        return canonical_digest(self.objective)

    @property
    def acceptance_criteria_digest(self) -> str:
        return canonical_digest(self.acceptance_criteria)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.task-contract",
            "contractId": self.contract_id,
            "taskId": self.task_id,
            "objective": self.objective,
            "acceptanceCriteria": self.acceptance_criteria,
            "constraints": list(self.constraints),
            "resourceRefs": [item.to_dict() for item in self.resource_refs],
            "consequencePolicyRef": self.consequence_policy_ref,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskContract:
        expected = {
            "schemaVersion",
            "kind",
            "contractId",
            "taskId",
            "objective",
            "acceptanceCriteria",
            "constraints",
            "resourceRefs",
            "consequencePolicyRef",
        }
        _exact(value, expected, "TaskContract")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.task-contract":
            raise ValueError("TaskContract version or kind is invalid")
        if not isinstance(value["contractId"], str) or not isinstance(value["taskId"], str):
            raise ValueError("TaskContract identities must be strings")
        objective = value["objective"]
        acceptance = value["acceptanceCriteria"]
        constraints = value["constraints"]
        refs = value["resourceRefs"]
        policy = value["consequencePolicyRef"]
        if not isinstance(objective, dict) or not isinstance(acceptance, dict):
            raise ValueError("TaskContract objective and acceptance must be objects")
        if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
            raise ValueError("TaskContract constraints must be strings")
        if not isinstance(refs, list) or any(not isinstance(item, dict) for item in refs):
            raise ValueError("TaskContract resource refs must be objects")
        if policy is not None and not isinstance(policy, str):
            raise ValueError("TaskContract consequence policy must be a string or null")
        validate_json_value(objective)
        validate_json_value(acceptance)
        return cls(
            contract_id=value["contractId"],
            task_id=value["taskId"],
            objective=dict(objective),
            acceptance_criteria=dict(acceptance),
            constraints=tuple(constraints),
            resource_refs=tuple(StateRef.from_dict(item) for item in refs),
            consequence_policy_ref=policy,
        )


@dataclass(frozen=True, slots=True)
class GrantedExecutionCheck:
    check_id: str
    executable: str
    args: tuple[str, ...] = ()
    cwd_relative: str = "."
    env: tuple[tuple[str, str], ...] = ()
    timeout_ms: int = 120_000
    stdout_limit_bytes: int = 262_144
    stderr_limit_bytes: int = 262_144

    def __post_init__(self) -> None:
        _identity(self.check_id, "check", "Execution Check")
        _text(self.executable, "Execution Check executable")
        if not self.executable.startswith("/"):
            raise ValueError("Execution Check executable must be absolute")
        if self.cwd_relative != ".":
            _relative_path(self.cwd_relative, "Execution Check working directory")
        for argument in self.args:
            if not isinstance(argument, str):
                raise ValueError("Execution Check arguments must be strings")
        keys = [key for key, _ in self.env]
        if len(keys) != len(set(keys)):
            raise ValueError("Execution Check environment keys must be unique")
        for key, value in self.env:
            _text(key, "Execution Check environment key", max_bytes=256)
            if not isinstance(value, str):
                raise ValueError("Execution Check environment values must be strings")
        if min(self.timeout_ms, self.stdout_limit_bytes, self.stderr_limit_bytes) < 1:
            raise ValueError("Execution Check bounds must be positive")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "checkId": self.check_id,
            "executable": self.executable,
            "args": list(self.args),
            "cwdRelative": self.cwd_relative,
            "env": {key: value for key, value in self.env},
            "timeoutMs": self.timeout_ms,
            "stdoutLimitBytes": self.stdout_limit_bytes,
            "stderrLimitBytes": self.stderr_limit_bytes,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GrantedExecutionCheck:
        expected = {
            "checkId",
            "executable",
            "args",
            "cwdRelative",
            "env",
            "timeoutMs",
            "stdoutLimitBytes",
            "stderrLimitBytes",
        }
        _exact(value, expected, "GrantedExecutionCheck")
        if not isinstance(value["checkId"], str) or not isinstance(value["executable"], str):
            raise ValueError("GrantedExecutionCheck identities must be strings")
        if not isinstance(value["cwdRelative"], str):
            raise ValueError("GrantedExecutionCheck working directory must be a string")
        args = value["args"]
        env = value["env"]
        if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
            raise ValueError("GrantedExecutionCheck args must be strings")
        if not isinstance(env, dict) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in env.items()
        ):
            raise ValueError("GrantedExecutionCheck env must contain string values")
        for field in ("timeoutMs", "stdoutLimitBytes", "stderrLimitBytes"):
            if type(value[field]) is not int:
                raise ValueError(f"GrantedExecutionCheck {field} must be an integer")
        return cls(
            check_id=value["checkId"],
            executable=value["executable"],
            args=tuple(args),
            cwd_relative=value["cwdRelative"],
            env=tuple(sorted(env.items())),
            timeout_ms=value["timeoutMs"],
            stdout_limit_bytes=value["stdoutLimitBytes"],
            stderr_limit_bytes=value["stderrLimitBytes"],
        )


@dataclass(frozen=True, slots=True)
class ToolGrant:
    tool_grant_id: str
    allowed_tools: tuple[str, ...]
    read_path_rules: tuple[str, ...] = ()
    mutate_path_rules: tuple[str, ...] = ()
    execution_checks: tuple[GrantedExecutionCheck, ...] = ()
    allow_opaque_exec: bool = False

    def __post_init__(self) -> None:
        _identity(self.tool_grant_id, "tool-grant", "Tool Grant")
        _unique_text(self.allowed_tools, "Tool Grant Tool")
        unknown = sorted(set(self.allowed_tools) - _NATIVE_TOOL_NAMES)
        if unknown:
            raise ValueError(f"Tool Grant contains unsupported Tools: {unknown}")
        for value in self.read_path_rules:
            _path_rule(value, "Tool Grant read path rule")
        for value in self.mutate_path_rules:
            _path_rule(value, "Tool Grant mutate path rule")
        if len(self.read_path_rules) != len(set(self.read_path_rules)):
            raise ValueError("Tool Grant read path rules must be unique")
        if len(self.mutate_path_rules) != len(set(self.mutate_path_rules)):
            raise ValueError("Tool Grant mutate path rules must be unique")
        checks = [item.check_id for item in self.execution_checks]
        if len(checks) != len(set(checks)):
            raise ValueError("Tool Grant Execution Check identities must be unique")
        if "read_workspace" in self.allowed_tools and not self.read_path_rules:
            raise ValueError("read_workspace requires at least one read path rule")
        if "mutate_workspace" in self.allowed_tools and not self.mutate_path_rules:
            raise ValueError("mutate_workspace requires at least one mutate path rule")
        if "run_check" in self.allowed_tools and not self.execution_checks:
            raise ValueError("run_check requires at least one Execution Check")
        if self.execution_checks and "run_check" not in self.allowed_tools:
            raise ValueError("Execution Checks require run_check permission")
        if "run_in_workspace" in self.allowed_tools and not self.allow_opaque_exec:
            raise ValueError("run_in_workspace requires explicit opaque-exec permission")
        if self.allow_opaque_exec and "run_in_workspace" not in self.allowed_tools:
            raise ValueError("opaque-exec permission requires run_in_workspace")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def allows_tool(self, name: str) -> bool:
        return name in self.allowed_tools

    def allows_path(self, name: str, relative_path: str) -> bool:
        normalized = _relative_path(relative_path, f"{name} relative path")
        rules = self.read_path_rules if name == "read_workspace" else self.mutate_path_rules
        for rule in rules:
            if rule == "**" or rule == normalized:
                return True
            if rule.endswith("/**"):
                prefix = rule[:-3]
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
        return False

    def execution_check(self, check_id: str) -> GrantedExecutionCheck:
        for value in self.execution_checks:
            if value.check_id == check_id:
                return value
        raise KeyError(f"Execution Check is not granted: {check_id}")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.tool-grant",
            "toolGrantId": self.tool_grant_id,
            "allowedTools": list(self.allowed_tools),
            "readPathRules": list(self.read_path_rules),
            "mutatePathRules": list(self.mutate_path_rules),
            "executionChecks": [item.to_dict() for item in self.execution_checks],
            "allowOpaqueExec": self.allow_opaque_exec,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ToolGrant:
        expected = {
            "schemaVersion",
            "kind",
            "toolGrantId",
            "allowedTools",
            "readPathRules",
            "mutatePathRules",
            "executionChecks",
            "allowOpaqueExec",
        }
        _exact(value, expected, "ToolGrant")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.tool-grant":
            raise ValueError("ToolGrant version or kind is invalid")
        if not isinstance(value["toolGrantId"], str):
            raise ValueError("ToolGrant identity must be a string")
        for field in ("allowedTools", "readPathRules", "mutatePathRules"):
            raw = value[field]
            if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
                raise ValueError(f"ToolGrant {field} must contain strings")
        checks = value["executionChecks"]
        if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
            raise ValueError("ToolGrant execution checks must be objects")
        if type(value["allowOpaqueExec"]) is not bool:
            raise ValueError("ToolGrant allowOpaqueExec must be a boolean")
        return cls(
            tool_grant_id=value["toolGrantId"],
            allowed_tools=tuple(value["allowedTools"]),
            read_path_rules=tuple(value["readPathRules"]),
            mutate_path_rules=tuple(value["mutatePathRules"]),
            execution_checks=tuple(GrantedExecutionCheck.from_dict(item) for item in checks),
            allow_opaque_exec=value["allowOpaqueExec"],
        )


@dataclass(frozen=True, slots=True)
class NativeHarnessRunContract:
    harness_run_id: str
    assignment_id: str
    assignment_generation: int
    assignment_digest: str
    harness_manifest_digest: str
    task_contract_digest: str
    task_contract_object_digest: str
    context_object_digest: str
    tool_catalog_digest: str
    tool_grant_digest: str
    tool_grant_object_digest: str
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.harness_run_id, "harness-run", "Harness Run")
        _identity(self.assignment_id, "assignment", "Assignment")
        if self.assignment_generation < 1:
            raise ValueError("Native Run Assignment generation must be positive")
        for value, label in (
            (self.assignment_digest, "Assignment digest"),
            (self.harness_manifest_digest, "Harness manifest digest"),
            (self.task_contract_digest, "Task Contract digest"),
            (self.task_contract_object_digest, "Task Contract object digest"),
            (self.context_object_digest, "Context object digest"),
            (self.tool_catalog_digest, "Tool catalog digest"),
            (self.tool_grant_digest, "Tool Grant digest"),
            (self.tool_grant_object_digest, "Tool Grant object digest"),
        ):
            _digest(value, label)
        if self.created_at_ms < 0:
            raise ValueError("Native Run creation time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.native-harness-run-contract",
            "harnessRunId": self.harness_run_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "assignmentDigest": self.assignment_digest,
            "harnessManifestDigest": self.harness_manifest_digest,
            "taskContractDigest": self.task_contract_digest,
            "taskContractObjectDigest": self.task_contract_object_digest,
            "contextObjectDigest": self.context_object_digest,
            "toolCatalogDigest": self.tool_catalog_digest,
            "toolGrantDigest": self.tool_grant_digest,
            "toolGrantObjectDigest": self.tool_grant_object_digest,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NativeHarnessRunContract:
        expected = {
            "schemaVersion",
            "kind",
            "harnessRunId",
            "assignmentId",
            "assignmentGeneration",
            "assignmentDigest",
            "harnessManifestDigest",
            "taskContractDigest",
            "taskContractObjectDigest",
            "contextObjectDigest",
            "toolCatalogDigest",
            "toolGrantDigest",
            "toolGrantObjectDigest",
            "createdAtMs",
        }
        _exact(value, expected, "NativeHarnessRunContract")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.native-harness-run-contract":
            raise ValueError("NativeHarnessRunContract version or kind is invalid")
        string_fields = expected - {"schemaVersion", "kind", "assignmentGeneration", "createdAtMs"}
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("NativeHarnessRunContract identity fields must be strings")
        if type(value["assignmentGeneration"]) is not int or type(value["createdAtMs"]) is not int:
            raise ValueError("NativeHarnessRunContract generation and time must be integers")
        return cls(
            harness_run_id=value["harnessRunId"],
            assignment_id=value["assignmentId"],
            assignment_generation=value["assignmentGeneration"],
            assignment_digest=value["assignmentDigest"],
            harness_manifest_digest=value["harnessManifestDigest"],
            task_contract_digest=value["taskContractDigest"],
            task_contract_object_digest=value["taskContractObjectDigest"],
            context_object_digest=value["contextObjectDigest"],
            tool_catalog_digest=value["toolCatalogDigest"],
            tool_grant_digest=value["toolGrantDigest"],
            tool_grant_object_digest=value["toolGrantObjectDigest"],
            created_at_ms=value["createdAtMs"],
        )


@dataclass(frozen=True, slots=True)
class CompletionVerification:
    verification_id: str
    completion_proposal_id: str
    method: str
    accepted: bool
    result: dict[str, JsonValue]
    evidence_refs: tuple[ArtifactRef, ...]
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.verification_id, "completion-verification", "Completion Verification")
        _identity(self.completion_proposal_id, "completion-proposal", "CompletionProposal")
        _text(self.method, "Completion Verification method")
        validate_json_value(self.result)
        _artifact_refs(self.evidence_refs, "Completion Verification evidence")
        if self.created_at_ms < 0:
            raise ValueError("Completion Verification time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.completion-verification",
            "verificationId": self.verification_id,
            "completionProposalId": self.completion_proposal_id,
            "method": self.method,
            "accepted": self.accepted,
            "result": self.result,
            "evidenceRefs": [item.to_dict() for item in self.evidence_refs],
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CompletionVerification:
        expected = {
            "schemaVersion",
            "kind",
            "verificationId",
            "completionProposalId",
            "method",
            "accepted",
            "result",
            "evidenceRefs",
            "createdAtMs",
        }
        _exact(value, expected, "CompletionVerification")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.completion-verification":
            raise ValueError("CompletionVerification version or kind is invalid")
        for field in ("verificationId", "completionProposalId", "method"):
            if not isinstance(value[field], str):
                raise ValueError(f"CompletionVerification {field} must be a string")
        if type(value["accepted"]) is not bool or type(value["createdAtMs"]) is not int:
            raise ValueError("CompletionVerification acceptance and time have invalid types")
        result = value["result"]
        refs = value["evidenceRefs"]
        if not isinstance(result, dict):
            raise ValueError("CompletionVerification result must be an object")
        if not isinstance(refs, list) or any(not isinstance(item, dict) for item in refs):
            raise ValueError("CompletionVerification evidence refs must be objects")
        validate_json_value(result)
        return cls(
            verification_id=value["verificationId"],
            completion_proposal_id=value["completionProposalId"],
            method=value["method"],
            accepted=value["accepted"],
            result=dict(result),
            evidence_refs=tuple(ArtifactRef.from_dict(item) for item in refs),
            created_at_ms=value["createdAtMs"],
        )
