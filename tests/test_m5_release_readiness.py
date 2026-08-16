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
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
SETUP_PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
PIP_AUDIT_ACTION_SHA = "1220774d901786e6f652ae159f7b6bc8fea6d266"
SETUP_NODE_SHA = "49933ea5288caeca8642d1e84afbd3f7d6820020"
GITLEAKS_CHECKSUM_FILE_SHA256 = "061476c21adaf5441516f96f185c1a4706a83cd6329b9b38762271b3d4a52fae"
GITLEAKS_LINUX_X64_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS_IGNORED_FINGERPRINTS = (
    # Intentional AWS-shaped historical fixture used to prove architecture redaction.
    "92b3134cb549bb625fcd71b202096c308e74ff09:tests/test_architecture.py:aws-access-token:405",
    # Intentional credential-shaped HEAD fixtures used to prove runtime-notice rejection.
    "047b04b53e1c30c1c9590e88b7b4d25e45989aab:"
    "apps/studio/tests/main/generic-asset-contracts.test.ts:aws-access-token:1059",
    "047b04b53e1c30c1c9590e88b7b4d25e45989aab:"
    "apps/studio/tests/main/generic-asset-contracts.test.ts:jwt:1053",
    "047b04b53e1c30c1c9590e88b7b4d25e45989aab:"
    "apps/studio/tests/main/generic-asset-contracts.test.ts:jwt:1057",
    "047b04b53e1c30c1c9590e88b7b4d25e45989aab:"
    "apps/studio/tests/main/generic-asset-contracts.test.ts:jwt:1061",
    "047b04b53e1c30c1c9590e88b7b4d25e45989aab:"
    "tests/test_multigenre_asset_production.py:aws-access-token:2571",
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
    receipt.write_bytes(canonical_json_bytes(document))
    payload = receipt.read_bytes()
    lock_path = fixture / "fixture.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    record = next(item for item in lock["files"] if item["path"] == relative)
    record["sha256"] = hashlib.sha256(payload).hexdigest()
    record["size"] = len(payload)
    lock_path.write_bytes(canonical_json_bytes(lock))


class M5ReleaseReadinessTests(unittest.TestCase):
    def test_root_workflow_declares_five_static_visible_jobs_without_matrices(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("ubuntu-latest", workflow)
        self.assertNotIn("windows-latest", workflow)

        jobs_section = workflow.split("\njobs:\n", 1)[1]
        job_ids = re.findall(r"^  ([A-Za-z0-9_-]+):$", jobs_section, flags=re.MULTILINE)
        self.assertEqual(
            [
                "ubuntu-py312-core",
                "ubuntu-py311-compat-native",
                "windows-py312-release",
                "windows-py311-compat-native",
                "ci-required",
            ],
            job_ids,
        )
        self.assertNotIn("matrix:", workflow)
        self.assertNotIn("headless-suite-shards", workflow)
        self.assertNotIn("headless-suite-aggregate", workflow)
        self.assertNotIn("verify_headless_suite", workflow)
        self.assertNotIn("headless-suite-", workflow)

        for path in (
            ROOT / ".github/headless-suite-shards-v1.json",
            ROOT / "scripts/verify_headless_suite.py",
            ROOT / "tests/test_ci_headless_sharding.py",
        ):
            with self.subTest(path=path.name):
                self.assertFalse(path.exists())

        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s]+)", workflow, flags=re.MULTILINE)
        self.assertGreaterEqual(len(uses), 7)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")
        self.assertIn(("actions/checkout", CHECKOUT_SHA), uses)
        self.assertIn(("actions/setup-python", SETUP_PYTHON_SHA), uses)
        self.assertIn(("pypa/gh-action-pip-audit", PIP_AUDIT_ACTION_SHA), uses)

    def test_static_jobs_partition_full_suite_studio_native_and_security_once(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ubuntu_core = workflow.split("  ubuntu-py312-core:\n", 1)[1].split(
            "  ubuntu-py311-compat-native:\n", 1
        )[0]
        ubuntu_compat = workflow.split("  ubuntu-py311-compat-native:\n", 1)[1].split(
            "  windows-py312-release:\n", 1
        )[0]
        windows_release = workflow.split("  windows-py312-release:\n", 1)[1].split(
            "  windows-py311-compat-native:\n", 1
        )[0]
        windows_compat = workflow.split("  windows-py311-compat-native:\n", 1)[1].split(
            "  ci-required:\n", 1
        )[0]
        final_gate = workflow.split("  ci-required:\n", 1)[1]

        self.assertIn("name: Ubuntu Python 3.12 core release gates", ubuntu_core)
        self.assertIn("runs-on: ubuntu-24.04", ubuntu_core)
        self.assertIn('python-version: "3.12"', ubuntu_core)
        self.assertIn("Run bounded full unittest suite once", ubuntu_core)
        self.assertEqual(1, workflow.count("python - <<'PY'\n          import concurrent.futures"))
        self.assertIn("Validate foundation release profile", ubuntu_core)
        self.assertIn("Verify neutral standalone and reproducible releases", ubuntu_core)
        self.assertIn("xvfb-run -a python tests/raylib_smoke.py", ubuntu_core)
        self.assertIn("xvfb-run -a python tests/pyray_3d_native_smoke.py", ubuntu_core)

        self.assertIn("name: Ubuntu Python 3.11 compatibility native gates", ubuntu_compat)
        self.assertIn("runs-on: ubuntu-24.04", ubuntu_compat)
        self.assertIn('python-version: "3.11"', ubuntu_compat)
        self.assertNotIn("Run bounded full unittest suite once", ubuntu_compat)
        self.assertNotIn("npm", ubuntu_compat.casefold())
        self.assertNotIn("studio", ubuntu_compat.casefold())
        self.assertNotIn("apps/studio", ubuntu_compat)

        self.assertIn("name: Windows Python 3.12 release gates", windows_release)
        self.assertIn("runs-on: windows-2022", windows_release)
        self.assertIn('python-version: "3.12"', windows_release)
        self.assertIn("Run native Windows world-project migration gate", windows_release)
        self.assertIn("python tests/raylib_cpu_media_smoke.py", windows_release)
        self.assertIn("python tests/pyray_3d_abi_smoke.py", windows_release)
        self.assertIn("Run complete Studio verification", windows_release)
        self.assertIn("Build and reverify unpacked Windows shell", windows_release)

        self.assertIn("name: Windows Python 3.11 compatibility native gates", windows_compat)
        self.assertIn("runs-on: windows-2022", windows_compat)
        self.assertIn('python-version: "3.11"', windows_compat)
        self.assertNotIn("Run complete Studio verification", windows_compat)

        self.assertEqual(2, workflow.count("Run complete Studio verification"))
        self.assertEqual(2, workflow.count("npm run verify"))
        self.assertIn("Set up Node", ubuntu_core)
        self.assertIn("Set up Node", windows_release)
        self.assertNotIn("Run focused Studio verification on Ubuntu", workflow)
        self.assertNotIn("Run focused Studio verification on Windows", workflow)
        self.assertEqual(1, workflow.count("pypa/gh-action-pip-audit@"))
        self.assertEqual(1, workflow.count('"${RUNNER_TEMP}/gitleaks" git'))
        self.assertIn("name: CI required", final_gate)
        self.assertIn("if: always()", final_gate)
        self.assertIn(
            "needs:\n"
            "      - ubuntu-py312-core\n"
            "      - ubuntu-py311-compat-native\n"
            "      - windows-py312-release\n"
            "      - windows-py311-compat-native\n",
            final_gate,
        )

    def test_windows_native_work_roots_are_unique_siblings_not_runner_temp_or_repo(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        windows_release = workflow.split("  windows-py312-release:\n", 1)[1].split(
            "  windows-py311-compat-native:\n", 1
        )[0]
        windows_compat = workflow.split("  windows-py311-compat-native:\n", 1)[1].split(
            "  ci-required:\n", 1
        )[0]

        for job_id, block in (
            ("windows-py312-release", windows_release),
            ("windows-py311-compat-native", windows_compat),
        ):
            with self.subTest(job_id=job_id):
                self.assertIn("Initialize strict external Windows native work root", block)
                self.assertIn("Cleanup strict external Windows native work root", block)
                self.assertIn("WORLD_FORGE_NATIVE_WORK_ROOT", block)
                self.assertIn("$env:GITHUB_WORKSPACE", block)
                self.assertIn("$env:GITHUB_JOB", block)
                self.assertIn("$env:GITHUB_RUN_ID", block)
                self.assertIn("$env:GITHUB_RUN_ATTEMPT", block)
                self.assertNotIn('work="${RUNNER_TEMP}/world-forge-multigenre-work"', block)
                self.assertNotIn(
                    '$output = Join-Path $env:RUNNER_TEMP "rwf-studio-shell-win32-x64"',
                    block,
                )
                init_step = block.split("Initialize strict external Windows native work root", 1)[
                    1
                ].split("Cleanup strict external Windows native work root", 1)[0]
                self.assertIn("Resolve-Path -LiteralPath $env:GITHUB_WORKSPACE", init_step)
                self.assertIn("GetDirectoryName($repo)", init_step)
                self.assertIn("GetFullPath", init_step)
                self.assertIn("StartsWith($repo", init_step)
                self.assertIn("StartsWith($runnerTemp", init_step)
                self.assertIn("New-Item -ItemType Directory", init_step)
                self.assertIn(">> $env:GITHUB_ENV", init_step)

    def test_four_native_axes_publish_exact_rows_and_final_gate_aggregates_them(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        expected_rows = {
            "ubuntu-py312-core": ("Linux", "3.12", "ubuntu-24.04"),
            "ubuntu-py311-compat-native": ("Linux", "3.11", "ubuntu-24.04"),
            "windows-py312-release": ("Windows", "3.12", "windows-2022"),
            "windows-py311-compat-native": ("Windows", "3.11", "windows-2022"),
        }

        def job_block(job_id: str) -> str:
            remainder = workflow.split(f"  {job_id}:\n", 1)[1]
            lines = remainder.splitlines(keepends=True)
            end = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if line.startswith("  ") and not line.startswith("    ")
                ),
                len(lines),
            )
            return "".join(lines[:end])

        for job_id, (runner_os, python_minor, runner_image) in expected_rows.items():
            with self.subTest(job_id=job_id):
                block = job_block(job_id)
                self.assertIn(f'python-version: "{python_minor}"', block)
                self.assertIn(f"WORLD_FORGE_RUNNER_IMAGE: {runner_image}", block)
                self.assertIn("Download, attest, and install the locked raylib wheel", block)
                self.assertIn("Verify exact generic release lineage with native raylib", block)
                self.assertIn("Upload exact native evidence row", block)
                self.assertIn(
                    f"name: multigenre-release-{runner_os}-py{python_minor}",
                    block,
                )

        for pin in ("cffi==1.17.1", "pycparser==2.23", "raylib==6.0.1.0"):
            with self.subTest(pin=pin):
                self.assertIn(pin, workflow)
        final_gate = workflow.split("  ci-required:\n", 1)[1]
        self.assertIn("Download Linux Python 3.11 native evidence", final_gate)
        self.assertIn("Download Linux Python 3.12 native evidence", final_gate)
        self.assertIn("Download Windows Python 3.11 native evidence", final_gate)
        self.assertIn("Download Windows Python 3.12 native evidence", final_gate)
        self.assertIn("Aggregate exact four native rows", final_gate)
        self.assertIn(
            'aggregate["matrix"] == [{"os": "linux", "python_minor": "3.11"}, '
            '{"os": "linux", "python_minor": "3.12"}, '
            '{"os": "windows", "python_minor": "3.11"}, '
            '{"os": "windows", "python_minor": "3.12"}]',
            final_gate,
        )

    def test_final_gate_runs_security_once_and_fails_truthfully(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        final_gate = workflow.split("  ci-required:\n", 1)[1]
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
        self.assertIn("Install locked final-gate Python dependencies", final_gate)
        self.assertIn("python -m pip install --requirement requirements-m5.lock", final_gate)
        self.assertIn("python -m pip install --no-build-isolation --no-deps -e .", final_gate)
        self.assertIn("python -m pip check", final_gate)
        for needed in (
            "${{ needs.ubuntu-py312-core.result }}",
            "${{ needs.ubuntu-py311-compat-native.result }}",
            "${{ needs.windows-py312-release.result }}",
            "${{ needs.windows-py311-compat-native.result }}",
        ):
            self.assertIn(needed, final_gate)
        ignored = [
            line.strip()
            for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(list(GITLEAKS_IGNORED_FINGERPRINTS), ignored)

    def test_full_suite_discovery_uses_real_test_modules_without_unittest_suite_introspection(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        core = workflow.split("  ubuntu-py312-core:\n", 1)[1].split(
            "  ubuntu-py311-compat-native:\n", 1
        )[0]
        script = core.split("      - name: Run bounded full unittest suite once\n", 1)[1].split(
            "      - name: Upload exact native evidence row\n", 1
        )[0]
        self.assertIn('Path("tests").glob("test_*.py")', script)
        self.assertIn('f"tests.{path.stem}"', script)
        self.assertIn("ThreadPoolExecutor(max_workers=4)", script)
        self.assertIn("failing modules:", script)
        self.assertNotIn("unittest.suite", script)
        self.assertNotIn("for group in suite for case in group", script)

        modules = [
            f"tests.{path.stem}"
            for path in sorted((ROOT / "tests").glob("test_*.py"))
            if path.name != "__init__.py"
        ]
        self.assertGreater(len(modules), 100)
        self.assertEqual(len(modules), len(set(modules)))
        self.assertIn("tests.test_m5_release_readiness", modules)
        self.assertIn("tests.test_multigenre_release_gate", modules)

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
