from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ...domain import RepositoryRef, TaskState
from ...objects import StoredObject
from ...objects.codecs import decode_versioned_object
from .._serde import digest_text, validate_digest

_PLAN_KIND = "ordivon.host-code-change-plan"
_DISPATCH_KIND = "ordivon.runtime-code-change-dispatch"
_VERIFICATION_KIND = "ordivon.code-change-verification-receipt"
_STEP_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SECRET_ENV_KEY = re.compile(r"(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)", re.IGNORECASE)


class CodeChangeError(RuntimeError):
    pass


class CodeChangeSuperseded(CodeChangeError):
    pass


class CodeChangeVerificationError(CodeChangeError):
    pass


@dataclass(frozen=True, slots=True)
class CodeFileReplacement:
    relative_path: str
    expected_digest: str
    content: str

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or self.relative_path != self.relative_path.strip()
        ):
            raise ValueError("code file path must be a safe relative path")
        validate_digest(self.expected_digest)
        if len(self.content.encode("utf-8")) > 524_288:
            raise ValueError("code file replacement exceeds 512 KiB")

    @property
    def result_digest(self) -> str:
        return digest_text(self.content)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "relativePath": self.relative_path,
            "expectedDigest": self.expected_digest,
            "content": self.content,
            "resultDigest": self.result_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CodeFileReplacement:
        expected = {"relativePath", "expectedDigest", "content", "resultDigest"}
        if set(value) != expected or any(
            not isinstance(value[field], str) for field in expected
        ):
            raise ValueError("CodeFileReplacement fields differ")
        result = cls(
            relative_path=value["relativePath"],
            expected_digest=value["expectedDigest"],
            content=value["content"],
        )
        if result.result_digest != value["resultDigest"]:
            raise ValueError("CodeFileReplacement result digest differs")
        return result


@dataclass(frozen=True, slots=True)
class ExecutionCheck:
    check_id: str
    executable: str
    args: tuple[str, ...]
    cwd_relative: str = "."
    env: tuple[tuple[str, str], ...] = ()
    timeout_ms: int = 120_000

    def __post_init__(self) -> None:
        if not _STEP_ID.fullmatch(self.check_id):
            raise ValueError("execution check identity is invalid")
        if not Path(self.executable).is_absolute():
            raise ValueError("execution check executable must be absolute")
        cwd = Path(self.cwd_relative)
        if cwd.is_absolute() or ".." in cwd.parts:
            raise ValueError("execution check cwd must stay inside the Workspace")
        if len(self.args) > 128 or any(not isinstance(value, str) for value in self.args):
            raise ValueError("execution check arguments are invalid")
        if len(dict(self.env)) != len(self.env):
            raise ValueError("execution check environment keys must be unique")
        for key, value in self.env:
            if not key or not isinstance(value, str):
                raise ValueError("execution check environment is invalid")
            if _SECRET_ENV_KEY.search(key):
                raise ValueError(
                    "execution check secrets must use a Runtime SecretRef, not durable env"
                )
        if self.timeout_ms < 1 or self.timeout_ms > 900_000:
            raise ValueError("execution check timeout is outside bounds")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.check_id,
            "executable": self.executable,
            "args": list(self.args),
            "cwdRelative": self.cwd_relative,
            "env": dict(self.env),
            "timeoutMs": self.timeout_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionCheck:
        expected = {"id", "executable", "args", "cwdRelative", "env", "timeoutMs"}
        if set(value) != expected:
            raise ValueError("ExecutionCheck fields differ")
        args = value["args"]
        env = value["env"]
        if (
            not isinstance(value["id"], str)
            or not isinstance(value["executable"], str)
            or not isinstance(value["cwdRelative"], str)
            or type(value["timeoutMs"]) is not int
            or not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
            or not isinstance(env, dict)
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in env.items())
        ):
            raise ValueError("ExecutionCheck field types are invalid")
        return cls(
            check_id=value["id"],
            executable=value["executable"],
            args=tuple(args),
            cwd_relative=value["cwdRelative"],
            env=tuple(sorted(env.items())),
            timeout_ms=value["timeoutMs"],
        )


@dataclass(frozen=True, slots=True)
class CodeChangePlan:
    task_id: str
    goal_id: str
    workspace_id: str
    repository: RepositoryRef
    files: tuple[CodeFileReplacement, ...]
    checks: tuple[ExecutionCheck, ...]
    patch_executable: str = "/root/.local/bin/python3.12"
    principal_id: str = "principal:local-owner"

    def __post_init__(self) -> None:
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("code change Task identity must start with task:")
        if not self.goal_id.startswith("goal:") or self.goal_id != self.goal_id.strip():
            raise ValueError("code change Goal identity must start with goal:")
        if not self.workspace_id or self.workspace_id != self.workspace_id.strip():
            raise ValueError("code change Workspace identity is required")
        if not 1 <= len(self.files) <= 8:
            raise ValueError("code change requires 1 to 8 files")
        if len({item.relative_path for item in self.files}) != len(self.files):
            raise ValueError("code change file paths must be unique")
        if not 1 <= len(self.checks) <= 8:
            raise ValueError("code change requires 1 to 8 checks")
        if len({item.check_id for item in self.checks}) != len(self.checks):
            raise ValueError("code change check identities must be unique")
        if not Path(self.patch_executable).is_absolute():
            raise ValueError("patch executable must be absolute")
        if not self.principal_id.startswith("principal:"):
            raise ValueError("code change principal identity must start with principal:")

    @property
    def source_revision(self) -> str:
        return self.repository.revision

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 2,
            "kind": _PLAN_KIND,
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "workspaceId": self.workspace_id,
            "repository": self.repository.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "checks": [item.to_dict() for item in self.checks],
            "patchExecutable": self.patch_executable,
            "principalId": self.principal_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CodeChangePlan:
        return decode_versioned_object(
            value,
            expected_kind=_PLAN_KIND,
            decoders={1: cls._from_dict_v1, 2: cls._from_dict_v2},
            label="CodeChangePlan",
        )

    @classmethod
    def _from_dict_v1(cls, value: dict[str, Any]) -> CodeChangePlan:
        expected = {
            "schemaVersion",
            "kind",
            "taskId",
            "goalId",
            "workspaceId",
            "sourceRepo",
            "sourceRevision",
            "files",
            "checks",
            "patchExecutable",
            "principalId",
        }
        if set(value) != expected:
            raise ValueError("CodeChangePlan v1 fields differ")
        string_fields = expected - {"schemaVersion", "files", "checks"}
        if value["schemaVersion"] != 1 or any(
            not isinstance(value[field], str) for field in string_fields
        ):
            raise ValueError("CodeChangePlan v1 fields are invalid")
        files = value["files"]
        checks = value["checks"]
        if (
            not isinstance(files, list)
            or any(not isinstance(item, dict) for item in files)
            or not isinstance(checks, list)
            or any(not isinstance(item, dict) for item in checks)
        ):
            raise ValueError("CodeChangePlan v1 files or checks are invalid")
        source_repo = value["sourceRepo"]
        if not Path(source_repo).is_absolute():
            raise ValueError("CodeChangePlan v1 source repository must be absolute")
        token = hashlib.sha256(source_repo.encode("utf-8")).hexdigest()[:24]
        return cls(
            task_id=value["taskId"],
            goal_id=value["goalId"],
            workspace_id=value["workspaceId"],
            repository=RepositoryRef(
                repository_id=f"repository:legacy-{token}",
                revision=value["sourceRevision"],
                legacy_path=source_repo,
            ),
            files=tuple(CodeFileReplacement.from_dict(item) for item in files),
            checks=tuple(ExecutionCheck.from_dict(item) for item in checks),
            patch_executable=value["patchExecutable"],
            principal_id=value["principalId"],
        )

    @classmethod
    def _from_dict_v2(cls, value: dict[str, Any]) -> CodeChangePlan:
        expected = {
            "schemaVersion",
            "kind",
            "taskId",
            "goalId",
            "workspaceId",
            "repository",
            "files",
            "checks",
            "patchExecutable",
            "principalId",
        }
        if set(value) != expected:
            raise ValueError("CodeChangePlan v2 fields differ")
        string_fields = expected - {"schemaVersion", "repository", "files", "checks"}
        repository = value["repository"]
        files = value["files"]
        checks = value["checks"]
        if (
            value["schemaVersion"] != 2
            or any(not isinstance(value[field], str) for field in string_fields)
            or not isinstance(repository, dict)
            or not isinstance(files, list)
            or any(not isinstance(item, dict) for item in files)
            or not isinstance(checks, list)
            or any(not isinstance(item, dict) for item in checks)
        ):
            raise ValueError("CodeChangePlan v2 field types are invalid")
        return cls(
            task_id=value["taskId"],
            goal_id=value["goalId"],
            workspace_id=value["workspaceId"],
            repository=RepositoryRef.from_dict(repository),
            files=tuple(CodeFileReplacement.from_dict(item) for item in files),
            checks=tuple(ExecutionCheck.from_dict(item) for item in checks),
            patch_executable=value["patchExecutable"],
            principal_id=value["principalId"],
        )


@dataclass(frozen=True, slots=True)
class CodeChangeDispatch:
    dispatch_id: str
    effect_id: str
    binding_id: str
    authority_decision_digest: str
    client_request_id: str
    workspace_id: str
    operation: str
    request_digest: str

    def __post_init__(self) -> None:
        if not self.dispatch_id.startswith("dispatch:"):
            raise ValueError("code change Dispatch identity is invalid")
        if not self.effect_id.startswith("effect:"):
            raise ValueError("code change Effect identity is invalid")
        if not self.binding_id.startswith("binding:"):
            raise ValueError("code change Binding identity is invalid")
        validate_digest(self.authority_decision_digest)
        if not self.client_request_id or not self.workspace_id:
            raise ValueError("code change Dispatch correlation is required")
        if self.operation != "workspace.execPlan":
            raise ValueError("code change Dispatch must target workspace.execPlan")
        validate_digest(self.request_digest)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 2,
            "kind": _DISPATCH_KIND,
            "dispatchId": self.dispatch_id,
            "effectId": self.effect_id,
            "bindingId": self.binding_id,
            "authorityDecisionDigest": self.authority_decision_digest,
            "clientRequestId": self.client_request_id,
            "workspaceId": self.workspace_id,
            "operation": self.operation,
            "requestDigest": self.request_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CodeChangeDispatch:
        expected = {
            "schemaVersion",
            "kind",
            "dispatchId",
            "effectId",
            "bindingId",
            "authorityDecisionDigest",
            "clientRequestId",
            "workspaceId",
            "operation",
            "requestDigest",
        }
        if set(value) != expected:
            raise ValueError("CodeChangeDispatch fields differ")
        if value["schemaVersion"] != 2 or value["kind"] != _DISPATCH_KIND:
            raise ValueError("CodeChangeDispatch version or kind is invalid")
        fields = expected - {"schemaVersion"}
        if any(not isinstance(value[field], str) for field in fields):
            raise ValueError("CodeChangeDispatch fields must be strings")
        return cls(
            dispatch_id=value["dispatchId"],
            effect_id=value["effectId"],
            binding_id=value["bindingId"],
            authority_decision_digest=value["authorityDecisionDigest"],
            client_request_id=value["clientRequestId"],
            workspace_id=value["workspaceId"],
            operation=value["operation"],
            request_digest=value["requestDigest"],
        )


@dataclass(frozen=True, slots=True)
class CodeChangeVerificationReceipt:
    dispatch_id: str
    job_id: str
    completed_steps: int
    total_steps: int
    file_results: tuple[dict[str, JsonValue], ...]
    changed_paths: tuple[str, ...]
    diff_digest: str
    diff_accepted: bool
    accepted: bool

    def __post_init__(self) -> None:
        if not self.dispatch_id.startswith("dispatch:") or not self.job_id:
            raise ValueError("code change verification identities are invalid")
        if self.completed_steps < 1 or self.total_steps < self.completed_steps:
            raise ValueError("code change step counts are invalid")
        validate_digest(self.diff_digest)
        for result in self.file_results:
            validate_json_value(result)
        if not self.changed_paths or len(set(self.changed_paths)) != len(self.changed_paths):
            raise ValueError("code change changed paths must be non-empty and unique")
        expected = (
            all(result.get("accepted") is True for result in self.file_results)
            and self.diff_accepted
        )
        if self.accepted != expected:
            raise ValueError("code change verification decision differs")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _VERIFICATION_KIND,
            "dispatchId": self.dispatch_id,
            "jobId": self.job_id,
            "completedSteps": self.completed_steps,
            "totalSteps": self.total_steps,
            "fileResults": list(self.file_results),
            "changedPaths": list(self.changed_paths),
            "diffDigest": self.diff_digest,
            "diffAccepted": self.diff_accepted,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class PreparedCodeChange:
    task_id: str
    task_revision: int
    plan: CodeChangePlan
    effect_object: StoredObject
    binding_object: StoredObject
    authority_object: StoredObject
    dispatch_object: StoredObject
    request_object: StoredObject
    dispatch: CodeChangeDispatch
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CodeChangeStep:
    task_id: str
    revision: int
    state: TaskState
    frontier: str | None
    dispatch_id: str | None = None
    job_id: str | None = None
    reconciled: bool = False
    completed: bool = False


def request_digest(value: dict[str, JsonValue]) -> str:
    return canonical_digest(value)
