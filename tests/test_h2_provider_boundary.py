from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
import unittest

import ordivon_host
import ordivon_host.cognition as cognition
import ordivon_host.providers as providers
from ordivon_host.cognition import CognitionTurnHost, OpenProposalHost

ROOT = Path(__file__).resolve().parents[1]


class HostH2ProviderBoundaryTests(unittest.TestCase):
    def test_current_public_surfaces_do_not_advertise_physical_provider_execution(self) -> None:
        for name in (
            "ProviderSettings",
            "CodexCliModelAdapter",
            "HermesCliModelAdapter",
            "CodexCliProposalAdapter",
            "ModelGateway",
            "ProposalGateway",
        ):
            self.assertNotIn(name, ordivon_host.__all__)
            self.assertNotIn(name, cognition.__all__)
        self.assertFalse(hasattr(CognitionTurnHost, "decide"))
        self.assertFalse(hasattr(OpenProposalHost, "propose"))

    def test_current_provider_package_contains_only_durable_invocation_records(self) -> None:
        self.assertEqual(
            set(providers.__all__),
            {
                "ModelInvocationIntent",
                "ModelInvocationObservation",
                "ModelInvocationOutputObservation",
                "ModelInvocationReceipt",
            },
        )

    def test_current_writer_modules_have_no_provider_process_execution(self) -> None:
        for relative in (
            "src/ordivon_host/cognition/turn.py",
            "src/ordivon_host/cognition/proposal_turn.py",
            "src/ordivon_host/config.py",
            "src/ordivon_host/cli.py",
            "src/ordivon_host/recovery.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            self.assertNotIn("subprocess", imports, relative)
            self.assertNotIn("legacy_provider_execution", source, relative)
            self.assertNotIn("gateway.invoke", source, relative)
            self.assertNotIn("gateway.decide", source, relative)

    def test_fresh_current_cognition_import_does_not_load_legacy_execution(self) -> None:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys; import ordivon_host.cognition; "
                    "print(json.dumps([name for name in sys.modules "
                    "if name.startswith('ordivon_host.legacy_provider_execution')]))"
                ),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(probe.stdout), [])

    def test_legacy_physical_execution_is_explicitly_namespaced(self) -> None:
        from ordivon_host import legacy_provider_execution

        self.assertIn("CodexCliModelAdapter", legacy_provider_execution.__all__)
        self.assertIn("HermesCliModelAdapter", legacy_provider_execution.__all__)
        self.assertIn("CodexCliProposalAdapter", legacy_provider_execution.__all__)


if __name__ == "__main__":
    unittest.main()
