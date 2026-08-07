from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage, TaskProjection, TaskState
from ordivon_host.cognition import (
    AdmissionState,
    BlockKind,
    CandidateAction,
    CognitionRequest,
    CognitionSuperseded,
    CognitionTurnHost,
    DecisionKind,
    Freshness,
    ScriptedPreferenceAdapter,
    block_from_payload,
)

WORLD = "sha256:" + ("a" * 64)
DISPATCH = "dispatch:runtime-job-7"
TASK_ID = "task:cognition-turn"
GOAL_ID = "goal:cognition-turn"
DECISION_NODE = "node:cognition-turn:decide"


def cognition_request() -> CognitionRequest:
    return CognitionRequest(
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


def host(storage: HostStorage) -> CognitionTurnHost:
    return CognitionTurnHost(
        storage,
        clock_ms=itertools.count(100).__next__,
    )


def admission_state() -> AdmissionState:
    return AdmissionState(
        world_digest=WORLD,
        completed_effect_ids=("effect:completed",),
        unresolved_dispatch_ids=(DISPATCH,),
    )


class CognitionTurnTests(unittest.TestCase):
    def test_prepare_is_persistent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                first = host(storage).prepare(
                    task_id=TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                second = host(storage).prepare(
                    task_id=TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                self.assertEqual(first.context.digest, second.context.digest)
                self.assertEqual(first.task_revision, 2)
                self.assertEqual(second.task_revision, 2)
                self.assertEqual(storage.journal.event_count(TASK_ID), 2)

            with HostStorage(directory) as reopened:
                recovered = host(reopened).load_prepared(TASK_ID)
                self.assertEqual(recovered.context.digest, first.context.digest)
                self.assertEqual(recovered.context_object, first.context_object)
                self.assertEqual(recovered.task_revision, 2)

    def test_external_cognition_holds_no_task_lease_and_decision_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).prepare(
                    task_id=TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                invocation = host(storage).prepare_invocation(
                    prepared, gateway_id="executor:external-test"
                )

            # External cognition occurs after durable intent and outside any Host lease.
            with HostStorage(directory) as concurrent:
                lease = concurrent.journal.acquire_lease(
                    TASK_ID,
                    owner_id="caller:external-cognition",
                    now_ms=500,
                    ttl_ms=100,
                )
                concurrent.journal.release_lease(lease)
            decision = ScriptedPreferenceAdapter(
                (DecisionKind.OBSERVE_DISPATCH,)
            ).decide(invocation.prepared.context)

            with HostStorage(directory) as storage:
                receipt = host(storage).admit_decision(
                    invocation,
                    decision,
                    evidence={
                        "executor": "external-test",
                        "physicalProviderCall": False,
                        "externalDecision": False,
                    },
                    state_reader=admission_state,
                )
                self.assertEqual(receipt.revision, 4)
                self.assertEqual(receipt.selected_action_id, "action:observe-original")
                projection = storage.journal.get_task(TASK_ID)
                self.assertIsNotNone(projection)
                assert projection is not None
                self.assertEqual(projection.ready_frontier, (receipt.selected_node_id,))
                kinds = {value.kind for value in storage.journal.object_refs()}
                self.assertTrue(
                    {
                        "compiled-context",
                        "model-invocation-intent",
                        "model-invocation-observation",
                        "model-invocation-receipt",
                        "model-decision",
                        "admitted-decision",
                    }.issubset(kinds)
                )
                self.assertEqual(storage.journal.event_count(TASK_ID), 4)
                observation = storage.objects.get(
                    receipt.invocation_observation_digest,
                    expected_kind="model-invocation-observation",
                )
                self.assertTrue(observation["evidence"]["externalDecision"])

    def test_external_execution_failure_leaves_durable_invocation_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).prepare(
                    task_id=TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                invocation = host(storage).prepare_invocation(
                    prepared, gateway_id="executor:failing-external"
                )
                self.assertEqual(invocation.task_revision, 3)
            # A Provider/caller failure creates no Host event because Host did not execute it.
            with HostStorage(directory) as storage:
                current = storage.journal.get_task(TASK_ID)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.revision, 3)
                self.assertEqual(current.state, TaskState.WAITING)
                self.assertEqual(storage.journal.event_count(TASK_ID), 3)
                self.assertEqual(
                    storage.read_task_event(TASK_ID).event_kind,
                    EventKind.COGNITION_INVOCATION_PREPARED,
                )

    def test_external_decision_is_superseded_if_task_advances_before_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                create_task(storage)
                prepared = host(storage).prepare(
                    task_id=TASK_ID,
                    decision_node_id=DECISION_NODE,
                    request=cognition_request(),
                    token_budget=4_000,
                )
                invocation = host(storage).prepare_invocation(
                    prepared, gateway_id="executor:external-test"
                )
            decision = ScriptedPreferenceAdapter(
                (DecisionKind.OBSERVE_DISPATCH,)
            ).decide(invocation.prepared.context)
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
                with self.assertRaisesRegex(CognitionSuperseded, "revision is 4"):
                    host(storage).admit_decision(
                        invocation, decision, state_reader=admission_state
                    )
                kinds = [value.kind for value in storage.journal.object_refs()]
                self.assertNotIn("model-decision", kinds)
                self.assertNotIn("admitted-decision", kinds)

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
