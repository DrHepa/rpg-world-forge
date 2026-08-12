from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_creation_reconciliation_edge import _workflow_with_dependency

ROOT = Path(__file__).resolve().parents[1]
SYSTEMIC_ROOT = ROOT / "examples/multigenre-contracts/systemic-simulation"


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONWARNINGS": "error::ResourceWarning",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "worldforge", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


class CreationReconciliationCliTests(unittest.TestCase):
    def test_argument_errors_are_json_stderr_with_exit_two(self) -> None:
        cases = {
            "missing_project_root": (),
            "missing_expected_hash": ("unused",),
            "unknown_flag": (
                "unused",
                "--expected-status-hash",
                "a" * 64,
                "--unknown",
            ),
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                result = _run_cli("reconcile-creation", *arguments)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                error = json.loads(result.stderr)
                self.assertEqual("error", error["status"])
                self.assertEqual(
                    "creation_workflow_cli_arguments_invalid",
                    error["reason_code"],
                )
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("ResourceWarning", result.stderr)

    def test_help_remains_normal_argparse_output(self) -> None:
        result = _run_cli("reconcile-creation", "--help")
        self.assertEqual(0, result.returncode)
        self.assertIn("usage: worldforge reconcile-creation", result.stdout)
        self.assertIn("--expected-status-hash", result.stdout)
        self.assertEqual("", result.stderr)

    def test_legacy_command_parser_errors_keep_the_existing_argparse_surface(self) -> None:
        result = _run_cli("phase-status")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("usage:", result.stderr)
        self.assertIn("the following arguments are required: project_root", result.stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(result.stderr)

    def test_real_cli_noop_is_deterministic_under_resource_warning_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            shutil.copytree(SYSTEMIC_ROOT, root)
            status = json.loads((root / ".worldforge/status.json").read_text(encoding="utf-8"))
            result = _run_cli(
                "reconcile-creation",
                str(root),
                "--expected-status-hash",
                status["content_hash"],
            )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["changed"])
        self.assertEqual(status, payload["workflow_status"])

    def test_malformed_report_dependency_is_json_stderr_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, expected_hash, _ = _workflow_with_dependency(
                Path(temp),
                lambda identity: identity.__setitem__("format_version", None),
            )
            result = _run_cli(
                "reconcile-creation",
                str(root),
                "--expected-status-hash",
                expected_hash,
            )
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        error = json.loads(result.stderr)
        self.assertEqual("error", error["status"])
        self.assertEqual(
            "creation_workflow_dependency_identity_invalid",
            error["reason_code"],
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("ResourceWarning", result.stderr)


if __name__ == "__main__":
    unittest.main()
