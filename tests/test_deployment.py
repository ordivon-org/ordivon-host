from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/ordivon-host-deploy"
LOADER = importlib.machinery.SourceFileLoader("ordivon_host_deploy", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
if SPEC is None:
    raise RuntimeError("cannot load deployment script")
module = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(module)


class HostDeploymentOperatorTests(unittest.TestCase):
    def test_tree_description_binds_bytes_modes_and_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            file = root / "value.txt"
            file.write_text("one\n", encoding="utf-8")
            file.chmod(0o644)
            (root / "link").symlink_to("value.txt")
            first = module.tree_description(root)
            file.write_text("two\n", encoding="utf-8")
            second = module.tree_description(root)
            self.assertNotEqual(first["digest"], second["digest"])
            file.write_text("one\n", encoding="utf-8")
            file.chmod(0o600)
            third = module.tree_description(root)
            self.assertNotEqual(first["digest"], third["digest"])
            file.chmod(0o644)
            (root / "link").unlink()
            (root / "link").symlink_to("other.txt")
            fourth = module.tree_description(root)
            self.assertNotEqual(first["digest"], fourth["digest"])

    def test_switch_current_is_atomic_and_release_id_is_not_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "host"
            releases = root / "releases"
            releases.mkdir(parents=True)
            commit = "a" * 40
            first_id = commit
            second_id = commit + "-123456789abc"
            for release_id in (first_id, second_id):
                release = releases / release_id
                release.mkdir()
                (release / "COMMIT").write_text(commit + "\n", encoding="utf-8")
            module.switch_current(root, first_id)
            self.assertEqual(module.inspect_current(root)["releaseId"], first_id)
            module.switch_current(root, second_id)
            current = module.inspect_current(root)
            self.assertEqual(current["releaseId"], second_id)
            self.assertEqual(current["commit"], commit)

    def test_status_marks_legacy_release_unreceipted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "host"
            receipts = Path(directory) / "receipts"
            release = root / "releases" / ("b" * 40)
            release.mkdir(parents=True)
            (release / "COMMIT").write_text("b" * 40 + "\n", encoding="utf-8")
            module.switch_current(root, release.name)
            status = module.deployment_status(root, receipts)
            self.assertEqual(status["status"], "unreceipted")
            self.assertIsNone(status["receipt"])

    def test_status_uses_previous_content_after_receipted_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "host"
            receipts = Path(directory) / "receipts"
            receipts.mkdir()
            previous_id = "d" * 40
            deployed_id = "e" * 40 + "-abcdef012345"
            previous_release = root / "releases" / previous_id
            deployed_release = root / "releases" / deployed_id
            previous_release.mkdir(parents=True)
            deployed_release.mkdir(parents=True)
            (previous_release / "COMMIT").write_text("d" * 40 + "\n", encoding="utf-8")
            (deployed_release / "COMMIT").write_text("e" * 40 + "\n", encoding="utf-8")
            previous_content = module.tree_description(previous_release)
            candidate_content = module.tree_description(deployed_release)
            receipt = receipts / "20260808T000000Z-test"
            receipt.mkdir()
            module.write_json_atomic(
                receipt / "manifest.json",
                {
                    "schemaVersion": 1,
                    "candidate": {"content": candidate_content},
                    "previous": {
                        "releaseId": previous_id,
                        "commit": "d" * 40,
                        "content": previous_content,
                    },
                },
            )
            module.write_json_atomic(
                receipt / "result.json",
                {"status": "deployed", "releaseId": deployed_id},
            )
            module.write_json_atomic(
                receipt / "rollback-result.json",
                {"status": "restored_previous", "releaseId": previous_id},
            )
            module.switch_current(root, previous_id)
            status = module.deployment_status(root, receipts)
            self.assertEqual(status["status"], "healthy")
            self.assertEqual(status["receipt"]["event"], "rollback-result.json")
            self.assertTrue(status["contentMatchesReceipt"])

    def test_candidate_validation_detects_tree_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            release = candidate / "release"
            release.mkdir(parents=True)
            commit = "c" * 40
            (release / "COMMIT").write_text(commit + "\n", encoding="utf-8")
            source = Path(directory) / "source"
            source.mkdir()
            manifest = {
                "schemaVersion": 1,
                "releaseId": candidate.name,
                "commit": commit,
                "sourceRepo": str(source),
                "sourceMaterialization": "detached_git_checkout",
                "content": module.tree_description(release),
            }
            module.write_json_atomic(candidate / "manifest.json", manifest)
            _, blockers = module.validate_candidate(candidate, commit, source)
            self.assertEqual(blockers, [])
            (release / "extra").write_text("tamper", encoding="utf-8")
            _, blockers = module.validate_candidate(candidate, commit, source)
            self.assertIn("candidate release tree does not match manifest content digest", blockers)


if __name__ == "__main__":
    unittest.main()
