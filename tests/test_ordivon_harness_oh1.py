from __future__ import annotations

import unittest

from anc_canonical import canonical_digest

from ordivon_host.harness.ordivon import (
    AgentRunConclusion,
    AgentToolCall,
    AgentToolDefinition,
    AgentTurnResult,
    CancellationToken,
    OrdivonAgentLoop,
    RunBudget,
    RunStopCode,
    ScriptedTurnAdapter,
    ToolObservation,
    ordivon_harness_manifest,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000

    def __call__(self) -> int:
        self.value += 1
        return self.value


class _Bridge:
    catalog_digest = canonical_digest({"catalog": "test"})

    def __init__(self, *, unknown: bool = False) -> None:
        self.unknown = unknown
        self.calls: list[tuple[AgentToolCall, str]] = []

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return (
            AgentToolDefinition(
                "read_workspace",
                "Read one test value.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"relativePath": {"type": "string"}},
                    "required": ["relativePath"],
                },
            ),
        )

    def execute(self, call: AgentToolCall, *, step_id: str) -> ToolObservation:
        self.calls.append((call, step_id))
        if self.unknown:
            return ToolObservation(
                call.tool_call_id,
                call.name,
                "unknown",
                {"error": {"type": "response_loss"}},
            )
        return ToolObservation(
            call.tool_call_id,
            call.name,
            "observed",
            {"content": "alpha", "digest": canonical_digest("alpha")},
        )


def _result(
    suffix: str,
    *,
    calls: tuple[AgentToolCall, ...] = (),
    conclusion: AgentRunConclusion | None = None,
) -> AgentTurnResult:
    return AgentTurnResult(
        model_call_id=f"model-call:{suffix}",
        model_id=ScriptedTurnAdapter.model_id,
        content=None,
        tool_calls=calls,
        conclusion=conclusion,
        usage={"inputTokens": 10, "outputTokens": 5},
        finish_reason="tool_calls" if calls else "stop",
        raw_response_digest=canonical_digest({"response": suffix}),
    )


class OrdivonHarnessOH1Tests(unittest.TestCase):
    def test_first_party_manifest_is_conservative_and_stable(self) -> None:
        manifest = ordivon_harness_manifest()
        self.assertEqual(manifest.harness_id, "ordivon-harness-v0")
        self.assertEqual(manifest.protocol_revision, "oh5")
        self.assertFalse(manifest.interrupt)
        self.assertTrue(manifest.tool_events)
        self.assertFalse(manifest.persistent_session)
        self.assertFalse(manifest.compaction)
        self.assertIn("ordivon.explicit-unknown.v0", manifest.extensions)
        self.assertIn("ordivon.deepseek-turn-adapter.v0", manifest.extensions)
        self.assertIn("ordivon.interrupt-between-turns.v0", manifest.extensions)
        self.assertIn("ordivon.native-run-contract.v0", manifest.extensions)
        self.assertIn("ordivon.tool-grant.v0", manifest.extensions)
        self.assertIn("ordivon.run-provenance.v0", manifest.extensions)
        self.assertIn("ordivon.native-run-recovery.v0", manifest.extensions)
        self.assertIn("ordivon.safe-abandonment.v0", manifest.extensions)
        self.assertIn("ordivon.provider-fault-taxonomy.v0", manifest.extensions)
        self.assertTrue(manifest.digest.startswith("sha256:"))

    def test_scripted_loop_runs_tool_observation_then_candidate_completion(self) -> None:
        call = AgentToolCall(
            "tool-call:read-1",
            "read_workspace",
            {"relativePath": "README.md"},
        )
        adapter = ScriptedTurnAdapter(
            (
                _result("one", calls=(call,)),
                _result(
                    "two",
                    conclusion=AgentRunConclusion(
                        "candidate_completed",
                        "The required Artifact is ready for Host verification.",
                    ),
                ),
            )
        )
        bridge = _Bridge()
        loop = OrdivonAgentLoop(
            adapter,
            bridge,
            budget=RunBudget(4, 4, 64_000, 10_000),
            clock_ms=_Clock(),
        )
        result = loop.run(
            harness_run_id="harness-run:test-oh1",
            assignment_id="assignment:test-oh1",
            context_digest=canonical_digest({"context": 1}),
            initial_messages=({"role": "user", "content": "inspect"},),
        )
        self.assertEqual(result.stop_code, RunStopCode.CANDIDATE_COMPLETED)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(adapter.requests), 2)
        self.assertEqual(adapter.requests[1].messages[-1]["role"], "tool")
        kinds = [event.kind for event in result.trace.events]
        self.assertEqual(kinds[0], "run_started")
        self.assertIn("tool_call_dispatched", kinds)
        self.assertEqual(kinds[-1], "run_stopped")
        self.assertTrue(result.trace.digest.startswith("sha256:"))

    def test_tool_definition_mismatch_stops_before_provider_call(self) -> None:
        class _PoisonedBridge(_Bridge):
            def definitions(self) -> tuple[AgentToolDefinition, ...]:
                original = super().definitions()[0]
                return (
                    AgentToolDefinition(
                        original.name,
                        original.description + " uncommitted overlay",
                        dict(original.input_schema),
                    ),
                )

        adapter = ScriptedTurnAdapter(
            (
                _result(
                    "unused-catalog-mismatch",
                    conclusion=AgentRunConclusion("needs_input", "unused"),
                ),
            )
        )
        committed_definitions = _Bridge().definitions()
        expected = canonical_digest(
            [tool.to_dict() for tool in committed_definitions]
        )
        result = OrdivonAgentLoop(
            adapter,
            _PoisonedBridge(),
            budget=RunBudget(2, 2, 64_000, 10_000),
            expected_tool_definition_digest=expected,
            clock_ms=_Clock(),
        ).run(
            harness_run_id="harness-run:test-tool-catalog-mismatch",
            assignment_id="assignment:test-tool-catalog-mismatch",
            context_digest=canonical_digest({"context": "catalog-mismatch"}),
            initial_messages=({"role": "user", "content": "inspect"},),
        )
        self.assertEqual(result.stop_code, RunStopCode.TOOL_CATALOG_MISMATCH)
        self.assertEqual(result.model_calls, 0)
        self.assertFalse(adapter.requests)
        self.assertEqual(
            [event.kind for event in result.trace.events],
            ["run_started", "tool_catalog_integrity_failed", "run_stopped"],
        )

    def test_invalid_expected_tool_definition_digest_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Tool-definition digest"):
            OrdivonAgentLoop(
                ScriptedTurnAdapter(
                    (
                        _result(
                            "unused-invalid-digest",
                            conclusion=AgentRunConclusion("needs_input", "unused"),
                        ),
                    )
                ),
                _Bridge(),
                budget=RunBudget(2, 2, 64_000, 10_000),
                expected_tool_definition_digest="sha256:not-a-digest",
            )

    def test_model_budget_stops_before_an_unavailable_next_turn(self) -> None:
        call = AgentToolCall(
            "tool-call:read-budget",
            "read_workspace",
            {"relativePath": "README.md"},
        )
        adapter = ScriptedTurnAdapter((_result("budget", calls=(call,)),))
        loop = OrdivonAgentLoop(
            adapter,
            _Bridge(),
            budget=RunBudget(1, 2, 64_000, 10_000),
            clock_ms=_Clock(),
        )
        result = loop.run(
            harness_run_id="harness-run:test-budget",
            assignment_id="assignment:test-budget",
            context_digest=canonical_digest({"context": "budget"}),
            initial_messages=({"role": "user", "content": "inspect"},),
        )
        self.assertEqual(result.stop_code, RunStopCode.BUDGET_EXHAUSTED)
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.tool_calls, 1)

    def test_runtime_unknown_stops_without_second_model_call(self) -> None:
        call = AgentToolCall(
            "tool-call:unknown",
            "read_workspace",
            {"relativePath": "README.md"},
        )
        adapter = ScriptedTurnAdapter((_result("unknown", calls=(call,)),))
        loop = OrdivonAgentLoop(
            adapter,
            _Bridge(unknown=True),
            budget=RunBudget(3, 3, 64_000, 10_000),
            clock_ms=_Clock(),
        )
        result = loop.run(
            harness_run_id="harness-run:test-unknown",
            assignment_id="assignment:test-unknown",
            context_digest=canonical_digest({"context": "unknown"}),
            initial_messages=({"role": "user", "content": "inspect"},),
        )
        self.assertEqual(result.stop_code, RunStopCode.RUNTIME_UNKNOWN)
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(len(adapter.requests), 1)

    def test_pre_cancelled_run_calls_neither_model_nor_tool(self) -> None:
        adapter = ScriptedTurnAdapter(
            (
                _result(
                    "unused",
                    conclusion=AgentRunConclusion("needs_input", "unused"),
                ),
            )
        )
        bridge = _Bridge()
        cancellation = CancellationToken()
        cancellation.cancel()
        result = OrdivonAgentLoop(
            adapter,
            bridge,
            budget=RunBudget(3, 3, 64_000, 10_000),
            clock_ms=_Clock(),
        ).run(
            harness_run_id="harness-run:test-cancel",
            assignment_id="assignment:test-cancel",
            context_digest=canonical_digest({"context": "cancel"}),
            initial_messages=({"role": "user", "content": "inspect"},),
            cancellation=cancellation,
        )
        self.assertEqual(result.stop_code, RunStopCode.CANCELLED)
        self.assertEqual(result.model_calls, 0)
        self.assertFalse(bridge.calls)


if __name__ == "__main__":
    unittest.main()
