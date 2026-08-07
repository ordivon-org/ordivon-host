#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import time

from ordivon_host import HostStorage
from ordivon_host.authority import (
    CapabilityProfileAuthorizer,
    OWNER_TRUSTED_PROFILE_ID,
)
from ordivon_host.cognition import (
    BlockKind,
    Freshness,
    OpenCognitionRequest,
    OpenProposalHost,
    ProposalResolutionKind,
    ResourceBinding,
    block_from_payload,
)
from ordivon_host.legacy_provider_execution import CodexCliProposalAdapter
from ordivon_host.domain import StaticRepositoryResolver
from ordivon_host.engine import DeterministicReadHost
from ordivon_host.runtime import RuntimeToolRejected
from ordivon_host.testing import (
    RuntimeClientFactory,
    ScenarioIdentity,
    cleanup_state_root,
    emit_receipt,
    load_scenario_token,
    scenario_clock_ms,
    scenario_state_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one live open ActionProposal through Host and Runtime."
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ORDIVON_MCP_ENDPOINT", "http://127.0.0.1:8897/mcp"),
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--relative-path", default="README.md")
    parser.add_argument("--codex-model")
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    identity = ScenarioIdentity.create("open-proposal")
    state_root = scenario_state_root(
        args.state_root,
        prefix="open-proposal",
        identity=identity,
    )
    token = load_scenario_token()
    client = RuntimeClientFactory(
        args.endpoint,
        token,
        "ordivon-host-live-open-proposal",
    ).client("run")
    source_repo = Path(args.source_repo).resolve()
    repository_id = "repository:live-open-proposal"
    owner_ref = "participant:local-owner"
    proposal_node = f"node:{identity.token}:open-proposal"
    resolver = StaticRepositoryResolver({repository_id: source_repo})
    request = OpenCognitionRequest(
        task_id=identity.task_id,
        world_digest="sha256:" + ("a" * 64),
        blocks=(
            block_from_payload(
                block_id=f"context-block:{identity.token}:goal",
                kind=BlockKind.GOAL,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source={"goalId": identity.goal_id},
                payload={
                    "statement": (
                        f"Read {args.relative_path} from the available repository and return "
                        "verified current content without choosing from a prebuilt action menu."
                    )
                },
            ),
        ),
        capability_profile_id=OWNER_TRUSTED_PROFILE_ID,
        responsible_participant_ref=owner_ref,
        resources=(
            ResourceBinding(repository_id, args.source_revision, owner_ref),
        ),
    )
    completed = False
    try:
        with HostStorage(state_root) as storage:
            host = OpenProposalHost(
                storage,
                client,
                clock_ms=scenario_clock_ms,
                repository_resolver=resolver,
            )
            host.create_task(
                task_id=identity.task_id,
                goal_id=identity.goal_id,
                proposal_node_id=proposal_node,
            )
            context_started = time.monotonic()
            prepared = host.prepare(
                task_id=identity.task_id,
                proposal_node_id=proposal_node,
                request=request,
                token_budget=8_000,
            )
            context_prepare_ms = round((time.monotonic() - context_started) * 1000)
            context_digest = prepared.context.digest
            context_byte_length = prepared.context.byte_length
            context_estimated_tokens = prepared.context.estimated_tokens
            context_has_action_menu = "allowedActions" in prepared.context.payload

        adapter = CodexCliProposalAdapter(
            working_directory=source_repo,
            model=args.codex_model,
            timeout_seconds=240,
        )
        with HostStorage(state_root) as storage:
            host = OpenProposalHost(
                storage,
                client,
                clock_ms=scenario_clock_ms,
                repository_resolver=resolver,
            )
            prepared = host.cognition.load_prepared(identity.task_id)
            invocation = host.prepare_invocation(
                prepared, executor_id=adapter.gateway_id
            )
            proposed = adapter.invoke(prepared.context)
            admission_started = time.monotonic()
            receipt = host.admit_proposal(
                invocation,
                proposed,
                evidence=adapter.evidence_metadata() or {},
            )
            proposal_admission_ms = round(
                (time.monotonic() - admission_started) * 1000
            )
            if receipt.resolution_kind is not ProposalResolutionKind.LOWERED:
                raise AssertionError(
                    f"live ActionProposal was not lowered: {receipt.resolution_kind.value}"
                )
            if receipt.child_task_id is None:
                raise AssertionError("lowered ActionProposal omitted child Task")
            child_task_id = receipt.child_task_id
            proposal_value = storage.objects.get(
                receipt.proposal_object_digest,
                expected_kind="action-proposal",
            )

        profiles = CapabilityProfileAuthorizer()
        step_receipts: list[dict[str, object]] = []
        for _ in range(3):
            with HostStorage(state_root) as storage:
                step_started = time.monotonic()
                step = DeterministicReadHost(
                    storage,
                    client,
                    clock_ms=scenario_clock_ms,
                    repository_resolver=resolver,
                    authorizer=profiles.bind(OWNER_TRUSTED_PROFILE_ID),
                ).step(child_task_id)
                step_elapsed_ms = round((time.monotonic() - step_started) * 1000)
                step_receipts.append(
                    {
                        "revision": step.revision,
                        "frontier": step.frontier,
                        "completed": step.completed,
                        "elapsedMs": step_elapsed_ms,
                    }
                )

        with HostStorage(state_root) as storage:
            parent = OpenProposalHost(
                storage,
                client,
                clock_ms=scenario_clock_ms,
                repository_resolver=resolver,
            ).reconcile(identity.task_id)
            if not parent.state.terminal:
                raise AssertionError("open proposal parent Task did not complete")
            child = storage.journal.get_task(child_task_id)
            if child is None or not child.state.terminal:
                raise AssertionError("open proposal child Task did not complete")
            child_snapshot = storage.read_task_event(child_task_id)
            if not isinstance(child_snapshot.data, dict):
                raise AssertionError("child terminal event data is invalid")
            child_outcome_digest = child_snapshot.data.get("outcomeDigest")
            if not isinstance(child_outcome_digest, str):
                raise AssertionError("child terminal event omitted outcomeDigest")
            child_outcome = storage.objects.get(
                child_outcome_digest, expected_kind="task-outcome"
            )
            if not isinstance(child_outcome, dict):
                raise AssertionError("child outcome is invalid")
            workspace_id = child_outcome.get("workspaceId")
            if not isinstance(workspace_id, str):
                raise AssertionError("child outcome omitted Workspace identity")
            try:
                client.call_tool(
                    "workspace.get",
                    {"schemaVersion": 1, "workspaceId": workspace_id},
                )
            except RuntimeToolRejected as error:
                runtime_workspace_closed = (
                    error.detail.code == "INVALID_REQUEST"
                    and error.detail.field == "workspaceId"
                    and error.detail.commit_state == "not_committed"
                )
            else:
                runtime_workspace_closed = False
            checks = {
                "openContextHasNoActionMenu": not context_has_action_menu,
                "modelProposedAction": isinstance(proposal_value, dict),
                "hostLoweredProposal": receipt.resolution_kind.value == "lowered",
                "hostGeneratedChildTask": child_task_id != identity.task_id,
                "freshStorageOpenPerStep": True,
                "parentCompleted": parent.state.value == "completed",
                "childCompleted": child.state.value == "completed",
                "runtimeWorkspaceClosed": runtime_workspace_closed,
            }
            if not all(checks.values()):
                raise AssertionError(f"live open proposal checks failed: {checks}")
            emit_receipt(
                {
                    "schemaVersion": 1,
                    "kind": "ordivon.host-live-open-proposal",
                    "capturedAt": datetime.now(timezone.utc).isoformat(),
                    "taskId": identity.task_id,
                    "goalId": identity.goal_id,
                    "context": {
                        "digest": context_digest,
                        "byteLength": context_byte_length,
                        "estimatedTokens": context_estimated_tokens,
                        "prepareElapsedMs": context_prepare_ms,
                        "hasActionMenu": context_has_action_menu,
                    },
                    "proposalDigest": receipt.proposal_digest,
                    "proposal": proposal_value,
                    "proposalAdapter": adapter.evidence_metadata(),
                    "proposalAdmissionElapsedMs": proposal_admission_ms,
                    "resolutionKind": receipt.resolution_kind.value,
                    "childTaskId": child_task_id,
                    "parentFinalState": parent.state.value,
                    "parentFinalRevision": parent.revision,
                    "childFinalState": child.state.value,
                    "childFinalRevision": child.revision,
                    "stepReceipts": step_receipts,
                    "hostEventCount": storage.journal.event_count(identity.task_id),
                    "childEventCount": storage.journal.event_count(child_task_id),
                    "checks": checks,
                    "stateRoot": str(state_root) if args.keep_state else None,
                }
            )
            completed = True
    finally:
        cleanup_state_root(state_root, keep=args.keep_state or not completed)


if __name__ == "__main__":
    main()
