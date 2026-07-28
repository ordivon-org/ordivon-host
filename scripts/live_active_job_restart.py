#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import time
from typing import Any

from anc_canonical import JsonValue
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError
from ordivon_host.testing import (
    RuntimeClientFactory,
    ScenarioIdentity,
    emit_receipt,
    load_scenario_token,
    restart_runtime,
    service_state,
    wait_runtime_ready,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restart Runtime while one real workspace.exec Job is running."
    )
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--sleep-seconds", type=float, default=3.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sleep_seconds <= 0 or args.sleep_seconds > 30:
        raise SystemExit("sleep-seconds must be in (0, 30]")
    token = load_scenario_token()
    identity = ScenarioIdentity.create("h6-active-restart")
    stamp = identity.stamp_ms
    nonce = identity.nonce
    workspace_id = f"h6-active-restart-{stamp}-{nonce}"
    relative_path = f"h6-active-restart-{stamp}-{nonce}.txt"
    content = f"active Runtime Job survived restart at {stamp}\n"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    client_request_id = f"request:h6-active-restart:{stamp}:{nonce}"

    factory = RuntimeClientFactory(
        args.endpoint, token, "ordivon-h6-active-restart"
    )

    def client(label: str):
        return factory.client(label, initialize=True)

    cleanup = client("cleanup")
    closed = False
    try:
        before_client = client("before")
        workspace = before_client.call_tool(
            "workspace.open",
            {
                "schemaVersion": 1,
                "sourceRepo": args.source_repo,
                "sourceRevision": args.source_revision,
                "workspaceId": workspace_id,
            },
        )
        script = (
            "import base64,pathlib,sys,time;"
            "time.sleep(float(sys.argv[1]));"
            "pathlib.Path(sys.argv[2]).write_bytes(base64.b64decode(sys.argv[3]))"
        )
        submitted = before_client.call_tool(
            "workspace.exec",
            {
                "schemaVersion": 1,
                "clientRequestId": client_request_id,
                "execution": {
                    "workspaceId": workspace_id,
                    "executable": "/usr/bin/python3",
                    "args": [
                        "-c",
                        script,
                        str(args.sleep_seconds),
                        relative_path,
                        encoded,
                    ],
                    "cwdRelative": ".",
                    "timeoutMs": int((args.sleep_seconds + 10) * 1_000),
                    "stdoutLimitBytes": 16_384,
                    "stderrLimitBytes": 16_384,
                },
                "waitMs": 0,
                "stdoutTailBytes": 4_096,
                "stderrTailBytes": 4_096,
            },
        )
        job_id = submitted.get("jobId")
        if not isinstance(job_id, str):
            raise AssertionError("workspace.exec omitted Job identity")
        pre_restart: dict[str, Any] | None = None
        active_deadline = time.monotonic() + 10
        while time.monotonic() < active_deadline:
            observed = before_client.call_tool(
                "task.observe",
                {
                    "schemaVersion": 1,
                    "jobId": job_id,
                    "waitMs": 200,
                    "stdoutTailBytes": 4_096,
                    "stderrTailBytes": 4_096,
                },
            )
            status = observed.get("status")
            if status == "working":
                pre_restart = observed
                break
            if status in {
                "succeeded",
                "failed",
                "timed_out",
                "cancelled",
                "lost",
                "orphaned",
            }:
                raise AssertionError(
                    f"Job became terminal before restart: {status}: "
                    f"{observed.get('stderrTail')}"
                )
        if pre_restart is None:
            raise AssertionError("Job did not enter working before restart")
        service_before = service_state(args.service)
        time.sleep(min(0.2, args.sleep_seconds / 8))
        restart_started = time.perf_counter()
        restart_runtime(args.service)
        wait_runtime_ready(args.service, factory)
        restart_elapsed_ms = int(round((time.perf_counter() - restart_started) * 1_000))
        service_after = service_state(args.service)

        statuses = [str(pre_restart.get("status"))]
        final: dict[str, Any] | None = None
        after_client = client("after")
        deadline = time.monotonic() + args.sleep_seconds + 20
        while time.monotonic() < deadline:
            observed = after_client.call_tool(
                "task.observe",
                {
                    "schemaVersion": 1,
                    "jobId": job_id,
                    "waitMs": 1_000,
                    "stdoutTailBytes": 4_096,
                    "stderrTailBytes": 4_096,
                },
            )
            status = str(observed.get("status"))
            if not statuses or statuses[-1] != status:
                statuses.append(status)
            if status in {
                "succeeded",
                "failed",
                "timed_out",
                "cancelled",
                "lost",
                "orphaned",
            }:
                final = observed
                break
        if final is None:
            raise AssertionError("active Job did not reach a terminal state")
        if final.get("status") != "succeeded":
            raise AssertionError(
                f"active Job ended after restart as {final.get('status')}: "
                f"{final.get('stderrTail')}"
            )
        read = after_client.call_tool(
            "workspace.read",
            {
                "schemaVersion": 1,
                "workspaceId": workspace_id,
                "relativePath": relative_path,
                "mode": "FULL",
                "offset": 0,
                "maxBytes": 65_536,
            },
        )
        closed_result = after_client.call_tool(
            "workspace.close",
            {"schemaVersion": 1, "workspaceId": workspace_id, "force": True},
        )
        closed = closed_result.get("workspaceId") == workspace_id
        checks = {
            "jobWasActiveBeforeRestart": pre_restart.get("status") in {"queued", "working"},
            "runtimeProcessReplaced": service_before["mainPid"] != service_after["mainPid"],
            "runtimeActiveAfterRestart": service_after["activeState"] == "active",
            "sameJobIdentityAfterRestart": final.get("jobId") == job_id,
            "jobSucceeded": final.get("status") == "succeeded",
            "contentWrittenExactlyOnce": read.get("content") == content,
            "contentDigestMatches": read.get("digest") == _digest_text(content),
            "workspaceClosed": closed,
        }
        if not all(checks.values()):
            raise AssertionError(f"active Runtime restart checks failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.runtime-live-active-job-restart",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "sourceRepo": args.source_repo,
            "sourceRevision": args.source_revision,
            "workspace": workspace,
            "clientRequestId": client_request_id,
            "jobId": job_id,
            "attemptId": final.get("attemptId"),
            "statusSequence": statuses,
            "finalStatus": final.get("status"),
            "service": {
                "name": args.service,
                "before": service_before,
                "after": service_after,
                "restartElapsedMs": restart_elapsed_ms,
            },
            "expectedContentDigest": _digest_text(content),
            "observedContentDigest": read.get("digest"),
            "checks": checks,
        }
        emit_receipt(receipt)
    finally:
        if not closed:
            try:
                cleanup.call_tool(
                    "workspace.close",
                    {"schemaVersion": 1, "workspaceId": workspace_id, "force": True},
                )
            except (RuntimeToolRejected, RuntimeTransportError):
                pass


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
