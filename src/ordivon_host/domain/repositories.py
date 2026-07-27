from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    repository_id: str
    revision: str
    legacy_path: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not self.repository_id.startswith("repository:")
            or self.repository_id != self.repository_id.strip()
        ):
            raise ValueError("repository identity must start with repository:")
        if (
            len(self.revision) != 40
            or any(character not in "0123456789abcdef" for character in self.revision)
        ):
            raise ValueError("repository revision must be a lowercase Git object id")
        if self.legacy_path is not None and not Path(self.legacy_path).is_absolute():
            raise ValueError("legacy repository path must be absolute")

    def to_dict(self) -> dict[str, str]:
        return {
            "repositoryId": self.repository_id,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RepositoryRef:
        if set(value) != {"repositoryId", "revision"}:
            raise ValueError("RepositoryRef fields differ")
        if not isinstance(value["repositoryId"], str) or not isinstance(
            value["revision"], str
        ):
            raise ValueError("RepositoryRef fields must be strings")
        return cls(value["repositoryId"], value["revision"])


class RepositoryResolver(Protocol):
    def resolve(self, repository: RepositoryRef) -> Path: ...


class StaticRepositoryResolver:
    def __init__(self, repositories: Mapping[str, str | Path]) -> None:
        self._repositories = {
            identity: Path(path)
            for identity, path in repositories.items()
        }
        for identity, path in self._repositories.items():
            if not identity.startswith("repository:"):
                raise ValueError("repository map keys must start with repository:")
            if not path.is_absolute():
                raise ValueError("repository map paths must be absolute")

    def resolve(self, repository: RepositoryRef) -> Path:
        if repository.legacy_path is not None:
            return Path(repository.legacy_path)
        try:
            return self._repositories[repository.repository_id]
        except KeyError as error:
            raise KeyError(
                f"unresolved repository identity: {repository.repository_id}"
            ) from error
