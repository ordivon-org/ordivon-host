from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from anc_canonical import JsonValue, canonical_bytes, canonical_digest, loads_strict

from ordivon_host.cognition import BlockKind, CompiledContext, ContextBlock, Freshness
from ordivon_host.harness import (
    CommittedHarnessAssignment,
    HarnessAssignment,
    TaskAttemptDescriptor,
)
from ordivon_host.harness.ordivon import (
    AgentToolDefinition,
    AgentTurnRequest,
    DeepSeekSettings,
    DeepSeekTurnAdapter,
    HarnessContextCompiler,
    HarnessContextRequest,
    OrdivonAgentLoop,
    OrdivonInputCompiler,
    RunBudget,
    RunStopCode,
    ToolObservation,
    harness_context_object_digest,
    ordivon_harness_manifest,
)
from ordivon_host.objects import StoredObject


class _Transport:
    def __init__(self, responses: tuple[dict[str, JsonValue], ...]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []
        self.index = 0

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeoutSeconds": timeout_seconds,
                "maxResponseBytes": max_response_bytes,
            }
        )
        if self.index >= len(self.responses):
            raise AssertionError("fake DeepSeek transport has no response")
        value = self.responses[self.index]
        self.index += 1
        return canonical_bytes(value)


def _stored(kind: str, payload: JsonValue) -> StoredObject:
    envelope: JsonValue = {
        "schemaVersion": 1,
        "kind": kind,
        "payload": payload,
    }
    return StoredObject(canonical_digest(envelope), len(canonical_bytes(envelope)), kind)


def _request_profile() -> HarnessContextRequest:
    return HarnessContextRequest(
        task_id="task:oh3-test",
        objective={
            "summary": "Read README.md and report its first heading.",
            "target": {"kind": "repository-file", "relativePath": "README.md"},
        },
        acceptance_criteria={
            "checks": [
                "The first Markdown heading is reported from a Runtime observation."
            ]
        },
        constraints=("Do not mutate the Workspace.",),
        blocks=(
            ContextBlock(
                block_id="context-block:oh3-test:reference",
                kind=BlockKind.TASK,
                priority=90,
                required=True,
                freshness=Freshness.CURRENT,
                source_digest=canonical_digest({"source": "README.md"}),
                payload={"relativePath": "README.md"},
            ),
        ),
    )


def _attempt(profile: HarnessContextRequest | None = None) -> TaskAttemptDescriptor:
    profile = profile or _request_profile()
    return TaskAttemptDescriptor(
        task_attempt_id="task-attempt:oh3-test:1",
        task_id=profile.task_id,
        started_at_task_revision=1,
        objective_digest=profile.objective_digest,
        acceptance_criteria_digest=profile.acceptance_criteria_digest,
        created_at_ms=1,
    )


def _compiled(
    profile: HarnessContextRequest | None = None,
    attempt: TaskAttemptDescriptor | None = None,
) -> CompiledContext:
    profile = profile or _request_profile()
    attempt = attempt or _attempt(profile)
    return HarnessContextCompiler().compile(attempt, profile, token_budget=4_000)


def _committed(
    context: CompiledContext | None = None,
    attempt: TaskAttemptDescriptor | None = None,
) -> CommittedHarnessAssignment:
    context = context or _compiled()
    attempt = attempt or _attempt()
    manifest = ordivon_harness_manifest()
    assignment = HarnessAssignment(
        assignment_id="assignment:oh3-test:g1",
        task_id=attempt.task_id,
        task_revision=1,
        task_attempt_id=attempt.task_attempt_id,
        generation=1,
        target_harness_id=manifest.harness_id,
        harness_manifest_digest=manifest.digest,
        context_object_digest=harness_context_object_digest(context),
        acceptance_criteria_digest=attempt.acceptance_criteria_digest,
        tool_catalog_digest=canonical_digest({"tools": "oh3-test"}),
        workspace_ref="workspace:oh3-test",
        source_ref="repository:ordivon-host@test",
        source_digest=canonical_digest({"revision": "test"}),
        prior_artifact_refs=(),
        required_capabilities=("tool_events",),
        budget={"maxModelCalls": 4, "maxToolCalls": 4},
        deadline_ms=None,
        created_at_ms=2,
    )
    return CommittedHarnessAssignment(
        attempt=attempt,
        attempt_object=_stored("task-attempt-descriptor", attempt.to_dict()),
        manifest=manifest,
        manifest_object=_stored("harness-capability-manifest", manifest.to_dict()),
        assignment=assignment,
        assignment_object=_stored("harness-assignment", assignment.to_dict()),
        task_revision=2,
    )


def _tool() -> AgentToolDefinition:
    return AgentToolDefinition(
        "read_workspace",
        "Read one file.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"relativePath": {"type": "string"}},
            "required": ["relativePath"],
        },
    )


def _request(messages: tuple[dict[str, JsonValue], ...]) -> AgentTurnRequest:
    context = _compiled()
    committed = _committed(context)
    return AgentTurnRequest(
        harness_run_id="harness-run:oh3-test",
        turn_id="turn:oh3-test:1",
        sequence=1,
        assignment_id=committed.assignment.assignment_id,
        context_digest=committed.assignment.context_object_digest,
        tool_catalog_digest=committed.assignment.tool_catalog_digest,
        messages=messages,
        tools=(_tool(),),
        remaining_budget={"modelCalls": 4, "toolCalls": 4},
    )


def _response(
    call_id: str,
    name: str,
    arguments: dict[str, JsonValue],
    *,
    response_id: str,
) -> dict[str, JsonValue]:
    return {
        "id": response_id,
        "created": 1,
        "model": "deepseek-v4-flash",
        "system_fingerprint": "fp-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": canonical_bytes(arguments).decode("utf-8"),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }


class OrdivonHarnessOH3InputTests(unittest.TestCase):
    def test_harness_profile_reuses_shared_compiled_context(self) -> None:
        profile = _request_profile()
        attempt = _attempt(profile)
        context = _compiled(profile, attempt)
        self.assertIsInstance(context, CompiledContext)
        self.assertEqual(context.payload["kind"], "ordivon.harness-compiled-context")
        self.assertEqual(context.payload["taskAttemptId"], attempt.task_attempt_id)
        self.assertNotIn("allowedActions", context.payload)
        self.assertEqual(context.manifest.selected_block_ids, ("context-block:oh3-test:reference",))

    def test_input_compiler_binds_context_object_and_assignment(self) -> None:
        context = _compiled()
        committed = _committed(context)
        compiled = OrdivonInputCompiler().compile(committed, context)
        self.assertEqual(compiled.assignment_id, "assignment:oh3-test:g1")
        self.assertEqual(compiled.context_object_digest, harness_context_object_digest(context))
        self.assertEqual([item["role"] for item in compiled.initial_messages], ["system", "user"])
        user = compiled.initial_messages[1]["content"]
        self.assertIsInstance(user, str)
        payload = loads_strict(user.encode("utf-8"))
        self.assertEqual(payload["assignment"]["workspaceRef"], "workspace:oh3-test")
        self.assertEqual(
            payload["compiledContext"]["payload"]["objective"],
            _request_profile().objective,
        )
        self.assertNotIn("apiKey", user)

    def test_compiler_rejects_attempt_or_context_object_drift(self) -> None:
        profile = _request_profile()
        attempt = _attempt(profile)
        drifted = replace(profile, objective={"summary": "A different objective."})
        with self.assertRaisesRegex(ValueError, "objective"):
            HarnessContextCompiler().compile(attempt, drifted, token_budget=4_000)
        context = _compiled(profile, attempt)
        committed = _committed(context, attempt)
        assignment = replace(
            committed.assignment,
            context_object_digest=canonical_digest({"wrong": "object"}),
        )
        wrong = replace(committed, assignment=assignment)
        with self.assertRaisesRegex(ValueError, "Context object"):
            OrdivonInputCompiler().compile(wrong, context)

    def test_harness_context_object_identity_matches_content_store_envelope(self) -> None:
        context = _compiled()
        stored = _stored("compiled-context", context.to_dict())
        self.assertEqual(stored.digest, harness_context_object_digest(context))
        self.assertEqual(CompiledContext.from_dict(context.to_dict()), context)


class _ObservedReadBridge:
    catalog_digest = canonical_digest({"tools": "oh3-test"})

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return (_tool(),)

    def execute(self, call, *, step_id: str) -> ToolObservation:
        self.last_step_id = step_id
        return ToolObservation(
            tool_call_id=call.tool_call_id,
            tool_name=call.name,
            status="observed",
            structured_content={
                "content": "# Ordivon Host\n",
                "digest": canonical_digest("# Ordivon Host\n"),
            },
        )


class OrdivonHarnessOH3DeepSeekTests(unittest.TestCase):
    def test_deepseek_adapter_drives_two_turn_agent_loop(self) -> None:
        transport = _Transport(
            (
                _response(
                    "call-read-loop",
                    "read_workspace",
                    {"relativePath": "README.md"},
                    response_id="chatcmpl-oh3-loop-1",
                ),
                _response(
                    "call-conclusion-loop",
                    "submit_run_conclusion",
                    {
                        "status": "candidate_completed",
                        "summary": "The observed first heading is Ordivon Host.",
                        "artifact_refs": [],
                        "evidence_refs": [],
                        "unresolved_unknowns": [],
                    },
                    response_id="chatcmpl-oh3-loop-2",
                ),
            )
        )
        adapter = DeepSeekTurnAdapter(
            DeepSeekSettings(api_key="sk-" + "z" * 40),
            transport=transport,
        )
        context = _compiled()
        committed = _committed(context)
        compiled = OrdivonInputCompiler().compile(committed, context)
        result = OrdivonAgentLoop(
            adapter,
            _ObservedReadBridge(),
            budget=RunBudget(4, 4, 65_536, 30_000),
        ).run(
            harness_run_id="harness-run:oh3-loop-test",
            assignment_id=committed.assignment.assignment_id,
            context_digest=committed.assignment.context_object_digest,
            initial_messages=compiled.initial_messages,
        )
        self.assertEqual(result.stop_code, RunStopCode.CANDIDATE_COMPLETED)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(transport.calls), 2)

    def test_first_turn_uses_required_tools_and_non_thinking_mode(self) -> None:
        transport = _Transport(
            (
                _response(
                    "call-read-1",
                    "read_workspace",
                    {"relativePath": "README.md"},
                    response_id="chatcmpl-oh3-1",
                ),
            )
        )
        api_key = "sk-" + "a" * 40
        settings = DeepSeekSettings(api_key=api_key)
        adapter = DeepSeekTurnAdapter(settings, transport=transport)
        context = _compiled()
        compiled = OrdivonInputCompiler().compile(_committed(context), context)
        result = adapter.invoke(_request(compiled.initial_messages))
        self.assertEqual(result.model_id, "deepseek-v4-flash")
        self.assertEqual(result.tool_calls[0].name, "read_workspace")
        self.assertIsNone(result.conclusion)
        raw_body = transport.calls[0]["body"]
        self.assertIsInstance(raw_body, bytes)
        body = loads_strict(raw_body)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["tool_choice"], "required")
        self.assertFalse(body["stream"])
        self.assertNotIn(api_key, raw_body.decode("utf-8"))
        self.assertNotIn(api_key, repr(adapter.settings))
        names = [item["function"]["name"] for item in body["tools"]]
        self.assertEqual(names, ["read_workspace", "submit_run_conclusion"])
        headers = transport.calls[0]["headers"]
        self.assertIsInstance(headers, dict)
        self.assertTrue(headers["Authorization"].startswith("Bearer sk-"))
        self.assertNotIn("sk-", transport.calls[0]["url"])

    def test_second_turn_translates_tool_history_and_parses_conclusion(self) -> None:
        transport = _Transport(
            (
                _response(
                    "call-conclusion-1",
                    "submit_run_conclusion",
                    {
                        "status": "candidate_completed",
                        "summary": "README heading observed as Ordivon Host.",
                        "artifact_refs": [],
                        "evidence_refs": ["observation:readme-heading"],
                        "unresolved_unknowns": [],
                    },
                    response_id="chatcmpl-oh3-2",
                ),
            )
        )
        adapter = DeepSeekTurnAdapter(
            DeepSeekSettings(api_key="sk-" + "b" * 40),
            transport=transport,
        )
        messages: tuple[dict[str, JsonValue], ...] = (
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
            {
                "role": "assistant",
                "content": None,
                "toolCalls": [
                    {
                        "toolCallId": "call-read-1",
                        "name": "read_workspace",
                        "arguments": {"relativePath": "README.md"},
                    }
                ],
            },
            {
                "role": "tool",
                "toolCallId": "call-read-1",
                "name": "read_workspace",
                "observation": {
                    "status": "observed",
                    "content": {"content": "# Ordivon Host"},
                    "runtimeJobRef": None,
                    "artifactRefs": [],
                    "reconciled": False,
                },
            },
        )
        result = adapter.invoke(_request(messages))
        self.assertIsNotNone(result.conclusion)
        assert result.conclusion is not None
        self.assertEqual(result.conclusion.status, "candidate_completed")
        raw_body = transport.calls[0]["body"]
        self.assertIsInstance(raw_body, bytes)
        body = loads_strict(raw_body)
        assistant = body["messages"][2]
        tool = body["messages"][3]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-read-1")
        self.assertEqual(tool["tool_call_id"], "call-read-1")
        observation = loads_strict(tool["content"].encode("utf-8"))
        self.assertEqual(observation["status"], "observed")

    def test_mixed_runtime_and_conclusion_calls_fail_closed(self) -> None:
        mixed = _response(
            "call-read-1",
            "read_workspace",
            {"relativePath": "README.md"},
            response_id="chatcmpl-oh3-mixed",
        )
        calls = mixed["choices"][0]["message"]["tool_calls"]
        assert isinstance(calls, list)
        calls.append(
            {
                "id": "call-conclusion-1",
                "type": "function",
                "function": {
                    "name": "submit_run_conclusion",
                    "arguments": canonical_bytes(
                        {
                            "status": "candidate_completed",
                            "summary": "mixed",
                            "artifact_refs": [],
                            "evidence_refs": [],
                            "unresolved_unknowns": [],
                        }
                    ).decode("utf-8"),
                },
            }
        )
        adapter = DeepSeekTurnAdapter(
            DeepSeekSettings(api_key="sk-" + "c" * 40),
            transport=_Transport((mixed,)),
        )
        with self.assertRaisesRegex(ValueError, "mixed"):
            adapter.invoke(_request(({"role": "user", "content": "test"},)))

    def test_secret_loader_requires_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deepseek.json"
            value: JsonValue = {
                "schemaVersion": 1,
                "provider": "deepseek",
                "apiKey": "sk-" + "d" * 40,
                "baseUrl": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            }
            path.write_bytes(canonical_bytes(value))
            path.chmod(0o600)
            settings = DeepSeekSettings.from_secret_file(path)
            self.assertEqual(settings.model, "deepseek-v4-flash")
            path.chmod(0o640)
            with self.assertRaises(PermissionError):
                DeepSeekSettings.from_secret_file(path)


if __name__ == "__main__":
    unittest.main()
