from __future__ import annotations

import itertools
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host import (
    ArtifactRef,
    CompletionDecision,
    CompletionProposal,
    EventKind,
    HarnessAssignment,
    HarnessCapabilityManifest,
    HarnessHost,
    HarnessRunReceipt,
    HostKernel,
    HostStorage,
    TaskAttemptDescriptor,
    TaskState,
    operator_handoff,
)

TASK_ID = "task:harness-h1"
GOAL_ID = "goal:harness-h1"
FRONTIER = "node:harness-h1:work"
OBJECTIVE = canonical_digest({"objective": "repair repository"})
ACCEPTANCE = canonical_digest({"acceptance": "tests pass and artifacts exist"})
TOOL_CATALOG = canonical_digest({"tools": ["read", "mutate", "exec"]})
SOURCE = canonical_digest({"source": "fixture-revision"})


def create_task(storage: HostStorage, clock) -> None:
    HostKernel(
        storage,
        clock_ms=clock,
        owner_id="host:harness-h1-task-create",
    ).create_task(
        event_id="event:harness-h1:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "harness-replacement-repository-repair-v1"},
        frontier=(FRONTIER,),
    )


def manifest(harness_id: str = "harness:codex-app-server") -> HarnessCapabilityManifest:
    return HarnessCapabilityManifest(
        harness_id=harness_id,
        protocol="json-rpc" if "codex" in harness_id else "acp",
        protocol_revision="0.145" if "codex" in harness_id else "0.18",
        persistent_session=True,
        session_resume=True,
        session_fork=True,
        interrupt=True,
        tool_events=True,
        approval_events=True,
        usage=True,
        images=False,
        compaction=True,
        checkpoint="hermes" in harness_id,
        local_subagents=False,
        extensions=("provider-native-events",),
    )


def context_object(storage: HostStorage, label: str):
    return storage.put_object(
        {"schemaVersion": 1, "label": label},
        kind="compiled-context",
    )


def artifact(name: str, marker: str) -> ArtifactRef:
    return ArtifactRef(
        ref=f"artifact:{name}",
        kind="fixture-artifact",
        digest=canonical_digest({"artifact": marker}),
    )


def run_receipt(assignment, *, run_id: str, stop_reason: str = "completed") -> HarnessRunReceipt:
    return HarnessRunReceipt(
        harness_run_id=run_id,
        assignment_id=assignment.assignment.assignment_id,
        assignment_generation=assignment.assignment.generation,
        harness_id=assignment.assignment.target_harness_id,
        harness_revision="fixture-revision",
        manifest_digest=assignment.assignment.harness_manifest_digest,
        session_ref=f"session:{run_id.removeprefix('harness-run:')}",
        started_at_ms=100,
        finished_at_ms=200,
        stop_reason=stop_reason,
        event_digest=canonical_digest({"run": run_id, "events": 3}),
        context_digest=assignment.assignment.context_object_digest,
        tool_catalog_digest=assignment.assignment.tool_catalog_digest,
        runtime_job_refs=(f"job:{run_id.removeprefix('harness-run:')}",),
        artifact_refs=(),
        usage={"modelCalls": 2, "toolCalls": 3},
    )


class HarnessH1ModelTests(unittest.TestCase):
    def test_models_round_trip_and_reject_unknown_fields(self) -> None:
        attempt = TaskAttemptDescriptor(
            task_attempt_id="task-attempt:harness-h1:1",
            task_id=TASK_ID,
            started_at_task_revision=1,
            objective_digest=OBJECTIVE,
            acceptance_criteria_digest=ACCEPTANCE,
            created_at_ms=10,
        )
        self.assertEqual(TaskAttemptDescriptor.from_dict(attempt.to_dict()), attempt)
        capability = manifest()
        self.assertEqual(
            HarnessCapabilityManifest.from_dict(capability.to_dict()), capability
        )
        assignment = HarnessAssignment(
            assignment_id="assignment:harness-h1:attempt-1:g1",
            task_id=TASK_ID,
            task_revision=1,
            task_attempt_id=attempt.task_attempt_id,
            generation=1,
            target_harness_id=capability.harness_id,
            harness_manifest_digest=capability.digest,
            context_object_digest=canonical_digest({"context": 1}),
            acceptance_criteria_digest=ACCEPTANCE,
            tool_catalog_digest=TOOL_CATALOG,
            workspace_ref="workspace:harness-h1",
            source_ref="repository:fixture",
            source_digest=SOURCE,
            prior_artifact_refs=(),
            required_capabilities=("persistent_session", "interrupt", "tool_events"),
            budget={"modelCalls": 4},
            deadline_ms=1_000,
            created_at_ms=20,
        )
        self.assertEqual(HarnessAssignment.from_dict(assignment.to_dict()), assignment)
        receipt = HarnessRunReceipt(
            harness_run_id="harness-run:codex:1",
            assignment_id=assignment.assignment_id,
            assignment_generation=1,
            harness_id=capability.harness_id,
            harness_revision="fixture",
            manifest_digest=capability.digest,
            session_ref="session:codex:1",
            started_at_ms=30,
            finished_at_ms=40,
            stop_reason="completed",
            event_digest=canonical_digest({"events": []}),
            context_digest=assignment.context_object_digest,
            tool_catalog_digest=TOOL_CATALOG,
            runtime_job_refs=("job:fixture",),
            artifact_refs=(),
            usage={},
        )
        self.assertEqual(HarnessRunReceipt.from_dict(receipt.to_dict()), receipt)
        proposal = CompletionProposal(
            completion_proposal_id="completion-proposal:codex:1",
            task_id=TASK_ID,
            task_revision=1,
            task_attempt_id=attempt.task_attempt_id,
            assignment_id=assignment.assignment_id,
            assignment_generation=1,
            harness_run_id=receipt.harness_run_id,
            summary="Repair completed and verified.",
            acceptance_results={"tests": "passed"},
            evidence_refs=(),
            artifact_refs=(),
            unresolved_effect_refs=(),
            unresolved_unknowns=(),
            usage={},
            created_at_ms=50,
        )
        self.assertEqual(CompletionProposal.from_dict(proposal.to_dict()), proposal)
        decision = CompletionDecision(
            completion_decision_id="completion-decision:codex:1",
            completion_proposal_id=proposal.completion_proposal_id,
            task_id=TASK_ID,
            accepted=True,
            reason_code="accepted",
            reason=None,
            verification_digest=canonical_digest({"accepted": True}),
            decided_at_ms=60,
        )
        self.assertEqual(CompletionDecision.from_dict(decision.to_dict()), decision)
        invalid = proposal.to_dict()
        invalid["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "fields differ"):
            CompletionProposal.from_dict(invalid)

    def test_duplicate_artifact_refs_and_missing_capability_are_rejected(self) -> None:
        duplicate = artifact("same", "one")
        with self.assertRaisesRegex(ValueError, "refs must be unique"):
            CompletionProposal(
                completion_proposal_id="completion-proposal:duplicate",
                task_id=TASK_ID,
                task_revision=1,
                task_attempt_id="task-attempt:harness-h1:1",
                assignment_id="assignment:harness-h1:attempt-1:g1",
                assignment_generation=1,
                harness_run_id="harness-run:duplicate",
                summary="Duplicate evidence fixture.",
                acceptance_results={},
                evidence_refs=(duplicate, duplicate),
                artifact_refs=(),
                unresolved_effect_refs=(),
                unresolved_unknowns=(),
                usage={},
                created_at_ms=1,
            )
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                context = context_object(storage, "missing-capability")
                with self.assertRaisesRegex(ValueError, "lacks required capabilities"):
                    host.assign(
                        attempt,
                        manifest=manifest(),
                        context_object_digest=context.digest,
                        tool_catalog_digest=TOOL_CATALOG,
                        required_capabilities=("local_subagents",),
                    )
                self.assertEqual(storage.journal.event_count(TASK_ID), 1)


class HarnessH1LifecycleTests(unittest.TestCase):
    def test_assignment_generation_replaces_harness_with_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                first_context = context_object(storage, "codex-context")
                first = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=first_context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                    workspace_ref="workspace:harness-h1",
                    source_ref="repository:fixture",
                    source_digest=SOURCE,
                    required_capabilities=("persistent_session", "interrupt", "tool_events"),
                )
                replay = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=first_context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                    workspace_ref="workspace:harness-h1",
                    source_ref="repository:fixture",
                    source_digest=SOURCE,
                    required_capabilities=("persistent_session", "interrupt", "tool_events"),
                )
                self.assertEqual(replay.assignment, first.assignment)
                recorded = host.record_run(
                    first,
                    run_receipt(first, run_id="harness-run:codex:1", stop_reason="interrupted"),
                )
                current_attempt = host.load_attempt(TASK_ID)
                second_context = context_object(storage, "hermes-context")
                second = host.assign(
                    current_attempt,
                    manifest=manifest("harness:hermes-acp"),
                    context_object_digest=second_context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                    workspace_ref="workspace:harness-h1",
                    source_ref="repository:fixture",
                    source_digest=SOURCE,
                    prior_artifact_refs=(artifact("diagnosis", "codex"),),
                    required_capabilities=("persistent_session", "interrupt", "tool_events"),
                )
                self.assertEqual(first.assignment.generation, 1)
                self.assertEqual(second.assignment.generation, 2)
                self.assertEqual(first.attempt, second.attempt)
                self.assertNotEqual(
                    first.assignment.context_object_digest,
                    second.assignment.context_object_digest,
                )
                self.assertEqual(second.assignment.task_revision, recorded.task_revision)
                capsule = operator_handoff(storage, TASK_ID)
                self.assertEqual(capsule.task_attempt_id, first.attempt.task_attempt_id)
                self.assertEqual(capsule.assignment_id, second.assignment.assignment_id)
                self.assertEqual(capsule.assignment_generation, 2)
                self.assertEqual(capsule.harness_run_id, None)
                self.assertEqual(
                    capsule.next_admissible,
                    ("run-current-harness-assignment",),
                )

    def test_stale_generation_proposal_is_retained_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                first_context = context_object(storage, "first")
                first = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=first_context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                first_run = host.record_run(
                    first,
                    run_receipt(first, run_id="harness-run:codex:stale"),
                )
                second_context = context_object(storage, "second")
                second = host.assign(
                    host.load_attempt(TASK_ID),
                    manifest=manifest("harness:hermes-acp"),
                    context_object_digest=second_context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                stale = host.propose_completion(
                    first_run,
                    summary="Old Harness claims completion.",
                    acceptance_results={"tests": "claimed"},
                )
                decision = host.adjudicate_completion(
                    stale,
                    artifact_exists=lambda _: True,
                    acceptance_verifier=lambda _: (True, None, {"shouldNotRun": True}),
                )
                self.assertFalse(decision.decision.accepted)
                self.assertEqual(decision.decision.reason_code, "stale_assignment")
                self.assertIsNone(decision.outcome)
                current = storage.journal.get_task(TASK_ID)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.state, TaskState.WAITING)
                self.assertEqual(
                    host.load_current_assignment(TASK_ID).assignment,
                    second.assignment,
                )
                kinds = {value.kind for value in storage.journal.object_refs()}
                self.assertIn("completion-proposal", kinds)
                self.assertIn("completion-decision", kinds)
                self.assertNotIn("task-outcome", kinds)
                capsule = operator_handoff(storage, TASK_ID)
                self.assertEqual(capsule.completion_proposal_id, stale.proposal.completion_proposal_id)
                self.assertEqual(
                    capsule.completion_decision_id,
                    decision.decision.completion_decision_id,
                )
                self.assertEqual(
                    capsule.next_admissible,
                    ("continue-current-harness-assignment",),
                )

    def test_missing_artifact_rejects_process_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(4_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                context = context_object(storage, "missing-artifact")
                assignment = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                recorded = host.record_run(
                    assignment,
                    run_receipt(assignment, run_id="harness-run:codex:missing"),
                )
                required = artifact("completion", "missing")
                proposed = host.propose_completion(
                    recorded,
                    summary="Runtime process exited successfully.",
                    acceptance_results={"processExit": 0},
                    artifact_refs=(required,),
                )

                def verifier(_):
                    raise AssertionError("acceptance verifier must not run")

                decision = host.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda ref: ref.ref != required.ref,
                    acceptance_verifier=verifier,
                )
                self.assertEqual(decision.decision.reason_code, "missing_artifact")
                self.assertIsNone(decision.outcome)
                self.assertEqual(storage.journal.get_task(TASK_ID).state, TaskState.WAITING)

    def test_unresolved_unknown_rejects_without_terminating_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(5_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                context = context_object(storage, "unknown")
                assignment = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                recorded = host.record_run(
                    assignment,
                    run_receipt(assignment, run_id="harness-run:codex:unknown"),
                )
                proposed = host.propose_completion(
                    recorded,
                    summary="Code changed but delivery remains ambiguous.",
                    acceptance_results={"tests": "passed"},
                    unresolved_unknowns=("runtime response lost after possible commitment",),
                )
                decision = host.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda _: True,
                    acceptance_verifier=lambda _: (True, None, {"shouldNotRun": True}),
                )
                self.assertEqual(decision.decision.reason_code, "unresolved_unknown")
                self.assertEqual(storage.journal.get_task(TASK_ID).state, TaskState.WAITING)

    def test_current_proposal_completes_once_despite_later_host_event_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(6_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                context = context_object(storage, "accepted")
                assignment = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                recorded = host.record_run(
                    assignment,
                    run_receipt(assignment, run_id="harness-run:codex:accepted"),
                )
                output = artifact("completion", "present")
                proposed = host.propose_completion(
                    recorded,
                    summary="Required code and evidence are complete.",
                    acceptance_results={"tests": "passed", "changedPaths": ["src/fix.py"]},
                    artifact_refs=(output,),
                )
                self.assertEqual(proposed.proposal.task_revision, 1)
                self.assertGreater(proposed.task_revision, proposed.proposal.task_revision)
                before = storage.journal.event_count(TASK_ID)
                accepted = host.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda ref: ref.ref == output.ref,
                    acceptance_verifier=lambda proposal: (
                        proposal.acceptance_results.get("tests") == "passed",
                        None,
                        {"accepted": True, "grader": "fixture-v1"},
                    ),
                )
                self.assertTrue(accepted.decision.accepted)
                self.assertIsNotNone(accepted.outcome)
                self.assertEqual(accepted.task_state, TaskState.COMPLETED.value)
                self.assertEqual(storage.journal.get_task(TASK_ID).state, TaskState.COMPLETED)
                replay = host.adjudicate_completion(
                    proposed,
                    artifact_exists=lambda _: False,
                    acceptance_verifier=lambda _: (False, "must not rerun", {}),
                )
                self.assertEqual(replay, accepted)
                self.assertEqual(storage.journal.event_count(TASK_ID), before + 1)
                self.assertEqual(
                    storage.read_task_event(TASK_ID).event_kind,
                    EventKind.COMPLETION_DECIDED,
                )
                capsule = operator_handoff(storage, TASK_ID)
                self.assertEqual(capsule.next_admissible, ("inspect-terminal-outcome",))
                self.assertIsNotNone(capsule.outcome_object_digest)

    def test_fresh_host_reloads_proposal_and_completion_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(7_000).__next__
            with HostStorage(directory) as storage:
                create_task(storage, clock)
                host = HarnessHost(storage, clock_ms=clock)
                attempt = host.start_attempt(
                    TASK_ID,
                    objective_digest=OBJECTIVE,
                    acceptance_criteria_digest=ACCEPTANCE,
                )
                context = context_object(storage, "fresh-host")
                assignment = host.assign(
                    attempt,
                    manifest=manifest(),
                    context_object_digest=context.digest,
                    tool_catalog_digest=TOOL_CATALOG,
                )
                recorded = host.record_run(
                    assignment,
                    run_receipt(assignment, run_id="harness-run:codex:fresh-host"),
                )
                output = artifact("completion", "fresh-host")
                proposed = host.propose_completion(
                    recorded,
                    summary="Fresh Host must recover this proposal.",
                    acceptance_results={"tests": "passed"},
                    artifact_refs=(output,),
                )
                proposal_digest = proposed.proposal.digest

            with HostStorage(directory) as reopened:
                fresh = HarnessHost(reopened, clock_ms=clock)
                recovered = fresh.load_proposed_completion(TASK_ID)
                self.assertEqual(recovered.proposal.digest, proposal_digest)
                self.assertEqual(
                    fresh.load_current_assignment(TASK_ID).assignment.assignment_id,
                    assignment.assignment.assignment_id,
                )
                self.assertEqual(
                    fresh.load_current_run(TASK_ID).receipt.harness_run_id,
                    recorded.receipt.harness_run_id,
                )
                decided = fresh.adjudicate_completion(
                    recovered,
                    artifact_exists=lambda ref: ref.ref == output.ref,
                    acceptance_verifier=lambda _: (
                        True,
                        None,
                        {"accepted": True, "freshHost": True},
                    ),
                )
                decision_digest = decided.decision.digest

            with HostStorage(directory) as reopened_again:
                fresh_again = HarnessHost(reopened_again, clock_ms=clock)
                recovered_proposal = fresh_again.load_proposed_completion(TASK_ID)
                replay = fresh_again.adjudicate_completion(
                    recovered_proposal,
                    artifact_exists=lambda _: False,
                    acceptance_verifier=lambda _: (False, "must not rerun", {}),
                )
                self.assertEqual(replay.decision.digest, decision_digest)
                self.assertEqual(
                    reopened_again.journal.get_task(TASK_ID).state,
                    TaskState.COMPLETED,
                )


if __name__ == "__main__":
    unittest.main()
