#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from anc_canonical import JsonValue, canonical_digest
from ordivon_host import EventKind, HostStorage, TaskProjection, TaskState
from ordivon_host.cognition import (
    AdmissionState,
    BlockKind,
    CandidateAction,
    CodexCliModelAdapter,
    CognitionRequest,
    CognitionTurnHost,
    DecisionAdmission,
    DecisionKind,
    Freshness,
    HermesCliModelAdapter,
    block_from_payload,
)

_WORLD_DIGEST = "sha256:" + ("a" * 64)
_DISPATCH_ID = "dispatch:runtime-job-7"
_COMPLETED_EFFECT_ID = "effect:completed-mutation"
_ACTION_ID = "action:observe-original-dispatch"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run two replaceable providers against one persistent multi-candidate Context."
    )
    parser.add_argument("--state-root")
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--codex-model")
    parser.add_argument("--hermes-model", default="deepseek-v4-pro")
    parser.add_argument("--hermes-provider", default="deepseek")
    parser.add_argument("--hermes-base-url", default="https://api.deepseek.com")
    parser.add_argument("--hermes-credentials", default="/root/.hermes/.env")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stamp = int(time.time() * 1_000)
    task_token = f"live-cognition-{stamp}"
    task_id = f"task:{task_token}"
    goal_id = f"goal:{task_token}"
    decision_node_id = f"node:{task_token}:decide"
    state_root = Path(
        args.state_root
        or tempfile.mkdtemp(prefix=f"ordivon-host-cognition-{stamp}-", dir="/tmp")
    )
    state_root.mkdir(parents=True, exist_ok=True)
    working_directory = Path(args.working_directory).resolve()
    request = _request(task_id, goal_id)
    admission_state = AdmissionState(
        world_digest=_WORLD_DIGEST,
        completed_effect_ids=(_COMPLETED_EFFECT_ID,),
        unresolved_dispatch_ids=(_DISPATCH_ID,),
    )
    def clock() -> int:
        return int(time.time() * 1_000)

    try:
        with HostStorage(state_root) as storage:
            projection = TaskProjection(
                task_id=task_id,
                goal_id=goal_id,
                state=TaskState.READY,
                active_node_id=None,
                ready_frontier=(decision_node_id,),
                revision=1,
                updated_at_ms=clock(),
            )
            storage.record_task_event(
                event_id=f"event:{task_token}:create",
                kind=EventKind.TASK_CREATED,
                payload={"decisionNodeId": decision_node_id},
                projection=projection,
                expected_revision=0,
            )
            prepared = CognitionTurnHost(storage, clock_ms=clock).prepare(
                task_id=task_id,
                decision_node_id=decision_node_id,
                request=request,
                token_budget=8_000,
            )
            prepared_digest = prepared.context.digest
            prepared_object_digest = prepared.context_object.digest
            context_bytes = prepared.context.byte_length
            context_tokens = prepared.context.estimated_tokens
            context_manifest = prepared.context.manifest.to_dict()

        with HostStorage(state_root) as storage:
            prepared = CognitionTurnHost(storage, clock_ms=clock).load_prepared(task_id)

        codex = CodexCliModelAdapter(
            working_directory=working_directory,
            model=args.codex_model,
            timeout_seconds=240,
        )
        hermes = HermesCliModelAdapter(
            working_directory=working_directory,
            model=args.hermes_model,
            provider=args.hermes_provider,
            base_url=args.hermes_base_url,
            credential_env_path=args.hermes_credentials,
            timeout_seconds=240,
        )
        codex_decision = codex.decide(prepared.context)
        hermes_decision = hermes.decide(prepared.context)
        admission = DecisionAdmission()
        codex_admitted = admission.admit(
            prepared.context,
            codex_decision,
            current_world_digest=admission_state.world_digest,
            completed_effect_ids=admission_state.completed_effect_ids,
            unresolved_dispatch_ids=admission_state.unresolved_dispatch_ids,
        )
        hermes_admitted = admission.admit(
            prepared.context,
            hermes_decision,
            current_world_digest=admission_state.world_digest,
            completed_effect_ids=admission_state.completed_effect_ids,
            unresolved_dispatch_ids=admission_state.unresolved_dispatch_ids,
        )
        selected = (
            codex_admitted.action.action_id,
            hermes_admitted.action.action_id,
        )
        if selected != (_ACTION_ID, _ACTION_ID):
            raise AssertionError(
                f"providers did not converge on original Dispatch observation: {selected}"
            )

        with HostStorage(state_root) as storage:
            recovered = CognitionTurnHost(storage, clock_ms=clock).load_prepared(task_id)
            persisted = CognitionTurnHost(storage, clock_ms=clock).admit_decision(
                recovered,
                codex_decision,
                adapter_id=codex.adapter_id,
                state_reader=lambda: admission_state,
            )
            final_projection = storage.journal.get_task(task_id)
            if final_projection is None:
                raise AssertionError("Cognition Task projection disappeared")
            object_refs = storage.journal.object_refs()
            snapshot = storage.read_task_event(task_id)
            checks = {
                "samePersistentContext": (
                    prepared_digest
                    == codex_decision.context_digest
                    == hermes_decision.context_digest
                    == persisted.context_digest
                ),
                "sameContextObjectAfterFreshOpen": (
                    prepared_object_digest == recovered.context_object.digest
                ),
                "codexAdmitted": codex_admitted.action.action_id == _ACTION_ID,
                "hermesAdmitted": hermes_admitted.action.action_id == _ACTION_ID,
                "providersConverged": (
                    codex_admitted.action.action_id
                    == hermes_admitted.action.action_id
                ),
                "persistedDecisionMatches": persisted.selected_action_id == _ACTION_ID,
                "noProviderSessionInTaskState": True,
                "unresolvedDispatchPreservedInContext": (
                    _DISPATCH_ID
                    in prepared.context.payload.get("unresolvedDispatches", [])
                ),
                "completedEffectForbiddenInContext": (
                    _COMPLETED_EFFECT_ID
                    in prepared.context.payload.get("forbiddenEffects", [])
                ),
                "taskAdvancedAfterAdmission": final_projection.revision == 3,
                "threeHostEvents": storage.journal.event_count(task_id) == 3,
            }
            if not all(checks.values()):
                raise AssertionError(f"live cognition checks failed: {checks}")
            receipt: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.host-live-multi-candidate-cognition",
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "taskId": task_id,
                "goalId": goal_id,
                "decisionNodeId": decision_node_id,
                "worldDigest": _WORLD_DIGEST,
                "completedEffectIds": [_COMPLETED_EFFECT_ID],
                "unresolvedDispatchIds": [_DISPATCH_ID],
                "context": {
                    "digest": prepared_digest,
                    "objectDigest": prepared_object_digest,
                    "byteLength": context_bytes,
                    "estimatedTokens": context_tokens,
                    "manifest": context_manifest,
                    "candidateCount": len(request.candidates),
                },
                "providers": [
                    {
                        "adapterId": codex.adapter_id,
                        "decision": codex_decision.to_dict(),
                        "admittedActionId": codex_admitted.action.action_id,
                        "evidence": codex.evidence_metadata(),
                    },
                    {
                        "adapterId": hermes.adapter_id,
                        "decision": hermes_decision.to_dict(),
                        "admittedActionId": hermes_admitted.action.action_id,
                        "evidence": hermes.evidence_metadata(),
                    },
                ],
                "persistence": {
                    "preparedRevision": 2,
                    "finalRevision": final_projection.revision,
                    "hostEventCount": storage.journal.event_count(task_id),
                    "objectRefCount": len(object_refs),
                    "objectKinds": sorted({reference.kind for reference in object_refs}),
                    "terminalEventKind": snapshot.event_kind.value,
                    "persistedAdapterId": persisted.adapter_id,
                    "persistedActionId": persisted.selected_action_id,
                },
                "checks": checks,
                "environment": {
                    "workingDirectory": str(working_directory),
                    "codexVersion": _version("codex", "--version"),
                    "hermesVersion": _version("hermes", "--version"),
                    "stateRoot": str(state_root) if args.keep_state else None,
                },
            }
            receipt["integrity"] = {
                "algorithm": "sha256",
                "canonicalization": "ordivon-canonical-json-v1",
                "payloadDigest": canonical_digest(receipt),
            }
            print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        if not args.keep_state:
            shutil.rmtree(state_root, ignore_errors=True)


def _request(task_id: str, goal_id: str) -> CognitionRequest:
    goal = {
        "goalId": goal_id,
        "statement": (
            "Continue the known Runtime operation without duplicate physical delivery. "
            "The original Runtime Job remains addressable and can be observed now."
        ),
    }
    dispatch = {
        "dispatchId": _DISPATCH_ID,
        "effectId": "effect:original-runtime-operation",
        "state": "unknown",
        "runtimeJobKnown": True,
        "observationAvailable": True,
    }
    constraints = {
        "duplicateDeliveryForbidden": True,
        "humanDecisionRequired": False,
        "externalSignalExpected": False,
        "completionAllowedBeforeReconciliation": False,
    }
    return CognitionRequest(
        task_id=task_id,
        world_digest=_WORLD_DIGEST,
        blocks=(
            block_from_payload(
                block_id="context-block:goal",
                kind=BlockKind.GOAL,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source=goal,
                payload=goal,
            ),
            block_from_payload(
                block_id="context-block:dispatch",
                kind=BlockKind.DISPATCH,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source=dispatch,
                payload=dispatch,
            ),
            block_from_payload(
                block_id="context-block:constraints",
                kind=BlockKind.CONSTRAINT,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source=constraints,
                payload=constraints,
            ),
        ),
        candidates=(
            CandidateAction(
                _ACTION_ID,
                DecisionKind.OBSERVE_DISPATCH,
                "Observe the original Runtime Job and reconcile its real state.",
                dispatch_id=_DISPATCH_ID,
            ),
            CandidateAction(
                "action:request-human-on-ambiguity",
                DecisionKind.REQUEST_HUMAN,
                "Request a human decision only if required information is unavailable.",
            ),
            CandidateAction(
                "action:wait-for-external-signal",
                DecisionKind.WAIT,
                "Wait only when no observation can currently reduce uncertainty.",
            ),
        ),
        forbidden_effect_ids=(_COMPLETED_EFFECT_ID,),
        unresolved_dispatch_ids=(_DISPATCH_ID,),
    )


def _version(executable: str, argument: str) -> str:
    completed = subprocess.run(
        [executable, argument],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = (completed.stdout or completed.stderr).strip()
    return output.splitlines()[0] if output else "unknown"


if __name__ == "__main__":
    main()
