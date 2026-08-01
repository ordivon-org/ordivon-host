from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host.effects import EffectLifecycleHost
from ordivon_host import (
    ArtifactRef,
    DeliveryUncertain,
    DispatchEnvelope,
    HostStorage,
    ObservationEnvelope,
    StateRef,
    TaskDescriptor,
    TaskOutcome,
    TaskState,
    VerificationReceipt,
    VerificationResultItem,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1
        return self.value


class ResponseLossExecutor:
    executor_id = "executor:game-world-v1"

    def __init__(self) -> None:
        self.committed: dict[str, ObservationEnvelope] = {}
        self.deliveries = 0
        self.observations = 0

    def deliver(self, dispatch, request):
        self.deliveries += 1
        observation = ObservationEnvelope(
            dispatch_id=dispatch.dispatch_id,
            executor_id=self.executor_id,
            status="succeeded",
            payload_digest=canonical_digest(
                {"worldRevision": 1, "requestDigest": canonical_digest(request)}
            ),
            evidence_refs=(
                ArtifactRef(
                    ref="world-event:run:test:r1",
                    kind="game-world-event",
                    digest=canonical_digest({"event": 1}),
                ),
            ),
        )
        self.committed[dispatch.idempotency_key] = observation
        raise DeliveryUncertain("response lost after World commit")

    def observe(self, dispatch, request):
        self.observations += 1
        return self.committed.get(dispatch.idempotency_key)


class EffectLifecycleTests(unittest.TestCase):
    def test_unknown_delivery_recovers_original_effect_without_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            clock = Clock()
            descriptor = TaskDescriptor(
                task_id="task:game:single-actor",
                goal_id="goal:game:mission",
                workload_id="ordivon.game.actor-turn.v1",
                assignee_ref="actor:engineer-01",
                provider_policy_ref="provider-policy:fixture",
                domain_ref="game-run:run:test",
            )
            request = {
                "runId": "run:test",
                "expectedWorldRevision": 0,
                "commandId": "command:run:test:r0",
                "command": {"kind": "wait", "actorId": "engineer-01"},
            }
            effect = {
                "schemaVersion": 1,
                "kind": "ordivon.game.world-effect",
                "effectId": "effect:game:single-actor:r0",
                "runId": "run:test",
            }
            dispatch = DispatchEnvelope(
                dispatch_id="dispatch:game:single-actor:r0",
                effect_id=effect["effectId"],
                executor_id="executor:game-world-v1",
                request_digest=canonical_digest(request),
                idempotency_key="command:run:test:r0",
                required_state_refs=(
                    StateRef(
                        "game-world:run:test",
                        canonical_digest({"runId": "run:test", "revision": 0}),
                    ),
                ),
                expected_observation_kind="ordivon.game.world-event-observation.v1",
            )
            executor = ResponseLossExecutor()

            with HostStorage(root) as storage:
                host = EffectLifecycleHost(storage, clock_ms=clock)
                host.create_task(
                    descriptor,
                    frontier="node:game:single-actor:prepare",
                )
                prepared = host.prepare(
                    task_id=descriptor.task_id,
                    prepare_frontier="node:game:single-actor:prepare",
                    reconcile_frontier="node:game:single-actor:reconcile",
                    verify_frontier="node:game:single-actor:verify",
                    result_frontier="node:game:single-actor:result",
                    effect=effect,
                    request=request,
                    dispatch=dispatch,
                )
                unknown = host.deliver(prepared, executor)
                self.assertEqual(unknown.state, TaskState.WAITING)
                self.assertEqual(executor.deliveries, 1)

            with HostStorage(root) as storage:
                fresh = EffectLifecycleHost(storage, clock_ms=clock)
                observed = fresh.reconcile(descriptor.task_id, executor)
                self.assertEqual(observed.state, TaskState.VERIFYING)
                self.assertTrue(observed.reconciled)
                self.assertEqual(executor.deliveries, 1)
                self.assertEqual(executor.observations, 1)

                def verify(prepared, observation):
                    return VerificationReceipt(
                        dispatch_id=prepared.dispatch.dispatch_id,
                        method="game-world-event.v1",
                        accepted=True,
                        observation_digest=canonical_digest(observation.to_dict()),
                        result_items=(
                            VerificationResultItem(
                                subject_ref=descriptor.task_id,
                                decision_digest=canonical_digest({"decision": "wait"}),
                                status="succeeded",
                                reason=None,
                                evidence_digest=observation.payload_digest,
                            ),
                        ),
                    )

                verified = fresh.verify(descriptor.task_id, verify)
                self.assertEqual(verified.state, TaskState.READY)
                self.assertIsNotNone(verified.verification_digest)
                outcome = TaskOutcome(
                    task_id=descriptor.task_id,
                    goal_id=descriptor.goal_id,
                    status="completed",
                    verification_digest=verified.verification_digest,
                    artifact_refs=(),
                )
                completed = fresh.complete(descriptor.task_id, outcome)
                self.assertEqual(completed.state, TaskState.COMPLETED)
                self.assertEqual(completed.outcome_digest, canonical_digest(outcome.to_dict()))
                self.assertEqual(storage.read_task_descriptor(descriptor.task_id), descriptor)


if __name__ == "__main__":
    unittest.main()
