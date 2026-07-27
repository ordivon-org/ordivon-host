#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from anc_canonical import JsonValue, canonical_digest
from live_guarded_mutation import (
    DropFirstSuccessfulExecResponse,
    _jobs_for_request,
    _workspace_absent,
)
from ordivon_host import (
    GuardedMutationHost,
    GuardedMutationPlan,
    HostStorage,
    McpRuntimeClient,
    TaskState,
)
from ordivon_host.runtime import (
    RuntimeProtocolError,
    RuntimeToolRejected,
    RuntimeTransportError,
)

_SERVICE = re.compile(r"^[A-Za-z0-9_.@-]+$")


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
    if not _SERVICE.fullmatch(args.service):
        raise SystemExit("service name contains unsupported characters")
    token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not token:
        raise SystemExit("ORDIVON_BEARER_TOKEN is required")
    stamp = int(time.time() * 1_000)
    nonce = uuid.uuid4().hex[:12]
    task_token = f"live-restart-recovery-{stamp}-{nonce}"
    state_root = Path(
        args.state_root
        or tempfile.mkdtemp(prefix=f"ordivon-host-restart-{stamp}-", dir="/tmp")
    )
    state_root.mkdir(parents=True, exist_ok=True)
    plan = GuardedMutationPlan(
        task_id=f"task:{task_token}",
        goal_id=f"goal:{task_token}",
        workspace_id=f"host-{task_token}",
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        relative_path=f"host-h6-restart-proof-{stamp}.txt",
        content=args.content,
    )

    def clock() -> int:
        return int(time.time() * 1_000)

    def client(label: str) -> McpRuntimeClient:
        return McpRuntimeClient(
            args.endpoint,
            token,
            client_name=f"ordivon-host-h6-restart-{label}",
            client_version="0.0.1",
        )

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

        before = _service_state(args.service)
        restart_started = time.perf_counter()
        subprocess.run(
            ["/usr/bin/systemctl", "restart", args.service],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _wait_runtime(args.service, args.endpoint, token)
        restart_elapsed_ms = int(round((time.perf_counter() - restart_started) * 1_000))
        after = _service_state(args.service)

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
        jobs = _jobs_for_request(audit, prepared.dispatch.client_request_id)
        workspace_closed = _workspace_absent(audit, plan.workspace_id)
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


def _service_state(service: str) -> dict[str, JsonValue]:
    output = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            service,
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "ExecMainStartTimestampMonotonic",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return {
        "mainPid": int(values.get("MainPID", "0")),
        "activeState": values.get("ActiveState", ""),
        "subState": values.get("SubState", ""),
        "startTimestampMonotonic": values.get(
            "ExecMainStartTimestampMonotonic", ""
        ),
    }


def _wait_runtime(service: str, endpoint: str, token: str) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        active = (
            subprocess.run(
                ["/usr/bin/systemctl", "is-active", "--quiet", service],
                check=False,
            ).returncode
            == 0
        )
        if active:
            probe = McpRuntimeClient(
                endpoint,
                token,
                timeout_seconds=1.0,
                client_name="ordivon-h6-restart-readiness",
                client_version="0.0.1",
            )
            try:
                probe.initialize()
                return
            except (RuntimeTransportError, RuntimeProtocolError) as error:
                last_error = error
        time.sleep(0.05)
    raise RuntimeError(
        f"Runtime did not become MCP-ready: {service}: {last_error}"
    )


if __name__ == "__main__":
    main()
