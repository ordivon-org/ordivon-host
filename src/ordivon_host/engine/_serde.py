from __future__ import annotations

import hashlib
from typing import Any

from anc_canonical import JsonValue, validate_json_value

from ..journal import JournalCorruption
from ..runtime.errors import RuntimeProtocolError


def task_token(task_id: str) -> str:
    return task_id.removeprefix("task:")


def digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def validate_digest(value: str) -> None:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("invalid sha256 digest")


def require_object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise JournalCorruption(f"{label} must be an object")
    return value


def require_string(value: dict[str, JsonValue], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise JournalCorruption(f"Task event field {key} must be a string")
    return result


def json_object(value: dict[str, Any], label: str) -> dict[str, JsonValue]:
    try:
        validate_json_value(value)
    except ValueError as error:
        raise RuntimeProtocolError(f"{label} contains non-JSON data") from error
    return dict(value)
