from __future__ import annotations

import json
from pathlib import Path
import tempfile
import textwrap
import unittest

from ordivon_host.cognition import (
    CandidateAction,
    CodexCliModelAdapter,
    CognitionRequest,
    ContextCompiler,
    DecisionKind,
    HermesCliModelAdapter,
)

WORLD = "sha256:" + ("a" * 64)


def context():
    return ContextCompiler().compile(
        CognitionRequest(
            task_id="task:adapter",
            world_digest=WORLD,
            blocks=(),
            candidates=(
                CandidateAction(
                    "action:inspect",
                    DecisionKind.INSPECT_WORLD,
                    "Inspect current world state.",
                ),
                CandidateAction(
                    "action:wait",
                    DecisionKind.WAIT,
                    "Wait for another signal.",
                ),
            ),
        ),
        token_budget=4_000,
    )


class CognitionAdapterTests(unittest.TestCase):
    def test_codex_adapter_is_ephemeral_and_parses_structured_decision(self) -> None:
        compiled = context()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent(
                    """
                    import json
                    import pathlib
                    import sys

                    arguments = sys.argv[1:]
                    output = pathlib.Path(arguments[arguments.index('--output-last-message') + 1])
                    prompt = sys.stdin.read()
                    marker = 'contextDigest='
                    digest = prompt.split(marker, 1)[1].splitlines()[0]
                    payload = json.loads(prompt[prompt.index('{'):])
                    action = next(
                        item for item in payload['allowedActions']
                        if item['kind'] == 'inspect-world'
                    )
                    output.write_text(json.dumps({
                        'contextDigest': digest,
                        'actionId': action['actionId'],
                        'kind': action['kind'],
                        'effectId': action['effectId'],
                        'bindingId': action['bindingId'],
                        'dispatchId': action['dispatchId'],
                        'requiredWorldDigest': action['requiredWorldDigest'],
                        'rationale': 'Inspection best reduces uncertainty.',
                    }))
                    """
                ).lstrip()
            )
            executable.chmod(0o755)
            adapter = CodexCliModelAdapter(
                working_directory=root / "work",
                executable=str(executable),
                timeout_seconds=10,
            )
            decision = adapter.decide(compiled)
        self.assertEqual(decision.context_digest, compiled.digest)
        self.assertEqual(decision.kind, DecisionKind.INSPECT_WORLD)
        self.assertEqual(decision.action_id, "action:inspect")
        self.assertIn("ephemeral", adapter.adapter_id)
        evidence = adapter.evidence_metadata()
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertTrue(evidence["ephemeral"])
        self.assertFalse(evidence["persistentSessionRetained"])
        self.assertEqual(evidence["toolsExposedByHost"], [])

    def test_hermes_adapter_isolated_home_tools_memory_and_session(self) -> None:
        compiled = context()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.env"
            credentials.write_text(
                "DEEPSEEK_API_KEY=test-secret\n"
                "DEEPSEEK_BASE_URL=https://should-be-replaced.invalid\n"
                "UNRELATED_SECRET=must-not-be-copied\n"
            )
            executable = root / "fake-hermes"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent(
                    """
                    import json
                    import os
                    import pathlib
                    import sys

                    arguments = sys.argv[1:]
                    prompt = arguments[arguments.index('--oneshot') + 1]
                    model = arguments[arguments.index('--model') + 1]
                    provider = arguments[arguments.index('--provider') + 1]
                    usage_path = pathlib.Path(arguments[arguments.index('--usage-file') + 1])
                    hermes_home = pathlib.Path(os.environ['HERMES_HOME'])
                    user_home = pathlib.Path(os.environ['HOME'])
                    assert hermes_home != user_home
                    assert (hermes_home / '.no-bundled-skills').exists()
                    config = (hermes_home / 'config.yaml').read_text()
                    assert 'cli: []' in config
                    assert 'mcp_servers: {}' in config
                    assert 'memory_enabled: false' in config
                    assert 'write_json_snapshots: false' in config
                    env_lines = (hermes_home / '.env').read_text().splitlines()
                    assert len(env_lines) == 2
                    assert env_lines[0] == 'DEEPSEEK_API_KEY=test-secret'
                    assert env_lines[1] == 'DEEPSEEK_BASE_URL=https://api.deepseek.com'
                    assert all('UNRELATED_SECRET' not in line for line in env_lines)
                    digest = prompt.split('contextDigest=', 1)[1].splitlines()[0]
                    payload = json.loads(prompt[prompt.index('{'):])
                    action = next(
                        item for item in payload['allowedActions']
                        if item['kind'] == 'wait'
                    )
                    usage_path.write_text(json.dumps({
                        'model': model,
                        'provider': provider,
                        'completed': True,
                        'failed': False,
                        'api_calls': 1,
                        'input_tokens': 120,
                        'output_tokens': 20,
                        'reasoning_tokens': 5,
                        'total_tokens': 145,
                        'estimated_cost_usd': 0.001,
                    }))
                    print(json.dumps({
                        'contextDigest': digest,
                        'actionId': action['actionId'],
                        'kind': action['kind'],
                        'effectId': action['effectId'],
                        'bindingId': action['bindingId'],
                        'dispatchId': action['dispatchId'],
                        'requiredWorldDigest': action['requiredWorldDigest'],
                        'rationale': 'Waiting is preferred in this fake provider.',
                    }))
                    """
                ).lstrip()
            )
            executable.chmod(0o755)
            adapter = HermesCliModelAdapter(
                working_directory=root / "work",
                model="deepseek-v4-pro",
                provider="deepseek",
                credential_env_path=credentials,
                executable=str(executable),
                timeout_seconds=10,
            )
            decision = adapter.decide(compiled)
        self.assertEqual(decision.context_digest, compiled.digest)
        self.assertEqual(decision.kind, DecisionKind.WAIT)
        self.assertEqual(decision.action_id, "action:wait")
        self.assertIn("isolated", adapter.adapter_id)
        evidence = adapter.evidence_metadata()
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["apiCalls"], 1)
        self.assertEqual(evidence["totalTokens"], 145)
        self.assertTrue(evidence["isolatedHome"])
        self.assertFalse(evidence["persistentSessionRetained"])
        self.assertEqual(evidence["enabledToolsets"], [])
        self.assertFalse(evidence["memoryLoaded"])
        self.assertNotIn("test-secret", json.dumps(evidence))

    def test_model_decision_decoder_is_strict(self) -> None:
        compiled = context()
        value = {
            "contextDigest": compiled.digest,
            "actionId": "action:wait",
            "kind": "wait",
            "effectId": None,
            "bindingId": None,
            "dispatchId": None,
            "requiredWorldDigest": None,
            "rationale": "Wait.",
            "extra": True,
        }
        from ordivon_host.cognition import ModelDecision

        with self.assertRaisesRegex(ValueError, "fields differ"):
            ModelDecision.from_dict(value)

    def test_compiled_context_is_json_serializable(self) -> None:
        compiled = context()
        encoded = json.dumps(compiled.to_dict(), sort_keys=True)
        self.assertIn(compiled.digest, encoded)


if __name__ == "__main__":
    unittest.main()
