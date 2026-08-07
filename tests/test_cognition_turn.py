from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage, TaskProjection, TaskState
from ordivon_host.cognition import (
    AdmissionState,
    BlockKind,
    CandidateAction,
    ClosedChoiceContextRequest,
    CognitionExecutionEvidence,
    CognitionHost,
    CognitionRequestSuperseded,
    DecisionKind,
    Freshness,
    ScriptedActionSelector,
    block_from_payload,
)

WORLD = "sha256:" + ("a" * 64)
DISPATCH = "dispatch:runtime-job-7"
TASK_ID = "task:cognition-turn"
GOAL_ID = "goal:cognition-turn"
DECISION_NODE = "node:cognition-turn:decide"


def cognition_request() -> ClosedChoiceContextRequest:
    return ClosedChoiceContextRequest(
        task_id=TASK_ID,
        world_digest=WORLD,
        blocks=(
            block_from_payload(
                block_id="context-block:goal",
                kind=BlockKind.GOAL,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source={"goalId": GOAL_ID},
                payload={
                    "statement": "Recover the original Runtime operation without duplicate delivery."
                },
            ),
            block_from_payload(
                block_id="context-block:dispatch",
                kind=BlockKind.DISPATCH,
                priority=90,
                required=True,
                freshness=Freshness.CURRENT,
                source={"dispatchId": DISPATCH},
                payload={"dispatchId": DISPATCH, "state": "unknown"},
            ),
        ),
        candidates=(
            CandidateAction(
                "action:observe-original",
                DecisionKind.OBSERVE_DISPATCH,
                "Observe the original Runtime Job.",
                dispatch_id=DISPATCH,
            ),
            CandidateAction(
                "action:request-human",
                DecisionKind.REQUEST_HUMAN,
                "Ask a human to resolve the ambiguity.",
            ),
            CandidateAction(
                "action:wait",
                DecisionKind.WAIT,
                "Wait for another signal.",
            ),
        ),
        forbidden_effect_ids=("effect:completed",),
        unresolved_dispatch_ids=(DISPATCH,),
    )


def create_task(storage: HostStorage) -> TaskProjection:
    projection = TaskProjection(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        state=TaskState.READY,
        active_node_id=None,
        ready_frontier=(DECISION_NODE,),
        revision=1,
        updated_at_ms=1,
    )
    storage.record_task_event(
        event_id="event:cognition-turn:create",
        kind=EventKind.TASK_CREATED,
        payload={"decisionNodeId": DECISION_NODE},
        projection=projection,
        expected_revision=0,
    )
    return projection


def host(storage: HostStorage) -> CognitionHost:
    return CognitionHost(storage, clock_ms=itertools.count(100).__next__)


def admission_state() -> AdmissionState:
    return AdmissionState(
        world_digest=WORLD,
        completed_effect_ids=("effect:completed",),
        unresolved_dispatch_ids=(DISPATCH,),
    )


def evidence() -> CognitionExecutionEvidence:
    return CognitionExecutionEvidence(
        source_ref="policy:scripted-test",
        evidence_refs=("trace:scripted-test",),
        metadata={"sourceKind": "deterministic-policy"},
    )


class CognitionTurnTests(unittest.TestCase):
    def test_request_is_persistent_idempotent_and_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                first = host(storage).request_selection(
                    task_id=TASK_ID,
                    node_id=DECISION_NODE,
                    context_request=cognition_request(),
                    token_budget=4_000,
                )
                second = host(storage).request_selection(
                    task_id=TASK_ID,
                    node_id=DECISION_NODE,
                    context_request=cognition_request(),
                    token_budget=4_000,
                )
                self.assertEqual(first.request, second.request)
                self.assertEqual(first.request.task_revision, 2)
                self.assertEqual(storage.journal.event_count(TASK_ID), 2)
                projection = storage.journal.get_task(TASK_ID)
                assert projection is not None
                self.assertEqual(projection.state, TaskState.WAITING)
                self.assertEqual(
                    storage.read_task_event(TASK_ID).event_kind,
                    EventKind.COGNITION_REQUESTED,
                )

            with HostStorage(directory) as reopened:
                recovered = host(reopened).load_request(TASK_ID)
                self.assertEqual(recovered.request, first.request)
                self.assertEqual(recovered.context, first.context)
                self.assertEqual(recovered.context_object, first.context_object)

    def test_external_cognition_holds_no_task_lease_and_selection_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).request_selection(
                    task_id=TASK_ID,
                    node_id=DECISION_NODE,
                    context_request=cognition_request(),
                    token_budget=4_000,
                )

            # Cognition executes after one durable semantic request and outside Host lease authority.
            with HostStorage(directory) as concurrent:
                lease = concurrent.journal.acquire_lease(
                    TASK_ID,
                    owner_id="caller:external-cognition",
                    now_ms=500,
                    ttl_ms=100,
                )
                concurrent.journal.release_lease(lease)
            selection = ScriptedActionSelector(
                (DecisionKind.OBSERVE_DISPATCH,)
            ).select(prepared.context)

            with HostStorage(directory) as storage:
                receipt = host(storage).admit_selection(
                    prepared,
                    selection,
                    evidence=evidence(),
                    state_reader=admission_state,
                )
                self.assertEqual(receipt.revision, 3)
                self.assertEqual(receipt.selected_action_id, "action:observe-original")
                projection = storage.journal.get_task(TASK_ID)
                assert projection is not None
                self.assertEqual(projection.ready_frontier, (receipt.selected_node_id,))
                kinds = {value.kind for value in storage.journal.object_refs()}
                self.assertTrue(
                    {
                        "compiled-context",
                        "cognition-work-request",
                        "action-selection",
                        "cognition-execution-evidence",
                        "admitted-action-selection",
                    }.issubset(kinds)
                )
                self.assertEqual(storage.journal.event_count(TASK_ID), 3)
                retained_evidence = storage.objects.get(
                    receipt.evidence_object_digest,
                    expected_kind="cognition-execution-evidence",
                )
                self.assertEqual(retained_evidence["sourceRef"], "policy:scripted-test")

    def test_external_failure_leaves_one_durable_semantic_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).request_selection(
                    task_id=TASK_ID,
                    node_id=DECISION_NODE,
                    context_request=cognition_request(),
                    token_budget=4_000,
                )
                self.assertEqual(prepared.request.task_revision, 2)
            with HostStorage(directory) as storage:
                current = storage.journal.get_task(TASK_ID)
                assert current is not None
                self.assertEqual(current.revision, 2)
                self.assertEqual(current.state, TaskState.WAITING)
                self.assertEqual(storage.journal.event_count(TASK_ID), 2)
                self.assertEqual(
                    storage.read_task_event(TASK_ID).event_kind,
                    EventKind.COGNITION_REQUESTED,
                )

    def test_result_is_superseded_if_task_advances_before_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).request_selection(
                    task_id=TASK_ID,
                    node_id=DECISION_NODE,
                    context_request=cognition_request(),
                    token_budget=4_000,
                )
            selection = ScriptedActionSelector(
                (DecisionKind.OBSERVE_DISPATCH,)
            ).select(prepared.context)
            with HostStorage(directory) as concurrent:
                current = concurrent.journal.get_task(TASK_ID)
                assert current is not None
                kernel = HostKernel(
                    concurrent,
                    clock_ms=itertools.count(current.updated_at_ms + 1).__next__,
                    owner_id="host:concurrent-entrypoint",
                )
                with kernel.locked_task(
                    TASK_ID,
                    expected_revision=current.revision,
                    expected_state=current.state,
                    expected_frontier=current.ready_frontier,
                ) as locked:
                    locked.commit(
                        event_id="event:cognition-turn:concurrent",
                        kind=EventKind.TASK_FRONTIER_CHANGED,
                        payload={"source": "concurrent-entrypoint"},
                    )
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(CognitionRequestSuperseded, "revision is 3"):
                    host(storage).admit_selection(
                        prepared,
                        selection,
                        evidence=evidence(),
                        state_reader=admission_state,
                    )
                kinds = [value.kind for value in storage.journal.object_refs()]
                self.assertNotIn("action-selection", kinds)
                self.assertNotIn("admitted-action-selection", kinds)

    def test_admission_state_rejects_untyped_execution_identities(self) -> None:
        with self.assertRaisesRegex(ValueError, "completed Effect identities"):
            AdmissionState(
                world_digest=WORLD,
                completed_effect_ids=("job:not-an-effect",),
                unresolved_dispatch_ids=(),
            )
        with self.assertRaisesRegex(ValueError, "unresolved Dispatch identities"):
            AdmissionState(
                world_digest=WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=("job:not-a-dispatch",),
            )


if __name__ == "__main__":
    unittest.main()
