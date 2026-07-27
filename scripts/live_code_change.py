#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import subprocess

from anc_canonical import JsonValue
from ordivon_host import (
    CodeChangeHost,
    CodeChangePlan,
    CodeFileReplacement,
    ExecutionCheck,
    HostStorage,
    TaskState,
)
from ordivon_host.engine._serde import digest_text
from ordivon_host.runtime import RuntimeToolRejected, RuntimeTransportError
from ordivon_host.testing import (
    DropFirstSuccessfulToolResponse,
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

_SOURCE_PATH = "src/ordivon_host/ops/inspect.py"
_TEST_PATH = "tests/test_operations.py"
_SOURCE_OLD = '            "tasks": len(tasks),\n            "tasksByState": dict(sorted(states.items())),\n'
_SOURCE_NEW = (
    '            "tasks": len(tasks),\n'
    '            "terminalTasks": sum(task.state.terminal for task in tasks),\n'
    '            "tasksByState": dict(sorted(states.items())),\n'
)
_TEST_OLD = '            self.assertEqual(inspection["tasks"], 1)\n            report = doctor_state(root, now_ms=10)\n'
_TEST_NEW = (
    '            self.assertEqual(inspection["tasks"], 1)\n'
    '            self.assertEqual(inspection["terminalTasks"], 0)\n'
    '            report = doctor_state(root, now_ms=10)\n'
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real two-file Ordivon Host source change through durable "
            "workspace.execPlan recovery and independent diff verification."
        )
    )
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:8897/mcp"
    )
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_scenario_token()
    identity = ScenarioIdentity.create("live-code-change")
    state_root = scenario_state_root(
        args.state_root, prefix="code-change", identity=identity
    )
    source_before = _git_file(args.source_repo, args.source_revision, _SOURCE_PATH)
    test_before = _git_file(args.source_repo, args.source_revision, _TEST_PATH)
    source_after = _replace_once(source_before, _SOURCE_OLD, _SOURCE_NEW, _SOURCE_PATH)
    test_after = _replace_once(test_before, _TEST_OLD, _TEST_NEW, _TEST_PATH)
    protocol_path = "/root/projects/ordivon-computing/packages/ordivon-protocol/src"
    plan = CodeChangePlan(
        task_id=identity.task_id,
        goal_id=identity.goal_id,
        workspace_id=identity.workspace_id,
        source_repo=args.source_repo,
        source_revision=args.source_revision,
        files=(
            CodeFileReplacement(
                _SOURCE_PATH, digest_text(source_before), source_after
            ),
            CodeFileReplacement(
                _TEST_PATH, digest_text(test_before), test_after
            ),
        ),
        checks=(
            ExecutionCheck(
                "ruff",
                "/root/.local/bin/python3.12",
                ("-m", "ruff", "check", _SOURCE_PATH, _TEST_PATH),
            ),
            ExecutionCheck(
                "operations-tests",
                "/root/.local/bin/python3.12",
                ("-m", "unittest", "tests.test_operations"),
                env=(("PYTHONPATH", f"{protocol_path}:src"),),
                timeout_ms=120_000,
            ),
        ),
    )
    factory = RuntimeClientFactory(
        args.endpoint, token, "ordivon-host-live-code-change"
    )
    client = factory.client
    completed = False
    lossy: DropFirstSuccessfulToolResponse | None = None
    try:
        with HostStorage(state_root) as storage:
            CodeChangeHost(storage, client("create"), clock_ms=scenario_clock_ms).create(
                plan
            )
        with HostStorage(state_root) as storage:
            opened = CodeChangeHost(
                storage, client("open"), clock_ms=scenario_clock_ms
            ).open_workspace(plan.task_id)
        with HostStorage(state_root) as storage:
            prepared = CodeChangeHost(
                storage, client("prepare"), clock_ms=scenario_clock_ms
            ).prepare(plan.task_id)
            dispatch_id = prepared.dispatch.dispatch_id
            client_request_id = prepared.dispatch.client_request_id
        lossy = DropFirstSuccessfulToolResponse(
            client("deliver"), "workspace.execPlan"
        )
        with HostStorage(state_root) as storage:
            recovered = CodeChangeHost(
                storage, lossy, clock_ms=scenario_clock_ms
            ).load_prepared(plan.task_id)
            unknown = CodeChangeHost(
                storage, lossy, clock_ms=scenario_clock_ms
            ).deliver(recovered)
        if unknown.state is not TaskState.WAITING or not lossy.response_dropped:
            raise AssertionError("code change did not persist UNKNOWN after response loss")
        reconciled = None
        reconciliation: list[dict[str, JsonValue]] = []
        for index in range(10):
            with HostStorage(state_root) as storage:
                result = CodeChangeHost(
                    storage,
                    client(f"reconcile-{index}"),
                    clock_ms=scenario_clock_ms,
                ).reconcile(plan.task_id, wait_ms=30_000)
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
        if reconciled is None or not reconciled.job_id:
            raise AssertionError("original code-change Runtime Job was not recovered")
        with HostStorage(state_root) as storage:
            verified = CodeChangeHost(
                storage, client("verify"), clock_ms=scenario_clock_ms
            ).verify(plan.task_id)
            verification_snapshot = storage.read_task_event(plan.task_id)
            if not isinstance(verification_snapshot.data, dict):
                raise AssertionError("code-change verification event is not an object")
            diff_digest = verification_snapshot.data.get("diffObjectDigest")
            verification_digest = verification_snapshot.data.get("verificationDigest")
            if not isinstance(diff_digest, str) or not isinstance(
                verification_digest, str
            ):
                raise AssertionError("code-change verification digests are missing")
            diff_value = storage.objects.get(
                diff_digest, expected_kind="workspace-diff"
            )
            verification_value = storage.objects.get(
                verification_digest,
                expected_kind="code-change-verification-receipt",
            )
        with HostStorage(state_root) as storage:
            closed = CodeChangeHost(
                storage, client("close"), clock_ms=scenario_clock_ms
            ).close(plan.task_id)
            projection = storage.journal.get_task(plan.task_id)
            snapshot = storage.read_task_event(plan.task_id)
            if projection is None or not isinstance(snapshot.data, dict):
                raise AssertionError("terminal code-change Task state is missing")
            outcome_digest = snapshot.data.get("outcomeDigest")
            if not isinstance(outcome_digest, str):
                raise AssertionError("terminal code-change event omitted outcome")
            outcome = storage.objects.get(
                outcome_digest, expected_kind="task-outcome"
            )
            event_count = storage.journal.event_count(plan.task_id)
            object_refs = storage.journal.object_refs()
        audit = client("audit")
        jobs = jobs_for_request(audit, client_request_id)
        workspace_closed = workspace_absent(audit, plan.workspace_id)
        diff_text = diff_value.get("diff") if isinstance(diff_value, dict) else None
        checks = {
            "responseDroppedAfterExecPlanAdmission": lossy.response_dropped,
            "unknownPersisted": unknown.revision == 4,
            "freshHostStoragePerStage": True,
            "oneExecPlanInvocation": lossy.calls.count("workspace.execPlan") == 1,
            "oneRuntimeJobForRequest": len(jobs) == 1,
            "originalRuntimeJobRecovered": jobs[0].get("jobId") == reconciled.job_id,
            "allStructuredStepsCompleted": (
                isinstance(verification_value, dict)
                and verification_value.get("completedSteps") == 3
                and verification_value.get("totalSteps") == 3
            ),
            "twoFilesVerified": (
                isinstance(verification_value, dict)
                and len(verification_value.get("fileResults", [])) == 2
                and all(
                    item.get("accepted") is True
                    for item in verification_value.get("fileResults", [])
                    if isinstance(item, dict)
                )
            ),
            "diffContainsSourceAndTest": (
                isinstance(diff_text, str)
                and _SOURCE_PATH in diff_text
                and _TEST_PATH in diff_text
            ),
            "taskCompleted": (
                closed.completed
                and projection.state is TaskState.COMPLETED
                and projection.revision == 7
            ),
            "runtimeWorkspaceClosed": workspace_closed,
        }
        if not all(checks.values()):
            raise AssertionError(f"live code-change checks failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-live-code-change",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "hostRevision": "5076e6b42bcb65ddd9a3615e9215ebae18279ec9",
            "sourceRepo": args.source_repo,
            "sourceRevision": args.source_revision,
            "taskId": plan.task_id,
            "goalId": plan.goal_id,
            "workspaceId": plan.workspace_id,
            "dispatchId": dispatch_id,
            "clientRequestId": client_request_id,
            "runtimeJobId": reconciled.job_id,
            "files": [item.to_dict() for item in plan.files],
            "checksPlan": [item.to_dict() for item in plan.checks],
            "reconciliation": reconciliation,
            "openedRevision": opened.revision,
            "unknownRevision": unknown.revision,
            "verifiedRevision": verified.revision,
            "completedRevision": closed.revision,
            "hostEventCount": event_count,
            "objectRefCount": len(object_refs),
            "diffObjectDigest": diff_digest,
            "verificationDigest": verification_digest,
            "generatedDiff": diff_value,
            "verification": verification_value,
            "outcome": outcome,
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


def _git_file(repository: str, revision: str, relative_path: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", repository, "show", f"{revision}:{relative_path}"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def _replace_once(value: str, old: str, new: str, label: str) -> str:
    if value.count(old) != 1:
        raise RuntimeError(f"live code-change anchor differs: {label}")
    return value.replace(old, new)


if __name__ == "__main__":
    main()
