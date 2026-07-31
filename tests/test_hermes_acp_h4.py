from __future__ import annotations

import itertools
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from anc_canonical import canonical_digest

from ordivon_host import EventKind, HarnessHost, HostKernel, HostStorage
from ordivon_host.harness.hermes_acp import (
    HermesACPDriver,
    HermesACPPromptResult,
    HermesACPProtocolError,
)

TASK_ID = "task:hermes-acp-h4"
GOAL_ID = "goal:hermes-acp-h4"
OBJECTIVE = canonical_digest({"objective": "exercise Hermes ACP"})
ACCEPTANCE = canonical_digest({"acceptance": "provider lifecycle retained"})
TOOL_CATALOG = canonical_digest({"tools": ["hermes.read_file"]})
_PRIVATE_THOUGHT = "PRIVATE_REASONING_SHOULD_NOT_PERSIST"


def _write_fake_server(root: Path) -> Path:
    path = root / "fake_hermes_acp.py"
    path.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys

            session_id = "11111111-2222-4333-8444-555555555555"
            waiting_prompt_id = None

            def emit(value):
                sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
                sys.stdout.flush()

            def update(value):
                emit({{
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {{"sessionId": session_id, "update": value}}
                }})

            for line in sys.stdin:
                message = json.loads(line)
                method = message.get("method")
                request_id = message.get("id")
                if method == "initialize":
                    emit({{
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {{
                            "protocolVersion": 1,
                            "agentInfo": {{"name": "hermes-agent", "version": "0.18.0"}},
                            "agentCapabilities": {{
                                "loadSession": True,
                                "promptCapabilities": {{"image": True}},
                                "sessionCapabilities": {{"fork": {{}}, "list": {{}}, "resume": {{}}}}
                            }},
                            "authMethods": []
                        }}
                    }})
                elif method == "session/new":
                    emit({{
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {{
                            "sessionId": session_id,
                            "models": {{
                                "currentModelId": "deepseek:deepseek-v4-pro",
                                "availableModels": [{{
                                    "modelId": "deepseek:deepseek-v4-pro",
                                    "name": "deepseek-v4-pro"
                                }}]
                            }},
                            "modes": {{"currentModeId": "default", "availableModes": []}},
                            "_meta": {{
                                "hermes": {{
                                    "sessionProvenance": {{
                                        "acpSessionId": session_id,
                                        "currentHermesSessionId": session_id,
                                        "rootHermesSessionId": session_id,
                                        "parentHermesSessionId": None,
                                        "sessionKind": "root",
                                        "compressionDepth": 0
                                    }}
                                }}
                            }}
                        }}
                    }})
                elif method == "session/prompt":
                    prompt = message["params"]["prompt"][0]["text"]
                    if "WAIT_FOR_CANCEL" in prompt:
                        waiting_prompt_id = request_id
                    elif "SERVER_REQUEST" in prompt:
                        emit({{
                            "jsonrpc": "2.0",
                            "id": 900,
                            "method": "session/request_permission",
                            "params": {{
                                "sessionId": session_id,
                                "options": [],
                                "toolCall": {{"toolCallId": "permission:1"}}
                            }}
                        }})
                    else:
                        update({{
                            "sessionUpdate": "available_commands_update",
                            "availableCommands": []
                        }})
                        update({{
                            "sessionUpdate": "usage_update",
                            "size": 100000,
                            "used": 1200
                        }})
                        update({{
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {{"type": "text", "text": "{_PRIVATE_THOUGHT}"}}
                        }})
                        update({{
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tool:read:1",
                            "title": "read: target.py",
                            "kind": "read",
                            "status": "in_progress",
                            "locations": [{{"path": "target.py", "line": 1}}],
                            "rawInput": {{"path": "target.py"}}
                        }})
                        update({{
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "tool:read:1",
                            "kind": "read",
                            "status": "completed",
                            "content": [{{
                                "type": "content",
                                "content": {{"type": "text", "text": "def target(): pass"}}
                            }}],
                            "rawOutput": "def target(): pass"
                        }})
                        update({{
                            "sessionUpdate": "agent_message_chunk",
                            "content": {{"type": "text", "text": "H4_"}}
                        }})
                        update({{
                            "sessionUpdate": "agent_message_chunk",
                            "content": {{"type": "text", "text": "RESULT"}}
                        }})
                        update({{
                            "sessionUpdate": "usage_update",
                            "size": 100000,
                            "used": 1900
                        }})
                        emit({{
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {{
                                "stopReason": "end_turn",
                                "usage": {{
                                    "inputTokens": 31,
                                    "outputTokens": 7,
                                    "totalTokens": 38,
                                    "thoughtTokens": 3,
                                    "cachedReadTokens": 17
                                }}
                            }}
                        }})
                elif method == "session/cancel":
                    if waiting_prompt_id is not None:
                        emit({{
                            "jsonrpc": "2.0",
                            "id": waiting_prompt_id,
                            "result": {{
                                "stopReason": "cancelled",
                                "usage": {{
                                    "inputTokens": 2,
                                    "outputTokens": 0,
                                    "totalTokens": 2
                                }}
                            }}
                        }})
                        waiting_prompt_id = None
                elif request_id == 900 and "error" in message:
                    break
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _create_assignment(storage: HostStorage, clock, manifest):
    HostKernel(
        storage,
        clock_ms=clock,
        owner_id="host:hermes-acp-h4-task-create",
    ).create_task(
        event_id="event:hermes-acp-h4:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "hermes-acp-h4-fixture"},
        frontier=("node:hermes-acp-h4:run",),
    )
    context = storage.put_object(
        {"schemaVersion": 1, "kind": "hermes-acp-h4-context"},
        kind="compiled-context",
    )
    host = HarnessHost(storage, clock_ms=clock)
    attempt = host.start_attempt(
        TASK_ID,
        objective_digest=OBJECTIVE,
        acceptance_criteria_digest=ACCEPTANCE,
    )
    return host.assign(
        attempt,
        manifest=manifest,
        context_object_digest=context.digest,
        tool_catalog_digest=TOOL_CATALOG,
        required_capabilities=(
            "persistent_session",
            "session_resume",
            "session_fork",
            "interrupt",
            "tool_events",
            "usage",
        ),
    )


class HermesACPH4Tests(unittest.TestCase):
    def test_manifest_is_provider_specific_and_conservative(self) -> None:
        driver = HermesACPDriver(
            working_directory="/tmp/hermes-acp-h4-manifest",
            executable=sys.executable,
            acp_args=("-c", "pass"),
        )
        manifest = driver.manifest()
        self.assertEqual(manifest.harness_id, "harness:hermes-acp")
        self.assertEqual(manifest.protocol, "agent-client-protocol-jsonrpc-stdio")
        self.assertTrue(manifest.persistent_session)
        self.assertTrue(manifest.session_resume)
        self.assertTrue(manifest.session_fork)
        self.assertTrue(manifest.interrupt)
        self.assertTrue(manifest.tool_events)
        self.assertTrue(manifest.approval_events)
        self.assertTrue(manifest.usage)
        self.assertTrue(manifest.images)
        self.assertFalse(manifest.compaction)
        self.assertFalse(manifest.checkpoint)
        self.assertFalse(manifest.local_subagents)

    def test_prompt_retains_lifecycle_without_thought_text_and_builds_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            driver = HermesACPDriver(
                working_directory=root,
                executable=sys.executable,
                acp_args=(str(fake),),
                timeout_seconds=5,
                clock_ms=itertools.count(1_000, 10).__next__,
            )
            with driver:
                result = driver.run_prompt("READ_TARGET")
                manifest = driver.manifest()
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(result.provider_stop_reason, "end_turn")
            self.assertEqual(result.assistant_text, "H4_RESULT")
            self.assertEqual(result.session.agent_version, "0.18.0")
            self.assertEqual(result.session.model_provider, "deepseek")
            self.assertEqual(result.session.model, "deepseek-v4-pro")
            self.assertTrue(result.session.load_session)
            self.assertTrue(result.session.session_resume)
            self.assertTrue(result.session.session_fork)
            self.assertEqual(result.thought_event_count, 1)
            self.assertEqual(result.update_type_counts["agent_thought_chunk"], 1)
            self.assertEqual(result.update_type_counts["tool_call"], 1)
            self.assertEqual(result.update_type_counts["tool_call_update"], 1)
            self.assertEqual(result.usage["totalTokens"], 38)
            self.assertEqual(len(result.tool_items), 1)
            tool = result.tool_items[0]
            self.assertEqual(tool["id"], "tool:read:1")
            self.assertEqual(tool["kind"], "read")
            self.assertEqual(tool["status"], "completed")
            self.assertEqual(tool["locations"], [{"path": "target.py", "line": 1}])
            self.assertEqual(tool["fileEditCount"], 0)
            self.assertNotIn("rawInput", tool)
            self.assertNotIn("rawOutput", tool)
            serialized = json.dumps(result.to_dict(), sort_keys=True)
            self.assertNotIn(_PRIVATE_THOUGHT, serialized)
            self.assertNotIn("def target(): pass", serialized)
            self.assertEqual(
                HermesACPPromptResult.from_dict(result.to_dict()).to_dict(),
                result.to_dict(),
            )
            self.assertTrue(result.raw_message_digest.startswith("sha256:"))
            self.assertGreater(result.raw_message_count, 7)
            self.assertEqual(
                [event.kind for event in result.normalized_events],
                [
                    "run_started",
                    "usage_observed",
                    "thought_observed",
                    "tool_started",
                    "tool_finished",
                    "message_delta",
                    "message_delta",
                    "usage_observed",
                    "run_stopped",
                ],
            )

            clock = itertools.count(10_000).__next__
            with HostStorage(root / "host-state") as storage:
                committed = _create_assignment(storage, clock, manifest)
                receipt = result.to_harness_run_receipt(
                    committed,
                    harness_run_id="harness-run:hermes-acp-h4:1",
                    runtime_job_refs=("job:hermes-acp-h4",),
                )
                self.assertEqual(receipt.assignment_generation, 1)
                self.assertEqual(
                    receipt.session_ref,
                    f"hermes-acp-session:{result.session.session_id}",
                )
                self.assertEqual(receipt.event_digest, result.raw_message_digest)
                self.assertEqual(receipt.runtime_job_refs, ("job:hermes-acp-h4",))
                self.assertEqual(receipt.usage["thoughtEventCount"], 1)

    def test_cancel_maps_to_session_cancel_and_interrupted_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            with HermesACPDriver(
                working_directory=root,
                executable=sys.executable,
                acp_args=(str(fake),),
                timeout_seconds=5,
                clock_ms=itertools.count(2_000, 100).__next__,
            ) as driver:
                session = driver.start_session()
                handle = driver.start_prompt(session, "WAIT_FOR_CANCEL")
                driver.cancel(session.session_id)
                result = driver.wait_prompt(handle)
            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.stop_reason, "interrupted")
            self.assertEqual(result.provider_stop_reason, "cancelled")

    def test_unexpected_server_request_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            with HermesACPDriver(
                working_directory=root,
                executable=sys.executable,
                acp_args=(str(fake),),
                timeout_seconds=5,
            ) as driver:
                session = driver.start_session()
                handle = driver.start_prompt(session, "SERVER_REQUEST")
                with self.assertRaisesRegex(
                    HermesACPProtocolError,
                    "unexpected Hermes ACP server request",
                ):
                    driver.wait_prompt(handle)


if __name__ == "__main__":
    unittest.main()
