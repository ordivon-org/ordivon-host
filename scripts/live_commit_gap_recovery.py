#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from anc_canonical import JsonValue, canonical_digest
from live_guarded_mutation import _jobs_for_request, _workspace_absent
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    McpRuntimeClient,
    TaskState,
)
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError


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
    token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not token:
        raise SystemExit("ORDIVON_BEARER_TOKEN is required")
    stamp = int(time.time() * 1_000)
    nonce = uuid.uuid4().hex[:12]
    task_token = f"live-commit-gap-{stamp}-{nonce}"
    state_root = Path(
        args.state_root
        or tempfile.mkdtemp(prefix=f"ordivon-host-commit-gap-{nonce}-", dir="/tmp")
    )
    state_root.mkdir(parents=True, exist_ok=True)
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=f"host-h6-commit-gap-{nonce}.txt",
        content=args.content,
    )

    def clock() -> int:
        return int(time.time() * 1_000)

    def client(label: str) -> McpRuntimeClient:
        return McpRuntimeClient(
            args.endpoint,
            token,
            client_name=f"ordivon-host-h6-commit-gap-{label}",
            client_version="0.0.1",
        )

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
        jobs = _jobs_for_request(audit, prepared.dispatch.client_request_id)
        workspace_closed = _workspace_absent(audit, plan.workspace_id)
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
            receipt["integrity"] = {
                "algorithm": "sha256",
                "canonicalization": "ordivon-canonical-json-v1",
                "payloadDigest": canonical_digest(receipt),
            }
            completed = True
            print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
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
        if not args.keep_state:
            shutil.rmtree(state_root, ignore_errors=True)


if __name__ == "__main__":
    main()
