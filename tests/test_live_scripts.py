from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.live_runtime_read import build_plan


class LiveScriptContractTests(unittest.TestCase):
    def test_live_read_uses_logical_repository_ref_and_explicit_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            plan, resolver, resolved = build_plan(
                task_token="live-script-contract",
                source_repo=source,
                source_revision="a" * 40,
                repository_id="repository:live-script-contract",
                relative_path="README.md",
            )
            self.assertEqual(resolver.resolve(plan.repository), resolved)
        encoded = plan.to_dict()
        self.assertEqual(plan.repository.repository_id, "repository:live-script-contract")
        self.assertEqual(plan.repository.revision, "a" * 40)
        self.assertEqual(encoded["schemaVersion"], 2)
        self.assertNotIn("sourceRepo", encoded)


if __name__ == "__main__":
    unittest.main()
