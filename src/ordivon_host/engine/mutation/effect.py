from __future__ import annotations

import base64

from anc_effect_ir import (
    CanonicalInput,
    CapabilityRequirement,
    CompletionKind,
    DeliverySemantics,
    EffectEnvelope,
    EffectMode,
    EvidenceKind,
    ExecutionKind,
    IdempotencyKind,
    ResultSemantics,
    SemanticAction,
    TargetRef,
    VerificationPlan,
)

from .._serde import task_token
from .models import GuardedMutationPlan

_CREATE_SCRIPT = (
    "import base64,os,sys;"
    "path=sys.argv[1];data=base64.b64decode(sys.argv[2],validate=True);"
    "fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
    "stream=os.fdopen(fd,'wb');stream.write(data);stream.flush();"
    "os.fsync(stream.fileno());stream.close()"
)


def mutation_effect(plan: GuardedMutationPlan) -> EffectEnvelope:
    action = "anc.execution.launch.v1"
    target = TargetRef(f"world_object:ordivon-workspace:{plan.workspace_id}")
    encoded = base64.b64encode(plan.content.encode("utf-8")).decode("ascii")
    return EffectEnvelope(
        effect_id=f"effect:{task_token(plan.task_id)}:exec",
        target=target,
        mode=EffectMode.CHANGE,
        action=SemanticAction(action, "anc.execution-launch-input.v1"),
        input=CanonicalInput(
            {
                "executable": "/usr/bin/python3",
                "args": ["-c", _CREATE_SCRIPT, plan.relative_path, encoded],
                "cwdRelative": ".",
                "env": {},
                "timeoutMs": plan.timeout_ms,
                "stdoutLimitBytes": 65_536,
                "stderrLimitBytes": 65_536,
                "waitMs": 0,
                "stdoutTailBytes": 4_096,
                "stderrTailBytes": 4_096,
            }
        ),
        capability=CapabilityRequirement(plan.principal_id, action, target.object_id),
        delivery=DeliverySemantics(IdempotencyKind.NONE),
        result=ResultSemantics(
            ExecutionKind.ASYNCHRONOUS,
            CompletionKind.ACCEPTED_VERIFICATION,
        ),
        verification=VerificationPlan(
            "exact-content-sha256.v1",
            (EvidenceKind.OBSERVATION,),
        ),
    )


