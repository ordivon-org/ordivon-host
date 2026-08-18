from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ordivon_host.config import load_config, read_private_token_file


class HostConfigTests(unittest.TestCase):
    def test_defaults_and_environment_overrides(self) -> None:
        config = load_config(
            environ={
                "ORDIVON_HOST_STATE_ROOT": "/tmp/host-state",
            }
        )
        self.assertEqual(config.state_root, Path("/tmp/host-state"))
        self.assertEqual(config.receipt_root, Path("/tmp/host-state/receipts"))

    def test_explicit_missing_config_fails(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config("/definitely/missing/host.toml")

    def test_config_file_and_unknown_field_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text(
                "[state]\nroot='/tmp/state'\nreceipt_root='/tmp/receipts'\n"
            )
            config = load_config(path, environ={})
            self.assertEqual(config.state_root, Path("/tmp/state"))
            self.assertEqual(config.receipt_root, Path("/tmp/receipts"))
            path.write_text("[state]\nroot='/tmp/state'\nunknown=true\n")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_config(path, environ={})

    def test_provider_table_is_rejected_as_removed_host_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text(
                "[providers]\ncodex_executable='legacy-codex'\n"
            )
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                load_config(path)

    def test_token_file_is_bounded_and_single_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("secret-token\n")
            path.chmod(0o600)
            self.assertEqual(read_private_token_file(path), "secret-token")
            path.write_text("two tokens")
            with self.assertRaises(ValueError):
                read_private_token_file(path)
