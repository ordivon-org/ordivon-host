from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from .migrations import SchemaMigrationError, initialize_schema
from ..domain import (
    EventAdmission,
    EventKind,
    HostEvent,
    StreamKind,
    TaskProjection,
    TaskState,
)
from ..objects import ObjectFileIdentity, StoredObject


class HostJournalError(RuntimeError):
    pass


class RevisionConflict(HostJournalError):
    pass


class EventConflict(HostJournalError):
    pass


class JournalCorruption(HostJournalError):
    pass


class LeaseHeld(HostJournalError):
    pass


class LeaseConflict(HostJournalError):
    pass


class TerminalTaskConflict(HostJournalError):
    pass


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    task_id: str
    owner_id: str
    revision: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class EventObjectReference:
    event_id: str
    digest: str
    role: str


@dataclass(frozen=True, slots=True)
class TaskHead:
    task_id: str
    event_kind: EventKind
    payload_digest: str
    revision: int


class HostJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.is_symlink():
            raise JournalCorruption("Host Journal cannot be a symlink")
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        os.chmod(self.path, 0o600)
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
            self._harden_database_files()
            try:
                initialize_schema(self.connection, self.path)
            except SchemaMigrationError as error:
                raise JournalCorruption(str(error)) from error
            self.validate_invariants()
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()
        self._harden_database_files()

    def _harden_database_files(self) -> None:
        for path in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise JournalCorruption(f"Host Journal file is not regular: {path.name}")
                os.chmod(path, 0o600)

    def __enter__(self) -> HostJournal:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def append_task_event(
        self,
        event: HostEvent,
        *,
        expected_revision: int,
        projection: TaskProjection,
        payload_object: StoredObject,
        referenced_objects: tuple[StoredObject, ...] = (),
        expected_lease: LeaseRecord | None = None,
        lease_checked_at_ms: int | None = None,
    ) -> EventAdmission:
        if event.stream_kind is not StreamKind.TASK:
            raise ValueError("task projection requires a task stream")
        if event.stream_id != projection.task_id:
            raise ValueError("event stream and Task projection identities differ")
        if event.payload_digest != payload_object.digest:
            raise ValueError("event payload digest differs from stored object")
        if payload_object.kind != "host-event-payload":
            raise ValueError("Host event payload has the wrong object kind")
        if projection.revision != expected_revision + 1:
            raise ValueError("projection revision must advance expected revision exactly once")
        if event.recorded_at_ms != projection.updated_at_ms:
            raise ValueError("event and projection timestamps must match")
        if expected_revision > 0 and expected_lease is None:
            raise LeaseConflict("non-creation Task event requires an exact live lease")
        if (expected_lease is None) != (lease_checked_at_ms is None):
            raise ValueError("lease identity and check time must be supplied together")
        if expected_lease is not None:
            if expected_revision < 1 or expected_lease.task_id != projection.task_id:
                raise ValueError("Task transition lease identity differs")
            if lease_checked_at_ms is None or lease_checked_at_ms < 0:
                raise ValueError("lease check time is invalid")

        with self._transaction():
            existing = self.connection.execute(
                "SELECT sequence, stream_id, stream_kind, event_kind, payload_digest, "
                "recorded_at_ms, caused_by_event_id FROM events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                expected = (
                    event.stream_id,
                    event.stream_kind.value,
                    event.kind.value,
                    event.payload_digest,
                    event.recorded_at_ms,
                    event.caused_by_event_id,
                )
                actual = tuple(existing)[1:]
                if actual != expected:
                    raise EventConflict("event identity is already bound to different content")
                if int(existing["sequence"]) >= self.event_object_refs_start_sequence():
                    expected_edges = self._event_object_edges(
                        payload_object, referenced_objects
                    )
                    actual_edges = {
                        (item.digest, item.role)
                        for item in self.event_object_references(event.event_id)
                    }
                    if actual_edges != expected_edges:
                        raise EventConflict(
                            "event identity is already bound to different object references"
                        )
                if expected_lease is not None:
                    self._validate_exact_lease(
                        expected_lease,
                        checked_at_ms=lease_checked_at_ms,
                    )
                    self._consume_exact_lease(expected_lease)
                return EventAdmission.EXISTING

            stream = self.connection.execute(
                "SELECT revision FROM streams WHERE stream_id = ?",
                (event.stream_id,),
            ).fetchone()
            current_revision = 0 if stream is None else int(stream["revision"])
            if current_revision != expected_revision:
                raise RevisionConflict(
                    f"stream revision is {current_revision}, expected {expected_revision}"
                )
            if expected_revision > 0:
                current_task = self.connection.execute(
                    "SELECT state FROM task_projection WHERE task_id = ?",
                    (event.stream_id,),
                ).fetchone()
                if current_task is None:
                    raise JournalCorruption("Task stream has no current projection")
                if TaskState(current_task["state"]).terminal:
                    raise TerminalTaskConflict("terminal Task cannot admit another event")
            if expected_lease is not None:
                self._validate_exact_lease(
                    expected_lease,
                    checked_at_ms=lease_checked_at_ms,
                )
            if event.caused_by_event_id is not None:
                cause = self.connection.execute(
                    "SELECT sequence FROM events WHERE event_id = ?",
                    (event.caused_by_event_id,),
                ).fetchone()
                if cause is None:
                    raise EventConflict("caused-by Event identity does not exist")

            self._admit_object(payload_object, event.recorded_at_ms)
            for referenced_object in referenced_objects:
                self._admit_object(referenced_object, event.recorded_at_ms)
            if stream is None:
                self.connection.execute(
                    "INSERT INTO streams(stream_id, stream_kind, revision, created_at_ms, updated_at_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event.stream_id,
                        event.stream_kind.value,
                        projection.revision,
                        event.recorded_at_ms,
                        event.recorded_at_ms,
                    ),
                )
            else:
                changed = self.connection.execute(
                    "UPDATE streams SET revision = ?, updated_at_ms = ? "
                    "WHERE stream_id = ? AND revision = ?",
                    (
                        projection.revision,
                        event.recorded_at_ms,
                        event.stream_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise RevisionConflict("stream revision changed during transaction")

            self.connection.execute(
                "INSERT INTO events(event_id, stream_id, stream_kind, stream_revision, event_kind, "
                "payload_digest, caused_by_event_id, recorded_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.stream_id,
                    event.stream_kind.value,
                    projection.revision,
                    event.kind.value,
                    event.payload_digest,
                    event.caused_by_event_id,
                    event.recorded_at_ms,
                ),
            )
            self.connection.executemany(
                "INSERT INTO event_object_refs(event_id, digest, role) VALUES (?, ?, ?)",
                (
                    (event.event_id, digest, role)
                    for digest, role in sorted(
                        self._event_object_edges(payload_object, referenced_objects)
                    )
                ),
            )
            self.connection.execute(
                "INSERT INTO task_projection(task_id, goal_id, state, active_node_id, ready_frontier_json, "
                "revision, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET goal_id = excluded.goal_id, state = excluded.state, "
                "active_node_id = excluded.active_node_id, ready_frontier_json = excluded.ready_frontier_json, "
                "revision = excluded.revision, updated_at_ms = excluded.updated_at_ms",
                (
                    projection.task_id,
                    projection.goal_id,
                    projection.state.value,
                    projection.active_node_id,
                    json.dumps(list(projection.ready_frontier), separators=(",", ":")),
                    projection.revision,
                    projection.updated_at_ms,
                ),
            )
            if expected_lease is not None:
                self._consume_exact_lease(expected_lease)
        return EventAdmission.CREATED

    def _validate_exact_lease(
        self,
        lease: LeaseRecord,
        *,
        checked_at_ms: int | None,
    ) -> None:
        if checked_at_ms is None:
            raise ValueError("lease check time is required")
        current = self.connection.execute(
            "SELECT owner_id, revision, expires_at_ms FROM leases WHERE task_id = ?",
            (lease.task_id,),
        ).fetchone()
        if (
            current is None
            or current["owner_id"] != lease.owner_id
            or int(current["revision"]) != lease.revision
            or int(current["expires_at_ms"]) != lease.expires_at_ms
            or int(current["expires_at_ms"]) <= checked_at_ms
        ):
            raise LeaseConflict("Task transition lease is absent, superseded, or expired")

    def _consume_exact_lease(self, lease: LeaseRecord) -> None:
        changed = self.connection.execute(
            "DELETE FROM leases WHERE task_id = ? AND owner_id = ? AND revision = ?",
            (lease.task_id, lease.owner_id, lease.revision),
        ).rowcount
        if changed != 1:
            raise LeaseConflict("Task transition lease changed during admission")

    def get_task(self, task_id: str) -> TaskProjection | None:
        row = self.connection.execute(
            "SELECT task_id, goal_id, state, active_node_id, ready_frontier_json, revision, updated_at_ms "
            "FROM task_projection WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            frontier = json.loads(row["ready_frontier_json"])
            if not isinstance(frontier, list) or any(
                not isinstance(item, str) for item in frontier
            ):
                raise ValueError("Task ready frontier is not a string list")
            return TaskProjection(
                task_id=row["task_id"],
                goal_id=row["goal_id"],
                state=TaskState(row["state"]),
                active_node_id=row["active_node_id"],
                ready_frontier=tuple(frontier),
                revision=int(row["revision"]),
                updated_at_ms=int(row["updated_at_ms"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise JournalCorruption(f"Task projection is invalid: {task_id}") from error

    def event_count(self, stream_id: str | None = None) -> int:
        if stream_id is None:
            row = self.connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM events WHERE stream_id = ?", (stream_id,)
            ).fetchone()
        return int(row["count"])

    def object_ref_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM object_refs").fetchone()
        return int(row["count"])

    def object_refs(self) -> tuple[StoredObject, ...]:
        rows = self.connection.execute(
            "SELECT digest, byte_length, kind FROM object_refs ORDER BY digest"
        ).fetchall()
        return tuple(
            StoredObject(row["digest"], int(row["byte_length"]), row["kind"])
            for row in rows
        )

    def event_object_refs_start_sequence(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM host_metadata "
            "WHERE key = 'event_object_refs_start_sequence'"
        ).fetchone()
        if row is None:
            raise JournalCorruption("Event-object reference boundary is missing")
        try:
            value = int(row["value"])
        except (TypeError, ValueError) as error:
            raise JournalCorruption(
                "Event-object reference boundary is invalid"
            ) from error
        if value < 1:
            raise JournalCorruption("Event-object reference boundary is invalid")
        return value

    def event_object_references(
        self, event_id: str
    ) -> tuple[EventObjectReference, ...]:
        rows = self.connection.execute(
            "SELECT event_id, digest, role FROM event_object_refs "
            "WHERE event_id = ? ORDER BY role, digest",
            (event_id,),
        ).fetchall()
        return tuple(
            EventObjectReference(row["event_id"], row["digest"], row["role"])
            for row in rows
        )

    def object_reference_validation_rows(self) -> sqlite3.Cursor:
        return self.connection.execute(
            "SELECT r.digest, r.byte_length, r.kind, "
            "v.device, v.inode, v.byte_length AS validated_byte_length, "
            "v.modified_at_ns, v.changed_at_ns, v.mode "
            "FROM object_refs r LEFT JOIN object_validation v ON v.digest = r.digest "
            "ORDER BY r.digest"
        )

    def record_object_validations(
        self,
        values: tuple[tuple[str, ObjectFileIdentity], ...],
    ) -> None:
        if not values:
            return
        with self._transaction():
            self.connection.executemany(
                "INSERT INTO object_validation("
                "digest, device, inode, byte_length, modified_at_ns, changed_at_ns, mode"
                ") VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(digest) DO UPDATE SET "
                "device = excluded.device, inode = excluded.inode, "
                "byte_length = excluded.byte_length, "
                "modified_at_ns = excluded.modified_at_ns, "
                "changed_at_ns = excluded.changed_at_ns, mode = excluded.mode",
                (
                    (digest, *identity.to_sql())
                    for digest, identity in values
                ),
            )

    def object_validation_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM object_validation"
        ).fetchone()
        return int(row["count"])

    def quick_check(self) -> tuple[str, ...]:
        rows = self.connection.execute("PRAGMA quick_check").fetchall()
        return tuple(str(row[0]) for row in rows)

    def task_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM task_projection"
        ).fetchone()
        return int(row["count"])

    def task_counts_by_state(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) AS count FROM task_projection GROUP BY state ORDER BY state"
        ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def task_ids(self) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT task_id FROM task_projection ORDER BY task_id"
        ).fetchall()
        return tuple(row["task_id"] for row in rows)

    def tasks_for_goal(self, goal_id: str) -> tuple[TaskProjection, ...]:
        if not goal_id.startswith("goal:") or goal_id != goal_id.strip():
            raise ValueError("Goal identity must start with goal:")
        rows = self.connection.execute(
            "SELECT task_id FROM task_projection WHERE goal_id = ? ORDER BY task_id",
            (goal_id,),
        ).fetchall()
        tasks: list[TaskProjection] = []
        for row in rows:
            task = self.get_task(row["task_id"])
            if task is None:  # pragma: no cover - same table query
                raise JournalCorruption("Goal query returned a missing Task")
            tasks.append(task)
        return tuple(tasks)

    def get_task_head(self, task_id: str) -> TaskHead | None:
        row = self.connection.execute(
            "SELECT e.stream_id, e.event_kind, e.payload_digest, e.stream_revision "
            "FROM events e JOIN streams s ON s.stream_id = e.stream_id "
            "WHERE e.stream_id = ? AND e.stream_revision = s.revision",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return TaskHead(
                task_id=row["stream_id"],
                event_kind=EventKind(row["event_kind"]),
                payload_digest=row["payload_digest"],
                revision=int(row["stream_revision"]),
            )
        except (TypeError, ValueError) as error:
            raise JournalCorruption(f"Task event head is invalid: {task_id}") from error

    def lease_records(self) -> tuple[LeaseRecord, ...]:
        rows = self.connection.execute(
            "SELECT task_id, owner_id, revision, expires_at_ms "
            "FROM leases ORDER BY task_id"
        ).fetchall()
        return tuple(
            LeaseRecord(
                task_id=row["task_id"],
                owner_id=row["owner_id"],
                revision=int(row["revision"]),
                expires_at_ms=int(row["expires_at_ms"]),
            )
            for row in rows
        )

    def acquire_lease(
        self,
        task_id: str,
        *,
        owner_id: str,
        now_ms: int,
        ttl_ms: int,
    ) -> LeaseRecord:
        if not owner_id or owner_id != owner_id.strip():
            raise ValueError("lease owner must be non-empty and trimmed")
        if now_ms < 0 or ttl_ms < 1:
            raise ValueError("lease time and TTL are invalid")
        expires_at_ms = now_ms + ttl_ms
        with self._transaction():
            task = self.connection.execute(
                "SELECT 1 FROM task_projection WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(f"unknown Task: {task_id}")
            row = self.connection.execute(
                "SELECT owner_id, revision, expires_at_ms FROM leases WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                revision = 1
                self.connection.execute(
                    "INSERT INTO leases(task_id, owner_id, revision, expires_at_ms) VALUES (?, ?, ?, ?)",
                    (task_id, owner_id, revision, expires_at_ms),
                )
            elif row["owner_id"] == owner_id or int(row["expires_at_ms"]) <= now_ms:
                revision = int(row["revision"]) + 1
                self.connection.execute(
                    "UPDATE leases SET owner_id = ?, revision = ?, expires_at_ms = ? WHERE task_id = ?",
                    (owner_id, revision, expires_at_ms, task_id),
                )
            else:
                raise LeaseHeld(
                    f"Task lease is held by {row['owner_id']} until {row['expires_at_ms']}"
                )
        return LeaseRecord(task_id, owner_id, revision, expires_at_ms)

    def release_lease(self, lease: LeaseRecord) -> None:
        with self._transaction():
            changed = self.connection.execute(
                "DELETE FROM leases WHERE task_id = ? AND owner_id = ? AND revision = ?",
                (lease.task_id, lease.owner_id, lease.revision),
            ).rowcount
            if changed != 1:
                raise LeaseConflict("lease identity, owner, or revision no longer matches")

    def validate_invariants(self) -> None:
        stream_mismatch = self.connection.execute(
            "SELECT s.stream_id FROM streams s LEFT JOIN "
            "(SELECT stream_id, MAX(stream_revision) AS revision FROM events GROUP BY stream_id) e "
            "ON e.stream_id = s.stream_id WHERE e.revision IS NULL OR e.revision != s.revision LIMIT 1"
        ).fetchone()
        if stream_mismatch is not None:
            raise JournalCorruption(
                f"stream revision differs from event history: {stream_mismatch['stream_id']}"
            )

        history_gap = self.connection.execute(
            "SELECT stream_id FROM events GROUP BY stream_id "
            "HAVING MIN(stream_revision) != 1 OR COUNT(*) != MAX(stream_revision) LIMIT 1"
        ).fetchone()
        if history_gap is not None:
            raise JournalCorruption(
                f"event history is not contiguous: {history_gap['stream_id']}"
            )

        kind_mismatch = self.connection.execute(
            "SELECT e.event_id FROM events e JOIN streams s ON s.stream_id = e.stream_id "
            "WHERE e.stream_kind != s.stream_kind LIMIT 1"
        ).fetchone()
        if kind_mismatch is not None:
            raise JournalCorruption(
                f"event stream kind differs from stream: {kind_mismatch['event_id']}"
            )

        missing_projection = self.connection.execute(
            "SELECT s.stream_id FROM streams s LEFT JOIN task_projection p "
            "ON p.task_id = s.stream_id "
            "WHERE s.stream_kind = 'task' AND p.task_id IS NULL LIMIT 1"
        ).fetchone()
        if missing_projection is not None:
            raise JournalCorruption(
                f"Task stream has no projection: {missing_projection['stream_id']}"
            )

        dangling_cause = self.connection.execute(
            "SELECT child.event_id FROM events child LEFT JOIN events parent "
            "ON parent.event_id = child.caused_by_event_id "
            "WHERE child.caused_by_event_id IS NOT NULL AND parent.event_id IS NULL LIMIT 1"
        ).fetchone()
        if dangling_cause is not None:
            raise JournalCorruption(
                f"Event has a dangling cause: {dangling_cause['event_id']}"
            )

        projection_mismatch = self.connection.execute(
            "SELECT p.task_id FROM task_projection p JOIN streams s ON s.stream_id = p.task_id "
            "WHERE s.stream_kind != 'task' OR s.revision != p.revision LIMIT 1"
        ).fetchone()
        if projection_mismatch is not None:
            raise JournalCorruption(
                f"Task projection differs from stream head: {projection_mismatch['task_id']}"
            )

        boundary = self.event_object_refs_start_sequence()
        next_sequence = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM events"
            ).fetchone()["value"]
        )
        if boundary > next_sequence:
            raise JournalCorruption(
                "Event-object reference boundary is beyond Journal history"
            )
        missing_payload_edge = self.connection.execute(
            "SELECT e.event_id FROM events e LEFT JOIN event_object_refs r "
            "ON r.event_id = e.event_id AND r.role = 'payload' "
            "WHERE e.sequence >= ? AND "
            "(r.digest IS NULL OR r.digest != e.payload_digest) LIMIT 1",
            (boundary,),
        ).fetchone()
        if missing_payload_edge is not None:
            raise JournalCorruption(
                "Event is missing its exact payload object edge: "
                f"{missing_payload_edge['event_id']}"
            )

        for row in self.connection.execute(
            "SELECT task_id FROM task_projection ORDER BY task_id"
        ):
            self.get_task(row["task_id"])

    @staticmethod
    def _event_object_edges(
        payload_object: StoredObject,
        referenced_objects: tuple[StoredObject, ...],
    ) -> set[tuple[str, str]]:
        edges = {(payload_object.digest, "payload")}
        for value in referenced_objects:
            edge = (value.digest, "reference")
            if value.digest == payload_object.digest:
                raise ValueError(
                    "Host event payload cannot also be an explicit reference"
                )
            edges.add(edge)
        return edges

    def _admit_object(self, value: StoredObject, first_seen_at_ms: int) -> None:
        existing = self.connection.execute(
            "SELECT kind, byte_length FROM object_refs WHERE digest = ?", (value.digest,)
        ).fetchone()
        if existing is not None:
            if (existing["kind"], int(existing["byte_length"])) != (
                value.kind,
                value.byte_length,
            ):
                raise JournalCorruption("object digest metadata differs")
            return
        self.connection.execute(
            "INSERT INTO object_refs(digest, kind, byte_length, first_seen_at_ms) VALUES (?, ?, ?, ?)",
            (value.digest, value.kind, value.byte_length, first_seen_at_ms),
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")
