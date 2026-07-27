from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anc_canonical import JsonValue, canonical_digest
from anc_effect_binding import EffectBinding, lower_to_ordivon
from anc_effect_ir import EffectEnvelope

from ...domain import EventKind, TaskProjection, TaskState
from ...journal import JournalCorruption
from ...kernel import HostKernel
from ...objects import ObjectCorrupt, StoredObject
from ...runtime import (
    RuntimeClient,
    RuntimeClientError,
    RuntimeProtocolError,
    RuntimeToolRejected,
    discover_execution_runtime_catalog,
    ensure_workspace,
    ensure_workspace_closed,
    find_jobs_by_client_request,
)
from ...storage import HostStorage, TaskEventSnapshot
from .._serde import (
    digest_text,
    json_object,
    require_object,
    require_string,
    task_token,
    validate_digest,
)
from .effect import mutation_effect
from .models import (
    _FAILED_JOB_STATES,
    DispatchIntent,
    GuardedMutationPlan,
    MutationStep,
    MutationSuperseded,
    MutationTaskError,
    MutationVerificationError,
    MutationVerificationReceipt,
    PreparedMutation,
    RuntimeJobObservation,
)

_OUTCOME_KIND = "ordivon.task-outcome"


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
            workspace = ensure_workspace(
                self.runtime,
                workspace_id=plan.workspace_id,
                source_repo=plan.source_repo,
                source_revision=plan.source_revision,
            )
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
            data = require_object(locked.snapshot.data, "mutation open data")
            expected_catalog = require_string(data, "catalogDigest")
            self.runtime.initialize()
            catalog = discover_execution_runtime_catalog(self.runtime)
            if catalog.digest != expected_catalog:
                raise RuntimeProtocolError(
                    "Runtime execution catalog changed before Dispatch preparation"
                )
            effect = mutation_effect(plan)
            binding = lower_to_ordivon(
                effect,
                catalog.exec_contract,
                binding_id=f"binding:{task_token(plan.task_id)}:exec:r1",
                workspace_id=plan.workspace_id,
            )
            if not isinstance(binding.arguments, dict):
                raise RuntimeProtocolError("workspace.exec Binding arguments are not an object")
            client_request_id = binding.arguments.get("clientRequestId")
            if not isinstance(client_request_id, str) or not client_request_id:
                raise RuntimeProtocolError("workspace.exec Binding omitted clientRequestId")
            dispatch = DispatchIntent(
                dispatch_id=f"dispatch:{task_token(plan.task_id)}:exec:r1",
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
                require_string(data, "catalogObjectDigest")
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
        jobs = find_jobs_by_client_request(
            self.runtime, prepared.dispatch.client_request_id
        )
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
            data = require_object(locked.snapshot.data, "mutation observation data")
            self.runtime.initialize()
            catalog = discover_execution_runtime_catalog(self.runtime)
            if catalog.digest != require_string(data, "catalogDigest"):
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
            expected_digest = digest_text(plan.content)
            observed_digest = digest_text(content)
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
                    "jobId": require_string(data, "jobId"),
                    "attemptId": data.get("attemptId"),
                    "jobStatus": require_string(data, "jobStatus"),
                    "observationDigest": require_string(data, "observationDigest"),
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
                job_id=require_string(data, "jobId"),
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
            data = require_object(locked.snapshot.data, "mutation verification data")
            closed = ensure_workspace_closed(
                self.runtime, plan.workspace_id, force=True
            )
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
                "jobId": require_string(data, "jobId"),
                "observationDigest": require_string(data, "observationDigest"),
                "verificationDigest": require_string(data, "verificationDigest"),
                "workspaceClosed": True,
                "deliveryReconciled": True,
            }
            outcome_object = self.storage.put_object(outcome, kind="task-outcome")
            references = self._state_references(data) + (
                self.storage.objects.inspect(require_string(data, "readObservationDigest")),
                self.storage.objects.inspect(require_string(data, "verificationDigest")),
                outcome_object,
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.TASK_STATE_CHANGED,
                payload={
                    **self._state_fields(data),
                    "jobId": require_string(data, "jobId"),
                    "attemptId": data.get("attemptId"),
                    "jobStatus": require_string(data, "jobStatus"),
                    "observationDigest": require_string(data, "observationDigest"),
                    "readObservationDigest": require_string(data, "readObservationDigest"),
                    "verificationDigest": require_string(data, "verificationDigest"),
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
                job_id=require_string(data, "jobId"),
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
            data = require_object(locked.snapshot.data, "prepared Dispatch data")
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
        typed_payload = json_object(payload, "Runtime Job observation")
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
            data = require_object(locked.snapshot.data, "mutation Dispatch data")
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
        data = require_object(snapshot.data, "prepared Dispatch data")
        plan = self._load_plan(snapshot)
        effect_object = self.storage.objects.inspect(require_string(data, "effectDigest"))
        binding_object = self.storage.objects.inspect(require_string(data, "bindingDigest"))
        dispatch_object = self.storage.objects.inspect(require_string(data, "dispatchDigest"))
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
            require_string(data, "dispatchDigest"),
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
            "planDigest": require_string(data, "planDigest"),
            "catalogDigest": require_string(data, "catalogDigest"),
            "catalogObjectDigest": require_string(data, "catalogObjectDigest"),
            "effectDigest": require_string(data, "effectDigest"),
            "bindingDigest": require_string(data, "bindingDigest"),
            "dispatchDigest": require_string(data, "dispatchDigest"),
            "clientRequestId": require_string(data, "clientRequestId"),
        }

    def _state_references(
        self, data: dict[str, JsonValue]
    ) -> tuple[StoredObject, ...]:
        return tuple(
            self.storage.objects.inspect(require_string(data, field))
            for field in (
                "planDigest",
                "catalogObjectDigest",
                "effectDigest",
                "bindingDigest",
                "dispatchDigest",
            )
        )

    def _plan_digest(self, snapshot: TaskEventSnapshot) -> str:
        data = require_object(snapshot.data, "mutation Task event data")
        digest = require_string(data, "planDigest")
        validate_digest(digest)
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
        return f"node:{task_token(plan.task_id)}:{stage}"

    @staticmethod
    def _event_id(plan: GuardedMutationPlan, revision: int) -> str:
        return f"event:{task_token(plan.task_id)}:r{revision}"

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


