from __future__ import annotations

import unittest

from ordivon_host.cognition import (
    DecisionRequestLifecycle,
    DecisionResponse,
    DecisionResponseKind,
    EvidenceItem,
    EvidenceRichDecisionRequest,
)


DIGEST = "sha256:" + ("a" * 64)
EVIDENCE_DIGEST = "sha256:" + ("b" * 64)
WORLD = "revision:world-v1"


def request(*, expires_at_ms: int | None = 2_000) -> EvidenceRichDecisionRequest:
    return EvidenceRichDecisionRequest(
        request_id="decision-request:publish-change",
        task_id="task:publish-change",
        proposal_digest=DIGEST,
        recipient_ref="participant:repository-owner",
        reason_code="shared-resource-commitment-required",
        summary="Publish a reversible change to a shared branch.",
        alternatives=("publish", "retain-local", "request-review"),
        evidence=(
            EvidenceItem(
                evidence_ref="evidence:test-results",
                digest=EVIDENCE_DIGEST,
                summary="All deterministic tests passed.",
            ),
        ),
        unresolved_claims=("claim:external-ci-not-observed",),
        consequence_class="shared-reversible",
        reversibility="conditionally-reversible",
        authority_impact="uses the repository owner's publication authority",
        budget_impact="one remote push",
        cost_of_delay="delays integration but does not invalidate local work",
        world_revision=WORLD,
        expires_at_ms=expires_at_ms,
    )


def response(
    item: EvidenceRichDecisionRequest,
    *,
    responder: str = "participant:repository-owner",
    world: str = WORLD,
    kind: DecisionResponseKind = DecisionResponseKind.APPROVE,
    replacement: str | None = None,
) -> DecisionResponse:
    return DecisionResponse(
        response_id="decision-response:publish-change-r1",
        request_id=item.request_id,
        request_digest=item.digest,
        responder_ref=responder,
        response=kind,
        world_revision=world,
        recorded_at_ms=1_500,
        rationale="The evidence is sufficient for the bounded publication.",
        replacement_proposal_digest=replacement,
    )


class DecisionRequestLifecycleTests(unittest.TestCase):
    def test_correct_recipient_can_approve_current_request(self) -> None:
        item = request()
        lifecycle = DecisionRequestLifecycle(item)
        resolved = lifecycle.respond(
            response(item),
            current_world_revision=WORLD,
            now_ms=1_500,
        )
        self.assertEqual(resolved.revision, 2)
        self.assertEqual(resolved.response.response, DecisionResponseKind.APPROVE)

    def test_stale_world_and_wrong_participant_are_rejected(self) -> None:
        item = request()
        lifecycle = DecisionRequestLifecycle(item)
        with self.assertRaisesRegex(ValueError, "world revision is stale"):
            lifecycle.respond(
                response(item),
                current_world_revision="revision:world-v2",
                now_ms=1_500,
            )
        with self.assertRaisesRegex(ValueError, "another participant"):
            lifecycle.respond(
                response(item, responder="participant:other"),
                current_world_revision=WORLD,
                now_ms=1_500,
            )

    def test_expired_or_revoked_request_cannot_be_used(self) -> None:
        item = request(expires_at_ms=1_000)
        with self.assertRaisesRegex(ValueError, "expired"):
            DecisionRequestLifecycle(item).respond(
                response(item),
                current_world_revision=WORLD,
                now_ms=1_500,
            )
        active = request(expires_at_ms=None)
        revoked = DecisionRequestLifecycle(active).revoke(
            now_ms=1_200,
            reason="Repository revision changed.",
        )
        with self.assertRaisesRegex(ValueError, "revoked"):
            revoked.respond(
                response(active),
                current_world_revision=WORLD,
                now_ms=1_500,
            )

    def test_modify_requires_replacement_proposal(self) -> None:
        item = request()
        with self.assertRaisesRegex(ValueError, "replacement Proposal"):
            response(item, kind=DecisionResponseKind.MODIFY)
        modified = response(
            item,
            kind=DecisionResponseKind.MODIFY,
            replacement="sha256:" + ("c" * 64),
        )
        resolved = DecisionRequestLifecycle(item).respond(
            modified,
            current_world_revision=WORLD,
            now_ms=1_500,
        )
        self.assertEqual(resolved.response.response, DecisionResponseKind.MODIFY)


if __name__ == "__main__":
    unittest.main()
