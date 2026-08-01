#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import stat
import subprocess

from anc_canonical import JsonValue
from ordivon_host import (
    CodeChangeHost,
    CodeChangePlan,
    CodeFileReplacement,
    EventKind,
    ExecutionCheck,
    HostStorage,
    TaskState,
)
from ordivon_host.domain import RepositoryRef, StaticRepositoryResolver
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

_SOURCE_PATH = "src/ordivon_host/objects/codecs.py"
_TEST_PATH = "tests/test_boundaries.py"
_SOURCE_OLD = (
    '    """Dispatch one durable semantic object by explicit kind and schema version."""\n'
    '    kind = value.get("kind")\n'
)
_SOURCE_NEW = (
    '    """Dispatch one durable semantic object by explicit kind and schema version."""\n'
    '    if not decoders:\n'
    '        raise ObjectCodecError(f"{label} has no registered decoders")\n'
    '    kind = value.get("kind")\n'
)
_TEST_OLD = (
    '        with self.assertRaises(UnsupportedObjectVersion):\n'
    '            CodeChangePlan.from_dict(value)\n'
)
_TEST_NEW = (
    '        with self.assertRaises(UnsupportedObjectVersion):\n'
    '            CodeChangePlan.from_dict(value)\n'
    '\n'
    '    def test_codec_rejects_an_empty_decoder_registry(self) -> None:\n'
    '        from ordivon_host.objects import ObjectCodecError, decode_versioned_object\n'
    '\n'
    '        with self.assertRaisesRegex(ObjectCodecError, "no registered decoders"):\n'
    '            decode_versioned_object(\n'
    '                {"schemaVersion": 1, "kind": "audit.object"},\n'
    '                expected_kind="audit.object",\n'
    '                decoders={},\n'
    '                label="AuditObject",\n'
    '            )\n'
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
    parser.add_argument(
        "--repository-id", default="repository:ordivon-host"
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--runtime-deployment-receipt", required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def code_change_host(
    storage: HostStorage,
    runtime,
    *,
    source_repo: str,
    repository_id: str,
) -> CodeChangeHost:
    return CodeChangeHost(
        storage,
        runtime,
        clock_ms=scenario_clock_ms,
        repository_resolver=StaticRepositoryResolver(
            {repository_id: source_repo}
        ),
    )


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
        repository=RepositoryRef(args.repository_id, args.source_revision),
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
                "boundary-tests",
                "/root/.local/bin/python3.12",
                ("-m", "unittest", "tests.test_boundaries"),
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
            code_change_host(
                storage,
                client("create"),
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).create(plan)
        with HostStorage(state_root) as storage:
            opened = code_change_host(
                storage,
                client("open"),
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).open_workspace(plan.task_id)
        with HostStorage(state_root) as storage:
            prepared = code_change_host(
                storage,
                client("prepare"),
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).prepare(plan.task_id)
            dispatch_id = prepared.dispatch.dispatch_id
            client_request_id = prepared.dispatch.client_request_id
        lossy = DropFirstSuccessfulToolResponse(
            client("deliver", initialize=True), "workspace.execPlan"
        )
        with HostStorage(state_root) as storage:
            recovered = code_change_host(
                storage,
                lossy,
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).load_prepared(plan.task_id)
            unknown = code_change_host(
                storage,
                lossy,
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).deliver(recovered)
        if unknown.state is not TaskState.WAITING or not lossy.response_dropped:
            raise AssertionError(
                "code change did not persist UNKNOWN after response loss: "
                f"state={unknown.state.value}, revision={unknown.revision}, "
                f"responseDropped={lossy.response_dropped}, calls={lossy.calls}"
            )
        reconciled = None
        reconciliation: list[dict[str, JsonValue]] = []
        for index in range(10):
            with HostStorage(state_root) as storage:
                result = code_change_host(
                    storage,
                    client(f"reconcile-{index}", initialize=True),
                    source_repo=args.source_repo,
                    repository_id=args.repository_id,
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
        close_loss = DropFirstSuccessfulToolResponse(
            client("verify-close-loss", initialize=True), "workspace.close"
        )
        try:
            with HostStorage(state_root) as storage:
                code_change_host(
                    storage,
                    close_loss,
                    source_repo=args.source_repo,
                    repository_id=args.repository_id,
                ).verify(plan.task_id)
        except RuntimeTransportError:
            pass
        else:
            raise AssertionError("fenced Workspace close response loss was not injected")
        if not close_loss.response_dropped:
            raise AssertionError("workspace.close response loss did not occur")
        with HostStorage(state_root) as storage:
            prepared_verification_snapshot = storage.read_task_event(plan.task_id)
            if (
                prepared_verification_snapshot.event_kind
                != EventKind.VERIFICATION_RECORDED
                or prepared_verification_snapshot.projection.state
                is not TaskState.VERIFYING
                or not isinstance(prepared_verification_snapshot.data, dict)
            ):
                raise AssertionError(
                    "close response loss did not retain prepared Verification"
                )
            prepared_verification_revision = (
                prepared_verification_snapshot.projection.revision
            )
            diff_digest = prepared_verification_snapshot.data.get("diffObjectDigest")
            verification_digest = prepared_verification_snapshot.data.get(
                "verificationDigest"
            )
            source_state_digest = prepared_verification_snapshot.data.get(
                "sourceStateDigest"
            )
            if not all(
                isinstance(value, str)
                for value in (
                    diff_digest,
                    verification_digest,
                    source_state_digest,
                )
            ):
                raise AssertionError("prepared verification digests are missing")
            diff_value = storage.objects.get(
                diff_digest, expected_kind="workspace-diff"
            )
            verification_value = storage.objects.get(
                verification_digest,
                expected_kind="code-change-verification-receipt",
            )
        with HostStorage(state_root) as storage:
            verified = code_change_host(
                storage,
                client("verify-tombstone-replay", initialize=True),
                source_repo=args.source_repo,
                repository_id=args.repository_id,
            ).verify(plan.task_id)
            accepted_snapshot = storage.read_task_event(plan.task_id)
            if not isinstance(accepted_snapshot.data, dict):
                raise AssertionError("accepted verification event is not an object")
            workspace_close = accepted_snapshot.data.get("workspaceClose")
            if (
                not isinstance(workspace_close, dict)
                or workspace_close.get("sourceStateDigest") != source_state_digest
            ):
                raise AssertionError("tombstone replay did not retain source state")
        with HostStorage(state_root) as storage:
            closed = code_change_host(
                storage,
                client("complete"),
                source_repo=args.source_repo,
                repository_id=args.repository_id,
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
        state_modes = {
            "stateRoot": oct(stat.S_IMODE(Path(state_root).stat().st_mode)),
            "objects": oct(
                stat.S_IMODE((Path(state_root) / "objects").stat().st_mode)
            ),
            "journal": oct(
                stat.S_IMODE((Path(state_root) / "host.sqlite3").stat().st_mode)
            ),
        }
        audit = client("audit", initialize=True)
        jobs = jobs_for_request(audit, client_request_id)
        workspace_closed = workspace_absent(audit, plan.workspace_id)
        checks = {
            "responseDroppedAfterExecPlanAdmission": lossy.response_dropped,
            "responseDroppedAfterFencedClose": close_loss.response_dropped,
            "unknownPersisted": unknown.revision == 4,
            "preparedVerificationPersisted": prepared_verification_revision == 6,
            "freshHostStoragePerStage": True,
            "oneExecPlanInvocation": lossy.calls.count("workspace.execPlan") == 1,
            "oneFencedCloseBeforeReplay": close_loss.calls.count("workspace.close") == 1,
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
            "structuredDiffMatchesExactPlan": (
                isinstance(diff_value, dict)
                and set(diff_value.get("changedPaths", []))
                == {_SOURCE_PATH, _TEST_PATH}
                and set(diff_value.get("modifiedPaths", []))
                == {_SOURCE_PATH, _TEST_PATH}
                and not diff_value.get("addedPaths")
                and not diff_value.get("deletedPaths")
                and not diff_value.get("renamedPaths")
                and not diff_value.get("untrackedPaths")
                and diff_value.get("truncated", False) is False
            ),
            "sourceStateBoundToClose": (
                isinstance(source_state_digest, str)
                and workspace_close.get("sourceStateDigest") == source_state_digest
            ),
            "taskCompleted": (
                closed.completed
                and projection.state is TaskState.COMPLETED
                and projection.revision == 8
            ),
            "runtimeWorkspaceClosed": workspace_closed,
            "privateStateModes": state_modes
            == {"stateRoot": "0o700", "objects": "0o700", "journal": "0o600"},
        }
        if not all(checks.values()):
            raise AssertionError(f"live code-change checks failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-live-code-change",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "hostRevision": args.source_revision,
            "scenarioScriptDigest": "sha256:"
            + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtimeRevision": args.runtime_revision,
            "runtimeDeploymentReceipt": args.runtime_deployment_receipt,
            "repositoryId": args.repository_id,
            "sourceRepoLocator": args.source_repo,
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
            "preparedVerificationRevision": prepared_verification_revision,
            "verifiedRevision": verified.revision,
            "completedRevision": closed.revision,
            "hostEventCount": event_count,
            "objectRefCount": len(object_refs),
            "diffObjectDigest": diff_digest,
            "verificationDigest": verification_digest,
            "sourceStateDigest": source_state_digest,
            "workspaceClose": workspace_close,
            "stateModes": state_modes,
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
                client("cleanup", initialize=True).call_tool(
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
