from __future__ import annotations

from dataclasses import replace
import unittest

from ordivon_host.cognition import (
    CandidateAction,
    CognitionRequest,
    ContextCompiler,
    DecisionAdmission,
    DecisionAdmissionError,
    DecisionKind,
    ModelDecision,
    ScriptedPreferenceAdapter,
)

WORLD = "sha256:" + ("a" * 64)
OTHER_WORLD = "sha256:" + ("b" * 64)
DISPATCH = "dispatch:runtime-job-7"


def continuation_context():
    request = CognitionRequest(
        task_id="task:continue",
        world_digest=WORLD,
        blocks=(),
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
    return ContextCompiler().compile(request, token_budget=4_000)


def mutation_context():
    request = CognitionRequest(
        task_id="task:mutate",
        world_digest=WORLD,
        blocks=(),
        candidates=(
            CandidateAction(
                "action:propose-current",
                DecisionKind.PROPOSE_EFFECT,
                "Propose the guarded mutation.",
                effect_id="effect:new",
                binding_id="binding:new",
                required_world_digest=WORLD,
            ),
            CandidateAction(
                "action:inspect",
                DecisionKind.INSPECT_WORLD,
                "Inspect again before mutation.",
            ),
        ),
    )
    return ContextCompiler().compile(request, token_budget=4_000)


class DecisionAdmissionTests(unittest.TestCase):
    def test_multiple_candidates_can_select_and_admit_observation(self) -> None:
        context = continuation_context()
        decision = ScriptedPreferenceAdapter(
            (DecisionKind.OBSERVE_DISPATCH, DecisionKind.REQUEST_HUMAN)
        ).decide(context)
        admitted = DecisionAdmission().admit(
            context,
            decision,
            current_world_digest=WORLD,
            completed_effect_ids=("effect:completed",),
            unresolved_dispatch_ids=(DISPATCH,),
        )
        self.assertEqual(admitted.action.kind, DecisionKind.OBSERVE_DISPATCH)
        self.assertEqual(admitted.action.dispatch_id, DISPATCH)

    def test_decision_for_another_context_is_rejected(self) -> None:
        context = continuation_context()
        decision = ScriptedPreferenceAdapter((DecisionKind.WAIT,)).decide(context)
        forged = replace(decision, context_digest=OTHER_WORLD)
        with self.assertRaisesRegex(DecisionAdmissionError, "another Context"):
            DecisionAdmission().admit(
                context,
                forged,
                current_world_digest=WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=(DISPATCH,),
            )

    def test_invented_action_is_rejected(self) -> None:
        context = continuation_context()
        decision = ModelDecision(
            context_digest=context.digest,
            action_id="action:invented",
            kind=DecisionKind.WAIT,
            effect_id=None,
            binding_id=None,
            dispatch_id=None,
            required_world_digest=None,
            rationale="Invented by the model.",
        )
        with self.assertRaisesRegex(DecisionAdmissionError, "not one exact"):
            DecisionAdmission().admit(
                context,
                decision,
                current_world_digest=WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=(DISPATCH,),
            )

    def test_world_drift_rejects_previously_valid_mutation(self) -> None:
        context = mutation_context()
        decision = ScriptedPreferenceAdapter((DecisionKind.PROPOSE_EFFECT,)).decide(
            context
        )
        with self.assertRaisesRegex(DecisionAdmissionError, "world requirement is stale"):
            DecisionAdmission().admit(
                context,
                decision,
                current_world_digest=OTHER_WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=(),
            )

    def test_completed_effect_is_rechecked_at_admission_time(self) -> None:
        context = mutation_context()
        decision = ScriptedPreferenceAdapter((DecisionKind.PROPOSE_EFFECT,)).decide(
            context
        )
        with self.assertRaisesRegex(DecisionAdmissionError, "repeat a completed Effect"):
            DecisionAdmission().admit(
                context,
                decision,
                current_world_digest=WORLD,
                completed_effect_ids=("effect:new",),
                unresolved_dispatch_ids=(),
            )

    def test_new_effect_is_forbidden_when_dispatch_became_unresolved(self) -> None:
        context = mutation_context()
        decision = ScriptedPreferenceAdapter((DecisionKind.PROPOSE_EFFECT,)).decide(
            context
        )
        with self.assertRaisesRegex(DecisionAdmissionError, "unresolved Dispatch"):
            DecisionAdmission().admit(
                context,
                decision,
                current_world_digest=WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=(DISPATCH,),
            )

    def test_observe_must_target_current_unresolved_dispatch(self) -> None:
        context = continuation_context()
        decision = ScriptedPreferenceAdapter((DecisionKind.OBSERVE_DISPATCH,)).decide(
            context
        )
        with self.assertRaisesRegex(DecisionAdmissionError, "another Dispatch"):
            DecisionAdmission().admit(
                context,
                decision,
                current_world_digest=WORLD,
                completed_effect_ids=(),
                unresolved_dispatch_ids=("dispatch:other",),
            )


if __name__ == "__main__":
    unittest.main()
