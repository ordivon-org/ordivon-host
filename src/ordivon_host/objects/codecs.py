from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

T = TypeVar("T")


class ObjectCodecError(ValueError):
    pass


class UnsupportedObjectVersion(ObjectCodecError):
    pass


def decode_versioned_object(
    value: dict[str, Any],
    *,
    expected_kind: str,
    decoders: Mapping[int, Callable[[dict[str, Any]], T]],
    label: str,
) -> T:
    """Dispatch one durable semantic object by explicit kind and schema version."""
    kind = value.get("kind")
    version = value.get("schemaVersion")
    if kind != expected_kind:
        raise ObjectCodecError(f"{label} kind is {kind!r}, expected {expected_kind!r}")
    if type(version) is not int:
        raise ObjectCodecError(f"{label} schemaVersion must be an integer")
    decoder = decoders.get(version)
    if decoder is None:
        raise UnsupportedObjectVersion(
            f"unsupported {label} schema version: {version}"
        )
    return decoder(value)
