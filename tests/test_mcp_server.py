from __future__ import annotations

import http.client
import json
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest import mock

from anc_canonical import canonical_digest
from ordivon_host.continuity import ExternalContinuityHost
from ordivon_host.continuity_models import WorkingCheckpoint, WorkingCheckpointRuntime
from ordivon_host.domain import EventKind, TaskState
from ordivon_host.kernel import HostKernel
from ordivon_host.mcp_server import (
    BearerAuthApp,
    HostMcpSettings,
    _checkpoint_task,
    _host_status,
    _list_host_tasks,
    _observe_task,
    _tool_schema_identity,
    build_mcp_server,
    check_settings,
)
from ordivon_host.testing.mcp_client import McpTestClient
from ordivon_host.testing.mcp_errors import McpToolRejected, McpTransportError
from ordivon_host.storage import HostStorage
from starlette.requests import ClientDisconnect


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _checkpoint(task_id: str, frontier: str) -> dict[str, object]:
    return WorkingCheckpoint(
        task_id=task_id,
        objective="preserve work across external Agent sessions",
        frontier=frontier,
        established=("Host owns semantic continuity",),
        unresolved=("physical truth requires revalidation",),
        rejected=("conversation transcript as authority",),
        constraints=("Runtime and Git remain stronger truth owners",),
        next_actions=("revalidate then continue",),
    ).to_dict()


class HostMcpSettingsTests(unittest.TestCase):
    def test_check_requires_private_long_token_and_initialized_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / "host-mcp.token"
            token_file.write_text("x" * 32)
            token_file.chmod(0o600)
            settings = HostMcpSettings(
                state_root=root / "state",
                token_file=token_file,
                port=_port(),
                public_origin="https://host-mcp.example.test",
            )
            with self.assertRaises(FileNotFoundError):
                check_settings(settings)
            with HostStorage(settings.state_root):
                pass
            result = check_settings(settings)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["tokenFilePrivate"])
            self.assertEqual(
                result["publicEndpoint"], "https://host-mcp.example.test/mcp"
            )
            self.assertNotIn("tokenCharacters", result)
            self.assertNotIn("x" * 32, str(result))

            token_file.chmod(0o644)
            with self.assertRaises(PermissionError):
                check_settings(settings)
            token_file.chmod(0o600)
            token_file.write_text("short")
            with self.assertRaises(ValueError):
                check_settings(settings)

    def test_bind_is_loopback_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            HostMcpSettings(
                state_root=Path("/tmp/state"),
                token_file=Path("/tmp/token"),
                bind_host="0.0.0.0",
            )
        with self.assertRaisesRegex(ValueError, "literal loopback"):
            HostMcpSettings(
                state_root=Path("/tmp/state"),
                token_file=Path("/tmp/token"),
                bind_host="localhost",
            )

    def test_public_origin_is_one_canonical_https_origin(self) -> None:
        valid = HostMcpSettings(
            state_root=Path("/tmp/state"),
            token_file=Path("/tmp/token"),
            public_origin="https://host-mcp.example.test",
        )
        self.assertEqual(valid.public_endpoint, "https://host-mcp.example.test/mcp")
        for value in (
            "http://host-mcp.example.test",
            "https://host-mcp.example.test/",
            "https://host-mcp.example.test/mcp",
            "https://user@host-mcp.example.test",
            "https://host-mcp.example.test?x=1",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "canonical HTTPS origin"
            ):
                HostMcpSettings(
                    state_root=Path("/tmp/state"),
                    token_file=Path("/tmp/token"),
                    public_origin=value,
                )

        with self.assertRaisesRegex(ValueError, "requires public_origin"):
            HostMcpSettings(
                state_root=Path("/tmp/state"),
                token_file=Path("/tmp/token"),
                trust_cf_access=True,
            )

    def test_agent_facing_descriptions_keep_continuity_distinct_from_current_work(self) -> None:
        server = build_mcp_server(
            HostMcpSettings(
                state_root=Path("/tmp/host-mcp-description-test"),
                token_file=Path("/tmp/host-mcp-description-test.token"),
            )
        )
        tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
        self.assertIn("not active-work counts or priority", tools["host.status"].description)
        self.assertIn("Host lifecycle mechanics only", tools["task.observe"].description)
        self.assertIn("continuity inventory, not a current-work or priority surface", tools["task.list"].description)
        self.assertIn("READY means only that Host continuity remains open", tools["task.list"].description)
        self.assertIn("Host semantic continuity only", tools["task.resume"].description)
        self.assertIn("does not admit cross-owner work priority", tools["task.adopt"].description)
        self.assertIn("mutates Host continuity tracking only", tools["task.checkpoint"].description)


class BearerAuthDisconnectTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, inner: Any) -> BearerAuthApp:
        return BearerAuthApp(
            inner,
            "host-mcp-test-token-0123456789abcdef",
            body_limit_bytes=1_048_576,
        )

    async def _scope(self) -> dict[str, Any]:
        return {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer host-mcp-test-token-0123456789abcdef"),
            ],
        }

    async def test_client_disconnect_mid_request_is_swallowed(self) -> None:
        async def inner(scope, receive, send) -> None:
            raise ClientDisconnect()

        app = self._app(inner)
        # Must not raise: a dropped peer is routine behind a flaky tunnel.
        await app(await self._scope(), _noop_receive, _noop_send)

    async def test_client_disconnect_after_auth_is_swallowed(self) -> None:
        async def inner(scope, receive, send) -> None:
            await _noop_receive()
            raise ClientDisconnect()

        app = self._app(inner)
        await app(await self._scope(), _noop_receive, _noop_send)

    async def test_unrelated_exception_still_propagates(self) -> None:
        async def inner(scope, receive, send) -> None:
            raise RuntimeError("boom")

        app = self._app(inner)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            await app(await self._scope(), _noop_receive, _noop_send)

    async def test_unauthorized_still_returns_401(self) -> None:
        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        app = self._app(lambda scope, receive, send: _unreachable())
        await app(
            {
                "type": "http",
                "headers": [(b"authorization", b"Bearer wrong-token")],
            },
            _noop_receive,
            send,
        )
        self.assertTrue(any(m.get("status") == 401 for m in sent))


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


async def _noop_send(message: dict[str, Any]) -> None:
    return None


async def _unreachable() -> None:
    raise AssertionError("inner app must not run for unauthorized request")


class HostMcpTaskDiscoveryTests(unittest.TestCase):
    def test_external_continuity_discovery_is_paginated_and_not_starved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [1_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                oldest = "task:mcp:external-oldest"
                continuity.adopt(
                    task_id=oldest,
                    goal_id="goal:mcp:external-oldest",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=oldest,
                        objective="remain discoverable",
                        frontier="continue",
                    ),
                )
                kernel = HostKernel(storage, clock_ms=clock, owner_id="mcp-list-test")
                for index in range(100):
                    task_id = f"task:mcp:internal-{index:03d}"
                    kernel.create_task(
                        event_id=f"event:mcp:internal-{index:03d}:r1",
                        kind=EventKind.TASK_CREATED,
                        task_id=task_id,
                        goal_id=f"goal:mcp:internal-{index:03d}",
                        payload={"test": True},
                        state=TaskState.READY,
                        frontier=(f"node:mcp:internal-{index:03d}",),
                    )
                for index in range(104):
                    task_id = f"task:mcp:external-{index:03d}"
                    continuity.adopt(
                        task_id=task_id,
                        goal_id=f"goal:mcp:external-{index:03d}",
                        initial_checkpoint=WorkingCheckpoint(
                            task_id=task_id,
                            objective="page external continuity",
                            frontier="continue",
                        ),
                    )

            first = _list_host_tasks(
                state_root, goal_id=None, limit=100
            )
            self.assertEqual(first["schemaVersion"], 2)
            self.assertEqual(first["scope"], "external-continuity")
            self.assertTrue(first["hasMore"])
            self.assertIsInstance(first["nextCursor"], str)
            self.assertTrue(
                all(item["externalContinuity"] for item in first["tasks"])
            )
            self.assertTrue(
                all(
                    item["workloadId"] == "ordivon.host.external-continuity.v1"
                    for item in first["tasks"]
                )
            )
            for item in first["tasks"]:
                summary = item["semanticSummary"]
                self.assertIsInstance(summary, dict)
                self.assertEqual(
                    set(summary),
                    {
                        "objectivePreview",
                        "objectiveTruncated",
                        "frontierPreview",
                        "frontierTruncated",
                        "checkpointRevision",
                        "checkpointDigest",
                        "runtimeNavigationHint",
                    },
                )

            second = _list_host_tasks(
                state_root,
                goal_id=None,
                limit=100,
                cursor=str(first["nextCursor"]),
            )
            self.assertFalse(second["hasMore"])
            self.assertIsNone(second["nextCursor"])
            ids = [
                item["projection"]["taskId"]
                for item in [*first["tasks"], *second["tasks"]]
            ]
            self.assertEqual(len(ids), 105)
            self.assertEqual(len(set(ids)), 105)
            self.assertIn(oldest, ids)
            self.assertFalse(any(task_id.startswith("task:mcp:internal-") for task_id in ids))

    def test_task_list_prefilters_tasks_without_durable_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [5_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id="task:mcp:prefilter-external",
                    goal_id="goal:mcp:prefilter",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:mcp:prefilter-external",
                        objective="remain cheap to discover",
                        frontier="continue",
                    ),
                )
                kernel = HostKernel(storage, clock_ms=clock, owner_id="prefilter-test")
                for index in range(100):
                    kernel.create_task(
                        event_id=f"event:mcp:prefilter-{index:03d}:r1",
                        kind=EventKind.TASK_CREATED,
                        task_id=f"task:mcp:prefilter-internal-{index:03d}",
                        goal_id=f"goal:mcp:prefilter-internal-{index:03d}",
                        payload={"test": True},
                        state=TaskState.READY,
                        frontier=(f"node:mcp:prefilter-{index:03d}",),
                    )

            original = HostStorage.read_task_descriptor
            seen: list[str] = []

            def counted(storage: HostStorage, task_id: str):
                seen.append(task_id)
                return original(storage, task_id)

            with mock.patch.object(HostStorage, "read_task_descriptor", counted):
                page = _list_host_tasks(state_root, goal_id=None, limit=50)
            self.assertEqual(
                [item["projection"]["taskId"] for item in page["tasks"]],
                ["task:mcp:prefilter-external"],
            )
            self.assertEqual(
                seen,
                [
                    "task:mcp:prefilter-external",
                    "task:mcp:prefilter-external",
                ],
            )

    def test_task_list_exposes_bounded_semantic_selection_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [7_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                for task_id, objective, frontier in (
                    ("task:mcp:opaque-a", "audit deployment authority", "inspect receipts"),
                    ("task:mcp:opaque-b", "research adversarial agents", "design deception trial"),
                ):
                    continuity.adopt(
                        task_id=task_id,
                        goal_id="goal:mcp:opaque",
                        initial_checkpoint=WorkingCheckpoint(
                            task_id=task_id,
                            objective=objective,
                            frontier=frontier,
                        ),
                    )

            page = _list_host_tasks(
                state_root, goal_id="goal:mcp:opaque", limit=10
            )
            summaries = {
                item["projection"]["taskId"]: item["semanticSummary"]
                for item in page["tasks"]
            }
            self.assertEqual(
                summaries["task:mcp:opaque-a"]["objectivePreview"],
                "audit deployment authority",
            )
            self.assertEqual(
                summaries["task:mcp:opaque-b"]["frontierPreview"],
                "design deception trial",
            )
            self.assertTrue(
                summaries["task:mcp:opaque-a"]["checkpointDigest"].startswith("sha256:")
            )

    def test_task_list_exposes_runtime_navigation_hint_without_promoting_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [8_500]

            def clock() -> int:
                now[0] += 1
                return now[0]

            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id="task:mcp:navigation-hint",
                    goal_id="goal:mcp:navigation-hint",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:mcp:navigation-hint",
                        objective="retain weak navigation",
                        frontier="continue",
                        runtime=WorkingCheckpointRuntime(
                            workspace_id="ws-navigation-only",
                            relevant_job_ids=(),
                            observed_head_revision=None,
                        ),
                    ),
                )
                continuity.adopt(
                    task_id="task:mcp:no-navigation-hint",
                    goal_id="goal:mcp:navigation-hint",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id="task:mcp:no-navigation-hint",
                        objective="no physical hint",
                        frontier="continue",
                    ),
                )

            page = _list_host_tasks(
                state_root, goal_id="goal:mcp:navigation-hint", limit=10
            )
            summaries = {
                item["projection"]["taskId"]: item["semanticSummary"]
                for item in page["tasks"]
            }
            hint = summaries["task:mcp:navigation-hint"]["runtimeNavigationHint"]
            self.assertEqual(hint["workspaceId"], "ws-navigation-only")
            self.assertEqual(
                hint["truthRole"], "host-retained-runtime-navigation-hint"
            )
            self.assertIn("Runtime currentness", hint["interpretation"])
            self.assertIn("semantic claimant standing", hint["interpretation"])
            self.assertIn("retention or closure", hint["interpretation"])
            self.assertIn("not a Human decision requirement", hint["interpretation"])
            self.assertIsNone(
                summaries["task:mcp:no-navigation-hint"]["runtimeNavigationHint"]
            )

    def test_task_list_hides_terminal_continuity_unless_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [9_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:terminal-history"
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:terminal-history",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="finish continuity tracking",
                        frontier="finish",
                    ),
                )
                continuity.checkpoint(
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="finish continuity tracking",
                        frontier="continuity tracking complete",
                    ),
                    disposition="complete",
                )

            active = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:terminal-history",
                limit=10,
            )
            self.assertEqual(active["tasks"], [])
            historical = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:terminal-history",
                limit=10,
                include_terminal=True,
            )
            self.assertEqual(len(historical["tasks"]), 1)
            self.assertEqual(
                historical["tasks"][0]["projection"]["state"], "completed"
            )

    def test_task_list_semantic_summary_is_revision_coherent_under_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [8_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:list-race"
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:list-race",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="old objective",
                        frontier="old frontier",
                    ),
                )

            original = ExternalContinuityHost.checkpoint_at_revision
            raced = [False]

            def racing_checkpoint_at_revision(
                host: ExternalContinuityHost,
                target_task_id: str,
                revision: int,
            ):
                if not raced[0]:
                    raced[0] = True
                    with HostStorage(state_root) as other_storage:
                        ExternalContinuityHost(other_storage, clock_ms=clock).checkpoint(
                            task_id=task_id,
                            expected_revision=2,
                            checkpoint=WorkingCheckpoint(
                                task_id=task_id,
                                objective="new objective",
                                frontier="new frontier",
                            ),
                        )
                return original(host, target_task_id, revision)

            with mock.patch.object(
                ExternalContinuityHost,
                "checkpoint_at_revision",
                racing_checkpoint_at_revision,
            ):
                page = _list_host_tasks(
                    state_root,
                    goal_id="goal:mcp:list-race",
                    limit=10,
                )

            self.assertEqual(len(page["tasks"]), 1)
            item = page["tasks"][0]
            self.assertEqual(item["projection"]["revision"], 2)
            self.assertEqual(item["semanticSummary"]["checkpointRevision"], 2)
            self.assertEqual(
                item["semanticSummary"]["frontierPreview"], "old frontier"
            )
            with HostStorage(state_root) as storage:
                current = ExternalContinuityHost(
                    storage, clock_ms=clock
                ).resume(task_id)
                self.assertEqual(current.projection.revision, 3)

    def test_task_list_semantic_summary_has_independent_preview_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [9_500]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:preview-budget"
            large = "界" * 1_365
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:preview-budget",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective=large,
                        frontier=large,
                    ),
                )

            page = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:preview-budget",
                limit=10,
            )
            summary = page["tasks"][0]["semanticSummary"]
            self.assertLessEqual(
                len(summary["objectivePreview"].encode("utf-8")), 512
            )
            self.assertLessEqual(
                len(summary["frontierPreview"].encode("utf-8")), 512
            )
            self.assertTrue(summary["objectiveTruncated"])
            self.assertTrue(summary["frontierTruncated"])
            self.assertEqual(summary["checkpointRevision"], 2)
            self.assertTrue(summary["checkpointDigest"].startswith("sha256:"))

    def test_task_list_rechecks_terminal_state_after_initial_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [11_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:terminal-race"
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                continuity.adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:terminal-race",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="close during list",
                        frontier="continue",
                    ),
                )

            from ordivon_host.journal.sqlite import HostJournal

            original = HostJournal.get_task
            fired = [False]

            def racing_get_task(journal: HostJournal, target_task_id: str):
                if target_task_id == task_id and not fired[0]:
                    fired[0] = True
                    with HostStorage(state_root) as other_storage:
                        ExternalContinuityHost(
                            other_storage, clock_ms=clock
                        ).checkpoint(
                            task_id=task_id,
                            expected_revision=2,
                            checkpoint=WorkingCheckpoint(
                                task_id=task_id,
                                objective="close during list",
                                frontier="closed",
                            ),
                            disposition="complete",
                        )
                return original(journal, target_task_id)

            with mock.patch.object(HostJournal, "get_task", racing_get_task):
                active = _list_host_tasks(
                    state_root,
                    goal_id="goal:mcp:terminal-race",
                    limit=10,
                )
            self.assertEqual(active["tasks"], [])
            historical = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:terminal-race",
                limit=10,
                include_terminal=True,
            )
            self.assertEqual(len(historical["tasks"]), 1)
            self.assertEqual(
                historical["tasks"][0]["projection"]["state"], "completed"
            )

    def test_task_list_recovers_seeded_initial_checkpoint_before_revision_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [11_500]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:seeded-discovery"
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                with mock.patch.object(
                    ExternalContinuityHost,
                    "checkpoint",
                    side_effect=RuntimeError("synthetic crash after seeded creation"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                        continuity.adopt(
                            task_id=task_id,
                            goal_id="goal:mcp:seeded-discovery",
                            initial_checkpoint=WorkingCheckpoint(
                                task_id=task_id,
                                objective="seeded objective",
                                frontier="seeded frontier",
                            ),
                        )

            page = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:seeded-discovery",
                limit=10,
            )
            self.assertEqual(len(page["tasks"]), 1)
            item = page["tasks"][0]
            self.assertEqual(item["projection"]["revision"], 1)
            self.assertEqual(item["semanticSummary"]["checkpointRevision"], 1)
            self.assertEqual(
                item["semanticSummary"]["objectivePreview"], "seeded objective"
            )
            self.assertEqual(
                item["semanticSummary"]["frontierPreview"], "seeded frontier"
            )

    def test_task_list_cursor_uses_immutable_creation_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [10_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_ids = [f"task:mcp:cursor-{index}" for index in range(3)]
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                for task_id in task_ids:
                    continuity.adopt(
                        task_id=task_id,
                        goal_id="goal:mcp:cursor",
                        initial_checkpoint=WorkingCheckpoint(
                            task_id=task_id,
                            objective="cursor stability",
                            frontier="initial",
                        ),
                    )

            first = _list_host_tasks(
                state_root, goal_id="goal:mcp:cursor", limit=1
            )
            first_id = first["tasks"][0]["projection"]["taskId"]
            cursor = str(first["nextCursor"])
            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                current = continuity.resume(first_id).projection
                continuity.checkpoint(
                    task_id=first_id,
                    expected_revision=current.revision,
                    checkpoint=WorkingCheckpoint(
                        task_id=first_id,
                        objective="cursor stability",
                        frontier="updated after first page",
                    ),
                )

            second = _list_host_tasks(
                state_root,
                goal_id="goal:mcp:cursor",
                limit=10,
                cursor=cursor,
            )
            remaining = [
                item["projection"]["taskId"] for item in second["tasks"]
            ]
            self.assertEqual(len(remaining), 2)
            self.assertNotIn(first_id, remaining)
            self.assertEqual(set(remaining), set(task_ids) - {first_id})

    def test_task_list_rejects_invalid_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            with HostStorage(state_root):
                pass
            with self.assertRaisesRegex(ValueError, "cursor is invalid") as captured:
                _list_host_tasks(
                    state_root,
                    goal_id=None,
                    limit=10,
                    cursor="not-a-valid-cursor!",
                )
            self.assertEqual(captured.exception.field, "cursor")

    def test_task_list_cursor_is_bound_to_query_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [12_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            with HostStorage(state_root) as storage:
                continuity = ExternalContinuityHost(storage, clock_ms=clock)
                for index in range(3):
                    task_id = f"task:mcp:scope-{index}"
                    continuity.adopt(
                        task_id=task_id,
                        goal_id="goal:mcp:scope-a",
                        initial_checkpoint=WorkingCheckpoint(
                            task_id=task_id, objective="scope a", frontier="continue"
                        ),
                    )

            first = _list_host_tasks(
                state_root, goal_id="goal:mcp:scope-a", limit=1
            )
            cursor = str(first["nextCursor"])
            with self.assertRaisesRegex(
                ValueError, "does not match the current query scope"
            ) as wrong_goal:
                _list_host_tasks(
                    state_root,
                    goal_id="goal:mcp:scope-b",
                    limit=1,
                    cursor=cursor,
                )
            self.assertEqual(wrong_goal.exception.field, "cursor")
            with self.assertRaisesRegex(
                ValueError, "does not match the current query scope"
            ) as wrong_terminal_scope:
                _list_host_tasks(
                    state_root,
                    goal_id="goal:mcp:scope-a",
                    limit=1,
                    cursor=cursor,
                    include_terminal=True,
                )
            self.assertEqual(wrong_terminal_scope.exception.field, "cursor")


class HostMcpAgentUxTests(unittest.TestCase):
    def test_host_status_projects_compact_state_activity_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [1_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:status-observation"
            with HostStorage(state_root) as storage:
                ExternalContinuityHost(storage, clock_ms=clock).adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:status-observation",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="make Host observable",
                        frontier="inspect current status",
                    ),
                )

            summary = _host_status(state_root, detail="summary", recent_limit=5)
            self.assertEqual(summary["kind"], "ordivon.host-status")
            self.assertEqual(summary["detail"], "summary")
            self.assertEqual(summary["interface"]["surfaceVersion"], 3)
            self.assertEqual(summary["interface"]["toolCount"], 8)
            self.assertEqual(
                summary["interface"]["toolNames"],
                [
                    "host.status",
                    "board.list",
                    "board.post",
                    "task.observe",
                    "task.list",
                    "task.resume",
                    "task.adopt",
                    "task.checkpoint",
                ],
            )
            self.assertEqual(
                summary["board"],
                {
                    "messages": 0,
                    "lastSequence": 0,
                    "truthRole": "durable-collaboration-messages",
                },
            )
            self.assertFalse(summary["interface"]["runtimeProxy"])
            self.assertEqual(summary["authority"]["tasks"], 1)
            self.assertEqual(summary["authority"]["tasksByState"]["ready"], 1)
            self.assertEqual(summary["continuity"], {"active": 1, "terminal": 0})
            self.assertEqual(summary["recentActivity"][0]["taskId"], task_id)
            self.assertEqual(summary["recentActivity"][0]["revision"], 2)
            self.assertGreaterEqual(summary["recentActivity"][0]["ageMs"], 0)
            self.assertIn(summary["deployment"]["status"], {"unbound", "unavailable"})
            self.assertIsNone(summary["doctor"])
            self.assertIn("Runtime remains independent", summary["truthBoundary"]["runtime"])

            integrity = _host_status(
                state_root, detail="integrity", recent_limit=1
            )
            self.assertTrue(integrity["doctor"]["healthy"])
            self.assertNotIn(
                "journal.history",
                {check["name"] for check in integrity["doctor"]["checks"]},
            )
            history = _host_status(state_root, detail="history", recent_limit=0)
            self.assertTrue(history["doctor"]["healthy"])
            self.assertIn(
                "journal.history",
                {check["name"] for check in history["doctor"]["checks"]},
            )
            self.assertEqual(history["recentActivity"], [])

    def test_task_observe_is_revision_fenced_and_payload_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [2_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:observe"
            with HostStorage(state_root) as storage:
                ExternalContinuityHost(storage, clock_ms=clock).adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:observe",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="observe without raw payload",
                        frontier="baseline",
                        unresolved=("visibility gap",),
                        next_actions=("inspect timeline",),
                    ),
                )

            observed = _observe_task(
                state_root, task_id=task_id, expected_revision=2, event_limit=5
            )
            self.assertEqual(observed["kind"], "ordivon.host-task-observation")
            self.assertEqual(observed["projection"]["revision"], 2)
            self.assertGreaterEqual(observed["activityAgeMs"], 0)
            self.assertEqual(
                observed["workloadId"], "ordivon.host.external-continuity.v1"
            )
            self.assertTrue(observed["externalContinuity"])
            self.assertIsNone(observed["recovery"])
            self.assertEqual(observed["continuity"]["checkpointRevision"], 2)
            self.assertEqual(observed["continuity"]["unresolved"]["total"], 1)
            self.assertEqual(
                observed["continuity"]["unresolved"]["items"][0]["text"],
                "visibility gap",
            )
            self.assertEqual(observed["continuity"]["nextActions"]["total"], 1)
            self.assertEqual(
                observed["continuity"]["nextActions"]["items"][0]["text"],
                "inspect timeline",
            )
            self.assertEqual(observed["head"]["eventKind"], "task.context-checkpointed")
            self.assertEqual([e["revision"] for e in observed["recentEvents"]], [2, 1])
            self.assertNotIn("data", observed["head"])
            self.assertNotIn("data", str(observed["recentEvents"]))

            with self.assertRaisesRegex(
                Exception, "Task revision is 2, expected 1"
            ):
                _observe_task(
                    state_root,
                    task_id=task_id,
                    expected_revision=1,
                    event_limit=5,
                )

    def test_summary_and_task_reads_do_not_refresh_validation_cache(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [2_500]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:low-disturbance"
            with HostStorage(state_root) as storage:
                ExternalContinuityHost(storage, clock_ms=clock).adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:low-disturbance",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="observe without cache writes",
                        frontier="baseline",
                    ),
                )
            database = state_root / "host.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("DELETE FROM object_validation")
                connection.commit()
                before = connection.execute(
                    "SELECT COUNT(*) FROM object_validation"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(before, 0)

            _host_status(state_root, detail="summary", recent_limit=2)
            _observe_task(
                state_root, task_id=task_id, expected_revision=2, event_limit=2
            )
            _list_host_tasks(state_root, goal_id=None, limit=10)

            connection = sqlite3.connect(database)
            try:
                after = connection.execute(
                    "SELECT COUNT(*) FROM object_validation"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(after, 0)

    def test_checkpoint_patch_inherits_exact_revision_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            now = [3_000]

            def clock() -> int:
                now[0] += 1
                return now[0]

            task_id = "task:mcp:patch"
            with HostStorage(state_root) as storage:
                ExternalContinuityHost(storage, clock_ms=clock).adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:patch",
                    initial_checkpoint=WorkingCheckpoint(
                        task_id=task_id,
                        objective="reduce checkpoint ceremony",
                        frontier="baseline",
                        established=("preserve this fact",),
                        unresolved=("patch not proven",),
                        constraints=("exact revision only",),
                        next_actions=("try patch",),
                    ),
                )

            patch = {
                "frontier": "patch proven",
                "unresolved": [],
                "nextActions": ["continue with smaller requests"],
            }
            first = _checkpoint_task(
                state_root,
                task_id=task_id,
                expected_revision=2,
                checkpoint_value=patch,
            )
            self.assertEqual(first["admission"], "created")
            self.assertEqual(first["projection"]["revision"], 3)
            self.assertEqual(first["checkpoint"]["frontier"], "patch proven")
            self.assertEqual(
                first["checkpoint"]["established"], ["preserve this fact"]
            )
            self.assertEqual(first["checkpoint"]["constraints"], ["exact revision only"])
            self.assertEqual(first["checkpoint"]["unresolved"], [])

            replay = _checkpoint_task(
                state_root,
                task_id=task_id,
                expected_revision=2,
                checkpoint_value=patch,
            )
            self.assertEqual(replay["admission"], "existing")
            self.assertEqual(replay["checkpointDigest"], first["checkpointDigest"])

            with self.assertRaisesRegex(Exception, "Task revision is 3, expected 2"):
                _checkpoint_task(
                    state_root,
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint_value={"frontier": "different stale patch"},
                )

    def test_new_terminal_checkpoint_requires_full_state_but_replay_converges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "state"
            task_id = "task:mcp:terminal-full"
            with HostStorage(state_root) as storage:
                ExternalContinuityHost(storage, clock_ms=lambda: 4_000).adopt(
                    task_id=task_id,
                    goal_id="goal:mcp:terminal-full",
                    initial_checkpoint=WorkingCheckpoint.from_dict(
                        _checkpoint(task_id, "baseline")
                    ),
                )

            with self.assertRaisesRegex(
                Exception,
                "new terminal continuity transition requires a complete WorkingCheckpoint",
            ):
                _checkpoint_task(
                    state_root,
                    task_id=task_id,
                    expected_revision=2,
                    checkpoint_value={"frontier": "final"},
                    disposition="complete",
                )
            with HostStorage(state_root) as storage:
                unchanged = storage.journal.get_task(task_id)
                assert unchanged is not None
                self.assertEqual(unchanged.revision, 2)
                self.assertEqual(unchanged.state, TaskState.READY)

            committed = _checkpoint_task(
                state_root,
                task_id=task_id,
                expected_revision=2,
                checkpoint_value=_checkpoint(task_id, "final"),
                disposition="complete",
            )
            self.assertEqual(committed["admission"], "created")
            self.assertEqual(committed["projection"]["state"], "completed")

            replay = _checkpoint_task(
                state_root,
                task_id=task_id,
                expected_revision=2,
                checkpoint_value={"frontier": "final"},
                disposition="complete",
            )
            self.assertEqual(replay["admission"], "existing")
            self.assertEqual(replay["checkpointDigest"], committed["checkpointDigest"])

    def test_tool_schema_identity_ignores_presentation_but_binds_schema(self) -> None:
        first = [
            {
                "name": "task.example",
                "title": "First title",
                "description": "presentation one",
                "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}},
                "outputSchema": None,
            }
        ]
        presentation_only = [
            {
                **first[0],
                "title": "Different title",
                "description": "presentation two",
            }
        ]
        changed_schema = [
            {
                **first[0],
                "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"}}},
            }
        ]
        first_identity = _tool_schema_identity(first)
        self.assertEqual(first_identity, _tool_schema_identity(presentation_only))
        self.assertNotEqual(
            first_identity["schemaDigest"],
            _tool_schema_identity(changed_schema)["schemaDigest"],
        )
        self.assertEqual(
            first_identity["schemaRevision"],
            f"mcp-schema:{str(first_identity['schemaDigest'])[7:]}",
        )


class HostMcpEndToEndTests(unittest.TestCase):
    def test_modern_mcp_auth_catalog_and_continuity_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            token_file = root / "host-mcp.token"
            token = "host-mcp-test-token-0123456789abcdef"
            token_file.write_text(token)
            token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
            with HostStorage(state_root):
                pass
            port = _port()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ordivon_host.mcp_server",
                    "--state-root",
                    str(state_root),
                    "--token-file",
                    str(token_file),
                    "--port",
                    str(port),
                    "--public-origin",
                    "https://host-mcp.example.test",
                    "--trust-cf-access",
                    "--log-level",
                    "ERROR",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                endpoint = f"http://127.0.0.1:{port}/mcp"
                client = self._wait_for_server(process, endpoint, token)
                legacy = self._legacy_lifecycle(endpoint, token)
                self.assertEqual(legacy["protocolVersion"], "2025-11-25")
                self.assertEqual(
                    {tool["name"] for tool in legacy["tools"]},
                    {
                        "host.status",
                        "board.list",
                        "board.post",
                        "task.observe",
                        "task.list",
                        "task.resume",
                        "task.adopt",
                        "task.checkpoint",
                    },
                )
                discovered = client.initialize()
                self.assertEqual(discovered["protocolVersion"], "2026-07-28")

                external_status, external = self._request_with_host(
                    port, token, "host-mcp.example.test",
                    origin="https://host-mcp.example.test",
                )
                self.assertEqual(external_status, 200)
                self.assertEqual(external["result"]["supportedVersions"], ["2026-07-28"])
                cf_access_status, cf_access = self._request_with_host(
                    port,
                    None,
                    "host-mcp.example.test",
                    origin="https://host-mcp.example.test",
                    cf_access_assertion="signed-access-assertion",
                )
                self.assertEqual(cf_access_status, 200)
                self.assertEqual(
                    cf_access["result"]["supportedVersions"], ["2026-07-28"]
                )
                rejected_host_status, rejected_host = self._request_with_host(
                    port, token, "untrusted.example.test"
                )
                self.assertEqual(rejected_host_status, 421)
                self.assertEqual(rejected_host, "Invalid Host header")
                rejected_origin_status, rejected_origin = self._request_with_host(
                    port, token, "host-mcp.example.test",
                    origin="https://untrusted.example.test",
                )
                self.assertEqual(rejected_origin_status, 403)
                self.assertEqual(rejected_origin, "Invalid Origin header")

                tools = client.list_tools()
                self.assertEqual(
                    {tool["name"] for tool in tools},
                    {
                        "host.status",
                        "board.list",
                        "board.post",
                        "task.observe",
                        "task.list",
                        "task.resume",
                        "task.adopt",
                        "task.checkpoint",
                    },
                )
                for tool in tools:
                    self.assertIsInstance(tool.get("inputSchema"), dict)
                by_name = {tool["name"]: tool for tool in tools}
                schema_descriptors = [
                    {
                        "name": tool["name"],
                        "inputSchema": tool.get("inputSchema"),
                        "outputSchema": tool.get("outputSchema"),
                    }
                    for tool in tools
                ]
                schema_descriptors.sort(key=lambda item: str(item["name"]))
                wire_schema_digest = canonical_digest(schema_descriptors)
                status_schema = by_name["host.status"]["inputSchema"]
                self.assertEqual(
                    status_schema["properties"]["detail"]["enum"],
                    ["summary", "integrity", "history"],
                )
                observe_schema = by_name["task.observe"]["inputSchema"]
                self.assertIn("eventLimit", observe_schema["properties"])
                list_schema = by_name["task.list"]["inputSchema"]
                self.assertIn("cursor", list_schema["properties"])
                self.assertIn("includeTerminal", list_schema["properties"])
                adopt_schema = by_name["task.adopt"]["inputSchema"]
                checkpoint_schema = by_name["task.checkpoint"]["inputSchema"]
                self.assertEqual(
                    checkpoint_schema["properties"]["continuityDisposition"]["enum"],
                    ["continue", "complete", "abandon"],
                )
                update_schema = checkpoint_schema["properties"]["checkpoint"]
                self.assertEqual(len(update_schema["oneOf"]), 2)
                full_schema, patch_schema = update_schema["oneOf"]
                self.assertFalse(full_schema["additionalProperties"])
                self.assertFalse(patch_schema["additionalProperties"])
                self.assertEqual(patch_schema["minProperties"], 1)
                self.assertIn("frontier", patch_schema["properties"])
                self.assertIn("nextActions", patch_schema["properties"])
                for schema, field in ((adopt_schema, "initialCheckpoint"),):
                    definition = schema["properties"][field]
                    self.assertFalse(definition["additionalProperties"])
                    self.assertNotIn("$ref", definition)
                    self.assertNotIn("$defs", definition)
                    self.assertEqual(
                        set(definition["required"]),
                        {
                            "schemaVersion",
                            "kind",
                            "truthRole",
                            "taskId",
                            "objective",
                            "frontier",
                            "established",
                            "unresolved",
                            "rejected",
                            "constraints",
                            "nextActions",
                            "runtime",
                        },
                    )
                    self.assertEqual(
                        definition["properties"]["kind"]["const"],
                        "ordivon.host-working-checkpoint",
                    )
                    self.assertEqual(
                        definition["properties"]["truthRole"]["const"],
                        "semantic-working-claim",
                    )

                board_list_schema = by_name["board.list"]["inputSchema"]
                self.assertIn("afterSequence", board_list_schema["properties"])
                self.assertIn("limit", board_list_schema["properties"])
                board_post_schema = by_name["board.post"]["inputSchema"]
                self.assertEqual(
                    board_post_schema["properties"]["messageKind"]["enum"],
                    ["note", "question", "proposal", "warning", "reply"],
                )
                board_created = client.call_tool(
                    "board.post",
                    {
                        "clientMessageId": "msg:mcp:e2e:first",
                        "authorLabel": "agent:mcp-e2e",
                        "messageKind": "proposal",
                        "topic": "mcp-e2e",
                        "message": "Exercise the durable collaboration surface over MCP.",
                    },
                )
                self.assertEqual(board_created["admission"], "created")
                self.assertEqual(board_created["message"]["sequence"], 1)
                self.assertEqual(
                    board_created["message"]["authorIdentityRole"],
                    "self-asserted-label",
                )
                self.assertEqual(
                    board_created["message"]["truthRole"],
                    "coordination-message-not-domain-truth",
                )
                board_replay = client.call_tool(
                    "board.post",
                    {
                        "clientMessageId": "msg:mcp:e2e:first",
                        "authorLabel": "agent:mcp-e2e",
                        "messageKind": "proposal",
                        "topic": "mcp-e2e",
                        "message": "Exercise the durable collaboration surface over MCP.",
                    },
                )
                self.assertEqual(board_replay["admission"], "existing")
                self.assertEqual(
                    board_replay["message"]["messageDigest"],
                    board_created["message"]["messageDigest"],
                )
                board_listing = client.call_tool(
                    "board.list", {"afterSequence": 0, "limit": 10}
                )
                self.assertEqual(board_listing["messageCount"], 1)
                self.assertEqual(board_listing["lastSequence"], 1)
                self.assertEqual(
                    [item["clientMessageId"] for item in board_listing["messages"]],
                    ["msg:mcp:e2e:first"],
                )

                with self.assertRaises(McpToolRejected) as invalid_checkpoint:
                    client.call_tool(
                        "task.adopt",
                        {
                            "taskId": "task:mcp:invalid-checkpoint",
                            "goalId": "goal:mcp:continuity",
                            "initialCheckpoint": {
                                "taskId": "task:mcp:invalid-checkpoint"
                            },
                        },
                    )
                self.assertEqual(
                    invalid_checkpoint.exception.detail.code, "INVALID_ARGUMENT"
                )
                self.assertEqual(
                    invalid_checkpoint.exception.detail.commit_state, "not_committed"
                )
                self.assertEqual(
                    invalid_checkpoint.exception.detail.field, "initialCheckpoint"
                )
                self.assertEqual(
                    invalid_checkpoint.exception.detail.retry_class, "fix_request"
                )
                self.assertEqual(
                    invalid_checkpoint.exception.detail.origin, "host-mcp"
                )
                mismatch = _checkpoint(
                    "task:mcp:checkpoint-inner-other", "mismatched inner Task"
                )
                with self.assertRaises(McpToolRejected) as mismatched_checkpoint:
                    client.call_tool(
                        "task.adopt",
                        {
                            "taskId": "task:mcp:checkpoint-outer",
                            "goalId": "goal:mcp:continuity",
                            "initialCheckpoint": mismatch,
                        },
                    )
                self.assertEqual(
                    mismatched_checkpoint.exception.detail.code, "INVALID_ARGUMENT"
                )
                self.assertEqual(
                    mismatched_checkpoint.exception.detail.field,
                    "initialCheckpoint.taskId",
                )
                self.assertEqual(
                    mismatched_checkpoint.exception.detail.commit_state,
                    "not_committed",
                )
                valid_for_bad_disposition = _checkpoint(
                    "task:mcp:bad-disposition", "bad disposition"
                )
                client.call_tool(
                    "task.adopt",
                    {
                        "taskId": "task:mcp:bad-disposition",
                        "goalId": "goal:mcp:continuity",
                        "initialCheckpoint": valid_for_bad_disposition,
                    },
                )
                with self.assertRaises(McpToolRejected) as bad_disposition:
                    client.call_tool(
                        "task.checkpoint",
                        {
                            "taskId": "task:mcp:bad-disposition",
                            "expectedRevision": 2,
                            "checkpoint": _checkpoint(
                                "task:mcp:bad-disposition", "still valid checkpoint"
                            ),
                            "continuityDisposition": "domain-success",
                        },
                    )
                self.assertEqual(
                    bad_disposition.exception.detail.code, "INVALID_ARGUMENT"
                )
                self.assertEqual(
                    bad_disposition.exception.detail.field, "continuityDisposition"
                )
                self.assertEqual(
                    bad_disposition.exception.detail.commit_state, "not_committed"
                )

                request = urllib.request.Request(
                    endpoint,
                    data=b'{"jsonrpc":"2.0","id":1,"method":"server/discover"}',
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + ("z" * 32),
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                    },
                )
                try:
                    urllib.request.urlopen(request, timeout=1.0)
                except urllib.error.HTTPError as error:
                    try:
                        self.assertEqual(error.code, 401)
                        self.assertEqual(error.read(), b'{"error":"unauthorized"}')
                    finally:
                        error.close()
                else:
                    self.fail("wrong Host MCP token was accepted")

                oversized = urllib.request.Request(
                    endpoint,
                    data=b"x" * 1_048_577,
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + ("z" * 32),
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                    },
                )
                try:
                    urllib.request.urlopen(oversized, timeout=2.0)
                except urllib.error.HTTPError as error:
                    try:
                        self.assertEqual(error.code, 413)
                        self.assertEqual(error.read(), b'{"error":"request_too_large"}')
                    finally:
                        error.close()
                else:
                    self.fail("oversized unauthenticated Host MCP request was accepted")

                task_id = "task:mcp:continuity"
                initial = _checkpoint(task_id, "revalidate initial truth")
                adopted = client.call_tool(
                    "task.adopt",
                    {
                        "taskId": task_id,
                        "goalId": "goal:mcp:continuity",
                        "initialCheckpoint": initial,
                    },
                )
                self.assertEqual(adopted["projection"]["revision"], 2)
                self.assertEqual(
                    adopted["checkpoint"]["checkpoint"]["truthRole"],
                    "semantic-working-claim",
                )
                self.assertEqual(
                    adopted["serverInterface"]["schemaDigest"], wire_schema_digest
                )
                self.assertEqual(
                    adopted["serverInterface"]["schemaRevision"],
                    f"mcp-schema:{wire_schema_digest[7:]}",
                )
                self.assertEqual(
                    adopted["serverInterface"]["toolNames"],
                    sorted(by_name),
                )

                listed = client.call_tool(
                    "task.list", {"goalId": "goal:mcp:continuity", "limit": 10}
                )
                item = next(
                    value
                    for value in listed["tasks"]
                    if value["projection"]["taskId"] == task_id
                )
                self.assertTrue(item["externalContinuity"])
                self.assertEqual(
                    item["workloadId"], "ordivon.host.external-continuity.v1"
                )

                resumed = client.call_tool(
                    "task.resume", {"taskId": task_id, "expectedRevision": 2}
                )
                self.assertEqual(resumed, adopted)

                updated = _checkpoint(task_id, "continue after revalidation")
                created = client.call_tool(
                    "task.checkpoint",
                    {
                        "taskId": task_id,
                        "expectedRevision": 2,
                        "checkpoint": updated,
                    },
                )
                self.assertEqual(created["admission"], "created")
                self.assertEqual(created["projection"]["revision"], 3)

                replay = client.call_tool(
                    "task.checkpoint",
                    {
                        "taskId": task_id,
                        "expectedRevision": 2,
                        "checkpoint": updated,
                    },
                )
                self.assertEqual(replay["admission"], "existing")
                self.assertEqual(replay["projection"]["revision"], 3)

                with self.assertRaises(McpToolRejected) as captured:
                    client.call_tool(
                        "task.checkpoint",
                        {
                            "taskId": task_id,
                            "expectedRevision": 2,
                            "checkpoint": _checkpoint(task_id, "different stale claim"),
                        },
                    )
                self.assertEqual(captured.exception.detail.code, "REVISION_CONFLICT")
                self.assertEqual(
                    captured.exception.detail.commit_state, "not_committed"
                )
                self.assertEqual(captured.exception.detail.origin, "host-mcp")

                loss_task = "task:mcp:response-loss"
                client.call_tool(
                    "task.adopt",
                    {
                        "taskId": loss_task,
                        "goalId": "goal:mcp:continuity",
                        "initialCheckpoint": _checkpoint(loss_task, "before response loss"),
                    },
                )
                lost_update = _checkpoint(loss_task, "committed while response is dropped")
                self._drop_tool_response(
                    port, token, "task.checkpoint",
                    {
                        "taskId": loss_task,
                        "expectedRevision": 2,
                        "checkpoint": lost_update,
                        "continuityDisposition": "complete",
                    },
                )
                deadline = time.monotonic() + 3
                while True:
                    current = client.call_tool("task.resume", {"taskId": loss_task})
                    if current["projection"]["revision"] == 3:
                        self.assertEqual(current["projection"]["state"], "completed")
                        self.assertEqual(current["handoff"]["nextAdmissible"], [])
                        break
                    if time.monotonic() >= deadline:
                        self.fail("dropped MCP response did not leave a committed checkpoint")
                    time.sleep(0.02)
                replay_after_loss = client.call_tool(
                    "task.checkpoint",
                    {
                        "taskId": loss_task,
                        "expectedRevision": 2,
                        "checkpoint": lost_update,
                        "continuityDisposition": "complete",
                    },
                )
                self.assertEqual(replay_after_loss["admission"], "existing")
                self.assertEqual(replay_after_loss["projection"]["state"], "completed")
                active_after_complete = client.call_tool(
                    "task.list", {"goalId": "goal:mcp:continuity", "limit": 20}
                )
                self.assertNotIn(
                    loss_task,
                    {item["projection"]["taskId"] for item in active_after_complete["tasks"]},
                )
                history_after_complete = client.call_tool(
                    "task.list",
                    {
                        "goalId": "goal:mcp:continuity",
                        "limit": 20,
                        "includeTerminal": True,
                    },
                )
                historical = next(
                    item
                    for item in history_after_complete["tasks"]
                    if item["projection"]["taskId"] == loss_task
                )
                self.assertEqual(historical["projection"]["state"], "completed")
                self.assertEqual(
                    historical["semanticSummary"]["checkpointRevision"], 3
                )

                race_task = "task:mcp:race"
                client.call_tool(
                    "task.adopt",
                    {
                        "taskId": race_task,
                        "goalId": "goal:mcp:continuity",
                        "initialCheckpoint": _checkpoint(race_task, "before race"),
                    },
                )

                def compete(label: str) -> str:
                    contender = McpTestClient(
                        endpoint,
                        token,
                        timeout_seconds=2.0,
                        client_name=f"ordivon-host-mcp-race-{label}",
                        client_version="0.1.2",
                    )
                    contender.initialize()
                    try:
                        result = contender.call_tool(
                            "task.checkpoint",
                            {
                                "taskId": race_task,
                                "expectedRevision": 2,
                                "checkpoint": _checkpoint(race_task, f"winner-{label}"),
                            },
                        )
                    except McpToolRejected as error:
                        return error.detail.code
                    return result["admission"]

                with ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(compete, ("a", "b")))
                self.assertEqual(outcomes.count("created"), 1, outcomes)
                self.assertEqual(len(outcomes), 2)
                self.assertIn(
                    next(value for value in outcomes if value != "created"),
                    {"TASK_BUSY", "REVISION_CONFLICT"},
                )
                raced = client.call_tool("task.resume", {"taskId": race_task})
                self.assertEqual(raced["projection"]["revision"], 3)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                stdout, stderr = process.communicate()
                if process.returncode not in {0, -15}:
                    self.fail(
                        f"Host MCP exited {process.returncode}: stdout={stdout!r} stderr={stderr!r}"
                    )

    @staticmethod
    def _request_with_host(
        port: int,
        token: str | None,
        host: str,
        *,
        origin: str | None = None,
        cf_access_assertion: str | None = None,
    ) -> tuple[int, dict[str, object] | str]:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 650,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "host-header-test",
                            "version": "0.1.2",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
            separators=(",", ":"),
        ).encode()
        headers = {
            "Host": host,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        if cf_access_assertion is not None:
            headers["Cf-Access-Jwt-Assertion"] = cf_access_assertion
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("POST", "/mcp", body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        finally:
            connection.close()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return status, raw.decode("utf-8", errors="replace")
        if not isinstance(parsed, dict):
            raise AssertionError("MCP response must be an object")
        return status, parsed

    @staticmethod
    def _legacy_lifecycle(endpoint: str, token: str) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }

        def exchange(payload: dict[str, object], *, expect_body: bool = True) -> object:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, separators=(",", ":")).encode(),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                body = response.read()
                if not expect_body:
                    return response.status
                return json.loads(body)

        initialized = exchange(
            {
                "jsonrpc": "2.0",
                "id": 701,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-test", "version": "0.1.2"},
                },
            }
        )
        assert isinstance(initialized, dict)
        result = initialized.get("result")
        assert isinstance(result, dict)
        notification_status = exchange(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_body=False,
        )
        if notification_status not in {200, 202, 204}:
            raise AssertionError(f"legacy initialized notification failed: {notification_status}")
        listed = exchange(
            {"jsonrpc": "2.0", "id": 702, "method": "tools/list", "params": {}}
        )
        assert isinstance(listed, dict)
        listed_result = listed.get("result")
        assert isinstance(listed_result, dict)
        tools = listed_result.get("tools")
        assert isinstance(tools, list)
        return {"protocolVersion": result.get("protocolVersion"), "tools": tools}

    @staticmethod
    def _drop_tool_response(
        port: int, token: str, name: str, arguments: dict[str, object]
    ) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "ordivon-host-mcp-response-loss-test",
                            "version": "0.1.2",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
            separators=(",", ":"),
        ).encode()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request(
            "POST",
            "/mcp",
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": name,
            },
        )
        time.sleep(0.05)
        connection.close()

    @staticmethod
    def _wait_for_server(
        process: subprocess.Popen[str], endpoint: str, token: str
    ) -> McpTestClient:
        deadline = time.monotonic() + 10
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Host MCP exited before readiness: stdout={stdout!r} stderr={stderr!r}"
                )
            client = McpTestClient(
                endpoint,
                token,
                timeout_seconds=1.0,
                client_name="ordivon-host-mcp-test",
                client_version="0.1.2",
            )
            try:
                client.initialize()
                return client
            except McpTransportError as error:
                last_error = error
                time.sleep(0.05)
        raise AssertionError(f"Host MCP did not become ready: {last_error}")


if __name__ == "__main__":
    unittest.main()
