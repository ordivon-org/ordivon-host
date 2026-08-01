from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from anc_canonical import JsonValue, validate_json_value

from .domain import EventKind, TaskProjection, TaskState
from .kernel import ErrorFactory, HostKernel
from .objects import StoredObject
from .storage import HostStorage, TaskEventSnapshot


class HostExtensionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HostExtensionSnapshot:
    event_kind: EventKind
    data: dict[str, JsonValue]
    projection: TaskProjection
    payload_digest: str


class HostExtensionPort:
    """Small public port for append-only component extension events.

    Components retain their own schemas. The Host only preserves the current Task
    projection, performs revision/state/frontier fencing, and retains referenced
    CAS objects named by top-level ``*ObjectDigest`` fields.
    """

    def __init__(self, storage: HostStorage, kernel: HostKernel) -> None:
        if kernel.storage is not storage:
            raise ValueError("Host Extension Port storage must match its Host Kernel")
        self.storage = storage
        self.kernel = kernel

    def load(self, task_id: str) -> HostExtensionSnapshot:
        return self._extension_snapshot(self.storage.read_task_event(task_id))

    def put_object(self, value: JsonValue, *, kind: str) -> StoredObject:
        return self.storage.put_object(value, kind=kind)

    def inspect_object(self, digest: str) -> StoredObject:
        return self.storage.objects.inspect(digest)

    def get_object(self, digest: str, *, expected_kind: str | None = None) -> JsonValue:
        return self.storage.objects.get(digest, expected_kind=expected_kind)

    def append_preserving(
        self,
        *,
        task_id: str,
        expected_revision: int,
        event_id: str,
        kind: EventKind,
        updates: dict[str, JsonValue],
        remove_fields: tuple[str, ...] = (),
        referenced_objects: tuple[StoredObject, ...] = (),
        expected_state: TaskState | None = None,
        expected_frontier: tuple[str, ...] | None = None,
        label: str = "Host extension",
        error_factory: ErrorFactory | None = None,
    ) -> HostExtensionSnapshot:
        validate_json_value(updates)
        if len(remove_fields) != len(set(remove_fields)):
            raise ValueError("Host extension remove fields must be unique")
        if set(remove_fields) & set(updates):
            raise ValueError("Host extension cannot update and remove the same field")

        current = self.load(task_id)
        if expected_state is None:
            expected_state = current.projection.state
        if expected_frontier is None:
            expected_frontier = current.projection.ready_frontier
        data = dict(current.data)
        for field in remove_fields:
            data.pop(field, None)
        data.update(updates)
        validate_json_value(data)
        retained = self._dedupe_objects(
            (*self._payload_objects(data), *referenced_objects)
        )
        with self.kernel.locked_task(
            task_id,
            expected_revision=expected_revision,
            expected_state=expected_state,
            expected_frontier=expected_frontier,
            label=label,
            error_factory=error_factory,
        ) as locked:
            receipt = locked.commit(
                event_id=event_id,
                kind=kind,
                payload=data,
                state=locked.projection.state,
                frontier=locked.projection.ready_frontier,
                referenced_objects=retained,
            )
        return self.load(receipt.projection.task_id)

    def _payload_objects(self, data: dict[str, JsonValue]) -> tuple[StoredObject, ...]:
        values: list[StoredObject] = []
        for field, value in data.items():
            if field.endswith("ObjectDigest") and isinstance(value, str):
                values.append(self.storage.objects.inspect(value))
            elif field.endswith("ObjectDigests"):
                if not isinstance(value, list) or any(
                    not isinstance(item, str) for item in value
                ):
                    raise HostExtensionError(
                        f"Host extension object reference list is invalid: {field}"
                    )
                values.extend(self.storage.objects.inspect(item) for item in value)
        return self._dedupe_objects(values)

    @staticmethod
    def _dedupe_objects(values: Iterable[StoredObject]) -> tuple[StoredObject, ...]:
        retained: dict[str, StoredObject] = {}
        for value in values:
            previous = retained.get(value.digest)
            if previous is not None and previous != value:
                raise HostExtensionError(
                    f"Host extension object metadata conflicts: {value.digest}"
                )
            retained[value.digest] = value
        return tuple(retained[key] for key in sorted(retained))

    @staticmethod
    def _extension_snapshot(snapshot: TaskEventSnapshot) -> HostExtensionSnapshot:
        if not isinstance(snapshot.data, dict):
            raise HostExtensionError(
                f"Host Task event data is not an object: {snapshot.projection.task_id}"
            )
        validate_json_value(snapshot.data)
        return HostExtensionSnapshot(
            snapshot.event_kind,
            dict(snapshot.data),
            snapshot.projection,
            snapshot.payload_digest,
        )
