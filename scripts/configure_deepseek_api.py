#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any

DEFAULT_SECRET_PATH = Path.home() / ".config" / "ordivon" / "secrets" / "deepseek.json"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
SUPPORTED_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Store a DeepSeek API key in a root-private Ordivon secret file. "
            "The key is read with hidden terminal input and is never accepted as a CLI argument."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SECRET_PATH,
        help=f"secret file path (default: {DEFAULT_SECRET_PATH})",
    )
    parser.add_argument(
        "--model",
        choices=SUPPORTED_MODELS,
        default=DEFAULT_MODEL,
        help=f"default model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing secret file without an overwrite prompt",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="after writing, make one minimal non-thinking Chat Completions request",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="test the existing secret file without replacing it",
    )
    return parser.parse_args()


def _validate_key(api_key: str) -> str:
    if not api_key or api_key != api_key.strip():
        raise ValueError("API key must be non-empty and contain no surrounding whitespace")
    if len(api_key) < 20:
        raise ValueError("API key is unexpectedly short")
    if any(character.isspace() for character in api_key):
        raise ValueError("API key must not contain whitespace")
    if any(ord(character) < 33 or ord(character) > 126 for character in api_key):
        raise ValueError("API key must contain visible ASCII characters only")
    return api_key


def _secret_payload(api_key: str, model: str) -> dict[str, Any]:
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"unsupported DeepSeek model: {model}")
    return {
        "schemaVersion": 1,
        "provider": "deepseek",
        "apiKey": _validate_key(api_key),
        "baseUrl": DEFAULT_BASE_URL,
        "model": model,
    }


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise ValueError("secret output path must be absolute")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(parent, 0o700)
    if path.exists() and not path.is_file():
        raise ValueError(f"secret output exists but is not a regular file: {path}")

    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _load_secret(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(
            f"secret file permissions are too broad: {oct(mode)}; expected 0o600"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "provider",
        "apiKey",
        "baseUrl",
        "model",
    }:
        raise ValueError("DeepSeek secret file has unexpected fields")
    if value.get("schemaVersion") != 1 or value.get("provider") != "deepseek":
        raise ValueError("DeepSeek secret file has an unsupported schema")
    api_key = value.get("apiKey")
    base_url = value.get("baseUrl")
    model = value.get("model")
    if not isinstance(api_key, str):
        raise ValueError("DeepSeek secret file has no API key")
    if base_url != DEFAULT_BASE_URL:
        raise ValueError("DeepSeek secret file uses an unexpected base URL")
    if not isinstance(model, str) or model not in SUPPORTED_MODELS:
        raise ValueError("DeepSeek secret file uses an unsupported model")
    value["apiKey"] = _validate_key(api_key)
    return value


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def _check_api(secret: dict[str, Any]) -> None:
    body = json.dumps(
        {
            "model": secret["model"],
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly OK.",
                }
            ],
            "thinking": {"type": "disabled"},
            "max_tokens": 8,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{secret['baseUrl']}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {secret['apiKey']}",
            "Content-Type": "application/json",
            "User-Agent": "ordivon-host-deepseek-setup/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(1_048_576)
    except urllib.error.HTTPError as error:
        detail = error.read(4_096).decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"DeepSeek API connection failed: {error.reason}") from error

    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("DeepSeek API returned a non-object response")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("DeepSeek API response omitted choices")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise RuntimeError("DeepSeek API response omitted assistant content")
    usage = value.get("usage")
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    print("DeepSeek API check passed")
    print(f"  model: {value.get('model', secret['model'])}")
    print(f"  response: {message['content'][:80]!r}")
    if isinstance(total_tokens, int):
        print(f"  total tokens: {total_tokens}")


def _confirm_replace(path: Path) -> bool:
    answer = input(f"Secret file already exists: {path}\nReplace it? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    try:
        if args.check_only:
            secret = _load_secret(output)
            print(f"Loaded DeepSeek secret: {output}")
            print(f"  model: {secret['model']}")
            print(f"  key fingerprint: sha256:{_fingerprint(secret['apiKey'])}")
            _check_api(secret)
            return 0

        if output.exists() and not args.force and not _confirm_replace(output):
            print("No changes made")
            return 1

        api_key = _validate_key(getpass.getpass("DeepSeek API Key (input hidden): "))
        secret = _secret_payload(api_key, args.model)
        _write_secret(output, secret)
        loaded = _load_secret(output)
        print(f"Saved DeepSeek secret: {output}")
        print("  directory mode: 0o700")
        print("  file mode: 0o600")
        print(f"  base URL: {loaded['baseUrl']}")
        print(f"  model: {loaded['model']}")
        print(f"  key fingerprint: sha256:{_fingerprint(loaded['apiKey'])}")
        if args.check:
            _check_api(loaded)
        return 0
    except (FileNotFoundError, PermissionError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
