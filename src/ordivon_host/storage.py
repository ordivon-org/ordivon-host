from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from anc_canonical import JsonValue, canonical_digest

from .domain import (
    EventAdmission,
    EventKind,
    HostEvent,
    StreamKind,
    TaskDescriptor,
    TaskProjection,
)
from .journal import HostJournal, JournalCorruption, LeaseRecord
from .objects import (
    ContentAddressedStore,
    ObjectCorrupt,
    ObjectFileIdentity,
    StoredObject,
)

_EVENT_PAYLOAD_KIND = "ordivon.host-task-event"


@dataclass(frozen=True, slots=True)
class TaskEventSnapshot:
    event_kind: EventKind
    data: JsonValue
    projection: TaskProjection
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ReferenceValidation:
    object_refs: int
    cached_objects: int
    hashed_objects: int
    task_heads: int
    full: bool


class HostStorage:
    def __init__(
        self,
        root: str | Path,
        *,
        validation_mode: Literal["cached", "full"] = "cached",
        update_validation_cache: bool = True,
    ) -> None:
        if validation_mode not in {"cached", "full"}:
            raise ValueError("Host validation mode must be cached or full")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("Host state root cannot be a symlink")
        os.chmod(self.root, 0o700)
        self.objects = ContentAddressedStore(self.root / "objects")
        self.journal = HostJournal(self.root / "host.sqlite3")
        try:
            self.validation_summary = self.validate_references(
                full=validation_mode == "full",
                update_cache=update_validation_cache,
            )
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

    def task_descriptor_digest(self, task_id: str) -> str:
        snapshot = self.read_task_event(task_id)
        data = snapshot.data
        if not isinstance(data, dict):
            raise JournalCorruption(f"Task event data is not an object: {task_id}")
        digest = data.get("descriptorDigest")
        if not isinstance(digest, str):
            raise KeyError(f"Task has no durable descriptor: {task_id}")
        return digest

    def task_descriptor_object_digest(self, task_id: str) -> str:
        snapshot = self.read_task_event(task_id)
        data = snapshot.data
        if not isinstance(data, dict):
            raise JournalCorruption(f"Task event data is not an object: {task_id}")
        digest = data.get("descriptorObjectDigest")
        if not isinstance(digest, str):
            raise KeyError(f"Task has no durable descriptor object: {task_id}")
        stored = self.objects.inspect(digest)
        if stored.kind != "task-descriptor":
            raise JournalCorruption(f"Task descriptor object kind differs: {task_id}")
        return digest

    def read_task_descriptor(self, task_id: str) -> TaskDescriptor | None:
        try:
            semantic_digest = self.task_descriptor_digest(task_id)
            object_digest = self.task_descriptor_object_digest(task_id)
        except KeyError:
            return None
        value = self.objects.get(object_digest, expected_kind="task-descriptor")
        if not isinstance(value, dict):
            raise ObjectCorrupt("TaskDescriptor object must be an object")
        try:
            descriptor = TaskDescriptor.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("TaskDescriptor object is invalid") from error
        if canonical_digest(descriptor.to_dict()) != semantic_digest:
            raise JournalCorruption("TaskDescriptor semantic digest differs")
        projection = self.journal.get_task(task_id)
        if (
            projection is None
            or descriptor.task_id != projection.task_id
            or descriptor.goal_id != projection.goal_id
        ):
            raise JournalCorruption("TaskDescriptor identity differs from projection")
        return descriptor

    def validate_references(
        self,
        *,
        full: bool = False,
        update_cache: bool = True,
    ) -> ReferenceValidation:
        cached_objects = 0
        hashed_objects = 0
        total_objects = 0
        pending: list[tuple[str, ObjectFileIdentity]] = []
        for row in self.journal.object_reference_validation_rows():
            total_objects += 1
            expected = StoredObject(
                row["digest"], int(row["byte_length"]), row["kind"]
            )
            current_identity = self.objects.identity(expected.digest)
            cached_identity = self._cached_identity(row)
            if (
                not full
                and cached_identity is not None
                and cached_identity == current_identity
                and current_identity.byte_length == expected.byte_length
            ):
                cached_objects += 1
                continue
            actual, verified_identity = self.objects.inspect_with_identity(
                expected.digest
            )
            if actual != expected:
                raise JournalCorruption(
                    f"CAS metadata differs from Host Journal: {expected.digest}"
                )
            if update_cache:
                pending.append((expected.digest, verified_identity))
            hashed_objects += 1
            if update_cache and len(pending) >= 2_000:
                self.journal.record_object_validations(tuple(pending))
                pending.clear()
        if update_cache:
            self.journal.record_object_validations(tuple(pending))

        task_ids = self.journal.task_ids()
        for task_id in task_ids:
            materialized = self.journal.get_task(task_id)
            rebuilt = self.rebuild_task(task_id)
            if materialized != rebuilt:
                raise JournalCorruption(
                    f"Task projection differs from event head: {task_id}"
                )
        return ReferenceValidation(
            object_refs=total_objects,
            cached_objects=cached_objects,
            hashed_objects=hashed_objects,
            task_heads=len(task_ids),
            full=full,
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
        expected_lease: LeaseRecord | None = None,
        lease_checked_at_ms: int | None = None,
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
            expected_lease=expected_lease,
            lease_checked_at_ms=lease_checked_at_ms,
        )

    @staticmethod
    def _cached_identity(row: object) -> ObjectFileIdentity | None:
        device = row["device"]  # type: ignore[index]
        if device is None:
            return None
        try:
            return ObjectFileIdentity(
                device=int(device),
                inode=int(row["inode"]),  # type: ignore[index]
                byte_length=int(row["validated_byte_length"]),  # type: ignore[index]
                modified_at_ns=int(row["modified_at_ns"]),  # type: ignore[index]
                changed_at_ns=int(row["changed_at_ns"]),  # type: ignore[index]
                mode=int(row["mode"]),  # type: ignore[index]
            )
        except (TypeError, ValueError, KeyError, IndexError) as error:
            raise JournalCorruption("object validation cache row is invalid") from error
