from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Protocol

from anc_canonical import JsonValue, validate_json_value
from anc_effect_binding import lower_to_ordivon
from anc_effect_ir import (
    CanonicalInput,
    CapabilityRequirement,
    CompletionKind,
    DeliverySemantics,
    EffectEnvelope,
    EffectMode,
    EvidenceKind,
    ExecutionKind,
    IdempotencyKind,
    ResultSemantics,
    SemanticAction,
    TargetRef,
    VerificationPlan,
)

from ..domain import EventKind, TaskProjection, TaskState
from ..journal import JournalCorruption
from ..objects import ObjectCorrupt
from ..runtime import (
    RuntimeProtocolError,
    RuntimeToolRejected,
    discover_runtime_catalog,
)
from ..storage import HostStorage, TaskEventSnapshot

_MAX_READ_BYTES = 4_194_304
_PLAN_KIND = "ordivon.host-read-task-plan"
_OBSERVATION_KIND = "ordivon.read-observation"
_VERIFICATION_KIND = "ordivon.verification-receipt"
_OUTCOME_KIND = "ordivon.task-outcome"


class RuntimeClient(Protocol):
    def initialize(self) -> dict[str, Any]: ...

    def list_tools(self) -> tuple[dict[str, Any], ...]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ReadVerificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReadTaskPlan:
    task_id: str
    goal_id: str
    workspace_id: str
    source_repo: str
    source_revision: str
    relative_path: str
    max_bytes: int = _MAX_READ_BYTES
    principal_id: str = "principal:local-owner"

    def __post_init__(self) -> None:
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("read Task identity must start with task:")
        if not self.goal_id.startswith("goal:") or self.goal_id != self.goal_id.strip():
            raise ValueError("read Goal identity must start with goal:")
        if not self.workspace_id or self.workspace_id != self.workspace_id.strip():
            raise ValueError("Runtime Workspace identity is required")
        if not Path(self.source_repo).is_absolute():
            raise ValueError("read source repository must be an absolute path")
        if (
            len(self.source_revision) != 40
            or any(character not in "0123456789abcdef" for character in self.source_revision)
        ):
            raise ValueError("read source revision must be a lowercase Git object id")
        relative = Path(self.relative_path)
        if (
            not self.relative_path
            or relative.is_absolute()
            or ".." in relative.parts
            or self.relative_path != self.relative_path.strip()
        ):
            raise ValueError("read path must be a bounded relative path")
        if self.max_bytes < 1 or self.max_bytes > _MAX_READ_BYTES:
            raise ValueError("read byte limit is outside Runtime bounds")
        if not self.principal_id.startswith("principal:"):
            raise ValueError("read principal identity must start with principal:")

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
            "maxBytes": self.max_bytes,
            "principalId": self.principal_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReadTaskPlan:
        expected = {
            "schemaVersion",
            "kind",
            "taskId",
            "goalId",
            "workspaceId",
            "sourceRepo",
            "sourceRevision",
            "relativePath",
            "maxBytes",
            "principalId",
        }
        if set(value) != expected:
            raise ValueError("ReadTaskPlan fields differ")
        if value["schemaVersion"] != 1 or value["kind"] != _PLAN_KIND:
            raise ValueError("ReadTaskPlan version or kind is invalid")
        string_fields = (
            "taskId",
            "goalId",
            "workspaceId",
            "sourceRepo",
            "sourceRevision",
            "relativePath",
            "principalId",
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("ReadTaskPlan identities and paths must be strings")
        if type(value["maxBytes"]) is not int:
            raise ValueError("ReadTaskPlan maxBytes must be an integer")
        return cls(
            task_id=value["taskId"],
            goal_id=value["goalId"],
            workspace_id=value["workspaceId"],
            source_repo=value["sourceRepo"],
            source_revision=value["sourceRevision"],
            relative_path=value["relativePath"],
            max_bytes=value["maxBytes"],
            principal_id=value["principalId"],
        )


@dataclass(frozen=True, slots=True)
class ReadObservation:
    workspace_id: str
    relative_path: str
    content: str
    digest: str

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.relative_path:
            raise ValueError("read Observation target is required")
        if _digest_text(self.content) != self.digest:
            raise ReadVerificationError("Runtime read digest differs from returned content")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _OBSERVATION_KIND,
            "workspaceId": self.workspace_id,
            "relativePath": self.relative_path,
            "content": self.content,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    method: str
    observation_digest: str
    expected_digest: str
    observed_digest: str
    accepted: bool

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("verification method is required")
        if self.accepted != (self.expected_digest == self.observed_digest):
            raise ValueError("verification decision differs from compared digests")
        for digest in (
            self.observation_digest,
            self.expected_digest,
            self.observed_digest,
        ):
            _validate_digest(digest)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": _VERIFICATION_KIND,
            "method": self.method,
            "observationDigest": self.observation_digest,
            "expectedDigest": self.expected_digest,
            "observedDigest": self.observed_digest,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class ReadTaskStep:
    task_id: str
    revision: int
    frontier: str | None
    completed: bool


class DeterministicReadHost:
    def __init__(
        self,
        storage: HostStorage,
        runtime: RuntimeClient,
        *,
        clock_ms: Callable[[], int],
        owner_id: str = "host:read-v0",
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if not owner_id or lease_ttl_ms < 1:
            raise ValueError("Host owner and lease TTL are required")
        self.storage = storage
        self.runtime = runtime
        self.clock_ms = clock_ms
        self.owner_id = owner_id
        self.lease_ttl_ms = lease_ttl_ms

    def create(self, plan: ReadTaskPlan) -> TaskProjection:
        plan_object = self.storage.put_object(plan.to_dict(), kind="host-read-task-plan")
        existing = self.storage.journal.get_task(plan.task_id)
        if existing is not None:
            current_plan = self._load_plan(self.storage.read_task_event(plan.task_id))
            if current_plan != plan:
                raise ValueError("Task identity is already bound to a different read plan")
            return existing
        projection = TaskProjection(
            task_id=plan.task_id,
            goal_id=plan.goal_id,
            state=TaskState.READY,
            active_node_id=None,
            ready_frontier=(self._node(plan, "open"),),
            revision=1,
            updated_at_ms=self._timestamp(None),
        )
        self.storage.record_task_event(
            event_id=self._event_id(plan, 1),
            kind=EventKind.TASK_CREATED,
            payload={"planDigest": plan_object.digest},
            projection=projection,
            expected_revision=0,
            referenced_objects=(plan_object,),
        )
        return projection

    def step(self, task_id: str) -> ReadTaskStep:
        current = self.storage.journal.get_task(task_id)
        if current is None:
            raise KeyError(f"unknown read Task: {task_id}")
        if current.state.terminal:
            return ReadTaskStep(task_id, current.revision, None, True)
        lease = self.storage.journal.acquire_lease(
            task_id,
            owner_id=self.owner_id,
            now_ms=self.clock_ms(),
            ttl_ms=self.lease_ttl_ms,
        )
        try:
            snapshot = self.storage.read_task_event(task_id)
            plan = self._load_plan(snapshot)
            if snapshot.projection != current:
                raise JournalCorruption("Task projection changed before Host step")
            if len(current.ready_frontier) != 1:
                raise JournalCorruption("read Task requires exactly one ready node")
            frontier = current.ready_frontier[0]
            if frontier == self._node(plan, "open"):
                projection = self._step_open(snapshot, plan)
            elif frontier == self._node(plan, "read"):
                projection = self._step_read(snapshot, plan)
            elif frontier == self._node(plan, "close"):
                projection = self._step_close(snapshot, plan)
            else:
                raise JournalCorruption(f"unknown read Task frontier: {frontier}")
            return ReadTaskStep(
                task_id=task_id,
                revision=projection.revision,
                frontier=(projection.ready_frontier[0] if projection.ready_frontier else None),
                completed=projection.state is TaskState.COMPLETED,
            )
        finally:
            self.storage.journal.release_lease(lease)

    def run(self, task_id: str, *, max_steps: int = 4) -> TaskProjection:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        for _ in range(max_steps):
            result = self.step(task_id)
            if result.completed:
                projection = self.storage.journal.get_task(task_id)
                if projection is None:
                    raise JournalCorruption("completed Task projection disappeared")
                return projection
        raise RuntimeError("read Task did not complete within the step bound")

    def _step_open(
        self,
        snapshot: TaskEventSnapshot,
        plan: ReadTaskPlan,
    ) -> TaskProjection:
        self.runtime.initialize()
        catalog = discover_runtime_catalog(self.runtime)
        catalog_object = self.storage.put_object(
            catalog.to_dict(), kind="runtime-catalog"
        )
        workspace = self._ensure_workspace(plan)
        projection = self._next_projection(snapshot.projection, plan, "read")
        plan_object = self.storage.objects.inspect(self._plan_digest(snapshot))
        self.storage.record_task_event(
            event_id=self._event_id(plan, projection.revision),
            kind=EventKind.RUNTIME_LINKED,
            payload={
                "planDigest": plan_object.digest,
                "catalogDigest": catalog.digest,
                "catalogObjectDigest": catalog_object.digest,
                "workspace": workspace,
            },
            projection=projection,
            expected_revision=snapshot.projection.revision,
            referenced_objects=(plan_object, catalog_object),
        )
        return projection

    def _step_read(
        self,
        snapshot: TaskEventSnapshot,
        plan: ReadTaskPlan,
    ) -> TaskProjection:
        data = _object(snapshot.data, "read Task open data")
        expected_catalog_digest = _string(data, "catalogDigest")
        self.runtime.initialize()
        catalog = discover_runtime_catalog(self.runtime)
        if catalog.digest != expected_catalog_digest:
            raise RuntimeProtocolError(
                "Runtime Tool catalog changed before the bound read"
            )
        effect = _read_effect(plan)
        binding = lower_to_ordivon(
            effect,
            catalog.read_contract,
            binding_id=f"binding:{_task_token(plan.task_id)}:read:r1",
            workspace_id=plan.workspace_id,
        )
        if not isinstance(binding.arguments, dict):
            raise RuntimeProtocolError("workspace.read Binding arguments are not an object")
        payload = self.runtime.call_tool("workspace.read", binding.arguments)
        content = payload.get("content")
        digest = payload.get("digest")
        if not isinstance(content, str) or not isinstance(digest, str):
            raise RuntimeProtocolError("workspace.read omitted content or digest")
        observation = ReadObservation(
            workspace_id=plan.workspace_id,
            relative_path=plan.relative_path,
            content=content,
            digest=digest,
        )
        effect_object = self.storage.put_object(effect.to_dict(), kind="effect")
        binding_object = self.storage.put_object(binding.to_dict(), kind="effect-binding")
        observation_object = self.storage.put_object(
            observation.to_dict(), kind="read-observation"
        )
        verification = VerificationReceipt(
            method="independent-content-sha256.v1",
            observation_digest=observation_object.digest,
            expected_digest=_digest_text(content),
            observed_digest=digest,
            accepted=True,
        )
        verification_object = self.storage.put_object(
            verification.to_dict(), kind="verification-receipt"
        )
        projection = self._next_projection(snapshot.projection, plan, "close")
        plan_object = self.storage.objects.inspect(self._plan_digest(snapshot))
        self.storage.record_task_event(
            event_id=self._event_id(plan, projection.revision),
            kind=EventKind.TASK_FRONTIER_CHANGED,
            payload={
                "planDigest": plan_object.digest,
                "catalogDigest": catalog.digest,
                "effectDigest": effect_object.digest,
                "bindingDigest": binding_object.digest,
                "observationDigest": observation_object.digest,
                "verificationDigest": verification_object.digest,
            },
            projection=projection,
            expected_revision=snapshot.projection.revision,
            referenced_objects=(
                plan_object,
                effect_object,
                binding_object,
                observation_object,
                verification_object,
            ),
        )
        return projection

    def _step_close(
        self,
        snapshot: TaskEventSnapshot,
        plan: ReadTaskPlan,
    ) -> TaskProjection:
        closed = self._ensure_closed(plan.workspace_id)
        data = _object(snapshot.data, "read Task result data")
        outcome: JsonValue = {
            "schemaVersion": 1,
            "kind": _OUTCOME_KIND,
            "taskId": plan.task_id,
            "goalId": plan.goal_id,
            "workspaceId": plan.workspace_id,
            "relativePath": plan.relative_path,
            "catalogDigest": _string(data, "catalogDigest"),
            "effectDigest": _string(data, "effectDigest"),
            "bindingDigest": _string(data, "bindingDigest"),
            "observationDigest": _string(data, "observationDigest"),
            "verificationDigest": _string(data, "verificationDigest"),
            "workspaceClosed": True,
        }
        outcome_object = self.storage.put_object(outcome, kind="task-outcome")
        projection = TaskProjection(
            task_id=plan.task_id,
            goal_id=plan.goal_id,
            state=TaskState.COMPLETED,
            active_node_id=None,
            ready_frontier=(),
            revision=snapshot.projection.revision + 1,
            updated_at_ms=self._timestamp(snapshot.projection.updated_at_ms),
        )
        plan_object = self.storage.objects.inspect(self._plan_digest(snapshot))
        self.storage.record_task_event(
            event_id=self._event_id(plan, projection.revision),
            kind=EventKind.TASK_STATE_CHANGED,
            payload={
                "planDigest": plan_object.digest,
                "outcomeDigest": outcome_object.digest,
                "workspaceClose": closed,
            },
            projection=projection,
            expected_revision=snapshot.projection.revision,
            referenced_objects=(plan_object, outcome_object),
        )
        return projection

    def _ensure_workspace(self, plan: ReadTaskPlan) -> dict[str, JsonValue]:
        try:
            workspace = self.runtime.call_tool(
                "workspace.get",
                {"schemaVersion": 1, "workspaceId": plan.workspace_id},
            )
        except RuntimeToolRejected as error:
            if not _missing_workspace(error):
                raise
            workspace = self.runtime.call_tool(
                "workspace.open",
                {
                    "schemaVersion": 1,
                    "sourceRepo": plan.source_repo,
                    "sourceRevision": plan.source_revision,
                    "workspaceId": plan.workspace_id,
                },
            )
        self._validate_workspace(workspace, plan)
        return _json_object(workspace, "Runtime Workspace")

    def _ensure_closed(self, workspace_id: str) -> dict[str, JsonValue]:
        try:
            self.runtime.call_tool(
                "workspace.get",
                {"schemaVersion": 1, "workspaceId": workspace_id},
            )
        except RuntimeToolRejected as error:
            if _missing_workspace(error):
                return {"workspaceId": workspace_id, "alreadyAbsent": True}
            raise
        try:
            closed = self.runtime.call_tool(
                "workspace.close",
                {"schemaVersion": 1, "workspaceId": workspace_id, "force": False},
            )
        except RuntimeToolRejected as error:
            if _missing_workspace(error):
                return {"workspaceId": workspace_id, "alreadyAbsent": True}
            raise
        if closed.get("workspaceId") != workspace_id:
            raise RuntimeProtocolError("workspace.close returned a different Workspace")
        return _json_object(closed, "Runtime Workspace close")

    def _validate_workspace(
        self,
        workspace: dict[str, Any],
        plan: ReadTaskPlan,
    ) -> None:
        if workspace.get("workspaceId") != plan.workspace_id:
            raise RuntimeProtocolError("Runtime returned a different Workspace identity")
        if workspace.get("sourceRevision") != plan.source_revision:
            raise RuntimeProtocolError("Runtime returned a different source revision")

    def _load_plan(self, snapshot: TaskEventSnapshot) -> ReadTaskPlan:
        digest = self._plan_digest(snapshot)
        value = self.storage.objects.get(digest, expected_kind="host-read-task-plan")
        if not isinstance(value, dict):
            raise ObjectCorrupt("read Task plan must be an object")
        try:
            return ReadTaskPlan.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("read Task plan is invalid") from error

    def _plan_digest(self, snapshot: TaskEventSnapshot) -> str:
        data = _object(snapshot.data, "read Task event data")
        digest = _string(data, "planDigest")
        _validate_digest(digest)
        return digest

    def _next_projection(
        self,
        current: TaskProjection,
        plan: ReadTaskPlan,
        frontier: str,
    ) -> TaskProjection:
        return TaskProjection(
            task_id=plan.task_id,
            goal_id=plan.goal_id,
            state=TaskState.READY,
            active_node_id=None,
            ready_frontier=(self._node(plan, frontier),),
            revision=current.revision + 1,
            updated_at_ms=self._timestamp(current.updated_at_ms),
        )

    def _timestamp(self, previous: int | None) -> int:
        value = self.clock_ms()
        if value < 0:
            raise ValueError("Host clock returned a negative timestamp")
        return value if previous is None else max(value, previous + 1)

    @staticmethod
    def _node(plan: ReadTaskPlan, stage: str) -> str:
        return f"node:{_task_token(plan.task_id)}:{stage}"

    @staticmethod
    def _event_id(plan: ReadTaskPlan, revision: int) -> str:
        return f"event:{_task_token(plan.task_id)}:r{revision}"


def _read_effect(plan: ReadTaskPlan) -> EffectEnvelope:
    action = "anc.object.read.v1"
    target = TargetRef(
        f"world_object:workspace-file:{plan.workspace_id}/{plan.relative_path}",
        None,
    )
    return EffectEnvelope(
        effect_id=f"effect:{_task_token(plan.task_id)}:read",
        target=target,
        mode=EffectMode.OBSERVE,
        action=SemanticAction(action, "anc.object-read-input.v1"),
        input=CanonicalInput({}),
        capability=CapabilityRequirement(
            plan.principal_id,
            action,
            target.object_id,
        ),
        delivery=DeliverySemantics(IdempotencyKind.NATURAL),
        result=ResultSemantics(
            ExecutionKind.SYNCHRONOUS,
            CompletionKind.ACCEPTED_VERIFICATION,
        ),
        verification=VerificationPlan(
            "independent-content-sha256.v1",
            (EvidenceKind.OBSERVATION,),
        ),
    )


def _missing_workspace(error: RuntimeToolRejected) -> bool:
    detail = error.detail
    return (
        detail.code == "INVALID_REQUEST"
        and detail.field == "workspaceId"
        and detail.commit_state == "not_committed"
    )


def _task_token(task_id: str) -> str:
    return task_id.removeprefix("task:")


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _validate_digest(value: str) -> None:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("invalid sha256 digest")


def _object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise JournalCorruption(f"{label} must be an object")
    return value


def _string(value: dict[str, JsonValue], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise JournalCorruption(f"Task event field {key} must be a string")
    return result


def _json_object(value: dict[str, Any], label: str) -> dict[str, JsonValue]:
    try:
        validate_json_value(value)
    except ValueError as error:
        raise RuntimeProtocolError(f"{label} contains non-JSON data") from error
    return dict(value)
