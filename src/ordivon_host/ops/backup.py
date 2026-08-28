from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from ..journal.migrations import migration_history, schema_version
from ..objects import StoredObject
from ..storage import HostStorage

_BACKUP_KIND = "ordivon.host-backup-manifest"


def create_backup(
    state_root: str | Path,
    destination: str | Path,
    *,
    created_at_ms: int | None = None,
) -> dict[str, object]:
    source = Path(state_root)
    target = Path(destination)
    if not (source / "host.sqlite3").is_file():
        raise FileNotFoundError(source / "host.sqlite3")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        database = temporary / "host.sqlite3"
        objects_root = temporary / "objects"
        objects_root.mkdir(mode=0o700)
        os.chmod(temporary, 0o700)
        # Freeze one exact SQLite authority snapshot first. Facts committed after this
        # point belong to the next backup and must not change this backup's CAS inventory.
        with HostStorage(
            source,
            validation_mode="targeted",
            update_validation_cache=False,
        ) as storage:
            _backup_database(storage, database)
            refs, version, migrations = _snapshot_inventory(database)
            for ref in refs:
                source_path = storage.objects.root / f"{ref.digest[7:]}.json"
                target_path = objects_root / source_path.name
                shutil.copyfile(source_path, target_path)
                os.chmod(target_path, 0o600)
                _fsync_file(target_path)

        # Validate the assembled snapshot, not a newer live authority. This catches a
        # missing/tampered CAS copied for a ref that entered the SQLite snapshot and
        # guarantees create_backup never publishes bytes that verify_backup would reject.
        _verify_backup_semantics_without_mutation(temporary)
        timestamp = int(time.time() * 1_000) if created_at_ms is None else created_at_ms
        manifest: dict[str, object] = {
            "schemaVersion": 1,
            "kind": _BACKUP_KIND,
            "createdAt": datetime.fromtimestamp(
                timestamp / 1_000, timezone.utc
            ).isoformat(),
            "createdAtMs": timestamp,
            "hostJournalSchemaVersion": version,
            "sourceStateRoot": str(source),
            "migrations": migrations,
            "objectRefs": [
                {"digest": ref.digest, "kind": ref.kind, "byteLength": ref.byte_length}
                for ref in refs
            ],
            "files": _file_manifest(temporary),
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        _fsync_file(manifest_path)
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _snapshot_inventory(
    database: Path,
) -> tuple[tuple[StoredObject, ...], int, list[dict[str, object]]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        refs = tuple(
            StoredObject(
                str(row["digest"]),
                int(row["byte_length"]),
                str(row["kind"]),
            )
            for row in connection.execute(
                "SELECT digest, byte_length, kind FROM object_refs ORDER BY digest"
            )
        )
        version = schema_version(connection)
        migrations = list(migration_history(connection))
    finally:
        connection.close()
    return refs, version, migrations


def verify_backup(backup_root: str | Path) -> dict[str, object]:
    backup = Path(backup_root)
    value = json.loads((backup / "manifest.json").read_text())
    if not isinstance(value, dict) or value.get("kind") != _BACKUP_KIND:
        raise ValueError("Host backup manifest kind is invalid")
    if value.get("schemaVersion") != 1:
        raise ValueError("Host backup manifest schema version is invalid")
    if not isinstance(value.get("createdAt"), str) or type(value.get("createdAtMs")) is not int:
        raise ValueError("Host backup manifest creation metadata is invalid")
    if not isinstance(value.get("sourceStateRoot"), str):
        raise ValueError("Host backup manifest source state root is invalid")

    files = value.get("files")
    if not isinstance(files, list):
        raise ValueError("Host backup file manifest is invalid")
    actual_files = [
        item for item in _file_manifest(backup) if item["path"] != "manifest.json"
    ]
    if files != actual_files:
        raise ValueError("Host backup file manifest differs from backup bytes")

    refs, version, migrations = _snapshot_inventory(backup / "host.sqlite3")
    if value.get("hostJournalSchemaVersion") != version:
        raise ValueError("Host backup manifest Journal schema differs")
    expected_migrations = list(migrations)
    if value.get("migrations") != expected_migrations:
        raise ValueError("Host backup manifest migration history differs")
    expected_refs = [
        {"digest": ref.digest, "kind": ref.kind, "byteLength": ref.byte_length}
        for ref in refs
    ]
    if value.get("objectRefs") != expected_refs:
        raise ValueError("Host backup manifest object references differ")

    _verify_backup_semantics_without_mutation(backup)
    return value


def _verify_backup_semantics_without_mutation(backup: Path) -> None:
    """Validate current semantics against a disposable copy, never the evidence."""
    with tempfile.TemporaryDirectory(
        prefix=f"ordivon-host-backup-{backup.name}.verify-"
    ) as directory:
        validation = Path(directory)
        shutil.copyfile(backup / "host.sqlite3", validation / "host.sqlite3")
        os.chmod(validation / "host.sqlite3", 0o600)
        shutil.copytree(
            backup / "objects",
            validation / "objects",
            copy_function=shutil.copyfile,
        )
        os.chmod(validation, 0o700)
        os.chmod(validation / "objects", 0o700)
        with HostStorage(
            validation,
            validation_mode="full",
            update_validation_cache=False,
        ):
            pass


def restore_backup(
    backup_root: str | Path,
    target_root: str | Path,
    *,
    replace: bool = False,
) -> dict[str, object]:
    backup = Path(backup_root)
    target = Path(target_root)
    manifest = verify_backup(backup)
    if target.exists() and not replace:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    previous: Path | None = None
    try:
        shutil.copyfile(backup / "host.sqlite3", temporary / "host.sqlite3")
        os.chmod(temporary / "host.sqlite3", 0o600)
        shutil.copytree(backup / "objects", temporary / "objects")
        os.chmod(temporary, 0o700)
        os.chmod(temporary / "objects", 0o700)
        for path in (temporary / "objects").glob("*.json"):
            os.chmod(path, 0o600)
        with HostStorage(temporary):
            pass
        if target.exists():
            previous = target.with_name(
                f"{target.name}.previous-{int(time.time() * 1_000)}"
            )
            os.replace(target, previous)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if previous is not None and previous.exists() and not target.exists():
            os.replace(previous, target)
        raise
    return {
        "restored": True,
        "targetRoot": str(target),
        "previousRoot": str(previous) if previous is not None else None,
        "manifest": manifest,
    }


def _backup_database(storage: HostStorage, destination: Path) -> None:
    target = sqlite3.connect(destination)
    try:
        storage.journal.connection.backup(target)
        mode = target.execute("PRAGMA journal_mode = DELETE").fetchone()
        if mode is None or str(mode[0]).lower() != "delete":
            raise RuntimeError("Host Journal backup did not become standalone")
        if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Host Journal backup failed quick_check")
    except BaseException:
        target.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target.close()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)
    os.chmod(destination, 0o600)
    _fsync_file(destination)


def _file_manifest(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        encoded = path.read_bytes()
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "digest": _sha256(encoded),
                "byteLength": len(encoded),
            }
        )
    return result


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
