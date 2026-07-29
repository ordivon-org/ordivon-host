from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from anc_canonical import JsonValue, canonical_text

from .context import CompiledContext
from .proposal import (
    ActionProposal,
    ConsequenceClass,
    ProposalIntent,
    Reversibility,
)


class ProposalAdapterError(RuntimeError):
    pass


class CodexCliProposalAdapter:
    """Run one isolated, tool-free Codex turn that returns an ActionProposal."""

    def __init__(
        self,
        *,
        working_directory: str | Path,
        timeout_seconds: int = 180,
        model: str | None = None,
        executable: str = "/usr/bin/codex",
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("Codex proposal timeout must be positive")
        self.working_directory = Path(working_directory)
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.executable = executable
        self.gateway_id = f"codex-cli-open-proposal-v1:{model or 'configured'}"
        self._last_evidence: dict[str, JsonValue] | None = None

    def evidence_metadata(self) -> dict[str, JsonValue] | None:
        return None if self._last_evidence is None else dict(self._last_evidence)

    def invoke(self, context: CompiledContext) -> ActionProposal:
        self.working_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ordivon-codex-proposal-") as temporary:
            root = Path(temporary)
            schema_path = root / "proposal.schema.json"
            output_path = root / "proposal.json"
            schema_path.write_text(
                json.dumps(_proposal_schema(), indent=2, sort_keys=True) + "\n"
            )
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
                "--color",
                "never",
                "-C",
                str(self.working_directory),
            ]
            if self.model is not None:
                command.extend(["--model", self.model])
            command.append("-")
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    input=_proposal_prompt(context),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ProposalAdapterError("Codex proposal invocation failed") from error
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if completed.returncode != 0:
                raise ProposalAdapterError(
                    "Codex proposal failed: " + completed.stderr.strip()[-2_000:]
                )
            try:
                value = json.loads(output_path.read_text())
                if not isinstance(value, dict):
                    raise ValueError("proposal output must be an object")
                proposal = ActionProposal.from_dict(value)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ProposalAdapterError(
                    "Codex returned an invalid ActionProposal"
                ) from error
            events: list[dict[str, Any]] = []
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
            completed_event = next(
                (event for event in reversed(events) if event.get("type") == "turn.completed"),
                None,
            )
            raw_usage = (
                completed_event.get("usage")
                if isinstance(completed_event, dict)
                else None
            )
            usage = raw_usage if isinstance(raw_usage, dict) else {}
            self._last_evidence = {
                "adapter": "codex-cli-open-proposal",
                "model": self.model or "configured",
                "elapsedMs": elapsed_ms,
                "inputTokens": _non_negative_integer(usage.get("input_tokens")),
                "cachedInputTokens": _non_negative_integer(
                    usage.get("cached_input_tokens")
                ),
                "outputTokens": _non_negative_integer(usage.get("output_tokens")),
                "ephemeral": True,
                "sandbox": "read-only",
                "persistentSessionRetained": False,
                "toolsExposedByHost": [],
            }
            return proposal


def _proposal_prompt(context: CompiledContext) -> str:
    return (
        "You are one replaceable cognition turn inside a persistent coordination and "
        "commitment Host. Use only the compiled Context below. Propose one useful next "
        "action, but do not invent Effect, Binding, Dispatch, authority, Tool request, or "
        "completion identities. For this first open profile, the useful action should be a "
        "private reversible observation of one available repository file when that advances "
        "the Goal. Copy taskId, contextDigest, capabilityProfileId, resourceRef, revision, "
        "and responsible participant facts exactly from Context. Use candidateMethod "
        "workspace.read for a repository file. Return JSON only and satisfy the schema.\n\n"
        f"contextDigest={context.digest}\n"
        + canonical_text(context.payload)
    )


def _proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schemaVersion",
            "kind",
            "proposalId",
            "taskId",
            "contextDigest",
            "intent",
            "target",
            "rationale",
            "preconditions",
            "affectedResourceRefs",
            "affectedParticipantRefs",
            "reversibility",
            "consequenceClass",
            "requestedProfileId",
            "candidateMethod",
            "expectedResult",
            "verificationPlan",
        ],
        "properties": {
            "schemaVersion": {"type": "integer", "const": 1},
            "kind": {"type": "string", "const": "ordivon.action-proposal"},
            "proposalId": {"type": "string", "pattern": "^proposal:"},
            "taskId": {"type": "string", "pattern": "^task:"},
            "contextDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "intent": {"type": "string", "enum": [value.value for value in ProposalIntent]},
            "target": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "resourceRef", "revision", "selector"],
                "properties": {
                    "kind": {"type": "string", "const": "repository-file"},
                    "resourceRef": {"type": "string"},
                    "revision": {"type": "string"},
                    "selector": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["relativePath", "maxBytes"],
                        "properties": {
                            "relativePath": {"type": "string", "minLength": 1},
                            "maxBytes": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4_194_304,
                            },
                        },
                    },
                },
            },
            "rationale": {"type": "string", "minLength": 1},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "affectedResourceRefs": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "affectedParticipantRefs": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reversibility": {"type": "string", "enum": [value.value for value in Reversibility]},
            "consequenceClass": {
                "type": "string",
                "enum": [value.value for value in ConsequenceClass],
            },
            "requestedProfileId": {"type": "string", "pattern": "^profile:"},
            "candidateMethod": {"type": ["string", "null"]},
            "expectedResult": {"type": "string", "minLength": 1},
            "verificationPlan": {"type": "string", "minLength": 1},
        },
    }


def _non_negative_integer(value: object) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ProposalAdapterError("Codex usage contains an invalid token count")
    return value
