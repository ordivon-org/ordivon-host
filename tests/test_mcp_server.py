from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import stat
import urllib.error
import urllib.request
import subprocess
import sys
import tempfile
import time
import unittest

from ordivon_host.continuity_models import WorkingCheckpoint
from ordivon_host.mcp_server import HostMcpSettings, check_settings
from ordivon_host.runtime import McpRuntimeClient, RuntimeToolRejected, RuntimeTransportError
from ordivon_host.storage import HostStorage


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
                    {"task.list", "task.resume", "task.adopt", "task.checkpoint"},
                )
                discovered = client.initialize()
                self.assertEqual(discovered["protocolVersion"], "2026-07-28")

                external_status, external = self._request_with_host(
                    port, token, "host-mcp.example.test",
                    origin="https://host-mcp.example.test",
                )
                self.assertEqual(external_status, 200)
                self.assertEqual(external["result"]["supportedVersions"], ["2026-07-28"])
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
                    {"task.list", "task.resume", "task.adopt", "task.checkpoint"},
                )
                for tool in tools:
                    self.assertIsInstance(tool.get("inputSchema"), dict)

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

                with self.assertRaises(RuntimeToolRejected) as captured:
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
                    },
                )
                deadline = time.monotonic() + 3
                while True:
                    current = client.call_tool("task.resume", {"taskId": loss_task})
                    if current["projection"]["revision"] == 3:
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
                    },
                )
                self.assertEqual(replay_after_loss["admission"], "existing")

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
                    contender = McpRuntimeClient(
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
                    except RuntimeToolRejected as error:
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
        port: int, token: str, host: str, *, origin: str | None = None
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
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        }
        if origin is not None:
            headers["Origin"] = origin
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
    ) -> McpRuntimeClient:
        deadline = time.monotonic() + 10
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Host MCP exited before readiness: stdout={stdout!r} stderr={stderr!r}"
                )
            client = McpRuntimeClient(
                endpoint,
                token,
                timeout_seconds=1.0,
                client_name="ordivon-host-mcp-test",
                client_version="0.1.2",
            )
            try:
                client.initialize()
                return client
            except RuntimeTransportError as error:
                last_error = error
                time.sleep(0.05)
        raise AssertionError(f"Host MCP did not become ready: {last_error}")


if __name__ == "__main__":
    unittest.main()
