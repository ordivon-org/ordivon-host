from __future__ import annotations

from .errors import RuntimeToolRejected


def is_missing_workspace(error: RuntimeToolRejected) -> bool:
    detail = error.detail
    return (
        detail.code == "INVALID_REQUEST"
        and detail.field == "workspaceId"
        and detail.commit_state == "not_committed"
    )
