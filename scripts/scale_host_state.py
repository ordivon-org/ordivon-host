#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from anc_canonical import JsonValue, canonical_bytes, canonical_digest
from ordivon_host import HostStorage, TaskState
from ordivon_host.ops import doctor_state, inspect_state, list_tasks
from ordivon_host.testing import ScenarioIdentity, emit_receipt

_EVENT_PAYLOAD_KIND = "ordivon.host-task-event"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and measure a large internally consistent Host state fixture."
    )
    parser.add_argument("--tasks", type=int, default=1_000)
    parser.add_argument("--events-per-task", type=int, default=100)
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.tasks <= 10_000:
        raise SystemExit("tasks must be in [1, 10000]")
    if not 1 <= args.events_per_task <= 1_000:
        raise SystemExit("events-per-task must be in [1, 1000]")
    identity = ScenarioIdentity.create("scale")
    host_revision = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(Path(__file__).resolve().parents[1]),
            "rev-parse",
            "HEAD",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    root = Path(
        args.state_root
        or tempfile.mkdtemp(prefix=f"ordivon-host-scale-{identity.nonce}-", dir="/tmp")
    )
    if args.state_root:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
    completed = False
    try:
        build_started = time.perf_counter()
        fixture = build_fixture(root, args.tasks, args.events_per_task)
        build_ms = _elapsed_ms(build_started)
        first = measure_open_subprocess(root)
        second = measure_open_subprocess(root)
        third = measure_open_subprocess(root)
        query_started = time.perf_counter()
        with HostStorage(root) as storage:
            opened_ms = _elapsed_ms(query_started)
            list_started = time.perf_counter()
            tasks = list_tasks(storage, limit=min(args.tasks, 10_000))
            list_ms = _elapsed_ms(list_started)
            show_started = time.perf_counter()
            shown = storage.journal.get_task(tasks[-1].task_id)
            show_ms = _elapsed_ms(show_started)
            invariant_started = time.perf_counter()
            storage.journal.validate_invariants()
            invariant_ms = _elapsed_ms(invariant_started)
            refs = storage.journal.object_ref_count()
            events = storage.journal.event_count()
        inspect_started = time.perf_counter()
        inspection = inspect_state(root)
        inspect_ms = _elapsed_ms(inspect_started)
        doctor_started = time.perf_counter()
        doctor = doctor_state(root)
        doctor_ms = _elapsed_ms(doctor_started)
        if shown is None or shown.state is not TaskState.COMPLETED:
            raise AssertionError("scale fixture Task lookup failed")
        checks = {
            "taskCountExact": len(tasks) == args.tasks,
            "eventCountExact": events == args.tasks * args.events_per_task,
            "objectCountExact": refs == args.tasks * args.events_per_task,
            "allTasksTerminal": all(task.state is TaskState.COMPLETED for task in tasks),
            "doctorHealthy": doctor["healthy"] is True,
            "inspectionMatches": (
                inspection["tasks"] == args.tasks
                and inspection["terminalTasks"] == args.tasks
                and inspection["events"] == events
                and inspection["objectRefs"] == refs
                and inspection["validatedObjects"] == refs
            ),
            "threeFreshProcessesOpened": all(
                result["taskCount"] == args.tasks
                and result["eventCount"] == events
                and result["objectCount"] == refs
                for result in (first, second, third)
            ),
        }
        if not all(checks.values()):
            raise AssertionError(f"scale checks failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-scale-measurement",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "hostRevision": host_revision,
            "fixture": {
                **fixture,
                "buildElapsedMs": build_ms,
                "stateRoot": str(root) if args.keep_state else None,
            },
            "startup": {
                "freshProcessRuns": [first, second, third],
                "inProcessOpenElapsedMs": opened_ms,
            },
            "queries": {
                "listElapsedMs": list_ms,
                "showElapsedMs": show_ms,
                "invariantElapsedMs": invariant_ms,
                "inspectElapsedMs": inspect_ms,
                "doctorElapsedMs": doctor_ms,
            },
            "inspection": inspection,
            "doctor": doctor,
            "checks": checks,
        }
        completed = True
        emit_receipt(receipt)
    finally:
        if not args.keep_state:
            shutil.rmtree(root, ignore_errors=True)
        elif not completed:
            print(f"state retained after failure: {root}", file=sys.stderr)


def build_fixture(root: Path, tasks: int, events_per_task: int) -> dict[str, JsonValue]:
    with HostStorage(root):
        pass
    database = root / "host.sqlite3"
    objects = root / "objects"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("BEGIN IMMEDIATE")
    object_rows: list[tuple[str, str, int, int]] = []
    event_rows: list[tuple[str, str, str, int, str, str, None, int]] = []
    written_objects = 0
    try:
        for task_index in range(tasks):
            task_id = f"task:scale-{task_index:06d}"
            goal_id = f"goal:scale-{task_index:06d}"
            node_id = f"node:scale-{task_index:06d}:work"
            connection.execute(
                "INSERT INTO streams(stream_id, stream_kind, revision, created_at_ms, updated_at_ms) "
                "VALUES (?, 'task', ?, 1, ?)",
                (task_id, events_per_task, events_per_task),
            )
            final_projection: dict[str, JsonValue] | None = None
            for revision in range(1, events_per_task + 1):
                terminal = revision == events_per_task
                if terminal:
                    state = "completed"
                    active_node: str | None = None
                    frontier: list[str] = []
                elif revision == 1:
                    state = "ready"
                    active_node = None
                    frontier = [node_id]
                else:
                    state = "running"
                    active_node = node_id
                    frontier = []
                projection: dict[str, JsonValue] = {
                    "taskId": task_id,
                    "goalId": goal_id,
                    "state": state,
                    "activeNodeId": active_node,
                    "readyFrontier": frontier,
                    "revision": revision,
                    "updatedAtMs": revision,
                }
                final_projection = projection
                event_kind = "task.created" if revision == 1 else "task.state-changed"
                envelope: JsonValue = {
                    "schemaVersion": 1,
                    "kind": "ordivon.host-task-event",
                    "eventKind": event_kind,
                    "data": {
                        "fixture": True,
                        "taskIndex": task_index,
                        "revision": revision,
                    },
                    "projection": projection,
                }
                object_envelope: JsonValue = {
                    "schemaVersion": 1,
                    "kind": "host-event-payload",
                    "payload": envelope,
                }
                encoded = canonical_bytes(object_envelope)
                digest = canonical_digest(object_envelope)
                path = objects / f"{digest[7:]}.json"
                path.write_bytes(encoded)
                written_objects += 1
                object_rows.append((digest, "host-event-payload", len(encoded), revision))
                event_rows.append(
                    (
                        f"event:scale-{task_index:06d}:r{revision}",
                        task_id,
                        "task",
                        revision,
                        event_kind,
                        digest,
                        None,
                        revision,
                    )
                )
                if len(object_rows) >= 2_000:
                    _flush(connection, object_rows, event_rows)
                    object_rows.clear()
                    event_rows.clear()
            if final_projection is None:
                raise AssertionError("scale Task had no final projection")
            connection.execute(
                "INSERT INTO task_projection(task_id, goal_id, state, active_node_id, "
                "ready_frontier_json, revision, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    goal_id,
                    final_projection["state"],
                    final_projection["activeNodeId"],
                    json.dumps(final_projection["readyFrontier"], separators=(",", ":")),
                    events_per_task,
                    events_per_task,
                ),
            )
        _flush(connection, object_rows, event_rows)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    sqlite_bytes = database.stat().st_size
    object_bytes = sum(path.stat().st_size for path in objects.glob("*.json"))
    return {
        "tasks": tasks,
        "eventsPerTask": events_per_task,
        "events": tasks * events_per_task,
        "objects": written_objects,
        "sqliteBytes": sqlite_bytes,
        "objectBytes": object_bytes,
        "objectFiles": sum(1 for _ in objects.glob("*.json")),
    }


def _flush(
    connection: sqlite3.Connection,
    objects: list[tuple[str, str, int, int]],
    events: list[tuple[str, str, str, int, str, str, None, int]],
) -> None:
    if objects:
        connection.executemany(
            "INSERT INTO object_refs(digest, kind, byte_length, first_seen_at_ms) "
            "VALUES (?, ?, ?, ?)",
            objects,
        )
    if events:
        connection.executemany(
            "INSERT INTO events(event_id, stream_id, stream_kind, stream_revision, "
            "event_kind, payload_digest, caused_by_event_id, recorded_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            events,
        )


def measure_open_subprocess(root: Path) -> dict[str, JsonValue]:
    code = """
import json, resource, sys, time
from ordivon_host import HostStorage
started=time.perf_counter()
with HostStorage(sys.argv[1]) as storage:
    elapsed=int(round((time.perf_counter()-started)*1000))
    print(json.dumps({
        'elapsedMs':elapsed,
        'maxRssKiB':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        'taskCount':len(storage.journal.task_ids()),
        'eventCount':storage.journal.event_count(),
        'objectCount':storage.journal.object_ref_count(),
        'validation':{
            'cachedObjects':storage.validation_summary.cached_objects,
            'hashedObjects':storage.validation_summary.hashed_objects,
            'taskHeads':storage.validation_summary.task_heads,
            'full':storage.validation_summary.full,
        },
    }, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
    )
    return json.loads(completed.stdout)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1_000))


if __name__ == "__main__":
    main()
