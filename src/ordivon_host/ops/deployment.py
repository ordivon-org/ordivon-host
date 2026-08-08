from __future__ import annotations

import re
import stat
from pathlib import Path

DEFAULT_HOST_RELEASE_ROOT = Path("/usr/local/libexec/ordivon/host")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def inspect_deployment(
    release_root: str | Path = DEFAULT_HOST_RELEASE_ROOT,
) -> dict[str, object]:
    """Project the exact installed Host release without consulting Git or Host authority state."""
    root = Path(release_root)
    current = root / "current"
    if not current.is_symlink():
        raise FileNotFoundError(f"Host current release symlink is missing: {current}")
    release = current.resolve(strict=True)
    releases = (root / "releases").resolve(strict=True)
    if release.parent != releases:
        raise ValueError("Host current release escapes the releases directory")
    commit_file = release / "COMMIT"
    metadata = commit_file.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Host release COMMIT must be a regular file")
    revision = commit_file.read_text(encoding="utf-8").strip()
    if _COMMIT.fullmatch(revision) is None:
        raise ValueError("Host release COMMIT is not an exact Git revision")
    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-deployment",
        "releaseRoot": str(root),
        "currentLink": str(current),
        "currentRelease": str(release),
        "releaseId": release.name,
        "deployedRevision": revision,
        "commitFile": str(commit_file),
    }
