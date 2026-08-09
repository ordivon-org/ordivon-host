from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path
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
                    expected_revision=external.projection.revision,
                    checkpoint=later,
                    disposition="continue",
                )
            with HostStorage(directory) as reopened:
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


    def test_migrated_legacy_namespace_requires_exact_recovery_before_mutation(self) -> None:
        from ordivon_host import (
            HostExtensionError,
            HostExtensionLegacyStateUnknown,
            HostExtensionPort,
        )

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
                port = HostExtensionPort(storage, kernel)
                world = port.append_preserving(
                    task_id=created.task_id,
                    expected_revision=created.revision,
                    event_id="event:extension-legacy:world",
                    kind=EventKind("world.outcome-unknown"),
                    updates={"worldOutcomeState": "unknown"},
                )
                with kernel.locked_task(
                    created.task_id, expected_revision=world.projection.revision
                ) as locked:
                    core = locked.commit(
                        event_id="event:extension-legacy:core",
                        kind=EventKind.TASK_CONTEXT_CHECKPOINTED,
                        payload={"hostCheckpoint": True},
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

            with HostStorage(directory) as reopened:
                port = HostExtensionPort(
                    reopened,
                    HostKernel(
                        reopened,
                        clock_ms=itertools.count(6_000).__next__,
                        owner_id="host:test-extension-legacy-reopened",
                    ),
                )
                retained = port.load_namespace(created.task_id, "world")
                self.assertEqual(retained.projection.revision, core.projection.revision)
                self.assertEqual(retained.data["worldOutcomeState"], "unknown")
                pointer = reopened.journal.task_extension_state(
                    created.task_id, "world"
                )
                assert pointer is not None
                self.assertTrue(pointer.legacy)
                self.assertEqual(retained.payload_digest, pointer.state_digest)
                with self.assertRaises(HostExtensionLegacyStateUnknown):
                    port.append_preserving(
                        task_id=created.task_id,
                        expected_revision=core.projection.revision,
                        event_id="event:extension-legacy:unsafe-mutation",
                        kind=EventKind("world.outcome-reconciled"),
                        updates={"worldOutcomeState": "delivered"},
                    )
                with self.assertRaises(HostExtensionError):
                    port.recover_legacy_namespace(
                        task_id=created.task_id,
                        expected_revision=core.projection.revision,
                        expected_legacy_state_digest="sha256:" + "0" * 64,
                        event_id="event:extension-legacy:wrong-recovery",
                        kind=EventKind("world.legacy-recovered"),
                        state={"worldOutcomeState": "unknown"},
                    )
                recovered = port.recover_legacy_namespace(
                    task_id=created.task_id,
                    expected_revision=core.projection.revision,
                    expected_legacy_state_digest=retained.payload_digest,
                    event_id="event:extension-legacy:recovered",
                    kind=EventKind("world.legacy-recovered"),
                    state={
                        "worldOutcomeState": "unknown",
                        "dispatchId": "dispatch:extension-legacy",
                    },
                )
                self.assertEqual(
                    recovered.data,
                    {
                        "worldOutcomeState": "unknown",
                        "dispatchId": "dispatch:extension-legacy",
                    },
                )
                self.assertEqual(recovered.projection.revision, core.projection.revision + 1)
                recovered_pointer = reopened.journal.task_extension_state(
                    created.task_id, "world"
                )
                assert recovered_pointer is not None
                self.assertFalse(recovered_pointer.legacy)
                self.assertEqual(recovered_pointer.state_digest, recovered.payload_digest)
                delivered = port.append_preserving(
                    task_id=created.task_id,
                    expected_revision=recovered.projection.revision,
                    event_id="event:extension-legacy:post-recovery-mutation",
                    kind=EventKind("world.outcome-reconciled"),
                    updates={"worldOutcomeState": "delivered"},
                )
                current_world = port.load_namespace(created.task_id, "world")
                self.assertEqual(delivered.projection.revision, recovered.projection.revision + 1)
                self.assertEqual(
                    current_world.data,
                    {
                        "worldOutcomeState": "delivered",
                        "dispatchId": "dispatch:extension-legacy",
                    },
                )



if __name__ == "__main__":
    unittest.main()
