from __future__ import annotations

from pathlib import Path

from ..domain import TaskProjection, TaskState
from ..journal.migrations import migration_history, schema_version
from ..storage import HostStorage


def list_tasks(
    storage: HostStorage,
    *,
    state: TaskState | None = None,
    goal_id: str | None = None,
    limit: int = 100,
) -> tuple[TaskProjection, ...]:
    if limit < 1 or limit > 10_000:
        raise ValueError("Task list limit must be in [1, 10000]")
    if goal_id is not None and (
        not goal_id.startswith("goal:") or goal_id != goal_id.strip()
    ):
        raise ValueError("Goal identity must start with goal:")
    clauses: list[str] = []
    params: list[object] = []
    if state is not None:
        clauses.append("state = ?")
        params.append(state.value)
    if goal_id is not None:
        clauses.append("goal_id = ?")
        params.append(goal_id)
    where = "" if not clauses else " WHERE " + " AND ".join(clauses)
    rows = storage.journal.connection.execute(
        "SELECT task_id FROM task_projection" + where
        + " ORDER BY updated_at_ms DESC, task_id LIMIT ?",
        (*params, limit),
    ).fetchall()
    tasks = tuple(storage.journal.get_task(row["task_id"]) for row in rows)
    if any(task is None for task in tasks):
        raise RuntimeError("Task disappeared during list projection")
    return tuple(task for task in tasks if task is not None)


def inspect_state(root: str | Path) -> dict[str, object]:
    state_root = Path(root)
    if not (state_root / "host.sqlite3").is_file():
        raise FileNotFoundError(state_root / "host.sqlite3")
    with HostStorage(state_root) as storage:
        states = storage.journal.task_counts_by_state()
        task_count = storage.journal.task_count()
        terminal_count = sum(
            count
            for state, count in states.items()
            if TaskState(state).terminal
        )
        lease_count = storage.journal.connection.execute(
            "SELECT COUNT(*) FROM leases"
        ).fetchone()[0]
        return {
            "schemaVersion": schema_version(storage.journal.connection),
            "stateRoot": str(state_root),
            "events": storage.journal.event_count(),
            "objectRefs": storage.journal.object_ref_count(),
            "validatedObjects": storage.journal.object_validation_count(),
            "startupValidation": {
                "cachedObjects": storage.validation_summary.cached_objects,
                "hashedObjects": storage.validation_summary.hashed_objects,
                "taskHeads": storage.validation_summary.task_heads,
                "full": storage.validation_summary.full,
            },
            "tasks": task_count,
            "terminalTasks": terminal_count,
            "tasksByState": states,
            "leases": int(lease_count),
            "migrations": list(migration_history(storage.journal.connection)),
        }
