from __future__ import annotations

from dataclasses import dataclass
from anc_canonical import JsonValue, validate_json_value
from anc_effect_binding import EffectBinding
from anc_effect_ir import EffectEnvelope, effect_digest

from ..domain import EventKind, TaskProjection
from ..journal import JournalCorruption
from ..objects import ObjectCorrupt
from ..storage import HostStorage

_EVENT_FIELDS = {
    "schemaVersion",
    "kind",
    "eventKind",
    "data",
    "projection",
}
_CAS_DIGEST_KEYS = frozenset(
    {
        "planDigest",
        "catalogObjectDigest",
        "effectDigest",
        "bindingDigest",
        "authorityDecisionDigest",
        "dispatchDigest",
        "requestObjectDigest",
        "observationDigest",
        "readObservationDigest",
        "verificationDigest",
        "diffObjectDigest",
        "outcomeDigest",
        "childOutcomeDigest",
        "contextObjectDigest",
        "decisionObjectDigest",
        "admissionObjectDigest",
        "intentObjectDigest",
        "observationObjectDigest",
        "invocationReceiptDigest",
        "proposalObjectDigest",
        "resolutionObjectDigest",
        "outputObservationDigest",
    }
)


@dataclass(frozen=True, slots=True)
class HistoryValidation:
    events: int
    task_streams: int
    semantic_references: int
    semantic_link_checks: int

    def to_dict(self) -> dict[str, int]:
        return {
            "events": self.events,
            "taskStreams": self.task_streams,
            "semanticReferences": self.semantic_references,
            "semanticLinkChecks": self.semantic_link_checks,
        }


def validate_history(storage: HostStorage) -> HistoryValidation:
    """Validate every historical Event payload and known semantic cross-link."""
    admitted = {item.digest for item in storage.journal.object_refs()}
    rows = storage.journal.connection.execute(
        "SELECT event_id, stream_id, stream_kind, stream_revision, event_kind, "
        "payload_digest, recorded_at_ms FROM events ORDER BY sequence"
    )
    events = 0
    task_streams: set[str] = set()
    semantic_references = 0
    semantic_link_checks = 0
    for row in rows:
        events += 1
        event_id = str(row["event_id"])
        stream_id = str(row["stream_id"])
        if row["stream_kind"] != "task":
            raise JournalCorruption(
                f"unsupported historical stream kind at {event_id}: {row['stream_kind']}"
            )
        task_streams.add(stream_id)
        value = storage.objects.get(
            str(row["payload_digest"]), expected_kind="host-event-payload"
        )
        if not isinstance(value, dict) or set(value) != _EVENT_FIELDS:
            raise ObjectCorrupt(f"historical Event payload fields differ: {event_id}")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.host-task-event":
            raise ObjectCorrupt(f"historical Event payload version differs: {event_id}")
        try:
            event_kind = EventKind(str(value["eventKind"]))
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical Event kind is invalid: {event_id}"
            ) from error
        if event_kind.value != row["event_kind"]:
            raise JournalCorruption(
                f"historical Event kind differs from row: {event_id}"
            )
        raw_projection = value["projection"]
        if not isinstance(raw_projection, dict):
            raise ObjectCorrupt(
                f"historical Event projection is not an object: {event_id}"
            )
        try:
            projection = TaskProjection.from_dict(raw_projection)
        except (TypeError, ValueError) as error:
            raise ObjectCorrupt(
                f"historical Event projection is invalid: {event_id}"
            ) from error
        if (
            projection.task_id != stream_id
            or projection.revision != int(row["stream_revision"])
            or projection.updated_at_ms != int(row["recorded_at_ms"])
        ):
            raise JournalCorruption(
                f"historical Event projection differs from row: {event_id}"
            )
        data = value["data"]
        validate_json_value(data)
        references = _known_references(data)
        semantic_references += len(references)
        for key, digest in references:
            if digest not in admitted:
                raise JournalCorruption(
                    f"historical {key} is not admitted in object_refs: {event_id}"
                )
        semantic_link_checks += _validate_semantic_links(storage, data, event_id)
    return HistoryValidation(
        events=events,
        task_streams=len(task_streams),
        semantic_references=semantic_references,
        semantic_link_checks=semantic_link_checks,
    )


def _known_references(value: JsonValue) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []

    def visit(current: JsonValue) -> None:
        if isinstance(current, dict):
            for key, item in current.items():
                if key in _CAS_DIGEST_KEYS and isinstance(item, str):
                    found.append((key, item))
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(value)
    return tuple(found)


def _validate_semantic_links(
    storage: HostStorage,
    data: JsonValue,
    event_id: str,
) -> int:
    if not isinstance(data, dict):
        return 0
    effect_key = data.get("effectDigest")
    binding_key = data.get("bindingDigest")
    authority_key = data.get("authorityDecisionDigest")
    checks = 0
    effect: EffectEnvelope | None = None
    if isinstance(effect_key, str):
        raw_effect = storage.objects.get(effect_key, expected_kind="effect")
        if not isinstance(raw_effect, dict):
            raise ObjectCorrupt(f"historical Effect is not an object: {event_id}")
        try:
            effect = EffectEnvelope.from_dict(raw_effect)
        except ValueError as error:
            raise ObjectCorrupt(f"historical Effect is invalid: {event_id}") from error
        checks += 1
    if isinstance(binding_key, str):
        raw_binding = storage.objects.get(
            binding_key, expected_kind="effect-binding"
        )
        if not isinstance(raw_binding, dict):
            raise ObjectCorrupt(f"historical Binding is not an object: {event_id}")
        try:
            binding = EffectBinding.from_dict(raw_binding)
        except ValueError as error:
            raise ObjectCorrupt(f"historical Binding is invalid: {event_id}") from error
        if effect is not None and (
            binding.effect_id != effect.effect_id
            or binding.effect_digest != effect_digest(effect)
        ):
            raise JournalCorruption(
                f"historical Effect and Binding identities differ: {event_id}"
            )
        checks += 1
    if isinstance(authority_key, str):
        authority = storage.objects.get(
            authority_key, expected_kind="capability-decision"
        )
        if not isinstance(authority, dict):
            raise ObjectCorrupt(
                f"historical CapabilityDecision is not an object: {event_id}"
            )
        expected = {
            "schemaVersion",
            "kind",
            "principalId",
            "actionId",
            "objectScope",
            "policyId",
            "allowed",
            "reason",
        }
        if (
            set(authority) != expected
            or authority.get("schemaVersion") != 1
            or authority.get("kind") != "ordivon.capability-decision"
            or authority.get("allowed") is not True
        ):
            raise ObjectCorrupt(
                f"historical CapabilityDecision is invalid: {event_id}"
            )
        if effect is not None and (
            authority.get("principalId") != effect.capability.principal_id
            or authority.get("actionId") != effect.capability.action_id
            or authority.get("objectScope") != effect.capability.object_scope
        ):
            raise JournalCorruption(
                f"historical Authority and Effect identities differ: {event_id}"
            )
        checks += 1
    return checks
