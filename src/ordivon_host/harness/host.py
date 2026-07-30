from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..domain import EventKind, TaskState
from ..effects import ArtifactRef, TaskOutcome
from ..journal import JournalCorruption
from ..kernel import HostKernel, worker_owner_id
from ..objects import ObjectCorrupt, StoredObject
from ..storage import HostStorage, TaskEventSnapshot
from .models import (
    CompletionDecision,
    CompletionDecisionReceipt,
    CompletionProposal,
    HarnessAssignment,
    HarnessCapabilityManifest,
    HarnessRunReceipt,
    TaskAttemptDescriptor,
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


@dataclass(frozen=True, slots=True)
class CommittedHarnessAssignment:
    attempt: TaskAttemptDescriptor
    attempt_object: StoredObject
    manifest: HarnessCapabilityManifest
    manifest_object: StoredObject
    assignment: HarnessAssignment
    assignment_object: StoredObject
    task_revision: int


@dataclass(frozen=True, slots=True)
class RecordedHarnessRun:
    assignment: CommittedHarnessAssignment
    receipt: HarnessRunReceipt
    receipt_object: StoredObject
    task_revision: int


@dataclass(frozen=True, slots=True)
class ProposedCompletion:
    proposal: CompletionProposal
    proposal_object: StoredObject
    task_revision: int


AcceptanceVerifier = Callable[
    [CompletionProposal], tuple[bool, str | None, JsonValue]
]
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
        objective_digest: str,
        acceptance_criteria_digest: str,
    ) -> PreparedHarnessAttempt:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError("terminal Task cannot start a Harness Task Attempt")
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
        workspace_ref: str | None = None,
        source_ref: str | None = None,
        source_digest: str | None = None,
        prior_artifact_refs: tuple[ArtifactRef, ...] = (),
        required_capabilities: tuple[str, ...] = (),
        budget: dict[str, JsonValue] | None = None,
        deadline_ms: int | None = None,
    ) -> CommittedHarnessAssignment:
        context_object = self.storage.objects.inspect(context_object_digest)
        required = set(required_capabilities)
        supported = set(manifest.supported_capabilities)
        missing = sorted(required - supported)
        if missing:
            raise ValueError(f"Harness lacks required capabilities: {missing}")
        snapshot = self.storage.read_task_event(prepared.descriptor.task_id)
        if snapshot.projection.revision != prepared.task_revision:
            existing = self._assignment_from_snapshot(snapshot)
            if existing is not None and self._assignment_request_matches(
                existing.assignment,
                prepared=prepared,
                manifest=manifest,
                context_object_digest=context_object_digest,
                tool_catalog_digest=tool_catalog_digest,
                workspace_ref=workspace_ref,
                source_ref=source_ref,
                source_digest=source_digest,
                prior_artifact_refs=prior_artifact_refs,
                required_capabilities=required_capabilities,
                budget={} if budget is None else budget,
                deadline_ms=deadline_ms,
            ):
                return existing
            raise HarnessSuperseded(
                f"Task revision is {snapshot.projection.revision}, expected {prepared.task_revision}"
            )
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError("terminal Task cannot receive a Harness Assignment")
        retained_attempt = self._attempt_from_snapshot(snapshot)
        if retained_attempt is not None and retained_attempt.descriptor != prepared.descriptor:
            raise HarnessLifecycleError("Harness Task Attempt differs from current Task state")
        previous = self._assignment_from_snapshot(snapshot)
        if previous is not None and previous.attempt != prepared.descriptor:
            raise HarnessLifecycleError("replacement Assignment belongs to another Task Attempt")
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
                payload={
                    **self._attempt_fields(prepared),
                    "harnessManifestDigest": manifest.digest,
                    "harnessManifestObjectDigest": manifest_object.digest,
                    "assignmentId": assignment.assignment_id,
                    "assignmentGeneration": assignment.generation,
                    "assignmentDigest": assignment.digest,
                    "assignmentObjectDigest": assignment_object.digest,
                },
                state=TaskState.WAITING,
                frontier=locked.projection.ready_frontier,
                referenced_objects=(
                    prepared.descriptor_object,
                    manifest_object,
                    assignment_object,
                    context_object,
                ),
            ).projection
        return CommittedHarnessAssignment(
            attempt=prepared.descriptor,
            attempt_object=prepared.descriptor_object,
            manifest=manifest,
            manifest_object=manifest_object,
            assignment=assignment,
            assignment_object=assignment_object,
            task_revision=projection.revision,
        )

    def load_current_assignment(self, task_id: str) -> CommittedHarnessAssignment:
        assignment = self._assignment_from_snapshot(self.storage.read_task_event(task_id))
        if assignment is None:
            raise HarnessLifecycleError("Task has no current Harness Assignment")
        return assignment

    def record_run(
        self,
        committed: CommittedHarnessAssignment,
        receipt: HarnessRunReceipt,
    ) -> RecordedHarnessRun:
        self._require_run_matches_assignment(committed, receipt)
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
        receipt_object = self.storage.put_object(
            receipt.to_dict(), kind="harness-run-receipt"
        )
        data = self._assignment_fields(committed)
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
                payload={
                    **data,
                    "harnessRunId": receipt.harness_run_id,
                    "harnessRunDigest": receipt.digest,
                    "harnessRunObjectDigest": receipt_object.digest,
                },
                state=TaskState.WAITING,
                frontier=locked.projection.ready_frontier,
                referenced_objects=self._dedupe_objects(
                    self._assignment_objects(committed) + (receipt_object,)
                ),
            ).projection
        return RecordedHarnessRun(
            assignment=committed,
            receipt=receipt,
            receipt_object=receipt_object,
            task_revision=projection.revision,
        )

    def load_current_run(self, task_id: str) -> RecordedHarnessRun:
        run = self._run_from_snapshot(self.storage.read_task_event(task_id))
        if run is None:
            raise HarnessLifecycleError("Task has no current Harness Run")
        return run

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
        proposal_id = f"completion-proposal:{self._run_token(recorded.receipt.harness_run_id)}"
        snapshot = self.storage.read_task_event(task_id)
        existing = self._proposal_from_snapshot(snapshot)
        if existing is not None and existing.proposal.completion_proposal_id == proposal_id:
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
            raise HarnessLifecycleError("Harness Run is already bound to another proposal")
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError("terminal Task cannot receive CompletionProposal")
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

    def adjudicate_completion(
        self,
        proposed: ProposedCompletion,
        *,
        artifact_exists: ArtifactExists,
        acceptance_verifier: AcceptanceVerifier,
    ) -> CompletionDecisionReceipt:
        proposal = proposed.proposal
        snapshot = self.storage.read_task_event(proposal.task_id)
        existing = self._decision_from_snapshot(snapshot)
        if existing is not None:
            decision, outcome = existing
            if decision.completion_proposal_id != proposal.completion_proposal_id:
                raise HarnessLifecycleError("Task head contains another CompletionDecision")
            return CompletionDecisionReceipt(
                decision=decision,
                task_revision=snapshot.projection.revision,
                task_state=snapshot.projection.state.value,
                outcome=outcome,
                outcome_digest=(None if outcome is None else canonical_digest(outcome.to_dict())),
            )
        if snapshot.projection.state.terminal:
            raise HarnessLifecycleError("terminal Task cannot adjudicate a new CompletionProposal")
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
                    None if current_assignment is None else current_assignment.assignment.assignment_id
                ),
                "currentAssignmentGeneration": (
                    None if current_assignment is None else current_assignment.assignment.generation
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
            missing = [
                ref.ref
                for ref in proposal.evidence_refs + proposal.artifact_refs
                if not artifact_exists(ref)
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
                    reason = verifier_reason or "acceptance verifier rejected the proposal"
        verification_digest = canonical_digest(verification)
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
            outcome_object = self.storage.put_object(outcome.to_dict(), kind="task-outcome")
            outcome_digest = canonical_digest(outcome.to_dict())
        current_data = self._data(snapshot)
        references = self._state_objects(current_data) + (
            proposed.proposal_object,
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
            raise JournalCorruption("Harness event has incomplete Task Attempt references")
        descriptor, stored = self._load_object(
            object_digest,
            semantic_digest,
            kind="task-attempt-descriptor",
            decoder=TaskAttemptDescriptor.from_dict,
            label="TaskAttemptDescriptor",
        )
        if descriptor.task_id != snapshot.projection.task_id:
            raise JournalCorruption("Task Attempt identity differs from Task projection")
        return PreparedHarnessAttempt(
            descriptor=descriptor,
            descriptor_object=stored,
            task_revision=snapshot.projection.revision,
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
            raise JournalCorruption("Harness event has incomplete Assignment references")
        attempt = self._attempt_from_snapshot(snapshot)
        if attempt is None:
            raise JournalCorruption("Harness Assignment has no Task Attempt")
        manifest_object_digest = data.get("harnessManifestObjectDigest")
        manifest_digest = data.get("harnessManifestDigest")
        if not isinstance(manifest_object_digest, str) or not isinstance(manifest_digest, str):
            raise JournalCorruption("Harness Assignment has incomplete manifest references")
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
        return CommittedHarnessAssignment(
            attempt=attempt.descriptor,
            attempt_object=attempt.descriptor_object,
            manifest=manifest,
            manifest_object=manifest_object,
            assignment=assignment,
            assignment_object=assignment_object,
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
        return RecordedHarnessRun(
            assignment=assignment,
            receipt=receipt,
            receipt_object=stored,
            task_revision=snapshot.projection.revision,
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
            raise JournalCorruption("Harness event has incomplete CompletionProposal references")
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

    def _decision_from_snapshot(
        self, snapshot: TaskEventSnapshot
    ) -> tuple[CompletionDecision, TaskOutcome | None] | None:
        data = self._data(snapshot)
        object_digest = data.get("completionDecisionObjectDigest")
        semantic_digest = data.get("completionDecisionDigest")
        if object_digest is None and semantic_digest is None:
            return None
        if not isinstance(object_digest, str) or not isinstance(semantic_digest, str):
            raise JournalCorruption("Harness event has incomplete CompletionDecision references")
        decision, _ = self._load_object(
            object_digest,
            semantic_digest,
            kind="completion-decision",
            decoder=CompletionDecision.from_dict,
            label="CompletionDecision",
        )
        outcome_object_digest = data.get("outcomeObjectDigest")
        outcome_digest = data.get("outcomeDigest")
        if outcome_object_digest is None and outcome_digest is None:
            return decision, None
        if not isinstance(outcome_object_digest, str) or not isinstance(outcome_digest, str):
            raise JournalCorruption("CompletionDecision has incomplete TaskOutcome references")
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

    @staticmethod
    def _assignment_request_matches(
        assignment: HarnessAssignment,
        *,
        prepared: PreparedHarnessAttempt,
        manifest: HarnessCapabilityManifest,
        context_object_digest: str,
        tool_catalog_digest: str,
        workspace_ref: str | None,
        source_ref: str | None,
        source_digest: str | None,
        prior_artifact_refs: tuple[ArtifactRef, ...],
        required_capabilities: tuple[str, ...],
        budget: dict[str, JsonValue],
        deadline_ms: int | None,
    ) -> bool:
        return (
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
        return {
            "taskAttemptId": prepared.descriptor.task_attempt_id,
            "taskAttemptDigest": prepared.descriptor.digest,
            "taskAttemptObjectDigest": prepared.descriptor_object.digest,
        }

    @classmethod
    def _assignment_fields(
        cls, committed: CommittedHarnessAssignment
    ) -> dict[str, JsonValue]:
        return {
            **cls._attempt_fields(
                PreparedHarnessAttempt(
                    descriptor=committed.attempt,
                    descriptor_object=committed.attempt_object,
                    task_revision=committed.assignment.task_revision,
                )
            ),
            "harnessManifestDigest": committed.manifest.digest,
            "harnessManifestObjectDigest": committed.manifest_object.digest,
            "assignmentId": committed.assignment.assignment_id,
            "assignmentGeneration": committed.assignment.generation,
            "assignmentDigest": committed.assignment.digest,
            "assignmentObjectDigest": committed.assignment_object.digest,
        }

    @classmethod
    def _current_state_fields(cls, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        fields = (
            "taskAttemptId",
            "taskAttemptDigest",
            "taskAttemptObjectDigest",
            "harnessManifestDigest",
            "harnessManifestObjectDigest",
            "assignmentId",
            "assignmentGeneration",
            "assignmentDigest",
            "assignmentObjectDigest",
            "harnessRunId",
            "harnessRunDigest",
            "harnessRunObjectDigest",
        )
        return {field: data[field] for field in fields if field in data}

    @staticmethod
    def _assignment_objects(
        committed: CommittedHarnessAssignment,
    ) -> tuple[StoredObject, ...]:
        return (
            committed.attempt_object,
            committed.manifest_object,
            committed.assignment_object,
        )

    def _state_objects(self, data: dict[str, JsonValue]) -> tuple[StoredObject, ...]:
        values: list[StoredObject] = []
        for field in (
            "taskAttemptObjectDigest",
            "harnessManifestObjectDigest",
            "assignmentObjectDigest",
            "harnessRunObjectDigest",
        ):
            digest = data.get(field)
            if isinstance(digest, str):
                values.append(self.storage.objects.inspect(digest))
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
