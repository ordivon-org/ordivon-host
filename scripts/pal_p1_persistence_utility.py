from __future__ import annotations

import concurrent.futures
import hashlib
import json
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ordivon_host.continuity import ExternalContinuityHost
from ordivon_host.continuity_models import WorkingCheckpoint, WorkingCheckpointRuntime
from ordivon_host.storage import HostStorage

ROOT = Path(__file__).resolve().parents[1]
SECRETS = Path('/root/.config/ordivon/secrets')
OUT = ROOT / 'evidence' / 'pal-p1-persistence-utility-20260813.json'


def sha256_bytes(value: bytes) -> str:
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def source_evidence() -> tuple[str, dict[str, str]]:
    parts: list[str] = []
    digests: dict[str, str] = {}
    for rel in ('README.md', 'docs/STATUS.md', 'docs/OPERATIONS.md'):
        data = (ROOT / rel).read_bytes()
        digests[rel] = sha256_bytes(data)
        text = data.decode('utf-8')
        # The same bounded current-owner evidence is supplied to both treatments.
        parts.append(f'===== {rel} =====\n{text[:14000]}')
    return '\n\n'.join(parts), digests


@dataclass(frozen=True)
class Case:
    case_id: str
    objective: str
    checkpoint: WorkingCheckpoint
    options: tuple[str, str, str, str]
    expected: str
    rationale: str


def cases() -> list[Case]:
    def cp(task: str, *, objective: str, frontier: str, established=(), unresolved=(), constraints=(), next_actions=(), observed='old-head') -> WorkingCheckpoint:
        return WorkingCheckpoint(
            task_id=task,
            objective=objective,
            frontier=frontier,
            established=tuple(established),
            unresolved=tuple(unresolved),
            rejected=('transcript or checkpoint as owner-current physical truth',),
            constraints=tuple(constraints),
            next_actions=tuple(next_actions),
            runtime=WorkingCheckpointRuntime(
                workspace_id='historical-workspace-hint',
                relevant_job_ids=('job-historical-hint',),
                observed_head_revision=observed,
            ),
        )

    return [
        Case(
            'task_specific_commitment',
            'Continue a deployment task after the previous Agent session disappeared.',
            cp('task:pal-p1:commitment', objective='continue deployment', frontier='verification passed; deployment not yet authorized', established=('Operator commitment: do not restart production before explicit approval.',), unresolved=('operator approval has not been observed',), constraints=('Preserve explicit participant commitments across session replacement.',), next_actions=('wait for or obtain explicit operator approval',)),
            ('Restart now because technical verification passed.', 'Infer approval from the absence of objections.', 'Keep the deployment stopped until explicit approval is observed.', 'Discard the old Task and recreate it.'),
            'C',
            'Task-specific commitment is not reconstructable from repository truth; bounded semantic persistence is the authority for that commitment.'
        ),
        Case(
            'stale_runtime_hint',
            'Continue an engineering task whose old checkpoint names a Runtime Workspace.',
            cp('task:pal-p1:stale-runtime', objective='continue engineering task', frontier='revalidate current physical work before mutation', established=('Host owns semantic continuity only.',), unresolved=('current Runtime/Git state may have advanced',), constraints=('Runtime/Git owner truth overrides navigation hints.',), next_actions=('revalidate current workspace and repository owner truth',), observed='historical-revision'),
            ('Use the checkpoint workspace/revision directly because Host persisted it.', 'Revalidate current Runtime/Git owner truth before acting.', 'Delete the checkpoint because any stale hint invalidates semantic continuity.', 'Mark the task complete because a Workspace identity exists.'),
            'B',
            'WorkingCheckpoint Runtime/Git fields are navigation hints and current owner truth must be re-observed.'
        ),
        Case(
            'continuity_not_domain_completion',
            'A previous Agent ended Host continuity tracking while an external provider consequence remained uncertain.',
            cp('task:pal-p1:continuity-end', objective='finish external operation', frontier='Host continuity can end; provider occurrence still unresolved', established=('continuityDisposition=complete ends Host tracking only',), unresolved=('provider-native occurrence remains UNKNOWN',), constraints=('Host terminal continuity disposition does not assert domain/provider success.',), next_actions=('reconcile the provider-native original identity',)),
            ('Treat Host completion as proof the provider succeeded.', 'Issue a fresh provider request because the Host task ended.', 'Mark the provider request failed because no receipt is present.', 'Reconcile/re-observe the original provider identity before any domain conclusion.'),
            'D',
            'Continuity completion is not external/domain completion; original provider identity must be reconciled.'
        ),
        Case(
            'response_loss_effect_identity',
            'Resume after a consequential external effect may have committed but its response was lost.',
            cp('task:pal-p1:response-loss', objective='recover uncertain effect', frontier='original dispatch admitted; response path lost', established=('Exact original effect/dispatch identity is durable.',), unresolved=('physical/provider outcome is UNKNOWN',), constraints=('Do not blindly redispatch an ambiguous consequence.',), next_actions=('query/reconcile the original durable external identity',)),
            ('Reconcile the original durable effect/request identity.', 'Create a new request with the same semantic intent.', 'Assume failure because no response was received.', 'Assume success because dispatch was attempted.'),
            'A',
            'Response loss creates UNKNOWN; exact original identity must be reconciled before any new effect.'
        ),
        Case(
            'checkpoint_owner_fact_stale',
            'Determine the current public Host MCP surface after an older checkpoint recorded a historical tool count.',
            cp('task:pal-p1:owner-currentness', objective='inspect current Host MCP surface', frontier='historical checkpoint recorded five tools', established=('Historical observation: five Host MCP tools at the time of the prior session.',), unresolved=('current owner source may have advanced',), constraints=('Current owner source outranks historical checkpoint for current product facts.',), next_actions=('inspect current Host owner source',), observed='historical-five-tools'),
            ('Report five tools because checkpoint history is durable.', 'Report UNKNOWN even if current owner README states the exact surface.', 'Use the current owner source and report its current six-tool MCP surface.', 'Average historical and current observations.'),
            'C',
            'Durable historical observation remains history; current owner source establishes current surface.'
        ),
        Case(
            'irrelevant_persistence',
            'Start a new owner-current documentation audit unrelated to the previous completed work.',
            cp('task:pal-p1:irrelevant', objective='historical unrelated formatting task', frontier='old task finished', established=('Old formatting choice was accepted for a different artifact.',), unresolved=(), constraints=('Do not generalize local historical choices without a current consumer.',), next_actions=('none; old work is complete',)),
            ('Inspect the current owner sources for the new audit and ignore irrelevant historical task detail.', 'Reuse the old formatting choice as the new audit conclusion.', 'Persist every old detail into the new task before observing current sources.', 'Treat the old accepted choice as a cross-project invariant.'),
            'A',
            'Irrelevant persistence should not displace fresh owner observation or become a universal prior.'
        ),
    ]


def load_credentials() -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in sorted(SECRETS.glob('deepseek*.json')):
        record = json.loads(path.read_text())
        values.append({
            'path': path.name,
            'apiKey': record['apiKey'],
            'baseUrl': record['baseUrl'].rstrip('/'),
            'model': record['model'],
            'credentialScopeId': record.get('credentialScopeId', path.stem),
        })
    if not values:
        raise RuntimeError('No DeepSeek credentials found')
    return values


def model_call(credential: dict[str, Any], prompt: str) -> dict[str, Any]:
    body = json.dumps({
        'model': credential['model'],
        'messages': [
            {'role': 'system', 'content': 'You are a continuation evaluator. Use only the supplied evidence. Return a JSON object with keys answer and reason. answer must be exactly A, B, C, or D. Keep reason under 80 words.'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.1,
        'max_tokens': 220,
        'response_format': {'type': 'json_object'},
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        credential['baseUrl'] + '/chat/completions',
        data=body,
        headers={'Authorization': 'Bearer ' + credential['apiKey'], 'Content-Type': 'application/json'},
        method='POST',
    )
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read())
            content = payload['choices'][0]['message']['content']
            parsed = json.loads(content)
            return {
                'ok': True,
                'attempts': attempt + 1,
                'latencyMs': round((time.perf_counter() - started) * 1000),
                'credentialScopeId': credential['credentialScopeId'],
                'model': payload.get('model'),
                'finishReason': payload['choices'][0].get('finish_reason'),
                'usage': payload.get('usage', {}),
                'answer': parsed.get('answer'),
                'reason': str(parsed.get('reason', ''))[:1200],
                'rawContentDigest': sha256_bytes(content.encode()),
            }
        except Exception as exc:  # retained as invalid evidence, not silently dropped
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
    return {
        'ok': False,
        'attempts': 3,
        'latencyMs': round((time.perf_counter() - started) * 1000),
        'credentialScopeId': credential['credentialScopeId'],
        'error': f'{type(last_error).__name__}: {last_error}'[:500],
    }


def main() -> None:
    current_evidence, source_digests = source_evidence()
    credentials = load_credentials()
    all_cases = cases()
    records: list[dict[str, Any]] = []
    checkpoints: dict[str, dict[str, Any]] = {}

    # Produce the treatment state through the real Host continuity implementation,
    # not by handing an invented JSON object directly to the model.
    with tempfile.TemporaryDirectory() as directory:
        state_root = Path(directory) / 'host-state'
        clock_value = [1_000_000]
        def clock_ms() -> int:
            clock_value[0] += 1
            return clock_value[0]
        with HostStorage(state_root) as storage:
            host = ExternalContinuityHost(storage, clock_ms=clock_ms)
            for case in all_cases:
                resumed = host.adopt(
                    task_id=case.checkpoint.task_id,
                    goal_id='goal:pal-p1-persistence-utility',
                    initial_checkpoint=case.checkpoint,
                )
                if resumed.checkpoint is None:
                    raise RuntimeError('Host did not retain checkpoint')
                checkpoints[case.case_id] = resumed.to_dict()

    jobs: list[tuple[str, Case, int, dict[str, Any], str]] = []
    replicas = 2
    for case_index, case in enumerate(all_cases):
        option_text = '\n'.join(f'{chr(65+i)}. {text}' for i, text in enumerate(case.options))
        for treatment in ('fresh-owner-baseline', 'checkpoint-assisted'):
            for replica in range(replicas):
                checkpoint_section = ''
                if treatment == 'checkpoint-assisted':
                    checkpoint_section = '\n\nHOST RESUME PACKET (semantic working claim; current owner facts still require revalidation):\n' + json.dumps(checkpoints[case.case_id], ensure_ascii=False, sort_keys=True)
                prompt = f'''OBJECTIVE\n{case.objective}\n\nCURRENT HOST OWNER EVIDENCE\n{current_evidence}{checkpoint_section}\n\nChoose the best next action.\n{option_text}\n\nReturn only JSON with answer and reason.'''
                credential = credentials[(case_index * replicas + replica) % len(credentials)]
                jobs.append((treatment, case, replica, credential, prompt))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {
            pool.submit(model_call, credential, prompt): (treatment, case, replica, prompt)
            for treatment, case, replica, credential, prompt in jobs
        }
        for future in concurrent.futures.as_completed(future_map):
            treatment, case, replica, prompt = future_map[future]
            result = future.result()
            records.append({
                'caseId': case.case_id,
                'treatment': treatment,
                'replica': replica,
                'promptBytes': len(prompt.encode()),
                'expectedAnswer': case.expected,
                'expectedReason': case.rationale,
                'result': result,
                'valid': bool(result.get('ok')) and result.get('answer') in {'A','B','C','D'},
                'correct': bool(result.get('ok')) and result.get('answer') == case.expected,
            })

    records.sort(key=lambda r: (r['caseId'], r['treatment'], r['replica']))
    summary: dict[str, Any] = {}
    for treatment in ('fresh-owner-baseline', 'checkpoint-assisted'):
        rows = [r for r in records if r['treatment'] == treatment]
        valid = [r for r in rows if r['valid']]
        correct = [r for r in valid if r['correct']]
        summary[treatment] = {
            'calls': len(rows),
            'valid': len(valid),
            'correct': len(correct),
            'accuracy': (len(correct) / len(valid)) if valid else None,
            'totalPromptBytes': sum(r['promptBytes'] for r in rows),
            'totalCompletionTokens': sum(int(r['result'].get('usage', {}).get('completion_tokens', 0) or 0) for r in valid),
            'totalPromptTokens': sum(int(r['result'].get('usage', {}).get('prompt_tokens', 0) or 0) for r in valid),
            'medianLatencyMs': sorted([int(r['result'].get('latencyMs', 0)) for r in valid])[len(valid)//2] if valid else None,
        }

    per_case = {}
    for case in all_cases:
        per_case[case.case_id] = {
            treatment: [
                {'replica': r['replica'], 'valid': r['valid'], 'correct': r['correct'], 'answer': r['result'].get('answer')}
                for r in records if r['caseId'] == case.case_id and r['treatment'] == treatment
            ] for treatment in ('fresh-owner-baseline', 'checkpoint-assisted')
        }

    treatment_correct = summary['checkpoint-assisted']['correct']
    baseline_correct = summary['fresh-owner-baseline']['correct']
    commitment_case = per_case['task_specific_commitment']
    stale_cases = ['stale_runtime_hint', 'checkpoint_owner_fact_stale']
    stale_safe = all(all(row['correct'] for row in per_case[c]['checkpoint-assisted'] if row['valid']) for c in stale_cases)
    receipt = {
        'schemaVersion': 1,
        'kind': 'ordivon.host.pal-p1-persistence-utility',
        'status': 'accepted-bounded-utility' if treatment_correct > baseline_correct and stale_safe else 'falsified-or-inconclusive',
        'hostRevision': __import__('subprocess').check_output(['git','rev-parse','HEAD'], text=True).strip(),
        'sourceDigests': source_digests,
        'design': {
            'question': 'Does bounded Host semantic persistence improve fresh-session continuation beyond a strong current-owner re-observation baseline without causing stale physical hints to outrank owner truth?',
            'treatments': ['fresh-owner-baseline', 'checkpoint-assisted'],
            'replicasPerCell': replicas,
            'cases': [case.case_id for case in all_cases],
            'sameCurrentOwnerEvidenceBothTreatments': True,
            'hostCheckpointProducedByRealExternalContinuityHost': True,
            'hiddenExpectedAnswersNotModelVisible': True,
        },
        'summary': summary,
        'perCase': per_case,
        'gates': {
            'allCallsValid': all(r['valid'] for r in records),
            'checkpointDoesNotLoseAggregateCorrectness': treatment_correct >= baseline_correct,
            'checkpointImprovesAggregateCorrectness': treatment_correct > baseline_correct,
            'checkpointPreservesStaleHintRevalidation': stale_safe,
            'taskSpecificCommitmentTreatmentAllCorrect': all(r['correct'] for r in commitment_case['checkpoint-assisted'] if r['valid']),
            'taskSpecificCommitmentBaselineNotAssumedRecoverable': not all(r['correct'] for r in commitment_case['fresh-owner-baseline'] if r['valid']),
        },
        'interpretation': {
            'persistenceVolumeIsValue': False,
            'boundedSemanticCommitmentCanCarryUniqueContinuationValue': True if treatment_correct > baseline_correct else None,
            'physicalNavigationHintBecomesAuthority': False,
            'deadPersistencePressure': 'irrelevant checkpoints should not be promoted into new work; fresh owner observation remains sufficient for unrelated tasks',
            'claimLimit': 'This tests bounded continuation decisions over current Host semantics. It does not prove all checkpoints improve all tasks or justify a generic memory layer.'
        },
        'records': records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': receipt['status'], 'summary': summary, 'gates': receipt['gates'], 'output': str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
