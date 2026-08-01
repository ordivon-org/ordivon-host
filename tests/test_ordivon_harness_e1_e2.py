from __future__ import annotations

from dataclasses import replace
import itertools
import tempfile
import unittest

from anc_canonical import canonical_digest

from ordivon_host import (
    GrantedExecutionCheck,
    HarnessHost,
    HostStorage,
    NativeHarnessRunContract,
    ToolGrant,
)
from ordivon_host.harness import (
    CompletionRoute,
    NativeRunFacts,
    NativeRunOperatorAction,
    NativeRunPhase,
    NativeToolCatalogSnapshot,
    NativeToolRecoveryConsequence,
    ReplacementScope,
    derive_native_run_disposition,
)
from ordivon_host.harness.ordivon import (
    AgentToolCall,
    NativeRunTimes,
    RuntimeToolBridge,
    build_native_run_receipt,
    discover_harness_runtime_catalog,
    ordivon_harness_manifest,
)
from ordivon_host.journal import JournalCorruption
from ordivon_host.objects import ObjectCorrupt
from ordivon_host.ops import validate_history
from ordivon_host.ops.history import _validate_semantic_links

from tests.test_ordivon_harness_oh5 import (
    TASK_ID,
    _RecoveryRuntime,
    _assign,
    _conclusion_result,
    _contract,
    _create_task,
    _grant,
)


class NativeToolCatalogTests(unittest.TestCase):
    def test_all_current_tools_have_complete_explicit_semantics(self) -> None:
        catalog = discover_harness_runtime_catalog(_RecoveryRuntime())
        self.assertEqual(len(catalog.tools), 7)
        self.assertEqual(len({tool.name for tool in catalog.tools}), 7)
        self.assertEqual(
            {tool.name for tool in catalog.tools},
            {
                "read_workspace",
                "mutate_workspace",
                "diff_workspace",
                "run_check",
                "run_in_workspace",
                "observe_job",
                "read_artifact",
            },
        )
        for tool in catalog.tools:
            with self.subTest(tool=tool.name):
                self.assertIsNot(
                    tool.recovery_consequence,
                    NativeToolRecoveryConsequence.UNKNOWN,
                )
                self.assertTrue(tool.runtime_operations)
                self.assertEqual(tool.contract.operation, tool.name)
                self.assertEqual(
                    catalog.model_tools[
                        tuple(item.name for item in catalog.model_tools).index(
                            tool.name
                        )
                    ].input_schema,
                    tool.contract.input_schema,
                )

    def test_mutate_workspace_exposes_exact_mutation_item_schema(self) -> None:
        catalog = discover_harness_runtime_catalog(_RecoveryRuntime())
        schema = catalog.tool("mutate_workspace").contract.input_schema
        mutations = schema["properties"]["mutations"]
        self.assertEqual(mutations["minItems"], 1)
        self.assertEqual(mutations["maxItems"], 32)
        mutation = mutations["items"]
        self.assertEqual(mutation["required"], ["relativePath", "mode"])
        self.assertFalse(mutation["additionalProperties"])
        self.assertEqual(
            mutation["properties"]["mode"]["enum"],
            ["WRITE", "APPEND", "REPLACE_EXACT"],
        )
        self.assertNotIn("action", mutation["properties"])
        self.assertEqual(
            set(mutation["properties"]),
            {
                "relativePath",
                "mode",
                "content",
                "expectedDigest",
                "expectedText",
            },
        )

    def test_declared_runtime_operations_match_actual_bridge_lowering(self) -> None:
        class SemanticRuntime(_RecoveryRuntime):
            def call_tool(self, name, arguments):
                if name == "task.observe":
                    self.calls.append((name, dict(arguments)))
                    return {
                        "jobId": arguments["jobId"],
                        "status": "succeeded",
                        "artifacts": [
                            {
                                "artifactId": "artifact:e1:result",
                                "kind": "stdout",
                                "digest": canonical_digest("e1-result"),
                            }
                        ],
                    }
                return super().call_tool(name, arguments)

        runtime = SemanticRuntime()
        clock = itertools.count(800).__next__
        grant = ToolGrant(
            tool_grant_id="tool-grant:e1:all-tools",
            allowed_tools=(
                "read_workspace",
                "mutate_workspace",
                "diff_workspace",
                "run_check",
                "run_in_workspace",
                "observe_job",
                "read_artifact",
            ),
            read_path_rules=("**",),
            mutate_path_rules=("**",),
            execution_checks=(
                GrantedExecutionCheck(
                    check_id="check:e1:smoke",
                    executable="/usr/bin/true",
                ),
            ),
            allow_opaque_exec=True,
        )
        calls = (
            AgentToolCall(
                "tool-call:e1:read",
                "read_workspace",
                {"relativePath": "README.md"},
            ),
            AgentToolCall(
                "tool-call:e1:mutate",
                "mutate_workspace",
                {
                    "mutations": [
                        {
                            "mode": "WRITE",
                            "relativePath": "e1-fixture.txt",
                            "content": "fixture",
                        }
                    ]
                },
            ),
            AgentToolCall("tool-call:e1:diff", "diff_workspace", {}),
            AgentToolCall(
                "tool-call:e1:check",
                "run_check",
                {"checkId": "check:e1:smoke"},
            ),
            AgentToolCall(
                "tool-call:e1:opaque",
                "run_in_workspace",
                {"executable": "/usr/bin/true"},
            ),
            AgentToolCall(
                "tool-call:e1:observe",
                "observe_job",
                {"jobId": "job:oh5"},
            ),
            AgentToolCall(
                "tool-call:e1:artifact",
                "read_artifact",
                {
                    "jobId": "job:oh5",
                    "artifactId": "artifact:e1:result",
                },
            ),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            HostStorage(directory) as storage,
        ):
            _create_task(storage, clock)
            _, committed, _, _ = _assign(storage, clock, runtime, grant=grant)
            assert committed.native_run_contract is not None
            assert committed.tool_catalog is not None
            bridge = RuntimeToolBridge(
                committed,
                harness_run_id=committed.native_run_contract.harness_run_id,
                runtime=runtime,
            )
            for index, call in enumerate(calls, start=1):
                before = len(runtime.calls)
                bridge.execute(call, step_id=f"turn-1-tool-{index}")
                actual_operation = runtime.calls[before][0]
                expected = committed.tool_catalog.tool(call.name).runtime_operations
                self.assertEqual((actual_operation,), expected)

    def test_catalog_round_trip_and_semantic_change_alter_digest(self) -> None:
        catalog = discover_harness_runtime_catalog(_RecoveryRuntime())
        self.assertEqual(
            NativeToolCatalogSnapshot.from_dict(catalog.to_dict()),
            catalog,
        )
        changed_tool = replace(
            catalog.tools[0],
            recovery_consequence=(
                NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE
            ),
        )
        changed = NativeToolCatalogSnapshot(
            catalog.runtime_descriptors,
            (changed_tool, *catalog.tools[1:]),
        )
        self.assertNotEqual(changed.digest, catalog.digest)
        self.assertNotEqual(changed.semantic_digest, catalog.semantic_digest)

    def test_unknown_tool_and_incomplete_runtime_mapping_fail_closed(self) -> None:
        catalog = discover_harness_runtime_catalog(_RecoveryRuntime())
        with self.assertRaises(KeyError):
            catalog.aggregate_recovery_consequence(("future_effectful_tool",))
        with self.assertRaisesRegex(ValueError, "unsupported Tools"):
            ToolGrant(
                tool_grant_id="tool-grant:e1:unknown",
                allowed_tools=("future_effectful_tool",),
            )
        broken = replace(
            catalog.tools[0],
            runtime_operations=("runtime.operation.missing",),
        )
        with self.assertRaisesRegex(ValueError, "missing Runtime operations"):
            NativeToolCatalogSnapshot(
                catalog.runtime_descriptors,
                (broken, *catalog.tools[1:]),
            )


class NativeRunContractV2Tests(unittest.TestCase):
    @staticmethod
    def _contract(*, catalog_object: str | None) -> NativeHarnessRunContract:
        return NativeHarnessRunContract(
            harness_run_id="harness-run:e1-e2-contract",
            assignment_id="assignment:e1-e2-contract:g1",
            assignment_generation=1,
            assignment_digest=canonical_digest({"assignment": 1}),
            harness_manifest_digest=canonical_digest({"manifest": 1}),
            task_contract_digest=canonical_digest({"taskContract": 1}),
            task_contract_object_digest=canonical_digest({"taskContractObject": 1}),
            context_object_digest=canonical_digest({"context": 1}),
            tool_catalog_digest=canonical_digest({"catalog": 1}),
            tool_grant_digest=canonical_digest({"grant": 1}),
            tool_grant_object_digest=canonical_digest({"grantObject": 1}),
            created_at_ms=1,
            tool_catalog_object_digest=catalog_object,
        )

    def test_v1_and_v2_round_trip_without_rewriting_history(self) -> None:
        v1 = self._contract(catalog_object=None)
        self.assertEqual(v1.schema_version, 1)
        self.assertEqual(NativeHarnessRunContract.from_dict(v1.to_dict()), v1)
        v2 = self._contract(catalog_object=canonical_digest({"catalogObject": 1}))
        self.assertEqual(v2.schema_version, 2)
        self.assertEqual(NativeHarnessRunContract.from_dict(v2.to_dict()), v2)
        self.assertNotIn("toolCatalogObjectDigest", v1.to_dict())
        self.assertIn("toolCatalogObjectDigest", v2.to_dict())

    def test_initial_native_assignment_requires_catalog_snapshot(self) -> None:
        runtime = _RecoveryRuntime()
        clock = itertools.count(1_000).__next__
        with (
            tempfile.TemporaryDirectory() as directory,
            HostStorage(directory) as storage,
        ):
            _create_task(storage, clock)
            host = HarnessHost(storage, clock_ms=clock)
            attempt = host.start_attempt(TASK_ID, task_contract=_contract())
            context = storage.put_object(
                {"schemaVersion": 1, "kind": "test-compiled-context"},
                kind="compiled-context",
            )
            catalog = discover_harness_runtime_catalog(runtime)
            with self.assertRaisesRegex(ValueError, "requires a durable Tool catalog"):
                host.assign(
                    attempt,
                    manifest=ordivon_harness_manifest(),
                    context_object_digest=context.digest,
                    tool_catalog_digest=catalog.digest,
                    workspace_ref="workspace:e1:no-catalog",
                    tool_grant=_grant(),
                )

    def test_fresh_host_reloads_exact_catalog_and_history_validates_it(self) -> None:
        runtime = _RecoveryRuntime()
        clock = itertools.count(2_000).__next__
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                _create_task(storage, clock)
                _, committed, _, _ = _assign(storage, clock, runtime)
                self.assertIsNotNone(committed.tool_catalog)
                self.assertIsNotNone(committed.tool_catalog_object)
                assert committed.native_run_contract is not None
                self.assertEqual(committed.native_run_contract.schema_version, 2)
                self.assertEqual(
                    committed.native_run_contract.tool_catalog_object_digest,
                    committed.tool_catalog_object.digest,
                )
            with HostStorage(directory) as storage:
                fresh = HarnessHost(storage, clock_ms=clock).load_current_assignment(
                    TASK_ID
                )
                self.assertEqual(fresh.tool_catalog, committed.tool_catalog)
                self.assertEqual(
                    fresh.tool_catalog_object.digest,
                    committed.tool_catalog_object.digest,
                )
                validated = validate_history(storage)
                self.assertGreaterEqual(validated.semantic_link_checks, 4)

    def test_runtime_bridge_rejects_missing_retained_v2_catalog(self) -> None:
        runtime = _RecoveryRuntime()
        clock = itertools.count(2_500).__next__
        with (
            tempfile.TemporaryDirectory() as directory,
            HostStorage(directory) as storage,
        ):
            _create_task(storage, clock)
            _, committed, _, _ = _assign(storage, clock, runtime)
            assert committed.native_run_contract is not None
            stripped = replace(
                committed,
                tool_catalog=None,
                tool_catalog_object=None,
            )
            with self.assertRaisesRegex(
                ValueError, "requires its retained Tool catalog"
            ):
                RuntimeToolBridge(
                    stripped,
                    harness_run_id=committed.native_run_contract.harness_run_id,
                    runtime=runtime,
                )

    def test_native_run_rejects_candidate_completion_with_unknown_evidence(
        self,
    ) -> None:
        runtime = _RecoveryRuntime()
        clock = itertools.count(2_700).__next__
        with (
            tempfile.TemporaryDirectory() as directory,
            HostStorage(directory) as storage,
        ):
            _create_task(storage, clock)
            host, committed, context_digest, _ = _assign(storage, clock, runtime)
            result = _conclusion_result(committed, context_digest)
            receipt = build_native_run_receipt(
                committed,
                result,
                times=NativeRunTimes(10_000, 10_010),
            )
            unknown_observation = {
                "schemaVersion": 1,
                "kind": "ordivon.tool-observation",
                "toolCallId": "tool-call:e1:contradictory-unknown",
                "toolName": "read_workspace",
                "status": "unknown",
                "structuredContent": {"error": {"type": "injected"}},
                "runtimeJobRef": None,
                "artifactRefs": [],
                "reconciled": False,
            }
            assert result.conclusion is not None
            with self.assertRaisesRegex(ValueError, "UNKNOWN evidence"):
                host.record_run(
                    committed,
                    receipt,
                    trace=result.trace.to_dict(),
                    observations=(unknown_observation,),
                    conclusion=result.conclusion.to_dict(),
                )

    def test_history_rejects_catalog_reference_tampering(self) -> None:
        runtime = _RecoveryRuntime()
        clock = itertools.count(3_000).__next__
        with (
            tempfile.TemporaryDirectory() as directory,
            HostStorage(directory) as storage,
        ):
            _create_task(storage, clock)
            _, committed, _, _ = _assign(storage, clock, runtime)
            snapshot = storage.read_task_event(TASK_ID)
            assert isinstance(snapshot.data, dict)
            tampered = dict(snapshot.data)
            tampered["toolCatalogObjectDigest"] = committed.assignment_object.digest
            with self.assertRaises((JournalCorruption, ObjectCorrupt)):
                _validate_semantic_links(storage, tampered, "event:e1-tampered")


class NativeRunDispositionMatrixTests(unittest.TestCase):
    def test_oh5_decision_matrix(self) -> None:
        read = NativeToolRecoveryConsequence.OBSERVATION_ONLY
        effect = NativeToolRecoveryConsequence.WORKSPACE_CHANGE_POSSIBLE
        cases = (
            (
                NativeRunFacts(NativeRunPhase.ASSIGNED_UNRECORDED, read),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.RUN_CURRENT_ASSIGNMENT,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RECOVERY_RECORDED,
                    read,
                    recovery_safe_to_abandon=True,
                ),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.ABANDON_CURRENT_RUN,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RECOVERY_RECORDED,
                    effect,
                    recovery_safe_to_abandon=False,
                    unresolved_unknowns=("effect unknown",),
                ),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.RECONCILE_UNKNOWN,
                NativeRunOperatorAction.RECONCILE_CURRENT_UNKNOWN,
            ),
            (
                NativeRunFacts(NativeRunPhase.ABANDONED, read),
                ReplacementScope.ANY_WORKSPACE,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.REPLACE_ASSIGNMENT,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RUN_RECORDED,
                    read,
                    termination_code="runtime_unknown",
                    has_tool_observations=True,
                    has_unknown_observation=True,
                ),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.RECONCILE_UNKNOWN,
                NativeRunOperatorAction.RECONCILE_CURRENT_UNKNOWN,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RUN_RECORDED,
                    read,
                    termination_code="candidate_completed",
                    has_candidate_conclusion=True,
                ),
                ReplacementScope.SAME_WORKSPACE,
                CompletionRoute.PROPOSE_CURRENT_RUN,
                NativeRunOperatorAction.REPLACE_OR_PROPOSE_COMPLETION,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RUN_RECORDED,
                    effect,
                    termination_code="candidate_completed",
                    has_tool_observations=True,
                    has_candidate_conclusion=True,
                ),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.PROPOSE_CURRENT_RUN,
                NativeRunOperatorAction.PROPOSE_CURRENT_COMPLETION,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RUN_RECORDED,
                    read,
                    termination_code="provider_timeout",
                ),
                ReplacementScope.SAME_WORKSPACE,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.REPLACE_ASSIGNMENT,
            ),
            (
                NativeRunFacts(
                    NativeRunPhase.RUN_RECORDED,
                    effect,
                    termination_code="provider_timeout",
                    has_tool_observations=True,
                ),
                ReplacementScope.FORBIDDEN,
                CompletionRoute.UNAVAILABLE,
                NativeRunOperatorAction.VERIFY_BEFORE_REPLACEMENT,
            ),
        )
        for facts, replacement, completion, action in cases:
            with self.subTest(facts=facts):
                result = derive_native_run_disposition(facts)
                self.assertIs(result.replacement_scope, replacement)
                self.assertIs(result.completion_route, completion)
                self.assertIs(result.operator_action, action)

    def test_runtime_unknown_dominates_candidate_completion(self) -> None:
        result = derive_native_run_disposition(
            NativeRunFacts(
                NativeRunPhase.RUN_RECORDED,
                NativeToolRecoveryConsequence.OBSERVATION_ONLY,
                termination_code="runtime_unknown",
                has_tool_observations=True,
                has_unknown_observation=True,
                has_candidate_conclusion=True,
            )
        )
        self.assertIs(result.replacement_scope, ReplacementScope.FORBIDDEN)
        self.assertIs(result.completion_route, CompletionRoute.RECONCILE_UNKNOWN)
        self.assertIs(
            result.operator_action,
            NativeRunOperatorAction.RECONCILE_CURRENT_UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
