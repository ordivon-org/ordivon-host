from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from anc_canonical import JsonValue, canonical_bytes, canonical_digest, loads_strict


class ObjectStoreError(RuntimeError):
    pass


class ObjectMissing(ObjectStoreError):
    pass


class ObjectCorrupt(ObjectStoreError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    digest: str
    byte_length: int
    kind: str

    def __post_init__(self) -> None:
        if not self.kind or self.kind != self.kind.strip():
            raise ValueError("object kind must be non-empty and trimmed")
        if self.byte_length < 0:
            raise ValueError("object byte length must be non-negative")


class ContentAddressedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, value: JsonValue, *, kind: str) -> StoredObject:
        if not kind or kind != kind.strip():
            raise ValueError("object kind must be non-empty and trimmed")
        envelope: JsonValue = {
            "schemaVersion": 1,
            "kind": kind,
            "payload": value,
        }
        encoded = canonical_bytes(envelope)
        digest = canonical_digest(envelope)
        path = self._path(digest)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ObjectCorrupt("content address maps to different bytes")
            return StoredObject(digest, len(encoded), kind)
        temporary = path.with_suffix(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        directory_fd: int | None = None
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(
                self.root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            os.fsync(directory_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            temporary.unlink(missing_ok=True)
        return StoredObject(digest, len(encoded), kind)

    def get(self, digest: str, *, expected_kind: str | None = None) -> JsonValue:
        envelope, stored = self._load(digest)
        if expected_kind is not None and stored.kind != expected_kind:
            raise ObjectCorrupt(
                f"object kind is {stored.kind}, expected {expected_kind}"
            )
        return envelope["payload"]

    def inspect(self, digest: str) -> StoredObject:
        _, stored = self._load(digest)
        return stored

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def _load(self, digest: str) -> tuple[dict[str, JsonValue], StoredObject]:
        path = self._path(digest)
        if not path.exists():
            raise ObjectMissing(f"content-addressed object is missing: {digest}")
        try:
            encoded = path.read_bytes()
            value = loads_strict(encoded)
        except (OSError, ValueError) as error:
            raise ObjectCorrupt(f"object cannot be decoded: {digest}") from error
        if canonical_digest(value) != digest:
            raise ObjectCorrupt("object content does not match its address")
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "kind",
            "payload",
        }:
            raise ObjectCorrupt("object envelope fields differ")
        if value["schemaVersion"] != 1 or not isinstance(value["kind"], str):
            raise ObjectCorrupt("object envelope version or kind is invalid")
        try:
            stored = StoredObject(digest, len(encoded), value["kind"])
        except ValueError as error:
            raise ObjectCorrupt("object envelope metadata is invalid") from error
        return value, stored

    def _path(self, digest: str) -> Path:
        if (
            len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise ValueError("object store key must be a sha256 digest")
        return self.root / f"{digest[7:]}.json"
