from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tomllib
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("/etc/ordivon/host.toml")
DEFAULT_STATE_ROOT = Path("/var/lib/ordivon/host")
DEFAULT_RUNTIME_ENDPOINT = "http://127.0.0.1:8897/mcp"
DEFAULT_TOKEN_FILE = Path("/etc/ordivon/runtime-mcp.token")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    endpoint: str = DEFAULT_RUNTIME_ENDPOINT
    token_file: Path = DEFAULT_TOKEN_FILE
    timeout_seconds: float = 45.0
    max_response_bytes: int = 2_097_152

    def __post_init__(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Runtime endpoint must be HTTP(S)")
        if not self.token_file.is_absolute():
            raise ValueError("Runtime token file must be absolute")
        if self.timeout_seconds <= 0 or self.max_response_bytes < 1:
            raise ValueError("Runtime bounds must be positive")


@dataclass(frozen=True, slots=True)
class HostConfig:
    state_root: Path = DEFAULT_STATE_ROOT
    receipt_root: Path | None = None
    runtime: RuntimeSettings = RuntimeSettings()
    repositories: tuple[tuple[str, Path], ...] = ()

    def __post_init__(self) -> None:
        if not self.state_root.is_absolute():
            raise ValueError("Host state root must be absolute")
        receipt_root = self.receipt_root or self.state_root / "receipts"
        if not receipt_root.is_absolute():
            raise ValueError("Host receipt root must be absolute")
        object.__setattr__(self, "receipt_root", receipt_root)
        if len(dict(self.repositories)) != len(self.repositories):
            raise ValueError("Host repository identities must be unique")
        for identity, path in self.repositories:
            if not identity.startswith("repository:"):
                raise ValueError("Host repository identity must start with repository:")
            if not path.is_absolute():
                raise ValueError("Host repository path must be absolute")

    def to_dict(self) -> dict[str, object]:
        return {
            "stateRoot": str(self.state_root),
            "receiptRoot": str(self.receipt_root),
            "runtime": {
                "endpoint": self.runtime.endpoint,
                "tokenFile": str(self.runtime.token_file),
                "timeoutSeconds": self.runtime.timeout_seconds,
                "maxResponseBytes": self.runtime.max_response_bytes,
            },
            "repositories": {
                identity: str(path) for identity, path in self.repositories
            },
        }


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> HostConfig:
    env = os.environ if environ is None else environ
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    raw: dict[str, object] = {}
    if config_path.exists():
        value = tomllib.loads(config_path.read_text())
        if not isinstance(value, dict):
            raise ValueError("Host config root must be a table")
        raw = value
    elif path is not None:
        raise FileNotFoundError(config_path)
    _check_keys(raw, {"state", "runtime", "repositories"}, "Host config")
    state = _table(raw.get("state"), "state")
    runtime = _table(raw.get("runtime"), "runtime")
    repositories = _table(raw.get("repositories"), "repositories")
    _check_keys(state, {"root", "receipt_root"}, "state")
    _check_keys(
        runtime,
        {"endpoint", "token_file", "timeout_seconds", "max_response_bytes"},
        "runtime",
    )
    state_root = Path(
        env.get("ORDIVON_HOST_STATE_ROOT", str(state.get("root", DEFAULT_STATE_ROOT)))
    )
    receipt_value = env.get("ORDIVON_HOST_RECEIPT_ROOT", state.get("receipt_root"))
    receipt_root = Path(str(receipt_value)) if receipt_value is not None else None
    return HostConfig(
        state_root=state_root,
        receipt_root=receipt_root,
        runtime=RuntimeSettings(
            endpoint=env.get(
                "ORDIVON_MCP_ENDPOINT",
                str(runtime.get("endpoint", DEFAULT_RUNTIME_ENDPOINT)),
            ),
            token_file=Path(
                env.get(
                    "ORDIVON_BEARER_TOKEN_FILE",
                    str(runtime.get("token_file", DEFAULT_TOKEN_FILE)),
                )
            ),
            timeout_seconds=float(runtime.get("timeout_seconds", 45.0)),
            max_response_bytes=_strict_int(
                runtime.get("max_response_bytes", 2_097_152),
                "runtime.max_response_bytes",
            ),
        ),
        repositories=tuple(
            sorted(
                (
                    identity,
                    Path(str(path)),
                )
                for identity, path in repositories.items()
            )
        ),
    )



def read_private_token_file(
    path: str | Path,
    *,
    label: str = "Bearer token",
    max_bytes: int = 16_384,
) -> str:
    token_path = Path(path)
    try:
        metadata = token_path.lstat()
    except FileNotFoundError:
        raise
    if token_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} path must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PermissionError(f"{label} file must not be accessible by group or others")
    if metadata.st_size > max_bytes:
        raise ValueError(f"{label} file exceeds the configured bound")
    token = token_path.read_text().strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError(f"{label} file must contain one non-whitespace token")
    return token


def read_token_file(path: str | Path, *, max_bytes: int = 16_384) -> str:
    return read_private_token_file(
        path, label="Runtime token", max_bytes=max_bytes
    )


def _table(value: object, label: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a table")
    return dict(value)


def _check_keys(value: dict[str, object], allowed: set[str], label: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise ValueError(f"{label} has unsupported fields: {sorted(unexpected)}")


def _strict_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value
