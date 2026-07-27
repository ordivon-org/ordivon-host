from __future__ import annotations

import unittest

from anc_canonical import canonical_digest
from ordivon_host.cognition import (
    BlockKind,
    CandidateAction,
    CognitionRequest,
    CompiledContext,
    ContextCompileError,
    ContextCompiler,
    DecisionKind,
    Freshness,
    block_from_payload,
)

WORLD = "sha256:" + ("a" * 64)


def block(
    name: str,
    kind: BlockKind,
    *,
    required: bool,
    priority: int,
    payload: object,
):
    return block_from_payload(
        block_id=f"context-block:{name}",
        kind=kind,
        priority=priority,
        required=required,
        freshness=Freshness.CURRENT,
        source={"source": name},
        payload=payload,
    )


def candidates() -> tuple[CandidateAction, ...]:
    return (
        CandidateAction(
            "action:observe-original-dispatch",
            DecisionKind.OBSERVE_DISPATCH,
            "Observe the original Runtime Job before any new delivery.",
            dispatch_id="dispatch:runtime-job-7",
        ),
        CandidateAction(
            "action:request-human",
            DecisionKind.REQUEST_HUMAN,
            "Ask a human to resolve the ambiguity.",
        ),
        CandidateAction(
            "action:wait",
            DecisionKind.WAIT,
            "Wait for another external signal.",
        ),
    )


def request(blocks: tuple = ()) -> CognitionRequest:
    return CognitionRequest(
        task_id="task:cognition-test",
        world_digest=WORLD,
        blocks=blocks,
        candidates=candidates(),
        forbidden_effect_ids=("effect:completed",),
        unresolved_dispatch_ids=("dispatch:runtime-job-7",),
    )


class ContextCompilerTests(unittest.TestCase):
    def test_context_blocks_bind_payload_and_source(self) -> None:
        value = block(
            "goal",
            BlockKind.GOAL,
            required=True,
            priority=100,
            payload={"statement": "Recover without duplicate execution."},
        )
        self.assertEqual(value.source_digest, canonical_digest({"source": "goal"}))
        self.assertEqual(value.payload_digest, canonical_digest(value.payload))
        self.assertGreater(value.estimated_tokens, 0)

    def test_budget_selection_is_deterministic_and_priority_ordered(self) -> None:
        required = (
            block(
                "goal",
                BlockKind.GOAL,
                required=True,
                priority=100,
                payload={"statement": "Recover the original Dispatch."},
            ),
            block(
                "world",
                BlockKind.WORLD,
                required=True,
                priority=100,
                payload={"digest": WORLD},
            ),
        )
        high = block(
            "dispatch",
            BlockKind.DISPATCH,
            required=False,
            priority=90,
            payload={"detail": "high-" + ("x" * 400)},
        )
        low = block(
            "history",
            BlockKind.EVIDENCE,
            required=False,
            priority=10,
            payload={"detail": "low-" + ("y" * 400)},
        )
        compiler = ContextCompiler()
        required_context = compiler.compile(request(required), token_budget=100_000)
        high_context = compiler.compile(
            request((*required, high)), token_budget=100_000
        )
        all_context = compiler.compile(
            request((*required, low, high)), token_budget=100_000
        )
        self.assertGreater(high_context.estimated_tokens, required_context.estimated_tokens)
        self.assertGreater(all_context.estimated_tokens, high_context.estimated_tokens)

        bounded = compiler.compile(
            request((*required, low, high)),
            token_budget=high_context.estimated_tokens,
        )
        self.assertEqual(
            bounded.manifest.selected_block_ids,
            (
                "context-block:goal",
                "context-block:world",
                "context-block:dispatch",
            ),
        )
        self.assertEqual(
            bounded.manifest.omitted_block_ids,
            ("context-block:history",),
        )
        self.assertLessEqual(
            bounded.manifest.estimated_tokens,
            bounded.manifest.token_budget,
        )
        repeated = compiler.compile(
            request((*required, low, high)),
            token_budget=high_context.estimated_tokens,
        )
        self.assertEqual(bounded.digest, repeated.digest)
        self.assertEqual(bounded.manifest, repeated.manifest)

    def test_required_blocks_fail_instead_of_being_truncated(self) -> None:
        required = (
            block(
                "goal",
                BlockKind.GOAL,
                required=True,
                priority=100,
                payload={"statement": "x" * 1_000},
            ),
        )
        compiler = ContextCompiler()
        minimum = compiler.compile(request(required), token_budget=100_000)
        with self.assertRaisesRegex(ContextCompileError, "required Context"):
            compiler.compile(
                request(required),
                token_budget=minimum.estimated_tokens - 1,
            )

    def test_forbidden_effect_cannot_be_an_allowed_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden Effect"):
            CognitionRequest(
                task_id="task:bad",
                world_digest=WORLD,
                blocks=(),
                candidates=(
                    CandidateAction(
                        "action:repeat",
                        DecisionKind.PROPOSE_EFFECT,
                        "Repeat completed work.",
                        effect_id="effect:completed",
                        binding_id="binding:repeat",
                        required_world_digest=WORLD,
                    ),
                    CandidateAction(
                        "action:wait",
                        DecisionKind.WAIT,
                        "Wait.",
                    ),
                ),
                forbidden_effect_ids=("effect:completed",),
            )

    def test_candidate_cannot_start_already_stale(self) -> None:
        with self.assertRaisesRegex(ValueError, "already stale"):
            CognitionRequest(
                task_id="task:bad-world",
                world_digest=WORLD,
                blocks=(),
                candidates=(
                    CandidateAction(
                        "action:mutate",
                        DecisionKind.PROPOSE_EFFECT,
                        "Apply a current-world change.",
                        effect_id="effect:new",
                        binding_id="binding:new",
                        required_world_digest="sha256:" + ("b" * 64),
                    ),
                    CandidateAction(
                        "action:wait",
                        DecisionKind.WAIT,
                        "Wait.",
                    ),
                ),
            )

    def test_execution_identity_prefixes_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity must start with effect"):
            CandidateAction(
                "action:bad-effect",
                DecisionKind.PROPOSE_EFFECT,
                "Reject an untyped Effect identity.",
                effect_id="wrong:effect",
                binding_id="binding:valid",
                required_world_digest=WORLD,
            )
        with self.assertRaisesRegex(ValueError, "identity must start with dispatch"):
            CandidateAction(
                "action:bad-dispatch",
                DecisionKind.OBSERVE_DISPATCH,
                "Reject an untyped Dispatch identity.",
                dispatch_id="job:runtime-job-7",
            )

    def test_compiled_context_envelope_detects_tampering(self) -> None:
        compiled = ContextCompiler().compile(request(), token_budget=4_000)
        envelope = compiled.to_dict()
        envelope["digest"] = "sha256:" + ("0" * 64)
        with self.assertRaisesRegex(ValueError, "digest or byte length differs"):
            CompiledContext.from_dict(envelope)

        envelope = compiled.to_dict()
        manifest = envelope["manifest"]
        assert isinstance(manifest, dict)
        manifest["selectedBlockIds"] = ["context-block:forged"]
        with self.assertRaisesRegex(ValueError, "blocks differ from manifest"):
            CompiledContext.from_dict(envelope)


if __name__ == "__main__":
    unittest.main()
