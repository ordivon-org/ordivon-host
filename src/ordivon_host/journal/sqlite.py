from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import stat
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


@dataclass(frozen=True, slots=True)
class TaskEventPointer:
    event_id: str
    task_id: str
    event_kind: EventKind
    payload_digest: str
    revision: int


@dataclass(frozen=True, slots=True)
class TaskExtensionStatePointer:
    task_id: str
    namespace: str
    state_digest: str
    event_id: str
    event_kind: EventKind
    revision: int
    legacy: bool


@dataclass(frozen=True, slots=True)
class BoardMessagePointer:
    sequence: int
    client_message_id: str
    author_label: str
    message_kind: str
    topic: str | None
    message_digest: str
    reply_to_client_message_id: str | None
    recorded_at_ms: int


@dataclass(frozen=True, slots=True)
class NewsPublicationPointer:
    sequence: int
    client_publish_id: str
    edition_id: str
    edition_date: str
    timezone: str
    expected_revision: int
    revision: int
    edition_digest: str
    recorded_at_ms: int


@dataclass(frozen=True, slots=True)
class NewsEditionSummary:
    edition_id: str
    edition_date: str
    timezone: str
    current_revision: int
    current_digest: str
    created_at_ms: int
    updated_at_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "editionId": self.edition_id,
            "editionDate": self.edition_date,
            "timezone": self.timezone,
            "currentRevision": self.current_revision,
            "currentDigest": self.current_digest,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
        }


class HostJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            pass
        except OSError as error:
            raise JournalCorruption("Host Journal cannot be safely created") from error
        else:
            os.close(descriptor)
        # Harden before SQLite owns any WAL/SHM locks in this process. Closing a
        # second fd for a locked SQLite inode can release process-scoped fcntl locks.
        self._harden_database_files()
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
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
        for path, required in (
            (self.path, True),
            (Path(str(self.path) + "-wal"), False),
            (Path(str(self.path) + "-shm"), False),
        ):
            flags = os.O_RDONLY | os.O_NONBLOCK
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError:
                if required:
                    raise JournalCorruption(
                        f"Host Journal file disappeared: {path.name}"
                    )
                # SQLite may retire WAL/SHM sidecars when another connection closes.
                continue
            except OSError as error:
                raise JournalCorruption(
                    f"Host Journal file cannot be safely opened: {path.name}"
                ) from error
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise JournalCorruption(
                        f"Host Journal file is not regular: {path.name}"
                    )
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)

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
        extension_state: tuple[str, StoredObject] | None = None,
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
        if extension_state is not None:
            namespace, state_object = extension_state
            if event.kind.name != "EXTENSION" or namespace != event.kind.namespace:
                raise ValueError("extension state namespace differs from Event kind")
            if state_object.kind != "host-extension-state":
                raise ValueError("extension state has the wrong object kind")
            if state_object not in referenced_objects:
                raise ValueError("extension state object must be an explicit Event reference")
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
                if extension_state is not None:
                    namespace, state_object = extension_state
                    pointer = self.task_extension_state(event.stream_id, namespace)
                    if (
                        pointer is None
                        or pointer.event_id != event.event_id
                        or pointer.state_digest != state_object.digest
                        or pointer.revision != projection.revision
                        or pointer.legacy
                    ):
                        raise EventConflict(
                            "event identity differs from retained extension state"
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
            if extension_state is not None:
                namespace, state_object = extension_state
                self.connection.execute(
                    "INSERT INTO task_extension_state("
                    "task_id, namespace, state_digest, event_id, revision, legacy"
                    ") VALUES (?, ?, ?, ?, ?, 0) "
                    "ON CONFLICT(task_id, namespace) DO UPDATE SET "
                    "state_digest = excluded.state_digest, event_id = excluded.event_id, "
                    "revision = excluded.revision, legacy = 0",
                    (
                        event.stream_id,
                        namespace,
                        state_object.digest,
                        event.event_id,
                        projection.revision,
                    ),
                )
            if expected_lease is not None:
                self._consume_exact_lease(expected_lease)
        return EventAdmission.CREATED

    def append_board_message(
        self,
        *,
        client_message_id: str,
        author_label: str,
        message_kind: str,
        topic: str | None,
        message_object: StoredObject,
        reply_to_client_message_id: str | None,
        recorded_at_ms: int,
    ) -> EventAdmission:
        if not client_message_id or client_message_id != client_message_id.strip():
            raise ValueError("board client message identity must be non-empty and trimmed")
        if not author_label or author_label != author_label.strip():
            raise ValueError("board author label must be non-empty and trimmed")
        if message_kind not in {"note", "question", "proposal", "warning", "reply"}:
            raise ValueError("board message kind is invalid")
        if topic is not None and (not topic or topic != topic.strip()):
            raise ValueError("board topic must be null or non-empty and trimmed")
        if reply_to_client_message_id == client_message_id:
            raise ValueError("board message cannot reply to itself")
        if message_object.kind != "host-board-message":
            raise ValueError("board message has the wrong object kind")
        if recorded_at_ms < 0:
            raise ValueError("board message time is invalid")

        with self._transaction():
            existing = self.connection.execute(
                "SELECT sequence, author_label, message_kind, topic, message_digest, "
                "reply_to_client_message_id, recorded_at_ms FROM board_messages "
                "WHERE client_message_id = ?",
                (client_message_id,),
            ).fetchone()
            if existing is not None:
                actual = (
                    str(existing["author_label"]),
                    str(existing["message_kind"]),
                    existing["topic"],
                    str(existing["message_digest"]),
                    existing["reply_to_client_message_id"],
                )
                expected = (
                    author_label,
                    message_kind,
                    topic,
                    message_object.digest,
                    reply_to_client_message_id,
                )
                if actual != expected:
                    raise EventConflict(
                        "board client message identity is already bound to different content"
                    )
                return EventAdmission.EXISTING
            if reply_to_client_message_id is not None:
                parent = self.connection.execute(
                    "SELECT 1 FROM board_messages WHERE client_message_id = ?",
                    (reply_to_client_message_id,),
                ).fetchone()
                if parent is None:
                    raise EventConflict("board reply target does not exist")
            self._admit_object(
                message_object,
                recorded_at_ms,
                validation_timing="on_access",
            )
            self.connection.execute(
                "INSERT INTO board_messages("
                "client_message_id, author_label, message_kind, topic, message_digest, "
                "reply_to_client_message_id, recorded_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    client_message_id,
                    author_label,
                    message_kind,
                    topic,
                    message_object.digest,
                    reply_to_client_message_id,
                    recorded_at_ms,
                ),
            )
        return EventAdmission.CREATED

    def board_message_count(self) -> int:
        # Board is append-only and Doctor proves sequence continuity. The current
        # high-water mark is therefore the count without scanning message history.
        return self.board_last_sequence()

    def board_last_sequence(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM board_messages"
        ).fetchone()
        return int(row["sequence"])

    def board_messages(
        self, *, after_sequence: int | None, limit: int
    ) -> tuple[BoardMessagePointer, ...]:
        if after_sequence is not None and (
            type(after_sequence) is not int or after_sequence < 0
        ):
            raise ValueError("board after sequence must be null or non-negative")
        if type(limit) is not int or limit < 1:
            raise ValueError("board limit must be positive")
        if after_sequence is None:
            rows = self.connection.execute(
                "SELECT sequence, client_message_id, author_label, message_kind, topic, "
                "message_digest, reply_to_client_message_id, recorded_at_ms "
                "FROM board_messages ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = self.connection.execute(
                "SELECT sequence, client_message_id, author_label, message_kind, topic, "
                "message_digest, reply_to_client_message_id, recorded_at_ms "
                "FROM board_messages WHERE sequence > ? ORDER BY sequence ASC LIMIT ?",
                (after_sequence, limit),
            ).fetchall()
        return tuple(
            BoardMessagePointer(
                sequence=int(row["sequence"]),
                client_message_id=str(row["client_message_id"]),
                author_label=str(row["author_label"]),
                message_kind=str(row["message_kind"]),
                topic=None if row["topic"] is None else str(row["topic"]),
                message_digest=str(row["message_digest"]),
                reply_to_client_message_id=(
                    None
                    if row["reply_to_client_message_id"] is None
                    else str(row["reply_to_client_message_id"])
                ),
                recorded_at_ms=int(row["recorded_at_ms"]),
            )
            for row in rows
        )

    def validate_board_invariants(self) -> None:
        sequence = self.connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(MIN(sequence), 0) AS first_sequence, "
            "COALESCE(MAX(sequence), 0) AS last_sequence FROM board_messages"
        ).fetchone()
        count = int(sequence["count"])
        first_sequence = int(sequence["first_sequence"])
        last_sequence = int(sequence["last_sequence"])
        if (
            (count == 0 and (first_sequence != 0 or last_sequence != 0))
            or (count > 0 and (first_sequence != 1 or last_sequence != count))
        ):
            raise JournalCorruption("Host board sequence history is not contiguous")
        dangling_reply = self.connection.execute(
            "SELECT child.client_message_id FROM board_messages child "
            "LEFT JOIN board_messages parent "
            "ON parent.client_message_id = child.reply_to_client_message_id "
            "WHERE child.reply_to_client_message_id IS NOT NULL "
            "AND parent.client_message_id IS NULL LIMIT 1"
        ).fetchone()
        if dangling_reply is not None:
            raise JournalCorruption(
                "Host board reply target is missing: "
                f"{dangling_reply['client_message_id']}"
            )
        self_reply = self.connection.execute(
            "SELECT client_message_id FROM board_messages "
            "WHERE reply_to_client_message_id = client_message_id LIMIT 1"
        ).fetchone()
        if self_reply is not None:
            raise JournalCorruption(
                "Host board message replies to itself: "
                f"{self_reply['client_message_id']}"
            )

    def validate_news_invariants(self) -> None:
        sequence = self.connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(MIN(sequence), 0) AS first_sequence, "
            "COALESCE(MAX(sequence), 0) AS last_sequence FROM news_publications"
        ).fetchone()
        count = int(sequence["count"])
        first_sequence = int(sequence["first_sequence"])
        last_sequence = int(sequence["last_sequence"])
        if (
            (count == 0 and (first_sequence != 0 or last_sequence != 0))
            or (count > 0 and (first_sequence != 1 or last_sequence != count))
        ):
            raise JournalCorruption("Host news publication sequence history is not contiguous")

        revision_gap = self.connection.execute(
            "SELECT edition_id FROM news_publications GROUP BY edition_id "
            "HAVING MIN(revision) != 1 OR COUNT(*) != MAX(revision) LIMIT 1"
        ).fetchone()
        if revision_gap is not None:
            raise JournalCorruption(
                "Host news edition revision history is not contiguous: "
                f"{revision_gap['edition_id']}"
            )

        publication_mismatch = self.connection.execute(
            "SELECT p.client_publish_id FROM news_publications p "
            "JOIN news_editions e ON e.edition_id = p.edition_id "
            "LEFT JOIN object_refs o ON o.digest = p.edition_digest "
            "WHERE p.edition_date != e.edition_date OR p.timezone != e.timezone "
            "OR p.revision != p.expected_revision + 1 "
            "OR p.revision > e.current_revision "
            "OR o.digest IS NULL OR o.kind != 'host-news-edition' "
            "OR o.validation_timing NOT IN ('startup', 'on_access') LIMIT 1"
        ).fetchone()
        if publication_mismatch is not None:
            raise JournalCorruption(
                "Host news publication differs from edition/object history: "
                f"{publication_mismatch['client_publish_id']}"
            )

        head_mismatch = self.connection.execute(
            "SELECT e.edition_id FROM news_editions e LEFT JOIN news_publications p "
            "ON p.edition_id = e.edition_id AND p.revision = e.current_revision "
            "WHERE p.sequence IS NULL OR p.edition_digest != e.current_digest "
            "OR p.edition_date != e.edition_date OR p.timezone != e.timezone LIMIT 1"
        ).fetchone()
        if head_mismatch is not None:
            raise JournalCorruption(
                "Host news edition head differs from publication history: "
                f"{head_mismatch['edition_id']}"
            )

    def append_news_publication(
        self,
        *,
        client_publish_id: str,
        edition_id: str,
        edition_date: str,
        timezone: str,
        expected_revision: int,
        edition_object: StoredObject,
        recorded_at_ms: int,
    ) -> EventAdmission:
        if not client_publish_id or client_publish_id != client_publish_id.strip():
            raise ValueError("news client publish identity must be non-empty and trimmed")
        if not edition_id.startswith("news:") or edition_id != edition_id.strip():
            raise ValueError("news edition identity is invalid")
        if not edition_date or edition_date != edition_date.strip():
            raise ValueError("news edition date is invalid")
        if not timezone or timezone != timezone.strip():
            raise ValueError("news timezone is invalid")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("news expected revision must be non-negative")
        if edition_object.kind != "host-news-edition":
            raise ValueError("news edition has the wrong object kind")
        if type(recorded_at_ms) is not int or recorded_at_ms < 0:
            raise ValueError("news recorded time is invalid")

        with self._transaction():
            existing = self.connection.execute(
                "SELECT sequence, edition_id, edition_date, timezone, expected_revision, "
                "revision, edition_digest, recorded_at_ms FROM news_publications "
                "WHERE client_publish_id = ?",
                (client_publish_id,),
            ).fetchone()
            if existing is not None:
                actual = (
                    str(existing["edition_id"]),
                    str(existing["edition_date"]),
                    str(existing["timezone"]),
                    int(existing["expected_revision"]),
                    str(existing["edition_digest"]),
                )
                expected = (
                    edition_id,
                    edition_date,
                    timezone,
                    expected_revision,
                    edition_object.digest,
                )
                if actual != expected:
                    raise EventConflict(
                        "news client publish identity is already bound to different content"
                    )
                return EventAdmission.EXISTING

            head = self.connection.execute(
                "SELECT edition_date, timezone, current_revision FROM news_editions "
                "WHERE edition_id = ?",
                (edition_id,),
            ).fetchone()
            current_revision = 0 if head is None else int(head["current_revision"])
            if current_revision != expected_revision:
                raise RevisionConflict(
                    f"news edition revision is {current_revision}, expected {expected_revision}"
                )
            if head is not None and (
                str(head["edition_date"]) != edition_date
                or str(head["timezone"]) != timezone
            ):
                raise EventConflict("news edition identity metadata cannot change across revisions")

            revision = expected_revision + 1
            self._admit_object(
                edition_object,
                recorded_at_ms,
                validation_timing="on_access",
            )
            if head is None:
                self.connection.execute(
                    "INSERT INTO news_editions("
                    "edition_id, edition_date, timezone, current_revision, current_digest, "
                    "created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        edition_id, edition_date, timezone, revision, edition_object.digest,
                        recorded_at_ms, recorded_at_ms,
                    ),
                )
            else:
                changed = self.connection.execute(
                    "UPDATE news_editions SET current_revision = ?, current_digest = ?, "
                    "updated_at_ms = ? WHERE edition_id = ? AND current_revision = ?",
                    (revision, edition_object.digest, recorded_at_ms, edition_id, expected_revision),
                ).rowcount
                if changed != 1:
                    raise RevisionConflict("news edition revision changed during transaction")
            self.connection.execute(
                "INSERT INTO news_publications("
                "client_publish_id, edition_id, edition_date, timezone, expected_revision, "
                "revision, edition_digest, recorded_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    client_publish_id, edition_id, edition_date, timezone, expected_revision,
                    revision, edition_object.digest, recorded_at_ms,
                ),
            )
        return EventAdmission.CREATED

    def news_publication_by_client_id(
        self, client_publish_id: str
    ) -> NewsPublicationPointer | None:
        row = self.connection.execute(
            "SELECT sequence, client_publish_id, edition_id, edition_date, timezone, "
            "expected_revision, revision, edition_digest, recorded_at_ms "
            "FROM news_publications WHERE client_publish_id = ?",
            (client_publish_id,),
        ).fetchone()
        return None if row is None else self._news_publication_pointer(row)

    def news_edition_pointer(
        self, *, edition_id: str | None, revision: int | None
    ) -> NewsPublicationPointer | None:
        target_id = edition_id
        if target_id is None:
            head = self.connection.execute(
                "SELECT edition_id FROM news_editions "
                "ORDER BY edition_date DESC, edition_id ASC LIMIT 1"
            ).fetchone()
            if head is None:
                return None
            target_id = str(head["edition_id"])
        if revision is None:
            row = self.connection.execute(
                "SELECT p.sequence, p.client_publish_id, p.edition_id, p.edition_date, p.timezone, "
                "p.expected_revision, p.revision, p.edition_digest, p.recorded_at_ms "
                "FROM news_publications p JOIN news_editions e ON e.edition_id = p.edition_id "
                "AND e.current_revision = p.revision WHERE p.edition_id = ?",
                (target_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT sequence, client_publish_id, edition_id, edition_date, timezone, "
                "expected_revision, revision, edition_digest, recorded_at_ms "
                "FROM news_publications WHERE edition_id = ? AND revision = ?",
                (target_id, revision),
            ).fetchone()
        return None if row is None else self._news_publication_pointer(row)

    def news_all_publications(self) -> tuple[NewsPublicationPointer, ...]:
        rows = self.connection.execute(
            "SELECT sequence, client_publish_id, edition_id, edition_date, timezone, "
            "expected_revision, revision, edition_digest, recorded_at_ms "
            "FROM news_publications ORDER BY sequence"
        ).fetchall()
        return tuple(self._news_publication_pointer(row) for row in rows)

    def news_publication_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM news_publications"
        ).fetchone()
        return int(row["count"])

    def news_edition_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM news_editions"
        ).fetchone()
        return int(row["count"])

    def news_editions(
        self,
        *,
        limit: int,
        after: tuple[str, str] | None,
        from_date: str | None,
        to_date: str | None,
    ) -> tuple[NewsEditionSummary, ...]:
        clauses: list[str] = []
        params: list[object] = []
        if from_date is not None:
            clauses.append("edition_date >= ?")
            params.append(from_date)
        if to_date is not None:
            clauses.append("edition_date <= ?")
            params.append(to_date)
        if after is not None:
            after_date, after_id = after
            clauses.append("(edition_date < ? OR (edition_date = ? AND edition_id > ?))")
            params.extend((after_date, after_date, after_id))
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = self.connection.execute(
            "SELECT edition_id, edition_date, timezone, current_revision, current_digest, "
            "created_at_ms, updated_at_ms FROM news_editions"
            + where
            + " ORDER BY edition_date DESC, edition_id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(
            NewsEditionSummary(
                edition_id=str(row["edition_id"]),
                edition_date=str(row["edition_date"]),
                timezone=str(row["timezone"]),
                current_revision=int(row["current_revision"]),
                current_digest=str(row["current_digest"]),
                created_at_ms=int(row["created_at_ms"]),
                updated_at_ms=int(row["updated_at_ms"]),
            )
            for row in rows
        )

    @staticmethod
    def _news_publication_pointer(row: object) -> NewsPublicationPointer:
        return NewsPublicationPointer(
            sequence=int(row["sequence"]),  # type: ignore[index]
            client_publish_id=str(row["client_publish_id"]),  # type: ignore[index]
            edition_id=str(row["edition_id"]),  # type: ignore[index]
            edition_date=str(row["edition_date"]),  # type: ignore[index]
            timezone=str(row["timezone"]),  # type: ignore[index]
            expected_revision=int(row["expected_revision"]),  # type: ignore[index]
            revision=int(row["revision"]),  # type: ignore[index]
            edition_digest=str(row["edition_digest"]),  # type: ignore[index]
            recorded_at_ms=int(row["recorded_at_ms"]),  # type: ignore[index]
        )

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

    def object_ref_count(self, *, validation_timing: str | None = None) -> int:
        if validation_timing is None:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM object_refs"
            ).fetchone()
        else:
            if validation_timing not in {"startup", "on_access"}:
                raise ValueError("object reference validation timing is invalid")
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM object_refs WHERE validation_timing = ?",
                (validation_timing,),
            ).fetchone()
        return int(row["count"])

    def object_refs(self) -> tuple[StoredObject, ...]:
        rows = self.connection.execute(
            "SELECT digest, byte_length, kind FROM object_refs ORDER BY digest"
        ).fetchall()
        return tuple(
            StoredObject(row["digest"], int(row["byte_length"]), row["kind"])
            for row in rows
        )

    def object_ref(
        self, digest: str
    ) -> tuple[StoredObject, str] | None:
        row = self.connection.execute(
            "SELECT digest, byte_length, kind, validation_timing "
            "FROM object_refs WHERE digest = ?",
            (digest,),
        ).fetchone()
        if row is None:
            return None
        timing = str(row["validation_timing"])
        if timing not in {"startup", "on_access"}:
            raise JournalCorruption("object reference validation timing is invalid")
        return (
            StoredObject(row["digest"], int(row["byte_length"]), row["kind"]),
            timing,
        )

    def legacy_object_refs(self) -> tuple[StoredObject, ...]:
        rows = self.connection.execute(
            "SELECT r.digest, r.byte_length, r.kind "
            "FROM legacy_object_refs l JOIN object_refs r ON r.digest = l.digest "
            "ORDER BY r.digest"
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

    def object_reference_validation_rows(
        self, *, include_on_access: bool
    ) -> sqlite3.Cursor:
        if include_on_access:
            return self.connection.execute(
                "SELECT r.digest, r.byte_length, r.kind, r.validation_timing, "
                "v.device, v.inode, v.byte_length AS validated_byte_length, "
                "v.modified_at_ns, v.changed_at_ns, v.mode "
                "FROM object_refs r LEFT JOIN object_validation v ON v.digest = r.digest "
                "ORDER BY r.digest"
            )
        return self.connection.execute(
            "SELECT r.digest, r.byte_length, r.kind, r.validation_timing, "
            "v.device, v.inode, v.byte_length AS validated_byte_length, "
            "v.modified_at_ns, v.changed_at_ns, v.mode "
            "FROM object_refs r LEFT JOIN object_validation v ON v.digest = r.digest "
            "WHERE r.validation_timing = 'startup' ORDER BY r.digest"
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

    def task_projection_validation_rows(
        self,
    ) -> tuple[tuple[TaskProjection, TaskEventPointer], ...]:
        # Projection and its exact event head must come from one SQLite statement.
        # Separate SELECTs can legitimately observe different committed revisions when
        # another Host process advances an independent Task during startup validation.
        rows = self.connection.execute(
            "SELECT p.task_id AS projection_task_id, p.goal_id, p.state, "
            "p.active_node_id, p.ready_frontier_json, p.revision AS projection_revision, "
            "p.updated_at_ms, e.event_id, e.stream_id, e.event_kind, "
            "e.payload_digest, e.stream_revision "
            "FROM task_projection p LEFT JOIN events e "
            "ON e.stream_id = p.task_id AND e.stream_kind = ? "
            "AND e.stream_revision = p.revision ORDER BY p.task_id",
            (StreamKind.TASK.value,),
        ).fetchall()
        result: list[tuple[TaskProjection, TaskEventPointer]] = []
        for row in rows:
            if row["event_id"] is None:
                raise JournalCorruption(
                    f"Task projection has no matching event head: {row['projection_task_id']}"
                )
            try:
                frontier = json.loads(row["ready_frontier_json"])
                if not isinstance(frontier, list) or any(
                    not isinstance(item, str) for item in frontier
                ):
                    raise ValueError("Task ready frontier is not a string list")
                projection = TaskProjection(
                    task_id=row["projection_task_id"],
                    goal_id=row["goal_id"],
                    state=TaskState(row["state"]),
                    active_node_id=row["active_node_id"],
                    ready_frontier=tuple(frontier),
                    revision=int(row["projection_revision"]),
                    updated_at_ms=int(row["updated_at_ms"]),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise JournalCorruption(
                    f"Task projection is invalid: {row['projection_task_id']}"
                ) from error
            result.append((projection, self._task_event_pointer(row)))
        return tuple(result)

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

    def task_event_at_revision(
        self, task_id: str, revision: int
    ) -> TaskEventPointer | None:
        if type(revision) is not int or revision < 1:
            raise ValueError("Task Event revision must be a positive integer")
        row = self.connection.execute(
            "SELECT event_id, stream_id, event_kind, payload_digest, stream_revision "
            "FROM events WHERE stream_id = ? AND stream_revision = ?",
            (task_id, revision),
        ).fetchone()
        return None if row is None else self._task_event_pointer(row)

    def latest_task_event_of_kind(
        self, task_id: str, kind: EventKind
    ) -> TaskEventPointer | None:
        if not isinstance(kind, EventKind):
            raise ValueError("Task Event kind must be an EventKind")
        row = self.connection.execute(
            "SELECT event_id, stream_id, event_kind, payload_digest, stream_revision "
            "FROM events WHERE stream_id = ? AND event_kind = ? "
            "ORDER BY stream_revision DESC LIMIT 1",
            (task_id, kind.value),
        ).fetchone()
        return None if row is None else self._task_event_pointer(row)

    @staticmethod
    def _task_event_pointer(row: sqlite3.Row) -> TaskEventPointer:
        try:
            return TaskEventPointer(
                event_id=row["event_id"],
                task_id=row["stream_id"],
                event_kind=EventKind(row["event_kind"]),
                payload_digest=row["payload_digest"],
                revision=int(row["stream_revision"]),
            )
        except (TypeError, ValueError) as error:
            raise JournalCorruption("Task Event pointer is invalid") from error

    def task_extension_state(
        self, task_id: str, namespace: str
    ) -> TaskExtensionStatePointer | None:
        if not namespace or "." in namespace or namespace != namespace.strip():
            raise ValueError("extension namespace must be one non-empty Event namespace")
        row = self.connection.execute(
            "SELECT s.task_id, s.namespace, s.state_digest, s.event_id, s.revision, "
            "s.legacy, e.event_kind FROM task_extension_state s "
            "JOIN events e ON e.event_id = s.event_id "
            "WHERE s.task_id = ? AND s.namespace = ?",
            (task_id, namespace),
        ).fetchone()
        if row is None:
            return None
        try:
            legacy = int(row["legacy"])
            if legacy != 0:
                raise JournalCorruption(
                    "legacy extension state requires a pre-0.5 Host client for owner recovery/export"
                )
            return TaskExtensionStatePointer(
                task_id=str(row["task_id"]),
                namespace=str(row["namespace"]),
                state_digest=str(row["state_digest"]),
                event_id=str(row["event_id"]),
                event_kind=EventKind(str(row["event_kind"])),
                revision=int(row["revision"]),
                legacy=False,
            )
        except JournalCorruption:
            raise
        except (TypeError, ValueError) as error:
            raise JournalCorruption("Task extension state pointer is invalid") from error

    def task_extension_namespaces(
        self, task_id: str, *, at_revision: int
    ) -> tuple[str, ...]:
        if type(at_revision) is not int or at_revision < 1:
            raise ValueError("Task extension namespace revision must be positive")
        projection = self.connection.execute(
            "SELECT revision FROM task_projection WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if projection is None:
            raise KeyError(f"unknown Task: {task_id}")
        current_revision = int(projection["revision"])
        if at_revision > current_revision:
            raise ValueError(
                f"Task extension namespace revision {at_revision} exceeds current Task revision {current_revision}"
            )
        rows = self.connection.execute(
            "SELECT s.namespace, e.event_kind FROM task_extension_state s "
            "JOIN events e ON e.event_id = s.event_id "
            "WHERE s.task_id = ? ORDER BY s.namespace",
            (task_id,),
        ).fetchall()
        durable: set[str] = set()
        for row in rows:
            namespace = row["namespace"]
            try:
                kind = EventKind(str(row["event_kind"]))
            except ValueError as error:
                raise JournalCorruption(
                    "Task extension state Event kind is invalid"
                ) from error
            if (
                not isinstance(namespace, str)
                or not namespace
                or "." in namespace
                or namespace != namespace.strip()
                or kind.name != "EXTENSION"
                or kind.namespace != namespace
            ):
                raise JournalCorruption("Task extension namespace is invalid")
            durable.add(namespace)

        history = self.connection.execute(
            "SELECT event_kind FROM events "
            "WHERE stream_id = ? AND stream_revision <= ? "
            "ORDER BY stream_revision",
            (task_id, at_revision),
        ).fetchall()
        visible: set[str] = set()
        for row in history:
            try:
                kind = EventKind(str(row["event_kind"]))
            except ValueError as error:
                raise JournalCorruption("Task Event kind is invalid") from error
            if kind.name == "EXTENSION" and kind.namespace in durable:
                visible.add(kind.namespace)
        return tuple(sorted(visible))

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

        extension_mismatch = self.connection.execute(
            "SELECT s.task_id, s.namespace FROM task_extension_state s "
            "LEFT JOIN events e ON e.event_id = s.event_id "
            "LEFT JOIN object_refs o ON o.digest = s.state_digest "
            "WHERE e.event_id IS NULL OR o.digest IS NULL "
            "OR e.stream_id != s.task_id OR e.stream_revision != s.revision "
            "OR s.revision > (SELECT revision FROM task_projection p WHERE p.task_id = s.task_id) "
            "LIMIT 1"
        ).fetchone()
        if extension_mismatch is not None:
            raise JournalCorruption(
                "Task extension state pointer differs from event/object history: "
                f"{extension_mismatch['task_id']}:{extension_mismatch['namespace']}"
            )
        for row in self.connection.execute(
            "SELECT s.task_id, s.namespace, s.event_id, e.event_kind, s.legacy "
            "FROM task_extension_state s JOIN events e ON e.event_id = s.event_id"
        ):
            kind = EventKind(str(row["event_kind"]))
            if kind.name != "EXTENSION" or kind.namespace != row["namespace"]:
                raise JournalCorruption(
                    "Task extension state namespace differs from Event kind: "
                    f"{row['task_id']}:{row['namespace']}"
                )
            if int(row["legacy"]) != 0:
                raise JournalCorruption(
                    "legacy extension state requires a pre-0.5 Host client for owner recovery/export"
                )

        self.validate_news_invariants()

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

    def _admit_object(
        self,
        value: StoredObject,
        first_seen_at_ms: int,
        *,
        validation_timing: str = "startup",
    ) -> None:
        if validation_timing not in {"startup", "on_access"}:
            raise ValueError("object reference validation timing is invalid")
        existing = self.connection.execute(
            "SELECT kind, byte_length, validation_timing FROM object_refs WHERE digest = ?",
            (value.digest,),
        ).fetchone()
        if existing is not None:
            if (existing["kind"], int(existing["byte_length"])) != (
                value.kind,
                value.byte_length,
            ):
                raise JournalCorruption("object digest metadata differs")
            if existing["validation_timing"] == "on_access" and validation_timing == "startup":
                self.connection.execute(
                    "UPDATE object_refs SET validation_timing = 'startup' WHERE digest = ?",
                    (value.digest,),
                )
            return
        self.connection.execute(
            "INSERT INTO object_refs("
            "digest, kind, byte_length, first_seen_at_ms, validation_timing"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                value.digest,
                value.kind,
                value.byte_length,
                first_seen_at_ms,
                validation_timing,
            ),
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
