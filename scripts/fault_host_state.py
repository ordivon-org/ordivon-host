#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from anc_canonical import JsonValue
from ordivon_host import EventKind, HostStorage, TaskProjection, TaskState
from ordivon_host.ops import (
    create_backup,
    doctor_state,
    plan_gc,
    verify_backup,
)
from ordivon_host.testing import emit_receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run destructive local Host state fault and recovery scenarios."
    )
    parser.add_argument("--root")
    parser.add_argument("--keep-root", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repository = Path(__file__).resolve().parents[1]
    host_revision = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    root = Path(args.root or tempfile.mkdtemp(prefix="ordivon-host-faults-", dir="/tmp"))
    if args.root:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
    started = time.perf_counter()
    completed = False
    try:
        scenarios = {
            "orphanAfterHostKill": orphan_after_host_kill(root / "orphan"),
            "sqliteTransactionCrash": sqlite_transaction_crash(root / "transaction"),
            "leaseOwnerCrash": lease_owner_crash(root / "lease"),
            "boundedDiskWriteFailure": bounded_disk_write_failure(root / "disk"),
            "missingCasObject": missing_cas_object(root / "missing"),
            "sqliteCorruption": sqlite_corruption(root / "corrupt"),
            "backupCrossProcessRestore": backup_cross_process_restore(
                root / "backup", repository
            ),
        }
        checks = {
            name: value.get("accepted") is True for name, value in scenarios.items()
        }
        if not all(checks.values()):
            raise AssertionError(f"Host fault matrix failed: {checks}")
        receipt: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-state-fault-matrix",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "hostRevision": host_revision,
            "elapsedMs": _elapsed_ms(started),
            "scenarios": scenarios,
            "checks": checks,
            "notProven": [
                "whole WSL restart during Host state transition",
                "machine reboot or abrupt power loss",
                "kernel crash",
                "remote Runtime network partition",
                "physical storage failure beyond detectable file or SQLite corruption",
            ],
            "retainedRoot": str(root) if args.keep_root else None,
        }
        completed = True
        emit_receipt(receipt)
    finally:
        if not args.keep_root:
            shutil.rmtree(root, ignore_errors=True)
        elif not completed:
            print(f"fault root retained after failure: {root}", file=sys.stderr)


def orphan_after_host_kill(root: Path) -> dict[str, JsonValue]:
    _initialize(root)
    code = r'''
import json, os, signal, sys
from ordivon_host import HostStorage
with HostStorage(sys.argv[1]) as storage:
    stored=storage.put_object({"fault":"orphan-after-kill"}, kind="fault-orphan")
    print(json.dumps({"digest":stored.digest}), flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
'''
    process = _run(code, root, check=False)
    value = json.loads(process.stdout)
    digest = value["digest"]
    with HostStorage(root) as storage:
        event_count = storage.journal.event_count()
        object_ref_count = storage.journal.object_ref_count()
    gc = plan_gc(root)
    doctor = doctor_state(root)
    orphan_name = f"{digest[7:]}.json"
    accepted = (
        process.returncode == -signal.SIGKILL
        and event_count == 0
        and object_ref_count == 0
        and gc["orphanedObjects"] == [orphan_name]
        and doctor["healthy"] is True
    )
    return {
        "accepted": accepted,
        "returnCode": process.returncode,
        "orphanDigest": digest,
        "eventCount": event_count,
        "objectRefCount": object_ref_count,
        "gcPlan": gc,
        "doctorHealthy": doctor["healthy"],
    }


def sqlite_transaction_crash(root: Path) -> dict[str, JsonValue]:
    _initialize(root)
    code = r'''
import os, signal, sys
from ordivon_host import HostStorage
storage=HostStorage(sys.argv[1])
storage.journal.connection.execute("BEGIN IMMEDIATE")
storage.journal.connection.execute(
    "INSERT INTO object_refs(digest,kind,byte_length,first_seen_at_ms) VALUES (?,?,?,?)",
    ("sha256:"+("a"*64),"uncommitted",1,1),
)
print("transaction-open", flush=True)
os.kill(os.getpid(), signal.SIGKILL)
'''
    process = _run(code, root, check=False)
    with HostStorage(root) as storage:
        refs = storage.journal.object_ref_count()
        quick = storage.journal.quick_check()
    accepted = (
        process.returncode == -signal.SIGKILL
        and process.stdout.strip() == "transaction-open"
        and refs == 0
        and quick == ("ok",)
    )
    return {
        "accepted": accepted,
        "returnCode": process.returncode,
        "objectRefCount": refs,
        "quickCheck": list(quick),
    }


def lease_owner_crash(root: Path) -> dict[str, JsonValue]:
    _populate(root, task_id="task:fault-lease")
    code = r'''
import json, sys, time
from ordivon_host import HostStorage
storage=HostStorage(sys.argv[1])
lease=storage.journal.acquire_lease(
    "task:fault-lease", owner_id="host:crashed", now_ms=100, ttl_ms=1000
)
print(json.dumps({"revision":lease.revision,"expiresAtMs":lease.expires_at_ms}), flush=True)
time.sleep(60)
'''
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_python_env(),
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    held = json.loads(line)
    process.kill()
    _, stderr = process.communicate(timeout=10)
    with HostStorage(root) as storage:
        rows = storage.journal.lease_records()
        takeover = storage.journal.acquire_lease(
            "task:fault-lease",
            owner_id="host:recovered",
            now_ms=held["expiresAtMs"] + 1,
            ttl_ms=100,
        )
        storage.journal.release_lease(takeover)
        remaining = storage.journal.lease_records()
    accepted = (
        process.returncode == -signal.SIGKILL
        and len(rows) == 1
        and rows[0].owner_id == "host:crashed"
        and takeover.owner_id == "host:recovered"
        and takeover.revision == rows[0].revision + 1
        and not remaining
        and not stderr
    )
    return {
        "accepted": accepted,
        "returnCode": process.returncode,
        "crashedLeaseRevision": rows[0].revision,
        "takeoverRevision": takeover.revision,
        "remainingLeases": len(remaining),
    }


def bounded_disk_write_failure(root: Path) -> dict[str, JsonValue]:
    _initialize(root)
    code = r'''
import json, resource, signal, sys
from ordivon_host import HostStorage
storage=HostStorage(sys.argv[1])
old=resource.getrlimit(resource.RLIMIT_FSIZE)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (1024, old[1]))
error=None
try:
    storage.put_object({"payload":"x"*100000}, kind="fault-disk-limit")
except OSError as exc:
    error={"type":type(exc).__name__,"errno":exc.errno}
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, old)
    storage.close()
print(json.dumps({"error":error}), flush=True)
'''
    process = _run(code, root)
    value = json.loads(process.stdout)
    with HostStorage(root) as storage:
        refs = storage.journal.object_ref_count()
        events = storage.journal.event_count()
    files = sorted(path.name for path in (root / "objects").iterdir())
    accepted = (
        value["error"] is not None
        and refs == 0
        and events == 0
        and files == []
    )
    return {
        "accepted": accepted,
        "error": value["error"],
        "objectRefCount": refs,
        "eventCount": events,
        "remainingObjectFiles": files,
    }


def missing_cas_object(root: Path) -> dict[str, JsonValue]:
    _populate(root, task_id="task:fault-missing")
    with HostStorage(root) as storage:
        digest = storage.journal.object_refs()[0].digest
    path = root / "objects" / f"{digest[7:]}.json"
    path.unlink()
    doctor = doctor_state(root)
    error_checks = [
        item
        for item in doctor["checks"]
        if isinstance(item, dict) and item.get("status") == "error"
    ]
    accepted = doctor["healthy"] is False and bool(error_checks)
    return {
        "accepted": accepted,
        "missingDigest": digest,
        "doctorHealthy": doctor["healthy"],
        "errorChecks": error_checks,
    }


def sqlite_corruption(root: Path) -> dict[str, JsonValue]:
    _initialize(root)
    database = root / "host.sqlite3"
    with database.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"NOT-A-SQLITE-DATABASE" + (b"!" * 64))
        handle.flush()
        os.fsync(handle.fileno())
    doctor = doctor_state(root)
    errors = [
        item
        for item in doctor["checks"]
        if isinstance(item, dict) and item.get("status") == "error"
    ]
    accepted = doctor["healthy"] is False and len(errors) >= 1
    return {
        "accepted": accepted,
        "doctorHealthy": doctor["healthy"],
        "errorChecks": errors,
    }


def backup_cross_process_restore(
    root: Path, repository: Path
) -> dict[str, JsonValue]:
    source = root / "source"
    backup = root / "backup"
    restored = root / "restored"
    _populate(source, task_id="task:fault-backup")
    manifest = create_backup(source, backup, created_at_ms=1_000)
    database = backup / "host.sqlite3"
    before = _sha256(database.read_bytes())
    verify_backup(backup)
    verify_backup(backup)
    after = _sha256(database.read_bytes())
    code = r'''
import json, sys
from ordivon_host.ops import inspect_state, restore_backup, verify_backup
verify_backup(sys.argv[1])
restored=restore_backup(sys.argv[1], sys.argv[2])
print(json.dumps({"restore":restored,"inspection":inspect_state(sys.argv[2])}, sort_keys=True))
'''
    process = _run(code, backup, restored)
    value = json.loads(process.stdout)
    inspection = value["inspection"]
    accepted = (
        before == after
        and manifest["hostJournalSchemaVersion"] == 3
        and inspection["tasks"] == 1
        and inspection["events"] == 1
        and inspection["objectRefs"] == 1
        and inspection["terminalTasks"] == 0
        and Path(value["restore"]["targetRoot"]) == restored
        and repository.is_dir()
    )
    return {
        "accepted": accepted,
        "backupDatabaseDigestBefore": before,
        "backupDatabaseDigestAfter": after,
        "manifestSchemaVersion": manifest["hostJournalSchemaVersion"],
        "restoredInspection": inspection,
    }


def _initialize(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with HostStorage(root):
        pass


def _populate(root: Path, *, task_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    token = task_id.removeprefix("task:")
    with HostStorage(root) as storage:
        storage.record_task_event(
            event_id=f"event:{token}:create",
            kind=EventKind.TASK_CREATED,
            payload={"faultFixture": True},
            projection=TaskProjection(
                task_id=task_id,
                goal_id=f"goal:{token}",
                state=TaskState.READY,
                active_node_id=None,
                ready_frontier=(f"node:{token}:work",),
                revision=1,
                updated_at_ms=1,
            ),
            expected_revision=0,
        )


def _run(code: str, *arguments: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *(str(value) for value in arguments)],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_python_env(),
        timeout=120,
    )


def _python_env() -> dict[str, str]:
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    protocol = Path("/root/projects/ordivon-computing/packages/ordivon-protocol/src")
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(root / "src"), str(protocol), inherited) if value
    )
    return env


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1_000))


if __name__ == "__main__":
    main()
