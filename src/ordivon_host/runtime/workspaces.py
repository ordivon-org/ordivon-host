from __future__ import annotations

from anc_canonical import JsonValue, validate_json_value

from .client import RuntimeClient
from .errors import RuntimeProtocolError, RuntimeToolRejected


def is_missing_workspace(error: RuntimeToolRejected) -> bool:
    detail = error.detail
    return (
        detail.code == "INVALID_REQUEST"
        and detail.field == "workspaceId"
        and detail.commit_state == "not_committed"
    )


def ensure_workspace(
    runtime: RuntimeClient,
    *,
    workspace_id: str,
    source_repo: str,
    source_revision: str,
) -> dict[str, JsonValue]:
    try:
        workspace = runtime.call_tool(
            "workspace.get",
            {"schemaVersion": 1, "workspaceId": workspace_id},
        )
    except RuntimeToolRejected as error:
        if not is_missing_workspace(error):
            raise
        workspace = runtime.call_tool(
            "workspace.open",
            {
                "schemaVersion": 1,
                "sourceRepo": source_repo,
                "sourceRevision": source_revision,
                "workspaceId": workspace_id,
            },
        )
    if workspace.get("workspaceId") != workspace_id:
        raise RuntimeProtocolError("Runtime returned another Workspace")
    if workspace.get("sourceRevision") != source_revision:
        raise RuntimeProtocolError("Runtime returned another source revision")
    return _json_object(workspace, "Runtime Workspace")


def ensure_workspace_closed(
    runtime: RuntimeClient,
    workspace_id: str,
    *,
    force: bool = True,
) -> dict[str, JsonValue]:
    try:
        runtime.call_tool(
            "workspace.get",
            {"schemaVersion": 1, "workspaceId": workspace_id},
        )
    except RuntimeToolRejected as error:
        if is_missing_workspace(error):
            return {"workspaceId": workspace_id, "alreadyAbsent": True}
        raise
    try:
        closed = runtime.call_tool(
            "workspace.close",
            {
                "schemaVersion": 1,
                "workspaceId": workspace_id,
                "force": force,
            },
        )
    except RuntimeToolRejected as error:
        if is_missing_workspace(error):
            return {"workspaceId": workspace_id, "alreadyAbsent": True}
        raise
    if closed.get("workspaceId") != workspace_id:
        raise RuntimeProtocolError("workspace.close returned another Workspace")
    return _json_object(closed, "Runtime Workspace close")


def _json_object(value: dict[str, object], label: str) -> dict[str, JsonValue]:
    try:
        validate_json_value(value)
    except (TypeError, ValueError) as error:
        raise RuntimeProtocolError(f"{label} contains unsupported data") from error
    return value  # type: ignore[return-value]
