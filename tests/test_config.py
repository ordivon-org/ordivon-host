from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host.config import load_config, read_token_file


class HostConfigTests(unittest.TestCase):
    def test_defaults_and_environment_overrides(self) -> None:
        config = load_config(
            environ={
                "ORDIVON_HOST_STATE_ROOT": "/tmp/host-state",
                "ORDIVON_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp",
                "ORDIVON_BEARER_TOKEN_FILE": "/tmp/token",
            }
        )
        self.assertEqual(config.state_root, Path("/tmp/host-state"))
        self.assertEqual(config.receipt_root, Path("/tmp/host-state/receipts"))
        self.assertEqual(config.runtime.endpoint, "http://127.0.0.1:9999/mcp")
        self.assertEqual(config.runtime.token_file, Path("/tmp/token"))

    def test_explicit_missing_config_fails(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config("/definitely/missing/host.toml")

    def test_config_file_and_unknown_field_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text(
                "[state]\nroot='/tmp/state'\nreceipt_root='/tmp/receipts'\n"
                "[runtime]\nendpoint='http://127.0.0.1:8897/mcp'\n"
                "token_file='/tmp/token'\n"
            )
            config = load_config(path, environ={})
            self.assertEqual(config.state_root, Path("/tmp/state"))
            self.assertEqual(config.receipt_root, Path("/tmp/receipts"))
            path.write_text("[state]\nroot='/tmp/state'\nunknown=true\n")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_config(path, environ={})

    def test_legacy_provider_table_is_accepted_but_not_projected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text(
                "[providers]\ncodex_executable='legacy-codex'\n"
                "hermes_executable='legacy-hermes'\ntimeout_seconds=12\n"
            )
            config = load_config(path, environ={})
            self.assertFalse(hasattr(config, "providers"))
            self.assertNotIn("providers", config.to_dict())

    def test_legacy_provider_table_still_fails_closed_on_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text("[providers]\ntimeout_seconds=0\n")
            with self.assertRaisesRegex(ValueError, "positive integer"):
                load_config(path, environ={})

    def test_token_file_is_bounded_and_single_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("secret-token\n")
            path.chmod(0o600)
            self.assertEqual(read_token_file(path), "secret-token")
            path.write_text("two tokens")
            with self.assertRaises(ValueError):
                read_token_file(path)
