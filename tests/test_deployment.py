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


def make_runtime(base: Path, name: str = "cpython-test") -> tuple[Path, Path]:
    parent = base / "python"
    runtime = parent / name
    executable = runtime / "bin" / "python3.12"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake-python-runtime\n")
    executable.chmod(0o755)
    library = runtime / "lib" / "python3.12"
    library.mkdir(parents=True)
    (library / "stdlib.txt").write_text("stdlib-one\n", encoding="utf-8")
    return runtime, executable


def make_release(
    releases: Path,
    release_id: str,
    commit: str,
    python_executable: Path,
) -> Path:
    release = releases / release_id
    (release / "venv" / "bin").mkdir(parents=True)
    (release / "COMMIT").write_text(commit + "\n", encoding="utf-8")
    (release / "venv" / "bin" / "python").symlink_to(python_executable)
    return release


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
            base = Path(directory)
            root = base / "host"
            releases = root / "releases"
            runtime, executable = make_runtime(base)
            commit = "a" * 40
            first_id = commit
            second_id = commit + "-123456789abc"
            make_release(releases, first_id, commit, executable)
            make_release(releases, second_id, commit, executable)
            module.switch_current(root, first_id)
            self.assertEqual(module.inspect_current(root)["releaseId"], first_id)
            module.switch_current(root, second_id)
            current = module.inspect_current(root)
            self.assertEqual(current["releaseId"], second_id)
            self.assertEqual(current["commit"], commit)
            self.assertEqual(current["pythonRuntime"]["runtimeRoot"], str(runtime))

    def test_status_marks_legacy_release_unreceipted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "host"
            receipts = base / "receipts"
            _, executable = make_runtime(base)
            release_id = "b" * 40
            make_release(root / "releases", release_id, release_id, executable)
            module.switch_current(root, release_id)
            status = module.deployment_status(root, receipts)
            self.assertEqual(status["status"], "unreceipted")
            self.assertIsNone(status["receipt"])

    def test_status_uses_previous_effective_bytes_after_receipted_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "host"
            receipts = base / "receipts"
            receipts.mkdir()
            _, executable = make_runtime(base)
            previous_id = "d" * 40
            deployed_id = "e" * 40 + "-abcdef012345"
            previous_release = make_release(
                root / "releases", previous_id, "d" * 40, executable
            )
            deployed_release = make_release(
                root / "releases", deployed_id, "e" * 40, executable
            )
            previous_content = module.tree_description(previous_release)
            candidate_content = module.tree_description(deployed_release)
            runtime_binding = module.python_runtime_dependency(executable)
            receipt = receipts / "20260808T000000Z-test"
            receipt.mkdir()
            module.write_json_atomic(
                receipt / "manifest.json",
                {
                    "schemaVersion": module.SCHEMA_VERSION,
                    "candidate": {
                        "content": candidate_content,
                        "pythonRuntime": runtime_binding,
                    },
                    "previous": {
                        "releaseId": previous_id,
                        "commit": "d" * 40,
                        "content": previous_content,
                        "pythonRuntime": runtime_binding,
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
            self.assertTrue(status["pythonRuntimeMatchesReceipt"])

    def test_candidate_validation_detects_release_tree_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime, executable = make_runtime(base)
            source = base / "source"
            source.mkdir()
            commit = "c" * 40
            staging = base / "staging"
            release = make_release(staging, "release", commit, executable)
            content = module.tree_description(release)
            runtime_binding = module.python_runtime_dependency(executable)
            effective = module.effective_release_digest(content, runtime_binding)
            release_id = f"{commit}-{effective[7:19]}"
            candidate = base / release_id
            staging.rename(candidate)
            release = candidate / "release"
            manifest = {
                "schemaVersion": module.SCHEMA_VERSION,
                "releaseId": release_id,
                "commit": commit,
                "sourceRepo": str(source),
                "sourceMaterialization": "detached_git_checkout",
                "content": content,
                "pythonRuntime": runtime_binding,
                "effectiveDigest": effective,
            }
            module.write_json_atomic(candidate / "manifest.json", manifest)
            _, blockers = module.validate_candidate(
                candidate, commit, source, runtime.parent
            )
            self.assertEqual(blockers, [])
            (release / "extra").write_text("tamper", encoding="utf-8")
            _, blockers = module.validate_candidate(
                candidate, commit, source, runtime.parent
            )
            self.assertIn(
                "candidate release tree does not match manifest content digest", blockers
            )

    def test_candidate_validation_detects_python_runtime_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime, executable = make_runtime(base)
            source = base / "source"
            source.mkdir()
            commit = "f" * 40
            staging = base / "staging"
            release = make_release(staging, "release", commit, executable)
            content = module.tree_description(release)
            runtime_binding = module.python_runtime_dependency(executable)
            effective = module.effective_release_digest(content, runtime_binding)
            release_id = f"{commit}-{effective[7:19]}"
            candidate = base / release_id
            staging.rename(candidate)
            module.write_json_atomic(
                candidate / "manifest.json",
                {
                    "schemaVersion": module.SCHEMA_VERSION,
                    "releaseId": release_id,
                    "commit": commit,
                    "sourceRepo": str(source),
                    "sourceMaterialization": "detached_git_checkout",
                    "content": content,
                    "pythonRuntime": runtime_binding,
                    "effectiveDigest": effective,
                },
            )
            _, blockers = module.validate_candidate(
                candidate, commit, source, runtime.parent
            )
            self.assertEqual(blockers, [])
            (runtime / "lib" / "python3.12" / "stdlib.txt").write_text(
                "stdlib-two\n", encoding="utf-8"
            )
            _, blockers = module.validate_candidate(
                candidate, commit, source, runtime.parent
            )
            self.assertIn(
                "candidate Python runtime differs from manifest binding", blockers
            )


if __name__ == "__main__":
    unittest.main()
