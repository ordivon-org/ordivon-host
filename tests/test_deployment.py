from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import py_compile
import sqlite3
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
    def test_candidate_prepare_uv_operations_are_offline(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        prepare = source[
            source.index("def prepare_candidate") : source.index("def load_candidate")
        ]
        self.assertIn('str(args.uv), "lock", "--check", "--offline"', prepare)
        self.assertIn('str(args.uv), "sync", "--offline"', prepare)
        self.assertIn('str(args.uv), "build", "--offline"', prepare)
        self.assertIn('str(args.uv), "pip", "install", "--offline"', prepare)
        self.assertIn('"UV_OFFLINE": "1"', prepare)

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

    def test_candidate_entrypoint_check_uses_isolated_state_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            venv = base / "release" / "venv"
            build_root = base / "build"
            build_root.mkdir(parents=True)
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs):
                commands.append(command)
                if command[0].endswith("ordivon-host") and command[-1] == "init":
                    self.assertIn("--state-root", command)
                    state_root = Path(command[command.index("--state-root") + 1])
                    self.assertEqual(state_root, build_root / ".entrypoint-check-state")
                    self.assertTrue(state_root.is_dir())
                    (state_root / "host.sqlite3").write_text("isolated", encoding="utf-8")
                if command[0].endswith("ordivon-host-mcp"):
                    self.assertIn("--state-root", command)
                    state_root = Path(command[command.index("--state-root") + 1])
                    self.assertEqual(state_root, build_root / ".entrypoint-check-state")
                    self.assertTrue((state_root / "host.sqlite3").is_file())
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.dict(
                module.os.environ,
                {"ORDIVON_HOST_STATE_ROOT": "/var/lib/ordivon/host"},
                clear=False,
            ), mock.patch.object(module, "run", side_effect=fake_run):
                module.validate_candidate_entrypoints(venv, build_root)

            self.assertEqual(len(commands), 3)
            self.assertEqual(commands[0], [str(venv / "bin/ordivon-host"), "--help"])
            self.assertEqual(
                commands[1],
                [
                    str(venv / "bin/ordivon-host"),
                    "--state-root",
                    str(build_root / ".entrypoint-check-state"),
                    "init",
                ],
            )
            self.assertEqual(
                commands[2],
                [
                    str(venv / "bin/ordivon-host-mcp"),
                    "--check",
                    "--state-root",
                    str(build_root / ".entrypoint-check-state"),
                ],
            )
            self.assertNotIn("/var/lib/ordivon/host", commands[1])
            self.assertNotIn("/var/lib/ordivon/host", commands[2])
            self.assertFalse((build_root / ".entrypoint-check-state").exists())

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

    def test_lifecycle_plan_retains_incident_evidence_without_treating_it_as_receipt(self) -> None:
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
            _, current_candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            make_deployment_receipt(
                receipt_root,
                "001-current",
                current_candidate,
                release_snapshot(previous),
            )
            incident = receipt_root / "incident-authority-audit-20260809"
            incident.mkdir()
            (incident / "evidence.json").write_text('{"kind":"incident-evidence"}', encoding="utf-8")

            plan = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertTrue(plan["eligible"], plan["blockers"])
            retained = {
                item["name"]: item for item in plan["retained"]["deploymentReceipts"]
            }
            self.assertEqual(
                retained[incident.name]["policy"], "incident_evidence_retained"
            )
            first_digest = plan["planDigest"]
            (incident / "evidence.json").write_text('{"kind":"changed"}', encoding="utf-8")
            changed = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertNotEqual(changed["planDigest"], first_digest)

            malformed = receipt_root / "002-malformed-receipt"
            malformed.mkdir()
            blocked = module.lifecycle_plan(
                release_root, candidate_root, receipt_root, runtime.parent
            )
            self.assertFalse(blocked["eligible"])
            self.assertTrue(
                any(
                    "deployment receipt manifest is unreadable" in blocker
                    and str(malformed) in blocker
                    for blocker in blocked["blockers"]
                )
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


    def test_journal_snapshot_restores_exact_preactivation_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "state"
            state.mkdir()
            database = state / "host.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE host_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO host_metadata VALUES ('schema_version', '4')")
                connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
                connection.execute("INSERT INTO marker VALUES ('before-activation')")
                connection.commit()
            finally:
                connection.close()
            snapshot = base / "receipt" / "authority-preactivation.sqlite3"
            metadata = module.snapshot_journal_database(state, snapshot)
            self.assertEqual(metadata["journalSchemaVersion"], 4)
            self.assertEqual(metadata["digest"], module.sha256_file(snapshot))

            connection = sqlite3.connect(database)
            try:
                connection.execute("UPDATE host_metadata SET value = '5' WHERE key = 'schema_version'")
                connection.execute("UPDATE marker SET value = 'candidate-wrote'")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(module.raw_journal_schema_version(state), 5)
            module.restore_journal_database(state, snapshot, metadata)
            self.assertEqual(module.raw_journal_schema_version(state), 4)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0],
                    "before-activation",
                )
            finally:
                connection.close()

            snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
            with self.assertRaisesRegex(RuntimeError, "snapshot digest differs"):
                module.verify_journal_snapshot(snapshot, metadata)

    def test_authority_transition_plan_detects_forward_and_backward_schema_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            current = {"path": str(base / "current")}
            candidate = base / "candidate"
            with mock.patch.object(module, "raw_journal_schema_version", return_value=4), mock.patch.object(
                module,
                "release_journal_schema_version",
                side_effect=[4, 5],
            ):
                transition, blockers = module.authority_transition_plan(
                    base / "state",
                    current,
                    candidate,
                )
            self.assertEqual(blockers, [])
            assert transition is not None
            self.assertTrue(transition["migrationRequired"])
            self.assertEqual(transition["liveSchemaVersion"], 4)
            self.assertEqual(transition["candidateSchemaVersion"], 5)
            self.assertEqual(
                transition["activationRollbackPolicy"],
                "restore-preactivation-journal-snapshot",
            )
            self.assertFalse(transition["explicitRollbackSupportedAfterSuccess"])

            with mock.patch.object(module, "raw_journal_schema_version", return_value=5), mock.patch.object(
                module,
                "release_journal_schema_version",
                side_effect=[5, 4],
            ):
                _transition, blockers = module.authority_transition_plan(
                    base / "state",
                    current,
                    candidate,
                )
            self.assertIn(
                "candidate Host Journal schema is older than live authority; backward migration is not supported",
                blockers,
            )

    def test_apply_schema_migration_restores_journal_before_previous_release_on_probe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "release-root"
            receipt_root = base / "receipts"
            state_root = base / "state"
            state_root.mkdir()
            (state_root / "host.sqlite3").write_bytes(b"placeholder")
            previous = {
                "releaseId": "previous-release",
                "commit": "a" * 40,
                "content": {"digest": "sha256:" + "1" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/previous"},
            }
            candidate = {
                "releaseId": "candidate-release",
                "content": {"digest": "sha256:" + "2" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/candidate"},
            }
            authority = {
                "schemaVersion": 1,
                "kind": "ordivon.host-deployment-authority-transition",
                "stateRoot": str(state_root),
                "databasePath": str(state_root / "host.sqlite3"),
                "liveSchemaVersion": 4,
                "previousReleaseSchemaVersion": 4,
                "candidateSchemaVersion": 5,
                "migrationRequired": True,
                "activationRollbackPolicy": "restore-preactivation-journal-snapshot",
                "explicitRollbackSupportedAfterSuccess": False,
            }
            plan = {
                "eligible": True,
                "blockers": [],
                "releaseId": "candidate-release",
                "candidate": candidate,
                "current": previous,
                "authorityTransition": authority,
            }
            args = argparse.Namespace(
                lock_file=base / "deploy.lock",
                confirm_release_id="candidate-release",
                receipt_root=receipt_root,
                release_root=release_root,
                state_root=state_root,
                service="host.service",
                systemctl=Path("/usr/bin/systemctl"),
                wait_seconds=1.0,
                env_file=base / "host.env",
                candidate_dir=base / "candidate",
                commit="b" * 40,
            )
            events: list[str] = []

            def record_run(command: list[str], **_kwargs):
                events.append(f"systemctl:{command[1]}")
                return mock.Mock(returncode=0, stdout="", stderr="")

            def record_snapshot(_state: Path, path: Path):
                events.append("snapshot")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"snapshot")
                return {
                    "schemaVersion": 1,
                    "kind": "ordivon.host-deployment-journal-snapshot",
                    "sourceDatabase": str(state_root / "host.sqlite3"),
                    "snapshotPath": str(path),
                    "journalSchemaVersion": 4,
                    "digest": "sha256:" + "3" * 64,
                    "byteLength": 8,
                }

            def record_switch(_root: Path, release_id: str):
                events.append(f"switch:{release_id}")

            def record_restore(_state: Path, _snapshot: Path, _metadata: dict[str, object]):
                events.append("restore-journal")

            probe_count = 0

            def record_probe(_env: Path, _wait: float):
                nonlocal probe_count
                probe_count += 1
                if probe_count == 1:
                    events.append("probe:candidate-failed")
                    raise RuntimeError("candidate probe failed")
                events.append("probe:previous-ok")
                return {"toolCount": 6}

            with mock.patch.object(module, "deployment_plan", return_value=plan), mock.patch.object(
                module, "copy_release", return_value=release_root / "releases" / "candidate-release"
            ), mock.patch.object(module, "run", side_effect=record_run), mock.patch.object(
                module, "wait_service_inactive"
            ), mock.patch.object(module, "wait_service_active"), mock.patch.object(
                module, "snapshot_journal_database", side_effect=record_snapshot
            ), mock.patch.object(module, "restore_journal_database", side_effect=record_restore), mock.patch.object(
                module, "switch_current", side_effect=record_switch
            ), mock.patch.object(module, "probe_host_mcp", side_effect=record_probe), mock.patch.object(
                module, "service_active", return_value=True
            ), mock.patch.object(module, "inspect_current", return_value=previous), mock.patch.object(
                module, "raw_journal_schema_version", return_value=4
            ):
                with self.assertRaisesRegex(RuntimeError, "candidate probe failed"):
                    module.apply_deployment(args)

            self.assertEqual(
                events,
                [
                    "systemctl:stop",
                    "snapshot",
                    "switch:candidate-release",
                    "systemctl:start",
                    "probe:candidate-failed",
                    "systemctl:stop",
                    "restore-journal",
                    "switch:previous-release",
                    "systemctl:start",
                    "probe:previous-ok",
                ],
            )
            receipts = list(receipt_root.iterdir())
            self.assertEqual(len(receipts), 1)
            result = module.read_json_object(receipts[0] / "result.json")
            rollback = module.read_json_object(receipts[0] / "rollback-result.json")
            self.assertEqual(result["status"], "rolled_back")
            self.assertTrue(result["rollback"]["authorityRestored"])
            self.assertTrue(rollback["activationOnly"])
            self.assertTrue(rollback["authorityRestored"])
            self.assertEqual(rollback["restoredJournalSchemaVersion"], 4)

    def test_apply_schema_migration_success_keeps_candidate_and_records_final_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "release-root"
            receipt_root = base / "receipts"
            state_root = base / "state"
            state_root.mkdir()
            (state_root / "host.sqlite3").write_bytes(b"placeholder")
            previous = {
                "releaseId": "previous-release",
                "commit": "a" * 40,
                "content": {"digest": "sha256:" + "1" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/previous"},
            }
            candidate = {
                "releaseId": "candidate-release",
                "content": {"digest": "sha256:" + "2" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/candidate"},
            }
            authority = {
                "schemaVersion": 1,
                "kind": "ordivon.host-deployment-authority-transition",
                "stateRoot": str(state_root),
                "databasePath": str(state_root / "host.sqlite3"),
                "liveSchemaVersion": 4,
                "previousReleaseSchemaVersion": 4,
                "candidateSchemaVersion": 5,
                "migrationRequired": True,
                "activationRollbackPolicy": "restore-preactivation-journal-snapshot",
                "explicitRollbackSupportedAfterSuccess": False,
            }
            plan = {
                "eligible": True,
                "blockers": [],
                "releaseId": "candidate-release",
                "candidate": candidate,
                "current": previous,
                "authorityTransition": authority,
            }
            args = argparse.Namespace(
                lock_file=base / "deploy.lock",
                confirm_release_id="candidate-release",
                receipt_root=receipt_root,
                release_root=release_root,
                state_root=state_root,
                service="host.service",
                systemctl=Path("/usr/bin/systemctl"),
                wait_seconds=1.0,
                env_file=base / "host.env",
                candidate_dir=base / "candidate",
                commit="b" * 40,
            )
            events: list[str] = []

            def record_run(command: list[str], **_kwargs):
                events.append(f"systemctl:{command[1]}")
                return mock.Mock(returncode=0, stdout="", stderr="")

            def record_snapshot(_state: Path, path: Path):
                events.append("snapshot")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"snapshot")
                return {
                    "schemaVersion": 1,
                    "kind": "ordivon.host-deployment-journal-snapshot",
                    "sourceDatabase": str(state_root / "host.sqlite3"),
                    "snapshotPath": str(path),
                    "journalSchemaVersion": 4,
                    "digest": "sha256:" + "3" * 64,
                    "byteLength": 8,
                }

            def record_switch(_root: Path, release_id: str):
                events.append(f"switch:{release_id}")

            with mock.patch.object(module, "deployment_plan", return_value=plan), mock.patch.object(
                module, "copy_release", return_value=release_root / "releases" / "candidate-release"
            ), mock.patch.object(module, "run", side_effect=record_run), mock.patch.object(
                module, "wait_service_inactive"
            ), mock.patch.object(module, "wait_service_active"), mock.patch.object(
                module, "snapshot_journal_database", side_effect=record_snapshot
            ), mock.patch.object(module, "switch_current", side_effect=record_switch), mock.patch.object(
                module, "probe_host_mcp", return_value={"toolCount": 6}
            ), mock.patch.object(module, "inspect_current", return_value={
                "releaseId": "candidate-release",
                "content": candidate["content"],
                "pythonRuntime": candidate["pythonRuntime"],
            }), mock.patch.object(module, "raw_journal_schema_version", return_value=5):
                result = module.apply_deployment(args)

            self.assertEqual(
                events,
                [
                    "systemctl:stop",
                    "snapshot",
                    "switch:candidate-release",
                    "systemctl:start",
                ],
            )
            self.assertEqual(result["status"], "deployed")
            self.assertEqual(result["finalJournalSchemaVersion"], 5)
            self.assertEqual(
                result["preactivationJournalSnapshot"]["journalSchemaVersion"],
                4,
            )
            receipts = list(receipt_root.iterdir())
            self.assertEqual(len(receipts), 1)
            retained = module.read_json_object(receipts[0] / "result.json")
            self.assertEqual(retained["finalJournalSchemaVersion"], 5)
            self.assertFalse(
                retained["authorityTransition"]["explicitRollbackSupportedAfterSuccess"]
            )

    def test_apply_schema_migration_snapshot_failure_restarts_unchanged_previous_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            receipt_root = base / "receipts"
            previous = {
                "releaseId": "previous-release",
                "commit": "a" * 40,
                "content": {"digest": "sha256:" + "1" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/previous"},
            }
            candidate = {
                "releaseId": "candidate-release",
                "content": {"digest": "sha256:" + "2" * 64},
                "pythonRuntime": {"runtimeRoot": "/runtime/candidate"},
            }
            authority = {
                "schemaVersion": 1,
                "kind": "ordivon.host-deployment-authority-transition",
                "liveSchemaVersion": 4,
                "previousReleaseSchemaVersion": 4,
                "candidateSchemaVersion": 5,
                "migrationRequired": True,
            }
            plan = {
                "eligible": True,
                "blockers": [],
                "releaseId": "candidate-release",
                "candidate": candidate,
                "current": previous,
                "authorityTransition": authority,
            }
            args = argparse.Namespace(
                lock_file=base / "deploy.lock",
                confirm_release_id="candidate-release",
                receipt_root=receipt_root,
                release_root=base / "release-root",
                state_root=base / "state",
                service="host.service",
                systemctl=Path("/usr/bin/systemctl"),
                wait_seconds=1.0,
                env_file=base / "host.env",
                candidate_dir=base / "candidate",
                commit="b" * 40,
            )
            events: list[str] = []

            def record_run(command: list[str], **_kwargs):
                events.append(f"systemctl:{command[1]}")
                return mock.Mock(returncode=0, stdout="", stderr="")

            def fail_snapshot(_state: Path, _path: Path):
                events.append("snapshot-failed")
                raise RuntimeError("snapshot failed")

            def record_probe(_env: Path, _wait: float):
                events.append("probe:previous-ok")
                return {"toolCount": 6}

            with mock.patch.object(module, "deployment_plan", return_value=plan), mock.patch.object(
                module, "copy_release", return_value=base / "candidate-release"
            ), mock.patch.object(module, "run", side_effect=record_run), mock.patch.object(
                module, "wait_service_inactive"
            ), mock.patch.object(module, "wait_service_active"), mock.patch.object(
                module, "snapshot_journal_database", side_effect=fail_snapshot
            ), mock.patch.object(module, "service_active", return_value=False), mock.patch.object(
                module, "raw_journal_schema_version", return_value=4
            ), mock.patch.object(module, "probe_host_mcp", side_effect=record_probe), mock.patch.object(
                module, "inspect_current", return_value=previous
            ), mock.patch.object(module, "switch_current") as switch:
                with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                    module.apply_deployment(args)
                switch.assert_not_called()

            self.assertEqual(
                events,
                [
                    "systemctl:stop",
                    "snapshot-failed",
                    "systemctl:start",
                    "probe:previous-ok",
                ],
            )
            receipt = next(receipt_root.iterdir())
            rollback = module.read_json_object(receipt / "rollback-result.json")
            self.assertTrue(rollback["activationOnly"])
            self.assertFalse(rollback["authorityRestored"])
            self.assertTrue(rollback["authorityUnchanged"])
            self.assertEqual(rollback["restoredJournalSchemaVersion"], 4)

    def test_explicit_rollback_rejects_successful_schema_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            receipt_root = base / "receipts"
            receipt = receipt_root / "migration"
            receipt.mkdir(parents=True)
            release_root = base / "release-root"
            state_root = base / "state"
            env_file = base / "host.env"
            module.write_json_atomic(
                receipt / "manifest.json",
                {
                    "schemaVersion": module.SCHEMA_VERSION,
                    "releaseRoot": str(release_root),
                    "stateRoot": str(state_root),
                    "envFile": str(env_file),
                    "service": "host.service",
                    "previous": {"releaseId": "previous-release"},
                    "authorityTransition": {
                        "schemaVersion": 1,
                        "kind": "ordivon.host-deployment-authority-transition",
                        "migrationRequired": True,
                    },
                },
            )
            args = argparse.Namespace(
                lock_file=base / "deploy.lock",
                receipt=receipt,
                receipt_root=receipt_root,
                release_root=release_root,
                state_root=state_root,
                env_file=env_file,
                service="host.service",
                confirm_release_id="previous-release",
                systemctl=Path("/usr/bin/systemctl"),
                wait_seconds=1.0,
            )
            with mock.patch.object(module, "switch_current") as switch:
                with self.assertRaisesRegex(RuntimeError, "schema migration is unsupported"):
                    module.rollback_deployment(args)
                switch.assert_not_called()

    def test_lifecycle_does_not_retain_schema_incompatible_previous_release_as_reversible_peer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release_root = base / "host"
            candidate_root = base / "candidates"
            receipt_root = base / "deployments"
            state_root = base / "state"
            state_root.mkdir()
            connection = sqlite3.connect(state_root / "host.sqlite3")
            try:
                connection.execute(
                    "CREATE TABLE host_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO host_metadata VALUES ('schema_version', '5')"
                )
                connection.commit()
            finally:
                connection.close()
            runtime, executable = make_runtime(base)
            releases = release_root / "releases"
            previous = make_release(releases, "b" * 40, "b" * 40, executable)
            current_id = "c" * 40 + "-schema5"
            make_release(releases, current_id, "c" * 40, executable)
            module.switch_current(release_root, current_id)
            _, candidate = make_candidate(
                candidate_root, current_id, "c" * 40, executable
            )
            receipt = make_deployment_receipt(
                receipt_root,
                "001-schema-migration",
                candidate,
                release_snapshot(previous),
            )
            manifest = module.read_json_object(receipt / "manifest.json")
            manifest["stateRoot"] = str(state_root)
            manifest["authorityTransition"] = {
                "schemaVersion": 1,
                "kind": "ordivon.host-deployment-authority-transition",
                "stateRoot": str(state_root),
                "liveSchemaVersion": 4,
                "previousReleaseSchemaVersion": 4,
                "candidateSchemaVersion": 5,
                "migrationRequired": True,
                "explicitRollbackSupportedAfterSuccess": False,
            }
            module.write_json_atomic(receipt / "manifest.json", manifest)

            status = module.deployment_status(release_root, receipt_root)
            self.assertEqual(status["status"], "healthy")
            self.assertTrue(status["authoritySchemaMatchesReceipt"])
            self.assertEqual(status["observedAuthoritySchemaVersion"], 5)
            self.assertEqual(status["expectedAuthoritySchemaVersion"], 5)
            self.assertFalse(status["explicitRollbackSupported"])
            plan = module.lifecycle_plan(
                release_root,
                candidate_root,
                receipt_root,
                runtime.parent,
            )
            self.assertTrue(plan["eligible"], plan["blockers"])
            self.assertEqual(plan["transition"]["mode"], "deployed_schema_migration")
            self.assertEqual(
                {item["id"] for item in plan["protected"]["releases"]},
                {current_id},
            )
            self.assertEqual(plan["protected"]["candidates"], [])
            self.assertEqual(
                {item["id"] for item in plan["delete"]["releases"]},
                {previous.name},
            )
            self.assertEqual(
                {item["id"] for item in plan["delete"]["candidates"]},
                {current_id},
            )

    def test_migration_sidecar_reconciliation_removes_only_activation_created_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            authority = {"liveSchemaVersion": 4, "candidateSchemaVersion": 5}
            prestate = module.migration_sidecar_prestate(state, authority)
            sidecar = state / "host.sqlite3.pre-schema-v5.sqlite3"
            sidecar.write_bytes(b"candidate-sidecar")
            (state / "host.sqlite3.pre-schema-v5.sqlite3-wal").write_bytes(b"")
            (state / "host.sqlite3.pre-schema-v5.sqlite3-shm").write_bytes(b"transient")
            result = module.reconcile_migration_sidecars(prestate)
            self.assertFalse(sidecar.exists())
            self.assertFalse((state / "host.sqlite3.pre-schema-v5.sqlite3-wal").exists())
            self.assertFalse((state / "host.sqlite3.pre-schema-v5.sqlite3-shm").exists())
            self.assertEqual(result["preserved"], [])
            self.assertEqual(len(result["removed"]), 3)

    def test_migration_sidecar_reconciliation_preserves_exact_preactivation_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            sidecar = state / "host.sqlite3.pre-schema-v5.sqlite3"
            sidecar.write_bytes(b"preactivation-sidecar")
            authority = {"liveSchemaVersion": 4, "candidateSchemaVersion": 5}
            prestate = module.migration_sidecar_prestate(state, authority)
            result = module.reconcile_migration_sidecars(prestate)
            self.assertEqual(sidecar.read_bytes(), b"preactivation-sidecar")
            self.assertEqual(result["removed"], [])
            self.assertEqual(result["preserved"], [str(sidecar)])
            sidecar.write_bytes(b"changed-during-activation")
            with self.assertRaisesRegex(RuntimeError, "changed during activation"):
                module.reconcile_migration_sidecars(prestate)


if __name__ == "__main__":
    unittest.main()
