from __future__ import annotations

import itertools
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host import (
    EventKind,
    HarnessCapabilityManifest,
    HarnessHost,
    HostKernel,
    HostRuntimeReference,
    HostStorage,
    build_harness_workspace_exec_request,
    harness_run_runtime_binding_digest,
    harness_runtime_client_request_id,
    host_runtime_references,
    task_runtime_binding_digest,
)

TASK_ID = "task:harness-h2"
GOAL_ID = "goal:harness-h2"
OBJECTIVE = canonical_digest({"objective": "exercise Host Runtime correlation"})
ACCEPTANCE = canonical_digest({"acceptance": "one exact Runtime Job"})
TOOL_CATALOG = canonical_digest({"runtimeCatalog": "fixture"})
SOURCE_DIGEST = canonical_digest({"sourceRevision": "fixture"})


def _manifest(harness_id: str) -> HarnessCapabilityManifest:
    return HarnessCapabilityManifest(
        harness_id=harness_id,
        protocol="fixture",
        protocol_revision="1",
        persistent_session=True,
        session_resume=False,
        session_fork=False,
        interrupt=True,
        tool_events=True,
        approval_events=False,
        usage=True,
        images=False,
        compaction=False,
        checkpoint=False,
        local_subagents=False,
    )


def _create_task(storage: HostStorage, clock) -> None:
    HostKernel(
        storage,
        clock_ms=clock,
        owner_id="host:harness-h2-task-create",
    ).create_task(
        event_id="event:harness-h2:create",
        kind=EventKind.TASK_CREATED,
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        payload={"workloadId": "harness-runtime-correlation-v1"},
        frontier=("node:harness-h2:run",),
    )


def _assign(storage: HostStorage, clock, harness_id: str, context_label: str):
    host = HarnessHost(storage, clock_ms=clock)
    attempt = host.start_attempt(
        TASK_ID,
        objective_digest=OBJECTIVE,
        acceptance_criteria_digest=ACCEPTANCE,
    )
    context = storage.put_object(
        {"schemaVersion": 1, "label": context_label},
        kind="compiled-context",
    )
    return host.assign(
        attempt,
        manifest=_manifest(harness_id),
        context_object_digest=context.digest,
        tool_catalog_digest=TOOL_CATALOG,
        workspace_ref="host-harness-h2-fixture",
        source_ref="repository:fixture",
        source_digest=SOURCE_DIGEST,
        required_capabilities=("persistent_session", "interrupt", "tool_events"),
    )


class HarnessH2RuntimeReferenceTests(unittest.TestCase):
    def test_references_are_canonical_ordered_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(1_000).__next__
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                committed = _assign(
                    storage,
                    clock,
                    "harness:codex-app-server",
                    "codex-context",
                )
                run_id = "harness-run:harness-h2:codex:1"
                references = host_runtime_references(committed, run_id)
                self.assertEqual(
                    [reference.reference_type for reference in references],
                    ["assignment", "harness_run", "task", "task_attempt"],
                )
                self.assertEqual(
                    tuple(
                        HostRuntimeReference.from_dict(reference.to_dict())
                        for reference in references
                    ),
                    references,
                )
                by_type = {reference.reference_type: reference for reference in references}
                self.assertEqual(
                    by_type["task"].generation,
                    str(committed.assignment.task_revision),
                )
                self.assertEqual(
                    by_type["task"].digest,
                    task_runtime_binding_digest(committed),
                )
                self.assertEqual(
                    by_type["task_attempt"].digest,
                    committed.attempt.digest,
                )
                self.assertEqual(
                    by_type["assignment"].generation,
                    str(committed.assignment.generation),
                )
                self.assertEqual(
                    by_type["assignment"].digest,
                    committed.assignment.digest,
                )
                self.assertEqual(
                    by_type["harness_run"].digest,
                    harness_run_runtime_binding_digest(committed, run_id),
                )

    def test_request_identity_is_assignment_run_and_step_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(2_000).__next__
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                first = _assign(
                    storage,
                    clock,
                    "harness:codex-app-server",
                    "codex-context",
                )
                run_id = "harness-run:harness-h2:codex:1"
                first_request = build_harness_workspace_exec_request(
                    first,
                    harness_run_id=run_id,
                    step_id="probe-runtime-correlation",
                    executable="/usr/bin/python3",
                    args=("-c", "print('h2')"),
                    wait_ms=30_000,
                )
                replay = build_harness_workspace_exec_request(
                    first,
                    harness_run_id=run_id,
                    step_id="probe-runtime-correlation",
                    executable="/usr/bin/python3",
                    args=("-c", "print('h2')"),
                    wait_ms=30_000,
                )
                self.assertEqual(first_request, replay)
                self.assertEqual(
                    first_request["clientRequestId"],
                    harness_runtime_client_request_id(
                        first, run_id, "probe-runtime-correlation"
                    ),
                )
                execution = first_request["execution"]
                assert isinstance(execution, dict)
                self.assertEqual(execution["workspaceId"], "host-harness-h2-fixture")
                self.assertEqual(
                    [item["type"] for item in execution["foreignReferences"]],
                    ["assignment", "harness_run", "task", "task_attempt"],
                )
                another_step = build_harness_workspace_exec_request(
                    first,
                    harness_run_id=run_id,
                    step_id="another-step",
                    executable="/usr/bin/python3",
                )
                self.assertNotEqual(
                    first_request["clientRequestId"],
                    another_step["clientRequestId"],
                )

                second = _assign(
                    storage,
                    clock,
                    "harness:hermes-acp",
                    "hermes-context",
                )
                second_request = build_harness_workspace_exec_request(
                    second,
                    harness_run_id="harness-run:harness-h2:hermes:1",
                    step_id="probe-runtime-correlation",
                    executable="/usr/bin/python3",
                )
                self.assertEqual(second.assignment.generation, 2)
                self.assertNotEqual(
                    first_request["clientRequestId"],
                    second_request["clientRequestId"],
                )
                second_execution = second_request["execution"]
                assert isinstance(second_execution, dict)
                assignment_ref = next(
                    item
                    for item in second_execution["foreignReferences"]
                    if item["type"] == "assignment"
                )
                self.assertEqual(assignment_ref["generation"], "2")
                self.assertEqual(assignment_ref["digest"], second.assignment.digest)

    def test_request_rejects_missing_workspace_and_relative_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = itertools.count(3_000).__next__
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                committed = _assign(
                    storage,
                    clock,
                    "harness:codex-app-server",
                    "codex-context",
                )
                object.__setattr__(committed.assignment, "workspace_ref", None)
                with self.assertRaisesRegex(ValueError, "Workspace reference"):
                    build_harness_workspace_exec_request(
                        committed,
                        harness_run_id="harness-run:harness-h2:invalid:1",
                        step_id="invalid",
                        executable="/usr/bin/python3",
                    )
                object.__setattr__(
                    committed.assignment,
                    "workspace_ref",
                    "host-harness-h2-fixture",
                )
                with self.assertRaisesRegex(ValueError, "must be absolute"):
                    build_harness_workspace_exec_request(
                        committed,
                        harness_run_id="harness-run:harness-h2:invalid:1",
                        step_id="invalid",
                        executable="python3",
                    )


if __name__ == "__main__":
    unittest.main()
