from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..effects import ArtifactRef, TaskOutcome


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def _identity(value: str, prefix: str, label: str) -> str:
    if not value.startswith(prefix + ":") or value != value.strip():
        raise ValueError(f"{label} identity must start with {prefix}:")
    if len(value.encode("utf-8")) > 300:
        raise ValueError(f"{label} identity exceeds 300 UTF-8 bytes")
    return value


def _digest(value: str, label: str) -> str:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _text(value: str, label: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-empty and trimmed")
    if len(value.encode("utf-8")) > 1_000:
        raise ValueError(f"{label} exceeds 1000 UTF-8 bytes")
    return value


def _unique_text(values: tuple[str, ...], label: str) -> None:
    for value in values:
        _text(value, label)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def _artifact_refs(values: tuple[ArtifactRef, ...], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        _text(value.ref, f"{label} ref")
        _text(value.kind, f"{label} kind")
        _digest(value.digest, f"{label} digest")
        if value.ref in seen:
            raise ValueError(f"{label} refs must be unique")
        seen.add(value.ref)


def _artifact_values(values: tuple[ArtifactRef, ...]) -> list[JsonValue]:
    return [value.to_dict() for value in values]


def _parse_artifacts(value: Any, label: str) -> tuple[ArtifactRef, ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    refs = tuple(ArtifactRef.from_dict(item) for item in value)
    _artifact_refs(refs, label)
    return refs


@dataclass(frozen=True, slots=True)
class HarnessCapabilityManifest:
    harness_id: str
    protocol: str
    protocol_revision: str
    persistent_session: bool
    session_resume: bool
    session_fork: bool
    interrupt: bool
    tool_events: bool
    approval_events: bool
    usage: bool
    images: bool
    compaction: bool
    checkpoint: bool
    local_subagents: bool
    extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.harness_id, "Harness")
        _text(self.protocol, "Harness protocol")
        _text(self.protocol_revision, "Harness protocol revision")
        _unique_text(self.extensions, "Harness extension")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def supported_capabilities(self) -> tuple[str, ...]:
        capabilities = [
            name
            for name, supported in (
                ("persistent_session", self.persistent_session),
                ("session_resume", self.session_resume),
                ("session_fork", self.session_fork),
                ("interrupt", self.interrupt),
                ("tool_events", self.tool_events),
                ("approval_events", self.approval_events),
                ("usage", self.usage),
                ("images", self.images),
                ("compaction", self.compaction),
                ("checkpoint", self.checkpoint),
                ("local_subagents", self.local_subagents),
            )
            if supported
        ]
        capabilities.extend(self.extensions)
        return tuple(capabilities)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-capability-manifest",
            "harnessId": self.harness_id,
            "protocol": self.protocol,
            "protocolRevision": self.protocol_revision,
            "persistentSession": self.persistent_session,
            "sessionResume": self.session_resume,
            "sessionFork": self.session_fork,
            "interrupt": self.interrupt,
            "toolEvents": self.tool_events,
            "approvalEvents": self.approval_events,
            "usage": self.usage,
            "images": self.images,
            "compaction": self.compaction,
            "checkpoint": self.checkpoint,
            "localSubagents": self.local_subagents,
            "extensions": list(self.extensions),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HarnessCapabilityManifest:
        expected = {
            "schemaVersion",
            "kind",
            "harnessId",
            "protocol",
            "protocolRevision",
            "persistentSession",
            "sessionResume",
            "sessionFork",
            "interrupt",
            "toolEvents",
            "approvalEvents",
            "usage",
            "images",
            "compaction",
            "checkpoint",
            "localSubagents",
            "extensions",
        }
        _exact(value, expected, "HarnessCapabilityManifest")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.harness-capability-manifest":
            raise ValueError("HarnessCapabilityManifest version or kind is invalid")
        strings = (value["harnessId"], value["protocol"], value["protocolRevision"])
        if any(not isinstance(item, str) for item in strings):
            raise ValueError("HarnessCapabilityManifest identity fields must be strings")
        boolean_fields = expected - {
            "schemaVersion",
            "kind",
            "harnessId",
            "protocol",
            "protocolRevision",
            "extensions",
        }
        if any(type(value[field]) is not bool for field in boolean_fields):
            raise ValueError("HarnessCapabilityManifest capability fields must be booleans")
        extensions = value["extensions"]
        if not isinstance(extensions, list) or any(not isinstance(item, str) for item in extensions):
            raise ValueError("HarnessCapabilityManifest extensions must be strings")
        return cls(
            harness_id=value["harnessId"],
            protocol=value["protocol"],
            protocol_revision=value["protocolRevision"],
            persistent_session=value["persistentSession"],
            session_resume=value["sessionResume"],
            session_fork=value["sessionFork"],
            interrupt=value["interrupt"],
            tool_events=value["toolEvents"],
            approval_events=value["approvalEvents"],
            usage=value["usage"],
            images=value["images"],
            compaction=value["compaction"],
            checkpoint=value["checkpoint"],
            local_subagents=value["localSubagents"],
            extensions=tuple(extensions),
        )


@dataclass(frozen=True, slots=True)
class TaskAttemptDescriptor:
    task_attempt_id: str
    task_id: str
    started_at_task_revision: int
    objective_digest: str
    acceptance_criteria_digest: str
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.task_attempt_id, "task-attempt", "Task Attempt")
        _identity(self.task_id, "task", "Task")
        if self.started_at_task_revision < 1:
            raise ValueError("Task Attempt start revision must be positive")
        _digest(self.objective_digest, "Task Attempt objective digest")
        _digest(self.acceptance_criteria_digest, "Task Attempt acceptance digest")
        if self.created_at_ms < 0:
            raise ValueError("Task Attempt creation time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.task-attempt-descriptor",
            "taskAttemptId": self.task_attempt_id,
            "taskId": self.task_id,
            "startedAtTaskRevision": self.started_at_task_revision,
            "objectiveDigest": self.objective_digest,
            "acceptanceCriteriaDigest": self.acceptance_criteria_digest,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskAttemptDescriptor:
        expected = {
            "schemaVersion",
            "kind",
            "taskAttemptId",
            "taskId",
            "startedAtTaskRevision",
            "objectiveDigest",
            "acceptanceCriteriaDigest",
            "createdAtMs",
        }
        _exact(value, expected, "TaskAttemptDescriptor")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.task-attempt-descriptor":
            raise ValueError("TaskAttemptDescriptor version or kind is invalid")
        if any(
            not isinstance(value[field], str)
            for field in ("taskAttemptId", "taskId", "objectiveDigest", "acceptanceCriteriaDigest")
        ):
            raise ValueError("TaskAttemptDescriptor identity fields must be strings")
        if type(value["startedAtTaskRevision"]) is not int or type(value["createdAtMs"]) is not int:
            raise ValueError("TaskAttemptDescriptor revision and time must be integers")
        return cls(
            task_attempt_id=value["taskAttemptId"],
            task_id=value["taskId"],
            started_at_task_revision=value["startedAtTaskRevision"],
            objective_digest=value["objectiveDigest"],
            acceptance_criteria_digest=value["acceptanceCriteriaDigest"],
            created_at_ms=value["createdAtMs"],
        )


@dataclass(frozen=True, slots=True)
class HarnessAssignment:
    assignment_id: str
    task_id: str
    task_revision: int
    task_attempt_id: str
    generation: int
    target_harness_id: str
    harness_manifest_digest: str
    context_object_digest: str
    acceptance_criteria_digest: str
    tool_catalog_digest: str
    workspace_ref: str | None
    source_ref: str | None
    source_digest: str | None
    prior_artifact_refs: tuple[ArtifactRef, ...]
    required_capabilities: tuple[str, ...]
    budget: dict[str, JsonValue]
    deadline_ms: int | None
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.assignment_id, "assignment", "Assignment")
        _identity(self.task_id, "task", "Task")
        _identity(self.task_attempt_id, "task-attempt", "Task Attempt")
        if self.task_revision < 1 or self.generation < 1:
            raise ValueError("Assignment revision and generation must be positive")
        _text(self.target_harness_id, "target Harness")
        _digest(self.harness_manifest_digest, "Harness manifest digest")
        _digest(self.context_object_digest, "Assignment Context object digest")
        _digest(self.acceptance_criteria_digest, "Assignment acceptance digest")
        _digest(self.tool_catalog_digest, "Assignment Tool catalog digest")
        for value, label in (
            (self.workspace_ref, "Workspace ref"),
            (self.source_ref, "source ref"),
        ):
            if value is not None:
                _text(value, label)
        if self.source_digest is not None:
            _digest(self.source_digest, "source digest")
            if self.source_ref is None:
                raise ValueError("source digest requires a source ref")
        _artifact_refs(self.prior_artifact_refs, "prior Artifact")
        _unique_text(self.required_capabilities, "required capability")
        validate_json_value(self.budget)
        if self.deadline_ms is not None and self.deadline_ms < self.created_at_ms:
            raise ValueError("Assignment deadline cannot precede creation")
        if self.created_at_ms < 0:
            raise ValueError("Assignment creation time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-assignment",
            "assignmentId": self.assignment_id,
            "taskId": self.task_id,
            "taskRevision": self.task_revision,
            "taskAttemptId": self.task_attempt_id,
            "generation": self.generation,
            "targetHarnessId": self.target_harness_id,
            "harnessManifestDigest": self.harness_manifest_digest,
            "contextObjectDigest": self.context_object_digest,
            "acceptanceCriteriaDigest": self.acceptance_criteria_digest,
            "toolCatalogDigest": self.tool_catalog_digest,
            "workspaceRef": self.workspace_ref,
            "sourceRef": self.source_ref,
            "sourceDigest": self.source_digest,
            "priorArtifactRefs": _artifact_values(self.prior_artifact_refs),
            "requiredCapabilities": list(self.required_capabilities),
            "budget": self.budget,
            "deadlineMs": self.deadline_ms,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HarnessAssignment:
        expected = {
            "schemaVersion",
            "kind",
            "assignmentId",
            "taskId",
            "taskRevision",
            "taskAttemptId",
            "generation",
            "targetHarnessId",
            "harnessManifestDigest",
            "contextObjectDigest",
            "acceptanceCriteriaDigest",
            "toolCatalogDigest",
            "workspaceRef",
            "sourceRef",
            "sourceDigest",
            "priorArtifactRefs",
            "requiredCapabilities",
            "budget",
            "deadlineMs",
            "createdAtMs",
        }
        _exact(value, expected, "HarnessAssignment")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.harness-assignment":
            raise ValueError("HarnessAssignment version or kind is invalid")
        string_fields = (
            "assignmentId",
            "taskId",
            "taskAttemptId",
            "targetHarnessId",
            "harnessManifestDigest",
            "contextObjectDigest",
            "acceptanceCriteriaDigest",
            "toolCatalogDigest",
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("HarnessAssignment required identity fields must be strings")
        for field in ("workspaceRef", "sourceRef", "sourceDigest"):
            if value[field] is not None and not isinstance(value[field], str):
                raise ValueError(f"HarnessAssignment {field} must be a string or null")
        if type(value["taskRevision"]) is not int or type(value["generation"]) is not int:
            raise ValueError("HarnessAssignment revision and generation must be integers")
        if type(value["createdAtMs"]) is not int:
            raise ValueError("HarnessAssignment creation time must be an integer")
        if value["deadlineMs"] is not None and type(value["deadlineMs"]) is not int:
            raise ValueError("HarnessAssignment deadline must be an integer or null")
        required = value["requiredCapabilities"]
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValueError("HarnessAssignment required capabilities must be strings")
        budget = value["budget"]
        if not isinstance(budget, dict):
            raise ValueError("HarnessAssignment budget must be an object")
        validate_json_value(budget)
        return cls(
            assignment_id=value["assignmentId"],
            task_id=value["taskId"],
            task_revision=value["taskRevision"],
            task_attempt_id=value["taskAttemptId"],
            generation=value["generation"],
            target_harness_id=value["targetHarnessId"],
            harness_manifest_digest=value["harnessManifestDigest"],
            context_object_digest=value["contextObjectDigest"],
            acceptance_criteria_digest=value["acceptanceCriteriaDigest"],
            tool_catalog_digest=value["toolCatalogDigest"],
            workspace_ref=value["workspaceRef"],
            source_ref=value["sourceRef"],
            source_digest=value["sourceDigest"],
            prior_artifact_refs=_parse_artifacts(value["priorArtifactRefs"], "prior Artifact"),
            required_capabilities=tuple(required),
            budget=dict(budget),
            deadline_ms=value["deadlineMs"],
            created_at_ms=value["createdAtMs"],
        )


_RUN_STOP_REASONS = {"completed", "interrupted", "cancelled", "failed", "unknown"}


@dataclass(frozen=True, slots=True)
class HarnessRunReceipt:
    harness_run_id: str
    assignment_id: str
    assignment_generation: int
    harness_id: str
    harness_revision: str
    manifest_digest: str
    session_ref: str
    started_at_ms: int
    finished_at_ms: int
    stop_reason: str
    event_digest: str
    context_digest: str
    tool_catalog_digest: str
    runtime_job_refs: tuple[str, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    usage: dict[str, JsonValue]

    def __post_init__(self) -> None:
        _identity(self.harness_run_id, "harness-run", "Harness Run")
        _identity(self.assignment_id, "assignment", "Assignment")
        if self.assignment_generation < 1:
            raise ValueError("Harness Run Assignment generation must be positive")
        for value, label in (
            (self.harness_id, "Harness Run Harness"),
            (self.harness_revision, "Harness revision"),
            (self.session_ref, "Harness Session ref"),
        ):
            _text(value, label)
        _digest(self.manifest_digest, "Harness Run manifest digest")
        _digest(self.event_digest, "Harness Run event digest")
        _digest(self.context_digest, "Harness Run Context digest")
        _digest(self.tool_catalog_digest, "Harness Run Tool catalog digest")
        if self.started_at_ms < 0 or self.finished_at_ms < self.started_at_ms:
            raise ValueError("Harness Run times are invalid")
        if self.stop_reason not in _RUN_STOP_REASONS:
            raise ValueError(f"unsupported Harness Run stop reason: {self.stop_reason}")
        _unique_text(self.runtime_job_refs, "Runtime Job ref")
        _artifact_refs(self.artifact_refs, "Harness Run Artifact")
        validate_json_value(self.usage)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-run-receipt",
            "harnessRunId": self.harness_run_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "harnessId": self.harness_id,
            "harnessRevision": self.harness_revision,
            "manifestDigest": self.manifest_digest,
            "sessionRef": self.session_ref,
            "startedAtMs": self.started_at_ms,
            "finishedAtMs": self.finished_at_ms,
            "stopReason": self.stop_reason,
            "eventDigest": self.event_digest,
            "contextDigest": self.context_digest,
            "toolCatalogDigest": self.tool_catalog_digest,
            "runtimeJobRefs": list(self.runtime_job_refs),
            "artifactRefs": _artifact_values(self.artifact_refs),
            "usage": self.usage,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HarnessRunReceipt:
        expected = {
            "schemaVersion",
            "kind",
            "harnessRunId",
            "assignmentId",
            "assignmentGeneration",
            "harnessId",
            "harnessRevision",
            "manifestDigest",
            "sessionRef",
            "startedAtMs",
            "finishedAtMs",
            "stopReason",
            "eventDigest",
            "contextDigest",
            "toolCatalogDigest",
            "runtimeJobRefs",
            "artifactRefs",
            "usage",
        }
        _exact(value, expected, "HarnessRunReceipt")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.harness-run-receipt":
            raise ValueError("HarnessRunReceipt version or kind is invalid")
        string_fields = expected - {
            "schemaVersion",
            "kind",
            "assignmentGeneration",
            "startedAtMs",
            "finishedAtMs",
            "runtimeJobRefs",
            "artifactRefs",
            "usage",
        }
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("HarnessRunReceipt identity fields must be strings")
        for field in ("assignmentGeneration", "startedAtMs", "finishedAtMs"):
            if type(value[field]) is not int:
                raise ValueError(f"HarnessRunReceipt {field} must be an integer")
        jobs = value["runtimeJobRefs"]
        if not isinstance(jobs, list) or any(not isinstance(item, str) for item in jobs):
            raise ValueError("HarnessRunReceipt Runtime Job refs must be strings")
        usage = value["usage"]
        if not isinstance(usage, dict):
            raise ValueError("HarnessRunReceipt usage must be an object")
        validate_json_value(usage)
        return cls(
            harness_run_id=value["harnessRunId"],
            assignment_id=value["assignmentId"],
            assignment_generation=value["assignmentGeneration"],
            harness_id=value["harnessId"],
            harness_revision=value["harnessRevision"],
            manifest_digest=value["manifestDigest"],
            session_ref=value["sessionRef"],
            started_at_ms=value["startedAtMs"],
            finished_at_ms=value["finishedAtMs"],
            stop_reason=value["stopReason"],
            event_digest=value["eventDigest"],
            context_digest=value["contextDigest"],
            tool_catalog_digest=value["toolCatalogDigest"],
            runtime_job_refs=tuple(jobs),
            artifact_refs=_parse_artifacts(value["artifactRefs"], "Harness Run Artifact"),
            usage=dict(usage),
        )


@dataclass(frozen=True, slots=True)
class CompletionProposal:
    completion_proposal_id: str
    task_id: str
    task_revision: int
    task_attempt_id: str
    assignment_id: str
    assignment_generation: int
    harness_run_id: str
    summary: str
    acceptance_results: dict[str, JsonValue]
    evidence_refs: tuple[ArtifactRef, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    unresolved_effect_refs: tuple[str, ...]
    unresolved_unknowns: tuple[str, ...]
    usage: dict[str, JsonValue]
    created_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.completion_proposal_id, "completion-proposal", "CompletionProposal")
        _identity(self.task_id, "task", "Task")
        _identity(self.task_attempt_id, "task-attempt", "Task Attempt")
        _identity(self.assignment_id, "assignment", "Assignment")
        _identity(self.harness_run_id, "harness-run", "Harness Run")
        if self.task_revision < 1 or self.assignment_generation < 1:
            raise ValueError("CompletionProposal revision and generation must be positive")
        _text(self.summary, "CompletionProposal summary")
        validate_json_value(self.acceptance_results)
        _artifact_refs(self.evidence_refs, "CompletionProposal evidence")
        _artifact_refs(self.artifact_refs, "CompletionProposal Artifact")
        for effect_ref in self.unresolved_effect_refs:
            _identity(effect_ref, "effect", "unresolved Effect")
        if len(self.unresolved_effect_refs) != len(set(self.unresolved_effect_refs)):
            raise ValueError("unresolved Effect refs must be unique")
        _unique_text(self.unresolved_unknowns, "unresolved unknown")
        validate_json_value(self.usage)
        if self.created_at_ms < 0:
            raise ValueError("CompletionProposal creation time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.completion-proposal",
            "completionProposalId": self.completion_proposal_id,
            "taskId": self.task_id,
            "taskRevision": self.task_revision,
            "taskAttemptId": self.task_attempt_id,
            "assignmentId": self.assignment_id,
            "assignmentGeneration": self.assignment_generation,
            "harnessRunId": self.harness_run_id,
            "summary": self.summary,
            "acceptanceResults": self.acceptance_results,
            "evidenceRefs": _artifact_values(self.evidence_refs),
            "artifactRefs": _artifact_values(self.artifact_refs),
            "unresolvedEffectRefs": list(self.unresolved_effect_refs),
            "unresolvedUnknowns": list(self.unresolved_unknowns),
            "usage": self.usage,
            "createdAtMs": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CompletionProposal:
        expected = {
            "schemaVersion",
            "kind",
            "completionProposalId",
            "taskId",
            "taskRevision",
            "taskAttemptId",
            "assignmentId",
            "assignmentGeneration",
            "harnessRunId",
            "summary",
            "acceptanceResults",
            "evidenceRefs",
            "artifactRefs",
            "unresolvedEffectRefs",
            "unresolvedUnknowns",
            "usage",
            "createdAtMs",
        }
        _exact(value, expected, "CompletionProposal")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.completion-proposal":
            raise ValueError("CompletionProposal version or kind is invalid")
        string_fields = (
            "completionProposalId",
            "taskId",
            "taskAttemptId",
            "assignmentId",
            "harnessRunId",
            "summary",
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("CompletionProposal identity fields must be strings")
        for field in ("taskRevision", "assignmentGeneration", "createdAtMs"):
            if type(value[field]) is not int:
                raise ValueError(f"CompletionProposal {field} must be an integer")
        acceptance = value["acceptanceResults"]
        usage = value["usage"]
        if not isinstance(acceptance, dict) or not isinstance(usage, dict):
            raise ValueError("CompletionProposal acceptance and usage must be objects")
        unresolved_effects = value["unresolvedEffectRefs"]
        unresolved_unknowns = value["unresolvedUnknowns"]
        if not isinstance(unresolved_effects, list) or any(
            not isinstance(item, str) for item in unresolved_effects
        ):
            raise ValueError("CompletionProposal unresolved Effects must be strings")
        if not isinstance(unresolved_unknowns, list) or any(
            not isinstance(item, str) for item in unresolved_unknowns
        ):
            raise ValueError("CompletionProposal unresolved unknowns must be strings")
        validate_json_value(acceptance)
        validate_json_value(usage)
        return cls(
            completion_proposal_id=value["completionProposalId"],
            task_id=value["taskId"],
            task_revision=value["taskRevision"],
            task_attempt_id=value["taskAttemptId"],
            assignment_id=value["assignmentId"],
            assignment_generation=value["assignmentGeneration"],
            harness_run_id=value["harnessRunId"],
            summary=value["summary"],
            acceptance_results=dict(acceptance),
            evidence_refs=_parse_artifacts(value["evidenceRefs"], "CompletionProposal evidence"),
            artifact_refs=_parse_artifacts(value["artifactRefs"], "CompletionProposal Artifact"),
            unresolved_effect_refs=tuple(unresolved_effects),
            unresolved_unknowns=tuple(unresolved_unknowns),
            usage=dict(usage),
            created_at_ms=value["createdAtMs"],
        )


_DECISION_REASONS = {
    "accepted",
    "stale_assignment",
    "missing_artifact",
    "unresolved_effect",
    "unresolved_unknown",
    "acceptance_rejected",
}


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    completion_decision_id: str
    completion_proposal_id: str
    task_id: str
    accepted: bool
    reason_code: str
    reason: str | None
    verification_digest: str
    decided_at_ms: int

    def __post_init__(self) -> None:
        _identity(self.completion_decision_id, "completion-decision", "CompletionDecision")
        _identity(self.completion_proposal_id, "completion-proposal", "CompletionProposal")
        _identity(self.task_id, "task", "Task")
        if self.reason_code not in _DECISION_REASONS:
            raise ValueError(f"unsupported CompletionDecision reason: {self.reason_code}")
        if self.accepted != (self.reason_code == "accepted"):
            raise ValueError("CompletionDecision acceptance differs from reason")
        if self.reason is not None:
            _text(self.reason, "CompletionDecision reason")
        _digest(self.verification_digest, "CompletionDecision verification digest")
        if self.decided_at_ms < 0:
            raise ValueError("CompletionDecision time must be non-negative")

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.completion-decision",
            "completionDecisionId": self.completion_decision_id,
            "completionProposalId": self.completion_proposal_id,
            "taskId": self.task_id,
            "accepted": self.accepted,
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "verificationDigest": self.verification_digest,
            "decidedAtMs": self.decided_at_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CompletionDecision:
        expected = {
            "schemaVersion",
            "kind",
            "completionDecisionId",
            "completionProposalId",
            "taskId",
            "accepted",
            "reasonCode",
            "reason",
            "verificationDigest",
            "decidedAtMs",
        }
        _exact(value, expected, "CompletionDecision")
        if value["schemaVersion"] != 1 or value["kind"] != "ordivon.completion-decision":
            raise ValueError("CompletionDecision version or kind is invalid")
        string_fields = (
            "completionDecisionId",
            "completionProposalId",
            "taskId",
            "reasonCode",
            "verificationDigest",
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("CompletionDecision identity fields must be strings")
        if type(value["accepted"]) is not bool or type(value["decidedAtMs"]) is not int:
            raise ValueError("CompletionDecision acceptance and time have invalid types")
        if value["reason"] is not None and not isinstance(value["reason"], str):
            raise ValueError("CompletionDecision reason must be a string or null")
        return cls(
            completion_decision_id=value["completionDecisionId"],
            completion_proposal_id=value["completionProposalId"],
            task_id=value["taskId"],
            accepted=value["accepted"],
            reason_code=value["reasonCode"],
            reason=value["reason"],
            verification_digest=value["verificationDigest"],
            decided_at_ms=value["decidedAtMs"],
        )


@dataclass(frozen=True, slots=True)
class CompletionDecisionReceipt:
    decision: CompletionDecision
    task_revision: int
    task_state: str
    outcome: TaskOutcome | None
    outcome_digest: str | None

    def __post_init__(self) -> None:
        if self.task_revision < 1:
            raise ValueError("CompletionDecisionReceipt Task revision must be positive")
        if self.outcome is None:
            if self.outcome_digest is not None:
                raise ValueError("CompletionDecisionReceipt digest requires an outcome")
        else:
            if self.outcome_digest != canonical_digest(self.outcome.to_dict()):
                raise ValueError("CompletionDecisionReceipt outcome digest differs")
