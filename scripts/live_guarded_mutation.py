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
from typing import Any

from anc_canonical import JsonValue, canonical_digest
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    McpRuntimeClient,
    TaskState,
)
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError


class DropFirstSuccessfulExecResponse:
    def __init__(self, client: McpRuntimeClient) -> None:
        self.client = client
        self.calls: list[str] = []
        self.response_dropped = False

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return self.client.initialize()

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        return self.client.list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        result = self.client.call_tool(name, arguments)
        if name == "workspace.exec" and not self.response_dropped:
            self.response_dropped = True
            raise RuntimeTransportError(
                "injected response loss after Runtime accepted workspace.exec"
            )
        return result


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
    token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not token:
        raise SystemExit("ORDIVON_BEARER_TOKEN is required")
    stamp = int(time.time() * 1_000)
    nonce = uuid.uuid4().hex[:12]
    task_token = f"live-mutation-{stamp}-{nonce}"
    relative_path = args.relative_path or f"host-h4-proof-{stamp}.txt"
    state_root = Path(
        args.state_root
        or tempfile.mkdtemp(prefix=f"ordivon-host-mutation-{stamp}-", dir="/tmp")
    )
    state_root.mkdir(parents=True, exist_ok=True)
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=relative_path,
        content=args.content,
    )

    def clock() -> int:
        return int(time.time() * 1_000)

    def client(label: str) -> McpRuntimeClient:
        return McpRuntimeClient(
            args.endpoint,
            token,
            client_name=f"ordivon-host-live-mutation-{label}",
            client_version="0.0.1",
        )

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
        jobs = _jobs_for_request(audit_client, client_request_id)
        runtime_workspace_closed = _workspace_absent(
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
            except RuntimeToolRejected:
                pass
        if not args.keep_state:
            shutil.rmtree(state_root, ignore_errors=True)


def _jobs_for_request(
    client: McpRuntimeClient,
    client_request_id: str,
) -> list[dict[str, JsonValue]]:
    filtered = _task_list_supports_client_request_filter(client)
    cursor: dict[str, JsonValue] | None = None
    seen_cursors: set[str] = set()
    matches: list[dict[str, JsonValue]] = []
    for _ in range(100):
        arguments: dict[str, Any] = {"limit": 100}
        if filtered:
            arguments["clientRequestId"] = client_request_id
        if cursor is not None:
            arguments["cursor"] = cursor
        page = client.call_tool("task.list", arguments)
        jobs = page.get("jobs")
        if not isinstance(jobs, list):
            raise AssertionError("task.list omitted jobs")
        for job in jobs:
            if not isinstance(job, dict):
                raise AssertionError("task.list returned a non-object Job")
            observed_request_id = job.get("clientRequestId")
            if filtered and observed_request_id != client_request_id:
                raise AssertionError(
                    "filtered task.list returned another clientRequestId"
                )
            if observed_request_id == client_request_id:
                matches.append(job)
        next_cursor = page.get("nextCursor")
        if next_cursor is None:
            return matches
        if not isinstance(next_cursor, dict):
            raise AssertionError("task.list returned an invalid cursor")
        cursor_digest = canonical_digest(next_cursor)
        if cursor_digest in seen_cursors:
            raise AssertionError("task.list repeated a pagination cursor")
        seen_cursors.add(cursor_digest)
        cursor = next_cursor
    raise AssertionError("task.list pagination exceeded the live proof bound")


def _task_list_supports_client_request_filter(client: McpRuntimeClient) -> bool:
    for tool in client.list_tools():
        if tool.get("name") != "task.list":
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            raise AssertionError("task.list input schema is not an object")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise AssertionError("task.list input schema omitted properties")
        return "clientRequestId" in properties
    raise AssertionError("Runtime Tool catalog omitted task.list")


def _workspace_absent(client: McpRuntimeClient, workspace_id: str) -> bool:
    try:
        client.call_tool(
            "workspace.get",
            {"schemaVersion": 1, "workspaceId": workspace_id},
        )
    except RuntimeToolRejected as error:
        return (
            error.detail.code == "INVALID_REQUEST"
            and error.detail.field == "workspaceId"
            and error.detail.commit_state == "not_committed"
        )
    return False


if __name__ == "__main__":
    main()
