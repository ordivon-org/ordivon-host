#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os

from anc_canonical import JsonValue
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    TaskState,
)
from ordivon_host.runtime import RuntimeToolRejected
from ordivon_host.testing import (
    DropFirstSuccessfulExecResponse,
    RuntimeClientFactory,
    ScenarioIdentity,
    cleanup_state_root,
    emit_receipt,
    jobs_for_request,
    load_scenario_token,
    scenario_clock_ms,
    scenario_state_root,
    workspace_absent,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one guarded mutation, drop the successful workspace.exec response, "
            "and recover by observing the original Runtime Job."
        )
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ORDIVON_MCP_ENDPOINT", "http://127.0.0.1:8897/mcp"),
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--relative-path")
    parser.add_argument(
        "--content",
        default="one guarded Runtime delivery recovered after response loss\n",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    identity = ScenarioIdentity.create("live-mutation")
    task_token = identity.token
    relative_path = args.relative_path or f"host-h4-proof-{identity.stamp_ms}.txt"
    state_root = scenario_state_root(
        args.state_root, prefix="mutation", identity=identity
    )
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=relative_path,
        content=args.content,
    )

    factory = RuntimeClientFactory(
        args.endpoint, token, "ordivon-host-live-mutation"
    )
    client = factory.client
    clock = scenario_clock_ms

    completed = False
    lossy: DropFirstSuccessfulExecResponse | None = None
    try:
        with HostStorage(state_root) as storage:
            GuardedMutationHost(storage, client("create"), clock_ms=clock).create(plan)

        with HostStorage(state_root) as storage:
            opened = GuardedMutationHost(
                storage, client("open"), clock_ms=clock
            ).open_workspace(plan.task_id)

        with HostStorage(state_root) as storage:
            prepared = GuardedMutationHost(
                storage, client("prepare"), clock_ms=clock
            ).prepare(plan.task_id)
            dispatch_id = prepared.dispatch.dispatch_id
            client_request_id = prepared.dispatch.client_request_id
            request_digest = prepared.dispatch.request_digest

        lossy = DropFirstSuccessfulExecResponse(client("deliver"))
        with HostStorage(state_root) as storage:
            recovered_prepared = GuardedMutationHost(
                storage, lossy, clock_ms=clock
            ).load_prepared(plan.task_id)
            unknown = GuardedMutationHost(
                storage, lossy, clock_ms=clock
            ).deliver(recovered_prepared)
        if not lossy.response_dropped or unknown.state is not TaskState.WAITING:
            raise AssertionError("live mutation did not enter UNKNOWN reconciliation")

        reconciliation_receipts: list[dict[str, JsonValue]] = []
        reconciled = None
        for index in range(10):
            with HostStorage(state_root) as storage:
                result = GuardedMutationHost(
                    storage,
                    client(f"reconcile-{index}"),
                    clock_ms=clock,
                ).reconcile(plan.task_id, wait_ms=30_000)
                reconciliation_receipts.append(
                    {
                        "revision": result.revision,
                        "state": result.state.value,
                        "frontier": result.frontier,
                        "jobId": result.job_id,
                    }
                )
                if result.state is TaskState.VERIFYING:
                    reconciled = result
                    break
        if reconciled is None or not reconciled.job_id:
            raise AssertionError("original Runtime Job did not reach verification")

        with HostStorage(state_root) as storage:
            verified = GuardedMutationHost(
                storage, client("verify"), clock_ms=clock
            ).verify(plan.task_id)

        with HostStorage(state_root) as storage:
            closed = GuardedMutationHost(
                storage, client("close"), clock_ms=clock
            ).close(plan.task_id)

        audit_client = client("audit")
        jobs = jobs_for_request(audit_client, client_request_id)
        runtime_workspace_closed = workspace_absent(
            audit_client, plan.workspace_id
        )
        with HostStorage(state_root) as storage:
            projection = storage.journal.get_task(plan.task_id)
            if projection is None:
                raise AssertionError("live mutation Task projection disappeared")
            snapshot = storage.read_task_event(plan.task_id)
            data = snapshot.data
            if not isinstance(data, dict):
                raise AssertionError("terminal mutation event data is not an object")
            outcome_digest = data.get("outcomeDigest")
            if not isinstance(outcome_digest, str):
                raise AssertionError("terminal mutation event omitted outcomeDigest")
            outcome = storage.objects.get(
                outcome_digest, expected_kind="task-outcome"
            )
            refs = storage.journal.object_refs()
            checks = {
                "responseDroppedAfterExecAdmission": lossy.response_dropped,
                "unknownStatePersisted": unknown.revision == 4,
                "freshStorageOpenPerStage": True,
                "oneWorkspaceExecInvocation": lossy.calls.count("workspace.exec") == 1,
                "oneRuntimeJobForClientRequestId": len(jobs) == 1,
                "originalRuntimeJobObserved": jobs[0].get("jobId") == reconciled.job_id,
                "noRedispatchAfterUnknown": lossy.calls.count("workspace.exec") == 1,
                "exactContentVerified": verified.state is TaskState.READY,
                "taskCompleted": (
                    closed.completed
                    and projection.state is TaskState.COMPLETED
                    and projection.revision == 7
                ),
                "runtimeWorkspaceClosed": runtime_workspace_closed,
                "noProviderSessionPersisted": True,
            }
            if not all(checks.values()):
                raise AssertionError(f"live guarded mutation checks failed: {checks}")
            receipt: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.host-live-guarded-mutation",
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "taskId": plan.task_id,
                "goalId": plan.goal_id,
                "workspaceId": plan.workspace_id,
                "sourceRepo": plan.source_repo,
                "sourceRevision": plan.source_revision,
                "relativePath": plan.relative_path,
                "expectedContentDigest": (
                    "sha256:"
                    + hashlib.sha256(plan.content.encode("utf-8")).hexdigest()
                ),
                "dispatch": {
                    "dispatchId": dispatch_id,
                    "clientRequestId": client_request_id,
                    "requestDigest": request_digest,
                    "runtimeJobId": reconciled.job_id,
                },
                "steps": {
                    "openedRevision": opened.revision,
                    "preparedRevision": prepared.task_revision,
                    "unknownRevision": unknown.revision,
                    "reconciliation": reconciliation_receipts,
                    "verifiedRevision": verified.revision,
                    "completedRevision": closed.revision,
                },
                "persistence": {
                    "finalState": projection.state.value,
                    "hostEventCount": storage.journal.event_count(plan.task_id),
                    "objectRefCount": len(refs),
                    "objectReferences": [
                        {
                            "digest": reference.digest,
                            "kind": reference.kind,
                            "byteLength": reference.byte_length,
                        }
                        for reference in refs
                    ],
                    "outcomeDigest": outcome_digest,
                    "outcome": outcome,
                },
                "runtime": {
                    "endpoint": args.endpoint,
                    "matchingJobs": jobs,
                    "workspaceClosed": runtime_workspace_closed,
                },
                "checks": checks,
                "environment": {
                    "stateRoot": str(state_root) if args.keep_state else None,
                    "providerSessionPersisted": False,
                },
            }
            completed = True
            emit_receipt(receipt)
    finally:
        if not completed:
            try:
                client("cleanup").call_tool(
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
