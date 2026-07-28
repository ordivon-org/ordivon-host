#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from ordivon_host import (
    DeterministicReadHost,
    HostStorage,
    ReadTaskPlan,
)
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
        description="Run the deterministic Ordivon Host read slice against a live Runtime."
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ORDIVON_MCP_ENDPOINT", "http://127.0.0.1:8897/mcp"),
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--relative-path", default="README.md")
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    identity = ScenarioIdentity.create("live-read")
    state_root = scenario_state_root(
        args.state_root, prefix="read", identity=identity
    )
    task_token = identity.token
    plan = ReadTaskPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=args.relative_path,
    )
    client = RuntimeClientFactory(
        args.endpoint, token, "ordivon-host-live-read"
    ).client("run")
    clock = scenario_clock_ms
    completed = False
    try:
        with HostStorage(state_root) as storage:
            DeterministicReadHost(storage, client, clock_ms=clock).create(plan)

        step_receipts: list[dict[str, object]] = []
        for _ in range(3):
            with HostStorage(state_root) as storage:
                step = DeterministicReadHost(storage, client, clock_ms=clock).step(
                    plan.task_id
                )
                step_receipts.append(
                    {
                        "revision": step.revision,
                        "frontier": step.frontier,
                        "completed": step.completed,
                    }
                )

        with HostStorage(state_root) as storage:
            projection = storage.journal.get_task(plan.task_id)
            if projection is None or not projection.state.terminal:
                raise AssertionError("live read Task did not reach a terminal state")
            snapshot = storage.read_task_event(plan.task_id)
            if not isinstance(snapshot.data, dict):
                raise AssertionError("terminal Task event data is not an object")
            outcome_digest = snapshot.data.get("outcomeDigest")
            if not isinstance(outcome_digest, str):
                raise AssertionError("terminal Task event omitted outcomeDigest")
            outcome = storage.objects.get(
                outcome_digest,
                expected_kind="task-outcome",
            )
            object_refs = storage.journal.object_refs()
            try:
                client.call_tool(
                    "workspace.get",
                    {"schemaVersion": 1, "workspaceId": plan.workspace_id},
                )
            except RuntimeToolRejected as error:
                runtime_workspace_closed = (
                    error.detail.code == "INVALID_REQUEST"
                    and error.detail.field == "workspaceId"
                    and error.detail.commit_state == "not_committed"
                )
            else:
                runtime_workspace_closed = False
            if not runtime_workspace_closed:
                raise AssertionError("terminal read Task left its Runtime Workspace open")

            receipt = {
                "schemaVersion": 1,
                "kind": "ordivon.host-live-read-receipt",
                "taskId": plan.task_id,
                "goalId": plan.goal_id,
                "workspaceId": plan.workspace_id,
                "sourceRepo": plan.source_repo,
                "sourceRevision": plan.source_revision,
                "relativePath": plan.relative_path,
                "finalState": projection.state.value,
                "finalRevision": projection.revision,
                "hostEventCount": storage.journal.event_count(plan.task_id),
                "objectRefCount": len(object_refs),
                "objectKinds": sorted({reference.kind for reference in object_refs}),
                "stepReceipts": step_receipts,
                "outcomeDigest": outcome_digest,
                "outcome": outcome,
                "freshStorageOpenPerStep": True,
                "providerSessionPersisted": False,
                "runtimeWorkspaceClosed": runtime_workspace_closed,
                "stateRoot": str(state_root) if args.keep_state else None,
            }
            completed = True
            emit_receipt(receipt)
    finally:
        if not completed:
            try:
                client.call_tool(
                    "workspace.close",
                    {
                        "schemaVersion": 1,
                        "workspaceId": plan.workspace_id,
                        "force": True,
                    },
                )
            except RuntimeToolRejected:
                pass
        cleanup_state_root(state_root, keep=args.keep_state)


if __name__ == "__main__":
    main()
