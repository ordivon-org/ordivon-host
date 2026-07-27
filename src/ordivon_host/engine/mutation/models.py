from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ...domain import TaskState
from ...objects import StoredObject
from .._serde import validate_digest

_PLAN_KIND = "ordivon.host-guarded-mutation-plan"
_DISPATCH_KIND = "ordivon.runtime-dispatch-intent"
_OBSERVATION_KIND = "ordivon.runtime-job-observation"
_VERIFICATION_KIND = "ordivon.mutation-verification-receipt"
_ACTIVE_JOB_STATES = {"queued", "working"}
_FAILED_JOB_STATES = {"failed", "timed_out", "cancelled"}
_UNKNOWN_JOB_STATES = {"lost", "orphaned", "unknown"}
_ALLOWED_JOB_STATES = (
    _ACTIVE_JOB_STATES | _FAILED_JOB_STATES | _UNKNOWN_JOB_STATES | {"succeeded"}
)


class MutationTaskError(RuntimeError):
    pass


class MutationSuperseded(MutationTaskError):
    pass


class MutationVerificationError(MutationTaskError):
    pass


@dataclass(frozen=True, slots=True)
class GuardedMutationPlan:
    task_id: str
    goal_id: str
    workspace_id: str
    source_repo: str
    source_revision: str
    relative_path: str
    content: str
    timeout_ms: int = 30_000
    principal_id: str = "principal:local-owner"

    def __post_init__(self) -> None:
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("mutation Task identity must start with task:")
        if not self.goal_id.startswith("goal:") or self.goal_id != self.goal_id.strip():
            raise ValueError("mutation Goal identity must start with goal:")
        if not self.workspace_id or self.workspace_id != self.workspace_id.strip():
            raise ValueError("Runtime Workspace identity is required")
        if not Path(self.source_repo).is_absolute():
            raise ValueError("mutation source repository must be absolute")
        if (
            len(self.source_revision) != 40
            or any(character not in "0123456789abcdef" for character in self.source_revision)
        ):
            raise ValueError("mutation source revision must be a lowercase Git object id")
        relative = Path(self.relative_path)
        if (
            not self.relative_path
            or relative.is_absolute()
            or len(relative.parts) != 1
            or relative.parts[0] in {".", ".."}
            or self.relative_path != self.relative_path.strip()
        ):
            raise ValueError("v0 guarded mutation requires one root-level relative file")
        if len(self.content.encode("utf-8")) > 65_536:
            raise ValueError("mutation content exceeds the v0 byte bound")
        if self.timeout_ms < 1 or self.timeout_ms > 300_000:
            raise ValueError("mutation timeout is outside v0 bounds")
        if not self.principal_id.startswith("principal:"):
            raise ValueError("mutation principal identity must start with principal:")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _PLAN_KIND,
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "workspaceId": self.workspace_id,
            "sourceRepo": self.source_repo,
            "sourceRevision": self.source_revision,
            "relativePath": self.relative_path,
            "content": self.content,
            "timeoutMs": self.timeout_ms,
            "principalId": self.principal_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GuardedMutationPlan:
        expected = {
            "schemaVersion",
            "kind",
            "taskId",
            "goalId",
            "workspaceId",
            "sourceRepo",
            "sourceRevision",
            "relativePath",
            "content",
            "timeoutMs",
            "principalId",
        }
        if set(value) != expected:
            raise ValueError("GuardedMutationPlan fields differ")
        if value["schemaVersion"] != 1 or value["kind"] != _PLAN_KIND:
            raise ValueError("GuardedMutationPlan version or kind is invalid")
        string_fields = expected - {"schemaVersion", "timeoutMs"}
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("GuardedMutationPlan string fields are invalid")
        if type(value["timeoutMs"]) is not int:
            raise ValueError("GuardedMutationPlan timeout must be an integer")
        return cls(
            task_id=value["taskId"],
            goal_id=value["goalId"],
            workspace_id=value["workspaceId"],
            source_repo=value["sourceRepo"],
            source_revision=value["sourceRevision"],
            relative_path=value["relativePath"],
            content=value["content"],
            timeout_ms=value["timeoutMs"],
            principal_id=value["principalId"],
        )


@dataclass(frozen=True, slots=True)
class DispatchIntent:
    dispatch_id: str
    effect_id: str
    binding_id: str
    client_request_id: str
    workspace_id: str
    operation: str
    request_digest: str

    def __post_init__(self) -> None:
        for value, prefix in (
            (self.dispatch_id, "dispatch:"),
            (self.effect_id, "effect:"),
            (self.binding_id, "binding:"),
        ):
            if not value.startswith(prefix) or value != value.strip():
                raise ValueError(f"Dispatch identity must start with {prefix}")
        if not self.client_request_id or not self.workspace_id:
            raise ValueError("Dispatch correlation and Workspace are required")
        if self.operation != "workspace.exec":
            raise ValueError("guarded mutation Dispatch must target workspace.exec")
        validate_digest(self.request_digest)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _DISPATCH_KIND,
            "dispatchId": self.dispatch_id,
            "effectId": self.effect_id,
            "bindingId": self.binding_id,
            "clientRequestId": self.client_request_id,
            "workspaceId": self.workspace_id,
            "operation": self.operation,
            "requestDigest": self.request_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DispatchIntent:
        expected = {
            "schemaVersion",
            "kind",
            "dispatchId",
            "effectId",
            "bindingId",
            "clientRequestId",
            "workspaceId",
            "operation",
            "requestDigest",
        }
        if set(value) != expected:
            raise ValueError("DispatchIntent fields differ")
        if value["schemaVersion"] != 1 or value["kind"] != _DISPATCH_KIND:
            raise ValueError("DispatchIntent version or kind is invalid")
        fields = expected - {"schemaVersion"}
        if any(not isinstance(value[field], str) for field in fields):
            raise ValueError("DispatchIntent fields must be strings")
        return cls(
            dispatch_id=value["dispatchId"],
            effect_id=value["effectId"],
            binding_id=value["bindingId"],
            client_request_id=value["clientRequestId"],
            workspace_id=value["workspaceId"],
            operation=value["operation"],
            request_digest=value["requestDigest"],
        )


@dataclass(frozen=True, slots=True)
class RuntimeJobObservation:
    dispatch_id: str
    job_id: str
    attempt_id: str | None
    status: str
    payload_digest: str
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.dispatch_id.startswith("dispatch:") or not self.job_id:
            raise ValueError("Runtime observation identities are required")
        if self.attempt_id is not None and not self.attempt_id:
            raise ValueError("Runtime Attempt identity cannot be empty")
        if self.status not in _ALLOWED_JOB_STATES:
            raise ValueError(f"unsupported Runtime Job status: {self.status}")
        validate_json_value(self.payload)
        if canonical_digest(self.payload) != self.payload_digest:
            raise ValueError("Runtime observation payload digest differs")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _OBSERVATION_KIND,
            "dispatchId": self.dispatch_id,
            "jobId": self.job_id,
            "attemptId": self.attempt_id,
            "status": self.status,
            "payloadDigest": self.payload_digest,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class MutationVerificationReceipt:
    dispatch_id: str
    method: str
    relative_path: str
    expected_digest: str
    runtime_digest: str
    observed_digest: str
    accepted: bool

    def __post_init__(self) -> None:
        if not self.dispatch_id.startswith("dispatch:") or not self.method.endswith(".v1"):
            raise ValueError("verification identity or method is invalid")
        for digest in (self.expected_digest, self.runtime_digest, self.observed_digest):
            validate_digest(digest)
        expected = self.expected_digest == self.runtime_digest == self.observed_digest
        if self.accepted != expected:
            raise ValueError("verification decision differs from compared digests")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _VERIFICATION_KIND,
            "dispatchId": self.dispatch_id,
            "method": self.method,
            "relativePath": self.relative_path,
            "expectedDigest": self.expected_digest,
            "runtimeDigest": self.runtime_digest,
            "observedDigest": self.observed_digest,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class PreparedMutation:
    task_id: str
    task_revision: int
    plan: GuardedMutationPlan
    effect_object: StoredObject
    binding_object: StoredObject
    dispatch_object: StoredObject
    dispatch: DispatchIntent
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class MutationStep:
    task_id: str
    revision: int
    state: TaskState
    frontier: str | None
    dispatch_id: str | None = None
    job_id: str | None = None
    reconciled: bool = False
    completed: bool = False


