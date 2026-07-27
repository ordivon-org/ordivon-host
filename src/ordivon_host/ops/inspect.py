from __future__ import annotations

from pathlib import Path

from ..domain import TaskProjection, TaskState
from ..journal.migrations import migration_history, schema_version
from ..storage import HostStorage


def list_tasks(
    storage: HostStorage,
    *,
    state: TaskState | None = None,
    limit: int = 100,
) -> tuple[TaskProjection, ...]:
    if limit < 1 or limit > 10_000:
        raise ValueError("Task list limit must be in [1, 10000]")
    if state is None:
        rows = storage.journal.connection.execute(
            "SELECT task_id FROM task_projection "
            "ORDER BY updated_at_ms DESC, task_id LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = storage.journal.connection.execute(
            "SELECT task_id FROM task_projection WHERE state = ? "
            "ORDER BY updated_at_ms DESC, task_id LIMIT ?",
            (state.value, limit),
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
        tasks = list_tasks(storage, limit=10_000)
        states: dict[str, int] = {}
        for task in tasks:
            states[task.state.value] = states.get(task.state.value, 0) + 1
        lease_count = storage.journal.connection.execute(
            "SELECT COUNT(*) FROM leases"
        ).fetchone()[0]
        return {
            "schemaVersion": schema_version(storage.journal.connection),
            "stateRoot": str(state_root),
            "events": storage.journal.event_count(),
            "objectRefs": storage.journal.object_ref_count(),
            "tasks": len(tasks),
            "tasksByState": dict(sorted(states.items())),
            "leases": int(lease_count),
            "migrations": list(migration_history(storage.journal.connection)),
        }
