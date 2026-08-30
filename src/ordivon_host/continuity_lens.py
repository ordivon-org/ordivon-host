from __future__ import annotations

from collections import Counter
import re

from anc_canonical import canonical_bytes

from .continuity import ExternalContinuityHost
from .continuity_models import EXTERNAL_CONTINUITY_WORKLOAD_ID
from .domain import TaskState
from .ops.inspect import list_tasks
from .storage import HostStorage

_ATTENTION_PATTERN = re.compile(
    r"^ATTENTION=([A-Z][A-Z0-9_-]{0,63})(?=[ ./]|$)"
)
_HINT_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{0,63}$")
_CARRIER_HINTS = frozenset({"CLOSE_CLEAN", "RETAIN", "DIRTY_HANDOFF"})
_UNSPECIFIED = "UNSPECIFIED"
_MAX_SCAN_TASKS = 10_000
_MAX_ITEM_LIMIT = 200


def build_continuity_lens(
    storage: HostStorage,
    *,
    goal_id: str | None = None,
    attention: str | None = None,
    carrier: str | None = None,
    item_limit: int = 20,
) -> dict[str, object]:
    """Derive a compact, non-authoritative external-continuity navigation view."""

    if type(item_limit) is not int or not 0 <= item_limit <= _MAX_ITEM_LIMIT:
        raise ValueError(f"continuity lens item limit must be in [0, {_MAX_ITEM_LIMIT}]")
    attention = _attention_filter(attention)
    carrier = _carrier_filter(carrier)

    host = ExternalContinuityHost(storage, clock_ms=lambda: 0)
    rows: list[dict[str, object]] = []
    for projection in list_tasks(
        storage,
        state=TaskState.READY,
        goal_id=goal_id,
        limit=_MAX_SCAN_TASKS,
    ):
        descriptor = storage.read_task_descriptor(projection.task_id)
        if (
            descriptor is None
            or descriptor.workload_id != EXTERNAL_CONTINUITY_WORKLOAD_ID
        ):
            continue
        record = host.checkpoint_at_revision(
            projection.task_id, projection.revision
        )
        checkpoint = None if record is None else record.checkpoint
        hints = _frontier_hints(
            "" if checkpoint is None else checkpoint.frontier
        )
        objective_preview, objective_truncated = _preview(
            "" if checkpoint is None else checkpoint.objective, 192
        )
        frontier_preview, frontier_truncated = _preview(
            "" if checkpoint is None else checkpoint.frontier, 256
        )
        runtime_workspace_id = (
            None
            if checkpoint is None or checkpoint.runtime is None
            else checkpoint.runtime.workspace_id
        )
        rows.append(
            {
                "taskId": projection.task_id,
                "goalId": projection.goal_id,
                "revision": projection.revision,
                "updatedAtMs": projection.updated_at_ms,
                "checkpointRevision": (
                    None if record is None else record.task_revision
                ),
                "checkpointDigest": (
                    None if record is None else record.checkpoint_digest
                ),
                "checkpointCanonicalBytes": (
                    None
                    if checkpoint is None
                    else len(canonical_bytes(checkpoint.to_dict()))
                ),
                "checkpointItemCount": (
                    None
                    if checkpoint is None
                    else sum(
                        len(values)
                        for values in (
                            checkpoint.established,
                            checkpoint.unresolved,
                            checkpoint.rejected,
                            checkpoint.constraints,
                            checkpoint.next_actions,
                        )
                    )
                ),
                "attentionHint": hints["attentionHint"],
                "carrierHint": hints["carrierHint"],
                "wakeHint": hints["wakeHint"],
                "hintIssues": hints["hintIssues"],
                "runtimeWorkspaceId": runtime_workspace_id,
                "objectivePreview": objective_preview,
                "objectiveTruncated": objective_truncated,
                "frontierPreview": frontier_preview,
                "frontierTruncated": frontier_truncated,
            }
        )

    attention_counts = Counter(str(row["attentionHint"]) for row in rows)
    carrier_counts = Counter(str(row["carrierHint"]) for row in rows)
    goal_counts = Counter(str(row["goalId"]) for row in rows)
    matching = [
        row
        for row in rows
        if (attention is None or row["attentionHint"] == attention)
        and (carrier is None or row["carrierHint"] == carrier)
    ]
    visible = matching[:item_limit]
    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-continuity-lens",
        "scope": "active-external-continuity",
        "truthRole": "derived-non-authoritative-continuity-navigation",
        "filters": {
            "goalId": goal_id,
            "attentionHint": attention,
            "carrierHint": carrier,
        },
        "summary": {
            "scopeCount": len(rows),
            "matchingCount": len(matching),
            "byGoal": _counts(goal_counts, "goalId"),
            "byAttention": _counts(attention_counts, "attentionHint"),
            "byCarrier": _counts(carrier_counts, "carrierHint"),
        },
        "items": visible,
        "itemLimit": item_limit,
        "hasMore": len(matching) > len(visible),
        "order": (
            "current Host updatedAtMs descending with taskId tie-break; "
            "navigation order only, never priority"
        ),
        "hintConvention": {
            "attention": "frontier first line ATTENTION=<TOKEN>",
            "carrier": (
                "frontier line CARRIER=CLOSE_CLEAN|RETAIN|DIRTY_HANDOFF"
            ),
            "wake": "frontier line WAKE=<opaque owner-authored condition>",
            "interpretation": (
                "checkpoint-authored hints only; Host does not infer admission, "
                "priority, Runtime currentness, or physical disposition"
            ),
        },
        "truthBoundary": (
            "derived from exact per-item Host Task revision/checkpoint identities; "
            "not a frozen global world, scheduler, priority, owner standing, domain "
            "truth, Runtime currentness, or Workspace close authorization"
        ),
    }


def _frontier_hints(frontier: str) -> dict[str, object]:
    lines = frontier.splitlines()
    first = lines[0] if lines else ""
    match = _ATTENTION_PATTERN.match(first)
    attention = match.group(1) if match is not None else _UNSPECIFIED
    carrier = _UNSPECIFIED
    wake: str | None = None
    issues: list[str] = []
    for line in lines[:8]:
        if line.startswith("CARRIER="):
            value = line.removeprefix("CARRIER=")
            if carrier != _UNSPECIFIED:
                issues.append("duplicate-carrier")
            elif value in _CARRIER_HINTS:
                carrier = value
            else:
                issues.append("invalid-carrier")
        elif line.startswith("WAKE="):
            value = line.removeprefix("WAKE=")
            if wake is not None:
                issues.append("duplicate-wake")
            elif value and value == value.strip():
                wake = _preview(value, 256)[0]
            else:
                issues.append("invalid-wake")
    return {
        "attentionHint": attention,
        "carrierHint": carrier,
        "wakeHint": wake,
        "hintIssues": issues,
    }


def _attention_filter(value: str | None) -> str | None:
    if value is None:
        return None
    if value == _UNSPECIFIED or _HINT_TOKEN_PATTERN.fullmatch(value):
        return value
    raise ValueError(
        "continuity lens attention filter must be UNSPECIFIED or an uppercase token"
    )


def _carrier_filter(value: str | None) -> str | None:
    if value is None:
        return None
    if value in {*_CARRIER_HINTS, _UNSPECIFIED}:
        return value
    raise ValueError(
        "continuity lens carrier filter must be CLOSE_CLEAN, RETAIN, "
        "DIRTY_HANDOFF, or UNSPECIFIED"
    )


def _preview(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _counts(
    values: Counter[str], label: str
) -> list[dict[str, object]]:
    return [
        {label: value, "count": count}
        for value, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ]
