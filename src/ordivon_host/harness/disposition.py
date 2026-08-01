from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .tool_semantics import NativeToolRecoveryConsequence


class NativeRunPhase(StrEnum):
    ASSIGNED_UNRECORDED = "assigned-unrecorded"
    RECOVERY_RECORDED = "recovery-recorded"
    ABANDONED = "abandoned"
    RUN_RECORDED = "run-recorded"


class ReplacementScope(StrEnum):
    FORBIDDEN = "forbidden"
    SAME_WORKSPACE = "same-workspace"
    ANY_WORKSPACE = "any-workspace"


class CompletionRoute(StrEnum):
    UNAVAILABLE = "unavailable"
    PROPOSE_CURRENT_RUN = "propose-current-run"
    RECONCILE_UNKNOWN = "reconcile-unknown"


class NativeRunOperatorAction(StrEnum):
    RUN_CURRENT_ASSIGNMENT = "run-current-harness-assignment"
    ABANDON_CURRENT_RUN = "abandon-current-harness-run"
    RECONCILE_CURRENT_UNKNOWN = "reconcile-current-harness-run-unknown"
    REPLACE_ASSIGNMENT = "replace-harness-assignment"
    REPLACE_OR_PROPOSE_COMPLETION = "replace-harness-or-propose-completion"
    PROPOSE_CURRENT_COMPLETION = "propose-completion-from-current-harness-run"
    VERIFY_BEFORE_REPLACEMENT = "verify-current-harness-run-before-replacement"


@dataclass(frozen=True, slots=True)
class NativeRunFacts:
    phase: NativeRunPhase
    granted_recovery_consequence: NativeToolRecoveryConsequence
    termination_code: str | None = None
    has_tool_observations: bool = False
    has_unknown_observation: bool = False
    recovery_safe_to_abandon: bool | None = None
    has_candidate_conclusion: bool = False
    unresolved_unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.phase is NativeRunPhase.RECOVERY_RECORDED:
            if self.recovery_safe_to_abandon is None:
                raise ValueError("Recovery facts require safe-to-abandon state")
        elif self.recovery_safe_to_abandon is not None:
            raise ValueError("only Recovery facts may carry safe-to-abandon state")
        if self.phase is NativeRunPhase.RUN_RECORDED:
            if self.termination_code is None:
                raise ValueError("recorded Run facts require a termination code")
        elif self.termination_code is not None:
            raise ValueError("only recorded Run facts may carry a termination code")
        if self.has_unknown_observation and not self.has_tool_observations:
            raise ValueError(
                "unknown Observation requires at least one Tool Observation"
            )
        if len(self.unresolved_unknowns) != len(set(self.unresolved_unknowns)):
            raise ValueError("Run disposition UNKNOWN values must be unique")


@dataclass(frozen=True, slots=True)
class NativeRunDisposition:
    unresolved_unknowns: tuple[str, ...]
    abandonment_allowed: bool
    replacement_scope: ReplacementScope
    completion_route: CompletionRoute
    operator_action: NativeRunOperatorAction

    @property
    def replacement_allowed(self) -> bool:
        return self.replacement_scope is not ReplacementScope.FORBIDDEN


def recovery_unknowns(
    consequence: NativeToolRecoveryConsequence,
    *,
    workspace_status: str,
) -> tuple[str, ...]:
    unknowns: list[str] = []
    if consequence is NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE:
        unknowns.append("unrecorded native Run may have committed Workspace mutations")
    elif consequence in {
        NativeToolRecoveryConsequence.PROCESS_OR_EXTERNAL_EFFECT_POSSIBLE,
        NativeToolRecoveryConsequence.UNKNOWN,
    }:
        unknowns.append(
            "unrecorded native Run may have started a process or external effect"
        )
    if workspace_status == "unknown":
        unknowns.append("Runtime Workspace cleanup is UNKNOWN")
    return tuple(unknowns)


def derive_native_run_disposition(facts: NativeRunFacts) -> NativeRunDisposition:
    if facts.phase is NativeRunPhase.ASSIGNED_UNRECORDED:
        return NativeRunDisposition(
            facts.unresolved_unknowns,
            False,
            ReplacementScope.FORBIDDEN,
            CompletionRoute.UNAVAILABLE,
            NativeRunOperatorAction.RUN_CURRENT_ASSIGNMENT,
        )
    if facts.phase is NativeRunPhase.RECOVERY_RECORDED:
        if facts.recovery_safe_to_abandon:
            return NativeRunDisposition(
                (),
                True,
                ReplacementScope.FORBIDDEN,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.ABANDON_CURRENT_RUN,
            )
        return NativeRunDisposition(
            facts.unresolved_unknowns,
            False,
            ReplacementScope.FORBIDDEN,
            CompletionRoute.RECONCILE_UNKNOWN,
            NativeRunOperatorAction.RECONCILE_CURRENT_UNKNOWN,
        )
    if facts.phase is NativeRunPhase.ABANDONED:
        return NativeRunDisposition(
            (),
            False,
            ReplacementScope.ANY_WORKSPACE,
            CompletionRoute.UNAVAILABLE,
            NativeRunOperatorAction.REPLACE_ASSIGNMENT,
        )
    assert facts.phase is NativeRunPhase.RUN_RECORDED
    if facts.termination_code == "runtime_unknown" or facts.has_unknown_observation:
        unknowns = facts.unresolved_unknowns or ("runtime_unknown",)
        return NativeRunDisposition(
            unknowns,
            False,
            ReplacementScope.FORBIDDEN,
            CompletionRoute.RECONCILE_UNKNOWN,
            NativeRunOperatorAction.RECONCILE_CURRENT_UNKNOWN,
        )
    effectful_observation = (
        facts.granted_recovery_consequence
        is not NativeToolRecoveryConsequence.OBSERVATION_ONLY
        and facts.has_tool_observations
    )
    if facts.has_candidate_conclusion:
        if effectful_observation:
            return NativeRunDisposition(
                (),
                False,
                ReplacementScope.FORBIDDEN,
                CompletionRoute.PROPOSE_CURRENT_RUN,
                NativeRunOperatorAction.PROPOSE_CURRENT_COMPLETION,
            )
        return NativeRunDisposition(
            (),
            False,
            ReplacementScope.SAME_WORKSPACE,
            CompletionRoute.PROPOSE_CURRENT_RUN,
            NativeRunOperatorAction.REPLACE_OR_PROPOSE_COMPLETION,
        )
    if effectful_observation:
        return NativeRunDisposition(
            (),
            False,
            ReplacementScope.FORBIDDEN,
            CompletionRoute.UNAVAILABLE,
            NativeRunOperatorAction.VERIFY_BEFORE_REPLACEMENT,
        )
    return NativeRunDisposition(
        (),
        False,
        ReplacementScope.SAME_WORKSPACE,
        CompletionRoute.UNAVAILABLE,
        NativeRunOperatorAction.REPLACE_ASSIGNMENT,
    )


def projected_native_run_disposition(
    *,
    phase: NativeRunPhase,
    termination_code: str | None = None,
    replacement_allowed: bool | None = None,
    recovery_safe_to_abandon: bool | None = None,
) -> NativeRunDisposition:
    if phase is NativeRunPhase.RECOVERY_RECORDED:
        return derive_native_run_disposition(
            NativeRunFacts(
                phase,
                NativeToolRecoveryConsequence.OBSERVATION_ONLY,
                recovery_safe_to_abandon=recovery_safe_to_abandon,
                unresolved_unknowns=(
                    () if recovery_safe_to_abandon else ("projected recovery UNKNOWN",)
                ),
            )
        )
    if phase is NativeRunPhase.ABANDONED:
        return derive_native_run_disposition(
            NativeRunFacts(phase, NativeToolRecoveryConsequence.OBSERVATION_ONLY)
        )
    if phase is NativeRunPhase.ASSIGNED_UNRECORDED:
        return derive_native_run_disposition(
            NativeRunFacts(phase, NativeToolRecoveryConsequence.OBSERVATION_ONLY)
        )
    if termination_code is None:
        raise ValueError("projected recorded Run requires termination code")
    if termination_code == "runtime_unknown":
        consequence = NativeToolRecoveryConsequence.UNKNOWN
        observations = True
        unknown = True
    elif replacement_allowed is False:
        consequence = NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE
        observations = True
        unknown = False
    else:
        consequence = NativeToolRecoveryConsequence.OBSERVATION_ONLY
        observations = False
        unknown = False
    return derive_native_run_disposition(
        NativeRunFacts(
            phase,
            consequence,
            termination_code=termination_code,
            has_tool_observations=observations,
            has_unknown_observation=unknown,
            has_candidate_conclusion=termination_code == "candidate_completed",
        )
    )
