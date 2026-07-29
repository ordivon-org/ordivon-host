from __future__ import annotations

import unittest

from ordivon_host.authority import CapabilityProfileAuthorizer, OWNER_TRUSTED_PROFILE_ID
from ordivon_host.cognition import (
    ActionProposal,
    BlockKind,
    ConsequenceClass,
    DecisionRequest,
    Freshness,
    LoweredMutationProposal,
    OpenCognitionRequest,
    OpenContextCompiler,
    ProposalIntent,
    ProposalRejection,
    ProposalTarget,
    RepositoryMutationProposalCompiler,
    ResourceBinding,
    Reversibility,
    block_from_payload,
)


WORLD = "sha256:" + ("a" * 64)
REVISION = "b" * 40
RESOURCE = "repository:round1-fixture"
OWNER = "participant:local-owner"


def context():
    request = OpenCognitionRequest(
        task_id="task:round1-maintenance",
        world_digest=WORLD,
        blocks=(
            block_from_payload(
                block_id="context-block:goal",
                kind=BlockKind.GOAL,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source={"goalRevision": 2},
                payload={"goal": "Adopt catalog v2 while retaining compatibility."},
            ),
        ),
        capability_profile_id=OWNER_TRUSTED_PROFILE_ID,
        responsible_participant_ref=OWNER,
        resources=(ResourceBinding(RESOURCE, REVISION, OWNER),),
    )
    return OpenContextCompiler().compile(request, token_budget=4_000)


def proposal(
    compiled,
    *,
    revision: str = REVISION,
    consequence: ConsequenceClass = ConsequenceClass.PRIVATE_REVERSIBLE,
    participants: tuple[str, ...] = (),
):
    return ActionProposal(
        proposal_id="proposal:round1-client-change",
        task_id="task:round1-maintenance",
        context_digest=compiled.digest,
        intent=ProposalIntent.CHANGE,
        target=ProposalTarget(
            kind="repository-file",
            resource_ref=RESOURCE,
            revision=revision,
            selector={
                "relativePath": "client.py",
                "content": "SCHEMA_VERSION = 1\n",
            },
        ),
        rationale="The current Tool contract requires schemaVersion one.",
        preconditions=("Repository and Tool catalog revisions remain current.",),
        affected_resource_refs=(RESOURCE,),
        affected_participant_refs=participants,
        reversibility=Reversibility.REVERSIBLE,
        consequence_class=consequence,
        requested_profile_id=OWNER_TRUSTED_PROFILE_ID,
        candidate_method="guarded-mutation",
        expected_result="The client emits schemaVersion one.",
        verification_plan="Reread client.py and run the hidden acceptance test.",
    )


class MutationProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = RepositoryMutationProposalCompiler(CapabilityProfileAuthorizer())

    def test_private_version_bound_change_lowers_to_guarded_mutation(self) -> None:
        compiled = context()
        result = self.compiler.compile(
            compiled,
            proposal(compiled),
            goal_id="goal:round1-maintenance",
            child_task_id="task:round1-maintenance:mutation",
            workspace_id="round1-maintenance-mutation",
            source_repo="/root/projects/round1-fixture",
        )
        self.assertIsInstance(result, LoweredMutationProposal)
        assert isinstance(result, LoweredMutationProposal)
        self.assertEqual(result.plan.source_revision, REVISION)
        self.assertEqual(result.plan.relative_path, "client.py")
        self.assertEqual(result.plan.content, "SCHEMA_VERSION = 1\n")
        self.assertTrue(result.capability_decision.allowed)

    def test_shared_change_routes_to_responsible_participant(self) -> None:
        compiled = context()
        result = self.compiler.compile(
            compiled,
            proposal(
                compiled,
                consequence=ConsequenceClass.SHARED_REVERSIBLE,
                participants=("participant:other",),
            ),
            goal_id="goal:round1-maintenance",
            child_task_id="task:unused",
            workspace_id="unused",
            source_repo="/root/projects/round1-fixture",
        )
        self.assertIsInstance(result, DecisionRequest)
        assert isinstance(result, DecisionRequest)
        self.assertEqual(result.recipient_ref, OWNER)

    def test_stale_revision_is_structured_rejection(self) -> None:
        compiled = context()
        result = self.compiler.compile(
            compiled,
            proposal(compiled, revision="c" * 40),
            goal_id="goal:round1-maintenance",
            child_task_id="task:unused",
            workspace_id="unused",
            source_repo="/root/projects/round1-fixture",
        )
        self.assertIsInstance(result, ProposalRejection)
        assert isinstance(result, ProposalRejection)
        self.assertEqual(result.code, "stale_resource")


if __name__ == "__main__":
    unittest.main()
