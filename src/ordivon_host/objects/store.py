from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
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


@dataclass(frozen=True, slots=True)
class ObjectFileIdentity:
    device: int
    inode: int
    byte_length: int
    modified_at_ns: int
    changed_at_ns: int
    mode: int

    def __post_init__(self) -> None:
        if min(
            self.device,
            self.inode,
            self.byte_length,
            self.modified_at_ns,
            self.changed_at_ns,
            self.mode,
        ) < 0:
            raise ValueError("object file identity values must be non-negative")

    @classmethod
    def from_stat(cls, value: os.stat_result) -> ObjectFileIdentity:
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            byte_length=int(value.st_size),
            modified_at_ns=int(value.st_mtime_ns),
            changed_at_ns=int(value.st_ctime_ns),
            mode=stat.S_IMODE(value.st_mode),
        )

    def to_sql(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.device,
            self.inode,
            self.byte_length,
            self.modified_at_ns,
            self.changed_at_ns,
            self.mode,
        )


class ContentAddressedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ensure_mode(self.root, 0o700)
        for path in self.root.glob("*.json"):
            if path.is_file() and not path.is_symlink():
                self._ensure_mode(path, 0o600)

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
            if path.is_symlink():
                raise ObjectCorrupt("content-addressed object cannot be a symlink")
            self._ensure_mode(path, 0o600)
            if path.read_bytes() != encoded:
                raise ObjectCorrupt("content address maps to different bytes")
            return StoredObject(digest, len(encoded), kind)
        temporary = path.with_suffix(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        directory_fd: int | None = None
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
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
        envelope, stored, _ = self._load_with_identity(digest)
        if expected_kind is not None and stored.kind != expected_kind:
            raise ObjectCorrupt(
                f"object kind is {stored.kind}, expected {expected_kind}"
            )
        return envelope["payload"]

    def inspect(self, digest: str) -> StoredObject:
        _, stored, _ = self._load_with_identity(digest)
        return stored

    def inspect_with_identity(
        self, digest: str
    ) -> tuple[StoredObject, ObjectFileIdentity]:
        _, stored, identity = self._load_with_identity(digest)
        return stored, identity

    def identity(self, digest: str) -> ObjectFileIdentity:
        path = self._path(digest)
        try:
            return ObjectFileIdentity.from_stat(path.stat())
        except FileNotFoundError as error:
            raise ObjectMissing(f"content-addressed object is missing: {digest}") from error
        except OSError as error:
            raise ObjectCorrupt(f"object metadata cannot be read: {digest}") from error

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def _load_with_identity(
        self, digest: str
    ) -> tuple[dict[str, JsonValue], StoredObject, ObjectFileIdentity]:
        path = self._path(digest)
        try:
            with path.open("rb") as handle:
                before = ObjectFileIdentity.from_stat(os.fstat(handle.fileno()))
                encoded = handle.read()
                after = ObjectFileIdentity.from_stat(os.fstat(handle.fileno()))
        except FileNotFoundError as error:
            raise ObjectMissing(f"content-addressed object is missing: {digest}") from error
        except OSError as error:
            raise ObjectCorrupt(f"object cannot be read: {digest}") from error
        if before != after or len(encoded) != after.byte_length:
            raise ObjectCorrupt(f"object changed while being read: {digest}")
        try:
            value = loads_strict(encoded)
        except ValueError as error:
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
        return value, stored, after

    @staticmethod
    def _ensure_mode(path: Path, mode: int) -> None:
        if stat.S_IMODE(path.stat().st_mode) != mode:
            os.chmod(path, mode)

    def _path(self, digest: str) -> Path:
        if (
            len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise ValueError("object store key must be a sha256 digest")
        return self.root / f"{digest[7:]}.json"
