from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Protocol

from anc_canonical import JsonValue, canonical_text

from .context import CompiledContext, DecisionKind
from .decision import ModelDecision


class ModelAdapterError(RuntimeError):
    pass


class ModelAdapter(Protocol):
    adapter_id: str

    def decide(self, context: CompiledContext) -> ModelDecision: ...


class ScriptedPreferenceAdapter:
    adapter_id = "scripted-preference-v1"

    def __init__(self, preferred_kinds: tuple[DecisionKind, ...]) -> None:
        if not preferred_kinds:
            raise ValueError("scripted adapter requires at least one preferred action kind")
        self.preferred_kinds = preferred_kinds

    def decide(self, context: CompiledContext) -> ModelDecision:
        raw_actions = context.payload.get("allowedActions")
        if not isinstance(raw_actions, list) or len(raw_actions) < 2:
            raise ModelAdapterError("scripted adapter requires multiple allowed actions")
        for preferred in self.preferred_kinds:
            for raw in raw_actions:
                if isinstance(raw, dict) and raw.get("kind") == preferred.value:
                    return _decision_from_action(
                        context,
                        raw,
                        rationale=f"Selected preferred action kind {preferred.value}.",
                    )
        raise ModelAdapterError("no candidate matches the scripted preference order")


class CodexCliModelAdapter:
    def __init__(
        self,
        *,
        working_directory: str | Path,
        timeout_seconds: int = 180,
        model: str | None = None,
        executable: str = "codex",
    ) -> None:
        self.working_directory = Path(working_directory)
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.executable = executable
        self.adapter_id = f"codex-cli-ephemeral-v2:{model or 'configured'}"
        self._last_evidence: dict[str, JsonValue] | None = None

    def evidence_metadata(self) -> dict[str, JsonValue] | None:
        return None if self._last_evidence is None else dict(self._last_evidence)

    def decide(self, context: CompiledContext) -> ModelDecision:
        self.working_directory.mkdir(parents=True, exist_ok=True)
        prompt = _decision_prompt(context)
        with tempfile.TemporaryDirectory(prefix="ordivon-codex-decision-") as temporary:
            root = Path(temporary)
            schema_path = root / "decision.schema.json"
            output_path = root / "decision.json"
            schema_path.write_text(
                json.dumps(_decision_schema(), indent=2, sort_keys=True) + "\n"
            )
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--color",
                "never",
                "-C",
                str(self.working_directory),
            ]
            if self.model is not None:
                command.extend(["--model", self.model])
            command.append("-")
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ModelAdapterError("Codex CLI invocation failed") from error
            if completed.returncode != 0:
                raise ModelAdapterError(
                    "Codex CLI decision failed: " + completed.stderr.strip()[-2_000:]
                )
            try:
                value = json.loads(output_path.read_text())
                if not isinstance(value, dict):
                    raise ValueError("decision must be an object")
                decision = ModelDecision.from_dict(value)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ModelAdapterError("Codex CLI returned an invalid decision") from error
            self._last_evidence = {
                "adapter": "codex-cli",
                "model": self.model or "configured",
                "ephemeral": True,
                "sandbox": "read-only",
                "persistentSessionRetained": False,
                "toolsExposedByHost": [],
            }
            return decision


class HermesCliModelAdapter:
    """Run one isolated Hermes cognition turn with no tools, memory, or session state."""

    def __init__(
        self,
        *,
        working_directory: str | Path,
        model: str = "deepseek-v4-pro",
        provider: str = "deepseek",
        base_url: str = "https://api.deepseek.com",
        credential_env_path: str | Path | None = None,
        executable: str = "hermes",
        timeout_seconds: int = 180,
    ) -> None:
        for value, label in (
            (model, "model"),
            (provider, "provider"),
            (base_url, "base URL"),
        ):
            if not value or value != value.strip() or "\n" in value:
                raise ValueError(f"Hermes {label} must be non-empty and single-line")
        if timeout_seconds < 1:
            raise ValueError("Hermes timeout must be positive")
        self.working_directory = Path(working_directory)
        self.model = model
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.credential_env_path = Path(
            credential_env_path or Path.home() / ".hermes" / ".env"
        )
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.adapter_id = f"hermes-cli-isolated-v2:{provider}/{model}"
        self._last_evidence: dict[str, JsonValue] | None = None

    def evidence_metadata(self) -> dict[str, JsonValue] | None:
        return None if self._last_evidence is None else dict(self._last_evidence)

    def decide(self, context: CompiledContext) -> ModelDecision:
        self.working_directory.mkdir(parents=True, exist_ok=True)
        prompt = _decision_prompt(context)
        with tempfile.TemporaryDirectory(prefix="ordivon-hermes-decision-") as temporary:
            root = Path(temporary)
            hermes_home = root / "hermes"
            user_home = root / "home"
            hermes_home.mkdir()
            user_home.mkdir()
            self._write_isolated_credentials(hermes_home / ".env")
            self._write_isolated_config(hermes_home / "config.yaml")
            (hermes_home / ".no-bundled-skills").touch()
            usage_path = root / "usage.json"
            command = [
                self.executable,
                "--oneshot",
                prompt,
                "--model",
                self.model,
                "--provider",
                self.provider,
                "--ignore-rules",
                "--usage-file",
                str(usage_path),
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(user_home),
                    "HERMES_HOME": str(hermes_home),
                    "NO_COLOR": "1",
                }
            )
            for name in (
                "HERMES_INFERENCE_MODEL",
                "HERMES_INFERENCE_PROVIDER",
                "HERMES_IGNORE_USER_CONFIG",
                "HERMES_SAFE_MODE",
            ):
                environment.pop(name, None)
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.working_directory,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ModelAdapterError("Hermes CLI invocation failed") from error
            if completed.returncode != 0:
                raise ModelAdapterError(
                    "Hermes CLI decision failed: " + completed.stderr.strip()[-2_000:]
                )
            try:
                value = json.loads(completed.stdout)
                usage = json.loads(usage_path.read_text())
                if not isinstance(value, dict) or not isinstance(usage, dict):
                    raise ValueError("Hermes decision and usage must be objects")
                decision = ModelDecision.from_dict(value)
                self._validate_usage(usage)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ModelAdapterError("Hermes CLI returned invalid structured output") from error
            self._last_evidence = {
                "adapter": "hermes-cli",
                "model": self.model,
                "provider": self.provider,
                "baseUrl": self.base_url,
                "apiCalls": _usage_integer(usage, "api_calls"),
                "inputTokens": _usage_integer(usage, "input_tokens"),
                "outputTokens": _usage_integer(usage, "output_tokens"),
                "reasoningTokens": _usage_integer(usage, "reasoning_tokens"),
                "totalTokens": _usage_integer(usage, "total_tokens"),
                "estimatedCostUsd": _usage_decimal_string(
                    usage, "estimated_cost_usd"
                ),
                "isolatedHome": True,
                "persistentSessionRetained": False,
                "enabledToolsets": [],
                "memoryLoaded": False,
            }
            return decision

    def _write_isolated_credentials(self, destination: Path) -> None:
        try:
            lines = self.credential_env_path.read_text().splitlines()
        except OSError as error:
            raise ModelAdapterError("Hermes credential environment file is unavailable") from error
        prefix = self.provider.upper().replace("-", "_")
        key_name = f"{prefix}_API_KEY"
        base_name = f"{prefix}_BASE_URL"
        key_lines = [line for line in lines if line.startswith(key_name + "=")]
        if not any(line.partition("=")[2] for line in key_lines):
            raise ModelAdapterError(f"Hermes credential file has no {key_name}")
        destination.write_text(
            "\n".join((key_lines[-1], f"{base_name}={self.base_url}")) + "\n"
        )
        destination.chmod(0o600)

    def _write_isolated_config(self, destination: Path) -> None:
        destination.write_text(
            "model:\n"
            f"  default: {json.dumps(self.model)}\n"
            f"  provider: {json.dumps(self.provider)}\n"
            f"  base_url: {json.dumps(self.base_url)}\n"
            "platform_toolsets:\n"
            "  cli: []\n"
            "mcp_servers: {}\n"
            "agent:\n"
            "  disabled_toolsets:\n"
            "    - kanban\n"
            "memory:\n"
            "  memory_enabled: false\n"
            "  user_profile_enabled: false\n"
            "  nudge_interval: 0\n"
            "  flush_min_turns: 999999\n"
            "skills:\n"
            "  creation_nudge_interval: 999999\n"
            "sessions:\n"
            "  write_json_snapshots: false\n"
        )
        destination.chmod(0o600)

    def _validate_usage(self, usage: dict[str, Any]) -> None:
        if usage.get("model") != self.model or usage.get("provider") != self.provider:
            raise ValueError("Hermes used another model or provider")
        if usage.get("completed") is not True or usage.get("failed") is not False:
            raise ValueError("Hermes usage reports an incomplete or failed call")
        if _usage_integer(usage, "api_calls") < 1:
            raise ValueError("Hermes usage reports no real model API call")


def _decision_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "contextDigest",
            "actionId",
            "kind",
            "effectId",
            "bindingId",
            "dispatchId",
            "requiredWorldDigest",
            "rationale",
        ],
        "properties": {
            "contextDigest": {"type": "string"},
            "actionId": {"type": "string"},
            "kind": {"enum": [kind.value for kind in DecisionKind]},
            "effectId": {"type": ["string", "null"]},
            "bindingId": {"type": ["string", "null"]},
            "dispatchId": {"type": ["string", "null"]},
            "requiredWorldDigest": {"type": ["string", "null"]},
            "rationale": {"type": "string", "minLength": 1},
        },
    }


def _decision_prompt(context: CompiledContext) -> str:
    return (
        "You are one replaceable cognition turn inside a persistent Agent Host. "
        "The Host, not you, owns Task state and execution. Read only the compiled context. "
        "Choose the action that best advances the stated goal under its constraints. Copy "
        "contextDigest and every selected action identity exactly; do not invent an action. "
        "Return exactly one JSON object with fields contextDigest, actionId, kind, effectId, "
        "bindingId, dispatchId, requiredWorldDigest, rationale. Do not call tools or use "
        "markdown.\n\n"
        f"contextDigest={context.digest}\n"
        + canonical_text(context.payload)
    )


def _usage_integer(usage: dict[str, Any], field: str) -> int:
    value = usage.get(field)
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError(f"Hermes usage field {field} is not a non-negative integer")
    return value


def _usage_decimal_string(
    usage: dict[str, Any],
    field: str,
) -> str | None:
    value = usage.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Hermes usage field {field} is not a decimal value")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(
            f"Hermes usage field {field} is not a decimal value"
        ) from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(
            f"Hermes usage field {field} is not a non-negative finite decimal"
        )
    text = format(decimal.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decision_from_action(
    context: CompiledContext,
    raw: dict[str, object],
    *,
    rationale: str,
) -> ModelDecision:
    return ModelDecision(
        context_digest=context.digest,
        action_id=str(raw.get("actionId")),
        kind=DecisionKind(str(raw.get("kind"))),
        effect_id=raw.get("effectId") if isinstance(raw.get("effectId"), str) else None,
        binding_id=(
            raw.get("bindingId") if isinstance(raw.get("bindingId"), str) else None
        ),
        dispatch_id=(
            raw.get("dispatchId") if isinstance(raw.get("dispatchId"), str) else None
        ),
        required_world_digest=(
            raw.get("requiredWorldDigest")
            if isinstance(raw.get("requiredWorldDigest"), str)
            else None
        ),
        rationale=rationale,
    )
