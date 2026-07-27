from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time

from ..config import HostConfig, read_token_file
from ..journal.migrations import schema_version
from ..runtime import McpRuntimeClient
from ..storage import HostStorage
from .gc import plan_gc


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def doctor_state(
    root: str | Path,
    *,
    config: HostConfig | None = None,
    check_runtime: bool = False,
    now_ms: int | None = None,
) -> dict[str, object]:
    state_root = Path(root)
    checks: list[DoctorCheck] = []
    database = state_root / "host.sqlite3"
    if not database.exists():
        checks.append(DoctorCheck("state", "error", "host.sqlite3 is missing"))
        return _result(state_root, checks)
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            quick = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        finally:
            connection.close()
        checks.append(
            DoctorCheck(
                "sqlite.quick_check",
                "ok" if quick == ("ok",) else "error",
                repr(quick),
            )
        )
    except sqlite3.Error as error:
        checks.append(DoctorCheck("sqlite.quick_check", "error", str(error)))
    try:
        with HostStorage(state_root) as storage:
            checks.append(
                DoctorCheck(
                    "journal.schema",
                    "ok",
                    str(schema_version(storage.journal.connection)),
                )
            )
            storage.journal.validate_invariants()
            checks.append(DoctorCheck("journal.invariants", "ok", "valid"))
            storage.validate_references()
            checks.append(DoctorCheck("cas.references", "ok", "valid"))
            gc_plan = plan_gc(state_root, storage=storage)
            orphans = gc_plan["orphanedObjects"]
            checks.append(
                DoctorCheck(
                    "cas.orphans",
                    "warning" if orphans else "ok",
                    str(len(orphans)),
                )
            )
            current = int(time.time() * 1_000) if now_ms is None else now_ms
            rows = storage.journal.connection.execute(
                "SELECT expires_at_ms FROM leases"
            ).fetchall()
            active = sum(int(row[0]) > current for row in rows)
            expired = len(rows) - active
            checks.append(
                DoctorCheck(
                    "journal.leases",
                    "warning" if expired else "ok",
                    f"active={active}, expired={expired}",
                )
            )
    except BaseException as error:
        checks.append(
            DoctorCheck("host.open", "error", f"{type(error).__name__}: {error}")
        )
    if check_runtime:
        checks.append(_runtime_check(config))
    return _result(state_root, checks)


def _runtime_check(config: HostConfig | None) -> DoctorCheck:
    if config is None:
        return DoctorCheck("runtime", "error", "Host config is required")
    try:
        token = read_token_file(config.runtime.token_file)
        client = McpRuntimeClient(
            config.runtime.endpoint,
            token,
            timeout_seconds=config.runtime.timeout_seconds,
            max_response_bytes=config.runtime.max_response_bytes,
            client_version="0.1.0",
        )
        initialized = client.initialize()
        return DoctorCheck(
            "runtime",
            "ok",
            json.dumps(initialized.get("serverInfo"), sort_keys=True),
        )
    except BaseException as error:
        return DoctorCheck("runtime", "error", f"{type(error).__name__}: {error}")


def _result(state_root: Path, checks: list[DoctorCheck]) -> dict[str, object]:
    return {
        "stateRoot": str(state_root),
        "healthy": not any(check.status == "error" for check in checks),
        "checks": [check.to_dict() for check in checks],
    }
