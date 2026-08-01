from __future__ import annotations

from dataclasses import dataclass
from anc_canonical import JsonValue, validate_json_value
from anc_effect_binding import EffectBinding
from anc_effect_ir import EffectEnvelope, effect_digest

from ..domain import EventKind, TaskProjection
from ..harness.contracts import NativeHarnessRunContract, ToolGrant
from ..harness.disposition import (
    CompletionRoute,
    NativeRunFacts,
    NativeRunPhase,
    derive_native_run_disposition,
    recovery_unknowns,
)
from ..harness.models import HarnessAssignment, HarnessRunReceipt
from ..harness.recovery import NativeRunAbandonment, NativeRunRecoveryAssessment
from ..harness.tool_semantics import (
    NativeToolCatalogSnapshot,
    legacy_grant_recovery_consequence,
    recovery_consequence_from_persisted,
)
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
        "taskAttemptObjectDigest",
        "taskContractObjectDigest",
        "harnessManifestObjectDigest",
        "assignmentObjectDigest",
        "toolGrantObjectDigest",
        "toolCatalogObjectDigest",
        "nativeHarnessRunContractObjectDigest",
        "harnessRunObjectDigest",
        "harnessTraceObjectDigest",
        "runConclusionObjectDigest",
        "harnessRunRecoveryAssessmentObjectDigest",
        "harnessRunAbandonmentObjectDigest",
        "completionProposalObjectDigest",
        "completionVerificationObjectDigest",
        "completionDecisionObjectDigest",
        "outcomeObjectDigest",
    }
)
_CAS_DIGEST_LIST_KEYS = frozenset({"toolObservationObjectDigests"})


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
                if key in _CAS_DIGEST_LIST_KEYS and isinstance(item, list):
                    for digest in item:
                        if isinstance(digest, str):
                            found.append((key, digest))
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
    assignment: HarnessAssignment | None = None
    tool_grant: ToolGrant | None = None
    tool_catalog: NativeToolCatalogSnapshot | None = None
    native_contract: NativeHarnessRunContract | None = None
    assignment_object_key = data.get("assignmentObjectDigest")
    native_object_key = data.get("nativeHarnessRunContractObjectDigest")
    if isinstance(assignment_object_key, str):
        raw_assignment = storage.objects.get(
            assignment_object_key, expected_kind="harness-assignment"
        )
        if not isinstance(raw_assignment, dict):
            raise ObjectCorrupt(
                f"historical Harness Assignment is not an object: {event_id}"
            )
        try:
            assignment = HarnessAssignment.from_dict(raw_assignment)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical Harness Assignment is invalid: {event_id}"
            ) from error
        if data.get("assignmentDigest") != assignment.digest:
            raise JournalCorruption(
                f"historical Harness Assignment digest differs: {event_id}"
            )
        checks += 1
    if isinstance(native_object_key, str):
        if assignment is None:
            raise JournalCorruption(
                f"historical native Run Contract has no Assignment: {event_id}"
            )
        raw_native = storage.objects.get(
            native_object_key, expected_kind="native-harness-run-contract"
        )
        if not isinstance(raw_native, dict):
            raise ObjectCorrupt(
                f"historical native Run Contract is not an object: {event_id}"
            )
        try:
            native_contract = NativeHarnessRunContract.from_dict(raw_native)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical native Run Contract is invalid: {event_id}"
            ) from error
        grant_object_key = data.get("toolGrantObjectDigest")
        if not isinstance(grant_object_key, str):
            raise JournalCorruption(
                f"historical native Run Contract has no Tool Grant: {event_id}"
            )
        raw_grant = storage.objects.get(grant_object_key, expected_kind="tool-grant")
        if not isinstance(raw_grant, dict):
            raise ObjectCorrupt(f"historical Tool Grant is not an object: {event_id}")
        try:
            tool_grant = ToolGrant.from_dict(raw_grant)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical Tool Grant is invalid: {event_id}"
            ) from error
        catalog_object_key = data.get("toolCatalogObjectDigest")
        if native_contract.tool_catalog_object_digest is None:
            if catalog_object_key is not None:
                raise JournalCorruption(
                    f"historical v1 native Run unexpectedly references a catalog: {event_id}"
                )
            try:
                legacy_grant_recovery_consequence(tool_grant.allowed_tools)
            except ValueError as error:
                raise JournalCorruption(
                    f"historical legacy Tool Grant is not closed: {event_id}"
                ) from error
        else:
            if (
                not isinstance(catalog_object_key, str)
                or catalog_object_key != native_contract.tool_catalog_object_digest
            ):
                raise JournalCorruption(
                    f"historical native Tool catalog reference differs: {event_id}"
                )
            raw_catalog = storage.objects.get(
                catalog_object_key, expected_kind="harness-runtime-catalog"
            )
            if not isinstance(raw_catalog, dict):
                raise ObjectCorrupt(
                    f"historical native Tool catalog is not an object: {event_id}"
                )
            try:
                tool_catalog = NativeToolCatalogSnapshot.from_dict(raw_catalog)
            except ValueError as error:
                raise ObjectCorrupt(
                    f"historical native Tool catalog is invalid: {event_id}"
                ) from error
            if tool_catalog.digest != assignment.tool_catalog_digest:
                raise JournalCorruption(
                    f"historical native Tool catalog digest differs: {event_id}"
                )
            try:
                tool_catalog.aggregate_recovery_consequence(tool_grant.allowed_tools)
            except KeyError as error:
                raise JournalCorruption(
                    f"historical Tool Grant is not covered by its catalog: {event_id}"
                ) from error
        if (
            data.get("nativeHarnessRunContractDigest") != native_contract.digest
            or data.get("harnessRunId") != native_contract.harness_run_id
            or native_contract.assignment_id != assignment.assignment_id
            or native_contract.assignment_generation != assignment.generation
            or native_contract.assignment_digest != assignment.digest
            or native_contract.harness_manifest_digest
            != assignment.harness_manifest_digest
            or native_contract.context_object_digest != assignment.context_object_digest
            or native_contract.task_contract_digest != data.get("taskContractDigest")
            or native_contract.task_contract_object_digest
            != data.get("taskContractObjectDigest")
            or native_contract.tool_catalog_digest != assignment.tool_catalog_digest
            or native_contract.tool_grant_digest != tool_grant.digest
            or data.get("toolGrantDigest") != tool_grant.digest
            or native_contract.tool_grant_object_digest != grant_object_key
        ):
            raise JournalCorruption(
                f"historical native Run Contract identities differ: {event_id}"
            )
        checks += 3 if tool_catalog is not None else 2
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
        raw_binding = storage.objects.get(binding_key, expected_kind="effect-binding")
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
    recovery: NativeRunRecoveryAssessment | None = None
    recovery_object_key = data.get("harnessRunRecoveryAssessmentObjectDigest")
    if isinstance(recovery_object_key, str):
        raw_recovery = storage.objects.get(
            recovery_object_key, expected_kind="native-run-recovery-assessment"
        )
        if not isinstance(raw_recovery, dict):
            raise ObjectCorrupt(
                f"historical native Run Recovery is not an object: {event_id}"
            )
        try:
            recovery = NativeRunRecoveryAssessment.from_dict(raw_recovery)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical native Run Recovery is invalid: {event_id}"
            ) from error
        if (
            data.get("harnessRunRecoveryAssessmentDigest") != recovery.digest
            or data.get("harnessRunRecoveryAssessmentId") != recovery.assessment_id
            or data.get("harnessRunRecoverySafeToAbandon")
            is not recovery.safe_to_abandon
        ):
            raise JournalCorruption(
                f"historical native Run Recovery identities differ: {event_id}"
            )
        if tool_grant is None:
            raise JournalCorruption(
                f"historical native Run Recovery has no Tool Grant: {event_id}"
            )
        expected_consequence = (
            tool_catalog.aggregate_recovery_consequence(tool_grant.allowed_tools)
            if tool_catalog is not None
            else legacy_grant_recovery_consequence(tool_grant.allowed_tools)
        )
        if (
            recovery_consequence_from_persisted(recovery.grant_effect_class)
            is not expected_consequence
            or recovery.unresolved_unknowns
            != recovery_unknowns(
                expected_consequence,
                workspace_status=recovery.workspace_status,
            )
        ):
            raise JournalCorruption(
                f"historical native Run Recovery semantics differ: {event_id}"
            )
        disposition = derive_native_run_disposition(
            NativeRunFacts(
                NativeRunPhase.RECOVERY_RECORDED,
                expected_consequence,
                recovery_safe_to_abandon=recovery.safe_to_abandon,
                unresolved_unknowns=recovery.unresolved_unknowns,
            )
        )
        if disposition.abandonment_allowed is not recovery.safe_to_abandon:
            raise JournalCorruption(
                f"historical native Run Recovery disposition differs: {event_id}"
            )
        checks += 2
    run_object_key = data.get("harnessRunObjectDigest")
    if isinstance(run_object_key, str) and native_contract is not None:
        if assignment is None or tool_grant is None:
            raise JournalCorruption(
                f"historical native Harness Run has incomplete Assignment: {event_id}"
            )
        raw_run = storage.objects.get(
            run_object_key, expected_kind="harness-run-receipt"
        )
        if not isinstance(raw_run, dict):
            raise ObjectCorrupt(
                f"historical native Harness Run is not an object: {event_id}"
            )
        try:
            receipt = HarnessRunReceipt.from_dict(raw_run)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical native Harness Run is invalid: {event_id}"
            ) from error
        observation_keys = data.get("toolObservationObjectDigests", [])
        if not isinstance(observation_keys, list) or any(
            not isinstance(item, str) for item in observation_keys
        ):
            raise JournalCorruption(
                f"historical native Harness Observation refs differ: {event_id}"
            )
        observations: list[dict[str, JsonValue]] = []
        for key in observation_keys:
            raw_observation = storage.objects.get(
                key, expected_kind="harness-tool-observation"
            )
            if not isinstance(raw_observation, dict):
                raise ObjectCorrupt(
                    f"historical Harness Observation is not an object: {event_id}"
                )
            observations.append(dict(raw_observation))
        if receipt.termination_code is None:
            raise JournalCorruption(
                f"historical native Harness Run omitted termination: {event_id}"
            )
        consequence = (
            tool_catalog.aggregate_recovery_consequence(tool_grant.allowed_tools)
            if tool_catalog is not None
            else legacy_grant_recovery_consequence(tool_grant.allowed_tools)
        )
        disposition = derive_native_run_disposition(
            NativeRunFacts(
                NativeRunPhase.RUN_RECORDED,
                consequence,
                termination_code=receipt.termination_code,
                has_tool_observations=bool(observations),
                has_unknown_observation=any(
                    item.get("status") == "unknown" for item in observations
                ),
                has_candidate_conclusion=(
                    receipt.termination_code == "candidate_completed"
                ),
            )
        )
        if (
            disposition.completion_route is CompletionRoute.RECONCILE_UNKNOWN
            and receipt.termination_code != "runtime_unknown"
        ):
            raise JournalCorruption(
                f"historical native Harness Run UNKNOWN termination differs: {event_id}"
            )
        if (
            data.get("harnessRunDigest") != receipt.digest
            or data.get("harnessRunId") != receipt.harness_run_id
            or data.get("harnessRunTerminationCode") != receipt.termination_code
            or data.get("harnessRunReplacementAllowed")
            is not disposition.replacement_allowed
            or receipt.assignment_id != assignment.assignment_id
            or receipt.assignment_generation != assignment.generation
            or receipt.tool_catalog_digest != assignment.tool_catalog_digest
        ):
            raise JournalCorruption(
                f"historical native Harness Run projection differs: {event_id}"
            )
        checks += 2
    abandonment_object_key = data.get("harnessRunAbandonmentObjectDigest")
    if isinstance(abandonment_object_key, str):
        raw_abandonment = storage.objects.get(
            abandonment_object_key, expected_kind="native-run-abandonment"
        )
        if not isinstance(raw_abandonment, dict):
            raise ObjectCorrupt(
                f"historical native Run Abandonment is not an object: {event_id}"
            )
        try:
            abandonment = NativeRunAbandonment.from_dict(raw_abandonment)
        except ValueError as error:
            raise ObjectCorrupt(
                f"historical native Run Abandonment is invalid: {event_id}"
            ) from error
        if recovery is None:
            raise JournalCorruption(
                f"historical native Run Abandonment has no Recovery: {event_id}"
            )
        if (
            data.get("harnessRunAbandonmentDigest") != abandonment.digest
            or data.get("harnessRunAbandonmentId") != abandonment.abandonment_id
            or abandonment.recovery_assessment_digest != recovery.digest
            or abandonment.recovery_assessment_object_digest != recovery_object_key
            or abandonment.assignment_id != recovery.assignment_id
            or abandonment.assignment_generation != recovery.assignment_generation
            or abandonment.assignment_digest != recovery.assignment_digest
            or abandonment.harness_run_id != recovery.harness_run_id
            or abandonment.reason_code != recovery.trigger
        ):
            raise JournalCorruption(
                f"historical native Run Abandonment identities differ: {event_id}"
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
            raise ObjectCorrupt(f"historical CapabilityDecision is invalid: {event_id}")
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
