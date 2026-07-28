#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import time

from anc_canonical import JsonValue
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    TaskState,
)
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError
from ordivon_host.testing import (
    DropFirstSuccessfulExecResponse,
    RuntimeClientFactory,
    ScenarioIdentity,
    cleanup_state_root,
    emit_receipt,
    jobs_for_request,
    load_scenario_token,
    restart_runtime,
    scenario_clock_ms,
    scenario_state_root,
    service_state,
    wait_runtime_ready,
    workspace_absent,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persist an UNKNOWN guarded mutation, restart the Runtime service, "
            "and recover the original durable Job without redispatch."
        )
    )
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--content", default="recovered across Runtime restart\n")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    identity = ScenarioIdentity.create("live-restart-recovery")
    task_token = identity.token
    state_root = scenario_state_root(
        args.state_root, prefix="restart", identity=identity
    )
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=f"host-h6-restart-proof-{identity.stamp_ms}.txt",
        content=args.content,
    )

    factory = RuntimeClientFactory(
        args.endpoint, token, "ordivon-host-h6-restart"
    )
    client = factory.client
    clock = scenario_clock_ms

    completed = False
    lossy: DropFirstSuccessfulExecResponse | None = None
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

        lossy = DropFirstSuccessfulExecResponse(client("deliver"))
        with HostStorage(state_root) as storage:
            recovered = GuardedMutationHost(
                storage, lossy, clock_ms=clock
            ).load_prepared(plan.task_id)
            unknown = GuardedMutationHost(
                storage, lossy, clock_ms=clock
            ).deliver(recovered)
        if unknown.state is not TaskState.WAITING or not lossy.response_dropped:
            raise AssertionError("mutation did not persist UNKNOWN before restart")

        before = service_state(args.service)
        restart_started = time.perf_counter()
        restart_runtime(args.service)
        wait_runtime_ready(args.service, factory)
        restart_elapsed_ms = int(round((time.perf_counter() - restart_started) * 1_000))
        after = service_state(args.service)

        reconciliation: list[dict[str, JsonValue]] = []
        reconciled = None
        for index in range(20):
            with HostStorage(state_root) as storage:
                result = GuardedMutationHost(
                    storage,
                    client(f"reconcile-{index}"),
                    clock_ms=clock,
                ).reconcile(plan.task_id, wait_ms=2_000)
            reconciliation.append(
                {
                    "revision": result.revision,
                    "state": result.state.value,
                    "jobId": result.job_id,
                }
            )
            if result.state is TaskState.VERIFYING:
                reconciled = result
                break
            time.sleep(0.1)
        if reconciled is None or not reconciled.job_id:
            raise AssertionError("original Runtime Job was not recovered after restart")

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
                raise AssertionError("Host Task projection disappeared")
            checks = {
                "unknownPersistedBeforeRestart": unknown.revision == 4,
                "runtimeProcessReplaced": before["mainPid"] != after["mainPid"],
                "runtimeActiveAfterRestart": after["activeState"] == "active",
                "oneWorkspaceExecInvocation": lossy.calls.count("workspace.exec") == 1,
                "oneRuntimeJobForRequest": len(jobs) == 1,
                "originalRuntimeJobRecovered": jobs[0].get("jobId") == reconciled.job_id,
                "noRedispatchAfterRestart": lossy.calls.count("workspace.exec") == 1,
                "verificationAccepted": verified.state is TaskState.READY,
                "taskCompleted": (
                    closed.completed
                    and projection.state is TaskState.COMPLETED
                    and projection.revision == 7
                ),
                "workspaceClosed": workspace_closed,
            }
            if not all(checks.values()):
                raise AssertionError(f"Runtime restart recovery checks failed: {checks}")
            receipt: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.host-live-runtime-restart-recovery",
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
                "service": {
                    "name": args.service,
                    "before": before,
                    "after": after,
                    "restartElapsedMs": restart_elapsed_ms,
                },
                "reconciliation": reconciliation,
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
