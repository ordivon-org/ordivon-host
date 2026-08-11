from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from anc_canonical import canonical_digest as owner_digest
from anc_canonical import loads_strict

from .journal._schema import SCHEMA_VERSION

MAPPING_VERSION = "host-observation-v1"
PROJECT_ID = "ordivon-host"
COMPONENT_ID = "host-journal"
_TYPED_FIELDS = {
    "external-execution-request": (
        "requestId",
        "taskId",
        "taskAttemptRef",
        "contractDigest",
    ),
    "external-run-binding": (
        "bindingId",
        "requestId",
        "foreignRunRef",
        "taskId",
        "taskAttemptRef",
        "contractDigest",
    ),
    "external-completion-proposal": (
        "proposalId",
        "foreignRunRef",
        "contractDigest",
    ),
}


class HostObservationExportError(RuntimeError):
    pass


def _core() -> Any:
    try:
        import ordivon_observation_core as core
    except ImportError as error:
        raise HostObservationExportError(
            "install the exact ordivon-observation-core exporter contract"
        ) from error
    return core


def _revision(value: str, label: str) -> str:
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{label} must be an exact 40-character Git revision")
    return value


def _private_directory(path: Path, label: str, *, create: bool) -> Path:
    value = path.expanduser()
    if value.is_symlink():
        raise HostObservationExportError(f"{label} cannot be a symlink")
    if not value.exists():
        if not create:
            raise HostObservationExportError(f"{label} does not exist")
        value.mkdir(parents=True, mode=0o700)
        os.chmod(value, 0o700)
    resolved = value.resolve(strict=True)
    if not resolved.is_dir() or stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise HostObservationExportError(f"{label} must be a private 0700 directory")
    return resolved


def _outside_owner(path: Path, owner_root: Path, label: str) -> None:
    resolved = path.expanduser().resolve(strict=False)
    if resolved == owner_root or owner_root in resolved.parents:
        raise HostObservationExportError(f"{label} must remain outside the Host state root")


def _database(root: Path) -> Path:
    database = root / "host.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise HostObservationExportError("Host database must be a regular non-symlink file")
    if stat.S_IMODE(database.stat().st_mode) != 0o600:
        raise HostObservationExportError("Host database must have mode 0600")
    return database


def _connection(database: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(database), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    row = connection.execute(
        "SELECT value FROM host_metadata WHERE key='schema_version'"
    ).fetchone()
    if row is None or int(row["value"]) != SCHEMA_VERSION:
        connection.close()
        raise HostObservationExportError(f"Host schema must be exactly {SCHEMA_VERSION}")
    return connection


def _load_typed_object(objects_root: Path, digest: str, kind: str) -> dict[str, str]:
    fields = _TYPED_FIELDS.get(kind)
    if fields is None:
        return {}
    path = objects_root / f"{digest.removeprefix('sha256:')}.json"
    if path.is_symlink() or not path.is_file():
        raise HostObservationExportError(f"typed Host object is absent: {digest}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise HostObservationExportError(f"typed Host object is not private: {digest}")
    value = loads_strict(path.read_bytes())
    if owner_digest(value) != digest:
        raise HostObservationExportError(f"typed Host object digest differs: {digest}")
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "kind", "payload"}
        or value["schemaVersion"] != 1
        or value["kind"] != kind
        or not isinstance(value["payload"], dict)
    ):
        raise HostObservationExportError(f"typed Host object envelope differs: {digest}")
    payload = value["payload"]
    result: dict[str, str] = {}
    for field in fields:
        item = payload.get(field)
        if not isinstance(item, str) or not item:
            raise HostObservationExportError(
                f"typed Host object {kind} has invalid {field}"
            )
        result[field] = item
    return result


def _relations(core: Any, row: sqlite3.Row, refs: list[sqlite3.Row], typed: list[tuple[str, dict[str, str]]]) -> tuple[Any, ...]:
    target = "ordivon.host.task" if row["stream_kind"] == "task" else "ordivon.host.goal"
    values = [core.ObservationRelation("belongs_to", target, row["stream_id"])]
    if row["caused_by_event_id"] is not None:
        values.append(
            core.ObservationRelation(
                "caused_by", "ordivon.host.event", row["caused_by_event_id"]
            )
        )
    for ref in refs:
        if ref["role"] == "reference":
            values.append(
                core.ObservationRelation(
                    "references",
                    "ordivon.host.object",
                    ref["digest"],
                    ref["digest"],
                )
            )
    for kind, item in typed:
        if kind == "external-execution-request":
            values.append(
                core.ObservationRelation(
                    "linked_to", "ordivon.host.external-request", item["requestId"]
                )
            )
            values.append(
                core.ObservationRelation(
                    "linked_to", "ordivon.host.task-attempt", item["taskAttemptRef"]
                )
            )
        elif kind == "external-run-binding":
            values.append(
                core.ObservationRelation(
                    "linked_to", "ordivon.host.external-request", item["requestId"]
                )
            )
            values.append(
                core.ObservationRelation(
                    "executes", "ordivon.harness.run", item["foreignRunRef"]
                )
            )
        elif kind == "external-completion-proposal":
            values.append(
                core.ObservationRelation(
                    "proposes_for", "ordivon.harness.run", item["foreignRunRef"]
                )
            )
    return tuple(sorted(set(values)))


def _read_events(
    state_root: Path,
    *,
    producer: Any,
    stream_id: str,
    after_sequence: int,
    limit: int,
) -> tuple[Any, ...]:
    core = _core()
    objects_root = state_root / "objects"
    if objects_root.is_symlink() or stat.S_IMODE(objects_root.stat().st_mode) != 0o700:
        raise HostObservationExportError("Host objects root must be a private directory")
    connection = _connection(_database(state_root))
    try:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT sequence,event_id,stream_id,stream_kind,stream_revision,event_kind,"
            "payload_digest,caused_by_event_id,recorded_at_ms FROM events "
            "WHERE sequence>? ORDER BY sequence LIMIT ?",
            (after_sequence, limit),
        ).fetchall()
        events: list[Any] = []
        for row in rows:
            refs = connection.execute(
                "SELECT r.digest,r.role,o.kind,o.byte_length FROM event_object_refs r "
                "JOIN object_refs o ON o.digest=r.digest WHERE r.event_id=? "
                "ORDER BY r.role,r.digest",
                (row["event_id"],),
            ).fetchall()
            typed: list[tuple[str, dict[str, str]]] = []
            for ref in refs:
                if ref["role"] == "reference" and ref["kind"] in _TYPED_FIELDS:
                    typed.append(
                        (
                            ref["kind"],
                            _load_typed_object(objects_root, ref["digest"], ref["kind"]),
                        )
                    )
            native = {
                "sequence": int(row["sequence"]),
                "eventId": row["event_id"],
                "streamId": row["stream_id"],
                "streamKind": row["stream_kind"],
                "streamRevision": int(row["stream_revision"]),
                "eventKind": row["event_kind"],
                "payloadDigest": row["payload_digest"],
                "causedByEventId": row["caused_by_event_id"],
                "recordedAtMs": int(row["recorded_at_ms"]),
                "references": [
                    {"digest": ref["digest"], "role": ref["role"], "kind": ref["kind"]}
                    for ref in refs
                ],
                "typedKeys": [item for _, item in typed],
            }
            source = core.ObservationSource(
                project_id=PROJECT_ID,
                component_id=COMPONENT_ID,
                instance_id=producer.instance_id,
                stream_id=stream_id,
                sequence=int(row["sequence"]),
                native_kind=f"ordivon.host.{row['event_kind']}",
                native_id=row["event_id"],
                native_revision=int(row["stream_revision"]),
                native_digest=core.canonical_digest(native),
                mapping_version=MAPPING_VERSION,
            )
            events.append(
                core.ObservationEnvelope.build(
                    occurred_at_ms=int(row["recorded_at_ms"]),
                    source=source,
                    relations=_relations(core, row, refs, typed),
                    attributes={
                        "eventKind": row["event_kind"],
                        "streamKind": row["stream_kind"],
                        "streamRevision": int(row["stream_revision"]),
                        "referenceCount": sum(ref["role"] == "reference" for ref in refs),
                        "typedKeyKinds": [kind for kind, _ in typed],
                    },
                    privacy=core.ObservationPrivacy(
                        "private_content_ref", "host-observation-metadata-v1"
                    ),
                    payload_ref=core.ObservationPayloadRef(
                        owner=PROJECT_ID,
                        kind="ordivon.host.event-payload",
                        native_id=row["event_id"],
                        digest_value=row["payload_digest"],
                        locator_class="owner_cas",
                    ),
                )
            )
        connection.rollback()
        return tuple(events)
    finally:
        connection.close()


def export_host_observations(
    *,
    state_root: str | Path,
    instance_id: str,
    checkpoint_path: str | Path,
    outbox_root: str | Path,
    owner_revision: str,
    exporter_revision: str,
    exported_at_ms: int,
    limit: int = 256,
    fail_after_bundle: bool = False,
) -> dict[str, Any]:
    core = _core()
    if not instance_id or instance_id != instance_id.strip():
        raise ValueError("instance_id must be non-empty and trimmed")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    _revision(owner_revision, "owner_revision")
    _revision(exporter_revision, "exporter_revision")
    if exported_at_ms < 0:
        raise ValueError("exported_at_ms must be non-negative")
    owner_root = _private_directory(Path(state_root), "Host state root", create=False)
    checkpoint = Path(checkpoint_path)
    outbox = Path(outbox_root)
    _outside_owner(checkpoint, owner_root, "checkpoint")
    _outside_owner(outbox, owner_root, "outbox")
    producer = core.ObservationProducerIdentity(PROJECT_ID, COMPONENT_ID, instance_id)
    stream_id = f"host-journal:{instance_id}"
    before = core.load_checkpoint(
        checkpoint, producer_identity=producer, mapping_version=MAPPING_VERSION
    )
    events = _read_events(
        owner_root,
        producer=producer,
        stream_id=stream_id,
        after_sequence=before.sequence(stream_id),
        limit=limit,
    )
    if not events:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-observation-export-result",
            "status": "no_events",
            "eventCount": 0,
            "lastSequence": before.sequence(stream_id),
            "checkpointDigest": before.integrity_digest,
            "bundlePath": None,
            "bundleDigest": None,
        }
    after = before.advance(
        {stream_id: events[-1].source.sequence}, updated_at_ms=exported_at_ms
    )
    batches = tuple(
        core.ObservationBatch.build(
            request_id=(
                f"host-observation:{instance_id}:"
                f"{chunk[0].source.sequence}-{chunk[-1].source.sequence}"
            ),
            events=chunk,
        )
        for offset in range(0, len(events), core.MAX_BATCH_EVENTS)
        if (chunk := events[offset : offset + core.MAX_BATCH_EVENTS])
    )
    bundle = core.ObservationExportBundle.build(
        producer_identity=producer,
        mapping_version=MAPPING_VERSION,
        owner_revision=owner_revision,
        exporter_revision=exporter_revision,
        exported_at_ms=exported_at_ms,
        checkpoint_before=before,
        checkpoint_after=after,
        batches=batches,
    )
    bundle_path = core.write_export_bundle(outbox, bundle)
    if fail_after_bundle:
        raise HostObservationExportError("injected failure after durable bundle")
    core.write_checkpoint(
        checkpoint,
        after,
        expected_digest=(before.integrity_digest if checkpoint.exists() else None),
    )
    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-observation-export-result",
        "status": "exported",
        "ownerRevision": owner_revision,
        "exporterRevision": exporter_revision,
        "eventCount": len(events),
        "batchCount": len(batches),
        "lastSequence": events[-1].source.sequence,
        "checkpointBeforeDigest": before.integrity_digest,
        "checkpointAfterDigest": after.integrity_digest,
        "bundlePath": str(bundle_path),
        "bundleDigest": bundle.integrity_digest,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export bounded Host metadata observations")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--owner-revision", required=True)
    parser.add_argument("--exporter-revision", required=True)
    parser.add_argument("--exported-at-ms", type=int)
    parser.add_argument("--limit", type=int, default=256)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = export_host_observations(
            state_root=args.state_root,
            instance_id=args.instance_id,
            checkpoint_path=args.checkpoint,
            outbox_root=args.outbox,
            owner_revision=args.owner_revision,
            exporter_revision=args.exporter_revision,
            exported_at_ms=(
                args.exported_at_ms
                if args.exported_at_ms is not None
                else time.time_ns() // 1_000_000
            ),
            limit=args.limit,
        )
    except (HostObservationExportError, OSError, sqlite3.Error, ValueError) as error:
        print(f"host observation export: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
