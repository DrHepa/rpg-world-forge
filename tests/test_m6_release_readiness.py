from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SETUP_NODE_SHA = "49933ea5288caeca8642d1e84afbd3f7d6820020"
WINDOWS_NATIVE_PYTHON_TESTS = (
    "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
    "test_native_windows_handles_block_target_swap_through_final_read",
    "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
    "test_native_windows_backend_assembles_and_zips_windows_target",
    "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
    "test_native_windows_retained_handles_block_after_write_parent_swaps",
)
WINDOWS_NATIVE_SHELL_TEST = "retains the native Windows package root against parent replacement"


def _studio_job(workflow: str) -> str:
    return workflow.split("  studio-m6-readiness:\n", 1)[1].split("  graphical-raylib-smoke:\n", 1)[
        0
    ]


def _require_vitest_test_passed(report_path: Path, test_title: str) -> None:
    if report_path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("vitest_report_too_large")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assertions = [
        assertion
        for result in report.get("testResults", ())
        for assertion in result.get("assertionResults", ())
        if assertion.get("title") == test_title
    ]
    if (
        len(assertions) != 1
        or assertions[0].get("status") != "passed"
        or report.get("numPassedTests") != 1
        or report.get("numFailedTests") != 0
    ):
        raise RuntimeError("required_vitest_test_did_not_pass")


def _run_windows_native_python_tests() -> int:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    suite = unittest.defaultTestLoader.loadTestsFromNames(WINDOWS_NATIVE_PYTHON_TESTS)
    if suite.countTestCases() != len(WINDOWS_NATIVE_PYTHON_TESTS):
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


class M6ReleaseReadinessContractTests(unittest.TestCase):
    def test_studio_matrix_pins_exact_runners_languages_and_actions(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        studio = _studio_job(workflow)
        self.assertIn("runs-on: ${{ matrix.os }}", studio)
        self.assertIn("          - ubuntu-24.04\n          - windows-2022", studio)
        self.assertIn('          - "3.11"\n          - "3.12"', studio)
        self.assertIn(f"uses: actions/setup-node@{SETUP_NODE_SHA}", studio)
        self.assertIn('node-version: "24.14.1"', studio)
        self.assertIn("process.version!=='v24.14.1'", studio)
        self.assertIn("!=='11.13.0'", studio)
        self.assertIn("cache-dependency-path: apps/studio/package-lock.json", studio)

        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s]+)", workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 10)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")

    def test_all_rows_bind_python_and_run_complete_studio_and_runtime_gates(self) -> None:
        studio = _studio_job(WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(studio.count("run: npm ci"), 1)
        self.assertEqual(studio.count("run: npm run verify"), 1)
        self.assertIn("PYTHON=%s\\nRWF_STUDIO_BUILD_PYTHON=%s\\n", studio)
        self.assertIn('"PYTHON=$pythonPath"', studio)
        self.assertIn('"RWF_STUDIO_BUILD_PYTHON=$pythonPath"', studio)
        self.assertIn(
            "test_synthetic_linux_and_windows_resources_are_complete_and_non_publishable",
            studio,
        )
        self.assertIn(
            "test_real_cli_fails_before_cache_or_output_mutation_with_all_blockers",
            studio,
        )
        self.assertNotIn("continue-on-error", studio)

    def test_windows_rows_run_exact_native_handle_tests_and_reject_skips(self) -> None:
        studio = _studio_job(WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(
            WINDOWS_NATIVE_PYTHON_TESTS,
            (
                "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
                "test_native_windows_handles_block_target_swap_through_final_read",
                "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
                "test_native_windows_backend_assembles_and_zips_windows_target",
                "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
                "test_native_windows_retained_handles_block_after_write_parent_swaps",
            ),
        )
        self.assertIn("--run-windows-native-python", studio)
        self.assertIn(f'--testNamePattern "{WINDOWS_NATIVE_SHELL_TEST}"', studio)
        self.assertIn("--assert-vitest-passed $report", studio)
        self.assertIn("if: runner.os == 'Windows'", studio)

    def test_python_quality_gates_cover_the_shell_handle_backend(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        path = "apps/studio/scripts/shell_package_snapshot.py"
        self.assertIn(f"ruff check src tests scripts {path}", workflow)
        self.assertIn(f"ruff format --check src tests scripts {path}", workflow)
        self.assertIn(f"src scripts tests {path}", workflow)

    def test_python_312_builds_host_shell_only_under_runner_temp_and_reverifies(self) -> None:
        studio = _studio_job(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn('CSC_IDENTITY_AUTO_DISCOVERY: "false"', studio)
        self.assertIn("if: matrix.python-version == '3.12' && runner.os == 'Linux'", studio)
        self.assertIn("if: matrix.python-version == '3.12' && runner.os == 'Windows'", studio)
        self.assertIn('output="${RUNNER_TEMP}/rwf-studio-shell-linux-x64"', studio)
        self.assertIn(
            '$output = Join-Path $env:RUNNER_TEMP "rwf-studio-shell-win32-x64"',
            studio,
        )
        self.assertIn(
            'npm run package:dir -- --output "${output}" --target linux-x64',
            studio,
        )
        self.assertIn("npm run package:dir -- --output $output --target win32-x64", studio)
        self.assertIn(
            '--path "${output}/linux-unpacked" --target linux-x64',
            studio,
        )
        self.assertIn("npm run package:verify -- --path $unpacked --target win32-x64", studio)

    def test_studio_job_does_not_acquire_publish_sign_or_build_installers(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        prohibited = (
            "actions/upload-artifact",
            "studio_runtime_inputs.py fetch",
            "studio_runtime_assembly.py assemble",
            "runtime-inputs fetch",
            "assemble_from_committed_sources",
            "npm publish",
            "gh release",
            "--publish",
            "signtool",
            "codesign",
            "notarize",
            "CSC_LINK",
            "AppImage",
            "nsis",
        )
        for command in prohibited:
            with self.subTest(command=command):
                self.assertNotIn(command, workflow)
        self.assertNotRegex(
            workflow,
            (
                r"(?m)^\s{2}(?:runtime-acquisition|self-contained-runtime|installer|"
                r"release-publication):"
            ),
        )

    def test_vitest_pass_requirement_rejects_a_skip_or_ambiguous_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-m6-ci-contract-") as temporary:
            report_path = Path(temporary) / "vitest.json"
            report = {
                "numFailedTests": 0,
                "numPassedTests": 0,
                "testResults": [
                    {
                        "assertionResults": [
                            {
                                "status": "skipped",
                                "title": WINDOWS_NATIVE_SHELL_TEST,
                            }
                        ]
                    }
                ],
            }
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "required_vitest_test_did_not_pass"):
                _require_vitest_test_passed(report_path, WINDOWS_NATIVE_SHELL_TEST)

            report["numPassedTests"] = 1
            report["testResults"][0]["assertionResults"][0]["status"] = "passed"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            _require_vitest_test_passed(report_path, WINDOWS_NATIVE_SHELL_TEST)


def _main(argv: list[str]) -> int:
    if argv == ["--run-windows-native-python"]:
        return _run_windows_native_python_tests()
    if len(argv) == 3 and argv[0] == "--assert-vitest-passed":
        _require_vitest_test_passed(Path(argv[1]), argv[2])
        return 0
    unittest.main(argv=[sys.argv[0], *argv])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
