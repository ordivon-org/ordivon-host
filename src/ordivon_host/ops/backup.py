from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
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
                _copy_regular_file_no_follow(
                    source_path, target_path, label=f"Host source CAS {ref.digest}"
                )
                os.chmod(target_path, 0o600)
                _fsync_file(target_path)

        _validate_backup_root(temporary, require_manifest=False)
        _validate_object_inventory(objects_root, refs)

        # Validate the assembled snapshot, not a newer live authority. This catches a
        # missing/tampered CAS copied for a ref that entered the SQLite snapshot and
        # guarantees create_backup never publishes bytes that verify_backup would reject.
        _verify_backup_semantics_without_mutation(temporary, refs)
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
            "files": _file_manifest_for_refs(temporary, refs),
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
    _validate_backup_root(backup)
    value = json.loads((backup / "manifest.json").read_text())
    if not isinstance(value, dict) or value.get("kind") != _BACKUP_KIND:
        raise ValueError("Host backup manifest kind is invalid")
    if value.get("schemaVersion") != 1:
        raise ValueError("Host backup manifest schema version is invalid")
    if not isinstance(value.get("createdAt"), str) or type(value.get("createdAtMs")) is not int:
        raise ValueError("Host backup manifest creation metadata is invalid")
    if not isinstance(value.get("sourceStateRoot"), str):
        raise ValueError("Host backup manifest source state root is invalid")

    try:
        refs, version, migrations = _snapshot_inventory(backup / "host.sqlite3")
    except sqlite3.Error as error:
        raise ValueError("Host backup Journal is invalid") from error
    _validate_object_inventory(backup / "objects", refs)

    files = value.get("files")
    if not isinstance(files, list):
        raise ValueError("Host backup file manifest is invalid")
    actual_files = _file_manifest_for_refs(backup, refs)
    if files != actual_files:
        raise ValueError("Host backup file manifest differs from backup bytes")

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

    _verify_backup_semantics_without_mutation(backup, refs)
    return value


def _validate_backup_root(backup: Path, *, require_manifest: bool = True) -> None:
    if not backup.is_dir():
        raise ValueError("Host backup root is not a directory")
    expected = {"host.sqlite3", "objects"}
    if require_manifest:
        expected.add("manifest.json")
    actual = {entry.name for entry in os.scandir(backup)}
    if actual != expected:
        raise ValueError("Host backup root layout differs")
    _require_regular_file(backup / "host.sqlite3", "Host backup Journal")
    if require_manifest:
        _require_regular_file(backup / "manifest.json", "Host backup manifest")
    objects = backup / "objects"
    mode = os.lstat(objects).st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError("Host backup objects entry is not a real directory")


def _validate_object_inventory(objects_root: Path, refs: tuple[StoredObject, ...]) -> None:
    expected = {f"{ref.digest[7:]}.json" for ref in refs}
    actual: set[str] = set()
    with os.scandir(objects_root) as entries:
        for entry in entries:
            actual.add(entry.name)
            if not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                raise ValueError(
                    f"Host backup object entry is not a regular file: {entry.name}"
                )
    if actual != expected:
        raise ValueError("Host backup object inventory differs from frozen Journal")


def _require_regular_file(path: Path, label: str) -> None:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        raise ValueError(f"{label} is missing") from None
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} is not a regular file")


def _verify_backup_semantics_without_mutation(
    backup: Path, refs: tuple[StoredObject, ...]
) -> None:
    """Validate current semantics against a disposable copy, never the evidence."""
    with tempfile.TemporaryDirectory(
        prefix=f"ordivon-host-backup-{backup.name}.verify-"
    ) as directory:
        validation = Path(directory)
        _copy_snapshot_authority(backup, validation, refs)
        with HostStorage(
            validation,
            validation_mode="full",
            update_validation_cache=False,
        ):
            pass


def _copy_snapshot_authority(
    source: Path, destination: Path, refs: tuple[StoredObject, ...]
) -> None:
    _copy_regular_file_no_follow(
        source / "host.sqlite3",
        destination / "host.sqlite3",
        label="Host backup Journal",
    )
    os.chmod(destination / "host.sqlite3", 0o600)
    objects = destination / "objects"
    objects.mkdir(mode=0o700)
    os.chmod(destination, 0o700)
    for ref in refs:
        name = f"{ref.digest[7:]}.json"
        target = objects / name
        _copy_regular_file_no_follow(
            source / "objects" / name, target, label=f"Host backup CAS {ref.digest}"
        )
        os.chmod(target, 0o600)


def _copy_regular_file_no_follow(source: Path, destination: Path, *, label: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise ValueError(f"{label} is not a readable regular file") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as source_file, destination.open(
            "xb"
        ) as target_file:
            shutil.copyfileobj(source_file, target_file)
    finally:
        os.close(descriptor)

def restore_backup(
    backup_root: str | Path,
    target_root: str | Path,
    *,
    replace: bool = False,
) -> dict[str, object]:
    backup = Path(backup_root)
    target = Path(target_root)
    if target.exists() and not replace:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    staged_parent = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.restore-evidence-", dir=target.parent)
    )
    staged_backup = staged_parent / "backup"
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.restore-state-", dir=target.parent)
    )
    previous: Path | None = None
    try:
        _stage_backup_evidence(backup, staged_backup)
        manifest = verify_backup(staged_backup)
        refs, _, _ = _snapshot_inventory(staged_backup / "host.sqlite3")
        if target.exists() and not replace:
            raise FileExistsError(target)

        # Realize current Host state only from the exact staged evidence that just
        # verified. Never reread caller `backup` after this verification boundary.
        _copy_snapshot_authority(staged_backup, temporary, refs)
        with HostStorage(temporary):
            pass
        _fsync_file(temporary / "host.sqlite3")
        for ref in refs:
            _fsync_file(temporary / "objects" / f"{ref.digest[7:]}.json")
        _fsync_directory(temporary / "objects")
        _fsync_directory(temporary)

        if target.exists():
            if not replace:
                raise FileExistsError(target)
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
            _fsync_directory(target.parent)
        raise
    finally:
        shutil.rmtree(staged_parent, ignore_errors=True)
    return {
        "restored": True,
        "targetRoot": str(target),
        "previousRoot": str(previous) if previous is not None else None,
        "manifest": manifest,
    }


def _stage_backup_evidence(source: Path, destination: Path) -> None:
    """Copy one bounded backup carrier once; later verification consumes only it."""
    _validate_backup_root(source)
    destination.mkdir(mode=0o700)

    database = destination / "host.sqlite3"
    _copy_regular_file_no_follow(
        source / "host.sqlite3", database, label="Host backup Journal"
    )
    os.chmod(database, 0o600)
    _fsync_file(database)
    try:
        refs, _, _ = _snapshot_inventory(database)
    except sqlite3.Error as error:
        raise ValueError("Staged Host backup Journal is invalid") from error

    # Staging is evidence capture, not repair: the caller source must itself expose
    # exactly the DB-derived object inventory before any selective copy can sanitize
    # an orphan, symlink, nested entry, or other over-complete physical topology.
    _validate_object_inventory(source / "objects", refs)

    objects = destination / "objects"
    objects.mkdir(mode=0o700)
    for ref in refs:
        name = f"{ref.digest[7:]}.json"
        target = objects / name
        _copy_regular_file_no_follow(
            source / "objects" / name, target, label=f"Host backup CAS {ref.digest}"
        )
        os.chmod(target, 0o600)
        _fsync_file(target)

    manifest = destination / "manifest.json"
    _copy_regular_file_no_follow(
        source / "manifest.json", manifest, label="Host backup manifest"
    )
    os.chmod(manifest, 0o600)
    _fsync_file(manifest)
    _fsync_directory(objects)
    _fsync_directory(destination)


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


def _file_manifest_for_refs(
    root: Path, refs: tuple[StoredObject, ...]
) -> list[dict[str, object]]:
    paths = [root / "host.sqlite3"] + [
        root / "objects" / f"{ref.digest[7:]}.json" for ref in refs
    ]
    result: list[dict[str, object]] = []
    for path in paths:
        _require_regular_file(path, f"Host backup file {path.relative_to(root).as_posix()}")
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
