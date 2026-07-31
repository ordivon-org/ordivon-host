#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from anc_canonical import JsonValue, canonical_bytes, canonical_digest

from ordivon_host.cognition import BlockKind, ContextBlock, Freshness
from ordivon_host.harness import (
    CommittedHarnessAssignment,
    HarnessAssignment,
    TaskAttemptDescriptor,
)
from ordivon_host.harness.ordivon import (
    DEFAULT_DEEPSEEK_SECRET_PATH,
    DeepSeekSettings,
    DeepSeekTurnAdapter,
    HarnessContextCompiler,
    HarnessContextRequest,
    OrdivonAgentLoop,
    OrdivonInputCompiler,
    RunBudget,
    RuntimeToolBridge,
    discover_harness_runtime_catalog,
    harness_context_object_digest,
    ordivon_harness_manifest,
)
from ordivon_host.objects import StoredObject
from ordivon_host.runtime import McpRuntimeClient

EXPECTED_HEADING = "# Ordivon Host"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the OH3 DeepSeek two-turn read-only Harness dogfood against Ordivon Runtime."
        )
    )
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path("/root/projects/ordivon-host"),
    )
    parser.add_argument(
        "--source-revision",
        help="Git revision to open; defaults to source repository main",
    )
    parser.add_argument(
        "--runtime-endpoint",
        default=os.environ.get("ORDIVON_RUNTIME_ENDPOINT"),
    )
    parser.add_argument(
        "--deepseek-secret",
        type=Path,
        default=DEFAULT_DEEPSEEK_SECRET_PATH,
    )
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--max-model-calls", type=int, default=4)
    parser.add_argument("--max-tool-calls", type=int, default=4)
    return parser.parse_args()


def _runtime_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit
    bind = os.environ.get("ORDIVON_BIND", "127.0.0.1:8897")
    port = bind.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}/mcp"


def _git_revision(repo: Path, revision: str | None) -> str:
    target = revision or "main"
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", target],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = completed.stdout.strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"Git returned an invalid revision: {value!r}")
    return value


def _stored(kind: str, payload: JsonValue) -> StoredObject:
    envelope: JsonValue = {
        "schemaVersion": 1,
        "kind": kind,
        "payload": payload,
    }
    encoded = canonical_bytes(envelope)
    return StoredObject(canonical_digest(envelope), len(encoded), kind)


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped
    return None


@dataclass(frozen=True, slots=True)
class LiveRunEvidence:
    source_revision: str
    runtime_catalog_digest: str
    assignment_id: str
    context_object_digest: str
    harness_run_id: str
    stop_code: str
    model_calls: int
    tool_calls: int
    observation_statuses: tuple[str, ...]
    trace_digest: str
    conclusion_status: str | None
    conclusion_summary: str | None
    observed_heading: str | None
    accepted: bool
    usage: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.harness-oh3-live-evidence",
            "sourceRevision": self.source_revision,
            "runtimeCatalogDigest": self.runtime_catalog_digest,
            "assignmentId": self.assignment_id,
            "contextObjectDigest": self.context_object_digest,
            "harnessRunId": self.harness_run_id,
            "stopCode": self.stop_code,
            "modelCalls": self.model_calls,
            "toolCalls": self.tool_calls,
            "observationStatuses": list(self.observation_statuses),
            "traceDigest": self.trace_digest,
            "conclusionStatus": self.conclusion_status,
            "conclusionSummary": self.conclusion_summary,
            "observedHeading": self.observed_heading,
            "expectedHeading": EXPECTED_HEADING,
            "accepted": self.accepted,
            "usage": self.usage,
        }


def _write_evidence(path: Path, evidence: LiveRunEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> LiveRunEvidence:
    source_repo = args.source_repo.expanduser().resolve()
    source_revision = _git_revision(source_repo, args.source_revision)
    runtime_token = os.environ.get("ORDIVON_BEARER_TOKEN")
    if not runtime_token:
        raise RuntimeError("ORDIVON_BEARER_TOKEN is not set")
    runtime = McpRuntimeClient(
        _runtime_endpoint(args.runtime_endpoint),
        runtime_token,
        client_name="ordivon-harness-oh3-live",
        client_version="0.1.0",
    )
    runtime.initialize()
    opened = runtime.call_tool(
        "workspace.open",
        {
            "schemaVersion": 1,
            "sourceRepo": str(source_repo),
            "sourceRevision": source_revision,
        },
    )
    workspace_id = opened.get("workspaceId")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise RuntimeError("workspace.open omitted Workspace identity")

    try:
        catalog = discover_harness_runtime_catalog(runtime)
        profile = HarnessContextRequest(
            task_id="task:oh3-live-readme",
            objective={
                "summary": (
                    "Read README.md through Ordivon Runtime and report its exact first Markdown "
                    "heading. Do not rely on prior knowledge."
                ),
                "target": {
                    "kind": "repository-file",
                    "relativePath": "README.md",
                    "sourceRevision": source_revision,
                },
            },
            acceptance_criteria={
                "checks": [
                    "A Runtime read_workspace Observation contains README.md content.",
                    (
                        "The first Markdown heading extracted from that Observation is "
                        f"{EXPECTED_HEADING}."
                    ),
                ]
            },
            constraints=(
                "Do not mutate the Workspace.",
                "Use read_workspace before submitting candidate_completed.",
                "Do not invent Artifact, evidence, Job, Tool Call, or completion identities.",
            ),
            blocks=(
                ContextBlock(
                    block_id="context-block:oh3-live:readme",
                    kind=BlockKind.TASK,
                    priority=100,
                    required=True,
                    freshness=Freshness.CURRENT,
                    source_digest=canonical_digest(
                        {
                            "sourceRevision": source_revision,
                            "relativePath": "README.md",
                        }
                    ),
                    payload={
                        "relativePath": "README.md",
                        "readMode": "FULL",
                        "maxBytes": 65_536,
                    },
                ),
            ),
        )
        attempt = TaskAttemptDescriptor(
            task_attempt_id="task-attempt:oh3-live-readme:1",
            task_id=profile.task_id,
            started_at_task_revision=1,
            objective_digest=profile.objective_digest,
            acceptance_criteria_digest=profile.acceptance_criteria_digest,
            created_at_ms=int(time.time_ns() // 1_000_000),
        )
        context = HarnessContextCompiler().compile(
            attempt,
            profile,
            token_budget=8_000,
        )
        context_object_digest = harness_context_object_digest(context)
        manifest = ordivon_harness_manifest()
        assignment = HarnessAssignment(
            assignment_id="assignment:oh3-live-readme:g1",
            task_id=profile.task_id,
            task_revision=1,
            task_attempt_id=attempt.task_attempt_id,
            generation=1,
            target_harness_id=manifest.harness_id,
            harness_manifest_digest=manifest.digest,
            context_object_digest=context_object_digest,
            acceptance_criteria_digest=attempt.acceptance_criteria_digest,
            tool_catalog_digest=catalog.digest,
            workspace_ref=workspace_id,
            source_ref=f"repository:ordivon-host@{source_revision}",
            source_digest=canonical_digest(
                {
                    "sourceRepo": str(source_repo),
                    "sourceRevision": source_revision,
                }
            ),
            prior_artifact_refs=(),
            required_capabilities=("tool_events", "usage"),
            budget={
                "maxModelCalls": args.max_model_calls,
                "maxToolCalls": args.max_tool_calls,
                "maxObservationBytes": 262_144,
            },
            deadline_ms=None,
            created_at_ms=attempt.created_at_ms + 1,
        )
        committed = CommittedHarnessAssignment(
            attempt=attempt,
            attempt_object=_stored("task-attempt-descriptor", attempt.to_dict()),
            manifest=manifest,
            manifest_object=_stored("harness-capability-manifest", manifest.to_dict()),
            assignment=assignment,
            assignment_object=_stored("harness-assignment", assignment.to_dict()),
            task_revision=2,
        )
        compiled_input = OrdivonInputCompiler().compile(committed, context)
        adapter = DeepSeekTurnAdapter(
            DeepSeekSettings.from_secret_file(args.deepseek_secret)
        )
        bridge = RuntimeToolBridge(
            committed,
            harness_run_id=f"harness-run:oh3-live-readme:{time.time_ns()}",
            runtime=runtime,
        )
        result = OrdivonAgentLoop(
            adapter,
            bridge,
            budget=RunBudget(
                args.max_model_calls,
                args.max_tool_calls,
                262_144,
                120_000,
            ),
        ).run(
            harness_run_id=bridge.harness_run_id,
            assignment_id=assignment.assignment_id,
            context_digest=context_object_digest,
            initial_messages=compiled_input.initial_messages,
        )

        observed_heading: str | None = None
        for observation in result.observations:
            if observation.tool_name != "read_workspace" or observation.status != "observed":
                continue
            content = observation.structured_content.get("content")
            if isinstance(content, str):
                observed_heading = _first_heading(content)
                if observed_heading is not None:
                    break
        conclusion_status = result.conclusion.status if result.conclusion is not None else None
        conclusion_summary = result.conclusion.summary if result.conclusion is not None else None
        accepted = (
            result.candidate_completed
            and result.model_calls >= 2
            and result.tool_calls >= 1
            and observed_heading == EXPECTED_HEADING
        )
        return LiveRunEvidence(
            source_revision=source_revision,
            runtime_catalog_digest=catalog.digest,
            assignment_id=assignment.assignment_id,
            context_object_digest=context_object_digest,
            harness_run_id=result.harness_run_id,
            stop_code=result.stop_code.value,
            model_calls=result.model_calls,
            tool_calls=result.tool_calls,
            observation_statuses=tuple(item.status for item in result.observations),
            trace_digest=result.trace.digest,
            conclusion_status=conclusion_status,
            conclusion_summary=conclusion_summary,
            observed_heading=observed_heading,
            accepted=accepted,
            usage=result.usage,
        )
    finally:
        runtime.call_tool(
            "workspace.close",
            {"schemaVersion": 1, "workspaceId": workspace_id, "force": True},
        )


def main() -> int:
    args = parse_args()
    try:
        evidence = run(args)
        if args.evidence_out is not None:
            _write_evidence(args.evidence_out, evidence)
        print(json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if evidence.accepted else 1
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
