from __future__ import annotations

import itertools
import tempfile
import unittest

from ordivon_host import EventKind, HostKernel, HostStorage, TaskState
from ordivon_host.ops import validate_history


class ExtensionEventTests(unittest.TestCase):
    def test_extension_event_round_trips_without_host_schema_import(self) -> None:
        kind = EventKind("harness.assignment-committed")
        self.assertIs(kind, EventKind("harness.assignment-committed"))
        self.assertEqual(kind.value, "harness.assignment-committed")
        with self.assertRaises(ValueError):
            EventKind("Harness.Assignment")

        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=clock,
                    owner_id="host:test-extension-event",
                )
                created = kernel.create_task(
                    event_id="event:extension:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:extension-event",
                    goal_id="goal:extension-event",
                    payload={},
                    frontier=("node:extension:assign",),
                ).projection
                assignment = storage.put_object(
                    {"schemaVersion": 1, "kind": "fixture-assignment"},
                    kind="harness-assignment",
                )
                with kernel.locked_task(
                    created.task_id,
                    expected_revision=created.revision,
                    expected_state=TaskState.READY,
                    expected_frontier=created.ready_frontier,
                ) as locked:
                    locked.commit(
                        event_id="event:extension:assignment",
                        kind=kind,
                        payload={"assignmentObjectDigest": assignment.digest},
                        state=TaskState.WAITING,
                        frontier=("node:extension:run",),
                        referenced_objects=(assignment,),
                    )
            with HostStorage(directory) as reopened:
                snapshot = reopened.read_task_event("task:extension-event")
                self.assertEqual(snapshot.event_kind.value, kind.value)
                self.assertIs(snapshot.event_kind, EventKind(kind.value))
                report = validate_history(reopened)
                self.assertEqual(report.events, 2)
                self.assertGreaterEqual(report.semantic_references, 1)


class ExtensionPortTests(unittest.TestCase):
    def test_port_preserves_payload_and_object_references_under_revision_fence(
        self,
    ) -> None:
        from ordivon_host import HostExtensionPort, TaskRevisionMismatch

        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=clock,
                    owner_id="host:test-extension-port",
                )
                base = storage.put_object(
                    {"schemaVersion": 1, "kind": "fixture-base"},
                    kind="fixture-base",
                )
                created = kernel.create_task(
                    event_id="event:extension-port:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:extension-port",
                    goal_id="goal:extension-port",
                    payload={
                        "baseObjectDigest": base.digest,
                        "activeExtensionToken": "token:one",
                    },
                    frontier=("node:extension-port",),
                    referenced_objects=(base,),
                ).projection
                port = HostExtensionPort(storage, kernel)
                item = port.put_object(
                    {"schemaVersion": 1, "kind": "fixture-extension"},
                    kind="fixture-extension",
                )
                committed = port.append_preserving(
                    task_id=created.task_id,
                    expected_revision=created.revision,
                    event_id="event:extension-port:append",
                    kind=EventKind("harness.extension-recorded"),
                    updates={"extensionObjectDigest": item.digest},
                    remove_fields=("activeExtensionToken",),
                    referenced_objects=(item,),
                )
                self.assertEqual(committed.data["baseObjectDigest"], base.digest)
                self.assertEqual(committed.data["extensionObjectDigest"], item.digest)
                self.assertNotIn("activeExtensionToken", committed.data)
                with self.assertRaises(TaskRevisionMismatch):
                    port.append_preserving(
                        task_id=created.task_id,
                        expected_revision=created.revision,
                        event_id="event:extension-port:stale",
                        kind=EventKind("harness.extension-recorded"),
                        updates={"stale": True},
                    )


if __name__ == "__main__":
    unittest.main()
