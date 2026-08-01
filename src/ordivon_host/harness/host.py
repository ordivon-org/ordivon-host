from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..domain import EventKind, TaskState
from ..effects import ArtifactRef, TaskOutcome
from ..journal import JournalCorruption
from ..kernel import HostKernel, worker_owner_id
from ..objects import ObjectCorrupt, ObjectMissing, StoredObject
from ..storage import HostStorage, TaskEventSnapshot
from .contracts import (
    CompletionVerification,
    NativeHarnessRunContract,
    TaskContract,
    ToolGrant,
)
from .models import (
    CompletionDecision,
    CompletionDecisionReceipt,
    CompletionProposal,
    HarnessAssignment,
    HarnessCapabilityManifest,
    HarnessRunReceipt,
    TaskAttemptDescriptor,
)
from .disposition import (
    CompletionRoute,
    NativeRunDisposition,
    NativeRunFacts,
    NativeRunPhase,
    ReplacementScope,
    derive_native_run_disposition,
    recovery_unknowns,
)
from .recovery import NativeRunAbandonment, NativeRunRecoveryAssessment
from .tool_semantics import (
    NativeToolCatalogSnapshot,
    NativeToolRecoveryConsequence,
    legacy_grant_recovery_consequence,
)


class HarnessLifecycleError(RuntimeError):
    pass


class HarnessSuperseded(HarnessLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedHarnessAttempt:
    descriptor: TaskAttemptDescriptor
    descriptor_object: StoredObject
    task_revision: int
    task_contract: TaskContract | None = None
    task_contract_object: StoredObject | None = None


@dataclass(frozen=True, slots=True)
class CommittedHarnessAssignment:
    attempt: TaskAttemptDescriptor
    attempt_object: StoredObject
    manifest: HarnessCapabilityManifest
    manifest_object: StoredObject
    assignment: HarnessAssignment
    assignment_object: StoredObject
    task_revision: int
    task_contract: TaskContract | None = None
    task_contract_object: StoredObject | None = None
    tool_grant: ToolGrant | None = None
    tool_grant_object: StoredObject | None = None
    tool_catalog: NativeToolCatalogSnapshot | None = None
    tool_catalog_object: StoredObject | None = None
    native_run_contract: NativeHarnessRunContract | None = None
    native_run_contract_object: StoredObject | None = None


@dataclass(frozen=True, slots=True)
class RecordedHarnessRun:
    assignment: CommittedHarnessAssignment
    receipt: HarnessRunReceipt
    receipt_object: StoredObject
    task_revision: int
    trace_object: StoredObject | None = None
    observation_objects: tuple[StoredObject, ...] = ()
    conclusion_object: StoredObject | None = None


@dataclass(frozen=True, slots=True)
class RecordedNativeRunRecovery:
    assignment: CommittedHarnessAssignment
    assessment: NativeRunRecoveryAssessment
    assessment_object: StoredObject
    task_revision: int


@dataclass(frozen=True, slots=True)
class RecordedNativeRunAbandonment:
    assignment: CommittedHarnessAssignment
    recovery: RecordedNativeRunRecovery
    abandonment: NativeRunAbandonment
    abandonment_object: StoredObject
    task_revision: int


@dataclass(frozen=True, slots=True)
class ProposedCompletion:
    proposal: CompletionProposal
    proposal_object: StoredObject
    task_revision: int


AcceptanceVerifier = Callable[[CompletionProposal], tuple[bool, str | None, JsonValue]]
ArtifactExists = Callable[[ArtifactRef], bool]
T = TypeVar("T")


class HarnessHost:
    """Host-local experimental Harness assignment and completion boundary."""

    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if owner_id is not None and (not owner_id or owner_id != owner_id.strip()):
            raise ValueError("explicit Harness Host owner identity must be trimmed")
        if lease_ttl_ms < 1:
            raise ValueError("Harness Host lease TTL must be positive")
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:harness-v0"),
            lease_ttl_ms=lease_ttl_ms,
        )

    def start_attempt(
        self,
        task_id: str,
        *,
        objective_digest: str | None = None,
        acceptance_criteria_digest: str | None = None,
        task_contract: TaskContract | None = None,
    ) -> PreparedHarnessAttempt:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError(
                "terminal Task cannot start a Harness Task Attempt"
            )
        contract_object: StoredObject | None = None
        if task_contract is not None:
            if task_contract.task_id != task_id:
                raise ValueError("Task Contract belongs to another Task")
            if objective_digest is not None or acceptance_criteria_digest is not None:
                raise ValueError(
                    "Task Contract and detached digests cannot be supplied together"
                )
            objective_digest = task_contract.objective_digest
            acceptance_criteria_digest = task_contract.acceptance_criteria_digest
            contract_object = self.storage.put_object(
                task_contract.to_dict(), kind="task-contract"
            )
        if objective_digest is None or acceptance_criteria_digest is None:
            raise ValueError(
                "Harness Task Attempt requires a Task Contract or both semantic digests"
            )
        existing = self._attempt_from_snapshot(snapshot)
        if existing is not None:
            if (
                existing.descriptor.objective_digest != objective_digest
                or existing.descriptor.acceptance_criteria_digest
                != acceptance_criteria_digest
            ):
                raise HarnessLifecycleError(
                    "Task is already bound to another Harness Task Attempt"
                )
            if task_contract is not None and existing.task_contract != task_contract:
                raise HarnessLifecycleError(
                    "Task is already bound to another Task Contract"
                )
            return existing
        descriptor = TaskAttemptDescriptor(
            task_attempt_id=f"task-attempt:{self._token(task_id)}:1",
            task_id=task_id,
            started_at_task_revision=snapshot.projection.revision,
            objective_digest=objective_digest,
            acceptance_criteria_digest=acceptance_criteria_digest,
            created_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        descriptor_object = self.storage.put_object(
            descriptor.to_dict(), kind="task-attempt-descriptor"
        )
        return PreparedHarnessAttempt(
            descriptor=descriptor,
            descriptor_object=descriptor_object,
            task_revision=snapshot.projection.revision,
            task_contract=task_contract,
            task_contract_object=contract_object,
        )

    def load_attempt(self, task_id: str) -> PreparedHarnessAttempt:
        attempt = self._attempt_from_snapshot(self.storage.read_task_event(task_id))
        if attempt is None:
            raise HarnessLifecycleError("Task has no committed Harness Task Attempt")
        return attempt

    def assign(
        self,
        prepared: PreparedHarnessAttempt,
        *,
        manifest: HarnessCapabilityManifest,
        context_object_digest: str,
        tool_catalog_digest: str,
        tool_catalog: NativeToolCatalogSnapshot | None = None,
        workspace_ref: str | None = None,
        source_ref: str | None = None,
        source_digest: str | None = None,
        prior_artifact_refs: tuple[ArtifactRef, ...] = (),
        required_capabilities: tuple[str, ...] = (),
        budget: dict[str, JsonValue] | None = None,
        deadline_ms: int | None = None,
        tool_grant: ToolGrant | None = None,
        harness_run_id: str | None = None,
    ) -> CommittedHarnessAssignment:
        context_object = self.storage.objects.inspect(context_object_digest)
        native_requested = tool_grant is not None or harness_run_id is not None
        if native_requested and tool_grant is None:
            raise ValueError("native Harness Assignment requires a Tool Grant")
        if not native_requested and tool_catalog is not None:
            raise ValueError(
                "external Harness Assignment cannot bind a native Tool catalog"
            )
        if native_requested and (
            prepared.task_contract is None or prepared.task_contract_object is None
        ):
            raise ValueError(
                "native Harness Assignment requires a durable Task Contract"
            )
        if harness_run_id is not None and not harness_run_id.startswith("harness-run:"):
            raise ValueError("Harness Run identity must start with harness-run:")
        required = set(required_capabilities)
        supported = set(manifest.supported_capabilities)
        missing = sorted(required - supported)
        if missing:
            raise ValueError(f"Harness lacks required capabilities: {missing}")
        snapshot = self.storage.read_task_event(prepared.descriptor.task_id)
        if snapshot.projection.revision != prepared.task_revision:
            existing = self._assignment_from_snapshot(snapshot)
            if existing is not None and self._assignment_request_matches(
                existing,
                prepared=prepared,
                manifest=manifest,
                context_object_digest=context_object_digest,
                tool_catalog_digest=tool_catalog_digest,
                tool_catalog=tool_catalog,
                workspace_ref=workspace_ref,
                source_ref=source_ref,
                source_digest=source_digest,
                prior_artifact_refs=prior_artifact_refs,
                required_capabilities=required_capabilities,
                budget={} if budget is None else budget,
                deadline_ms=deadline_ms,
                tool_grant=tool_grant,
                harness_run_id=harness_run_id,
            ):
                return existing
            raise HarnessSuperseded(
                f"Task revision is {snapshot.projection.revision}, expected {prepared.task_revision}"
            )
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError(
                "terminal Task cannot receive a Harness Assignment"
            )
        retained_attempt = self._attempt_from_snapshot(snapshot)
        if (
            retained_attempt is not None
            and retained_attempt.descriptor != prepared.descriptor
        ):
            raise HarnessLifecycleError(
                "Harness Task Attempt differs from current Task state"
            )
        previous = self._assignment_from_snapshot(snapshot)
        if previous is not None and previous.attempt != prepared.descriptor:
            raise HarnessLifecycleError(
                "replacement Assignment belongs to another Task Attempt"
            )
        if native_requested and tool_catalog is None:
            if previous is None or previous.tool_catalog is None:
                raise ValueError(
                    "initial native Harness Assignment requires a durable Tool catalog"
                )
            tool_catalog = previous.tool_catalog
        if tool_catalog is not None and tool_catalog.digest != tool_catalog_digest:
            raise ValueError("native Tool catalog digest differs from Assignment input")
        if previous is not None and previous.native_run_contract is not None:
            previous_run = self._run_from_snapshot(snapshot)
            previous_abandonment = self._abandonment_from_snapshot(snapshot)
            previous_recovery = self._recovery_from_snapshot(snapshot)
            if previous_abandonment is not None:
                disposition = derive_native_run_disposition(
                    NativeRunFacts(
                        NativeRunPhase.ABANDONED,
                        self._grant_recovery_consequence(previous),
                    )
                )
            elif previous_run is not None:
                disposition = self._recorded_run_disposition(previous_run)
            elif previous_recovery is not None:
                disposition = derive_native_run_disposition(
                    NativeRunFacts(
                        NativeRunPhase.RECOVERY_RECORDED,
                        self._grant_recovery_consequence(previous),
                        recovery_safe_to_abandon=(
                            previous_recovery.assessment.safe_to_abandon
                        ),
                        unresolved_unknowns=(
                            previous_recovery.assessment.unresolved_unknowns
                        ),
                    )
                )
            else:
                disposition = derive_native_run_disposition(
                    NativeRunFacts(
                        NativeRunPhase.ASSIGNED_UNRECORDED,
                        self._grant_recovery_consequence(previous),
                    )
                )
            if disposition.replacement_scope is ReplacementScope.FORBIDDEN:
                if disposition.completion_route is CompletionRoute.RECONCILE_UNKNOWN:
                    raise HarnessLifecycleError(
                        "native Harness Run retains unresolved Runtime UNKNOWN state"
                    )
                if previous_run is not None:
                    raise HarnessLifecycleError(
                        "effectful recorded native Run requires explicit continuation, "
                        "verification, or completion before replacement"
                    )
                raise HarnessLifecycleError(
                    "current native Harness Run has no recorded or abandoned terminal disposition"
                )
            if (
                disposition.replacement_scope is ReplacementScope.SAME_WORKSPACE
                and workspace_ref != previous.assignment.workspace_ref
            ):
                raise HarnessLifecycleError(
                    "recorded native Run replacement must retain the same Workspace "
                    "until a durable cleanup disposition exists"
                )
        generation = 1 if previous is None else previous.assignment.generation + 1
        manifest_object = self.storage.put_object(
            manifest.to_dict(), kind="harness-capability-manifest"
        )
        created_at_ms = self.kernel.timestamp(snapshot.projection.updated_at_ms)
        assignment = HarnessAssignment(
            assignment_id=(
                f"assignment:{self._token(prepared.descriptor.task_id)}:"
                f"attempt-1:g{generation}"
            ),
            task_id=prepared.descriptor.task_id,
            task_revision=prepared.task_revision,
            task_attempt_id=prepared.descriptor.task_attempt_id,
            generation=generation,
            target_harness_id=manifest.harness_id,
            harness_manifest_digest=manifest.digest,
            context_object_digest=context_object_digest,
            acceptance_criteria_digest=prepared.descriptor.acceptance_criteria_digest,
            tool_catalog_digest=tool_catalog_digest,
            workspace_ref=workspace_ref,
            source_ref=source_ref,
            source_digest=source_digest,
            prior_artifact_refs=prior_artifact_refs,
            required_capabilities=required_capabilities,
            budget={} if budget is None else dict(budget),
            deadline_ms=deadline_ms,
            created_at_ms=created_at_ms,
        )
        assignment_object = self.storage.put_object(
            assignment.to_dict(), kind="harness-assignment"
        )
        tool_grant_object: StoredObject | None = None
        tool_catalog_object: StoredObject | None = None
        native_contract: NativeHarnessRunContract | None = None
        native_contract_object: StoredObject | None = None
        if tool_grant is not None:
            run_id = harness_run_id or (
                f"harness-run:{self._token(prepared.descriptor.task_id)}:"
                f"attempt-1:g{generation}"
            )
            tool_grant_object = self.storage.put_object(
                tool_grant.to_dict(), kind="tool-grant"
            )
            assert tool_catalog is not None
            tool_catalog_object = self.storage.put_object(
                tool_catalog.to_dict(), kind="harness-runtime-catalog"
            )
            assert prepared.task_contract is not None
            assert prepared.task_contract_object is not None
            native_contract = NativeHarnessRunContract(
                harness_run_id=run_id,
                assignment_id=assignment.assignment_id,
                assignment_generation=assignment.generation,
                assignment_digest=assignment.digest,
                harness_manifest_digest=manifest.digest,
                task_contract_digest=prepared.task_contract.digest,
                task_contract_object_digest=prepared.task_contract_object.digest,
                context_object_digest=context_object_digest,
                tool_catalog_digest=tool_catalog_digest,
                tool_grant_digest=tool_grant.digest,
                tool_grant_object_digest=tool_grant_object.digest,
                created_at_ms=created_at_ms,
                tool_catalog_object_digest=tool_catalog_object.digest,
            )
            native_contract_object = self.storage.put_object(
                native_contract.to_dict(), kind="native-harness-run-contract"
            )
        payload: dict[str, JsonValue] = {
            **self._attempt_fields(prepared),
            "harnessManifestDigest": manifest.digest,
            "harnessManifestObjectDigest": manifest_object.digest,
            "assignmentId": assignment.assignment_id,
            "assignmentGeneration": assignment.generation,
            "assignmentDigest": assignment.digest,
            "assignmentObjectDigest": assignment_object.digest,
        }
        references: tuple[StoredObject, ...] = (
            prepared.descriptor_object,
            manifest_object,
            assignment_object,
            context_object,
        )
        if native_contract is not None:
            assert prepared.task_contract_object is not None
            assert tool_grant_object is not None
            assert tool_catalog_object is not None
            assert native_contract_object is not None
            payload.update(
                {
                    "toolGrantDigest": tool_grant.digest,
                    "toolGrantObjectDigest": tool_grant_object.digest,
                    "toolCatalogObjectDigest": tool_catalog_object.digest,
                    "nativeHarnessRunContractDigest": native_contract.digest,
                    "nativeHarnessRunContractObjectDigest": native_contract_object.digest,
                    "harnessRunId": native_contract.harness_run_id,
                }
            )
            references += (
                prepared.task_contract_object,
                tool_grant_object,
                tool_catalog_object,
                native_contract_object,
            )
        with self.kernel.locked_task(
            prepared.descriptor.task_id,
            expected_revision=prepared.task_revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="Harness Assignment",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=(
                    f"event:{self._token(prepared.descriptor.task_id)}:"
                    f"harness-assignment:g{generation}"
                ),
                kind=EventKind.HARNESS_ASSIGNMENT_COMMITTED,
                payload=payload,
                state=TaskState.WAITING,
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(references),
            ).projection
        return CommittedHarnessAssignment(
            attempt=prepared.descriptor,
            attempt_object=prepared.descriptor_object,
            manifest=manifest,
            manifest_object=manifest_object,
            assignment=assignment,
            assignment_object=assignment_object,
            task_revision=projection.revision,
            task_contract=prepared.task_contract,
            task_contract_object=prepared.task_contract_object,
            tool_grant=tool_grant,
            tool_grant_object=tool_grant_object,
            tool_catalog=tool_catalog,
            tool_catalog_object=tool_catalog_object,
            native_run_contract=native_contract,
            native_run_contract_object=native_contract_object,
        )

    def load_current_assignment(self, task_id: str) -> CommittedHarnessAssignment:
        assignment = self._assignment_from_snapshot(
            self.storage.read_task_event(task_id)
        )
        if assignment is None:
            raise HarnessLifecycleError("Task has no current Harness Assignment")
        return assignment

    def record_native_run_recovery(
        self,
        committed: CommittedHarnessAssignment,
        *,
        trigger: str,
        catalog_status: str,
        workspace_status: str,
        workspace_evidence: dict[str, JsonValue],
    ) -> RecordedNativeRunRecovery:
        native = committed.native_run_contract
        grant = committed.tool_grant
        if native is None or grant is None:
            raise ValueError(
                "native Run Recovery requires a native Assignment and Tool Grant"
            )
        snapshot = self.storage.read_task_event(committed.assignment.task_id)
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError(
                "terminal Task cannot record native Run Recovery"
            )
        if snapshot.projection.revision != committed.task_revision:
            raise HarnessSuperseded(
                f"Task revision is {snapshot.projection.revision}, expected {committed.task_revision}"
            )
        current = self._assignment_from_snapshot(snapshot)
        if current is None or current.assignment != committed.assignment:
            raise HarnessSuperseded("native Harness Assignment is no longer current")
        if self._run_from_snapshot(snapshot) is not None:
            raise HarnessLifecycleError(
                "recorded native Harness Run does not require abandonment"
            )
        if self._abandonment_from_snapshot(snapshot) is not None:
            raise HarnessLifecycleError("native Harness Run is already abandoned")
        if committed.assignment.workspace_ref is None:
            if workspace_status != "not_applicable":
                raise ValueError(
                    "Run without a Workspace requires not_applicable cleanup status"
                )
        elif workspace_status == "not_applicable":
            raise ValueError(
                "Run with a Workspace cannot use not_applicable cleanup status"
            )
        evidence_workspace = workspace_evidence.get("workspaceId")
        if workspace_status == "not_applicable":
            if (
                evidence_workspace is not None
                or workspace_evidence.get("notApplicable") is not True
            ):
                raise ValueError("not_applicable Workspace evidence differs")
        else:
            if evidence_workspace != committed.assignment.workspace_ref:
                raise ValueError("Run Recovery Workspace evidence identity differs")
            if (
                workspace_status == "closed"
                and workspace_evidence.get("closed") is not True
            ):
                raise ValueError("closed Workspace evidence differs")
            if (
                workspace_status == "already_absent"
                and workspace_evidence.get("alreadyAbsent") is not True
            ):
                raise ValueError("already_absent Workspace evidence differs")
            if workspace_status == "unknown" and not isinstance(
                workspace_evidence.get("errorType"), str
            ):
                raise ValueError("unknown Workspace evidence omitted errorType")
        previous = self._recovery_from_snapshot(snapshot)
        sequence = 1 if previous is None else previous.assessment.sequence + 1
        consequence = self._grant_recovery_consequence(committed)
        unknowns = recovery_unknowns(consequence, workspace_status=workspace_status)
        assessment = NativeRunRecoveryAssessment(
            assessment_id=(
                f"harness-run-recovery:{self._run_token(native.harness_run_id)}:r{sequence}"
            ),
            sequence=sequence,
            harness_run_id=native.harness_run_id,
            assignment_id=committed.assignment.assignment_id,
            assignment_generation=committed.assignment.generation,
            assignment_digest=committed.assignment.digest,
            trigger=trigger,
            grant_effect_class=consequence.value,
            catalog_status=catalog_status,
            workspace_status=workspace_status,
            workspace_evidence=dict(workspace_evidence),
            unresolved_unknowns=unknowns,
            created_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        disposition = derive_native_run_disposition(
            NativeRunFacts(
                NativeRunPhase.RECOVERY_RECORDED,
                consequence,
                recovery_safe_to_abandon=assessment.safe_to_abandon,
                unresolved_unknowns=assessment.unresolved_unknowns,
            )
        )
        assessment_object = self.storage.put_object(
            assessment.to_dict(), kind="native-run-recovery-assessment"
        )
        payload: dict[str, JsonValue] = {
            **self._assignment_fields(committed),
            "harnessRunRecoveryAssessmentId": assessment.assessment_id,
            "harnessRunRecoveryAssessmentDigest": assessment.digest,
            "harnessRunRecoveryAssessmentObjectDigest": assessment_object.digest,
            "harnessRunRecoverySafeToAbandon": disposition.abandonment_allowed,
        }
        with self.kernel.locked_task(
            committed.assignment.task_id,
            expected_revision=committed.task_revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="native Harness Run Recovery",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=(
                    f"event:{self._token(committed.assignment.task_id)}:"
                    f"harness-run-recovery:{self._run_token(native.harness_run_id)}:r{sequence}"
                ),
                kind=EventKind.HARNESS_RUN_RECOVERY_RECORDED,
                payload=payload,
                state=(
                    TaskState.WAITING
                    if disposition.abandonment_allowed
                    else TaskState.BLOCKED
                ),
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(
                    self._assignment_objects(committed) + (assessment_object,)
                ),
            ).projection
        return RecordedNativeRunRecovery(
            assignment=committed,
            assessment=assessment,
            assessment_object=assessment_object,
            task_revision=projection.revision,
        )

    def load_current_native_run_recovery(
        self, task_id: str
    ) -> RecordedNativeRunRecovery:
        recovery = self._recovery_from_snapshot(self.storage.read_task_event(task_id))
        if recovery is None:
            raise HarnessLifecycleError(
                "Task has no current native Run Recovery assessment"
            )
        return recovery

    def abandon_native_run(
        self,
        recovery: RecordedNativeRunRecovery,
        *,
        reason_code: str,
    ) -> RecordedNativeRunAbandonment:
        assessment = recovery.assessment
        if reason_code != assessment.trigger:
            raise ValueError("Run Abandonment reason must match the Recovery trigger")
        if not assessment.safe_to_abandon:
            raise HarnessLifecycleError(
                "native Harness Run retains UNKNOWN state and cannot be abandoned"
            )
        snapshot = self.storage.read_task_event(recovery.assignment.assignment.task_id)
        existing = self._abandonment_from_snapshot(snapshot)
        if existing is not None:
            if (
                existing.abandonment.recovery_assessment_digest == assessment.digest
                and existing.abandonment.reason_code == reason_code
            ):
                return existing
            raise HarnessLifecycleError(
                "native Harness Run is already bound to another abandonment"
            )
        if snapshot.projection.revision != recovery.task_revision:
            raise HarnessSuperseded(
                f"Task revision is {snapshot.projection.revision}, expected {recovery.task_revision}"
            )
        current_recovery = self._recovery_from_snapshot(snapshot)
        if current_recovery is None or current_recovery.assessment != assessment:
            raise HarnessSuperseded(
                "native Run Recovery assessment is no longer current"
            )
        abandonment = NativeRunAbandonment(
            abandonment_id=(
                f"harness-run-abandonment:{self._run_token(assessment.harness_run_id)}"
            ),
            harness_run_id=assessment.harness_run_id,
            assignment_id=assessment.assignment_id,
            assignment_generation=assessment.assignment_generation,
            assignment_digest=assessment.assignment_digest,
            recovery_assessment_digest=assessment.digest,
            recovery_assessment_object_digest=recovery.assessment_object.digest,
            reason_code=reason_code,
            created_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        abandonment_object = self.storage.put_object(
            abandonment.to_dict(), kind="native-run-abandonment"
        )
        payload: dict[str, JsonValue] = {
            **self._assignment_fields(recovery.assignment),
            "harnessRunRecoveryAssessmentId": assessment.assessment_id,
            "harnessRunRecoveryAssessmentDigest": assessment.digest,
            "harnessRunRecoveryAssessmentObjectDigest": recovery.assessment_object.digest,
            "harnessRunRecoverySafeToAbandon": True,
            "harnessRunAbandonmentId": abandonment.abandonment_id,
            "harnessRunAbandonmentDigest": abandonment.digest,
            "harnessRunAbandonmentObjectDigest": abandonment_object.digest,
        }
        with self.kernel.locked_task(
            recovery.assignment.assignment.task_id,
            expected_revision=recovery.task_revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="native Harness Run Abandonment",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=(
                    f"event:{self._token(recovery.assignment.assignment.task_id)}:"
                    f"harness-run-abandoned:{self._run_token(assessment.harness_run_id)}"
                ),
                kind=EventKind.HARNESS_RUN_ABANDONED,
                payload=payload,
                state=TaskState.WAITING,
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(
                    self._assignment_objects(recovery.assignment)
                    + (recovery.assessment_object, abandonment_object)
                ),
            ).projection
        return RecordedNativeRunAbandonment(
            assignment=recovery.assignment,
            recovery=recovery,
            abandonment=abandonment,
            abandonment_object=abandonment_object,
            task_revision=projection.revision,
        )

    def load_current_native_run_abandonment(
        self, task_id: str
    ) -> RecordedNativeRunAbandonment:
        abandonment = self._abandonment_from_snapshot(
            self.storage.read_task_event(task_id)
        )
        if abandonment is None:
            raise HarnessLifecycleError("Task has no current native Run Abandonment")
        return abandonment

    def record_run(
        self,
        committed: CommittedHarnessAssignment,
        receipt: HarnessRunReceipt,
        *,
        trace: dict[str, JsonValue] | None = None,
        observations: tuple[dict[str, JsonValue], ...] = (),
        conclusion: dict[str, JsonValue] | None = None,
    ) -> RecordedHarnessRun:
        self._require_run_matches_assignment(committed, receipt)
        if committed.native_run_contract is not None:
            if trace is None:
                raise ValueError("native Harness Run requires a durable Trace")
            if receipt.termination_code is None:
                raise ValueError(
                    "native Harness Run receipt requires an exact termination code"
                )
        snapshot = self.storage.read_task_event(committed.assignment.task_id)
        existing = self._run_from_snapshot(snapshot)
        if existing is not None and existing.receipt == receipt:
            return existing
        if snapshot.projection.revision != committed.task_revision:
            raise HarnessSuperseded(
                f"Task revision is {snapshot.projection.revision}, expected {committed.task_revision}"
            )
        current = self._assignment_from_snapshot(snapshot)
        if current is None or current.assignment != committed.assignment:
            raise HarnessSuperseded("Harness Assignment is no longer current")
        if self._recovery_from_snapshot(snapshot) is not None:
            raise HarnessSuperseded(
                "native Harness Run Recovery already superseded this unrecorded result"
            )
        if self._abandonment_from_snapshot(snapshot) is not None:
            raise HarnessSuperseded("native Harness Run is already abandoned")
        trace_object: StoredObject | None = None
        observation_objects: tuple[StoredObject, ...] = ()
        conclusion_object: StoredObject | None = None
        if trace is not None:
            validate_json_value(trace)
            if (
                trace.get("kind") != "ordivon.harness-trace"
                or trace.get("harnessRunId") != receipt.harness_run_id
                or canonical_digest(trace) != receipt.event_digest
            ):
                raise ValueError("Harness Trace differs from the Run receipt")
            trace_object = self.storage.put_object(trace, kind="harness-trace")
        derived_jobs: set[str] = set()
        derived_artifacts: dict[str, ArtifactRef] = {}
        retained_observations: list[StoredObject] = []
        for value in observations:
            validate_json_value(value)
            if value.get("kind") != "ordivon.tool-observation":
                raise ValueError("Run evidence contains a non-Observation object")
            job_ref = value.get("runtimeJobRef")
            if job_ref is not None:
                if not isinstance(job_ref, str) or not job_ref:
                    raise ValueError("Tool Observation Runtime Job ref is invalid")
                derived_jobs.add(job_ref)
            raw_artifacts = value.get("artifactRefs")
            if not isinstance(raw_artifacts, list) or any(
                not isinstance(item, dict) for item in raw_artifacts
            ):
                raise ValueError("Tool Observation Artifact refs are invalid")
            for item in raw_artifacts:
                ref = ArtifactRef.from_dict(item)
                previous = derived_artifacts.get(ref.ref)
                if previous is not None and previous != ref:
                    raise ValueError(
                        "one Artifact ref resolves to conflicting evidence"
                    )
                derived_artifacts[ref.ref] = ref
            retained_observations.append(
                self.storage.put_object(value, kind="harness-tool-observation")
            )
        observation_objects = tuple(retained_observations)
        if committed.native_run_contract is not None:
            if tuple(sorted(derived_jobs)) != tuple(sorted(receipt.runtime_job_refs)):
                raise ValueError(
                    "Harness Run Runtime Job refs are not derived from Observations"
                )
            if tuple(
                derived_artifacts[key] for key in sorted(derived_artifacts)
            ) != tuple(sorted(receipt.artifact_refs, key=lambda item: item.ref)):
                raise ValueError(
                    "Harness Run Artifact refs are not derived from Observations"
                )
        if conclusion is not None:
            validate_json_value(conclusion)
            status = conclusion.get("status")
            if status not in {"candidate_completed", "needs_input"}:
                raise ValueError("Harness Run conclusion status is invalid")
            if receipt.termination_code != status:
                raise ValueError("Harness Run conclusion differs from termination code")
            conclusion_value: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.agent-run-conclusion",
                "harnessRunId": receipt.harness_run_id,
                "conclusion": conclusion,
            }
            conclusion_object = self.storage.put_object(
                conclusion_value, kind="agent-run-conclusion"
            )
        elif receipt.termination_code in {"candidate_completed", "needs_input"}:
            raise ValueError("concluding Harness Run omitted its conclusion object")
        receipt_object = self.storage.put_object(
            receipt.to_dict(), kind="harness-run-receipt"
        )
        data = self._assignment_fields(committed)
        if committed.native_run_contract is not None:
            disposition = self._recorded_disposition_from_values(
                committed,
                termination_code=receipt.termination_code,
                observation_values=observations,
            )
            if (
                disposition.completion_route is CompletionRoute.RECONCILE_UNKNOWN
                and receipt.termination_code != "runtime_unknown"
            ):
                raise ValueError(
                    "native Harness Run with UNKNOWN evidence must terminate runtime_unknown"
                )
            replacement_allowed = disposition.replacement_allowed
        else:
            replacement_allowed = receipt.termination_code != "runtime_unknown"
        payload: dict[str, JsonValue] = {
            **data,
            "harnessRunId": receipt.harness_run_id,
            "harnessRunDigest": receipt.digest,
            "harnessRunObjectDigest": receipt_object.digest,
            "harnessRunTerminationCode": receipt.termination_code,
            "harnessRunReplacementAllowed": replacement_allowed,
            "harnessTraceObjectDigest": (
                None if trace_object is None else trace_object.digest
            ),
            "toolObservationObjectDigests": [
                item.digest for item in observation_objects
            ],
            "runConclusionObjectDigest": (
                None if conclusion_object is None else conclusion_object.digest
            ),
        }
        references = self._assignment_objects(committed) + (receipt_object,)
        if trace_object is not None:
            references += (trace_object,)
        references += observation_objects
        if conclusion_object is not None:
            references += (conclusion_object,)
        with self.kernel.locked_task(
            committed.assignment.task_id,
            expected_revision=committed.task_revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="Harness Run",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=f"event:{self._token(committed.assignment.task_id)}:harness-run:{self._run_token(receipt.harness_run_id)}",
                kind=EventKind.HARNESS_RUN_RECORDED,
                payload=payload,
                state=TaskState.WAITING,
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(references),
            ).projection
        return RecordedHarnessRun(
            assignment=committed,
            receipt=receipt,
            receipt_object=receipt_object,
            task_revision=projection.revision,
            trace_object=trace_object,
            observation_objects=observation_objects,
            conclusion_object=conclusion_object,
        )

    def load_current_run(self, task_id: str) -> RecordedHarnessRun:
        run = self._run_from_snapshot(self.storage.read_task_event(task_id))
        if run is None:
            raise HarnessLifecycleError("Task has no current Harness Run")
        return run

    def propose_native_completion(
        self,
        recorded: RecordedHarnessRun,
    ) -> ProposedCompletion:
        if recorded.assignment.native_run_contract is None:
            raise ValueError("native CompletionProposal requires a native Run Contract")
        disposition = self._recorded_run_disposition(recorded)
        if disposition.completion_route is not CompletionRoute.PROPOSE_CURRENT_RUN:
            raise HarnessLifecycleError(
                "current native Harness Run disposition does not admit completion"
            )
        if recorded.trace_object is None or recorded.conclusion_object is None:
            raise ValueError(
                "native CompletionProposal requires durable Trace and conclusion"
            )
        value = self.storage.objects.get(
            recorded.conclusion_object.digest, expected_kind="agent-run-conclusion"
        )
        if not isinstance(value, dict) or not isinstance(value.get("conclusion"), dict):
            raise ObjectCorrupt("native Harness conclusion object is invalid")
        conclusion = value["conclusion"]
        status = conclusion.get("status")
        summary = conclusion.get("summary")
        if status != "candidate_completed" or not isinstance(summary, str):
            raise HarnessLifecycleError(
                "only a candidate-completed native Run can propose Task completion"
            )
        evidence_refs = tuple(
            ArtifactRef(
                ref=f"host-object:{item.digest}",
                kind=item.kind,
                digest=item.digest,
            )
            for item in (recorded.trace_object, *recorded.observation_objects)
        )
        unresolved: list[str] = []
        if recorded.receipt.termination_code == "runtime_unknown":
            unresolved.append("runtime_unknown")
        for item in recorded.observation_objects:
            observation = self.storage.objects.get(
                item.digest, expected_kind="harness-tool-observation"
            )
            if isinstance(observation, dict) and observation.get("status") == "unknown":
                tool_call_id = observation.get("toolCallId")
                unresolved.append(
                    f"unknown Tool Observation: {tool_call_id}"
                    if isinstance(tool_call_id, str)
                    else "unknown Tool Observation"
                )
        return self.propose_completion(
            recorded,
            summary=summary,
            acceptance_results={
                "candidateStatus": status,
                "traceDigest": recorded.receipt.event_digest,
                "systemDerivedEvidenceCount": len(evidence_refs),
            },
            evidence_refs=evidence_refs,
            artifact_refs=recorded.receipt.artifact_refs,
            unresolved_effect_refs=(),
            unresolved_unknowns=tuple(dict.fromkeys(unresolved)),
            usage=recorded.receipt.usage,
        )

    def propose_completion(
        self,
        recorded: RecordedHarnessRun,
        *,
        summary: str,
        acceptance_results: dict[str, JsonValue],
        evidence_refs: tuple[ArtifactRef, ...] = (),
        artifact_refs: tuple[ArtifactRef, ...] = (),
        unresolved_effect_refs: tuple[str, ...] = (),
        unresolved_unknowns: tuple[str, ...] = (),
        usage: dict[str, JsonValue] | None = None,
    ) -> ProposedCompletion:
        task_id = recorded.assignment.assignment.task_id
        proposal_id = (
            f"completion-proposal:{self._run_token(recorded.receipt.harness_run_id)}"
        )
        snapshot = self.storage.read_task_event(task_id)
        existing = self._proposal_from_snapshot(snapshot)
        if (
            existing is not None
            and existing.proposal.completion_proposal_id == proposal_id
        ):
            if self._proposal_request_matches(
                existing.proposal,
                recorded=recorded,
                summary=summary,
                acceptance_results=acceptance_results,
                evidence_refs=evidence_refs,
                artifact_refs=artifact_refs,
                unresolved_effect_refs=unresolved_effect_refs,
                unresolved_unknowns=unresolved_unknowns,
                usage={} if usage is None else usage,
            ):
                return existing
            raise HarnessLifecycleError(
                "Harness Run is already bound to another proposal"
            )
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError(
                "terminal Task cannot receive CompletionProposal"
            )
        proposal = CompletionProposal(
            completion_proposal_id=proposal_id,
            task_id=task_id,
            task_revision=recorded.assignment.assignment.task_revision,
            task_attempt_id=recorded.assignment.assignment.task_attempt_id,
            assignment_id=recorded.assignment.assignment.assignment_id,
            assignment_generation=recorded.assignment.assignment.generation,
            harness_run_id=recorded.receipt.harness_run_id,
            summary=summary,
            acceptance_results=dict(acceptance_results),
            evidence_refs=evidence_refs,
            artifact_refs=artifact_refs,
            unresolved_effect_refs=unresolved_effect_refs,
            unresolved_unknowns=unresolved_unknowns,
            usage={} if usage is None else dict(usage),
            created_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        proposal_object = self.storage.put_object(
            proposal.to_dict(), kind="completion-proposal"
        )
        current_data = self._data(snapshot)
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="CompletionProposal",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=f"event:{self._token(task_id)}:completion-proposal:{self._run_token(recorded.receipt.harness_run_id)}",
                kind=EventKind.COMPLETION_PROPOSED,
                payload={
                    **self._current_state_fields(current_data),
                    "completionProposalId": proposal.completion_proposal_id,
                    "completionProposalDigest": proposal.digest,
                    "completionProposalObjectDigest": proposal_object.digest,
                },
                state=locked.projection.state,
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(
                    self._state_objects(current_data)
                    + self._assignment_objects(recorded.assignment)
                    + (recorded.receipt_object, proposal_object)
                ),
            ).projection
        return ProposedCompletion(
            proposal=proposal,
            proposal_object=proposal_object,
            task_revision=projection.revision,
        )

    def load_proposed_completion(self, task_id: str) -> ProposedCompletion:
        proposal = self._proposal_from_snapshot(self.storage.read_task_event(task_id))
        if proposal is None:
            raise HarnessLifecycleError("Task head has no CompletionProposal")
        return proposal

    def load_completion_verification(self, task_id: str) -> CompletionVerification:
        snapshot = self.storage.read_task_event(task_id)
        verification = self._completion_verification_from_snapshot(snapshot)
        if verification is None:
            raise HarnessLifecycleError("Task head has no CompletionVerification")
        return verification

    def adjudicate_completion(
        self,
        proposed: ProposedCompletion,
        *,
        artifact_exists: ArtifactExists,
        acceptance_verifier: AcceptanceVerifier,
        verification_method: str = "host-completion-adjudication-v1",
    ) -> CompletionDecisionReceipt:
        proposal = proposed.proposal
        snapshot = self.storage.read_task_event(proposal.task_id)
        existing = self._decision_from_snapshot(snapshot)
        if existing is not None:
            decision, outcome = existing
            if decision.completion_proposal_id != proposal.completion_proposal_id:
                raise HarnessLifecycleError(
                    "Task head contains another CompletionDecision"
                )
            return CompletionDecisionReceipt(
                decision=decision,
                task_revision=snapshot.projection.revision,
                task_state=snapshot.projection.state.value,
                outcome=outcome,
                outcome_digest=(
                    None if outcome is None else canonical_digest(outcome.to_dict())
                ),
            )
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError(
                "terminal Task cannot adjudicate a new CompletionProposal"
            )
        current_assignment = self._assignment_from_snapshot(snapshot)
        current_run = self._run_from_snapshot(snapshot)
        reason_code: str
        reason: str | None
        verification: JsonValue
        accepted = False
        if not self._proposal_is_current(proposal, current_assignment, current_run):
            reason_code = "stale_assignment"
            reason = "CompletionProposal does not match the current Assignment generation and Harness Run"
            verification = {
                "reasonCode": reason_code,
                "currentAssignmentId": (
                    None
                    if current_assignment is None
                    else current_assignment.assignment.assignment_id
                ),
                "currentAssignmentGeneration": (
                    None
                    if current_assignment is None
                    else current_assignment.assignment.generation
                ),
                "currentHarnessRunId": (
                    None if current_run is None else current_run.receipt.harness_run_id
                ),
            }
        elif proposal.unresolved_effect_refs:
            reason_code = "unresolved_effect"
            reason = "CompletionProposal retains unresolved Effects"
            verification = {
                "reasonCode": reason_code,
                "unresolvedEffectRefs": list(proposal.unresolved_effect_refs),
            }
        elif proposal.unresolved_unknowns:
            reason_code = "unresolved_unknown"
            reason = "CompletionProposal retains unresolved UNKNOWN state"
            verification = {
                "reasonCode": reason_code,
                "unresolvedUnknowns": list(proposal.unresolved_unknowns),
            }
        else:

            def retained_ref_exists(ref: ArtifactRef) -> bool:
                if ref.ref.startswith("host-object:"):
                    object_digest = ref.ref.removeprefix("host-object:")
                    if object_digest != ref.digest:
                        return False
                    try:
                        retained = self.storage.objects.inspect(object_digest)
                    except (ObjectMissing, ObjectCorrupt):
                        return False
                    return retained.kind == ref.kind and retained.digest == ref.digest
                return artifact_exists(ref)

            missing = [
                ref.ref
                for ref in proposal.evidence_refs + proposal.artifact_refs
                if not retained_ref_exists(ref)
            ]
            if missing:
                reason_code = "missing_artifact"
                reason = "CompletionProposal references missing evidence or Artifacts"
                verification = {
                    "reasonCode": reason_code,
                    "missingRefs": missing,
                }
            else:
                accepted, verifier_reason, verification = acceptance_verifier(proposal)
                validate_json_value(verification)
                if accepted:
                    reason_code = "accepted"
                    reason = verifier_reason
                else:
                    reason_code = "acceptance_rejected"
                    reason = (
                        verifier_reason or "acceptance verifier rejected the proposal"
                    )
        validate_json_value(verification)
        verification_result: dict[str, JsonValue] = (
            dict(verification)
            if isinstance(verification, dict)
            else {"value": verification}
        )
        verification_evidence: dict[str, ArtifactRef] = {}
        for ref in proposal.evidence_refs + proposal.artifact_refs:
            retained = verification_evidence.get(ref.ref)
            if retained is not None and retained != ref:
                raise HarnessLifecycleError(
                    "CompletionProposal evidence identity resolves to conflicting values"
                )
            verification_evidence[ref.ref] = ref
        completion_verification = CompletionVerification(
            verification_id=(
                f"completion-verification:"
                f"{proposal.completion_proposal_id.removeprefix('completion-proposal:')}"
            ),
            completion_proposal_id=proposal.completion_proposal_id,
            method=verification_method,
            accepted=accepted,
            result=verification_result,
            evidence_refs=tuple(
                verification_evidence[key] for key in sorted(verification_evidence)
            ),
            created_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        verification_object = self.storage.put_object(
            completion_verification.to_dict(), kind="completion-verification"
        )
        verification_digest = completion_verification.digest
        decision = CompletionDecision(
            completion_decision_id=(
                f"completion-decision:{proposal.completion_proposal_id.removeprefix('completion-proposal:')}"
            ),
            completion_proposal_id=proposal.completion_proposal_id,
            task_id=proposal.task_id,
            accepted=accepted,
            reason_code=reason_code,
            reason=reason,
            verification_digest=verification_digest,
            decided_at_ms=self.kernel.timestamp(snapshot.projection.updated_at_ms),
        )
        decision_object = self.storage.put_object(
            decision.to_dict(), kind="completion-decision"
        )
        outcome: TaskOutcome | None = None
        outcome_object: StoredObject | None = None
        outcome_digest: str | None = None
        if accepted:
            outcome = TaskOutcome(
                task_id=proposal.task_id,
                goal_id=snapshot.projection.goal_id,
                status="completed",
                verification_digest=verification_digest,
                artifact_refs=proposal.artifact_refs,
            )
            outcome_object = self.storage.put_object(
                outcome.to_dict(), kind="task-outcome"
            )
            outcome_digest = canonical_digest(outcome.to_dict())
        current_data = self._data(snapshot)
        references = self._state_objects(current_data) + (
            proposed.proposal_object,
            verification_object,
            decision_object,
        )
        if outcome_object is not None:
            references += (outcome_object,)
        with self.kernel.locked_task(
            proposal.task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=snapshot.projection.state,
            expected_frontier=snapshot.projection.ready_frontier,
            label="CompletionDecision",
            error_factory=self._kernel_error,
        ) as locked:
            projection = locked.commit(
                event_id=f"event:{self._token(proposal.task_id)}:completion-decision:{self._run_token(proposal.harness_run_id)}",
                kind=EventKind.COMPLETION_DECIDED,
                payload={
                    **self._current_state_fields(current_data),
                    "completionProposalId": proposal.completion_proposal_id,
                    "completionProposalDigest": proposal.digest,
                    "completionProposalObjectDigest": proposed.proposal_object.digest,
                    "completionDecisionId": decision.completion_decision_id,
                    "completionDecisionDigest": decision.digest,
                    "completionDecisionObjectDigest": decision_object.digest,
                    "completionAccepted": accepted,
                    "completionReasonCode": reason_code,
                    "verificationDigest": verification_digest,
                    "completionVerificationDigest": completion_verification.digest,
                    "completionVerificationObjectDigest": verification_object.digest,
                    "outcomeDigest": outcome_digest,
                    "outcomeObjectDigest": (
                        None if outcome_object is None else outcome_object.digest
                    ),
                },
                state=TaskState.COMPLETED if accepted else locked.projection.state,
                frontier=() if accepted else locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(references),
            ).projection
        return CompletionDecisionReceipt(
            decision=decision,
            task_revision=projection.revision,
            task_state=projection.state.value,
            outcome=outcome,
            outcome_digest=outcome_digest,
        )

    def _attempt_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> PreparedHarnessAttempt | None:
        data = self._data(snapshot)
        object_digest = data.get("taskAttemptObjectDigest")
        semantic_digest = data.get("taskAttemptDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption(
                "Harness event has incomplete Task Attempt references"
            )
        descriptor, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="task-attempt-descriptor",
            decoder=TaskAttemptDescriptor.from_dict,
            label="TaskAttemptDescriptor",
        )
        if descriptor.task_id != snapshot.projection.task_id:
            raise JournalCorruption(
                "Task Attempt identity differs from Task projection"
            )
        contract_object_digest = data.get("taskContractObjectDigest")
        contract_digest = data.get("taskContractDigest")
        contract: TaskContract | None = None
        contract_object: StoredObject | None = None
        if contract_object_digest is not None or contract_digest is not None:
            if not isinstance(contract_object_digest, str) or not isinstance(
                contract_digest, str
            ):
                raise JournalCorruption(
                    "Harness event has incomplete Task Contract references"
                )
            contract, contract_object = self._load_object(
                contract_object_digest,
                contract_digest,
                kind="task-contract",
                decoder=TaskContract.from_dict,
                label="TaskContract",
            )
            if (
                contract.task_id != descriptor.task_id
                or contract.objective_digest != descriptor.objective_digest
                or contract.acceptance_criteria_digest
                != descriptor.acceptance_criteria_digest
            ):
                raise JournalCorruption("Task Contract differs from Task Attempt")
        return PreparedHarnessAttempt(
            descriptor=descriptor,
            descriptor_object=stored,
            task_revision=snapshot.projection.revision,
            task_contract=contract,
            task_contract_object=contract_object,
        )

    def _assignment_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> CommittedHarnessAssignment | None:
        data = self._data(snapshot)
        object_digest = data.get("assignmentObjectDigest")
        semantic_digest = data.get("assignmentDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption(
                "Harness event has incomplete Assignment references"
            )
        attempt = self._attempt_from_snapshot(snapshot)
        if attempt is None:
            raise JournalCorruption("Harness Assignment has no Task Attempt")
        manifest_object_digest = data.get("harnessManifestObjectDigest")
        manifest_digest = data.get("harnessManifestDigest")
        if not isinstance(manifest_object_digest, str) or not isinstance(
            manifest_digest, str
        ):
            raise JournalCorruption(
                "Harness Assignment has incomplete manifest references"
            )
        manifest, manifest_object = self._load_object(
            manifest_object_digest,
            manifest_digest,
            kind="harness-capability-manifest",
            decoder=HarnessCapabilityManifest.from_dict,
            label="HarnessCapabilityManifest",
        )
        assignment, assignment_object = self._load_object(
            object_digest,
            semantic_digest,
            kind="harness-assignment",
            decoder=HarnessAssignment.from_dict,
            label="HarnessAssignment",
        )
        if (
            assignment.task_id != snapshot.projection.task_id
            or assignment.task_attempt_id != attempt.descriptor.task_attempt_id
            or assignment.harness_manifest_digest != manifest.digest
            or assignment.target_harness_id != manifest.harness_id
        ):
            raise JournalCorruption("Harness Assignment identities differ")
        tool_grant: ToolGrant | None = None
        tool_grant_object: StoredObject | None = None
        tool_catalog: NativeToolCatalogSnapshot | None = None
        tool_catalog_object: StoredObject | None = None
        native_contract: NativeHarnessRunContract | None = None
        native_contract_object: StoredObject | None = None
        grant_object_digest = data.get("toolGrantObjectDigest")
        grant_digest = data.get("toolGrantDigest")
        native_object_digest = data.get("nativeHarnessRunContractObjectDigest")
        native_digest = data.get("nativeHarnessRunContractDigest")
        native_fields = (
            grant_object_digest,
            grant_digest,
            native_object_digest,
            native_digest,
        )
        if any(value is not None for value in native_fields):
            if not all(isinstance(value, str) for value in native_fields):
                raise JournalCorruption(
                    "Harness Assignment has incomplete native Run references"
                )
            if attempt.task_contract is None or attempt.task_contract_object is None:
                raise JournalCorruption(
                    "native Harness Assignment has no Task Contract"
                )
            assert isinstance(grant_object_digest, str)
            assert isinstance(grant_digest, str)
            assert isinstance(native_object_digest, str)
            assert isinstance(native_digest, str)
            tool_grant, tool_grant_object = self._load_object(
                grant_object_digest,
                grant_digest,
                kind="tool-grant",
                decoder=ToolGrant.from_dict,
                label="ToolGrant",
            )
            native_contract, native_contract_object = self._load_object(
                native_object_digest,
                native_digest,
                kind="native-harness-run-contract",
                decoder=NativeHarnessRunContract.from_dict,
                label="NativeHarnessRunContract",
            )
            catalog_object_digest = data.get("toolCatalogObjectDigest")
            if native_contract.tool_catalog_object_digest is None:
                if catalog_object_digest is not None:
                    raise JournalCorruption(
                        "v1 native Harness Run Contract cannot reference a Tool catalog object"
                    )
                try:
                    legacy_grant_recovery_consequence(tool_grant.allowed_tools)
                except ValueError as error:
                    raise JournalCorruption(
                        "legacy native Tool Grant is invalid"
                    ) from error
            else:
                if (
                    not isinstance(catalog_object_digest, str)
                    or catalog_object_digest
                    != native_contract.tool_catalog_object_digest
                ):
                    raise JournalCorruption(
                        "native Harness Assignment Tool catalog reference differs"
                    )
                tool_catalog, tool_catalog_object = self._load_object(
                    catalog_object_digest,
                    assignment.tool_catalog_digest,
                    kind="harness-runtime-catalog",
                    decoder=NativeToolCatalogSnapshot.from_dict,
                    label="NativeToolCatalogSnapshot",
                )
                try:
                    tool_catalog.aggregate_recovery_consequence(
                        tool_grant.allowed_tools
                    )
                except KeyError as error:
                    raise JournalCorruption(
                        "native Tool Grant is not covered by its retained catalog"
                    ) from error
            if (
                native_contract.assignment_id != assignment.assignment_id
                or native_contract.assignment_generation != assignment.generation
                or native_contract.assignment_digest != assignment.digest
                or native_contract.harness_manifest_digest != manifest.digest
                or native_contract.task_contract_digest != attempt.task_contract.digest
                or native_contract.task_contract_object_digest
                != attempt.task_contract_object.digest
                or native_contract.context_object_digest
                != assignment.context_object_digest
                or native_contract.tool_catalog_digest != assignment.tool_catalog_digest
                or native_contract.tool_grant_digest != tool_grant.digest
                or native_contract.tool_grant_object_digest != tool_grant_object.digest
                or data.get("harnessRunId") != native_contract.harness_run_id
            ):
                raise JournalCorruption("native Harness Run Contract identities differ")
        elif data.get("toolCatalogObjectDigest") is not None:
            raise JournalCorruption(
                "external Harness Assignment cannot retain a native Tool catalog"
            )
        return CommittedHarnessAssignment(
            attempt=attempt.descriptor,
            attempt_object=attempt.descriptor_object,
            manifest=manifest,
            manifest_object=manifest_object,
            assignment=assignment,
            assignment_object=assignment_object,
            task_revision=snapshot.projection.revision,
            task_contract=attempt.task_contract,
            task_contract_object=attempt.task_contract_object,
            tool_grant=tool_grant,
            tool_grant_object=tool_grant_object,
            tool_catalog=tool_catalog,
            tool_catalog_object=tool_catalog_object,
            native_run_contract=native_contract,
            native_run_contract_object=native_contract_object,
        )

    def _recovery_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> RecordedNativeRunRecovery | None:
        data = self._data(snapshot)
        object_digest = data.get("harnessRunRecoveryAssessmentObjectDigest")
        semantic_digest = data.get("harnessRunRecoveryAssessmentDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption("native Run Recovery references are incomplete")
        assignment = self._assignment_from_snapshot(snapshot)
        if assignment is None or assignment.native_run_contract is None:
            raise JournalCorruption("native Run Recovery has no native Assignment")
        assessment, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="native-run-recovery-assessment",
            decoder=NativeRunRecoveryAssessment.from_dict,
            label="NativeRunRecoveryAssessment",
        )
        native = assignment.native_run_contract
        if (
            assessment.harness_run_id != native.harness_run_id
            or assessment.assignment_id != assignment.assignment.assignment_id
            or assessment.assignment_generation != assignment.assignment.generation
            or assessment.assignment_digest != assignment.assignment.digest
            or data.get("harnessRunRecoveryAssessmentId") != assessment.assessment_id
            or data.get("harnessRunRecoverySafeToAbandon")
            is not assessment.safe_to_abandon
        ):
            raise JournalCorruption("native Run Recovery identities differ")
        return RecordedNativeRunRecovery(
            assignment=assignment,
            assessment=assessment,
            assessment_object=stored,
            task_revision=snapshot.projection.revision,
        )

    def _abandonment_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> RecordedNativeRunAbandonment | None:
        data = self._data(snapshot)
        object_digest = data.get("harnessRunAbandonmentObjectDigest")
        semantic_digest = data.get("harnessRunAbandonmentDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption("native Run Abandonment references are incomplete")
        recovery = self._recovery_from_snapshot(snapshot)
        if recovery is None or not recovery.assessment.safe_to_abandon:
            raise JournalCorruption(
                "native Run Abandonment has no safe Recovery assessment"
            )
        abandonment, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="native-run-abandonment",
            decoder=NativeRunAbandonment.from_dict,
            label="NativeRunAbandonment",
        )
        assignment = recovery.assignment.assignment
        if (
            abandonment.harness_run_id != recovery.assessment.harness_run_id
            or abandonment.assignment_id != assignment.assignment_id
            or abandonment.assignment_generation != assignment.generation
            or abandonment.assignment_digest != assignment.digest
            or abandonment.recovery_assessment_digest != recovery.assessment.digest
            or abandonment.recovery_assessment_object_digest
            != recovery.assessment_object.digest
            or abandonment.reason_code != recovery.assessment.trigger
            or data.get("harnessRunAbandonmentId") != abandonment.abandonment_id
        ):
            raise JournalCorruption("native Run Abandonment identities differ")
        return RecordedNativeRunAbandonment(
            assignment=recovery.assignment,
            recovery=recovery,
            abandonment=abandonment,
            abandonment_object=stored,
            task_revision=snapshot.projection.revision,
        )

    def _run_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> RecordedHarnessRun | None:
        data = self._data(snapshot)
        object_digest = data.get("harnessRunObjectDigest")
        semantic_digest = data.get("harnessRunDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption("Harness event has incomplete Run references")
        assignment = self._assignment_from_snapshot(snapshot)
        if assignment is None:
            raise JournalCorruption("Harness Run has no Assignment")
        receipt, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="harness-run-receipt",
            decoder=HarnessRunReceipt.from_dict,
            label="HarnessRunReceipt",
        )
        self._require_run_matches_assignment(assignment, receipt)
        projected_termination = data.get("harnessRunTerminationCode")
        if (
            projected_termination is not None
            and projected_termination != receipt.termination_code
        ):
            raise JournalCorruption("Harness Run termination projection differs")
        projected_replacement = data.get("harnessRunReplacementAllowed")
        if (
            projected_replacement is not None
            and type(projected_replacement) is not bool
        ):
            raise JournalCorruption("Harness Run replacement projection is invalid")
        trace_object: StoredObject | None = None
        trace_digest = data.get("harnessTraceObjectDigest")
        if trace_digest is not None:
            if not isinstance(trace_digest, str):
                raise JournalCorruption("Harness Trace object digest is invalid")
            trace_object = self.storage.objects.inspect(trace_digest)
            if trace_object.kind != "harness-trace":
                raise JournalCorruption("Harness Trace object kind differs")
            trace = self.storage.objects.get(
                trace_digest, expected_kind="harness-trace"
            )
            if (
                not isinstance(trace, dict)
                or trace.get("harnessRunId") != receipt.harness_run_id
                or canonical_digest(trace) != receipt.event_digest
            ):
                raise JournalCorruption("Harness Trace differs from Run receipt")
        elif assignment.native_run_contract is not None:
            raise JournalCorruption("native Harness Run has no durable Trace")
        raw_observation_digests = data.get("toolObservationObjectDigests", [])
        if not isinstance(raw_observation_digests, list) or any(
            not isinstance(item, str) for item in raw_observation_digests
        ):
            raise JournalCorruption("Harness Tool Observation refs are invalid")
        observation_objects: list[StoredObject] = []
        derived_jobs: set[str] = set()
        derived_artifacts: dict[str, ArtifactRef] = {}
        for digest in raw_observation_digests:
            retained = self.storage.objects.inspect(digest)
            if retained.kind != "harness-tool-observation":
                raise JournalCorruption("Harness Tool Observation object kind differs")
            value = self.storage.objects.get(
                digest, expected_kind="harness-tool-observation"
            )
            if (
                not isinstance(value, dict)
                or value.get("kind") != "ordivon.tool-observation"
            ):
                raise ObjectCorrupt("Harness Tool Observation is invalid")
            job_ref = value.get("runtimeJobRef")
            if isinstance(job_ref, str):
                derived_jobs.add(job_ref)
            raw_artifacts = value.get("artifactRefs")
            if not isinstance(raw_artifacts, list) or any(
                not isinstance(item, dict) for item in raw_artifacts
            ):
                raise ObjectCorrupt(
                    "Harness Tool Observation Artifact refs are invalid"
                )
            for item in raw_artifacts:
                ref = ArtifactRef.from_dict(item)
                previous = derived_artifacts.get(ref.ref)
                if previous is not None and previous != ref:
                    raise JournalCorruption("Harness Artifact provenance conflicts")
                derived_artifacts[ref.ref] = ref
            observation_objects.append(retained)
        if assignment.native_run_contract is not None:
            if receipt.termination_code is None:
                raise JournalCorruption(
                    "native Harness Run receipt omitted its exact termination code"
                )
            observation_values = tuple(
                self.storage.objects.get(
                    item.digest, expected_kind="harness-tool-observation"
                )
                for item in observation_objects
            )
            if any(not isinstance(item, dict) for item in observation_values):
                raise ObjectCorrupt("Harness Tool Observation must be an object")
            disposition = self._recorded_disposition_from_values(
                assignment,
                termination_code=receipt.termination_code,
                observation_values=tuple(
                    dict(item) for item in observation_values if isinstance(item, dict)
                ),
            )
            if (
                disposition.completion_route is CompletionRoute.RECONCILE_UNKNOWN
                and receipt.termination_code != "runtime_unknown"
            ):
                raise JournalCorruption(
                    "native Harness Run UNKNOWN evidence differs from termination"
                )
            expected_replacement = disposition.replacement_allowed
            if (
                projected_replacement is not None
                and projected_replacement is not expected_replacement
            ):
                raise JournalCorruption("Harness Run replacement projection differs")
            if tuple(sorted(derived_jobs)) != tuple(sorted(receipt.runtime_job_refs)):
                raise JournalCorruption("Harness Run Job provenance differs")
            if tuple(
                derived_artifacts[key] for key in sorted(derived_artifacts)
            ) != tuple(sorted(receipt.artifact_refs, key=lambda item: item.ref)):
                raise JournalCorruption("Harness Run Artifact provenance differs")
        conclusion_object: StoredObject | None = None
        conclusion_digest = data.get("runConclusionObjectDigest")
        if conclusion_digest is not None:
            if not isinstance(conclusion_digest, str):
                raise JournalCorruption("Harness conclusion object digest is invalid")
            conclusion_object = self.storage.objects.inspect(conclusion_digest)
            if conclusion_object.kind != "agent-run-conclusion":
                raise JournalCorruption("Harness conclusion object kind differs")
            conclusion = self.storage.objects.get(
                conclusion_digest, expected_kind="agent-run-conclusion"
            )
            if (
                not isinstance(conclusion, dict)
                or conclusion.get("kind") != "ordivon.agent-run-conclusion"
                or conclusion.get("harnessRunId") != receipt.harness_run_id
                or not isinstance(conclusion.get("conclusion"), dict)
                or conclusion["conclusion"].get("status") != receipt.termination_code
            ):
                raise JournalCorruption("Harness conclusion differs from Run receipt")
        elif receipt.termination_code in {"candidate_completed", "needs_input"}:
            raise JournalCorruption("concluding Harness Run has no conclusion object")
        return RecordedHarnessRun(
            assignment=assignment,
            receipt=receipt,
            receipt_object=stored,
            task_revision=snapshot.projection.revision,
            trace_object=trace_object,
            observation_objects=tuple(observation_objects),
            conclusion_object=conclusion_object,
        )

    def _proposal_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> ProposedCompletion | None:
        data = self._data(snapshot)
        object_digest = data.get("completionProposalObjectDigest")
        semantic_digest = data.get("completionProposalDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption(
                "Harness event has incomplete CompletionProposal references"
            )
        proposal, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="completion-proposal",
            decoder=CompletionProposal.from_dict,
            label="CompletionProposal",
        )
        if proposal.task_id != snapshot.projection.task_id:
            raise JournalCorruption("CompletionProposal Task identity differs")
        return ProposedCompletion(
            proposal=proposal,
            proposal_object=stored,
            task_revision=snapshot.projection.revision,
        )

    def _completion_verification_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> CompletionVerification | None:
        data = self._data(snapshot)
        object_digest = data.get("completionVerificationObjectDigest")
        semantic_digest = data.get("completionVerificationDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption("CompletionVerification references are incomplete")
        verification, _ = self._load_object(
            object_digest,
            semantic_digest,
            kind="completion-verification",
            decoder=CompletionVerification.from_dict,
            label="CompletionVerification",
        )
        if verification.completion_proposal_id != data.get("completionProposalId"):
            raise JournalCorruption("CompletionVerification targets another proposal")
        return verification

    def _decision_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> tuple[CompletionDecision, TaskOutcome | None] | None:
        data = self._data(snapshot)
        object_digest = data.get("completionDecisionObjectDigest")
        semantic_digest = data.get("completionDecisionDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption(
                "Harness event has incomplete CompletionDecision references"
            )
        decision, _ = self._load_object(
            object_digest,
            semantic_digest,
            kind="completion-decision",
            decoder=CompletionDecision.from_dict,
            label="CompletionDecision",
        )
        completion_verification = self._completion_verification_from_snapshot(snapshot)
        if completion_verification is not None and (
            completion_verification.completion_proposal_id
            != decision.completion_proposal_id
            or completion_verification.accepted != decision.accepted
            or completion_verification.digest != decision.verification_digest
        ):
            raise JournalCorruption("CompletionDecision verification differs")
        outcome_object_digest = data.get("outcomeObjectDigest")
        outcome_digest = data.get("outcomeDigest")
        if outcome_object_digest is None and outcome_digest is None:
            return decision, None
        if not isinstance(outcome_object_digest, str) or not isinstance(
            outcome_digest, str
        ):
            raise JournalCorruption(
                "CompletionDecision has incomplete TaskOutcome references"
            )
        outcome, _ = self._load_object(
            outcome_object_digest,
            outcome_digest,
            kind="task-outcome",
            decoder=TaskOutcome.from_dict,
            label="TaskOutcome",
        )
        return decision, outcome

    def _load_object(
        self,
        object_digest: str,
        semantic_digest: str,
        *,
        kind: str,
        decoder: Callable[[dict[str, object]], T],
        label: str,
    ) -> tuple[T, StoredObject]:
        stored = self.storage.objects.inspect(object_digest)
        if stored.kind != kind:
            raise JournalCorruption(f"{label} object kind differs")
        value = self.storage.objects.get(object_digest, expected_kind=kind)
        if not isinstance(value, dict):
            raise ObjectCorrupt(f"{label} object must be an object")
        try:
            decoded = decoder(value)
        except (TypeError, ValueError) as error:
            raise ObjectCorrupt(f"{label} object is invalid") from error
        to_dict = getattr(decoded, "to_dict", None)
        if not callable(to_dict) or canonical_digest(to_dict()) != semantic_digest:
            raise JournalCorruption(f"{label} semantic digest differs")
        return decoded, stored

    @staticmethod
    def _grant_recovery_consequence(
        committed: CommittedHarnessAssignment,
    ) -> NativeToolRecoveryConsequence:
        grant = committed.tool_grant
        if grant is None:
            return NativeToolRecoveryConsequence.OBSERVATION_ONLY
        if committed.tool_catalog is not None:
            return committed.tool_catalog.aggregate_recovery_consequence(
                grant.allowed_tools
            )
        return legacy_grant_recovery_consequence(grant.allowed_tools)

    def _recorded_disposition_from_values(
        self,
        committed: CommittedHarnessAssignment,
        *,
        termination_code: str | None,
        observation_values: tuple[dict[str, JsonValue], ...],
    ) -> NativeRunDisposition:
        if termination_code is None:
            raise ValueError("native recorded Run requires a termination code")
        has_unknown = any(
            value.get("status") == "unknown" for value in observation_values
        )
        return derive_native_run_disposition(
            NativeRunFacts(
                NativeRunPhase.RUN_RECORDED,
                self._grant_recovery_consequence(committed),
                termination_code=termination_code,
                has_tool_observations=bool(observation_values),
                has_unknown_observation=has_unknown,
                has_candidate_conclusion=(termination_code == "candidate_completed"),
            )
        )

    def _recorded_run_disposition(
        self, recorded: RecordedHarnessRun
    ) -> NativeRunDisposition:
        values: list[dict[str, JsonValue]] = []
        for retained in recorded.observation_objects:
            value = self.storage.objects.get(
                retained.digest, expected_kind="harness-tool-observation"
            )
            if not isinstance(value, dict):
                raise ObjectCorrupt("Harness Tool Observation must be an object")
            values.append(dict(value))
        return self._recorded_disposition_from_values(
            recorded.assignment,
            termination_code=recorded.receipt.termination_code,
            observation_values=tuple(values),
        )

    @staticmethod
    def _proposal_is_current(
        proposal: CompletionProposal,
        assignment: CommittedHarnessAssignment | None,
        run: RecordedHarnessRun | None,
    ) -> bool:
        return bool(
            assignment is not None
            and run is not None
            and proposal.task_revision == assignment.assignment.task_revision
            and proposal.task_attempt_id == assignment.assignment.task_attempt_id
            and proposal.assignment_id == assignment.assignment.assignment_id
            and proposal.assignment_generation == assignment.assignment.generation
            and proposal.harness_run_id == run.receipt.harness_run_id
        )

    @staticmethod
    def _require_run_matches_assignment(
        assignment: CommittedHarnessAssignment,
        receipt: HarnessRunReceipt,
    ) -> None:
        current = assignment.assignment
        if (
            receipt.assignment_id != current.assignment_id
            or receipt.assignment_generation != current.generation
            or receipt.harness_id != current.target_harness_id
            or receipt.manifest_digest != current.harness_manifest_digest
            or receipt.context_digest != current.context_object_digest
            or receipt.tool_catalog_digest != current.tool_catalog_digest
        ):
            raise ValueError("Harness Run receipt differs from Assignment")
        if (
            assignment.native_run_contract is not None
            and receipt.harness_run_id != assignment.native_run_contract.harness_run_id
        ):
            raise ValueError("Harness Run receipt differs from the native Run Contract")

    @staticmethod
    def _assignment_request_matches(
        committed: CommittedHarnessAssignment,
        *,
        prepared: PreparedHarnessAttempt,
        manifest: HarnessCapabilityManifest,
        context_object_digest: str,
        tool_catalog_digest: str,
        tool_catalog: NativeToolCatalogSnapshot | None,
        workspace_ref: str | None,
        source_ref: str | None,
        source_digest: str | None,
        prior_artifact_refs: tuple[ArtifactRef, ...],
        required_capabilities: tuple[str, ...],
        budget: dict[str, JsonValue],
        deadline_ms: int | None,
        tool_grant: ToolGrant | None,
        harness_run_id: str | None,
    ) -> bool:
        assignment = committed.assignment
        base_matches = (
            assignment.task_revision == prepared.task_revision
            and assignment.task_attempt_id == prepared.descriptor.task_attempt_id
            and assignment.target_harness_id == manifest.harness_id
            and assignment.harness_manifest_digest == manifest.digest
            and assignment.context_object_digest == context_object_digest
            and assignment.acceptance_criteria_digest
            == prepared.descriptor.acceptance_criteria_digest
            and assignment.tool_catalog_digest == tool_catalog_digest
            and assignment.workspace_ref == workspace_ref
            and assignment.source_ref == source_ref
            and assignment.source_digest == source_digest
            and assignment.prior_artifact_refs == prior_artifact_refs
            and assignment.required_capabilities == required_capabilities
            and assignment.budget == budget
            and assignment.deadline_ms == deadline_ms
        )
        if not base_matches:
            return False
        if tool_grant is None:
            return committed.tool_grant is None and harness_run_id is None
        return bool(
            committed.task_contract == prepared.task_contract
            and committed.tool_grant == tool_grant
            and (tool_catalog is None or committed.tool_catalog == tool_catalog)
            and committed.native_run_contract is not None
            and (
                harness_run_id is None
                or committed.native_run_contract.harness_run_id == harness_run_id
            )
        )

    @staticmethod
    def _proposal_request_matches(
        proposal: CompletionProposal,
        *,
        recorded: RecordedHarnessRun,
        summary: str,
        acceptance_results: dict[str, JsonValue],
        evidence_refs: tuple[ArtifactRef, ...],
        artifact_refs: tuple[ArtifactRef, ...],
        unresolved_effect_refs: tuple[str, ...],
        unresolved_unknowns: tuple[str, ...],
        usage: dict[str, JsonValue],
    ) -> bool:
        assignment = recorded.assignment.assignment
        return (
            proposal.task_revision == assignment.task_revision
            and proposal.task_attempt_id == assignment.task_attempt_id
            and proposal.assignment_id == assignment.assignment_id
            and proposal.assignment_generation == assignment.generation
            and proposal.harness_run_id == recorded.receipt.harness_run_id
            and proposal.summary == summary
            and proposal.acceptance_results == acceptance_results
            and proposal.evidence_refs == evidence_refs
            and proposal.artifact_refs == artifact_refs
            and proposal.unresolved_effect_refs == unresolved_effect_refs
            and proposal.unresolved_unknowns == unresolved_unknowns
            and proposal.usage == usage
        )

    @staticmethod
    def _attempt_fields(prepared: PreparedHarnessAttempt) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "taskAttemptId": prepared.descriptor.task_attempt_id,
            "taskAttemptDigest": prepared.descriptor.digest,
            "taskAttemptObjectDigest": prepared.descriptor_object.digest,
        }
        if prepared.task_contract is not None:
            if prepared.task_contract_object is None:
                raise ValueError("Task Contract object is missing")
            value.update(
                {
                    "taskContractId": prepared.task_contract.contract_id,
                    "taskContractDigest": prepared.task_contract.digest,
                    "taskContractObjectDigest": prepared.task_contract_object.digest,
                }
            )
        return value

    @classmethod
    def _assignment_fields(
        cls, committed: CommittedHarnessAssignment
    ) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            **cls._attempt_fields(
                PreparedHarnessAttempt(
                    descriptor=committed.attempt,
                    descriptor_object=committed.attempt_object,
                    task_revision=committed.assignment.task_revision,
                    task_contract=committed.task_contract,
                    task_contract_object=committed.task_contract_object,
                )
            ),
            "harnessManifestDigest": committed.manifest.digest,
            "harnessManifestObjectDigest": committed.manifest_object.digest,
            "assignmentId": committed.assignment.assignment_id,
            "assignmentGeneration": committed.assignment.generation,
            "assignmentDigest": committed.assignment.digest,
            "assignmentObjectDigest": committed.assignment_object.digest,
        }
        if committed.native_run_contract is not None:
            if (
                committed.tool_grant is None
                or committed.tool_grant_object is None
                or committed.native_run_contract_object is None
            ):
                raise ValueError("native Harness Assignment objects are incomplete")
            value.update(
                {
                    "toolGrantDigest": committed.tool_grant.digest,
                    "toolGrantObjectDigest": committed.tool_grant_object.digest,
                    **(
                        {}
                        if committed.tool_catalog_object is None
                        else {
                            "toolCatalogObjectDigest": committed.tool_catalog_object.digest
                        }
                    ),
                    "nativeHarnessRunContractDigest": committed.native_run_contract.digest,
                    "nativeHarnessRunContractObjectDigest": (
                        committed.native_run_contract_object.digest
                    ),
                    "harnessRunId": committed.native_run_contract.harness_run_id,
                }
            )
        return value

    @classmethod
    def _current_state_fields(cls, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        fields = (
            "taskAttemptId",
            "taskAttemptDigest",
            "taskAttemptObjectDigest",
            "taskContractId",
            "taskContractDigest",
            "taskContractObjectDigest",
            "harnessManifestDigest",
            "harnessManifestObjectDigest",
            "assignmentId",
            "assignmentGeneration",
            "assignmentDigest",
            "assignmentObjectDigest",
            "toolGrantDigest",
            "toolGrantObjectDigest",
            "toolCatalogObjectDigest",
            "nativeHarnessRunContractDigest",
            "nativeHarnessRunContractObjectDigest",
            "harnessRunId",
            "harnessRunDigest",
            "harnessRunObjectDigest",
            "harnessRunTerminationCode",
            "harnessRunReplacementAllowed",
            "harnessTraceObjectDigest",
            "toolObservationObjectDigests",
            "runConclusionObjectDigest",
            "harnessRunRecoveryAssessmentId",
            "harnessRunRecoveryAssessmentDigest",
            "harnessRunRecoveryAssessmentObjectDigest",
            "harnessRunRecoverySafeToAbandon",
            "harnessRunAbandonmentId",
            "harnessRunAbandonmentDigest",
            "harnessRunAbandonmentObjectDigest",
            "completionVerificationDigest",
            "completionVerificationObjectDigest",
        )
        return {field: data[field] for field in fields if field in data}

    @staticmethod
    def _assignment_objects(
        committed: CommittedHarnessAssignment,
    ) -> tuple[StoredObject, ...]:
        values: tuple[StoredObject, ...] = (
            committed.attempt_object,
            committed.manifest_object,
            committed.assignment_object,
        )
        for item in (
            committed.task_contract_object,
            committed.tool_grant_object,
            committed.tool_catalog_object,
            committed.native_run_contract_object,
        ):
            if item is not None:
                values += (item,)
        return HarnessHost._dedupe_objects(values)

    def _state_objects(self, data: dict[str, JsonValue]) -> tuple[StoredObject, ...]:
        values: list[StoredObject] = []
        for field in (
            "taskAttemptObjectDigest",
            "taskContractObjectDigest",
            "harnessManifestObjectDigest",
            "assignmentObjectDigest",
            "toolGrantObjectDigest",
            "toolCatalogObjectDigest",
            "nativeHarnessRunContractObjectDigest",
            "harnessRunObjectDigest",
            "harnessTraceObjectDigest",
            "runConclusionObjectDigest",
            "harnessRunRecoveryAssessmentObjectDigest",
            "harnessRunAbandonmentObjectDigest",
            "completionVerificationObjectDigest",
        ):
            digest = data.get(field)
            if isinstance(digest, str):
                values.append(self.storage.objects.inspect(digest))
        observations = data.get("toolObservationObjectDigests")
        if observations is not None:
            if not isinstance(observations, list) or any(
                not isinstance(item, str) for item in observations
            ):
                raise JournalCorruption("Tool Observation object digests are invalid")
            values.extend(self.storage.objects.inspect(item) for item in observations)
        return self._dedupe_objects(tuple(values))

    @staticmethod
    def _dedupe_objects(values: tuple[StoredObject, ...]) -> tuple[StoredObject, ...]:
        retained: dict[str, StoredObject] = {}
        for value in values:
            retained[value.digest] = value
        return tuple(retained.values())

    @staticmethod
    def _data(snapshot: TaskEventSnapshot) -> dict[str, JsonValue]:
        if not isinstance(snapshot.data, dict):
            raise JournalCorruption("Harness event data must be an object")
        return dict(snapshot.data)

    @staticmethod
    def _token(task_id: str) -> str:
        return task_id.removeprefix("task:")

    @staticmethod
    def _run_token(harness_run_id: str) -> str:
        return harness_run_id.removeprefix("harness-run:")

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision", "state", "frontier"}:
            return HarnessSuperseded(message)
        return JournalCorruption(message)
