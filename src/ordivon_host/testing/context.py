from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from typing import Mapping

from anc_canonical import JsonValue, canonical_digest

from ..config import read_private_token_file


@dataclass(frozen=True, slots=True)
class ScenarioIdentity:
    prefix: str
    stamp_ms: int
    nonce: str

    @classmethod
    def create(cls, prefix: str, *, stamp_ms: int | None = None) -> ScenarioIdentity:
        if not prefix or prefix != prefix.strip():
            raise ValueError("Scenario prefix is required")
        stamp = int(time.time() * 1_000) if stamp_ms is None else stamp_ms
        return cls(prefix, stamp, uuid.uuid4().hex[:12])

    @property
    def token(self) -> str:
        return f"{self.prefix}-{self.stamp_ms}-{self.nonce}"

    @property
    def task_id(self) -> str:
        return f"task:{self.token}"

    @property
    def goal_id(self) -> str:
        return f"goal:{self.token}"

    @property
    def workspace_id(self) -> str:
        return f"host-{self.token}"



def scenario_state_root(
    requested: str | Path | None,
    *,
    prefix: str,
    identity: ScenarioIdentity,
) -> Path:
    if requested is not None:
        root = Path(requested)
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(
        tempfile.mkdtemp(
            prefix=f"ordivon-host-{prefix}-{identity.nonce}-",
            dir="/tmp",
        )
    )


def cleanup_state_root(root: str | Path, *, keep: bool) -> None:
    if not keep:
        shutil.rmtree(Path(root), ignore_errors=True)


def scenario_clock_ms() -> int:
    return int(time.time() * 1_000)


def load_scenario_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    token = env.get("ORDIVON_BEARER_TOKEN")
    if token:
        if any(character.isspace() for character in token):
            raise ValueError("ORDIVON_BEARER_TOKEN must be one token")
        return token
    token_file = env.get("ORDIVON_BEARER_TOKEN_FILE")
    if token_file:
        return read_private_token_file(token_file, label="Scenario bearer token")
    raise RuntimeError(
        "ORDIVON_BEARER_TOKEN_FILE or ORDIVON_BEARER_TOKEN is required"
    )


def emit_receipt(receipt: dict[str, JsonValue]) -> None:
    if "integrity" in receipt:
        raise ValueError("Scenario receipt already contains integrity metadata")
    receipt["integrity"] = {
        "algorithm": "sha256",
        "canonicalization": "ordivon-canonical-json-v1",
        "payloadDigest": canonical_digest(receipt),
    }
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
