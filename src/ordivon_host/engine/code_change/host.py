from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value
from anc_effect_binding import EffectBinding, bind_effect
from anc_effect_ir import (
    EffectEnvelope,
    SourceChangeSpec,
    SourceFileChange,
    source_change_effect,
)
from anc_tool_contract import ToolContract, normalize_mcp_tool_contract

from ...authority import CapabilityAuthorizer, TrustedLocalAuthorizer
from ...domain import EventKind, RepositoryResolver, StaticRepositoryResolver, TaskProjection, TaskState
from ...journal import JournalCorruption
from ...kernel import HostKernel, worker_owner_id
from ...objects import ObjectCorrupt, StoredObject
from ...runtime import (
    RuntimeClient,
    RuntimeClientError,
    RuntimeProtocolError,
    RuntimeToolRejected,
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
from ..mutation.models import RuntimeJobObservation
from .models import (
    CodeChangeDispatch,
    CodeChangeError,
    CodeChangePlan,
    CodeChangeStep,
    CodeChangeSuperseded,
    CodeChangeVerificationError,
    CodeChangeVerificationReceipt,
    PreparedCodeChange,
)
from .request import build_exec_plan_request

_OUTCOME_KIND = "ordivon.code-change-task-outcome"
_REQUIRED_TOOLS = (
    "task.list",
    "task.observe",
    "workspace.close",
    "workspace.diff",
    "workspace.execPlan",
    "workspace.get",
    "workspace.open",
    "workspace.read",
)
_ACTIVE_JOB_STATES = {"queued", "working"}
_BLOCKED_JOB_STATES = {"failed", "timed_out", "cancelled", "lost", "orphaned", "unknown"}


class CodeChangeHost:
    def __init__(
        self,
        storage: HostStorage,
        runtime: RuntimeClient,
        *,
        clock_ms: Callable[[], int],
        repository_resolver: RepositoryResolver | None = None,
        authorizer: CapabilityAuthorizer | None = None,
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if owner_id is not None and (not owner_id or owner_id != owner_id.strip()):
            raise ValueError("explicit Host owner identity must be trimmed")
        if lease_ttl_ms < 1:
            raise ValueError("code change Host lease TTL must be positive")
        self.storage = storage
        self.runtime = runtime
        self.repository_resolver = repository_resolver or StaticRepositoryResolver({})
        self.authorizer = authorizer or TrustedLocalAuthorizer()
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:code-change-v2"),
            lease_ttl_ms=lease_ttl_ms,
        )

    def create(self, plan: CodeChangePlan) -> TaskProjection:
        plan_object = self.storage.put_object(plan.to_dict(), kind="host-code-change-plan")
        existing = self.storage.journal.get_task(plan.task_id)
        if existing is not None:
            if self._load_plan(self.storage.read_task_event(plan.task_id)) != plan:
                raise ValueError("Task identity is bound to another code change plan")
            return existing
        return self.kernel.create_task(
            event_id=self._event_id(plan, 1),
            kind=EventKind.TASK_CREATED,
            task_id=plan.task_id,
            goal_id=plan.goal_id,
            payload={
                "planDigest": plan_object.digest,
                "referenceDigests": [plan_object.digest],
            },
            frontier=(self._node(plan, "open"),),
            referenced_objects=(plan_object,),
        ).projection

    def open_workspace(self, task_id: str) -> CodeChangeStep:
        snapshot = self._current(task_id, "open")
        plan = self._load_plan(snapshot)
        catalog_value, catalog_digest, _ = self._runtime_catalog()
        catalog_object = self.storage.put_object(
            catalog_value, kind="runtime-code-change-catalog"
        )
        source_repo = self.repository_resolver.resolve(plan.repository)
        workspace = ensure_workspace(
            self.runtime,
            workspace_id=plan.workspace_id,
            source_repo=str(source_repo),
            source_revision=plan.repository.revision,
        )
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=TaskState.READY,
            expected_frontier=(self._node(plan, "open"),),
            label="code-change",
            error_factory=self._kernel_error,
        ) as locked:
            self._require_same_plan(locked.snapshot, plan)
            plan_object = self.storage.objects.inspect(self._plan_digest(locked.snapshot))
            references = (plan_object, catalog_object)
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.RUNTIME_LINKED,
                payload={
                    "planDigest": plan_object.digest,
                    "catalogDigest": catalog_digest,
                    "catalogObjectDigest": catalog_object.digest,
                    "workspace": workspace,
                    "referenceDigests": [item.digest for item in references],
                },
                frontier=(self._node(plan, "dispatch"),),
                referenced_objects=references,
            ).projection
        return self._step(projection)

    def prepare(self, task_id: str) -> PreparedCodeChange:
        snapshot = self._current(task_id, "dispatch")
        plan = self._load_plan(snapshot)
        data = require_object(snapshot.data, "code change open data")
        _, catalog_digest, contract = self._runtime_catalog()
        if catalog_digest != require_string(data, "catalogDigest"):
            raise RuntimeProtocolError(
                "Runtime code-change catalog changed before Dispatch preparation"
            )
        spec = SourceChangeSpec(
            repository_id=plan.repository.repository_id,
            base_revision=plan.repository.revision,
            files=tuple(
                SourceFileChange(
                    relative_path=item.relative_path,
                    expected_digest=item.expected_digest,
                    result_digest=item.result_digest,
                    content=item.content,
                )
                for item in plan.files
            ),
            verification_ids=tuple(item.check_id for item in plan.checks),
        )
        effect = source_change_effect(
            effect_id=f"effect:{task_token(plan.task_id)}:source-change:r1",
            principal_id=plan.principal_id,
            spec=spec,
        )
        authority_decision = self.authorizer.authorize(effect.capability)
        authority_object = self.storage.put_object(
            authority_decision.to_dict(), kind="capability-decision"
        )
        arguments = build_exec_plan_request(plan)
        binding = bind_effect(
            effect,
            contract,
            encoder_id="anc.binding.ordivon.workspace-exec-plan-source-change",
            binding_id=f"binding:{task_token(plan.task_id)}:source-change:r1",
            revision=1,
            arguments=arguments,
        )
        effect_object = self.storage.put_object(effect.to_dict(), kind="effect")
        binding_object = self.storage.put_object(
            binding.to_dict(), kind="effect-binding"
        )
        request_object = self.storage.put_object(
            arguments, kind="runtime-code-change-request"
        )
        client_request_id = arguments.get("clientRequestId")
        if not isinstance(client_request_id, str) or not client_request_id:
            raise RuntimeProtocolError("workspace.execPlan request omitted clientRequestId")
        dispatch = CodeChangeDispatch(
            dispatch_id=f"dispatch:{task_token(plan.task_id)}:exec-plan:r1",
            effect_id=effect.effect_id,
            binding_id=binding.binding_id,
            authority_decision_digest=authority_object.digest,
            client_request_id=client_request_id,
            workspace_id=plan.workspace_id,
            operation="workspace.execPlan",
            request_digest=canonical_digest(arguments),
        )
        dispatch_object = self.storage.put_object(
            dispatch.to_dict(), kind="runtime-code-change-dispatch"
        )
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=TaskState.READY,
            expected_frontier=(self._node(plan, "dispatch"),),
            label="code-change",
            error_factory=self._kernel_error,
        ) as locked:
            self._require_same_plan(locked.snapshot, plan)
            current = require_object(locked.snapshot.data, "code change open data")
            if require_string(current, "catalogDigest") != catalog_digest:
                raise CodeChangeSuperseded("Runtime catalog changed during preparation")
            plan_object = self.storage.objects.inspect(self._plan_digest(locked.snapshot))
            catalog_object = self.storage.objects.inspect(
                require_string(current, "catalogObjectDigest")
            )
            references = (
                plan_object,
                catalog_object,
                effect_object,
                binding_object,
                authority_object,
                request_object,
                dispatch_object,
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.RUNTIME_DISPATCH_PREPARED,
                payload={
                    "planDigest": plan_object.digest,
                    "catalogDigest": catalog_digest,
                    "catalogObjectDigest": catalog_object.digest,
                    "effectDigest": effect_object.digest,
                    "bindingDigest": binding_object.digest,
                    "authorityDecisionDigest": authority_object.digest,
                    "requestObjectDigest": request_object.digest,
                    "dispatchDigest": dispatch_object.digest,
                    "clientRequestId": dispatch.client_request_id,
                    "referenceDigests": [item.digest for item in references],
                },
                state=TaskState.WAITING,
                frontier=(self._node(plan, "reconcile"),),
                referenced_objects=references,
            ).projection
        return PreparedCodeChange(
            task_id=plan.task_id,
            task_revision=projection.revision,
            plan=plan,
            effect_object=effect_object,
            binding_object=binding_object,
            authority_object=authority_object,
            dispatch_object=dispatch_object,
            request_object=request_object,
            dispatch=dispatch,
            arguments=arguments,
        )

    def load_prepared(self, task_id: str) -> PreparedCodeChange:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind not in {
            EventKind.RUNTIME_DISPATCH_PREPARED,
            EventKind.RUNTIME_OUTCOME_UNKNOWN,
        }:
            raise CodeChangeError("Task head does not preserve a prepared code change")
        return self._prepared_from_snapshot(snapshot)

    def deliver(self, prepared: PreparedCodeChange) -> CodeChangeStep:
        self._require_prepared_current(prepared)
        try:
            payload = self.runtime.call_tool("workspace.execPlan", prepared.arguments)
        except RuntimeToolRejected as error:
            if error.detail.commit_state == "not_committed":
                raise
            return self._record_unknown(prepared, error)
        except RuntimeClientError as error:
            return self._record_unknown(prepared, error)
        return self._record_observation(prepared, payload, reconciled=False)

    def reconcile(self, task_id: str, *, wait_ms: int = 30_000) -> CodeChangeStep:
        snapshot = self.storage.read_task_event(task_id)
        plan = self._load_plan(snapshot)
        if (
            snapshot.projection.state is not TaskState.WAITING
            or snapshot.projection.ready_frontier != (self._node(plan, "reconcile"),)
        ):
            raise CodeChangeError("Task is not at the code-change reconciliation frontier")
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
                "one code-change clientRequestId resolved to conflicting Runtime Jobs"
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
                "stdoutTailBytes": 8_192,
                "stderrTailBytes": 8_192,
            },
        )
        return self._record_observation(prepared, payload, reconciled=True)

    def verify(self, task_id: str) -> CodeChangeStep:
        snapshot = self._current(task_id, "verify", TaskState.VERIFYING)
        plan = self._load_plan(snapshot)
        if snapshot.event_kind == EventKind.VERIFICATION_RECORDED:
            try:
                return self._accept_prepared_verification(snapshot, plan)
            except RuntimeToolRejected as error:
                if not _is_source_state_mismatch(error):
                    raise
                snapshot = self.storage.read_task_event(task_id)
        prepared = self._prepare_verification(snapshot, plan)
        return self._accept_prepared_verification(prepared, plan)

    def _prepare_verification(
        self,
        snapshot: TaskEventSnapshot,
        plan: CodeChangePlan,
    ) -> TaskEventSnapshot:
        data = require_object(snapshot.data, "code change observation data")
        dispatch = self._load_dispatch(data)
        job_id = require_string(data, "jobId")
        completed_steps = self._positive_int(data, "completedSteps")
        total_steps = self._positive_int(data, "totalSteps")
        expected_steps = 1 + len(plan.checks)
        if completed_steps != expected_steps or total_steps != expected_steps:
            raise CodeChangeVerificationError(
                "Runtime Job did not complete every code-change step"
            )
        before_state = self._workspace_source_state(plan)
        file_results: list[dict[str, JsonValue]] = []
        file_objects: list[StoredObject] = []
        for item in plan.files:
            payload = self.runtime.call_tool(
                "workspace.read",
                {
                    "schemaVersion": 1,
                    "workspaceId": plan.workspace_id,
                    "relativePath": item.relative_path,
                    "mode": "FULL",
                    "offset": 0,
                    "maxBytes": 524_288,
                },
            )
            content = payload.get("content")
            runtime_digest = payload.get("digest")
            if not isinstance(content, str) or not isinstance(runtime_digest, str):
                raise RuntimeProtocolError("workspace.read omitted content or digest")
            observed_digest = digest_text(content)
            accepted = (
                content == item.content
                and item.result_digest == runtime_digest == observed_digest
            )
            result: dict[str, JsonValue] = {
                "relativePath": item.relative_path,
                "expectedDigest": item.result_digest,
                "runtimeDigest": runtime_digest,
                "observedDigest": observed_digest,
                "accepted": accepted,
            }
            file_results.append(result)
            file_objects.append(
                self.storage.put_object(result, kind="code-change-file-verification")
            )
        diff_payload = self.runtime.call_tool(
            "workspace.diff",
            {
                "schemaVersion": 1,
                "workspaceId": plan.workspace_id,
                "maxBytes": 1_048_576,
            },
        )
        diff_text = diff_payload.get("diff")
        truncated = diff_payload.get("truncated", False)
        changed = diff_payload.get("changedPaths")
        modified = diff_payload.get("modifiedPaths")
        added = diff_payload.get("addedPaths")
        deleted = diff_payload.get("deletedPaths")
        renamed = diff_payload.get("renamedPaths")
        untracked = diff_payload.get("untrackedPaths")
        path_lists = (changed, modified, added, deleted, untracked)
        if (
            not isinstance(diff_text, str)
            or type(truncated) is not bool
            or any(
                not isinstance(values, list)
                or any(not isinstance(path, str) for path in values)
                for values in path_lists
            )
            or not isinstance(renamed, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"fromPath", "toPath"}
                or any(not isinstance(item[key], str) for key in item)
                for item in renamed
            )
        ):
            raise RuntimeProtocolError("workspace.diff returned invalid structured fields")
        planned_paths = {item.relative_path for item in plan.files}
        diff_accepted = (
            bool(diff_text)
            and not truncated
            and set(changed) == planned_paths
            and set(modified) == planned_paths
            and not added
            and not deleted
            and not renamed
            and not untracked
        )
        if not diff_accepted:
            raise CodeChangeVerificationError(
                "Workspace structured diff differs from the exact planned file set"
            )
        after_state = self._workspace_source_state(plan)
        if after_state != before_state:
            raise CodeChangeVerificationError(
                "Workspace source state changed while verification evidence was collected"
            )
        diff_value = json_object(diff_payload, "Workspace diff")
        diff_object = self.storage.put_object(diff_value, kind="workspace-diff")
        accepted = all(result["accepted"] is True for result in file_results)
        receipt = CodeChangeVerificationReceipt(
            dispatch_id=dispatch.dispatch_id,
            job_id=job_id,
            completed_steps=completed_steps,
            total_steps=total_steps,
            file_results=tuple(file_results),
            changed_paths=tuple(changed),
            diff_digest=canonical_digest(diff_value),
            diff_accepted=diff_accepted,
            accepted=accepted and diff_accepted,
        )
        if not receipt.accepted:
            raise CodeChangeVerificationError("code file verification failed")
        verification_object = self.storage.put_object(
            receipt.to_dict(), kind="code-change-verification-receipt"
        )
        with self.kernel.locked_task(
            plan.task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=TaskState.VERIFYING,
            expected_frontier=(self._node(plan, "verify"),),
            label="code-change verification preparation",
            error_factory=self._kernel_error,
        ) as locked:
            self._require_same_plan(locked.snapshot, plan)
            current = require_object(locked.snapshot.data, "code change observation data")
            if require_string(current, "jobId") != job_id:
                raise CodeChangeSuperseded("Runtime Job changed before verification")
            references = self._reference_objects(current) + tuple(file_objects) + (
                diff_object,
                verification_object,
            )
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.VERIFICATION_RECORDED,
                payload={
                    **self._state_fields(current),
                    "jobId": job_id,
                    "attemptId": current.get("attemptId"),
                    "jobStatus": require_string(current, "jobStatus"),
                    "observationDigest": require_string(current, "observationDigest"),
                    "completedSteps": completed_steps,
                    "totalSteps": total_steps,
                    "fileVerificationDigests": [item.digest for item in file_objects],
                    "diffObjectDigest": diff_object.digest,
                    "verificationDigest": verification_object.digest,
                    "sourceStateDigest": before_state,
                    "referenceDigests": [item.digest for item in references],
                },
                state=TaskState.VERIFYING,
                frontier=(self._node(plan, "verify"),),
                referenced_objects=references,
            ).projection
        return self.storage.read_task_event(projection.task_id)

    def _accept_prepared_verification(
        self,
        snapshot: TaskEventSnapshot,
        plan: CodeChangePlan,
    ) -> CodeChangeStep:
        if snapshot.event_kind != EventKind.VERIFICATION_RECORDED:
            raise CodeChangeError("code-change verification evidence is not prepared")
        data = require_object(snapshot.data, "prepared code change verification")
        source_state_digest = require_string(data, "sourceStateDigest")
        validate_digest(source_state_digest)
        closed = self.runtime.call_tool(
            "workspace.close",
            {
                "schemaVersion": 1,
                "workspaceId": plan.workspace_id,
                "force": True,
                "expectedSourceStateDigest": source_state_digest,
            },
        )
        if (
            closed.get("workspaceId") != plan.workspace_id
            or closed.get("sourceStateDigest") != source_state_digest
        ):
            raise RuntimeProtocolError(
                "workspace.close did not preserve the verified source state"
            )
        close_value = json_object(closed, "verified Workspace close")
        close_object = self.storage.put_object(
            close_value, kind="workspace-close-receipt"
        )
        with self.kernel.locked_task(
            plan.task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=TaskState.VERIFYING,
            expected_frontier=(self._node(plan, "verify"),),
            label="code-change verification admission",
            error_factory=self._kernel_error,
        ) as locked:
            self._require_same_plan(locked.snapshot, plan)
            current = require_object(locked.snapshot.data, "prepared code change verification")
            if (
                require_string(current, "sourceStateDigest") != source_state_digest
                or require_string(current, "verificationDigest")
                != require_string(data, "verificationDigest")
            ):
                raise CodeChangeSuperseded(
                    "prepared verification changed before Workspace closure admission"
                )
            references = self._reference_objects(current) + (close_object,)
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.VERIFICATION_ACCEPTED,
                payload={
                    **current,
                    "workspaceClose": close_value,
                    "workspaceCloseObjectDigest": close_object.digest,
                    "referenceDigests": [item.digest for item in references],
                },
                state=TaskState.READY,
                frontier=(self._node(plan, "close"),),
                referenced_objects=references,
            ).projection
        return self._step(
            projection,
            dispatch_id=self._load_dispatch(data).dispatch_id,
            job_id=require_string(data, "jobId"),
        )

    def _workspace_source_state(self, plan: CodeChangePlan) -> str:
        workspace = self.runtime.call_tool(
            "workspace.get",
            {"schemaVersion": 1, "workspaceId": plan.workspace_id},
        )
        if (
            workspace.get("workspaceId") != plan.workspace_id
            or workspace.get("sourceRevision") != plan.source_revision
        ):
            raise RuntimeProtocolError("Runtime returned another code-change Workspace")
        digest = workspace.get("sourceStateDigest")
        if not isinstance(digest, str):
            raise RuntimeProtocolError("workspace.get omitted sourceStateDigest")
        validate_digest(digest)
        return digest

    def close(self, task_id: str) -> CodeChangeStep:
        snapshot = self.storage.read_task_event(task_id)
        plan = self._load_plan(snapshot)
        if snapshot.projection.ready_frontier != (self._node(plan, "close"),):
            raise CodeChangeError("Task is not at the code-change close frontier")
        if snapshot.projection.state not in {TaskState.READY, TaskState.BLOCKED}:
            raise CodeChangeError("code-change close requires ready or blocked state")
        data = require_object(snapshot.data, "code change close data")
        succeeded = snapshot.projection.state is TaskState.READY
        if succeeded:
            closed = data.get("workspaceClose")
            if not isinstance(closed, dict):
                raise JournalCorruption(
                    "verified code change has no fenced Workspace close receipt"
                )
            source_state_digest = require_string(data, "sourceStateDigest")
            if closed.get("sourceStateDigest") != source_state_digest:
                raise JournalCorruption("Workspace close receipt source state differs")
        else:
            closed = ensure_workspace_closed(self.runtime, plan.workspace_id, force=True)
        outcome: JsonValue = {
            "schemaVersion": 1,
            "kind": _OUTCOME_KIND,
            "taskId": plan.task_id,
            "goalId": plan.goal_id,
            "workspaceId": plan.workspace_id,
            "sourceRevision": plan.source_revision,
            "dispatchId": self._load_dispatch(data).dispatch_id,
            "clientRequestId": require_string(data, "clientRequestId"),
            "jobId": require_string(data, "jobId"),
            "status": "completed" if succeeded else "failed",
            "workspaceClosed": True,
            "diffObjectDigest": data.get("diffObjectDigest"),
            "verificationDigest": data.get("verificationDigest"),
            "sourceStateDigest": data.get("sourceStateDigest"),
        }
        outcome_object = self.storage.put_object(outcome, kind="task-outcome")
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=snapshot.projection.state,
            expected_frontier=(self._node(plan, "close"),),
            label="code-change",
            error_factory=self._kernel_error,
        ) as locked:
            self._require_same_plan(locked.snapshot, plan)
            current = require_object(locked.snapshot.data, "code change close data")
            if require_string(current, "jobId") != require_string(data, "jobId"):
                raise CodeChangeSuperseded("Runtime Job changed before close")
            references = self._reference_objects(current) + (outcome_object,)
            projection = locked.commit(
                event_id=self._event_id(plan, locked.projection.revision + 1),
                kind=EventKind.TASK_STATE_CHANGED,
                payload={
                    **current,
                    "outcomeDigest": outcome_object.digest,
                    "workspaceClose": closed,
                    "referenceDigests": [item.digest for item in references],
                },
                state=TaskState.COMPLETED if succeeded else TaskState.FAILED,
                frontier=(),
                referenced_objects=references,
            ).projection
        return self._step(
            projection,
            dispatch_id=self._load_dispatch(data).dispatch_id,
            job_id=require_string(data, "jobId"),
            reconciled=True,
            completed=succeeded,
        )

    def _record_unknown(
        self,
        prepared: PreparedCodeChange,
        error: RuntimeClientError,
    ) -> CodeChangeStep:
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
        with self.kernel.locked_task(
            prepared.task_id,
            expected_revision=prepared.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(self._node(prepared.plan, "reconcile"),),
            label="code-change",
            error_factory=self._prepared_kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise CodeChangeSuperseded("prepared code-change Dispatch changed")
            data = require_object(locked.snapshot.data, "prepared code-change data")
            references = self._reference_objects(data) + (uncertainty_object,)
            projection = locked.commit(
                event_id=self._event_id(
                    prepared.plan, locked.projection.revision + 1
                ),
                kind=EventKind.RUNTIME_OUTCOME_UNKNOWN,
                payload={
                    **self._state_fields(data),
                    "uncertaintyDigest": uncertainty_object.digest,
                    "referenceDigests": [item.digest for item in references],
                },
                state=TaskState.WAITING,
                frontier=(self._node(prepared.plan, "reconcile"),),
                referenced_objects=references,
            ).projection
        return self._step(projection, dispatch_id=prepared.dispatch.dispatch_id)

    def _record_observation(
        self,
        prepared: PreparedCodeChange,
        payload: dict[str, Any],
        *,
        reconciled: bool,
    ) -> CodeChangeStep:
        typed = json_object(payload, "Runtime code-change Job observation")
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
            payload_digest=canonical_digest(typed),
            payload=typed,
        )
        completed_steps = payload.get("completedSteps", 0)
        total_steps = payload.get("totalSteps", 1 + len(prepared.plan.checks))
        if type(completed_steps) is not int or type(total_steps) is not int:
            raise RuntimeProtocolError("Runtime Job step counts are invalid")
        observation_object = self.storage.put_object(
            observation.to_dict(), kind="runtime-job-observation"
        )
        with self.kernel.locked_task(
            prepared.task_id,
            expected_state=TaskState.WAITING,
            expected_frontier=(self._node(prepared.plan, "reconcile"),),
            label="code-change",
            error_factory=self._observation_kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise CodeChangeSuperseded("prepared code-change Dispatch changed")
            data = require_object(locked.snapshot.data, "code-change Dispatch data")
            if status == "succeeded":
                state = TaskState.VERIFYING
                stage = "verify"
            elif status in _ACTIVE_JOB_STATES:
                state = TaskState.WAITING
                stage = "reconcile"
            elif status in _BLOCKED_JOB_STATES:
                state = TaskState.BLOCKED
                stage = "close"
            else:
                raise RuntimeProtocolError(f"unsupported Runtime Job status: {status}")
            references = self._reference_objects(data) + (observation_object,)
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
                    "completedSteps": completed_steps,
                    "totalSteps": total_steps,
                    "failedStepId": payload.get("failedStepId"),
                    "failedStepIndex": payload.get("failedStepIndex"),
                    "reconciled": reconciled,
                    "referenceDigests": [item.digest for item in references],
                },
                state=state,
                frontier=(self._node(prepared.plan, stage),),
                referenced_objects=references,
            ).projection
        return self._step(
            projection,
            dispatch_id=prepared.dispatch.dispatch_id,
            job_id=job_id,
            reconciled=reconciled,
        )

    def _prepared_from_snapshot(self, snapshot: TaskEventSnapshot) -> PreparedCodeChange:
        data = require_object(snapshot.data, "prepared code-change data")
        plan = self._load_plan(snapshot)
        effect_object = self.storage.objects.inspect(require_string(data, "effectDigest"))
        binding_object = self.storage.objects.inspect(require_string(data, "bindingDigest"))
        authority_object = self.storage.objects.inspect(
            require_string(data, "authorityDecisionDigest")
        )
        request_object = self.storage.objects.inspect(
            require_string(data, "requestObjectDigest")
        )
        dispatch_object = self.storage.objects.inspect(
            require_string(data, "dispatchDigest")
        )
        effect_value = self.storage.objects.get(effect_object.digest, expected_kind="effect")
        binding_value = self.storage.objects.get(
            binding_object.digest, expected_kind="effect-binding"
        )
        authority_value = self.storage.objects.get(
            authority_object.digest, expected_kind="capability-decision"
        )
        arguments = self.storage.objects.get(
            request_object.digest, expected_kind="runtime-code-change-request"
        )
        dispatch_value = self.storage.objects.get(
            dispatch_object.digest, expected_kind="runtime-code-change-dispatch"
        )
        if not all(
            isinstance(value, dict)
            for value in (effect_value, binding_value, authority_value, arguments, dispatch_value)
        ):
            raise ObjectCorrupt("prepared code-change semantic objects must be objects")
        try:
            effect = EffectEnvelope.from_dict(effect_value)
            binding = EffectBinding.from_dict(binding_value)
            dispatch = CodeChangeDispatch.from_dict(dispatch_value)
        except ValueError as error:
            raise ObjectCorrupt("prepared code-change semantic object is invalid") from error
        typed_arguments = json_object(arguments, "code-change Runtime request")
        if (
            dispatch.effect_id != effect.effect_id
            or dispatch.binding_id != binding.binding_id
            or binding.effect_id != effect.effect_id
            or binding.arguments != typed_arguments
            or canonical_digest(typed_arguments) != dispatch.request_digest
            or dispatch.authority_decision_digest != authority_object.digest
            or authority_value.get("allowed") is not True
            or authority_value.get("principalId") != effect.capability.principal_id
            or authority_value.get("actionId") != effect.capability.action_id
            or authority_value.get("objectScope") != effect.capability.object_scope
        ):
            raise JournalCorruption("prepared code-change semantic identities differ")
        return PreparedCodeChange(
            task_id=plan.task_id,
            task_revision=snapshot.projection.revision,
            plan=plan,
            effect_object=effect_object,
            binding_object=binding_object,
            authority_object=authority_object,
            dispatch_object=dispatch_object,
            request_object=request_object,
            dispatch=dispatch,
            arguments=typed_arguments,
        )

    def _require_prepared_current(self, prepared: PreparedCodeChange) -> None:
        try:
            self.kernel.current_snapshot(
                prepared.task_id,
                expected_revision=prepared.task_revision,
                expected_state=TaskState.WAITING,
                expected_frontier=(self._node(prepared.plan, "reconcile"),),
                label="code-change",
                error_factory=self._prepared_kernel_error,
            )
        except KeyError as error:
            raise CodeChangeSuperseded(
                "prepared code-change Dispatch is no longer current"
            ) from error

    def _runtime_catalog(self) -> tuple[dict[str, JsonValue], str, ToolContract]:
        self.runtime.initialize()
        catalog: dict[str, dict[str, Any]] = {}
        for raw in self.runtime.list_tools():
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                raise RuntimeProtocolError("Runtime Tool descriptor has no name")
            if name in catalog:
                raise RuntimeProtocolError(f"Runtime Tool catalog repeats {name}")
            catalog[name] = raw
        missing = [name for name in _REQUIRED_TOOLS if name not in catalog]
        if missing:
            raise RuntimeProtocolError(
                f"Runtime code-change catalog is missing operations: {missing}"
            )
        selected: JsonValue = [
            {
                "name": catalog[name]["name"],
                "inputSchema": catalog[name].get("inputSchema"),
                "outputSchema": catalog[name].get("outputSchema"),
                "execution": catalog[name].get("execution"),
            }
            for name in _REQUIRED_TOOLS
        ]
        validate_json_value(selected)
        digest = canonical_digest(selected)
        revision = f"mcp-catalog:{digest[7:]}"
        contract = normalize_mcp_tool_contract(
            catalog["workspace.execPlan"],
            provider_id="ordivon-runtime",
            revision=revision,
            semantics={
                "semanticAction": "anc.source.change.v1",
                "execution": "asynchronous",
                "completion": "accepted-verification",
                "effectClass": "change",
                "idempotencySupport": "keyed",
                "correlation": "stable-key",
                "cancellation": "supported",
                "evidence": ["observation", "artifact"],
                "capabilityClass": "repository-source-change",
            },
        )
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "providerId": "ordivon-runtime",
            "revision": revision,
            "digest": digest,
            "operations": list(_REQUIRED_TOOLS),
            "tools": selected,
            "sourceChangeContract": contract.to_dict(),
        }
        return value, digest, contract

    def _load_plan(self, snapshot: TaskEventSnapshot) -> CodeChangePlan:
        value = self.storage.objects.get(
            self._plan_digest(snapshot), expected_kind="host-code-change-plan"
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("code change Task plan must be an object")
        try:
            return CodeChangePlan.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("code change Task plan is invalid") from error

    def _load_dispatch(self, data: dict[str, JsonValue]) -> CodeChangeDispatch:
        value = self.storage.objects.get(
            require_string(data, "dispatchDigest"),
            expected_kind="runtime-code-change-dispatch",
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("code-change Dispatch must be an object")
        try:
            return CodeChangeDispatch.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("code-change Dispatch is invalid") from error

    @staticmethod
    def _state_fields(data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "planDigest": require_string(data, "planDigest"),
            "catalogDigest": require_string(data, "catalogDigest"),
            "catalogObjectDigest": require_string(data, "catalogObjectDigest"),
            "effectDigest": require_string(data, "effectDigest"),
            "bindingDigest": require_string(data, "bindingDigest"),
            "authorityDecisionDigest": require_string(data, "authorityDecisionDigest"),
            "requestObjectDigest": require_string(data, "requestObjectDigest"),
            "dispatchDigest": require_string(data, "dispatchDigest"),
            "clientRequestId": require_string(data, "clientRequestId"),
        }

    def _reference_objects(
        self, data: dict[str, JsonValue]
    ) -> tuple[StoredObject, ...]:
        raw = data.get("referenceDigests")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise JournalCorruption("code-change referenceDigests are invalid")
        result: list[StoredObject] = []
        seen: set[str] = set()
        for digest in raw:
            validate_digest(digest)
            if digest in seen:
                continue
            seen.add(digest)
            result.append(self.storage.objects.inspect(digest))
        return tuple(result)

    def _plan_digest(self, snapshot: TaskEventSnapshot) -> str:
        data = require_object(snapshot.data, "code change event data")
        digest = require_string(data, "planDigest")
        validate_digest(digest)
        return digest

    def _current(
        self,
        task_id: str,
        stage: str,
        state: TaskState = TaskState.READY,
    ) -> TaskEventSnapshot:
        snapshot = self.kernel.current_snapshot(
            task_id,
            expected_state=state,
            label="code-change",
            error_factory=self._kernel_error,
        )
        plan = self._load_plan(snapshot)
        if snapshot.projection.ready_frontier != (self._node(plan, stage),):
            raise CodeChangeError(f"Task is not at the {stage} frontier")
        return snapshot

    def _require_same_plan(
        self, snapshot: TaskEventSnapshot, expected: CodeChangePlan
    ) -> None:
        if self._load_plan(snapshot) != expected:
            raise CodeChangeSuperseded("code change plan changed")

    @staticmethod
    def _positive_int(data: dict[str, JsonValue], field: str) -> int:
        value = data.get(field)
        if type(value) is not int or value < 1:
            raise RuntimeProtocolError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return CodeChangeSuperseded(message)
        if category in {"state", "frontier"}:
            return CodeChangeError(message)
        return JournalCorruption(message)

    @staticmethod
    def _prepared_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision", "state", "frontier"}:
            return CodeChangeSuperseded(
                "prepared code-change Dispatch is no longer current"
            )
        return JournalCorruption(message)

    @staticmethod
    def _observation_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision", "state", "frontier"}:
            return CodeChangeSuperseded(
                "code-change Task advanced before Runtime observation"
            )
        return JournalCorruption(message)

    @staticmethod
    def _node(plan: CodeChangePlan, stage: str) -> str:
        return f"node:{task_token(plan.task_id)}:{stage}"

    @staticmethod
    def _event_id(plan: CodeChangePlan, revision: int) -> str:
        return f"event:{task_token(plan.task_id)}:r{revision}"

    @staticmethod
    def _step(
        projection: TaskProjection,
        *,
        dispatch_id: str | None = None,
        job_id: str | None = None,
        reconciled: bool = False,
        completed: bool = False,
    ) -> CodeChangeStep:
        return CodeChangeStep(
            task_id=projection.task_id,
            revision=projection.revision,
            state=projection.state,
            frontier=(projection.ready_frontier[0] if projection.ready_frontier else None),
            dispatch_id=dispatch_id,
            job_id=job_id,
            reconciled=reconciled,
            completed=completed,
        )

def _is_source_state_mismatch(error: RuntimeToolRejected) -> bool:
    detail = error.detail
    return (
        detail.code == "REVISION_MISMATCH"
        or (
            detail.code == "INVALID_REQUEST"
            and detail.field == "expectedSourceStateDigest"
            and detail.commit_state == "not_committed"
        )
    )
