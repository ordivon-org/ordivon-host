from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from anc_canonical import JsonValue


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


class TaskState(StrEnum):
    PROPOSED = "proposed"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}


@dataclass(frozen=True, slots=True)
class TaskProjection:
    task_id: str
    goal_id: str
    state: TaskState
    active_node_id: str | None
    ready_frontier: tuple[str, ...]
    revision: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not self.task_id.startswith("task:") or self.task_id != self.task_id.strip():
            raise ValueError("task identity must start with task:")
        if not self.goal_id.startswith("goal:") or self.goal_id != self.goal_id.strip():
            raise ValueError("goal identity must start with goal:")
        if self.active_node_id is not None and not self.active_node_id.startswith("node:"):
            raise ValueError("active node identity must start with node:")
        if any(not item.startswith("node:") for item in self.ready_frontier):
            raise ValueError("ready frontier identities must start with node:")
        if len(set(self.ready_frontier)) != len(self.ready_frontier):
            raise ValueError("ready frontier identities must be unique")
        if self.active_node_id is not None and self.active_node_id in self.ready_frontier:
            raise ValueError("active node cannot remain in the ready frontier")
        if self.revision < 1 or self.updated_at_ms < 0:
            raise ValueError("projection revision and time are invalid")
        if self.state is TaskState.READY and not self.ready_frontier:
            raise ValueError("ready Task requires a non-empty frontier")
        if self.state is TaskState.RUNNING and self.active_node_id is None:
            raise ValueError("running Task requires an active node")
        if self.state.terminal and (self.active_node_id is not None or self.ready_frontier):
            raise ValueError("terminal Task cannot retain active or ready nodes")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "state": self.state.value,
            "activeNodeId": self.active_node_id,
            "readyFrontier": list(self.ready_frontier),
            "revision": self.revision,
            "updatedAtMs": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskProjection:
        _exact(
            value,
            {
                "taskId",
                "goalId",
                "state",
                "activeNodeId",
                "readyFrontier",
                "revision",
                "updatedAtMs",
            },
            "TaskProjection",
        )
        task_id = value["taskId"]
        goal_id = value["goalId"]
        state = value["state"]
        active_node_id = value["activeNodeId"]
        frontier = value["readyFrontier"]
        revision = value["revision"]
        updated_at_ms = value["updatedAtMs"]
        if not isinstance(task_id, str) or not isinstance(goal_id, str):
            raise ValueError("Task and Goal identities must be strings")
        if not isinstance(state, str):
            raise ValueError("Task state must be a string")
        if active_node_id is not None and not isinstance(active_node_id, str):
            raise ValueError("activeNodeId must be a string or null")
        if not isinstance(frontier, list) or any(not isinstance(item, str) for item in frontier):
            raise ValueError("readyFrontier must be a list of strings")
        if type(revision) is not int or type(updated_at_ms) is not int:
            raise ValueError("Task revision and update time must be integers")
        return cls(
            task_id=task_id,
            goal_id=goal_id,
            state=TaskState(state),
            active_node_id=active_node_id,
            ready_frontier=tuple(frontier),
            revision=revision,
            updated_at_ms=updated_at_ms,
        )
