from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from .host import CommittedHarnessAssignment

_NAMESPACE = "ordivon.host"
_REFERENCE_TYPES = {
    "assignment",
    "dispatch",
    "effect",
    "harness_run",
    "native_run_contract",
    "task",
    "task_attempt",
    "task_contract",
    "tool_grant",
}


def _text(value: str, label: str, *, max_bytes: int = 300) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


@dataclass(frozen=True, slots=True)
class HostRuntimeReference:
    reference_type: str
    reference_id: str
    generation: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if self.reference_type not in _REFERENCE_TYPES:
            raise ValueError(
                f"unsupported Host Runtime reference type: {self.reference_type}"
            )
        _text(self.reference_id, "Host Runtime reference identity")
        if self.generation is not None:
            _text(self.generation, "Host Runtime reference generation", max_bytes=120)
        if self.digest is not None:
            _digest(self.digest, "Host Runtime reference digest")

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (_NAMESPACE, self.reference_type, self.reference_id)

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "namespace": _NAMESPACE,
            "type": self.reference_type,
            "id": self.reference_id,
        }
        if self.generation is not None:
            value["generation"] = self.generation
        if self.digest is not None:
            value["digest"] = self.digest
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HostRuntimeReference:
        allowed = {"namespace", "type", "id", "generation", "digest"}
        if not {"namespace", "type", "id"}.issubset(value) or set(value) - allowed:
            raise ValueError("HostRuntimeReference fields differ")
        if value["namespace"] != _NAMESPACE:
            raise ValueError("HostRuntimeReference namespace differs")
        if not isinstance(value["type"], str) or not isinstance(value["id"], str):
            raise ValueError("HostRuntimeReference identity fields must be strings")
        generation = value.get("generation")
        digest = value.get("digest")
        if generation is not None and not isinstance(generation, str):
            raise ValueError("HostRuntimeReference generation must be a string")
        if digest is not None and not isinstance(digest, str):
            raise ValueError("HostRuntimeReference digest must be a string")
        return cls(
            reference_type=value["type"],
            reference_id=value["id"],
            generation=generation,
            digest=digest,
        )


def task_runtime_binding_digest(
    committed: CommittedHarnessAssignment,
) -> str:
    assignment = committed.assignment
    attempt = committed.attempt
    return canonical_digest(
        {
            "schemaVersion": 1,
            "kind": "ordivon.host-task-runtime-binding",
            "taskId": assignment.task_id,
            "taskRevision": assignment.task_revision,
            "taskAttemptId": attempt.task_attempt_id,
            "objectiveDigest": attempt.objective_digest,
            "acceptanceCriteriaDigest": attempt.acceptance_criteria_digest,
        }
    )


def harness_run_runtime_binding_digest(
    committed: CommittedHarnessAssignment,
    harness_run_id: str,
) -> str:
    _text(harness_run_id, "Harness Run identity")
    if not harness_run_id.startswith("harness-run:"):
        raise ValueError("Harness Run identity must start with harness-run:")
    assignment = committed.assignment
    native = committed.native_run_contract
    if native is not None and native.harness_run_id != harness_run_id:
        raise ValueError("Harness Run identity differs from native Run Contract")
    value: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "kind": "ordivon.harness-run-runtime-binding",
        "harnessRunId": harness_run_id,
        "assignmentId": assignment.assignment_id,
        "assignmentGeneration": assignment.generation,
        "harnessId": assignment.target_harness_id,
        "harnessManifestDigest": assignment.harness_manifest_digest,
        "contextDigest": assignment.context_object_digest,
        "toolCatalogDigest": assignment.tool_catalog_digest,
    }
    if native is not None:
        value.update(
            {
                "nativeRunContractDigest": native.digest,
                "taskContractDigest": native.task_contract_digest,
                "toolGrantDigest": native.tool_grant_digest,
            }
        )
    return canonical_digest(value)


def host_runtime_references(
    committed: CommittedHarnessAssignment,
    harness_run_id: str,
) -> tuple[HostRuntimeReference, ...]:
    assignment = committed.assignment
    references: tuple[HostRuntimeReference, ...] = (
        HostRuntimeReference(
            "task",
            assignment.task_id,
            generation=str(assignment.task_revision),
            digest=task_runtime_binding_digest(committed),
        ),
        HostRuntimeReference(
            "task_attempt",
            committed.attempt.task_attempt_id,
            digest=committed.attempt.digest,
        ),
        HostRuntimeReference(
            "assignment",
            assignment.assignment_id,
            generation=str(assignment.generation),
            digest=assignment.digest,
        ),
        HostRuntimeReference(
            "harness_run",
            harness_run_id,
            digest=harness_run_runtime_binding_digest(committed, harness_run_id),
        ),
    )
    if committed.native_run_contract is not None:
        native = committed.native_run_contract
        assert committed.task_contract is not None
        assert committed.tool_grant is not None
        references += (
            HostRuntimeReference(
                "task_contract",
                committed.task_contract.contract_id,
                digest=committed.task_contract.digest,
            ),
            HostRuntimeReference(
                "tool_grant",
                committed.tool_grant.tool_grant_id,
                digest=committed.tool_grant.digest,
            ),
            HostRuntimeReference(
                "native_run_contract",
                native.harness_run_id,
                generation=str(native.assignment_generation),
                digest=native.digest,
            ),
        )
    return tuple(sorted(references, key=lambda value: value.sort_key))


def harness_runtime_client_request_id(
    committed: CommittedHarnessAssignment,
    harness_run_id: str,
    step_id: str,
) -> str:
    _text(step_id, "Harness Runtime step identity", max_bytes=200)
    digest = canonical_digest(
        {
            "schemaVersion": 1,
            "kind": "ordivon.harness-runtime-request-identity",
            "assignmentId": committed.assignment.assignment_id,
            "assignmentGeneration": committed.assignment.generation,
            "assignmentDigest": committed.assignment.digest,
            "harnessRunId": harness_run_id,
            "harnessRunBindingDigest": harness_run_runtime_binding_digest(
                committed, harness_run_id
            ),
            "stepId": step_id,
        }
    )
    return f"request:harness:g{committed.assignment.generation}:{digest[7:39]}"


def build_harness_workspace_exec_request(
    committed: CommittedHarnessAssignment,
    *,
    harness_run_id: str,
    step_id: str,
    executable: str,
    args: tuple[str, ...] = (),
    cwd_relative: str = ".",
    env: dict[str, str] | None = None,
    timeout_ms: int = 30_000,
    stdout_limit_bytes: int = 262_144,
    stderr_limit_bytes: int = 262_144,
    wait_ms: int = 0,
    stdout_tail_bytes: int = 8_192,
    stderr_tail_bytes: int = 8_192,
) -> dict[str, JsonValue]:
    assignment = committed.assignment
    if assignment.workspace_ref is None:
        raise ValueError("Harness Assignment has no Runtime Workspace reference")
    _text(executable, "Runtime executable")
    if not executable.startswith("/"):
        raise ValueError("Runtime executable must be absolute")
    _text(cwd_relative, "Runtime working directory")
    for argument in args:
        if not isinstance(argument, str):
            raise ValueError("Runtime arguments must be strings")
    environment = {} if env is None else dict(env)
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not key
        or key != key.strip()
        for key, value in environment.items()
    ):
        raise ValueError("Runtime environment must contain trimmed string keys and values")
    if timeout_ms < 0:
        raise ValueError("Runtime timeout must be non-negative")
    if stdout_limit_bytes < 0 or stderr_limit_bytes < 0:
        raise ValueError("Runtime output limits must be non-negative")
    if wait_ms < 0 or wait_ms > 30_000:
        raise ValueError("Runtime wait must be between 0 and 30000 milliseconds")
    if not 0 <= stdout_tail_bytes <= 65_536 or not 0 <= stderr_tail_bytes <= 65_536:
        raise ValueError("Runtime tail limits must be between 0 and 65536 bytes")
    references = [
        reference.to_dict()
        for reference in host_runtime_references(committed, harness_run_id)
    ]
    request: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "clientRequestId": harness_runtime_client_request_id(
            committed, harness_run_id, step_id
        ),
        "execution": {
            "workspaceId": assignment.workspace_ref,
            "executable": executable,
            "args": list(args),
            "cwdRelative": cwd_relative,
            "env": environment,
            "timeoutMs": timeout_ms,
            "stdoutLimitBytes": stdout_limit_bytes,
            "stderrLimitBytes": stderr_limit_bytes,
            "foreignReferences": references,
        },
        "waitMs": wait_ms,
        "stdoutTailBytes": stdout_tail_bytes,
        "stderrTailBytes": stderr_tail_bytes,
    }
    validate_json_value(request)
    return request
