from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

_SCRIPT = Path(__file__).resolve().parents[1] / "configure_deepseek_api.py"
_SPEC = importlib.util.spec_from_file_location("configure_deepseek_api", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class ConfigureDeepSeekApiTests(unittest.TestCase):
    def test_validate_key_rejects_short_or_whitespace_values(self) -> None:
        for value in ("", "short", "sk-valid-but-has whitespace"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _MODULE._validate_key(value)

    def test_write_and_load_secret_use_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "deepseek.json"
            payload = _MODULE._secret_payload("sk-" + "a" * 40, "deepseek-v4-pro")
            _MODULE._write_secret(path, payload)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(_MODULE._load_secret(path), payload)
            self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_load_rejects_group_readable_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deepseek.json"
            path.write_text(
                json.dumps(_MODULE._secret_payload("sk-" + "b" * 40, "deepseek-v4-flash")),
                encoding="utf-8",
            )
            os.chmod(path, 0o640)
            with self.assertRaises(PermissionError):
                _MODULE._load_secret(path)

    def test_payload_is_fixed_to_official_base_url(self) -> None:
        payload = _MODULE._secret_payload("sk-" + "c" * 40, "deepseek-v4-pro")
        self.assertEqual(payload["baseUrl"], "https://api.deepseek.com")
        self.assertEqual(payload["provider"], "deepseek")
        self.assertNotIn("apiKey", _MODULE._fingerprint(payload["apiKey"]))


if __name__ == "__main__":
    unittest.main()
