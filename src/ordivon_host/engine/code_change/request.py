from __future__ import annotations

import base64
import json

from anc_canonical import JsonValue

from .._serde import task_token
from .models import CodeChangeDispatch, CodeChangePlan, request_digest

_PATCH_SCRIPT = r'''
import base64, hashlib, json, os, pathlib, sys, uuid
spec = json.loads(base64.b64decode(sys.argv[1], validate=True))
prepared = []
originals = []
for item in spec["files"]:
    path = pathlib.Path(item["relativePath"])
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("unsafe code-change path")
    original = path.read_bytes()
    digest = "sha256:" + hashlib.sha256(original).hexdigest()
    if digest != item["expectedDigest"]:
        raise SystemExit(f"source digest differs: {path}: {digest}")
    content = base64.b64decode(item["contentBase64"], validate=True)
    result = "sha256:" + hashlib.sha256(content).hexdigest()
    if result != item["resultDigest"]:
        raise SystemExit(f"result digest differs: {path}: {result}")
    temporary = path.with_name(f".{path.name}.ordivon-{uuid.uuid4().hex}.tmp")
    mode = path.stat().st_mode & 0o777
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    prepared.append((temporary, path))
    originals.append((path, original, mode))
replaced = []
try:
    for temporary, path in prepared:
        os.replace(temporary, path)
        replaced.append(path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
except BaseException:
    for path, original, mode in originals:
        if path not in replaced:
            continue
        rollback = path.with_name(f".{path.name}.ordivon-rollback-{uuid.uuid4().hex}.tmp")
        with rollback.open("xb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(rollback, mode)
        os.replace(rollback, path)
    raise
finally:
    for temporary, _ in prepared:
        temporary.unlink(missing_ok=True)
print(json.dumps({"changedFiles": [item["relativePath"] for item in spec["files"]]}, sort_keys=True), flush=True)
'''.strip()


def build_exec_plan_request(
    plan: CodeChangePlan,
) -> tuple[CodeChangeDispatch, dict[str, JsonValue]]:
    token = task_token(plan.task_id)
    client_request_id = f"request:code-change:{token}:r1"
    files = [
        {
            "relativePath": item.relative_path,
            "expectedDigest": item.expected_digest,
            "resultDigest": item.result_digest,
            "contentBase64": base64.b64encode(item.content.encode("utf-8")).decode("ascii"),
        }
        for item in plan.files
    ]
    encoded = base64.b64encode(
        json.dumps({"files": files}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    steps: list[JsonValue] = [
        {
            "id": "apply-files",
            "executable": plan.patch_executable,
            "args": ["-c", _PATCH_SCRIPT, encoded],
            "cwdRelative": ".",
            "env": {},
            "timeoutMs": 30_000,
        }
    ]
    steps.extend(check.to_dict() for check in plan.checks)
    arguments: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "clientRequestId": client_request_id,
        "execution": {
            "workspaceId": plan.workspace_id,
            "steps": steps,
            "stdoutLimitBytes": 262_144,
            "stderrLimitBytes": 262_144,
        },
        "waitMs": 0,
        "stdoutTailBytes": 8_192,
        "stderrTailBytes": 8_192,
    }
    dispatch = CodeChangeDispatch(
        dispatch_id=f"dispatch:{token}:exec-plan:r1",
        client_request_id=client_request_id,
        workspace_id=plan.workspace_id,
        operation="workspace.execPlan",
        request_digest=request_digest(arguments),
    )
    return dispatch, arguments
