from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verify_m5_release import (
    ReadinessError,
    _regenerate_neutral_fixture,
    _require_clean_source_tree,
    _tree_records,
    verify_release_readiness,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
SETUP_PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
PIP_AUDIT_ACTION_SHA = "1220774d901786e6f652ae159f7b6bc8fea6d266"
GITLEAKS_CHECKSUM_FILE_SHA256 = "061476c21adaf5441516f96f185c1a4706a83cd6329b9b38762271b3d4a52fae"
GITLEAKS_LINUX_X64_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS_IGNORED_FINGERPRINT = (
    "92b3134cb549bb625fcd71b202096c308e74ff09:tests/test_architecture.py:aws-access-token:405"
)


def _copy_committed_fixture(destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}
            or name.endswith(".pyc")
        }

    shutil.copytree(ROOT, destination, ignore=ignore)
    commands = (
        ("git", "init"),
        ("git", "config", "user.email", "readiness-test@example.invalid"),
        ("git", "config", "user.name", "Readiness Test"),
        ("git", "add", "."),
        ("git", "commit", "-m", "test: committed readiness source"),
    )
    for command in commands:
        subprocess.run(command, cwd=destination, check=True, capture_output=True)


def _write_live_toolchain_fixture(output: Path, python_version: str) -> None:
    output.mkdir()
    fixture = output / "m5-neutral"
    shutil.copytree(ROOT / "examples/m5-neutral", fixture)
    relative = "renderpack/processed/neutral_font/processing.receipt.json"
    receipt = fixture / relative
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["toolchain"]["python_version"] = python_version
    receipt.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload = receipt.read_bytes()
    lock_path = fixture / "fixture.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    record = next(item for item in lock["files"] if item["path"] == relative)
    record["sha256"] = hashlib.sha256(payload).hexdigest()
    record["size"] = len(payload)
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class M5ReleaseReadinessTests(unittest.TestCase):
    def test_root_workflow_uses_explicit_runners_and_only_full_action_shas(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ubuntu-24.04", workflow)
        self.assertIn("windows-2022", workflow)
        self.assertNotIn("ubuntu-latest", workflow)
        self.assertNotIn("windows-latest", workflow)
        self.assertIn('          - "3.11"', workflow)
        self.assertIn('          - "3.12"', workflow)
        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s]+)", workflow, flags=re.MULTILINE)
        self.assertGreaterEqual(len(uses), 7)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")
        self.assertIn(("actions/checkout", CHECKOUT_SHA), uses)
        self.assertIn(("actions/setup-python", SETUP_PYTHON_SHA), uses)
        self.assertIn(("pypa/gh-action-pip-audit", PIP_AUDIT_ACTION_SHA), uses)

    def test_security_jobs_verify_exact_inputs_and_scan_complete_history(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("requirements-m5.lock", workflow)
        self.assertIn("src/worldforge/templates/pyray_game/requirements.lock.tmpl", workflow)
        self.assertIn("no-deps: true", workflow)
        self.assertIn(GITLEAKS_CHECKSUM_FILE_SHA256, workflow)
        self.assertIn(GITLEAKS_LINUX_X64_SHA256, workflow)
        self.assertIn("version=8.30.1", workflow)
        self.assertIn('checksums="gitleaks_${version}_checksums.txt"', workflow)
        self.assertIn("sha256sum --check --strict", workflow)
        self.assertIn('"${RUNNER_TEMP}/gitleaks" git', workflow)
        self.assertIn("--log-opts=--all", workflow)
        self.assertNotIn("continue-on-error", workflow)
        ignored = [
            line.strip()
            for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual([GITLEAKS_IGNORED_FINGERPRINT], ignored)

    def test_driver_refuses_to_write_inside_repository(self) -> None:
        blocked = ROOT / "must-not-create-readiness-output"
        self.assertFalse(blocked.exists())
        with self.assertRaisesRegex(ReadinessError, "outside the repository"):
            verify_release_readiness(blocked, neutral_only=True)
        self.assertFalse(blocked.exists())

    def test_regeneration_accepts_live_toolchain_evidence_but_requires_dual_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-live-toolchain-") as temporary:
            root = Path(temporary)
            with patch(
                "scripts.verify_m5_release.generate_neutral_fixture",
                side_effect=lambda output, *, allow_repo: _write_live_toolchain_fixture(
                    output, "3.11.99-live-toolchain"
                ),
            ):
                generated = _regenerate_neutral_fixture(root)
            committed = ROOT / "examples/m5-neutral"
            self.assertNotEqual(_tree_records(generated), _tree_records(committed))
            self.assertEqual(
                _tree_records(root / "neutral-regenerated-a/m5-neutral"),
                _tree_records(root / "neutral-regenerated-b/m5-neutral"),
            )

            nondeterministic = root / "nondeterministic"
            nondeterministic.mkdir()
            markers = iter(("3.11.99-first", "3.11.99-second"))
            with (
                patch(
                    "scripts.verify_m5_release.generate_neutral_fixture",
                    side_effect=lambda output, *, allow_repo: _write_live_toolchain_fixture(
                        output, next(markers)
                    ),
                ),
                self.assertRaisesRegex(ReadinessError, "same-toolchain"),
            ):
                _regenerate_neutral_fixture(nondeterministic)

    def test_full_readiness_requires_clean_tracked_and_untracked_source_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-source-identity-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            tracked = repo / "tracked.txt"
            tracked.write_text("clean\n", encoding="utf-8")
            commands = (
                ("git", "init"),
                ("git", "config", "user.email", "identity-test@example.invalid"),
                ("git", "config", "user.name", "Identity Test"),
                ("git", "add", "tracked.txt"),
                ("git", "commit", "-m", "test: clean identity"),
            )
            for command in commands:
                subprocess.run(command, cwd=repo, check=True, capture_output=True)
            _require_clean_source_tree(repo)

            tracked.write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ReadinessError, "matching HEAD"):
                _require_clean_source_tree(repo)
            tracked.write_text("clean\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            with self.assertRaisesRegex(ReadinessError, "untracked.txt"):
                _require_clean_source_tree(repo)

            output = root / "must-not-create"
            with (
                patch(
                    "scripts.verify_m5_release._require_clean_source_tree",
                    side_effect=ReadinessError("dirty source identity"),
                ),
                self.assertRaisesRegex(ReadinessError, "dirty source identity"),
            ):
                verify_release_readiness(output)
            self.assertFalse(output.exists())

    def test_driver_defines_cross_platform_clean_artifact_gates(self) -> None:
        driver = (ROOT / "scripts/verify_m5_release.py").read_text(encoding="utf-8")
        for required in (
            'Path("Scripts/python.exe")',
            'Path("bin/python")',
            '"--no-build-isolation", "--no-deps"',
            '"-m", "pip", "check"',
            '"-I", "-m", "worldforge", "audit-contracts"',
            'ROOT / "scripts/build_release.py"',
            "with export_runtime_bundle(",
            "with verify_runtime_bundle(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, driver)

    def test_neutral_readiness_runs_from_an_isolated_committed_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-readiness-test-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            _copy_committed_fixture(repo)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(repo / "src")
            result = subprocess.run(
                [sys.executable, "-m", "scripts.verify_m5_release", "--neutral-only"],
                cwd=repo,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("neutral-e2e=pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
