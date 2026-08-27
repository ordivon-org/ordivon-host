from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
                # Payload names are component-local and do not implicitly retain CAS.
                opaque = port.append_preserving(
                    task_id=created.task_id,
                    expected_revision=committed.projection.revision,
                    event_id="event:extension-port:opaque",
                    kind=EventKind("harness.extension-recorded"),
                    updates={"looksLikeObjectDigest": item.digest},
                )
                self.assertEqual(opaque.data["looksLikeObjectDigest"], item.digest)
                with self.assertRaises(TaskRevisionMismatch):
                    port.append_preserving(
                        task_id=created.task_id,
                        expected_revision=created.revision,
                        event_id="event:extension-port:stale",
                        kind=EventKind("harness.extension-recorded"),
                        updates={"stale": True},
                    )

    def test_namespaced_state_survives_core_event_without_cross_namespace_collision(self) -> None:
        from ordivon_host import HostExtensionPort, WorkingCheckpoint
        from ordivon_host.continuity import ExternalContinuityHost

        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            with HostStorage(directory) as storage:
                host = ExternalContinuityHost(
                    storage, clock_ms=clock, owner_id="host:test-extension-continuity"
                )
                checkpoint = WorkingCheckpoint(
                    task_id="task:extension-continuity",
                    objective="Preserve opaque extension state.",
                    frontier="Continue.",
                    established=(),
                    unresolved=(),
                    rejected=(),
                    constraints=(),
                    next_actions=("Continue.",),
                    runtime=None,
                )
                adopted = host.adopt(
                    task_id=checkpoint.task_id,
                    goal_id="goal:extension-continuity",
                    initial_checkpoint=checkpoint,
                )
                port = HostExtensionPort(
                    storage,
                    HostKernel(
                        storage, clock_ms=clock, owner_id="host:test-extension-port-state"
                    ),
                )
                world = port.append_preserving(
                    task_id=checkpoint.task_id,
                    expected_revision=adopted.projection.revision,
                    event_id="event:extension-continuity:world",
                    kind=EventKind("world.outcome-unknown"),
                    updates={"worldOutcomeState": "unknown"},
                )
                external = port.append_preserving(
                    task_id=checkpoint.task_id,
                    expected_revision=world.projection.revision,
                    event_id="event:extension-continuity:external",
                    kind=EventKind("external.run-bound"),
                    updates={"externalBinding": "binding:one"},
                )
                custom = port.append_preserving(
                    task_id=checkpoint.task_id,
                    expected_revision=external.projection.revision,
                    event_id="event:extension-continuity:custom",
                    kind=EventKind("custom-owner.observed"),
                    updates={"privateCustomState": "must-not-route"},
                )
                world_at_external_head = port.load_namespace(
                    checkpoint.task_id, "world"
                )
                external_at_external_head = port.load_namespace(
                    checkpoint.task_id, "external"
                )
                self.assertEqual(
                    world_at_external_head.data, {"worldOutcomeState": "unknown"}
                )
                self.assertEqual(
                    external_at_external_head.data, {"externalBinding": "binding:one"}
                )
                later = WorkingCheckpoint(
                    task_id=checkpoint.task_id,
                    objective="Preserve opaque extension state.",
                    frontier="Continue.",
                    established=("Host meaning advanced.",),
                    unresolved=("External commitments remain owner-authored.",),
                    rejected=(),
                    constraints=(),
                    next_actions=("Inspect owners.",),
                    runtime=None,
                )
                committed = host.checkpoint(
                    task_id=checkpoint.task_id,
                    expected_revision=custom.projection.revision,
                    checkpoint=later,
                    disposition="continue",
                )
            with HostStorage(directory) as reopened:
                resumed = ExternalContinuityHost(
                    reopened,
                    clock_ms=itertools.count(3_900).__next__,
                    owner_id="host:test-extension-routing-reopened",
                ).resume(checkpoint.task_id, expected_revision=committed.projection.revision)
                self.assertEqual(
                    resumed.extension_namespaces,
                    ("custom-owner", "external", "world"),
                )
                routed = resumed.to_dict()
                self.assertEqual(
                    routed["extensionNamespaces"],
                    ["custom-owner", "external", "world"],
                )
                routed_text = str(routed)
                self.assertNotIn("worldOutcomeState", routed_text)
                self.assertNotIn("externalBinding", routed_text)
                self.assertNotIn("privateCustomState", routed_text)

                port = HostExtensionPort(
                    reopened,
                    HostKernel(
                        reopened,
                        clock_ms=itertools.count(4_000).__next__,
                        owner_id="host:test-extension-port-reopened",
                    ),
                )
                world = port.load_namespace(checkpoint.task_id, "world")
                external = port.load_namespace(checkpoint.task_id, "external")
                self.assertEqual(world.projection.revision, committed.projection.revision)
                self.assertEqual(external.projection.revision, committed.projection.revision)
                self.assertEqual(world.data["worldOutcomeState"], "unknown")
                self.assertNotIn("externalBinding", world.data)
                self.assertEqual(external.data["externalBinding"], "binding:one")
                self.assertNotIn("worldOutcomeState", external.data)


    def test_namespace_snapshot_exposes_only_host_owned_metadata_under_revision_fence(self) -> None:
        from ordivon_host import HostExtensionPort, TaskRevisionMismatch

        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(4_500).__next__
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage,
                    clock_ms=clock,
                    owner_id="host:test-extension-namespace-snapshot",
                )
                created = kernel.create_task(
                    event_id="event:namespace-snapshot:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:namespace-snapshot",
                    goal_id="goal:namespace-snapshot",
                    payload={"coreMarker": "created"},
                    frontier=("node:namespace-snapshot",),
                ).projection
                port = HostExtensionPort(storage, kernel)

                absent = port.load_namespace_snapshot(
                    created.task_id,
                    "world",
                    expected_revision=created.revision,
                )
                self.assertFalse(absent.retained)
                self.assertEqual(absent.data, {})
                self.assertEqual(absent.projection.revision, created.revision)
                self.assertIs(absent.task_event_kind, EventKind.TASK_CREATED)
                self.assertIsNone(absent.owner_event_id)
                self.assertIsNone(absent.owner_event_kind)
                self.assertIsNone(absent.owner_state_digest)
                self.assertIsNone(absent.owner_revision)
                self.assertFalse(absent.legacy)

                world = port.append_preserving(
                    task_id=created.task_id,
                    expected_revision=created.revision,
                    event_id="event:namespace-snapshot:world",
                    kind=EventKind("world.outcome-unknown"),
                    updates={"worldOutcomeState": "unknown"},
                )
                world_event = storage.read_task_event_at_revision(
                    created.task_id, world.projection.revision
                )
                assert world_event is not None
                pointer = storage.journal.task_extension_state(created.task_id, "world")
                assert pointer is not None

                retained = port.load_namespace_snapshot(
                    created.task_id,
                    "world",
                    expected_revision=world.projection.revision,
                )
                self.assertTrue(retained.retained)
                self.assertEqual(retained.data, {"worldOutcomeState": "unknown"})
                self.assertEqual(retained.owner_event_id, pointer.event_id)
                self.assertIs(retained.owner_event_kind, pointer.event_kind)
                self.assertEqual(retained.owner_state_digest, pointer.state_digest)
                self.assertEqual(retained.owner_revision, pointer.revision)
                self.assertFalse(retained.legacy)

                with kernel.locked_task(
                    created.task_id, expected_revision=world.projection.revision
                ) as locked:
                    core = locked.commit(
                        event_id="event:namespace-snapshot:core",
                        kind=EventKind.TASK_CONTEXT_CHECKPOINTED,
                        payload={"hostCheckpoint": True},
                    )
                core_event = storage.read_task_event(created.task_id)
                current = port.load_namespace_snapshot(
                    created.task_id,
                    "world",
                    expected_revision=core.projection.revision,
                )
                self.assertEqual(current.projection.revision, core.projection.revision)
                self.assertIs(
                    current.task_event_kind, EventKind.TASK_CONTEXT_CHECKPOINTED
                )
                self.assertEqual(current.owner_revision, world.projection.revision)
                self.assertIs(
                    current.owner_event_kind, EventKind("world.outcome-unknown")
                )
                self.assertEqual(current.data, {"worldOutcomeState": "unknown"})
                self.assertNotEqual(
                    current.task_payload_digest, current.owner_state_digest
                )

                with self.assertRaisesRegex(
                    TaskRevisionMismatch, "differs from expected Task revision"
                ):
                    port.load_namespace_snapshot(
                        created.task_id,
                        "world",
                        expected_revision=world.projection.revision,
                    )

                with mock.patch.object(
                    storage,
                    "read_task_event",
                    side_effect=[world_event, core_event, core_event, core_event],
                ):
                    raced = port.load_namespace_snapshot(created.task_id, "world")
                self.assertEqual(raced.projection.revision, core.projection.revision)
                self.assertEqual(raced.owner_revision, world.projection.revision)
                self.assertEqual(raced.data, {"worldOutcomeState": "unknown"})


    def test_schema_v4_extension_state_requires_pre_0_5_recovery_before_upgrade(self) -> None:
        from ordivon_host import HostExtensionPort
        from ordivon_host.journal import JournalCorruption

        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(5_000).__next__
            with HostStorage(directory) as storage:
                kernel = HostKernel(
                    storage, clock_ms=clock, owner_id="host:test-extension-legacy"
                )
                created = kernel.create_task(
                    event_id="event:extension-legacy:create",
                    kind=EventKind.TASK_CREATED,
                    task_id="task:extension-legacy",
                    goal_id="goal:extension-legacy",
                    payload={"coreMarker": "created"},
                    frontier=("node:extension-legacy",),
                ).projection
                HostExtensionPort(storage, kernel).append_preserving(
                    task_id=created.task_id,
                    expected_revision=created.revision,
                    event_id="event:extension-legacy:world",
                    kind=EventKind("world.outcome-unknown"),
                    updates={"worldOutcomeState": "unknown"},
                )

            database = Path(directory) / "host.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE task_extension_state")
            connection.execute(
                "UPDATE host_metadata SET value = '4' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                JournalCorruption,
                "pre-0.5 Host client for owner recovery/export",
            ):
                HostStorage(directory)

            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM host_metadata WHERE key = 'schema_version'"
                ).fetchone()[0],
                "4",
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='task_extension_state'"
                ).fetchone()
            )
            connection.close()



if __name__ == "__main__":
    unittest.main()
