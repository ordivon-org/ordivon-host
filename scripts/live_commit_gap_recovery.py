#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib

from anc_canonical import JsonValue
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    TaskState,
)
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError
from ordivon_host.testing import (
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
            "Deliver one Runtime mutation, discard the response before Host admission, "
            "and recover the original Job from a fresh Host process."
        )
    )
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--content", default="recovered after Host commit gap\n")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    identity = ScenarioIdentity.create("live-commit-gap")
    task_token = identity.token
    state_root = scenario_state_root(
        args.state_root, prefix="commit-gap", identity=identity
    )
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=f"host-h6-commit-gap-{identity.nonce}.txt",
        content=args.content,
    )

    factory = RuntimeClientFactory(
        args.endpoint, token, "ordivon-host-h6-commit-gap"
    )
    client = factory.client
    clock = scenario_clock_ms

    completed = False
    try:
        with HostStorage(state_root) as storage:
            GuardedMutationHost(storage, client("create"), clock_ms=clock).create(plan)
        with HostStorage(state_root) as storage:
            GuardedMutationHost(storage, client("open"), clock_ms=clock).open_workspace(
                plan.task_id
            )
        with HostStorage(state_root) as storage:
            prepared = GuardedMutationHost(
                storage, client("prepare"), clock_ms=clock
            ).prepare(plan.task_id)

        # The Runtime accepts and executes the mutation. The returned observation is
        # deliberately discarded before HostStorage can append it.
        delivered = client("physical-delivery").call_tool(
            "workspace.exec", prepared.arguments
        )
        with HostStorage(state_root) as storage:
            before = storage.journal.get_task(plan.task_id)
            if before is None:
                raise AssertionError("prepared Task projection disappeared")
            if before.revision != 3 or before.state is not TaskState.WAITING:
                raise AssertionError("Host state advanced across the injected commit gap")

        with HostStorage(state_root) as storage:
            reconciled = GuardedMutationHost(
                storage, client("reconcile"), clock_ms=clock
            ).reconcile(plan.task_id, wait_ms=5_000)
        if reconciled.state is not TaskState.VERIFYING or not reconciled.job_id:
            raise AssertionError("commit-gap Job did not reach verification")
        with HostStorage(state_root) as storage:
            verified = GuardedMutationHost(
                storage, client("verify"), clock_ms=clock
            ).verify(plan.task_id)
        with HostStorage(state_root) as storage:
            closed = GuardedMutationHost(
                storage, client("close"), clock_ms=clock
            ).close(plan.task_id)

        audit = client("audit")
        jobs = jobs_for_request(audit, prepared.dispatch.client_request_id)
        workspace_closed = workspace_absent(audit, plan.workspace_id)
        with HostStorage(state_root) as storage:
            projection = storage.journal.get_task(plan.task_id)
            if projection is None:
                raise AssertionError("terminal Task projection disappeared")
            checks = {
                "runtimeResponseNeverAdmitted": before.revision == 3,
                "onePhysicalWorkspaceExec": True,
                "oneRuntimeJobForRequest": len(jobs) == 1,
                "originalRuntimeJobRecovered": (
                    jobs[0].get("jobId")
                    == delivered.get("jobId")
                    == reconciled.job_id
                ),
                "verificationAccepted": verified.state is TaskState.READY,
                "taskCompleted": (
                    closed.completed
                    and projection.state is TaskState.COMPLETED
                    and projection.revision == 6
                ),
                "workspaceClosed": workspace_closed,
            }
            if not all(checks.values()):
                raise AssertionError(f"commit-gap recovery checks failed: {checks}")
            receipt: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.host-live-commit-gap-recovery",
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "sourceRepo": plan.source_repo,
                "sourceRevision": plan.source_revision,
                "taskId": plan.task_id,
                "workspaceId": plan.workspace_id,
                "relativePath": plan.relative_path,
                "expectedContentDigest": (
                    "sha256:"
                    + hashlib.sha256(plan.content.encode("utf-8")).hexdigest()
                ),
                "dispatchId": prepared.dispatch.dispatch_id,
                "clientRequestId": prepared.dispatch.client_request_id,
                "runtimeJobId": reconciled.job_id,
                "preparedRevision": prepared.task_revision,
                "finalRevision": projection.revision,
                "hostEventCount": storage.journal.event_count(plan.task_id),
                "matchingRuntimeJobs": jobs,
                "checks": checks,
                "stateRoot": str(state_root) if args.keep_state else None,
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
            except (RuntimeToolRejected, RuntimeTransportError):
                pass
        cleanup_state_root(state_root, keep=args.keep_state)


if __name__ == "__main__":
    main()
