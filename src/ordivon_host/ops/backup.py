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
        with HostStorage(source) as storage:
            _backup_database(storage, database)
            refs = storage.journal.object_refs()
            for ref in refs:
                source_path = storage.objects.root / f"{ref.digest[7:]}.json"
                target_path = objects_root / source_path.name
                shutil.copyfile(source_path, target_path)
                os.chmod(target_path, 0o600)
                _fsync_file(target_path)
            version = schema_version(storage.journal.connection)
            migrations = list(migration_history(storage.journal.connection))
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


def verify_backup(backup_root: str | Path) -> dict[str, object]:
    backup = Path(backup_root)
    value = json.loads((backup / "manifest.json").read_text())
    if not isinstance(value, dict) or value.get("kind") != _BACKUP_KIND:
        raise ValueError("Host backup manifest kind is invalid")
    files = value.get("files")
    if not isinstance(files, list):
        raise ValueError("Host backup file manifest is invalid")
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Host backup file entry is invalid")
        relative = item.get("path")
        digest = item.get("digest")
        byte_length = item.get("byteLength")
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or type(byte_length) is not int
        ):
            raise ValueError("Host backup file metadata is invalid")
        encoded = (backup / relative).read_bytes()
        if len(encoded) != byte_length or _sha256(encoded) != digest:
            raise ValueError(f"Host backup file differs: {relative}")
    with HostStorage(
        backup,
        validation_mode="full",
        update_validation_cache=False,
    ):
        pass
    return value


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
        if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Host Journal backup failed quick_check")
    except BaseException:
        target.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target.close()
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
