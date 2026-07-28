from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anc_canonical import JsonValue, canonical_digest
from ordivon_protocol import validate_host_workload_object


@dataclass(frozen=True, slots=True)
class TaskDescriptor:
    task_id: str
    goal_id: str
    workload_id: str
    assignee_ref: str | None = None
    provider_policy_ref: str | None = None
    domain_ref: str | None = None
    configuration_digests: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-task-descriptor",
            "taskId": self.task_id,
            "goalId": self.goal_id,
            "workloadId": self.workload_id,
            "assigneeRef": self.assignee_ref,
            "providerPolicyRef": self.provider_policy_ref,
            "domainRef": self.domain_ref,
            "configurationDigests": list(self.configuration_digests),
        }
        validate_host_workload_object(value)
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskDescriptor:
        validate_host_workload_object(value)
        if value.get("kind") != "ordivon.host-task-descriptor":
            raise ValueError("wire object is not a TaskDescriptor")
        configuration = value["configurationDigests"]
        assert isinstance(configuration, list)
        return cls(
            task_id=str(value["taskId"]),
            goal_id=str(value["goalId"]),
            workload_id=str(value["workloadId"]),
            assignee_ref=(
                None if value["assigneeRef"] is None else str(value["assigneeRef"])
            ),
            provider_policy_ref=(
                None
                if value["providerPolicyRef"] is None
                else str(value["providerPolicyRef"])
            ),
            domain_ref=None if value["domainRef"] is None else str(value["domainRef"]),
            configuration_digests=tuple(str(item) for item in configuration),
        )
