from __future__ import annotations

from pathlib import Path

from ..storage import HostStorage


def plan_gc(
    state_root: str | Path,
    *,
    storage: HostStorage | None = None,
) -> dict[str, object]:
    root = Path(state_root)
    owns_storage = storage is None
    current = HostStorage(root) if storage is None else storage
    try:
        referenced = {ref.digest[7:] + ".json" for ref in current.journal.object_refs()}
        present = {
            path.name
            for path in current.objects.root.glob("*.json")
            if path.is_file()
        }
        return {
            "stateRoot": str(root),
            "referencedObjects": len(referenced),
            "presentObjects": len(present),
            "orphanedObjects": sorted(present - referenced),
            "missingObjects": sorted(referenced - present),
            "deleteAllowed": False,
        }
    finally:
        if owns_storage:
            current.close()
