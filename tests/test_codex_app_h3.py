from __future__ import annotations

import itertools
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from anc_canonical import canonical_digest

from ordivon_host import EventKind, HarnessHost, HostKernel, HostStorage
from ordivon_host.harness import (
    CodexAppServerDriver,
    CodexAppServerProtocolError,
    CodexAppTurnResult,
)

TASK_ID = "task:codex-app-h3"
GOAL_ID = "goal:codex-app-h3"
OBJECTIVE = canonical_digest({"objective": "exercise Codex App Server"})
ACCEPTANCE = canonical_digest({"acceptance": "provider lifecycle retained"})
TOOL_CATALOG = canonical_digest({"tools": ["codex.commandExecution"]})


def _write_fake_server(root: Path) -> Path:
    path = root / "fake_codex_app_server.py"
    path.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time

            thread_id = "019fb600-0000-7000-8000-000000000001"
            session_id = "019fb600-0000-7000-8000-000000000002"
            turn_id = "019fb600-0000-7000-8000-000000000003"
            waiting_interrupt = False

            def emit(value):
                sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
                sys.stdout.flush()

            for line in sys.stdin:
                message = json.loads(line)
                method = message.get("method")
                request_id = message.get("id")
                if method == "initialize":
                    emit({
                        "id": request_id,
                        "result": {
                            "userAgent": "fake-codex/0.145.0",
                            "codexHome": "/tmp/fake-codex",
                            "platformFamily": "unix",
                            "platformOs": "linux"
                        }
                    })
                elif method == "initialized":
                    emit({
                        "method": "remoteControl/status/changed",
                        "params": {"status": "disabled"},
                        "emittedAtMs": 1000
                    })
                elif method == "thread/start":
                    thread = {
                        "id": thread_id,
                        "sessionId": session_id,
                        "ephemeral": True,
                        "cliVersion": "0.145.0"
                    }
                    emit({
                        "id": request_id,
                        "result": {
                            "thread": thread,
                            "model": "fake-model",
                            "modelProvider": "fake-provider",
                            "cwd": "/tmp/fake-workspace",
                            "approvalPolicy": "never",
                            "sandbox": {"type": "readOnly", "networkAccess": False}
                        }
                    })
                    emit({
                        "method": "thread/started",
                        "params": {"thread": thread},
                        "emittedAtMs": 1100
                    })
                elif method == "turn/start":
                    prompt = message["params"]["input"][0]["text"]
                    emit({
                        "id": request_id,
                        "result": {
                            "turn": {
                                "id": turn_id,
                                "status": "inProgress",
                                "items": [],
                                "error": None
                            }
                        }
                    })
                    emit({
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "inProgress"}
                        },
                        "emittedAtMs": 1200
                    })
                    if "SERVER_REQUEST" in prompt:
                        emit({
                            "id": 900,
                            "method": "item/commandExecution/requestApproval",
                            "params": {"threadId": thread_id, "turnId": turn_id}
                        })
                    elif "WAIT_FOR_INTERRUPT" in prompt:
                        waiting_interrupt = True
                    else:
                        item = {
                            "type": "commandExecution",
                            "id": "item:command:1",
                            "command": "sed -n '1,20p' target.py",
                            "cwd": "/tmp/fake-workspace",
                            "status": "inProgress",
                            "exitCode": None,
                            "durationMs": None
                        }
                        emit({
                            "method": "item/started",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": item
                            },
                            "emittedAtMs": 1300
                        })
                        emit({
                            "method": "item/commandExecution/outputDelta",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "itemId": item["id"],
                                "delta": "def target(): pass\\n"
                            },
                            "emittedAtMs": 1350
                        })
                        item["status"] = "completed"
                        item["exitCode"] = 0
                        item["durationMs"] = 7
                        emit({
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": item
                            },
                            "emittedAtMs": 1400
                        })
                        emit({
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "itemId": "item:message:1",
                                "delta": "H3"
                            },
                            "emittedAtMs": 1450
                        })
                        emit({
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "type": "agentMessage",
                                    "id": "item:message:1",
                                    "text": "H3_RESULT",
                                    "phase": "final_answer"
                                }
                            },
                            "emittedAtMs": 1500
                        })
                        emit({
                            "method": "thread/tokenUsage/updated",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "tokenUsage": {
                                    "total": {
                                        "totalTokens": 21,
                                        "inputTokens": 13,
                                        "cachedInputTokens": 0,
                                        "cacheWriteInputTokens": 0,
                                        "outputTokens": 8,
                                        "reasoningOutputTokens": 0
                                    },
                                    "last": {
                                        "totalTokens": 21,
                                        "inputTokens": 13,
                                        "cachedInputTokens": 0,
                                        "cacheWriteInputTokens": 0,
                                        "outputTokens": 8,
                                        "reasoningOutputTokens": 0
                                    },
                                    "modelContextWindow": 1000
                                }
                            },
                            "emittedAtMs": 1550
                        })
                        emit({
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "completed",
                                    "startedAt": 1,
                                    "completedAt": 2,
                                    "durationMs": 800
                                }
                            },
                            "emittedAtMs": 2000
                        })
                elif method == "turn/interrupt":
                    emit({"id": request_id, "result": {}})
                    if waiting_interrupt:
                        emit({
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "interrupted",
                                    "startedAt": 1,
                                    "completedAt": 3,
                                    "durationMs": 1800
                                }
                            },
                            "emittedAtMs": 3000
                        })
                        waiting_interrupt = False
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
        owner_id="host:codex-app-h3-task-create",
    ).create_task(
        event_id="event:codex-app-h3:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "codex-app-h3-fixture"},
        frontier=("node:codex-app-h3:run",),
    )
    context = storage.put_object(
        {"schemaVersion": 1, "kind": "codex-app-h3-context"},
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
            "interrupt",
            "tool_events",
        ),
    )


class CodexAppServerH3Tests(unittest.TestCase):
    def test_manifest_is_provider_specific_and_conservative(self) -> None:
        driver = CodexAppServerDriver(
            working_directory="/tmp/codex-app-h3-manifest",
            executable=sys.executable,
            app_server_args=("-c", "pass"),
        )
        manifest = driver.manifest()
        self.assertEqual(manifest.harness_id, "harness:codex-app-server")
        self.assertEqual(manifest.protocol, "codex-app-server-v2-stdio")
        self.assertTrue(manifest.persistent_session)
        self.assertTrue(manifest.session_resume)
        self.assertTrue(manifest.session_fork)
        self.assertTrue(manifest.interrupt)
        self.assertTrue(manifest.tool_events)
        self.assertTrue(manifest.usage)
        self.assertFalse(manifest.checkpoint)
        self.assertFalse(manifest.local_subagents)

    def test_run_turn_retains_provider_lifecycle_and_builds_harness_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            driver = CodexAppServerDriver(
                working_directory=root,
                executable=sys.executable,
                app_server_args=(str(fake),),
                timeout_seconds=5,
            )
            with driver:
                result = driver.run_turn("READ_TARGET")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.assistant_text, "H3_RESULT")
            self.assertEqual(result.thread.cli_version, "0.145.0")
            self.assertEqual(result.thread.session_id, "019fb600-0000-7000-8000-000000000002")
            self.assertEqual(result.provider_method_counts["turn/completed"], 1)
            self.assertEqual(result.item_type_counts["commandExecution"], 2)
            self.assertEqual(result.item_type_counts["agentMessage"], 1)
            self.assertEqual(len(result.tool_items), 1)
            self.assertEqual(result.tool_items[0]["command"], "sed -n '1,20p' target.py")
            self.assertEqual(result.tool_items[0]["exitCode"], 0)
            self.assertEqual(
                [event.kind for event in result.normalized_events],
                [
                    "run_started",
                    "tool_started",
                    "tool_finished",
                    "message_delta",
                    "usage_observed",
                    "run_stopped",
                ],
            )
            self.assertEqual(result.usage["total"]["totalTokens"], 21)
            self.assertTrue(result.raw_message_digest.startswith("sha256:"))
            self.assertGreater(result.raw_message_count, 6)
            self.assertEqual(
                CodexAppTurnResult.from_dict(result.to_dict()).to_dict(),
                result.to_dict(),
            )

            clock = itertools.count(10_000).__next__
            with HostStorage(root / "host-state") as storage:
                committed = _create_assignment(storage, clock, driver.manifest())
                receipt = result.to_harness_run_receipt(
                    committed,
                    harness_run_id="harness-run:codex-app-h3:1",
                    runtime_job_refs=("job:codex-app-h3",),
                )
                self.assertEqual(receipt.assignment_generation, 1)
                self.assertEqual(receipt.session_ref, f"codex-thread:{result.thread.thread_id}")
                self.assertEqual(receipt.event_digest, result.raw_message_digest)
                self.assertEqual(receipt.runtime_job_refs, ("job:codex-app-h3",))
                self.assertEqual(receipt.usage["toolItemCount"], 1)

    def test_interrupt_maps_to_turn_interrupt_and_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            with CodexAppServerDriver(
                working_directory=root,
                executable=sys.executable,
                app_server_args=(str(fake),),
                timeout_seconds=5,
            ) as driver:
                thread = driver.start_thread()
                handle = driver.start_turn(thread, "WAIT_FOR_INTERRUPT")
                driver.interrupt(thread.thread_id, handle.turn_id)
                result = driver.wait_turn(handle)
            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.stop_reason, "interrupted")
            self.assertEqual(result.finished_at_ms, 3000)

    def test_unexpected_server_request_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = _write_fake_server(root)
            with CodexAppServerDriver(
                working_directory=root,
                executable=sys.executable,
                app_server_args=(str(fake),),
                timeout_seconds=5,
            ) as driver:
                thread = driver.start_thread()
                handle = driver.start_turn(thread, "SERVER_REQUEST")
                with self.assertRaisesRegex(
                    CodexAppServerProtocolError,
                    "unexpected Codex server request",
                ):
                    driver.wait_turn(handle)


if __name__ == "__main__":
    unittest.main()
