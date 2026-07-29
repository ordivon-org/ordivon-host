from __future__ import annotations

import argparse
from pathlib import Path
import itertools
import json
import tempfile

from anc_canonical import canonical_bytes, canonical_digest

from ordivon_host import HostKernel, HostStorage, TaskState, operator_handoff
from ordivon_host.authority import CapabilityProfileAuthorizer, OWNER_TRUSTED_PROFILE_ID
from ordivon_host.cognition import (
    ActionProposal,
    BlockKind,
    ClaimStatus,
    ConsequenceClass,
    ContextSourceBinding,
    DecisionRequestLifecycle,
    DecisionResponse,
    DecisionResponseKind,
    EvidenceItem,
    EvidenceRichDecisionRequest,
    Freshness,
    LoweredMutationProposal,
    OpenCognitionRequest,
    OpenContextCompiler,
    ProposalIntent,
    ProposalTarget,
    RepositoryMutationProposalCompiler,
    ResourceBinding,
    Reversibility,
    SelectionMethod,
    TrustClass,
    block_from_payload,
    evaluate_source,
    provenance_block,
)
from ordivon_host.domain import EventKind


WORLD_DIGEST = "sha256:" + ("a" * 64)
REVISION = "b" * 40
RESOURCE = "repository:round1-fixture"
OWNER = "participant:local-owner"


def build_receipt(source_revision: str) -> dict[str, object]:
    source_payload = {"schemaVersion": 2, "const": 1}
    source = ContextSourceBinding(
        source_ref="source:tool-catalog",
        source_revision="catalog-v2",
        payload_digest=canonical_digest(source_payload),
        observed_at_ms=1_000,
        trust_class=TrustClass.AUTHORITATIVE,
        claim_status=ClaimStatus.FACT,
        selection_method=SelectionMethod.DIRECT,
        invalidation_keys=("catalog-digest", "repository-revision"),
        selected_by="selector:round1-host-conformance",
    )
    block = provenance_block(
        block_id="context-block:catalog",
        kind=BlockKind.CONSTRAINT,
        priority=100,
        required=True,
        freshness=Freshness.CURRENT,
        payload=source_payload,
        binding=source,
    )
    current_validity = evaluate_source(
        source,
        bound_revisions={"catalog-digest": "v2", "repository-revision": REVISION},
        current_revisions={"catalog-digest": "v2", "repository-revision": REVISION},
    )
    stale_validity = evaluate_source(
        source,
        bound_revisions={"catalog-digest": "v2", "repository-revision": REVISION},
        current_revisions={"catalog-digest": "v3", "repository-revision": "c" * 40},
    )

    decision = EvidenceRichDecisionRequest(
        request_id="decision-request:round1-shared-effect",
        task_id="task:round1-maintenance",
        proposal_digest="sha256:" + ("d" * 64),
        recipient_ref=OWNER,
        reason_code="shared-resource-commitment-required",
        summary="Commit the bounded shared effect.",
        alternatives=("approve", "retain-local", "request-review"),
        evidence=(
            EvidenceItem(
                evidence_ref="evidence:round1-tests",
                digest="sha256:" + ("e" * 64),
                summary="Deterministic tests passed.",
            ),
        ),
        unresolved_claims=("claim:remote-ci-not-observed",),
        consequence_class="shared-reversible",
        reversibility="conditionally-reversible",
        authority_impact="uses repository publication authority",
        budget_impact="one bounded publication",
        cost_of_delay="integration remains blocked",
        world_revision="world-revision:2",
        expires_at_ms=2_000,
    )
    lifecycle = DecisionRequestLifecycle(decision).respond(
        DecisionResponse(
            response_id="decision-response:round1-shared-effect",
            request_id=decision.request_id,
            request_digest=decision.digest,
            responder_ref=OWNER,
            response=DecisionResponseKind.APPROVE,
            world_revision="world-revision:2",
            recorded_at_ms=1_500,
            rationale="The bounded evidence supports this commitment.",
        ),
        current_world_revision="world-revision:2",
        now_ms=1_500,
    )

    context = OpenContextCompiler().compile(
        OpenCognitionRequest(
            task_id="task:round1-maintenance",
            world_digest=WORLD_DIGEST,
            blocks=(
                block_from_payload(
                    block_id="context-block:goal",
                    kind=BlockKind.GOAL,
                    priority=100,
                    required=True,
                    freshness=Freshness.CURRENT,
                    source={"goalRevision": 2},
                    payload={"goal": "Adopt catalog v2 and retain compatibility."},
                ),
            ),
            capability_profile_id=OWNER_TRUSTED_PROFILE_ID,
            responsible_participant_ref=OWNER,
            resources=(ResourceBinding(RESOURCE, REVISION, OWNER),),
        ),
        token_budget=4_000,
    )
    proposal = ActionProposal(
        proposal_id="proposal:round1-client-change",
        task_id="task:round1-maintenance",
        context_digest=context.digest,
        intent=ProposalIntent.CHANGE,
        target=ProposalTarget(
            kind="repository-file",
            resource_ref=RESOURCE,
            revision=REVISION,
            selector={"relativePath": "client.py", "content": "SCHEMA_VERSION = 1\n"},
        ),
        rationale="The new Tool contract requires schemaVersion one.",
        preconditions=("Repository and catalog revisions remain current.",),
        affected_resource_refs=(RESOURCE,),
        affected_participant_refs=(),
        reversibility=Reversibility.REVERSIBLE,
        consequence_class=ConsequenceClass.PRIVATE_REVERSIBLE,
        requested_profile_id=OWNER_TRUSTED_PROFILE_ID,
        candidate_method="guarded-mutation",
        expected_result="The client emits schemaVersion one.",
        verification_plan="Reread the file and run acceptance tests.",
    )
    lowered = RepositoryMutationProposalCompiler(CapabilityProfileAuthorizer()).compile(
        context,
        proposal,
        goal_id="goal:round1-maintenance",
        child_task_id="task:round1-maintenance:mutation",
        workspace_id="round1-maintenance-mutation",
        source_repo="/root/projects/round1-fixture",
    )
    if not isinstance(lowered, LoweredMutationProposal):
        raise RuntimeError("Round 1 mutation proposal did not lower")

    with tempfile.TemporaryDirectory() as directory:
        with HostStorage(directory) as storage:
            kernel = HostKernel(
                storage,
                clock_ms=itertools.count(2_000).__next__,
                owner_id="host:round1-conformance",
            )
            created = kernel.create_task(
                event_id="event:round1-handoff:create",
                kind=EventKind.TASK_CREATED,
                task_id="task:round1-handoff",
                goal_id="goal:round1-handoff",
                payload={},
                frontier=("node:round1:dispatch",),
            ).projection
            with kernel.locked_task(
                created.task_id,
                expected_revision=1,
                expected_state=TaskState.READY,
                expected_frontier=created.ready_frontier,
            ) as locked:
                locked.commit(
                    event_id="event:round1-handoff:unknown",
                    kind=EventKind.RUNTIME_OUTCOME_UNKNOWN,
                    payload={
                        "effectDigest": "sha256:" + ("f" * 64),
                        "dispatchDigest": "sha256:" + ("1" * 64),
                    },
                    state=TaskState.WAITING,
                    frontier=("node:round1:reconcile",),
                )
            handoff = operator_handoff(storage, created.task_id)

    payload: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "ordivon.round1-host-conformance",
        "sourceRevision": source_revision,
        "context": {
            "blockDigest": canonical_digest(block.to_dict()),
            "currentValid": current_validity.valid,
            "staleValid": stale_validity.valid,
            "staleReasons": list(stale_validity.reasons),
        },
        "decision": {
            "requestDigest": decision.digest,
            "lifecycleDigest": lifecycle.digest,
            "response": lifecycle.response.response.value if lifecycle.response else None,
        },
        "mutation": {
            "proposalDigest": proposal.digest,
            "loweredPlan": lowered.plan.to_dict(),
            "capabilityPolicy": lowered.capability_decision.policy_id,
        },
        "handoff": handoff.to_dict(),
        "protocolPromoted": False,
        "defaultOpenProposalHostBroadened": False,
    }
    payload["receiptDigest"] = canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if len(args.source_revision) != 40:
        raise SystemExit("source revision must be a 40-character Git SHA")
    payload = build_receipt(args.source_revision)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(payload) + b"\n")
    print(json.dumps({"receiptDigest": payload["receiptDigest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
