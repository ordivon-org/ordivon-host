from __future__ import annotations

import itertools
from pathlib import Path
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host import EventKind, HostExtensionPort, HostKernel, HostStorage, TaskState
from ordivon_host.external_executor import (
    ExternalCompletionProposal,
    ExternalExecutionRequest,
    ExternalExecutorCoordinator,
    ExternalObservationConflict,
    ExternalRequestConflict,
    ExternalRunObservation,
    ExternalRunStatus,
)
from ordivon_host.ops import validate_history


CONTRACT_DIGEST = "sha256:" + "a" * 64


class FakeExternalExecutor:
    adapter_id = "external-executor:fake"

    def __init__(self) -> None:
        self.start_calls = 0
        self.physical_starts = 0
        self.observe_calls = 0
        self.cancel_calls = 0
        self.recover_calls = 0
        self.completion_calls = 0
        self.lose_first_start_response = False
        self._lost = False
        self.requests: dict[str, str] = {}
        self.observations: dict[str, ExternalRunObservation] = {}
        self.proposals: dict[str, ExternalCompletionProposal] = {}

    def start(self, request: ExternalExecutionRequest) -> ExternalRunObservation:
        self.start_calls += 1
        foreign = self.requests.get(request.request_id)
        if foreign is None:
            self.physical_starts += 1
            foreign = f"foreign-run:fake:{self.physical_starts}"
            self.requests[request.request_id] = foreign
            self.observations[foreign] = ExternalRunObservation(
                foreign_run_ref=foreign,
                status=ExternalRunStatus.RUNNING,
                revision=1,
                evidence_refs=(f"evidence:start:{self.physical_starts}",),
                observed_at_ms=2_000 + self.physical_starts,
                metadata={"phase": "started"},
            )
        if self.lose_first_start_response and not self._lost:
            self._lost = True
            raise RuntimeError("injected response loss after foreign Run creation")
        return self.observations[foreign]

    def observe(self, foreign_run_ref: str) -> ExternalRunObservation:
        self.observe_calls += 1
        return self.observations[foreign_run_ref]

    def cancel(self, foreign_run_ref: str, request_id: str) -> ExternalRunObservation:
        self.cancel_calls += 1
        if self.requests[request_id] != foreign_run_ref:
            raise AssertionError("cancel request identity differs")
        previous = self.observations[foreign_run_ref]
        observed = ExternalRunObservation(
            foreign_run_ref=foreign_run_ref,
            status=ExternalRunStatus.CANCELLED,
            revision=previous.revision + 1,
            evidence_refs=previous.evidence_refs + ("evidence:cancelled",),
            observed_at_ms=previous.observed_at_ms + 1,
            metadata={"phase": "cancelled"},
        )
        self.observations[foreign_run_ref] = observed
        return observed

    def recover(
        self,
        request: ExternalExecutionRequest,
        foreign_run_ref: str | None,
    ) -> ExternalRunObservation:
        self.recover_calls += 1
        recovered = self.requests[request.request_id]
        if foreign_run_ref is not None and foreign_run_ref != recovered:
            raise AssertionError("recovery foreign Run differs")
        return self.observations[recovered]

    def collect_completion(
        self,
        foreign_run_ref: str,
    ) -> ExternalCompletionProposal | None:
        self.completion_calls += 1
        return self.proposals.get(foreign_run_ref)

    def advance(
        self,
        foreign_run_ref: str,
        *,
        status: ExternalRunStatus,
        evidence: str,
    ) -> ExternalRunObservation:
        previous = self.observations[foreign_run_ref]
        observed = ExternalRunObservation(
            foreign_run_ref=foreign_run_ref,
            status=status,
            revision=previous.revision + 1,
            evidence_refs=previous.evidence_refs + (evidence,),
            observed_at_ms=previous.observed_at_ms + 1,
            metadata={"phase": status.value},
        )
        self.observations[foreign_run_ref] = observed
        return observed

    def set_completion(self, foreign_run_ref: str) -> ExternalCompletionProposal:
        proposal = ExternalCompletionProposal(
            proposal_id=f"completion-proposal:{foreign_run_ref.rsplit(':', 1)[-1]}",
            foreign_run_ref=foreign_run_ref,
            contract_digest=CONTRACT_DIGEST,
            summary="The foreign executor proposes completion; Host verification remains pending.",
            evidence_refs=("evidence:result",),
            artifact_refs=("artifact:result",),
            created_at_ms=3_000,
            metadata={"source": "fake"},
        )
        self.proposals[foreign_run_ref] = proposal
        return proposal


class ExternalExecutorCoordinatorTests(unittest.TestCase):
    @staticmethod
    def create_task(storage: HostStorage, clock, suffix: str = "one"):
        kernel = HostKernel(
            storage,
            clock_ms=clock,
            owner_id=f"host:external-executor:{suffix}",
        )
        projection = kernel.create_task(
            event_id=f"event:external-task-created:{suffix}",
            kind=EventKind.TASK_CREATED,
            task_id=f"task:external:{suffix}",
            goal_id=f"goal:external:{suffix}",
            payload={"workload": "external"},
            frontier=(f"node:external:{suffix}",),
        ).projection
        return kernel, projection

    @staticmethod
    def request(projection, suffix: str = "one") -> ExternalExecutionRequest:
        return ExternalExecutionRequest(
            request_id=f"external-request:{suffix}",
            adapter_id=FakeExternalExecutor.adapter_id,
            task_id=projection.task_id,
            task_revision=projection.revision,
            task_attempt_ref=f"task-attempt:{suffix}",
            contract_digest=CONTRACT_DIGEST,
            correlation_context={
                "traceId": "1" * 32,
                "links": [f"task:{suffix}"],
            },
            created_at_ms=1_500,
        )

    def test_response_loss_retry_binds_one_foreign_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            with HostStorage(directory) as storage:
                kernel, projection = self.create_task(storage, clock, "loss")
                coordinator = ExternalExecutorCoordinator(
                    HostExtensionPort(storage, kernel)
                )
                adapter = FakeExternalExecutor()
                adapter.lose_first_start_response = True
                request = self.request(projection, "loss")

                with self.assertRaisesRegex(RuntimeError, "response loss"):
                    coordinator.start(request, adapter)
                prepared = coordinator.load(projection.task_id)
                self.assertEqual(prepared.request, request)
                self.assertIsNone(prepared.binding)
                self.assertEqual(prepared.projection.revision, 2)

                bound = coordinator.start(request, adapter)
                assert bound.binding is not None
                self.assertEqual(bound.binding.request_id, request.request_id)
                self.assertEqual(bound.binding.foreign_run_ref, "foreign-run:fake:1")
                self.assertEqual(adapter.start_calls, 2)
                self.assertEqual(adapter.physical_starts, 1)

                duplicate = coordinator.start(request, adapter)
                self.assertEqual(duplicate.binding, bound.binding)
                self.assertEqual(adapter.start_calls, 2)
                self.assertEqual(adapter.physical_starts, 1)
                self.assertEqual(duplicate.projection.state, TaskState.READY)

    def test_changed_request_and_stale_new_request_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            with HostStorage(directory) as storage:
                kernel, projection = self.create_task(storage, clock, "conflict")
                port = HostExtensionPort(storage, kernel)
                coordinator = ExternalExecutorCoordinator(port)
                adapter = FakeExternalExecutor()
                request = self.request(projection, "conflict")
                coordinator.start(request, adapter)

                changed = ExternalExecutionRequest(
                    request_id=request.request_id,
                    adapter_id=request.adapter_id,
                    task_id=request.task_id,
                    task_revision=request.task_revision,
                    task_attempt_ref=request.task_attempt_ref,
                    contract_digest="sha256:" + "b" * 64,
                    correlation_context=request.correlation_context,
                    created_at_ms=request.created_at_ms,
                )
                with self.assertRaises(ExternalRequestConflict):
                    coordinator.start(changed, adapter)

            with HostStorage(Path(directory) / "second") as storage:
                kernel, projection = self.create_task(storage, clock, "stale")
                port = HostExtensionPort(storage, kernel)
                marker = port.put_object({"marker": True}, kind="fixture-marker")
                port.append_preserving(
                    task_id=projection.task_id,
                    expected_revision=projection.revision,
                    event_id="event:external-stale:marker",
                    kind=EventKind("fixture.marker-recorded"),
                    updates={"markerObjectDigest": marker.digest},
                    referenced_objects=(marker,),
                )
                with self.assertRaisesRegex(ExternalRequestConflict, "revision"):
                    ExternalExecutorCoordinator(port).start(
                        self.request(projection, "stale"),
                        FakeExternalExecutor(),
                    )

    def test_observe_revision_fencing_and_adapter_free_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            adapter = FakeExternalExecutor()
            with HostStorage(directory) as storage:
                kernel, projection = self.create_task(storage, clock, "observe")
                coordinator = ExternalExecutorCoordinator(
                    HostExtensionPort(storage, kernel)
                )
                started = coordinator.start(self.request(projection, "observe"), adapter)
                assert started.binding is not None
                foreign = started.binding.foreign_run_ref
                adapter.advance(
                    foreign,
                    status=ExternalRunStatus.WAITING,
                    evidence="evidence:waiting",
                )
                observed = coordinator.observe(projection.task_id, adapter)
                assert observed.binding is not None
                self.assertEqual(observed.binding.observed_status, ExternalRunStatus.WAITING)
                self.assertEqual(observed.binding.last_reconciled_revision, 2)

                adapter.observations[foreign] = ExternalRunObservation(
                    foreign_run_ref=foreign,
                    status=ExternalRunStatus.RUNNING,
                    revision=1,
                    evidence_refs=("evidence:stale",),
                    observed_at_ms=4_000,
                    metadata={"phase": "stale"},
                )
                with self.assertRaises(ExternalObservationConflict):
                    coordinator.observe(projection.task_id, adapter)

            with HostStorage(directory) as reopened:
                coordinator = ExternalExecutorCoordinator(
                    HostExtensionPort(
                        reopened,
                        HostKernel(
                            reopened,
                            clock_ms=clock,
                            owner_id="host:external-executor:reopen",
                        ),
                    )
                )
                loaded = coordinator.load(projection.task_id)
                assert loaded.binding is not None
                self.assertEqual(loaded.binding.foreign_run_ref, foreign)
                self.assertEqual(loaded.binding.observed_status, ExternalRunStatus.WAITING)
                self.assertEqual(loaded.projection.state, TaskState.READY)
                self.assertTrue(validate_history(reopened).events >= 3)

    def test_cancel_recover_and_completion_never_accept_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(4_000).__next__
            with HostStorage(directory) as storage:
                kernel, projection = self.create_task(storage, clock, "complete")
                coordinator = ExternalExecutorCoordinator(
                    HostExtensionPort(storage, kernel)
                )
                adapter = FakeExternalExecutor()
                started = coordinator.start(self.request(projection, "complete"), adapter)
                assert started.binding is not None
                foreign = started.binding.foreign_run_ref

                recovered = coordinator.recover(projection.task_id, adapter)
                assert recovered.binding is not None
                self.assertEqual(recovered.binding.foreign_run_ref, foreign)
                self.assertEqual(adapter.recover_calls, 1)

                cancelled = coordinator.cancel(projection.task_id, adapter)
                assert cancelled.binding is not None
                self.assertTrue(cancelled.binding.cancellation_requested)
                self.assertEqual(cancelled.binding.observed_status, ExternalRunStatus.CANCELLED)

                proposal = adapter.set_completion(foreign)
                collected = coordinator.collect_completion(projection.task_id, adapter)
                self.assertEqual(collected.completion_proposal, proposal)
                assert collected.binding is not None
                self.assertEqual(
                    collected.binding.completion_proposal_digest,
                    proposal.digest,
                )
                self.assertEqual(collected.projection.state, TaskState.READY)
                self.assertFalse(collected.projection.state.terminal)
                self.assertNotIn("taskOutcomeObjectDigest", storage.read_task_event(projection.task_id).data)

                duplicate = coordinator.collect_completion(projection.task_id, adapter)
                self.assertEqual(duplicate.completion_proposal, proposal)
                self.assertEqual(duplicate.projection.revision, collected.projection.revision)

    def test_models_round_trip_and_host_has_no_harness_dependency(self) -> None:
        request = ExternalExecutionRequest(
            request_id="external-request:model",
            adapter_id=FakeExternalExecutor.adapter_id,
            task_id="task:external:model",
            task_revision=7,
            task_attempt_ref="task-attempt:model",
            contract_digest=CONTRACT_DIGEST,
            correlation_context={"traceId": "2" * 32},
            created_at_ms=5_000,
        )
        self.assertEqual(ExternalExecutionRequest.from_dict(request.to_dict()), request)
        observation = ExternalRunObservation(
            foreign_run_ref="foreign-run:model",
            status=ExternalRunStatus.UNKNOWN,
            revision=2,
            evidence_refs=("evidence:model",),
            observed_at_ms=5_001,
            metadata={"reason": "transport"},
        )
        self.assertEqual(ExternalRunObservation.from_dict(observation.to_dict()), observation)
        proposal = ExternalCompletionProposal(
            proposal_id="completion-proposal:model",
            foreign_run_ref=observation.foreign_run_ref,
            contract_digest=CONTRACT_DIGEST,
            summary="Candidate result only.",
            evidence_refs=observation.evidence_refs,
            artifact_refs=(),
            created_at_ms=5_002,
            metadata={},
        )
        self.assertEqual(ExternalCompletionProposal.from_dict(proposal.to_dict()), proposal)
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ordivon_host"
            / "external_executor.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ordivon_harness", source)
        self.assertNotIn("HarnessRunContract", source)
        self.assertNotIn("HarnessRunner", source)
        self.assertEqual(
            canonical_digest(request.to_dict()),
            request.digest,
        )


if __name__ == "__main__":
    unittest.main()
