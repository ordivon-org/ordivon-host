from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from ordivon_host.domain import EventKind
from ordivon_host.external_executor import (
    ExternalCompletionProposal,
    ExternalExecutionRequest,
    ExternalExecutorCoordinator,
    ExternalRunObservation,
    ExternalRunStatus,
)
from ordivon_host.extensions import HostExtensionPort
from ordivon_host.kernel import HostKernel
from ordivon_host.observation_export import (
    HostObservationExportError,
    MAPPING_VERSION,
    export_host_observations,
)
from ordivon_host.storage import HostStorage

OBSERVATION_AVAILABLE = importlib.util.find_spec("ordivon_observation_core") is not None
if OBSERVATION_AVAILABLE:
    import ordivon_observation_core as observation  # noqa: E402

OWNER_REVISION = "1" * 40
EXPORTER_REVISION = "2" * 40
INSTANCE_ID = "host:observation-test"
CONTRACT_DIGEST = "sha256:" + "a" * 64


class _Adapter:
    adapter_id = "external-executor:observation-test"

    def start(self, request: ExternalExecutionRequest) -> ExternalRunObservation:
        return ExternalRunObservation(
            foreign_run_ref="harness-run:observation-test",
            status=ExternalRunStatus.RUNNING,
            revision=1,
            evidence_refs=("evidence:observation-test",),
            observed_at_ms=request.created_at_ms + 1,
            metadata={"private": "must not be exported"},
        )

    def observe(self, foreign_run_ref: str) -> ExternalRunObservation:
        raise AssertionError(f"unexpected observe: {foreign_run_ref}")

    def cancel(self, foreign_run_ref: str, request_id: str) -> ExternalRunObservation:
        raise AssertionError(f"unexpected cancel: {foreign_run_ref} {request_id}")

    def recover(
        self, request: ExternalExecutionRequest, foreign_run_ref: str | None
    ) -> ExternalRunObservation:
        return self.start(request)

    def collect_completion(
        self, foreign_run_ref: str
    ) -> ExternalCompletionProposal | None:
        return None


@unittest.skipUnless(OBSERVATION_AVAILABLE, "exact Observation contract is optional")
class HostObservationExporterTests(unittest.TestCase):
    def create_history(self, root: Path) -> None:
        clock = iter(range(1_000, 2_000)).__next__
        with HostStorage(root) as storage:
            kernel = HostKernel(
                storage,
                clock_ms=clock,
                owner_id="host:observation-test",
            )
            projection = kernel.create_task(
                event_id="event:observation-test:created",
                kind=EventKind.TASK_CREATED,
                task_id="task:observation-test",
                goal_id="goal:observation-test",
                payload={"private": "task payload must not be exported"},
                frontier=("node:observation-test",),
            ).projection
            coordinator = ExternalExecutorCoordinator(HostExtensionPort(storage, kernel))
            coordinator.start(
                ExternalExecutionRequest(
                    request_id="external-request:observation-test",
                    adapter_id=_Adapter.adapter_id,
                    task_id=projection.task_id,
                    task_revision=projection.revision,
                    task_attempt_ref="task-attempt:observation-test",
                    contract_digest=CONTRACT_DIGEST,
                    correlation_context={"private": "trace content"},
                    created_at_ms=1_100,
                ),
                _Adapter(),
            )

    @staticmethod
    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if (
                path.is_file()
                and not path.is_symlink()
                and not path.name.endswith(("-wal", "-shm"))
            )
        }

    def run_export(
        self,
        directory: str,
        *,
        limit: int = 256,
        fail_after_bundle: bool = False,
        exported_at_ms: int = 2_000,
    ) -> dict[str, object]:
        root = Path(directory)
        return export_host_observations(
            state_root=root / "host",
            instance_id=INSTANCE_ID,
            checkpoint_path=root / "sidecar" / "host.json",
            outbox_root=root / "outbox",
            owner_revision=OWNER_REVISION,
            exporter_revision=EXPORTER_REVISION,
            exported_at_ms=exported_at_ms,
            limit=limit,
            fail_after_bundle=fail_after_bundle,
        )

    def test_metadata_only_export_gateway_ingest_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_history(root / "host")
            before = self.snapshot(root / "host")
            result = self.run_export(directory)
            self.assertEqual(result["status"], "exported")
            self.assertEqual(result["eventCount"], 3)
            self.assertEqual(self.snapshot(root / "host"), before)
            bundle_path = Path(str(result["bundlePath"]))
            encoded = bundle_path.read_text(encoding="utf-8")
            for secret in (
                "task payload must not be exported",
                "trace content",
                "must not be exported",
            ):
                self.assertNotIn(secret, encoded)
            bundle = observation.ObservationExportBundle.from_dict(json.loads(encoded))
            relations = [
                relation.to_dict()
                for batch in bundle.batches
                for event in batch.events
                for relation in event.relations
            ]
            self.assertTrue(
                any(
                    item["relationType"] == "executes"
                    and item["targetId"] == "harness-run:observation-test"
                    for item in relations
                )
            )
            producer = observation.ObservationProducerIdentity(
                "ordivon-host", "host-journal", INSTANCE_ID
            )
            with observation.SQLiteObservationGateway.initialize(
                root / "gateway",
                gateway_instance_id="observation-gateway:host-test",
                producer_allowlist=(producer,),
                mapping_versions=(("ordivon-host", "host-journal", MAPPING_VERSION),),
                created_at_ms=100,
            ) as gateway:
                accepted = 0
                for batch in bundle.batches:
                    accepted += gateway.ingest(batch, ingested_at_ms=3_000).accepted
                self.assertEqual(accepted, 3)
                self.assertTrue(gateway.doctor(full=True)["healthy"])
            replay = self.run_export(directory, exported_at_ms=2_001)
            self.assertEqual(replay["status"], "no_events")

    def test_pagination_and_failure_after_bundle_are_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_history(root / "host")
            with self.assertRaisesRegex(HostObservationExportError, "injected failure"):
                self.run_export(directory, limit=2, fail_after_bundle=True)
            self.assertFalse((root / "sidecar" / "host.json").exists())
            self.assertEqual(len(tuple((root / "outbox").glob("bundle-*.json"))), 1)
            recovered = self.run_export(directory, limit=2)
            self.assertEqual(recovered["eventCount"], 2)
            self.assertEqual(len(tuple((root / "outbox").glob("bundle-*.json"))), 1)
            final = self.run_export(directory, limit=2, exported_at_ms=2_001)
            self.assertEqual(final["eventCount"], 1)
            empty = self.run_export(directory, limit=2, exported_at_ms=2_002)
            self.assertEqual(empty["status"], "no_events")

    def test_sidecars_inside_owner_root_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_history(root / "host")
            with self.assertRaisesRegex(HostObservationExportError, "outside"):
                export_host_observations(
                    state_root=root / "host",
                    instance_id=INSTANCE_ID,
                    checkpoint_path=root / "host" / "checkpoint.json",
                    outbox_root=root / "outbox",
                    owner_revision=OWNER_REVISION,
                    exporter_revision=EXPORTER_REVISION,
                    exported_at_ms=2_000,
                )


if __name__ == "__main__":
    unittest.main()
