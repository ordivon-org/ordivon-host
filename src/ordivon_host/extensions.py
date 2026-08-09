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


class HostExtensionLegacyStateUnknown(HostExtensionError):
    pass


_EXTENSION_STATE_KIND = "host-extension-state"


@dataclass(frozen=True, slots=True)
class HostExtensionSnapshot:
    event_kind: EventKind
    data: dict[str, JsonValue]
    projection: TaskProjection
    payload_digest: str


class HostExtensionPort:
    """Small public port for append-only component extension events.

    Components retain their own schemas. The Host only preserves the current Task
    projection, performs revision/state/frontier fencing, and retains the CAS
    objects the caller supplies explicitly. Payload field names have no Host
    semantics.
    """

    def __init__(self, storage: HostStorage, kernel: HostKernel) -> None:
        if kernel.storage is not storage:
            raise ValueError("Host Extension Port storage must match its Host Kernel")
        self.storage = storage
        self.kernel = kernel

    def load(self, task_id: str) -> HostExtensionSnapshot:
        return self._extension_snapshot(self.storage.read_task_event(task_id))

    def load_namespace(self, task_id: str, namespace: str) -> HostExtensionSnapshot:
        current = self.storage.read_task_event(task_id)
        if not isinstance(current.data, dict):
            raise HostExtensionError(
                f"Host Task event data is not an object: {current.projection.task_id}"
            )
        retained = self.storage.read_task_extension_state(task_id, namespace)
        if retained is None:
            data: dict[str, JsonValue] = {}
            event_kind = current.event_kind
            state_digest = current.payload_digest
        else:
            pointer, extension_data = retained
            data = dict(extension_data)
            event_kind = pointer.event_kind
            state_digest = pointer.state_digest
        validate_json_value(data)
        return HostExtensionSnapshot(
            event_kind,
            data,
            current.projection,
            state_digest,
        )

    def _namespace_state(
        self, task_id: str, namespace: str
    ) -> tuple[dict[str, JsonValue], bool]:
        retained = self.storage.read_task_extension_state(task_id, namespace)
        if retained is None:
            return {}, False
        pointer, data = retained
        return data, pointer.legacy

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

        if kind.name != "EXTENSION":
            raise ValueError("HostExtensionPort requires a non-core Event kind")
        namespace = kind.namespace
        current = self.load(task_id)
        state_data, legacy = self._namespace_state(task_id, namespace)
        if legacy:
            raise HostExtensionLegacyStateUnknown(
                "legacy extension state cannot be mutated safely; "
                "recover it with recover_legacy_namespace() first"
            )
        if expected_state is None:
            expected_state = current.projection.state
        if expected_frontier is None:
            expected_frontier = current.projection.ready_frontier
        extension_data = dict(state_data)
        for field in remove_fields:
            extension_data.pop(field, None)
        extension_data.update(updates)
        validate_json_value(extension_data)
        state_object = self.storage.put_object(
            extension_data, kind=_EXTENSION_STATE_KIND
        )
        data = dict(current.data)
        for field in remove_fields:
            data.pop(field, None)
        data.update(extension_data)
        validate_json_value(data)
        retained = self._dedupe_objects((*referenced_objects, state_object))
        for value in retained:
            if self.storage.objects.inspect(value.digest) != value:
                raise HostExtensionError(
                    f"Host extension object metadata is not owned by this store: {value.digest}"
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
                extension_state=(namespace, state_object),
            )
        return self.load(receipt.projection.task_id)

    def recover_legacy_namespace(
        self,
        *,
        task_id: str,
        expected_revision: int,
        expected_legacy_state_digest: str,
        event_id: str,
        kind: EventKind,
        state: dict[str, JsonValue],
        referenced_objects: tuple[StoredObject, ...] = (),
        expected_state: TaskState | None = None,
        expected_frontier: tuple[str, ...] | None = None,
        label: str = "Host extension legacy recovery",
        error_factory: ErrorFactory | None = None,
    ) -> HostExtensionSnapshot:
        """Replace one exact migrated legacy namespace with explicit v5 owner state.

        Migration cannot infer which fields in a historical extension Event belonged
        to one owner. The owner must therefore supply the complete replacement state
        after reading the exact legacy digest. This method is intentionally invalid
        for an absent namespace or a namespace that is already native v5 state.
        """
        validate_json_value(state)
        if kind.name != "EXTENSION":
            raise ValueError("HostExtensionPort requires a non-core Event kind")
        namespace = kind.namespace
        retained = self.storage.read_task_extension_state(task_id, namespace)
        if retained is None:
            raise HostExtensionError("extension namespace has no migrated legacy state")
        pointer, _legacy_data = retained
        if not pointer.legacy:
            raise HostExtensionError("extension namespace is already native v5 state")
        if pointer.state_digest != expected_legacy_state_digest:
            raise HostExtensionError("legacy extension state digest changed before recovery")

        current = self.load(task_id)
        if expected_state is None:
            expected_state = current.projection.state
        if expected_frontier is None:
            expected_frontier = current.projection.ready_frontier
        replacement = dict(state)
        state_object = self.storage.put_object(replacement, kind=_EXTENSION_STATE_KIND)
        data = dict(current.data)
        data.update(replacement)
        validate_json_value(data)
        retained_objects = self._dedupe_objects((*referenced_objects, state_object))
        for value in retained_objects:
            if self.storage.objects.inspect(value.digest) != value:
                raise HostExtensionError(
                    f"Host extension object metadata is not owned by this store: {value.digest}"
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
                referenced_objects=retained_objects,
                extension_state=(namespace, state_object),
            )
        return self.load_namespace(receipt.projection.task_id, namespace)

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
