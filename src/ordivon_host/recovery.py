from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from anc_canonical import JsonValue

from .authority import CapabilityAuthorizer, TrustedLocalAuthorizer
from .domain import RepositoryResolver, TaskState
from .engine import CodeChangeHost, DeterministicReadHost, GuardedMutationHost
from .runtime import RuntimeClient
from .storage import HostStorage, TaskEventSnapshot


class RecoveryAction(StrEnum):
    NONE = "none"
    ADVANCE_READ = "advance-read"
    OBSERVE_RUNTIME_DISPATCH = "observe-runtime-dispatch"
    INVOKE_PROVIDER = "invoke-provider"
    MANUAL_STAGE = "manual-stage"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    task_id: str
    state: TaskState
    revision: int
    workload: str
    event_kind: str
    frontier: str | None
    action: RecoveryAction
    automatic: bool
    reason: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "taskId": self.task_id,
            "state": self.state.value,
            "revision": self.revision,
            "workload": self.workload,
            "eventKind": self.event_kind,
            "frontier": self.frontier,
            "action": self.action.value,
            "automatic": self.automatic,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    before: RecoveryAssessment
    after: RecoveryAssessment
    changed: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "changed": self.changed,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


def assess_recovery(storage: HostStorage, task_id: str) -> RecoveryAssessment:
    snapshot = storage.read_task_event(task_id)
    projection = snapshot.projection
    frontier = (
        projection.ready_frontier[0]
        if len(projection.ready_frontier) == 1
        else None
    )
    workload = _workload(storage, snapshot)
    if projection.state.terminal:
        return RecoveryAssessment(
            task_id,
            projection.state,
            projection.revision,
            workload,
            snapshot.event_kind.value,
            frontier,
            RecoveryAction.NONE,
            False,
            "Task is already terminal",
        )
    if workload == "deterministic-read":
        valid_stage = frontier is not None and frontier.rsplit(":", 1)[-1] in {
            "open",
            "read",
            "close",
        }
        if projection.state is not TaskState.READY or not valid_stage:
            return _manual(
                snapshot,
                workload,
                "read recovery requires READY at an open/read/close frontier",
            )
        return RecoveryAssessment(
            task_id,
            projection.state,
            projection.revision,
            workload,
            snapshot.event_kind.value,
            frontier,
            RecoveryAction.ADVANCE_READ,
            True,
            "read lifecycle and workspace.read are naturally replayable",
        )
    if workload in {"guarded-mutation", "code-change"}:
        if (
            projection.state is TaskState.WAITING
            and frontier is not None
            and frontier.endswith(":reconcile")
        ):
            return RecoveryAssessment(
                task_id,
                projection.state,
                projection.revision,
                workload,
                snapshot.event_kind.value,
                frontier,
                RecoveryAction.OBSERVE_RUNTIME_DISPATCH,
                True,
                "only the already-persisted Runtime Dispatch will be observed",
            )
        return _manual(
            snapshot,
            workload,
            "workload stage is deterministic but not an uncertain-delivery recovery point",
        )
    if workload == "experimental-effect-lifecycle":
        return _manual(
            snapshot,
            workload,
            "executor identity and domain observation source are required for Effect reconciliation",
        )
    if workload == "cognition":
        if projection.state is TaskState.WAITING:
            return RecoveryAssessment(
                task_id,
                projection.state,
                projection.revision,
                workload,
                snapshot.event_kind.value,
                frontier,
                RecoveryAction.INVOKE_PROVIDER,
                False,
                "a model Gateway and explicit admission state are required",
            )
        return _manual(snapshot, workload, "cognition requires an explicit caller decision")
    return RecoveryAssessment(
        task_id,
        projection.state,
        projection.revision,
        workload,
        snapshot.event_kind.value,
        frontier,
        RecoveryAction.UNSUPPORTED,
        False,
        "Task workload cannot be identified from durable state",
    )


class TaskReconciler:
    """One-shot recovery driver. It never creates or redispatches an Effect."""

    def __init__(
        self,
        storage: HostStorage,
        runtime: RuntimeClient,
        *,
        clock_ms: Callable[[], int],
        repository_resolver: RepositoryResolver,
        authorizer: CapabilityAuthorizer | None = None,
    ) -> None:
        self.storage = storage
        self.runtime = runtime
        self.clock_ms = clock_ms
        self.repository_resolver = repository_resolver
        self.authorizer = authorizer or TrustedLocalAuthorizer()

    def reconcile(self, task_id: str, *, wait_ms: int = 30_000) -> RecoveryResult:
        if wait_ms < 0 or wait_ms > 30_000:
            raise ValueError("recovery wait_ms must be between 0 and 30000")
        before = assess_recovery(self.storage, task_id)
        if not before.automatic:
            return RecoveryResult(before, before, False)
        if before.action is RecoveryAction.ADVANCE_READ:
            DeterministicReadHost(
                self.storage,
                self.runtime,
                clock_ms=self.clock_ms,
                repository_resolver=self.repository_resolver,
                authorizer=self.authorizer,
            ).step(task_id)
        elif before.action is RecoveryAction.OBSERVE_RUNTIME_DISPATCH:
            if before.workload == "guarded-mutation":
                GuardedMutationHost(
                    self.storage,
                    self.runtime,
                    clock_ms=self.clock_ms,
                ).reconcile(task_id, wait_ms=wait_ms)
            elif before.workload == "code-change":
                CodeChangeHost(
                    self.storage,
                    self.runtime,
                    clock_ms=self.clock_ms,
                    repository_resolver=self.repository_resolver,
                    authorizer=self.authorizer,
                ).reconcile(task_id, wait_ms=wait_ms)
            else:  # pragma: no cover - guarded by assessment
                raise AssertionError("automatic Runtime recovery workload differs")
        else:  # pragma: no cover - guarded by assessment
            raise AssertionError("automatic recovery action differs")
        after = assess_recovery(self.storage, task_id)
        return RecoveryResult(before, after, before.revision != after.revision)


def _workload(storage: HostStorage, snapshot: TaskEventSnapshot) -> str:
    descriptor = storage.read_task_descriptor(snapshot.projection.task_id)
    if snapshot.event_kind.value.startswith("effect."):
        return "experimental-effect-lifecycle"
    if descriptor is not None:
        return descriptor.workload_id
    if snapshot.event_kind.value.startswith("cognition."):
        return "cognition"
    if not isinstance(snapshot.data, dict):
        return "unknown"
    plan_digest = snapshot.data.get("planDigest")
    if not isinstance(plan_digest, str):
        return "unknown"
    kind = storage.objects.inspect(plan_digest).kind
    return {
        "host-read-task-plan": "deterministic-read",
        "host-mutation-task-plan": "guarded-mutation",
        "host-code-change-plan": "code-change",
    }.get(kind, "unknown")


def _manual(
    snapshot: TaskEventSnapshot,
    workload: str,
    reason: str,
) -> RecoveryAssessment:
    projection = snapshot.projection
    frontier = (
        projection.ready_frontier[0]
        if len(projection.ready_frontier) == 1
        else None
    )
    return RecoveryAssessment(
        projection.task_id,
        projection.state,
        projection.revision,
        workload,
        snapshot.event_kind.value,
        frontier,
        RecoveryAction.MANUAL_STAGE,
        False,
        reason,
    )
