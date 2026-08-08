from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def release_snapshot(release: Path) -> dict[str, object]:
    return {
        "releaseId": release.name,
        "commit": (release / "COMMIT").read_text(encoding="utf-8").strip(),
        "path": str(release),
        "content": module.tree_description(release),
        "pythonRuntime": module.python_runtime_dependency(
            release / "venv" / "bin" / "python"
        ),
    }


def make_candidate(
    candidate_root: Path,
    release_id: str,
    commit: str,
    python_executable: Path,
    *,
    schema_version: int | None = None,
) -> tuple[Path, dict[str, object]]:
    candidate = candidate_root / release_id
    release = make_release(candidate, "release", commit, python_executable)
    manifest = {
        "schemaVersion": (
            module.SCHEMA_VERSION if schema_version is None else schema_version
        ),
        "kind": "ordivon.host-release-candidate",
        "releaseId": release_id,
        "commit": commit,
        "content": module.tree_description(release),
        "pythonRuntime": module.python_runtime_dependency(python_executable),
        "pythonRuntimePolicy": {"kind": "test-stable-runtime"},
    }
    module.write_json_atomic(candidate / "manifest.json", manifest)
    return candidate, manifest


def make_deployment_receipt(
    receipt_root: Path,
    name: str,
    candidate_manifest: dict[str, object],
    previous: dict[str, object],
    *,
    result_status: str = "deployed",
    rollback_result: dict[str, object] | None = None,
) -> Path:
    receipt = receipt_root / name
    receipt.mkdir(parents=True)
    module.write_json_atomic(
        receipt / "manifest.json",
        {
            "schemaVersion": module.SCHEMA_VERSION,
            "kind": "ordivon.host-deployment-receipt",
            "releaseId": candidate_manifest["releaseId"],
            "candidate": candidate_manifest,
            "previous": previous,
        },
    )
    module.write_json_atomic(
        receipt / "plan.json",
        {
            "schemaVersion": module.SCHEMA_VERSION,
            "candidateDir": str(
                receipt_root.parent / "candidates" / str(candidate_manifest["releaseId"])
            ),
        },
    )
    module.write_json_atomic(
        receipt / "result.json",
        {
            "schemaVersion": module.SCHEMA_VERSION,
            "status": result_status,
            "releaseId": candidate_manifest["releaseId"],
        },
    )
    if rollback_result is not None:
        module.write_json_atomic(receipt / "rollback-result.json", rollback_result)
    return receipt


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
                "pythonRuntimePolicy": {"kind": "test-stable-runtime"},
                "effectiveDigest": effective,
            }
            module.write_json_atomic(candidate / "manifest.json", manifest)
            with mock.patch.object(
                module,
                "validate_python_runtime_stability",
                return_value={"kind": "test-stable-runtime"},
            ):
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
                    "pythonRuntimePolicy": {"kind": "test-stable-runtime"},
                    "effectiveDigest": effective,
                },
            )
            with mock.patch.object(
                module,
                "validate_python_runtime_stability",
                return_value={"kind": "test-stable-runtime"},
            ):
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

    def test_python_runtime_stability_requires_complete_checked_hash_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdlib = root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
            stdlib.mkdir(parents=True)
            source = stdlib / "example.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            cache = Path(
                py_compile.compile(
                    str(source),
                    doraise=True,
                    invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
                )
            )
            policy = module.validate_python_runtime_stability(
                Path(sys.executable), root
            )
            self.assertEqual(policy["sourceCount"], 1)
            self.assertEqual(policy["pycCount"], 1)
            self.assertEqual(policy["invalidationMode"], "checked-hash")
            cache.unlink()
            with self.assertRaisesRegex(RuntimeError, "not fully materialized"):
                module.validate_python_runtime_stability(Path(sys.executable), root)
            py_compile.compile(str(source), doraise=True)
            with self.assertRaisesRegex(RuntimeError, "not fully materialized"):
                module.validate_python_runtime_stability(Path(sys.executable), root)

    def test_lifecycle_plan_keeps_minimal_reversible_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            old = make_release(releases, "a" * 40, "a" * 40, executable)
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            obsolete_id = "d" * 40 + "-obsolete"
            make_candidate(
                candidate_root,
                obsolete_id,
                "d" * 40,
                executable,
                schema_version=module.SCHEMA_VERSION - 1,
            )
            unconsumed_id = "e" * 40 + "-unconsumed"
            make_candidate(candidate_root, unconsumed_id, "e" * 40, executable)
            make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertTrue(plan["eligible"], plan["blockers"])
            self.assertEqual(plan["transition"]["mode"], "deployed")
            protected_releases = {
                item["id"] for item in plan["protected"]["releases"]
            }
            self.assertEqual(protected_releases, {current_id, previous.name})
            self.assertEqual(
                {item["id"] for item in plan["protected"]["candidates"]},
                {current_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["retained"]["candidates"]},
                {unconsumed_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["releases"]},
                {old.name},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["candidates"]},
                {obsolete_id},
            )
            external = plan["retained"]["externalDependencies"]
            self.assertEqual(len(external), 1)
            self.assertEqual(external[0]["runtimeRoot"], str(runtime))
            self.assertEqual(external[0]["deletionAuthority"], "not_host")
            self.assertEqual(
                plan["retained"]["deploymentReceipts"][0]["policy"],
                "evidence_retained",
            )

    def test_substrate_claims_project_one_deduplicated_runtime_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            unclaimed_runtime, _ = make_runtime(base, "cpython-unclaimed")
            releases = release_root / "releases"
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            projection = module.substrate_claims(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertEqual(projection["status"], "ready")
            self.assertEqual(projection["consumer"], "ordivon-host")
            self.assertEqual(projection["currentReleaseId"], current_id)
            self.assertEqual(projection["blockers"], [])
            self.assertEqual(len(projection["claims"]), 1)
            claim = projection["claims"][0]
            self.assertTrue(claim["claimId"].startswith("sha256:"))
            self.assertEqual(claim["resourceKind"], "python_runtime")
            self.assertEqual(claim["resourceRoot"], str(runtime))
            self.assertEqual(claim["deletionAuthority"], "not_host")
            self.assertEqual(
                claim["reasons"],
                [
                    "current_release",
                    "recovery_candidate",
                    "reversible_transition_peer",
                ],
            )
            self.assertNotEqual(claim["resourceRoot"], str(unclaimed_runtime))
            self.assertIn(
                "absence_is_not_deletion_authority", projection["limitations"]
            )

    def test_substrate_claims_fail_closed_with_lifecycle_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import shutil

            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            candidate_path, candidate_manifest = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                candidate_manifest,
                release_snapshot(previous),
            )
            shutil.rmtree(candidate_path)
            projection = module.substrate_claims(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertEqual(projection["status"], "blocked")
            self.assertEqual(projection["claims"], [])
            self.assertTrue(
                any(
                    "recovery candidate is unreadable" in blocker
                    for blocker in projection["blockers"]
                )
            )

    def test_lifecycle_plan_reverses_protection_after_explicit_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            old = make_release(releases, "a" * 40, "a" * 40, executable)
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            displaced_id = "c" * 40 + "-deployed"
            displaced = make_release(
                releases, displaced_id, "c" * 40, executable
            )
            _, displaced_candidate = make_candidate(
                candidate_root, displaced_id, "c" * 40, executable
            )
            receipt = make_deployment_receipt(
                receipt_root,
                "001-deploy-and-rollback",
                displaced_candidate,
                release_snapshot(previous),
            )
            module.write_json_atomic(
                receipt / "rollback-result.json",
                {
                    "schemaVersion": module.SCHEMA_VERSION,
                    "status": "restored_previous",
                    "releaseId": previous.name,
                    "displaced": release_snapshot(displaced),
                },
            )
            module.switch_current(release_root, previous.name)
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertTrue(plan["eligible"], plan["blockers"])
            self.assertEqual(plan["transition"]["mode"], "explicit_rollback")
            self.assertEqual(
                {item["id"] for item in plan["protected"]["releases"]},
                {previous.name, displaced_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["protected"]["candidates"]},
                {displaced_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["releases"]},
                {old.name},
            )

    def test_lifecycle_plan_blocks_when_recovery_candidate_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            candidate_path, candidate_manifest = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                candidate_manifest,
                release_snapshot(previous),
            )
            import shutil

            shutil.rmtree(candidate_path)
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertFalse(plan["eligible"])
            self.assertTrue(
                any("recovery candidate is unreadable" in item for item in plan["blockers"])
            )

    def test_lifecycle_apply_retires_collects_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            lifecycle_root = base / "lifecycle"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            old = make_release(releases, "a" * 40, "a" * 40, executable)
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            obsolete_id = "d" * 40 + "-obsolete"
            obsolete, _ = make_candidate(
                candidate_root,
                obsolete_id,
                "d" * 40,
                executable,
                schema_version=module.SCHEMA_VERSION - 1,
            )
            deployment_receipt = make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            args = argparse.Namespace(
                release_root=release_root,
                candidate_root=candidate_root,
                receipt_root=receipt_root,
                python_runtime_root=runtime.parent,
                lifecycle_root=lifecycle_root,
                confirm_plan_digest=plan["planDigest"],
                lock_file=base / "deploy.lock",
            )
            result = module.apply_lifecycle_plan(args)
            self.assertEqual(result["status"], "pruned")
            self.assertFalse(old.exists())
            self.assertFalse(obsolete.exists())
            self.assertTrue((releases / current_id).is_dir())
            self.assertTrue((releases / previous.name).is_dir())
            self.assertTrue((candidate_root / current_id).is_dir())
            self.assertTrue(runtime.is_dir())
            self.assertTrue(deployment_receipt.is_dir())
            replay = module.apply_lifecycle_plan(args)
            self.assertEqual(replay["admission"], "existing")
            self.assertEqual(replay["planDigest"], plan["planDigest"])

    def test_lifecycle_plan_ignores_failed_candidate_after_automatic_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            old = make_release(releases, "a" * 40, "a" * 40, executable)
            current_id = "b" * 40 + "-current"
            current = make_release(releases, current_id, "b" * 40, executable)
            failed_id = "d" * 40 + "-failed"
            failed = make_release(releases, failed_id, "d" * 40, executable)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "b" * 40, executable
            )
            _, failed_candidate = make_candidate(
                candidate_root, failed_id, "d" * 40, executable
            )
            prior = make_deployment_receipt(
                receipt_root,
                "001-current-deployed",
                current_candidate,
                release_snapshot(old),
            )
            failed_receipt = make_deployment_receipt(
                receipt_root,
                "002-failed-deploy",
                failed_candidate,
                release_snapshot(current),
                result_status="rolled_back",
            )
            module.write_json_atomic(
                failed_receipt / "rollback-result.json",
                {
                    "schemaVersion": module.SCHEMA_VERSION,
                    "status": "restored_previous",
                    "releaseId": current_id,
                    "automatic": True,
                    "cause": "deployment_probe_or_activation_failure",
                },
            )
            module.switch_current(release_root, current_id)
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertTrue(plan["eligible"], plan["blockers"])
            self.assertEqual(plan["transition"]["mode"], "automatic_recovery")
            self.assertEqual(plan["transition"]["authorityReceipt"], str(prior))
            self.assertEqual(
                {item["id"] for item in plan["protected"]["releases"]},
                {old.name, current_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["protected"]["candidates"]},
                {current_id},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["releases"]},
                {failed.name},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["candidates"]},
                {failed_id},
            )

    def test_lifecycle_apply_recovers_after_collected_tombstone_before_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import shutil

            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            lifecycle_root = base / "lifecycle"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            old = make_release(releases, "a" * 40, "a" * 40, executable)
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            obsolete_id = "d" * 40 + "-obsolete"
            make_candidate(
                candidate_root,
                obsolete_id,
                "d" * 40,
                executable,
                schema_version=module.SCHEMA_VERSION - 1,
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            receipt = module.lifecycle_receipt_path(
                lifecycle_root, plan["planDigest"]
            )
            receipt.mkdir(parents=True)
            module.write_json_atomic(receipt / "plan.json", plan)
            first = plan["delete"]["releases"][0]
            tombstone = module.lifecycle_tombstone(
                first, release_root, candidate_root, plan["planDigest"]
            )
            tombstone.parent.mkdir(parents=True)
            Path(first["path"]).rename(tombstone)
            retired_item = {
                "kind": first["kind"],
                "id": first["id"],
                "tree": first["tree"],
                "tombstone": str(tombstone),
            }
            module.write_json_atomic(
                receipt / "retire-result.json",
                {
                    "schemaVersion": module.LIFECYCLE_SCHEMA_VERSION,
                    "kind": "ordivon.host-release-lifecycle-retirement",
                    "status": "retired",
                    "planDigest": plan["planDigest"],
                    "retired": [retired_item],
                    "retiredAtMs": 1,
                },
            )
            shutil.rmtree(tombstone)
            args = argparse.Namespace(
                release_root=release_root,
                candidate_root=candidate_root,
                receipt_root=receipt_root,
                python_runtime_root=runtime.parent,
                lifecycle_root=lifecycle_root,
                confirm_plan_digest=plan["planDigest"],
                lock_file=base / "deploy.lock",
            )
            result = module.apply_lifecycle_plan(args)
            self.assertEqual(result["status"], "pruned")
            self.assertFalse(old.exists())
            self.assertFalse((candidate_root / obsolete_id).exists())
            retire = module.read_json_object(receipt / "retire-result.json")
            self.assertEqual(
                {(item["kind"], item["id"]) for item in retire["retired"]},
                {
                    (item["kind"], item["id"])
                    for group in plan["delete"].values()
                    for item in group
                },
            )
            self.assertEqual(retire["retiredAtMs"], 1)

    def test_lifecycle_apply_rejects_inventory_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            lifecycle_root = base / "lifecycle"
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-current"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            make_candidate(
                candidate_root,
                "e" * 40 + "-new-unconsumed",
                "e" * 40,
                executable,
            )
            args = argparse.Namespace(
                release_root=release_root,
                candidate_root=candidate_root,
                receipt_root=receipt_root,
                python_runtime_root=runtime.parent,
                lifecycle_root=lifecycle_root,
                confirm_plan_digest=plan["planDigest"],
                lock_file=base / "deploy.lock",
            )
            with self.assertRaisesRegex(RuntimeError, "confirm-plan-digest"):
                module.apply_lifecycle_plan(args)


if __name__ == "__main__":
    unittest.main()
