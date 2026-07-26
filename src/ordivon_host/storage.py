from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anc_canonical import JsonValue

from .domain import EventAdmission, EventKind, HostEvent, StreamKind, TaskProjection
from .journal import HostJournal, JournalCorruption
from .objects import ContentAddressedStore, ObjectCorrupt, StoredObject

_EVENT_PAYLOAD_KIND = "ordivon.host-task-event"


@dataclass(frozen=True, slots=True)
class TaskEventSnapshot:
    event_kind: EventKind
    data: JsonValue
    projection: TaskProjection
    payload_digest: str


class HostStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = ContentAddressedStore(self.root / "objects")
        self.journal = HostJournal(self.root / "host.sqlite3")
        try:
            self.validate_references()
        except BaseException:
            self.journal.close()
            raise

    def close(self) -> None:
        self.journal.close()

    def __enter__(self) -> HostStorage:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def put_object(self, value: JsonValue, *, kind: str) -> StoredObject:
        return self.objects.put(value, kind=kind)

    def validate_references(self) -> None:
        for expected in self.journal.object_refs():
            actual = self.objects.inspect(expected.digest)
            if actual != expected:
                raise JournalCorruption(
                    f"CAS metadata differs from Host Journal: {expected.digest}"
                )
        for task_id in self.journal.task_ids():
            materialized = self.journal.get_task(task_id)
            rebuilt = self.rebuild_task(task_id)
            if materialized != rebuilt:
                raise JournalCorruption(
                    f"Task projection differs from event head: {task_id}"
                )

    def read_task_event(self, task_id: str) -> TaskEventSnapshot:
        head = self.journal.get_task_head(task_id)
        if head is None:
            raise JournalCorruption(f"Task has no event head: {task_id}")
        value = self.objects.get(
            head.payload_digest,
            expected_kind="host-event-payload",
        )
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "kind",
            "eventKind",
            "data",
            "projection",
        }:
            raise ObjectCorrupt("Host event payload fields differ")
        if value["schemaVersion"] != 1 or value["kind"] != _EVENT_PAYLOAD_KIND:
            raise ObjectCorrupt("Host event payload version or kind is invalid")
        if value["eventKind"] != head.event_kind.value:
            raise JournalCorruption(f"Task event kind differs from payload: {task_id}")
        raw_projection = value["projection"]
        if not isinstance(raw_projection, dict):
            raise ObjectCorrupt("Host event projection must be an object")
        try:
            projection = TaskProjection.from_dict(raw_projection)
        except (TypeError, ValueError) as error:
            raise ObjectCorrupt("Host event projection is invalid") from error
        if projection.task_id != task_id or projection.revision != head.revision:
            raise JournalCorruption(
                f"Task event head identity or revision differs: {task_id}"
            )
        return TaskEventSnapshot(
            event_kind=head.event_kind,
            data=value["data"],
            projection=projection,
            payload_digest=head.payload_digest,
        )

    def rebuild_task(self, task_id: str) -> TaskProjection:
        return self.read_task_event(task_id).projection

    def record_task_event(
        self,
        *,
        event_id: str,
        kind: EventKind,
        payload: JsonValue,
        projection: TaskProjection,
        expected_revision: int,
        caused_by_event_id: str | None = None,
        referenced_objects: tuple[StoredObject, ...] = (),
    ) -> EventAdmission:
        event_payload: JsonValue = {
            "schemaVersion": 1,
            "kind": _EVENT_PAYLOAD_KIND,
            "eventKind": kind.value,
            "data": payload,
            "projection": projection.to_dict(),
        }
        stored = self.objects.put(event_payload, kind="host-event-payload")
        event = HostEvent(
            event_id=event_id,
            stream_id=projection.task_id,
            stream_kind=StreamKind.TASK,
            kind=kind,
            payload_digest=stored.digest,
            recorded_at_ms=projection.updated_at_ms,
            caused_by_event_id=caused_by_event_id,
        )
        return self.journal.append_task_event(
            event,
            expected_revision=expected_revision,
            projection=projection,
            payload_object=stored,
            referenced_objects=referenced_objects,
        )
