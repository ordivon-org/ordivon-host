from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Protocol

from anc_canonical import JsonValue, canonical_digest, validate_json_value
from anc_effect_binding import EffectBinding, lower_to_ordivon
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
from ..kernel import HostKernel
from ..objects import ObjectCorrupt, StoredObject
from ..runtime import (
    RuntimeClientError,
    RuntimeProtocolError,
    RuntimeToolRejected,
    discover_execution_runtime_catalog,
)
from ..storage import HostStorage, TaskEventSnapshot

_PLAN_KIND = "ordivon.host-guarded-mutation-plan"
_DISPATCH_KIND = "ordivon.runtime-dispatch-intent"
_OBSERVATION_KIND = "ordivon.runtime-job-observation"
_VERIFICATION_KIND = "ordivon.mutation-verification-receipt"
_OUTCOME_KIND = "ordivon.task-outcome"
_CREATE_SCRIPT = (
    "import base64,os,sys;"
    "path=sys.argv[1];data=base64.b64decode(sys.argv[2],validate=True);"
    "fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
    "stream=os.fdopen(fd,'wb');stream.write(data);stream.flush();"
    "os.fsync(stream.fileno());stream.close()"
)
_ACTIVE_JOB_STATES = {"queued", "working"}
_FAILED_JOB_STATES = {"failed", "timed_out", "cancelled"}
_UNKNOWN_JOB_STATES = {"lost", "orphaned", "unknown"}
_ALLOWED_JOB_STATES = (
    _ACTIVE_JOB_STATES | _FAILED_JOB_STATES | _UNKNOWN_JOB_STATES | {"succeeded"}
)


class RuntimeClient(Protocol):
    def initialize(self) -> dict[str, Any]: ...

    def list_tools(self) -> tuple[dict[str, Any], ...]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


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
        _validate_digest(self.request_digest)

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
            _validate_digest(digest)
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


class GuardedMutationHost:
    def __init__(
        self,
        storage: HostStorage,
        runtime: RuntimeClient,
        *,
        clock_ms: Callable[[], int],
        owner_id: str = "host:guarded-mutation-v0",
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if not owner_id or lease_ttl_ms < 1:
            raise ValueError("mutation Host owner and lease TTL are required")
        self.storage = storage
        self.runtime = runtime
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id,
            lease_ttl_ms=lease_ttl_ms,
        )

    def create(self, plan: GuardedMutationPlan) -> TaskProjection:
        plan_object = self.storage.put_object(plan.to_dict(), kind="host-mutation-task-plan")
        existing = self.storage.journal.get_task(plan.task_id)
        if existing is not None:
            if self._load_plan(self.storage.read_task_event(plan.task_id)) != plan:
                raise ValueError("Task identity is bound to another mutation plan")
            return existing
        return self.kernel.create_task(
            event_id=self._event_id(plan, 1),
            kind=EventKind.TASK_CREATED,
            task_id=plan.task_id,
            goal_id=plan.goal_id,
            payload={"planDigest": plan_object.digest},
            frontier=(self._node(plan, "open"),),
            referenced_objects=(plan_object,),
        ).projection

    def open_workspace(self, task_id: str) -> MutationStep:
        current = self._require_frontier(task_id, "open")
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(current.ready_frontier[0],),
            label="mutation",
            error_factory=self._kernel_error,
        ) as locked:
            plan = self._load_plan(locked.snapshot)
            self.runtime.initialize()
            catalog = discover_execution_runtime_catalog(self.runtime)
            catalog_object = self.storage.put_object(
                catalog.to_dict(), kind="runtime-execution-catalog"
            )
            workspace = self._ensure_workspace(plan)
            plan_object = self.storage.objects.inspect(
                self._plan_digest(locked.snapshot)
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.RUNTIME_LINKED,
                payload={
                    "planDigest": plan_object.digest,
                    "catalogDigest": catalog.digest,
                    "catalogObjectDigest": catalog_object.digest,
                    "workspace": workspace,
                },
                frontier=(self._node(plan, "dispatch"),),
                referenced_objects=(plan_object, catalog_object),
            ).projection
            return self._step(projection)

    def prepare(self, task_id: str) -> PreparedMutation:
        current = self._require_frontier(task_id, "dispatch")
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(current.ready_frontier[0],),
            label="mutation",
            error_factory=self._kernel_error,
        ) as locked:
            plan = self._load_plan(locked.snapshot)
            data = _object(locked.snapshot.data, "mutation open data")
            expected_catalog = _string(data, "catalogDigest")
            self.runtime.initialize()
            catalog = discover_execution_runtime_catalog(self.runtime)
            if catalog.digest != expected_catalog:
                raise RuntimeProtocolError(
                    "Runtime execution catalog changed before Dispatch preparation"
                )
            effect = _mutation_effect(plan)
            binding = lower_to_ordivon(
                effect,
                catalog.exec_contract,
                binding_id=f"binding:{_task_token(plan.task_id)}:exec:r1",
                workspace_id=plan.workspace_id,
            )
            if not isinstance(binding.arguments, dict):
                raise RuntimeProtocolError("workspace.exec Binding arguments are not an object")
            client_request_id = binding.arguments.get("clientRequestId")
            if not isinstance(client_request_id, str) or not client_request_id:
                raise RuntimeProtocolError("workspace.exec Binding omitted clientRequestId")
            dispatch = DispatchIntent(
                dispatch_id=f"dispatch:{_task_token(plan.task_id)}:exec:r1",
                effect_id=effect.effect_id,
                binding_id=binding.binding_id,
                client_request_id=client_request_id,
                workspace_id=plan.workspace_id,
                operation="workspace.exec",
                request_digest=canonical_digest(binding.arguments),
            )
            effect_object = self.storage.put_object(effect.to_dict(), kind="effect")
            binding_object = self.storage.put_object(
                binding.to_dict(), kind="effect-binding"
            )
            dispatch_object = self.storage.put_object(
                dispatch.to_dict(), kind="runtime-dispatch-intent"
            )
            plan_object = self.storage.objects.inspect(
                self._plan_digest(locked.snapshot)
            )
            catalog_object = self.storage.objects.inspect(
                _string(data, "catalogObjectDigest")
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.RUNTIME_DISPATCH_PREPARED,
                payload={
                    "planDigest": plan_object.digest,
                    "catalogDigest": catalog.digest,
                    "catalogObjectDigest": catalog_object.digest,
                    "effectDigest": effect_object.digest,
                    "bindingDigest": binding_object.digest,
                    "dispatchDigest": dispatch_object.digest,
                    "clientRequestId": dispatch.client_request_id,
                },
                state=TaskState.WAITING,
                frontier=(self._node(plan, "reconcile"),),
                referenced_objects=(
                    plan_object,
                    catalog_object,
                    effect_object,
                    binding_object,
                    dispatch_object,
                ),
            ).projection
            return PreparedMutation(
                task_id=plan.task_id,
                task_revision=projection.revision,
                plan=plan,
                effect_object=effect_object,
                binding_object=binding_object,
                dispatch_object=dispatch_object,
                dispatch=dispatch,
                arguments=dict(binding.arguments),
            )

    def load_prepared(self, task_id: str) -> PreparedMutation:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind not in {
            EventKind.RUNTIME_DISPATCH_PREPARED,
            EventKind.RUNTIME_OUTCOME_UNKNOWN,
        }:
            raise MutationTaskError("Task head does not preserve a prepared Dispatch")
        return self._prepared_from_snapshot(snapshot)

    def deliver(self, prepared: PreparedMutation) -> MutationStep:
        self._require_prepared_current(prepared)
        try:
            payload = self.runtime.call_tool("workspace.exec", prepared.arguments)
        except RuntimeToolRejected as error:
            if error.detail.commit_state == "not_committed":
                raise
            return self._record_unknown(prepared, error)
        except RuntimeClientError as error:
            return self._record_unknown(prepared, error)
        return self._record_observation(prepared, payload, reconciled=False)

    def reconcile(self, task_id: str, *, wait_ms: int = 30_000) -> MutationStep:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.projection.state is not TaskState.WAITING:
            raise MutationTaskError("Dispatch reconciliation requires a waiting Task")
        plan = self._load_plan(snapshot)
        if snapshot.projection.ready_frontier != (self._node(plan, "reconcile"),):
            raise MutationTaskError("Task is not at the reconciliation frontier")
        prepared = self._prepared_from_snapshot(snapshot)
        jobs = self._find_jobs(prepared.dispatch.client_request_id)
        if not jobs:
            return self._step(
                snapshot.projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                reconciled=True,
            )
        job_ids = {job.get("jobId") for job in jobs}
        if len(job_ids) != 1 or None in job_ids:
            raise RuntimeProtocolError(
                "one clientRequestId resolved to conflicting Runtime Jobs"
            )
        job_id = next(iter(job_ids))
        if not isinstance(job_id, str):
            raise RuntimeProtocolError("Runtime Job identity is invalid")
        payload = self.runtime.call_tool(
            "task.observe",
            {
                "schemaVersion": 1,
                "jobId": job_id,
                "waitMs": wait_ms,
                "stdoutTailBytes": 4_096,
                "stderrTailBytes": 4_096,
            },
        )
        return self._record_observation(prepared, payload, reconciled=True)

    def verify(self, task_id: str) -> MutationStep:
        current = self._require_frontier(task_id, "verify", TaskState.VERIFYING)
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.VERIFYING,
            expected_frontier=(current.ready_frontier[0],),
            label="mutation",
            error_factory=self._kernel_error,
        ) as locked:
            plan = self._load_plan(locked.snapshot)
            data = _object(locked.snapshot.data, "mutation observation data")
            self.runtime.initialize()
            catalog = discover_execution_runtime_catalog(self.runtime)
            if catalog.digest != _string(data, "catalogDigest"):
                raise RuntimeProtocolError(
                    "Runtime execution catalog changed before verification"
                )
            payload = self.runtime.call_tool(
                "workspace.read",
                {
                    "schemaVersion": 1,
                    "workspaceId": plan.workspace_id,
                    "relativePath": plan.relative_path,
                    "mode": "FULL",
                    "offset": 0,
                    "maxBytes": 65_536,
                },
            )
            content = payload.get("content")
            runtime_digest = payload.get("digest")
            if not isinstance(content, str) or not isinstance(runtime_digest, str):
                raise RuntimeProtocolError("workspace.read omitted content or digest")
            expected_digest = _digest_text(plan.content)
            observed_digest = _digest_text(content)
            accepted = (
                plan.content == content
                and expected_digest == runtime_digest == observed_digest
            )
            receipt = MutationVerificationReceipt(
                dispatch_id=self._load_dispatch(data).dispatch_id,
                method="exact-content-sha256.v1",
                relative_path=plan.relative_path,
                expected_digest=expected_digest,
                runtime_digest=runtime_digest,
                observed_digest=observed_digest,
                accepted=accepted,
            )
            if not receipt.accepted:
                raise MutationVerificationError(
                    "guarded mutation content verification failed"
                )
            read_observation: JsonValue = {
                "schemaVersion": 1,
                "kind": "ordivon.mutation-read-observation",
                "workspaceId": plan.workspace_id,
                "relativePath": plan.relative_path,
                "content": content,
                "digest": runtime_digest,
            }
            read_object = self.storage.put_object(
                read_observation, kind="mutation-read-observation"
            )
            verification_object = self.storage.put_object(
                receipt.to_dict(), kind="verification-receipt"
            )
            references = self._state_references(data) + (
                read_object,
                verification_object,
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.VERIFICATION_ACCEPTED,
                payload={
                    **self._state_fields(data),
                    "jobId": _string(data, "jobId"),
                    "attemptId": data.get("attemptId"),
                    "jobStatus": _string(data, "jobStatus"),
                    "observationDigest": _string(data, "observationDigest"),
                    "readObservationDigest": read_object.digest,
                    "verificationDigest": verification_object.digest,
                },
                state=TaskState.READY,
                frontier=(self._node(plan, "close"),),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=receipt.dispatch_id,
                job_id=_string(data, "jobId"),
            )

    def close(self, task_id: str) -> MutationStep:
        current = self._require_frontier(task_id, "close")
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(current.ready_frontier[0],),
            label="mutation",
            error_factory=self._kernel_error,
        ) as locked:
            plan = self._load_plan(locked.snapshot)
            data = _object(locked.snapshot.data, "mutation verification data")
            closed = self._ensure_closed(plan.workspace_id)
            dispatch = self._load_dispatch(data)
            outcome: JsonValue = {
                "schemaVersion": 1,
                "kind": _OUTCOME_KIND,
                "taskId": plan.task_id,
                "goalId": plan.goal_id,
                "workspaceId": plan.workspace_id,
                "relativePath": plan.relative_path,
                "dispatchId": dispatch.dispatch_id,
                "clientRequestId": dispatch.client_request_id,
                "jobId": _string(data, "jobId"),
                "observationDigest": _string(data, "observationDigest"),
                "verificationDigest": _string(data, "verificationDigest"),
                "workspaceClosed": True,
                "deliveryReconciled": True,
            }
            outcome_object = self.storage.put_object(outcome, kind="task-outcome")
            references = self._state_references(data) + (
                self.storage.objects.inspect(_string(data, "readObservationDigest")),
                self.storage.objects.inspect(_string(data, "verificationDigest")),
                outcome_object,
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.TASK_STATE_CHANGED,
                payload={
                    **self._state_fields(data),
                    "jobId": _string(data, "jobId"),
                    "attemptId": data.get("attemptId"),
                    "jobStatus": _string(data, "jobStatus"),
                    "observationDigest": _string(data, "observationDigest"),
                    "readObservationDigest": _string(data, "readObservationDigest"),
                    "verificationDigest": _string(data, "verificationDigest"),
                    "outcomeDigest": outcome_object.digest,
                    "workspaceClose": closed,
                },
                state=TaskState.COMPLETED,
                frontier=(),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=dispatch.dispatch_id,
                job_id=_string(data, "jobId"),
                reconciled=True,
                completed=True,
            )

    def _record_unknown(
        self,
        prepared: PreparedMutation,
        error: RuntimeClientError,
    ) -> MutationStep:
        with self.kernel.locked_task(
            prepared.task_id,
            expected_revision=prepared.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(self._node(prepared.plan, "reconcile"),),
            label="mutation",
            error_factory=self._kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise MutationSuperseded("prepared Dispatch identity changed")
            uncertainty: JsonValue = {
                "schemaVersion": 1,
                "kind": "ordivon.runtime-uncertain-delivery",
                "dispatchId": prepared.dispatch.dispatch_id,
                "errorType": type(error).__name__,
                "message": str(error)[:2_048],
            }
            uncertainty_object = self.storage.put_object(
                uncertainty, kind="runtime-uncertain-delivery"
            )
            data = _object(locked.snapshot.data, "prepared Dispatch data")
            references = self._state_references(data) + (uncertainty_object,)
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.plan, locked.projection.revision + 1
                ),
                kind=EventKind.RUNTIME_OUTCOME_UNKNOWN,
                payload={
                    **self._state_fields(data),
                    "uncertaintyDigest": uncertainty_object.digest,
                },
                state=TaskState.WAITING,
                frontier=(self._node(prepared.plan, "reconcile"),),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
            )

    def _record_observation(
        self,
        prepared: PreparedMutation,
        payload: dict[str, Any],
        *,
        reconciled: bool,
    ) -> MutationStep:
        typed_payload = _json_object(payload, "Runtime Job observation")
        job_id = payload.get("jobId")
        status = payload.get("status")
        attempt_id = payload.get("attemptId")
        if not isinstance(job_id, str) or not isinstance(status, str):
            raise RuntimeProtocolError("Runtime Job observation omitted identity or status")
        if attempt_id is not None and not isinstance(attempt_id, str):
            raise RuntimeProtocolError("Runtime Attempt identity is invalid")
        observation = RuntimeJobObservation(
            dispatch_id=prepared.dispatch.dispatch_id,
            job_id=job_id,
            attempt_id=attempt_id,
            status=status,
            payload_digest=canonical_digest(typed_payload),
            payload=typed_payload,
        )
        if status in _FAILED_JOB_STATES:
            raise MutationTaskError(f"Runtime mutation Job ended with {status}")
        with self.kernel.locked_task(
            prepared.task_id,
            expected_state=TaskState.WAITING,
            expected_frontier=(self._node(prepared.plan, "reconcile"),),
            label="mutation",
            error_factory=self._observation_kernel_error,
        ) as locked:
            current_prepared = self._prepared_from_snapshot(locked.snapshot)
            if current_prepared.dispatch != prepared.dispatch:
                raise MutationSuperseded("prepared Dispatch changed before observation")
            observation_object = self.storage.put_object(
                observation.to_dict(), kind="runtime-job-observation"
            )
            data = _object(locked.snapshot.data, "mutation Dispatch data")
            if status == "succeeded":
                state = TaskState.VERIFYING
                frontier = "verify"
            else:
                state = TaskState.WAITING
                frontier = "reconcile"
            references = self._state_references(data) + (observation_object,)
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.plan, locked.projection.revision + 1
                ),
                kind=EventKind.RUNTIME_DISPATCH_OBSERVED,
                payload={
                    **self._state_fields(data),
                    "jobId": job_id,
                    "attemptId": attempt_id,
                    "jobStatus": status,
                    "observationDigest": observation_object.digest,
                    "reconciled": reconciled,
                },
                state=state,
                frontier=(self._node(prepared.plan, frontier),),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                job_id=job_id,
                reconciled=reconciled,
            )

    def _prepared_from_snapshot(self, snapshot: TaskEventSnapshot) -> PreparedMutation:
        data = _object(snapshot.data, "prepared Dispatch data")
        plan = self._load_plan(snapshot)
        effect_object = self.storage.objects.inspect(_string(data, "effectDigest"))
        binding_object = self.storage.objects.inspect(_string(data, "bindingDigest"))
        dispatch_object = self.storage.objects.inspect(_string(data, "dispatchDigest"))
        effect_value = self.storage.objects.get(effect_object.digest, expected_kind="effect")
        binding_value = self.storage.objects.get(
            binding_object.digest, expected_kind="effect-binding"
        )
        dispatch_value = self.storage.objects.get(
            dispatch_object.digest, expected_kind="runtime-dispatch-intent"
        )
        if not all(
            isinstance(value, dict)
            for value in (effect_value, binding_value, dispatch_value)
        ):
            raise ObjectCorrupt("prepared mutation semantic objects must be objects")
        try:
            effect = EffectEnvelope.from_dict(effect_value)
            binding = EffectBinding.from_dict(binding_value)
            dispatch = DispatchIntent.from_dict(dispatch_value)
        except ValueError as error:
            raise ObjectCorrupt("prepared mutation semantic object is invalid") from error
        if (
            effect.effect_id != dispatch.effect_id
            or binding.effect_id != effect.effect_id
            or binding.binding_id != dispatch.binding_id
            or canonical_digest(binding.arguments) != dispatch.request_digest
        ):
            raise JournalCorruption("prepared mutation semantic identities differ")
        if not isinstance(binding.arguments, dict):
            raise ObjectCorrupt("workspace.exec Binding arguments must be an object")
        return PreparedMutation(
            task_id=plan.task_id,
            task_revision=snapshot.projection.revision,
            plan=plan,
            effect_object=effect_object,
            binding_object=binding_object,
            dispatch_object=dispatch_object,
            dispatch=dispatch,
            arguments=dict(binding.arguments),
        )

    def _require_prepared_current(self, prepared: PreparedMutation) -> None:
        try:
            self.kernel.current_snapshot(
                prepared.task_id,
                expected_revision=prepared.task_revision,
                expected_state=TaskState.WAITING,
                expected_frontier=(self._node(prepared.plan, "reconcile"),),
                label="mutation",
                error_factory=self._prepared_kernel_error,
            )
        except KeyError as error:
            raise MutationSuperseded(
                "prepared Dispatch revision is no longer current"
            ) from error

    def _find_jobs(self, client_request_id: str) -> list[dict[str, Any]]:
        cursor: dict[str, JsonValue] | None = None
        seen_cursors: set[str] = set()
        matches: list[dict[str, Any]] = []
        for _ in range(100):
            arguments: dict[str, Any] = {"limit": 100}
            if cursor is not None:
                arguments["cursor"] = cursor
            page = self.runtime.call_tool("task.list", arguments)
            jobs = page.get("jobs")
            if not isinstance(jobs, list):
                raise RuntimeProtocolError("task.list omitted jobs")
            matches.extend(
                job
                for job in jobs
                if isinstance(job, dict)
                and job.get("clientRequestId") == client_request_id
            )
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                return matches
            if not isinstance(next_cursor, dict):
                raise RuntimeProtocolError("task.list returned an invalid cursor")
            typed_cursor = _json_object(next_cursor, "Runtime Job list cursor")
            cursor_digest = canonical_digest(typed_cursor)
            if cursor_digest in seen_cursors:
                raise RuntimeProtocolError("task.list repeated a pagination cursor")
            seen_cursors.add(cursor_digest)
            cursor = typed_cursor
        raise RuntimeProtocolError("task.list pagination exceeded the Host bound")

    def _ensure_workspace(self, plan: GuardedMutationPlan) -> dict[str, JsonValue]:
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
        if workspace.get("workspaceId") != plan.workspace_id:
            raise RuntimeProtocolError("Runtime returned another Workspace")
        if workspace.get("sourceRevision") != plan.source_revision:
            raise RuntimeProtocolError("Runtime returned another source revision")
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
                {"schemaVersion": 1, "workspaceId": workspace_id, "force": True},
            )
        except RuntimeToolRejected as error:
            if _missing_workspace(error):
                return {"workspaceId": workspace_id, "alreadyAbsent": True}
            raise
        if closed.get("workspaceId") != workspace_id:
            raise RuntimeProtocolError("workspace.close returned another Workspace")
        return _json_object(closed, "Runtime Workspace close")

    def _load_plan(self, snapshot: TaskEventSnapshot) -> GuardedMutationPlan:
        value = self.storage.objects.get(
            self._plan_digest(snapshot), expected_kind="host-mutation-task-plan"
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("mutation Task plan must be an object")
        try:
            return GuardedMutationPlan.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("mutation Task plan is invalid") from error

    def _load_dispatch(self, data: dict[str, JsonValue]) -> DispatchIntent:
        value = self.storage.objects.get(
            _string(data, "dispatchDigest"),
            expected_kind="runtime-dispatch-intent",
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("Runtime Dispatch intent must be an object")
        try:
            return DispatchIntent.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("Runtime Dispatch intent is invalid") from error

    @staticmethod
    def _state_fields(data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "planDigest": _string(data, "planDigest"),
            "catalogDigest": _string(data, "catalogDigest"),
            "catalogObjectDigest": _string(data, "catalogObjectDigest"),
            "effectDigest": _string(data, "effectDigest"),
            "bindingDigest": _string(data, "bindingDigest"),
            "dispatchDigest": _string(data, "dispatchDigest"),
            "clientRequestId": _string(data, "clientRequestId"),
        }

    def _state_references(
        self, data: dict[str, JsonValue]
    ) -> tuple[StoredObject, ...]:
        return tuple(
            self.storage.objects.inspect(_string(data, field))
            for field in (
                "planDigest",
                "catalogObjectDigest",
                "effectDigest",
                "bindingDigest",
                "dispatchDigest",
            )
        )

    def _plan_digest(self, snapshot: TaskEventSnapshot) -> str:
        data = _object(snapshot.data, "mutation Task event data")
        digest = _string(data, "planDigest")
        _validate_digest(digest)
        return digest

    def _require_frontier(
        self,
        task_id: str,
        stage: str,
        state: TaskState = TaskState.READY,
    ) -> TaskProjection:
        snapshot = self.kernel.current_snapshot(
            task_id,
            expected_state=state,
            label="mutation",
            error_factory=self._kernel_error,
        )
        plan = self._load_plan(snapshot)
        if snapshot.projection.ready_frontier != (self._node(plan, stage),):
            raise MutationTaskError(f"Task is not at the {stage} frontier")
        return snapshot.projection

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return MutationSuperseded(message)
        if category == "state":
            return MutationTaskError(message.replace("requires a", "Task is not"))
        if category == "frontier":
            return MutationTaskError(message)
        return JournalCorruption(message)

    @staticmethod
    def _prepared_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision"}:
            return MutationSuperseded(
                "prepared Dispatch revision is no longer current"
            )
        if category in {"state", "frontier"}:
            return MutationSuperseded("prepared Dispatch Task is not waiting")
        return JournalCorruption(message)

    @staticmethod
    def _observation_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "state", "frontier"}:
            return MutationSuperseded("mutation Task advanced before observation")
        if category == "revision":
            return MutationSuperseded(message)
        return JournalCorruption(message)

    @staticmethod
    def _node(plan: GuardedMutationPlan, stage: str) -> str:
        return f"node:{_task_token(plan.task_id)}:{stage}"

    @staticmethod
    def _event_id(plan: GuardedMutationPlan, revision: int) -> str:
        return f"event:{_task_token(plan.task_id)}:r{revision}"

    @staticmethod
    def _step(
        projection: TaskProjection,
        *,
        dispatch_id: str | None = None,
        job_id: str | None = None,
        reconciled: bool = False,
        completed: bool = False,
    ) -> MutationStep:
        return MutationStep(
            task_id=projection.task_id,
            revision=projection.revision,
            state=projection.state,
            frontier=(projection.ready_frontier[0] if projection.ready_frontier else None),
            dispatch_id=dispatch_id,
            job_id=job_id,
            reconciled=reconciled,
            completed=completed,
        )


def _mutation_effect(plan: GuardedMutationPlan) -> EffectEnvelope:
    action = "anc.execution.launch.v1"
    target = TargetRef(f"world_object:ordivon-workspace:{plan.workspace_id}")
    encoded = base64.b64encode(plan.content.encode("utf-8")).decode("ascii")
    return EffectEnvelope(
        effect_id=f"effect:{_task_token(plan.task_id)}:exec",
        target=target,
        mode=EffectMode.CHANGE,
        action=SemanticAction(action, "anc.execution-launch-input.v1"),
        input=CanonicalInput(
            {
                "executable": "/usr/bin/python3",
                "args": ["-c", _CREATE_SCRIPT, plan.relative_path, encoded],
                "cwdRelative": ".",
                "env": {},
                "timeoutMs": plan.timeout_ms,
                "stdoutLimitBytes": 65_536,
                "stderrLimitBytes": 65_536,
                "waitMs": 0,
                "stdoutTailBytes": 4_096,
                "stderrTailBytes": 4_096,
            }
        ),
        capability=CapabilityRequirement(plan.principal_id, action, target.object_id),
        delivery=DeliverySemantics(IdempotencyKind.NONE),
        result=ResultSemantics(
            ExecutionKind.ASYNCHRONOUS,
            CompletionKind.ACCEPTED_VERIFICATION,
        ),
        verification=VerificationPlan(
            "exact-content-sha256.v1",
            (EvidenceKind.OBSERVATION,),
        ),
    )


def _missing_workspace(error: RuntimeToolRejected) -> bool:
    return (
        error.detail.code == "INVALID_REQUEST"
        and error.detail.field == "workspaceId"
        and error.detail.commit_state == "not_committed"
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
