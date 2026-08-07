from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue, canonical_bytes, canonical_digest

from .domain import EventAdmission, TaskProjection
from .handoff import OperatorHandoffCapsule

EXTERNAL_CONTINUITY_WORKLOAD_ID = "ordivon.host.external-continuity.v1"
WORKING_CHECKPOINT_OBJECT_KIND = "host-working-checkpoint"
WORKING_CHECKPOINT_WIRE_KIND = "ordivon.host-working-checkpoint"
MAX_CHECKPOINT_BYTES = 65_536
MAX_CHECKPOINT_ITEMS = 64
MAX_CHECKPOINT_ITEM_BYTES = 4_096


def _text(value: object, label: str, *, max_bytes: int = MAX_CHECKPOINT_ITEM_BYTES) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(f"{label} must be a non-empty trimmed string")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _items(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")
    if len(value) > MAX_CHECKPOINT_ITEMS:
        raise ValueError(f"{label} exceeds {MAX_CHECKPOINT_ITEMS} items")
    items = tuple(_text(item, f"{label} item") for item in value)
    if len(set(items)) != len(items):
        raise ValueError(f"{label} items must be unique")
    return items


@dataclass(frozen=True, slots=True)
class WorkingCheckpointRuntime:
    workspace_id: str
    relevant_job_ids: tuple[str, ...] = ()
    observed_head_revision: str | None = None

    def __post_init__(self) -> None:
        _text(self.workspace_id, "runtime workspaceId", max_bytes=512)
        if len(self.relevant_job_ids) > MAX_CHECKPOINT_ITEMS:
            raise ValueError(
                f"runtime relevantJobIds exceeds {MAX_CHECKPOINT_ITEMS} items"
            )
        for value in self.relevant_job_ids:
            _text(value, "runtime Job identity", max_bytes=512)
        if len(set(self.relevant_job_ids)) != len(self.relevant_job_ids):
            raise ValueError("runtime relevantJobIds must be unique")
        if self.observed_head_revision is not None:
            _text(
                self.observed_head_revision,
                "runtime observedHeadRevision",
                max_bytes=512,
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "workspaceId": self.workspace_id,
            "relevantJobIds": list(self.relevant_job_ids),
            "observedHeadRevision": self.observed_head_revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkingCheckpointRuntime:
        expected = {"workspaceId", "relevantJobIds", "observedHeadRevision"}
        if set(value) != expected:
            raise ValueError(
                f"WorkingCheckpoint runtime fields differ: {sorted(set(value) ^ expected)}"
            )
        workspace_id = value["workspaceId"]
        observed_head_revision = value["observedHeadRevision"]
        if not isinstance(workspace_id, str):
            raise ValueError("runtime workspaceId must be a string")
        if observed_head_revision is not None and not isinstance(
            observed_head_revision, str
        ):
            raise ValueError("runtime observedHeadRevision must be a string or null")
        return cls(
            workspace_id=workspace_id,
            relevant_job_ids=_items(value["relevantJobIds"], "runtime relevantJobIds"),
            observed_head_revision=observed_head_revision,
        )


@dataclass(frozen=True, slots=True)
class WorkingCheckpoint:
    task_id: str
    objective: str
    frontier: str
    established: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    runtime: WorkingCheckpointRuntime | None = None

    def __post_init__(self) -> None:
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("WorkingCheckpoint task identity must start with task:")
        _text(self.objective, "WorkingCheckpoint objective")
        _text(self.frontier, "WorkingCheckpoint frontier")
        for label, values in (
            ("established", self.established),
            ("unresolved", self.unresolved),
            ("rejected", self.rejected),
            ("constraints", self.constraints),
            ("nextActions", self.next_actions),
        ):
            if len(values) > MAX_CHECKPOINT_ITEMS:
                raise ValueError(
                    f"WorkingCheckpoint {label} exceeds {MAX_CHECKPOINT_ITEMS} items"
                )
            for item in values:
                _text(item, f"WorkingCheckpoint {label} item")
            if len(set(values)) != len(values):
                raise ValueError(f"WorkingCheckpoint {label} items must be unique")
        if len(canonical_bytes(self.to_dict())) > MAX_CHECKPOINT_BYTES:
            raise ValueError(
                f"WorkingCheckpoint exceeds {MAX_CHECKPOINT_BYTES} canonical bytes"
            )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": WORKING_CHECKPOINT_WIRE_KIND,
            "truthRole": "semantic-working-claim",
            "taskId": self.task_id,
            "objective": self.objective,
            "frontier": self.frontier,
            "established": list(self.established),
            "unresolved": list(self.unresolved),
            "rejected": list(self.rejected),
            "constraints": list(self.constraints),
            "nextActions": list(self.next_actions),
            "runtime": None if self.runtime is None else self.runtime.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkingCheckpoint:
        expected = {
            "schemaVersion",
            "kind",
            "truthRole",
            "taskId",
            "objective",
            "frontier",
            "established",
            "unresolved",
            "rejected",
            "constraints",
            "nextActions",
            "runtime",
        }
        if set(value) != expected:
            raise ValueError(
                f"WorkingCheckpoint fields differ: {sorted(set(value) ^ expected)}"
            )
        if value["schemaVersion"] != 1:
            raise ValueError("WorkingCheckpoint schemaVersion must be 1")
        if value["kind"] != WORKING_CHECKPOINT_WIRE_KIND:
            raise ValueError("wire object is not a WorkingCheckpoint")
        if value["truthRole"] != "semantic-working-claim":
            raise ValueError(
                "WorkingCheckpoint truthRole must be semantic-working-claim"
            )
        task_id = value["taskId"]
        objective = value["objective"]
        frontier = value["frontier"]
        runtime_value = value["runtime"]
        if not isinstance(task_id, str):
            raise ValueError("WorkingCheckpoint taskId must be a string")
        if not isinstance(objective, str) or not isinstance(frontier, str):
            raise ValueError("WorkingCheckpoint objective and frontier must be strings")
        if runtime_value is not None and not isinstance(runtime_value, dict):
            raise ValueError("WorkingCheckpoint runtime must be an object or null")
        return cls(
            task_id=task_id,
            objective=objective,
            frontier=frontier,
            established=_items(value["established"], "WorkingCheckpoint established"),
            unresolved=_items(value["unresolved"], "WorkingCheckpoint unresolved"),
            rejected=_items(value["rejected"], "WorkingCheckpoint rejected"),
            constraints=_items(value["constraints"], "WorkingCheckpoint constraints"),
            next_actions=_items(value["nextActions"], "WorkingCheckpoint nextActions"),
            runtime=(
                None
                if runtime_value is None
                else WorkingCheckpointRuntime.from_dict(runtime_value)
            ),
        )


@dataclass(frozen=True, slots=True)
class WorkingCheckpointRecord:
    checkpoint: WorkingCheckpoint
    checkpoint_digest: str
    checkpoint_object_digest: str
    task_revision: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "checkpoint": self.checkpoint.to_dict(),
            "checkpointDigest": self.checkpoint_digest,
            "checkpointObjectDigest": self.checkpoint_object_digest,
            "taskRevision": self.task_revision,
        }


@dataclass(frozen=True, slots=True)
class CheckpointReceipt:
    admission: EventAdmission
    projection: TaskProjection
    record: WorkingCheckpointRecord

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-working-checkpoint-receipt",
            "admission": self.admission.value,
            "projection": self.projection.to_dict(),
            **self.record.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExternalContinuityResume:
    projection: TaskProjection
    handoff: OperatorHandoffCapsule
    checkpoint: WorkingCheckpointRecord | None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-external-continuity-resume",
            "projection": self.projection.to_dict(),
            "handoff": self.handoff.to_dict(),
            "handoffDigest": self.handoff.digest,
            "checkpoint": (
                None if self.checkpoint is None else self.checkpoint.to_dict()
            ),
        }
