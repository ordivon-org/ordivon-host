from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import tempfile
from typing import Any
import unittest

from anc_effect_ir import CapabilityRequirement

from ordivon_host import HostStorage, TaskState
from ordivon_host.authority import (
    CapabilityDenied,
    CapabilityProfileAuthorizer,
    OWNER_TRUSTED_PROFILE_ID,
    PUBLIC_BOUNDED_PROFILE_ID,
)
from ordivon_host.cognition import (
    ActionProposal,
    BlockKind,
    ConsequenceClass,
    DecisionRequest,
    Freshness,
    LoweredReadProposal,
    OpenCognitionRequest,
    OpenContextCompiler,
    OpenProposalHost,
    ProposalIntent,
    ProposalRejection,
    ProposalResolutionKind,
    ProposalTarget,
    RepositoryReadProposalCompiler,
    ResourceBinding,
    Reversibility,
    block_from_payload,
)
from ordivon_host.domain import StaticRepositoryResolver
from ordivon_host.engine import DeterministicReadHost
from ordivon_host.ops import doctor_state
from ordivon_host.runtime import RuntimeErrorDetail, RuntimeToolRejected


WORLD = "sha256:" + ("a" * 64)
REVISION = "b" * 40
RESOURCE = "repository:ordivon-host"
OWNER = "participant:local-owner"
PARENT_TASK = "task:open-proposal"
GOAL = "goal:inspect-host"
PROPOSAL_NODE = "node:open-proposal:propose"


def descriptor(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {"schemaVersion": {"type": "integer", "const": 1}}
    required = ["schemaVersion"]
    if name == "workspace.read":
        properties.update(
            {
                "workspaceId": {"type": "string"},
                "relativePath": {"type": "string"},
                "mode": {"type": "string", "enum": ["FULL", "SLICE"]},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "maxBytes": {"type": "integer", "minimum": 1, "maximum": 4_194_304},
            }
        )
        required.extend(["workspaceId", "relativePath", "mode", "maxBytes"])
    return {
        "name": name,
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def missing_workspace(operation: str) -> RuntimeToolRejected:
    return RuntimeToolRejected(
        operation,
        RuntimeErrorDetail(
            code="INVALID_REQUEST",
            message="missing workspace",
            field="workspaceId",
            retryable=False,
            retry_class="never",
            commit_state="not_committed",
            origin="runtime_core",
            trace_id="test",
            raw={},
        ),
    )


class FakeRuntime:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.content = "# Ordivon Host\nPersistent coordination and commitment plane.\n"

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {"serverInfo": {"name": "fake-runtime"}}

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        self.calls.append("tools/list")
        return tuple(
            deepcopy(descriptor(name))
            for name in (
                "workspace.close",
                "workspace.get",
                "workspace.open",
                "workspace.read",
            )
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        workspace_id = arguments.get("workspaceId")
        if name == "workspace.get":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            return deepcopy(self.workspaces[workspace_id])
        if name == "workspace.open":
            record = {
                "workspaceId": workspace_id,
                "sourceRevision": arguments["sourceRevision"],
                "dirty": False,
                "headMode": "detached",
            }
            self.workspaces[str(workspace_id)] = record
            return deepcopy(record)
        if name == "workspace.read":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            digest = "sha256:" + hashlib.sha256(self.content.encode()).hexdigest()
            return {"content": self.content, "digest": digest}
        if name == "workspace.close":
            if workspace_id not in self.workspaces:
                raise missing_workspace(name)
            del self.workspaces[str(workspace_id)]
            return {"workspaceId": workspace_id, "closed": True}
        raise AssertionError(f"unexpected Tool: {name}")


def request(*, profile_id: str = OWNER_TRUSTED_PROFILE_ID) -> OpenCognitionRequest:
    return OpenCognitionRequest(
        task_id=PARENT_TASK,
        world_digest=WORLD,
        blocks=(
            block_from_payload(
                block_id="context-block:goal",
                kind=BlockKind.GOAL,
                priority=100,
                required=True,
                freshness=Freshness.CURRENT,
                source={"goalId": GOAL},
                payload={
                    "statement": (
                        "Inspect the Host README and identify its public positioning."
                    )
                },
            ),
        ),
        capability_profile_id=profile_id,
        responsible_participant_ref=OWNER,
        resources=(ResourceBinding(RESOURCE, REVISION, OWNER),),
    )


def proposal(
    context,
    *,
    revision: str = REVISION,
    consequence: ConsequenceClass = ConsequenceClass.PRIVATE_REVERSIBLE,
    reversibility: Reversibility = Reversibility.REVERSIBLE,
    profile_id: str = OWNER_TRUSTED_PROFILE_ID,
    participants: tuple[str, ...] = (),
) -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal:read-host-readme",
        task_id=PARENT_TASK,
        context_digest=context.digest,
        intent=ProposalIntent.OBSERVE,
        target=ProposalTarget(
            kind="repository-file",
            resource_ref=RESOURCE,
            revision=revision,
            selector={"relativePath": "README.md", "maxBytes": 65_536},
        ),
        rationale=(
            "The README is the authoritative compact description of the current Host role."
        ),
        preconditions=("Repository revision remains current.",),
        affected_resource_refs=(RESOURCE,),
        affected_participant_refs=participants,
        reversibility=reversibility,
        consequence_class=consequence,
        requested_profile_id=profile_id,
        candidate_method="workspace.read",
        expected_result="Return the current README content and digest.",
        verification_plan="Verify the returned content with an independent SHA-256 digest.",
    )


def open_host(storage: HostStorage, runtime: FakeRuntime) -> OpenProposalHost:
    return OpenProposalHost(
        storage,
        runtime,
        clock_ms=itertools.count(1_000).__next__,
        repository_resolver=StaticRepositoryResolver(
            {RESOURCE: "/root/projects/ordivon-host"}
        ),
    )


class OpenProposalTests(unittest.TestCase):
    def test_open_context_contains_resources_but_no_action_menu(self) -> None:
        context = OpenContextCompiler().compile(request(), token_budget=4_000)
        self.assertEqual(context.payload["kind"], "ordivon.open-compiled-context")
        self.assertNotIn("allowedActions", context.payload)
        self.assertEqual(
            context.payload["availableResources"],
            [{"resourceRef": RESOURCE, "revision": REVISION, "ownerRef": OWNER}],
        )
        lowered = RepositoryReadProposalCompiler(
            CapabilityProfileAuthorizer()
        ).compile(
            context,
            proposal(context),
            goal_id=GOAL,
            child_task_id="task:open-proposal:read-child",
            workspace_id="open-proposal-read-child",
        )
        self.assertIsInstance(lowered, LoweredReadProposal)
        assert isinstance(lowered, LoweredReadProposal)
        self.assertEqual(lowered.plan.relative_path, "README.md")
        self.assertEqual(lowered.capability_decision.policy_id, OWNER_TRUSTED_PROFILE_ID)

    def test_shared_or_foreign_effect_routes_to_responsible_participant(self) -> None:
        context = OpenContextCompiler().compile(request(), token_budget=4_000)
        result = RepositoryReadProposalCompiler(CapabilityProfileAuthorizer()).compile(
            context,
            proposal(
                context,
                consequence=ConsequenceClass.SHARED_REVERSIBLE,
                participants=("participant:another",),
            ),
            goal_id=GOAL,
            child_task_id="task:unused",
            workspace_id="unused",
        )
        self.assertIsInstance(result, DecisionRequest)
        assert isinstance(result, DecisionRequest)
        self.assertEqual(result.recipient_ref, OWNER)
        self.assertNotIn("human", result.reason_code)

    def test_stale_resource_and_profile_drift_are_structured_rejections(self) -> None:
        context = OpenContextCompiler().compile(request(), token_budget=4_000)
        compiler = RepositoryReadProposalCompiler(CapabilityProfileAuthorizer())
        stale = compiler.compile(
            context,
            proposal(context, revision="c" * 40),
            goal_id=GOAL,
            child_task_id="task:unused",
            workspace_id="unused",
        )
        self.assertIsInstance(stale, ProposalRejection)
        assert isinstance(stale, ProposalRejection)
        self.assertEqual(stale.code, "stale_resource")
        wrong_profile = compiler.compile(
            context,
            proposal(context, profile_id=PUBLIC_BOUNDED_PROFILE_ID),
            goal_id=GOAL,
            child_task_id="task:unused",
            workspace_id="unused",
        )
        self.assertIsInstance(wrong_profile, ProposalRejection)
        assert isinstance(wrong_profile, ProposalRejection)
        self.assertEqual(wrong_profile.code, "wrong_profile")

    def test_owner_trusted_and_public_bounded_are_distinct_profiles(self) -> None:
        profiles = CapabilityProfileAuthorizer()
        requirement = CapabilityRequirement(
            "principal:local-owner",
            "anc.source.change.v1",
            "world_object:repository:ordivon-host",
        )
        admitted = profiles.authorize(
            requirement,
            profile_id=OWNER_TRUSTED_PROFILE_ID,
        )
        self.assertTrue(admitted.allowed)
        with self.assertRaises(CapabilityDenied):
            profiles.authorize(requirement, profile_id=PUBLIC_BOUNDED_PROFILE_ID)

    def test_child_commit_gap_is_recovered_without_second_child_creation(self) -> None:
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = open_host(storage, runtime)
                host.create_task(
                    task_id=PARENT_TASK,
                    goal_id=GOAL,
                    proposal_node_id=PROPOSAL_NODE,
                )
                prepared = host.prepare(
                    task_id=PARENT_TASK,
                    proposal_node_id=PROPOSAL_NODE,
                    request=request(),
                    token_budget=4_000,
                )
                invocation = host.cognition.prepare_invocation(
                    prepared, gateway_id="fixture-open-proposal"
                )
                action = proposal(prepared.context)
                token = action.digest[7:23]
                child_task_id = f"task:open-proposal:read-{token}"
                resolution = host.proposal_compiler.compile(
                    prepared.context,
                    action,
                    goal_id=GOAL,
                    child_task_id=child_task_id,
                    workspace_id=f"host-proposal-read-{token}",
                )
                self.assertIsInstance(resolution, LoweredReadProposal)
                assert isinstance(resolution, LoweredReadProposal)
                DeterministicReadHost(
                    storage,
                    runtime,
                    clock_ms=itertools.count(1_500).__next__,
                    repository_resolver=StaticRepositoryResolver(
                        {RESOURCE: "/root/projects/ordivon-host"}
                    ),
                    authorizer=CapabilityProfileAuthorizer().bind(
                        OWNER_TRUSTED_PROFILE_ID
                    ),
                ).create(resolution.plan)
                self.assertEqual(storage.journal.event_count(child_task_id), 1)
                self.assertEqual(
                    storage.read_task_event(PARENT_TASK).event_kind.value,
                    "cognition.invocation-prepared",
                )

            with HostStorage(directory) as storage:
                receipt = open_host(storage, runtime).admit_proposal(
                    invocation,
                    action,
                    evidence={"recoveredAfterChildCommitGap": True},
                )
                self.assertEqual(receipt.child_task_id, child_task_id)
                self.assertEqual(storage.journal.event_count(child_task_id), 1)
                self.assertEqual(storage.journal.event_count(PARENT_TASK), 4)

    def test_decision_request_is_persisted_without_a_child_effect(self) -> None:
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = open_host(storage, runtime)
                host.create_task(
                    task_id=PARENT_TASK,
                    goal_id=GOAL,
                    proposal_node_id=PROPOSAL_NODE,
                )
                prepared = host.prepare(
                    task_id=PARENT_TASK,
                    proposal_node_id=PROPOSAL_NODE,
                    request=request(),
                    token_budget=4_000,
                )
                invocation = host.cognition.prepare_invocation(
                    prepared, gateway_id="fixture-open-proposal"
                )
                receipt = host.admit_proposal(
                    invocation,
                    proposal(
                        prepared.context,
                        consequence=ConsequenceClass.SHARED_REVERSIBLE,
                        participants=("participant:another",),
                    ),
                )
                self.assertEqual(
                    receipt.resolution_kind,
                    ProposalResolutionKind.DECISION_REQUEST,
                )
                self.assertIsNone(receipt.child_task_id)
                self.assertIsNotNone(receipt.decision_request_id)
                current = storage.journal.get_task(PARENT_TASK)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.state, TaskState.BLOCKED)
                self.assertEqual(runtime.calls, [])

    def test_open_proposal_executes_and_recovers_across_fresh_hosts(self) -> None:
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                host = open_host(storage, runtime)
                host.create_task(
                    task_id=PARENT_TASK,
                    goal_id=GOAL,
                    proposal_node_id=PROPOSAL_NODE,
                )
                prepared = host.prepare(
                    task_id=PARENT_TASK,
                    proposal_node_id=PROPOSAL_NODE,
                    request=request(),
                    token_budget=4_000,
                )
                invocation = host.cognition.prepare_invocation(
                    prepared,
                    gateway_id="fixture-open-proposal",
                )
                action = proposal(prepared.context)
                receipt = host.admit_proposal(
                    invocation,
                    action,
                    evidence={"physicalProviderCall": False},
                )
                replayed = host.admit_proposal(
                    invocation,
                    action,
                    evidence={"physicalProviderCall": False},
                )
                self.assertEqual(receipt, replayed)
                self.assertEqual(receipt.resolution_kind, ProposalResolutionKind.LOWERED)
                self.assertIsNotNone(receipt.child_task_id)
                parent = storage.journal.get_task(PARENT_TASK)
                self.assertIsNotNone(parent)
                assert parent is not None
                self.assertEqual(parent.state, TaskState.WAITING)
                child_task_id = receipt.child_task_id
                assert child_task_id is not None

            profiles = CapabilityProfileAuthorizer()
            for expected_revision in (2, 3, 4):
                with HostStorage(directory) as storage:
                    runner = DeterministicReadHost(
                        storage,
                        runtime,
                        clock_ms=itertools.count(2_000).__next__,
                        repository_resolver=StaticRepositoryResolver(
                            {RESOURCE: "/root/projects/ordivon-host"}
                        ),
                        authorizer=profiles.bind(OWNER_TRUSTED_PROFILE_ID),
                    )
                    step = runner.step(child_task_id)
                    self.assertEqual(step.revision, expected_revision)

            with HostStorage(directory) as storage:
                completed = open_host(storage, runtime).reconcile(PARENT_TASK)
                self.assertEqual(completed.state, TaskState.COMPLETED)
                self.assertEqual(runtime.calls.count("workspace.read"), 1)
                self.assertEqual(runtime.calls.count("workspace.open"), 1)
                self.assertEqual(runtime.calls.count("workspace.close"), 1)

            report = doctor_state(directory, check_history=True)
            self.assertTrue(report["healthy"])


if __name__ == "__main__":
    unittest.main()
