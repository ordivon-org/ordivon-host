from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import sqlite3
import stat
import time

from ..board import HostMessageBoard
from ..news import HostDailyNews
from ..journal.migrations import schema_version
from ..storage import HostStorage
from .gc import plan_gc
from .history import validate_history


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
    check_history: bool = False,
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
    permission_paths = [
        (state_root, 0o700),
        (state_root / "objects", 0o700),
        (database, 0o600),
    ]
    permission_paths.extend(
        (path, 0o600) for path in (state_root / "objects").glob("*.json")
    )
    insecure_before = [
        f"{path.name}:{stat.S_IMODE(path.stat().st_mode):04o}"
        for path, expected in permission_paths
        if path.exists() and stat.S_IMODE(path.stat().st_mode) != expected
    ]
    for path, expected in permission_paths:
        if path.exists() and not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) != expected:
            os.chmod(path, expected)
    try:
        with HostStorage(
            state_root, validation_mode="full", update_validation_cache=False
        ) as storage:
            checks.append(
                DoctorCheck(
                    "state.permissions",
                    "warning" if insecure_before else "ok",
                    (
                        "hardened on open: " + ", ".join(insecure_before)
                        if insecure_before
                        else "private"
                    ),
                )
            )
            checks.append(
                DoctorCheck(
                    "journal.schema",
                    "ok",
                    str(schema_version(storage.journal.connection)),
                )
            )
            storage.journal.validate_invariants()
            checks.append(DoctorCheck("journal.invariants", "ok", "valid"))
            try:
                board_messages = HostMessageBoard(storage).validate_integrity()
            except BaseException as error:
                checks.append(
                    DoctorCheck(
                        "board.integrity",
                        "error",
                        f"{type(error).__name__}: {error}",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "board.integrity",
                        "ok",
                        f"validated={board_messages}",
                    )
                )
            try:
                news_publications = HostDailyNews(storage).validate_integrity()
            except BaseException as error:
                checks.append(
                    DoctorCheck(
                        "news.integrity",
                        "error",
                        f"{type(error).__name__}: {error}",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "news.integrity",
                        "ok",
                        f"validated={news_publications}",
                    )
                )
            validation = storage.validation_summary
            checks.append(
                DoctorCheck(
                    "cas.references",
                    "ok",
                    f"full={validation.full}, hashed={validation.hashed_objects}, "
                    f"cached={validation.cached_objects}",
                )
            )
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
            if check_history:
                try:
                    history = validate_history(storage)
                except BaseException as error:
                    checks.append(
                        DoctorCheck(
                            "journal.history",
                            "error",
                            f"{type(error).__name__}: {error}",
                        )
                    )
                else:
                    checks.append(
                        DoctorCheck(
                            "journal.history",
                            "ok",
                            json.dumps(history.to_dict(), sort_keys=True),
                        )
                    )
    except BaseException as error:
        checks.append(
            DoctorCheck("host.open", "error", f"{type(error).__name__}: {error}")
        )
    return _result(state_root, checks)


def _result(state_root: Path, checks: list[DoctorCheck]) -> dict[str, object]:
    return {
        "stateRoot": str(state_root),
        "healthy": not any(check.status == "error" for check in checks),
        "checks": [check.to_dict() for check in checks],
    }
