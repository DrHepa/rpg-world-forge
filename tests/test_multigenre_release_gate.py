from __future__ import annotations

import ast
import copy
import ctypes
import hashlib
import inspect
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from worldforge.integrity import canonical_json_bytes
from worldforge.standalone_templates import STANDALONE_TEMPLATE_FILES

ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    from scripts import verify_multigenre_release as gate

    return gate


def _workflow_job_block(workflow: str, job_id: str) -> str:
    marker = f"  {job_id}:\n"
    if workflow.count(marker) != 1:
        raise AssertionError(f"workflow must contain exactly one {job_id} job")
    block = workflow.split(marker, 1)[1]
    lines = block.splitlines(keepends=True)
    end = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("  ") and not line.startswith("    ")
        ),
        len(lines),
    )
    return "".join(lines[:end])


def _workflow_job_contract(workflow: str, job_id: str) -> dict[str, object]:
    """Parse the complete closed YAML subset used by one release job."""

    def add(target: dict[str, object], key: str, value: object) -> None:
        if key in target:
            raise AssertionError(f"duplicate workflow field: {key}")
        target[key] = value

    def scalar(line: str, *, indent: int, allowed: set[str]) -> tuple[str, str]:
        if not line.startswith(" " * indent) or line.startswith(" " * (indent + 2)):
            raise AssertionError(f"invalid workflow indentation: {line!r}")
        key, separator, value = line[indent:].partition(": ")
        if separator != ": " or key not in allowed or not value:
            raise AssertionError(f"unsupported workflow field: {line!r}")
        return key, value

    def block_scalar(lines: list[str], index: int, *, indent: int, key: str) -> tuple[str, int]:
        marker = " " * indent + f"{key}: >-"
        if lines[index] != marker:
            raise AssertionError(f"unsupported workflow block scalar: {lines[index]!r}")
        index += 1
        payload = []
        payload_indent = " " * (indent + 2)
        while index < len(lines) and lines[index].startswith(payload_indent):
            payload.append(lines[index][indent + 2 :])
            index += 1
        if not payload:
            raise AssertionError(f"empty workflow block scalar: {key}")
        return " ".join(line.strip() for line in payload), index

    def literal_scalar(lines: list[str], index: int, *, indent: int, key: str) -> tuple[str, int]:
        marker = " " * indent + f"{key}: |"
        if lines[index] != marker:
            raise AssertionError(f"unsupported workflow literal scalar: {lines[index]!r}")
        index += 1
        payload = []
        payload_indent = " " * (indent + 2)
        while index < len(lines) and lines[index].startswith(payload_indent):
            payload.append(lines[index][indent + 2 :])
            index += 1
        if not payload:
            raise AssertionError(f"empty workflow literal scalar: {key}")
        return "\n".join(payload), index

    marker = f"  {job_id}:\n"
    if workflow.count(marker) != 1:
        raise AssertionError(f"workflow must contain exactly one {job_id} job")
    block = workflow.split(marker, 1)[1]
    lines = block.splitlines()
    end = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("  ") and not line.startswith("    ")
        ),
        len(lines),
    )
    lines = lines[:end]
    job: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        if line in {"    env:", "    needs:", "    permissions:", "    strategy:", "    steps:"}:
            section = line[4:-1]
            index += 1
            if section == "needs":
                values = []
                while index < len(lines) and lines[index].startswith("      - "):
                    values.append(lines[index].removeprefix("      - "))
                    index += 1
                if not values:
                    raise AssertionError("release job needs list must not be empty")
                add(job, section, values)
                continue
            if section == "env":
                values: dict[str, object] = {}
                while index < len(lines) and lines[index].startswith("      "):
                    key, value = scalar(
                        lines[index], indent=6, allowed={"WORLD_FORGE_RUNNER_IMAGE"}
                    )
                    add(values, key, value)
                    index += 1
                add(job, section, values)
                continue
            if section == "permissions":
                values: dict[str, object] = {}
                while index < len(lines) and lines[index].startswith("      "):
                    key, value = scalar(
                        lines[index],
                        indent=6,
                        allowed={"attestations", "contents", "id-token"},
                    )
                    add(values, key, value)
                    index += 1
                add(job, section, values)
                continue
            if section == "strategy":
                strategy: dict[str, object] = {}
                key, value = scalar(lines[index], indent=6, allowed={"fail-fast"})
                add(strategy, key, value)
                index += 1
                if lines[index] != "      matrix:":
                    raise AssertionError("release strategy must contain one matrix")
                index += 1
                matrix: dict[str, object] = {}
                if lines[index] != "        os:":
                    raise AssertionError("release matrix must declare os first")
                index += 1
                operating_systems = []
                while index < len(lines) and lines[index].startswith("          - "):
                    operating_systems.append(lines[index].removeprefix("          - "))
                    index += 1
                add(matrix, "os", operating_systems)
                key, value = scalar(lines[index], indent=8, allowed={"python-version"})
                if value.startswith("["):
                    value = ast.literal_eval(value)
                add(matrix, key, value)
                index += 1
                add(strategy, "matrix", matrix)
                add(job, section, strategy)
                continue
            steps: list[dict[str, object]] = []
            while index < len(lines):
                if not lines[index].startswith("      - name: "):
                    raise AssertionError(f"every release step needs a name: {lines[index]!r}")
                step: dict[str, object] = {"name": lines[index].removeprefix("      - name: ")}
                index += 1
                while index < len(lines) and not lines[index].startswith("      - "):
                    current = lines[index]
                    if not current:
                        index += 1
                        continue
                    if current == "        run: |":
                        if "run" in step:
                            raise AssertionError("duplicate run field")
                        step["run"], index = literal_scalar(lines, index, indent=8, key="run")
                        continue
                    if current == "        env:":
                        values = {}
                        index += 1
                        while index < len(lines) and lines[index].startswith("          "):
                            key, value = scalar(
                                lines[index],
                                indent=10,
                                allowed={
                                    "ATTESTATION_BUNDLE_PATH",
                                    "ATTESTATION_ID",
                                    "ATTESTATION_URL",
                                },
                            )
                            add(values, key, value)
                            index += 1
                        add(step, "env", values)
                        continue
                    if current == "        with:":
                        values = {}
                        index += 1
                        while index < len(lines) and lines[index].startswith("          "):
                            if lines[index] == "          path: |":
                                value, index = literal_scalar(lines, index, indent=10, key="path")
                                add(values, "path", value)
                                continue
                            key, value = scalar(
                                lines[index],
                                indent=10,
                                allowed={
                                    "cache",
                                    "cache-dependency-path",
                                    "if-no-files-found",
                                    "merge-multiple",
                                    "name",
                                    "path",
                                    "pattern",
                                    "persist-credentials",
                                    "python-version",
                                    "retention-days",
                                    "subject-path",
                                },
                            )
                            add(values, key, value)
                            index += 1
                        add(step, "with", values)
                        continue
                    key, value = scalar(
                        current,
                        indent=8,
                        allowed={"id", "if", "run", "shell", "uses"},
                    )
                    add(step, key, value)
                    index += 1
                if ("run" in step) == ("uses" in step):
                    raise AssertionError("each release step needs exactly one run or uses field")
                steps.append(step)
            add(job, section, steps)
            continue
        if line == "    if: >-":
            value, index = block_scalar(lines, index, indent=4, key="if")
            add(job, "if", value)
            continue
        key, value = scalar(line, indent=4, allowed={"name", "needs", "runs-on", "if"})
        add(job, key, value)
        index += 1
    return job


def _workflow_job_steps(workflow: str, job_id: str) -> list[dict[str, object]]:
    steps = _workflow_job_contract(workflow, job_id).get("steps")
    if not isinstance(steps, list):
        raise AssertionError(f"workflow job {job_id} has no steps")
    return steps


def _action_ref(value: object) -> str:
    if not isinstance(value, str):
        raise AssertionError("workflow action ref must be a string")
    ref = value.split(" # ", 1)[0]
    if not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref):
        raise AssertionError(f"workflow action must use a pinned 40-hex ref: {value!r}")
    return ref


def _step_by_name(job: dict[str, object], name: str) -> dict[str, object]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise AssertionError("workflow job must define steps")
    matches = [step for step in steps if isinstance(step, dict) and step.get("name") == name]
    if len(matches) != 1:
        raise AssertionError(f"workflow job must contain exactly one step named {name!r}")
    return matches[0]


def _literal_lines(value: object) -> list[str]:
    if not isinstance(value, str):
        raise AssertionError("workflow literal field must be a string")
    return value.splitlines()


def _assert_closed_run_tokens(run: object, *, required: set[str], forbidden: set[str]) -> None:
    if not isinstance(run, str):
        raise AssertionError("workflow run block must be a string")
    for token in required:
        if token not in run:
            raise AssertionError(f"workflow run block missing required token: {token}")
    for token in forbidden:
        if token in run:
            raise AssertionError(f"workflow run block contains forbidden token: {token}")


def _assert_all_uses_are_pinned(workflow: str) -> None:
    uses = re.findall(r"^\s*uses:\s*(.+)$", workflow, flags=re.MULTILINE)
    if len(uses) < 10:
        raise AssertionError("workflow must contain at least ten action uses")
    for uses_value in uses:
        _action_ref(uses_value)


def _native_smoke_report() -> dict[str, object]:
    return {
        "adapter_id": "adapter_test",
        "adapter_version": "1.2.3",
        "frames": 2,
        "platform_id": "platform:test_x86_64",
        "status": "native_smoke_executed",
    }


def _native_evidence_payloads() -> dict[str, dict[str, bytes]]:
    return {
        "abstract-puzzle": {
            "attempt.json": b'{"attempt":1}\n',
            "stderr.log": b"diagnostic\n",
            "stdout.log": b"banner\n",
        },
        "branching-narrative": {
            "attempt.json": b'{"attempt":2}\n',
            "report.json": canonical_json_bytes(_native_smoke_report()),
            "stderr.log": b"",
            "stdout.log": b"",
        },
    }


def _write_native_smoke_child(extracted_root: Path, body: str) -> Path:
    script = extracted_root / "scripts/native_smoke.py"
    script.parent.mkdir(parents=True)
    extracted_root.joinpath("immutable.txt").write_bytes(b"immutable extracted tree\n")
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "import time\n\n"
        "report = Path(sys.argv[sys.argv.index('--report') + 1])\n" + body,
        encoding="utf-8",
    )
    return script


def _fixture_hashes() -> dict[str, dict[str, str]]:
    return {
        "abstract-puzzle": {
            "gamepack": "1" * 64,
            "package": "2" * 64,
            "package_archive": "3" * 64,
        },
        "branching-narrative": {
            "gamepack": "4" * 64,
            "package": "5" * 64,
            "package_archive": "6" * 64,
        },
    }


def _matrix_report(os_name: str, python_minor: str) -> dict[str, object]:
    gate = _load_gate()
    host = {
        "architecture": "x86_64",
        "os": os_name,
        "platform_id": f"platform:{os_name}_x86_64",
        "python_abi": f"cp{python_minor.replace('.', '')}",
        "python_implementation": "cpython",
        "python_minor": python_minor,
        "runner_image": "ubuntu-24.04" if os_name == "linux" else "windows-2022",
    }
    expected_lock = gate._expected_platform_lock(host)
    if expected_lock is None:
        raise AssertionError("matrix host must have one built-in platform lock")
    cases = []
    for case_id, hashes in _fixture_hashes().items():
        adapter_id, adapter_version = gate.CASE_ADAPTERS[case_id]
        cases.append(
            {
                "case_id": case_id,
                "status": "passed",
                "stages": [
                    {"reason_code": None, "stage": stage, "state": "passed"}
                    for stage in gate.REQUIRED_CASE_STAGES
                ],
                "hashes": {
                    "analysis": "7" * 64,
                    "assetpack": "8" * 64,
                    "capability_ledger": "9" * 64,
                    "gamepack": hashes["gamepack"],
                    "materialization_bundle": "a" * 64,
                    "package": hashes["package"],
                    "package_archive": hashes["package_archive"],
                    "runtime_bundle": "b" * 64,
                    "runtime_support_authority": "d" * 64,
                    "runtime_support_report": "e" * 64,
                    "standalone_game": "c" * 64,
                },
                "lineage": {stage: hashes["gamepack"] for stage in gate.LINEAGE_STAGES},
                "identities": {
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "assetpack_id": f"assetpack_{case_id.replace('-', '_')}",
                    "materialization_bundle_id": f"materialization_{case_id.replace('-', '_')}",
                    "package_id": f"package_{case_id.replace('-', '_')}",
                    "runtime_bundle_id": f"runtime_{case_id.replace('-', '_')}",
                    "runtime_support_authority_id": (
                        f"runtime_authority_{case_id.replace('-', '_')}"
                    ),
                    "runtime_support_report_id": f"runtime_report_{case_id.replace('-', '_')}",
                    "standalone_game_id": case_id.replace("-", "_"),
                },
                "native_evidence": {
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "extracted_runtime_bundle_hash": "b" * 64,
                    "frames": 2,
                    "gamepack_hash": hashes["gamepack"],
                    "platform_lock_hash": expected_lock["content_hash"],
                    "platform_lock_id": expected_lock["lock_id"],
                    "reason_code": None,
                    "runtime_artifact_sha256": expected_lock["dependency"]["artifact"]["sha256"],
                    "state": "passed",
                },
                "persistence": {
                    "endings": (
                        ["puzzle_complete"]
                        if case_id == "abstract-puzzle"
                        else ["ending_left", "ending_right"]
                    ),
                    "replays_verified": 1 if case_id == "abstract-puzzle" else 2,
                    "saves_restored": 1 if case_id == "abstract-puzzle" else 2,
                    "saves_verified": 1 if case_id == "abstract-puzzle" else 2,
                },
            }
        )
    report = {
        "format": gate.REPORT_FORMAT,
        "format_version": 1,
        "status": "passed",
        "source": {
            "input_tree_hash": "d" * 64,
            "revision": "e" * 40,
            "tree_state": "clean",
        },
        "toolchain": {
            "pillow": "12.3.0",
            "python": f"{python_minor}.0",
            "raylib": "6.0.1.0",
            "raylib_artifact": gate._runtime_artifact_identity(expected_lock),
            "world_forge": "0.7.0",
        },
        "host": host,
        "native_mode": "required",
        "cases": cases,
        "failure_reasons": [],
    }
    return gate.validate_release_report(report)


def _native_off_report(os_name: str, python_minor: str) -> dict[str, object]:
    gate = _load_gate()
    report = copy.deepcopy(_matrix_report(os_name, python_minor))
    report["native_mode"] = "off"
    report["toolchain"]["raylib_artifact"] = None
    for case in report["cases"]:
        case["native_evidence"] = gate.native_untested_evidence("off")
        case["stages"][-1] = {
            "reason_code": "native_disabled",
            "stage": "native",
            "state": "untested",
        }
    return gate.validate_release_report(report)


class MultigenreReleaseGateContractTests(unittest.TestCase):
    def test_script_does_not_import_tests_or_provider_sdks(self) -> None:
        path = ROOT / "scripts/verify_multigenre_release.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        roots.update(
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue({"gamepack_runtime", "worldforge"}.issubset(roots))
        self.assertTrue(
            roots.isdisjoint({"tests", "openai", "anthropic", "ollama", "requests", "httpx"})
        )
        imported_modules = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("gamepack_raylib_2d.native_smoke", imported_modules)
        self.assertIn('"scripts/native_smoke.py"', path.read_text(encoding="utf-8"))

        run_case = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_case"
        )
        called_names = {
            node.func.id
            for node in ast.walk(run_case)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertLessEqual(
            {
                "initialize_runtime_support_authority",
                "build_headless_evidence_set",
                "attach_verified_headless_evidence",
                "build_game_package_extraction_evidence",
                "attach_verified_game_package",
                "derive_runtime_evidence",
                "derive_runtime_support_report",
            },
            called_names,
        )

    def test_external_authority_markers_are_rejected_on_all_distribution_surfaces(self) -> None:
        gate = _load_gate()
        markers = (
            b"world-forge.runtime_support_authority",
            b"world-forge.hosted_native_release_authority",
            b"world-forge.hosted_native_release_attestation_receipt",
        )
        surfaces = (
            "assetpack",
            "runtime bundle",
            "materialization bundle",
            "standalone files",
            "package archive",
            "package extracted files",
            "save file",
            "replay file",
            "runtime snapshot",
        )
        for surface in surfaces:
            for marker in markers:
                with (
                    self.subTest(surface=surface, marker=marker.decode("ascii")),
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        "^runtime_support_authority_leaked:",
                    ),
                ):
                    gate._assert_runtime_authority_external(
                        surface,
                        {f"{surface.replace(' ', '-')}.json": b'{"format":"' + marker + b'"}'},
                    )

    def test_release_report_rejects_unknown_stage_hash_and_source_tampering(self) -> None:
        gate = _load_gate()
        report = _matrix_report("linux", "3.12")
        mutations = []

        unknown_stage = copy.deepcopy(report)
        unknown_stage["cases"][0]["stages"][0]["stage"] = "unknown"
        mutations.append((unknown_stage, "release_report_stage_invalid"))

        crossed_hash = copy.deepcopy(report)
        crossed_hash["cases"][0]["lineage"][gate.LINEAGE_STAGES[0]] = "f" * 64
        mutations.append((crossed_hash, "release_report_lineage_mismatch"))

        dirty_source = copy.deepcopy(report)
        dirty_source["source"]["tree_state"] = "dirty"
        mutations.append((dirty_source, "release_report_source_invalid"))

        extra = copy.deepcopy(report)
        extra["unexpected"] = True
        mutations.append((extra, "release_report_fields_invalid"))

        for document, reason in mutations:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    f"^{reason}:",
                ),
            ):
                gate.validate_release_report(document, hosted=True)

    def test_native_off_report_rejects_unsupported_headless_platforms(self) -> None:
        gate = _load_gate()
        linux_arm64 = copy.deepcopy(_native_off_report("linux", "3.12"))
        linux_arm64["host"]["architecture"] = "arm64"
        linux_arm64["host"]["platform_id"] = "platform:linux_arm64"

        unsupported_os = copy.deepcopy(_native_off_report("linux", "3.12"))
        unsupported_os["host"]["os"] = "darwin"
        unsupported_os["host"]["platform_id"] = "platform:darwin_x86_64"
        unsupported_os["host"]["runner_image"] = "local"

        for document in (linux_arm64, unsupported_os):
            with (
                self.subTest(host=document["host"]["platform_id"]),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^release_report_host_invalid:",
                ),
            ):
                gate.validate_release_report(document)

    def test_native_off_report_accepts_exact_x86_headless_platforms(self) -> None:
        gate = _load_gate()
        for os_name in ("linux", "windows"):
            with self.subTest(os_name=os_name):
                report = _native_off_report(os_name, "3.12")
                checked = gate.validate_release_report(report)
                self.assertEqual(checked["status"], "passed")
                self.assertEqual(checked["host"]["architecture"], "x86_64")
                self.assertEqual(checked["native_mode"], "off")
                for case in checked["cases"]:
                    self.assertEqual(case["native_evidence"], gate.native_untested_evidence("off"))

    def test_native_required_rejects_arm64_before_claiming_evidence(self) -> None:
        gate = _load_gate()
        host = {
            "architecture": "arm64",
            "os": "linux",
            "platform_id": "platform:linux_arm64",
            "python_minor": "3.12",
        }
        with self.assertRaisesRegex(
            gate.MultigenreReleaseError,
            "^native_platform_unsupported:",
        ):
            gate.require_native_host("required", host)
        self.assertEqual(
            gate.native_untested_evidence("off"),
            {
                "adapter_id": None,
                "adapter_version": None,
                "extracted_runtime_bundle_hash": None,
                "frames": 0,
                "gamepack_hash": None,
                "platform_lock_hash": None,
                "platform_lock_id": None,
                "reason_code": "native_disabled",
                "runtime_artifact_sha256": None,
                "state": "untested",
            },
        )

    def test_release_report_closes_host_identity_native_and_persistence_fields(self) -> None:
        gate = _load_gate()
        report = _matrix_report("windows", "3.11")
        mutations = []

        bad_toolchain = copy.deepcopy(report)
        bad_toolchain["toolchain"]["provider"] = "forbidden"
        mutations.append((bad_toolchain, "release_report_toolchain_invalid"))

        bad_artifact = copy.deepcopy(report)
        bad_artifact["toolchain"]["raylib_artifact"]["sha256"] = "0" * 64
        mutations.append((bad_artifact, "release_report_toolchain_invalid"))

        bad_host = copy.deepcopy(report)
        bad_host["host"]["platform_id"] = "platform:linux_x86_64"
        mutations.append((bad_host, "release_report_host_invalid"))

        bad_identity = copy.deepcopy(report)
        bad_identity["cases"][0]["identities"]["unexpected"] = "value"
        mutations.append((bad_identity, "release_report_identity_invalid"))

        bad_native = copy.deepcopy(report)
        bad_native["cases"][0]["native_evidence"]["frames"] = 0
        mutations.append((bad_native, "release_report_native_invalid"))

        bad_native_artifact = copy.deepcopy(report)
        bad_native_artifact["cases"][0]["native_evidence"]["runtime_artifact_sha256"] = "0" * 64
        mutations.append((bad_native_artifact, "release_report_native_invalid"))

        crossed_stage = copy.deepcopy(report)
        crossed_stage["cases"][0]["stages"][-1]["state"] = "failed"
        crossed_stage["cases"][0]["stages"][-1]["reason_code"] = "native_execution_failed"
        mutations.append((crossed_stage, "release_report_native_invalid"))

        duplicate_endings = copy.deepcopy(report)
        duplicate_endings["cases"][1]["persistence"]["endings"] = [
            "ending_left",
            "ending_left",
        ]
        mutations.append((duplicate_endings, "release_report_persistence_invalid"))

        missing_restore = copy.deepcopy(report)
        missing_restore["cases"][0]["persistence"]["saves_restored"] = 0
        mutations.append((missing_restore, "release_report_persistence_invalid"))

        for document, reason in mutations:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    f"^{reason}:",
                ),
            ):
                gate.validate_release_report(document, hosted=True)

    def test_release_report_rejects_non_integer_zero_native_frames(self) -> None:
        gate = _load_gate()
        required = _matrix_report("linux", "3.12")
        off = copy.deepcopy(required)
        off["native_mode"] = "off"
        off["toolchain"]["raylib_artifact"] = None
        for case in off["cases"]:
            case["native_evidence"] = gate.native_untested_evidence("off")
            case["stages"][-1] = {
                "reason_code": "native_disabled",
                "stage": "native",
                "state": "untested",
            }
        self.assertEqual(gate.validate_release_report(off), off)

        failed = copy.deepcopy(required)
        failed["native_mode"] = "optional"
        failed["status"] = "completed_with_native_gap"
        failed["failure_reasons"] = ["native_execution_failed"]
        for case in failed["cases"]:
            case["native_evidence"] = {
                **gate.native_untested_evidence("off"),
                "reason_code": "native_execution_failed",
                "state": "failed",
            }
            case["stages"][-1] = {
                "reason_code": "native_execution_failed",
                "stage": "native",
                "state": "failed",
            }
            case["status"] = "failed"
        self.assertEqual(gate.validate_release_report(failed), failed)

        for template in (off, failed):
            for invalid_frames in (False, 0.0):
                malformed = copy.deepcopy(template)
                malformed["cases"][0]["native_evidence"]["frames"] = invalid_frames
                with (
                    self.subTest(
                        state=template["cases"][0]["native_evidence"]["state"],
                        frames=repr(invalid_frames),
                    ),
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        "^release_report_native_invalid:",
                    ),
                ):
                    gate.validate_release_report(malformed)

    def test_release_report_rejects_malformed_nested_values_and_incoherent_reasons(self) -> None:
        gate = _load_gate()
        report = _matrix_report("windows", "3.11")
        mutations = []

        non_object_case = copy.deepcopy(report)
        non_object_case["cases"][0] = None
        mutations.append((non_object_case, "release_report_cases_invalid"))

        non_object_stage = copy.deepcopy(report)
        non_object_stage["cases"][0]["stages"][0] = []
        mutations.append((non_object_stage, "release_report_stage_invalid"))

        non_string_lineage = copy.deepcopy(report)
        non_string_lineage["cases"][0]["lineage"]["validate"] = []
        mutations.append((non_string_lineage, "release_report_lineage_mismatch"))

        unhashable_reason = copy.deepcopy(report)
        unhashable_reason["failure_reasons"] = [[]]
        mutations.append((unhashable_reason, "release_report_invalid"))

        unexpected_reason = copy.deepcopy(report)
        unexpected_reason["failure_reasons"] = ["invented_failure"]
        mutations.append((unexpected_reason, "release_report_native_invalid"))

        non_hex_lock = copy.deepcopy(report)
        non_hex_lock["cases"][0]["native_evidence"]["platform_lock_hash"] = "g" * 64
        mutations.append((non_hex_lock, "release_report_native_invalid"))

        empty_lock_id = copy.deepcopy(report)
        empty_lock_id["cases"][0]["native_evidence"]["platform_lock_id"] = ""
        mutations.append((empty_lock_id, "release_report_native_invalid"))

        python_mismatch = copy.deepcopy(report)
        python_mismatch["toolchain"]["python"] = "3.12.0"
        mutations.append((python_mismatch, "release_report_toolchain_invalid"))

        unavailable_hosted_raylib = copy.deepcopy(report)
        unavailable_hosted_raylib["toolchain"]["raylib"] = "unavailable"
        mutations.append((unavailable_hosted_raylib, "release_report_toolchain_invalid"))

        non_string_toolchain = copy.deepcopy(report)
        non_string_toolchain["toolchain"]["python"] = 312
        mutations.append((non_string_toolchain, "release_report_toolchain_invalid"))

        boolean_persistence_count = copy.deepcopy(report)
        boolean_persistence_count["cases"][0]["persistence"]["replays_verified"] = True
        mutations.append((boolean_persistence_count, "release_report_persistence_invalid"))

        arm_host = copy.deepcopy(report)
        arm_host["host"]["architecture"] = "arm64"
        arm_host["host"]["platform_id"] = "platform:windows_arm64"
        mutations.append((arm_host, "release_report_host_invalid"))

        fake_valid_lock = copy.deepcopy(report)
        fake_valid_lock["cases"][0]["native_evidence"]["platform_lock_hash"] = "e" * 64
        fake_valid_lock["cases"][0]["native_evidence"]["platform_lock_id"] = "fake_lock"
        mutations.append((fake_valid_lock, "release_report_native_invalid"))

        malformed_mode = copy.deepcopy(report)
        malformed_mode["native_mode"] = []
        mutations.append((malformed_mode, "release_report_native_invalid"))

        for document, reason in mutations:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    f"^{reason}:",
                ),
            ):
                gate.validate_release_report(document, hosted=True)

    def test_aggregate_requires_exact_complete_unique_matrix_and_hashes(self) -> None:
        gate = _load_gate()
        reports = [
            _matrix_report(os_name, python_minor)
            for os_name in ("linux", "windows")
            for python_minor in ("3.11", "3.12")
        ]
        aggregate = gate.aggregate_release_reports(reports)
        self.assertEqual(aggregate["status"], "passed")
        self.assertEqual(
            aggregate["matrix"],
            [
                {"os": "linux", "python_minor": "3.11"},
                {"os": "linux", "python_minor": "3.12"},
                {"os": "windows", "python_minor": "3.11"},
                {"os": "windows", "python_minor": "3.12"},
            ],
        )
        self.assertEqual(aggregate["source_revision"], "e" * 40)
        self.assertEqual(aggregate["source_input_tree_hash"], "d" * 64)
        self.assertEqual(len(aggregate["reports"]), 4)
        self.assertEqual(
            aggregate["reports"][0],
            {
                "os": "linux",
                "python_minor": "3.11",
                "report_sha256": hashlib.sha256(canonical_json_bytes(reports[0])).hexdigest(),
            },
        )
        self.assertEqual(
            aggregate["fixtures"][0]["gamepack_hash"],
            _fixture_hashes()["abstract-puzzle"]["gamepack"],
        )
        self.assertEqual(gate.validate_aggregate_report(aggregate), aggregate)

        bad_sets = []
        bad_sets.append((reports[:3], "release_aggregate_matrix_incomplete"))
        bad_sets.append(([reports[0], *reports], "release_aggregate_matrix_duplicate"))
        source_mismatch = copy.deepcopy(reports)
        source_mismatch[-1]["source"]["revision"] = "f" * 40
        bad_sets.append((source_mismatch, "release_aggregate_source_mismatch"))
        input_mismatch = copy.deepcopy(reports)
        input_mismatch[-1]["source"]["input_tree_hash"] = "f" * 64
        bad_sets.append((input_mismatch, "release_aggregate_source_mismatch"))
        fixture_mismatch = copy.deepcopy(reports)
        fixture_mismatch[-1]["cases"][0]["hashes"]["package_archive"] = "0" * 64
        bad_sets.append((fixture_mismatch, "release_aggregate_fixture_mismatch"))
        analysis_mismatch = copy.deepcopy(reports)
        analysis_mismatch[-1]["cases"][0]["hashes"]["analysis"] = "0" * 64
        bad_sets.append((analysis_mismatch, "release_aggregate_fixture_mismatch"))
        identity_mismatch = copy.deepcopy(reports)
        identity_mismatch[-1]["cases"][0]["identities"]["assetpack_id"] += "_drift"
        bad_sets.append((identity_mismatch, "release_aggregate_fixture_mismatch"))
        native_gap = copy.deepcopy(reports)
        failed_case = native_gap[-1]["cases"][0]
        failed_case["native_evidence"] = {
            **gate.native_untested_evidence("off"),
            "reason_code": "native_execution_failed",
            "state": "failed",
        }
        failed_case["stages"][-1] = {
            "reason_code": "native_execution_failed",
            "stage": "native",
            "state": "failed",
        }
        failed_case["status"] = "failed"
        native_gap[-1]["status"] = "failed"
        native_gap[-1]["failure_reasons"] = ["native_execution_failed"]
        bad_sets.append((native_gap, "release_aggregate_native_incomplete"))
        for documents, reason in bad_sets:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    f"^{reason}:",
                ),
            ):
                gate.aggregate_release_reports(documents)

        invalid_aggregates = []
        extra = copy.deepcopy(aggregate)
        extra["unexpected"] = True
        invalid_aggregates.append(extra)
        bad_report_hash = copy.deepcopy(aggregate)
        bad_report_hash["reports"][0]["report_sha256"] = "g" * 64
        invalid_aggregates.append(bad_report_hash)
        bad_fixture_hash = copy.deepcopy(aggregate)
        bad_fixture_hash["fixtures"][0]["gamepack_hash"] = "short"
        invalid_aggregates.append(bad_fixture_hash)
        for document in invalid_aggregates:
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^release_aggregate_invalid:",
            ):
                gate.validate_aggregate_report(document)

    def test_aggregate_loader_rejects_duplicate_json_keys(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-load-") as temporary:
            path = Path(temporary) / "report.json"
            path.write_bytes(b'{"format":"first","format":"second"}')
            with self.assertRaisesRegex(Exception, "duplicate JSON object key"):
                gate.load_release_report(path)

    def test_aggregate_hashes_exact_canonical_report_file_bytes(self) -> None:
        gate = _load_gate()
        reports = [
            _matrix_report(os_name, python_minor)
            for os_name in ("linux", "windows")
            for python_minor in ("3.11", "3.12")
        ]
        with tempfile.TemporaryDirectory(prefix="wf-release-file-hash-") as temporary:
            root = Path(temporary)
            loaded = []
            for index, report in enumerate(reports):
                path = root / f"report-{index}.json"
                payload = canonical_json_bytes(report)
                path.write_bytes(payload)
                item = gate.load_release_report(path)
                self.assertEqual(item.payload, payload)
                loaded.append(item)
            aggregate = gate.aggregate_release_reports(loaded)
            self.assertEqual(
                [item["report_sha256"] for item in aggregate["reports"]],
                [hashlib.sha256(item.payload).hexdigest() for item in loaded],
            )

            noncanonical = root / "noncanonical.json"
            noncanonical.write_text(
                json.dumps(reports[0], sort_keys=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^release_report_encoding_invalid:",
            ):
                gate.load_release_report(noncanonical)

    def test_aggregate_loader_reads_only_one_bounded_retained_file(self) -> None:
        gate = _load_gate()
        report = _matrix_report("linux", "3.12")
        with tempfile.TemporaryDirectory(prefix="wf-release-bounded-load-") as temporary:
            root = Path(temporary)
            path = root / "report.json"
            path.write_bytes(canonical_json_bytes(report))
            root.joinpath("unrelated.bin").write_bytes(b"x" * 2048)
            with mock.patch.object(
                gate,
                "capture_retained_tree",
                side_effect=AssertionError("parent tree must not be captured"),
            ):
                loaded = gate.load_release_report(path)
            self.assertEqual(loaded.payload, canonical_json_bytes(report))

    def test_runtime_wheel_bytes_match_the_selected_platform_lock(self) -> None:
        gate = _load_gate()
        payload = b"synthetic locked wheel bytes"
        with tempfile.TemporaryDirectory(prefix="wf-runtime-wheel-") as temporary:
            root = Path(temporary)
            wheel = root / "raylib-synthetic.whl"
            wheel.write_bytes(payload)
            lock = {
                "lock_id": "synthetic_lock",
                "content_hash": "1" * 64,
                "dependency": {
                    "artifact": {
                        "filename": wheel.name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    }
                },
            }
            expected = {
                "filename": wheel.name,
                "platform_lock_hash": "1" * 64,
                "platform_lock_id": "synthetic_lock",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            self.assertEqual(gate.verify_runtime_wheel(wheel, lock), expected)
            wheel.write_bytes(payload + b"tampered")
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_runtime_artifact_mismatch:",
            ):
                gate.verify_runtime_wheel(wheel, lock)

    def test_subprocess_json_boundary_is_bounded_strict_and_timed(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-process-") as temporary:
            root = Path(temporary)
            cases = (
                (
                    "duplicate.py",
                    'print(\'{"status":"first","status":"second"}\')\n',
                    "standalone_execution_invalid",
                    2.0,
                ),
                (
                    "oversized.py",
                    'print("x" * 4096)\n',
                    "standalone_output_too_large",
                    2.0,
                ),
                (
                    "timeout.py",
                    "import time\ntime.sleep(5)\n",
                    "standalone_execution_timeout",
                    0.05,
                ),
            )
            for name, source, reason, timeout_seconds in cases:
                path = root / name
                path.write_text(source, encoding="utf-8")
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        f"^{reason}:",
                    ),
                ):
                    gate._checked_subprocess_json(
                        (sys.executable, "-I", str(path)),
                        cwd=root,
                        environment={},
                        timeout_seconds=timeout_seconds,
                        output_limit=1024,
                    )

    def test_raw_subprocess_boundary_preserves_separate_bounded_stream_evidence(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-raw-process-") as temporary:
            root = Path(temporary)
            cases = (
                (
                    "success.py",
                    "import sys\n"
                    "sys.stdout.buffer.write(b'alpha\\n')\n"
                    "sys.stderr.buffer.write(b'beta\\n')\n",
                    {
                        "return_code": 0,
                        "stderr": b"beta\n",
                        "stderr_overflow": False,
                        "stdout": b"alpha\n",
                        "stdout_overflow": False,
                        "timed_out": False,
                    },
                    2.0,
                ),
                (
                    "nonzero.py",
                    "import sys\n"
                    "sys.stdout.buffer.write(b'before-exit')\n"
                    "sys.stderr.buffer.write(b'precise failure')\n"
                    "raise SystemExit(7)\n",
                    {
                        "return_code": 7,
                        "stderr": b"precise failure",
                        "stderr_overflow": False,
                        "stdout": b"before-exit",
                        "stdout_overflow": False,
                        "timed_out": False,
                    },
                    2.0,
                ),
                (
                    "timeout.py",
                    "import sys, time\n"
                    "sys.stdout.buffer.write(b'before-timeout')\n"
                    "sys.stdout.buffer.flush()\n"
                    "time.sleep(5)\n",
                    {
                        "stderr": b"",
                        "stderr_overflow": False,
                        "stdout": b"before-timeout",
                        "stdout_overflow": False,
                        "timed_out": True,
                    },
                    0.05,
                ),
                (
                    "stdout-overflow.py",
                    "import sys, time\n"
                    "sys.stdout.buffer.write(b'O' * 4096)\n"
                    "sys.stdout.buffer.flush()\n"
                    "time.sleep(5)\n",
                    {
                        "stderr": b"",
                        "stderr_overflow": False,
                        "stdout": b"O" * 128,
                        "stdout_overflow": True,
                        "timed_out": False,
                    },
                    2.0,
                ),
                (
                    "stderr-overflow.py",
                    "import sys, time\n"
                    "sys.stderr.buffer.write(b'E' * 4096)\n"
                    "sys.stderr.buffer.flush()\n"
                    "time.sleep(5)\n",
                    {
                        "stderr": b"E" * 128,
                        "stderr_overflow": True,
                        "stdout": b"",
                        "stdout_overflow": False,
                        "timed_out": False,
                    },
                    2.0,
                ),
                (
                    "both-overflow.py",
                    "import sys\n"
                    "sys.stdout.buffer.write(b'O' * 4096)\n"
                    "sys.stdout.buffer.flush()\n"
                    "sys.stderr.buffer.write(b'E' * 4096)\n"
                    "sys.stderr.buffer.flush()\n",
                    {
                        "stderr": b"E" * 128,
                        "stderr_overflow": True,
                        "stdout": b"O" * 128,
                        "stdout_overflow": True,
                        "timed_out": False,
                    },
                    2.0,
                ),
            )
            for name, source, expected, timeout_seconds in cases:
                script = root / name
                script.write_text(source, encoding="utf-8")
                with self.subTest(name=name):
                    result = gate._run_bounded_subprocess(
                        (sys.executable, "-I", str(script)),
                        cwd=root,
                        environment={},
                        timeout_seconds=timeout_seconds,
                        output_limit=128,
                    )
                    for field, value in expected.items():
                        self.assertEqual(value, getattr(result, field))
                    if expected.get("timed_out"):
                        self.assertNotEqual(0, result.return_code)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group containment")
    def test_raw_subprocess_containment_kills_grandchildren_with_inherited_pipes(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-process-tree-") as temporary:
            root = Path(temporary)
            marker = root / "late-grandchild-write.bin"
            grandchild = (
                "import pathlib, time\n"
                "time.sleep(1.0)\n"
                f"pathlib.Path({str(marker)!r}).write_bytes(b'escaped')\n"
                "print('grandchild-exit', flush=True)\n"
            )
            script = root / "spawn-grandchild.py"
            script.write_text(
                "import subprocess, sys\n"
                f"subprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}])\n"
                "print('root-exit', flush=True)\n",
                encoding="utf-8",
            )

            started = time.monotonic()
            result = gate._run_bounded_subprocess(
                (sys.executable, "-I", str(script)),
                cwd=root,
                environment={},
                timeout_seconds=2.0,
                output_limit=1024,
            )
            elapsed = time.monotonic() - started

            self.assertEqual(0, result.return_code)
            self.assertEqual(b"root-exit\n", result.stdout)
            self.assertLess(elapsed, 0.75)
            time.sleep(1.1)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descendant containment")
    def test_raw_subprocess_containment_kills_detached_grandchildren(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-detached-tree-") as temporary:
            root = Path(temporary)
            marker = root / "late-detached-write.bin"
            grandchild = (
                "import pathlib, time\n"
                "time.sleep(0.8)\n"
                f"pathlib.Path({str(marker)!r}).write_bytes(b'escaped')\n"
            )
            script = root / "spawn-detached-grandchild.py"
            script.write_text(
                "import subprocess, sys\n"
                "subprocess.Popen(\n"
                f"    [sys.executable, '-I', '-c', {grandchild!r}],\n"
                "    start_new_session=True,\n"
                "    stdin=subprocess.DEVNULL,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                "print('root-exit', flush=True)\n",
                encoding="utf-8",
            )

            started = time.monotonic()
            result = gate._run_bounded_subprocess(
                (sys.executable, "-I", str(script)),
                cwd=root,
                environment={},
                timeout_seconds=2.0,
                output_limit=1024,
            )
            elapsed = time.monotonic() - started

            self.assertEqual(0, result.return_code)
            self.assertEqual(b"root-exit\n", result.stdout)
            self.assertLess(elapsed, 0.75)
            time.sleep(0.9)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux scoped containment")
    def test_raw_subprocess_containment_preserves_unrelated_sibling_processes(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-scoped-tree-") as temporary:
            root = Path(temporary)
            ready = root / "target-ready.bin"
            marker = root / "unrelated-sibling-finished.bin"
            target = root / "contained-target.py"
            target.write_text(
                "import pathlib, time\n"
                f"pathlib.Path({str(ready)!r}).write_bytes(b'ready')\n"
                "time.sleep(0.2)\n"
                "print('target done', flush=True)\n",
                encoding="utf-8",
            )
            unrelated = (
                "import pathlib, time\n"
                "time.sleep(0.5)\n"
                f"pathlib.Path({str(marker)!r}).write_bytes(b'unrelated')\n"
            )
            sibling_started = threading.Event()
            sibling_errors: list[BaseException] = []

            def run_unrelated_sibling() -> None:
                try:
                    deadline = time.monotonic() + 2.0
                    while not ready.exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("contained target did not start")
                        time.sleep(0.005)
                    sibling = subprocess.Popen(
                        (sys.executable, "-I", "-c", unrelated),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    sibling_started.set()
                    sibling.wait(timeout=2.0)
                except BaseException as exc:
                    sibling_errors.append(exc)

            thread = threading.Thread(target=run_unrelated_sibling, daemon=True)
            thread.start()
            result = gate._run_bounded_subprocess(
                (sys.executable, "-I", str(target)),
                cwd=root,
                environment={},
                timeout_seconds=2.0,
                output_limit=1024,
            )
            thread.join(timeout=2.0)

            self.assertEqual(0, result.return_code)
            self.assertEqual(b"target done\n", result.stdout)
            self.assertTrue(sibling_started.is_set())
            self.assertFalse(thread.is_alive())
            self.assertEqual([], sibling_errors)
            self.assertTrue(marker.is_file())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper recovery")
    def test_broker_kill_is_bounded_and_fails_with_containment_lost(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-broker-kill-") as temporary:
            root = Path(temporary)
            pid_path = root / "detached.pid"
            child = (
                "import os, pathlib, signal, time\n"
                "broker = os.getppid()\n"
                "first = os.fork()\n"
                "if first == 0:\n"
                "    os.setsid()\n"
                "    second = os.fork()\n"
                "    if second == 0:\n"
                f"        pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                "        time.sleep(30)\n"
                "    os._exit(0)\n"
                "time.sleep(0.05)\n"
                "os.kill(broker, signal.SIGKILL)\n"
                "time.sleep(30)\n"
            )
            leaked_pid: int | None = None
            try:
                started = time.monotonic()
                with self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^subprocess_containment_lost:",
                ):
                    gate._run_bounded_subprocess(
                        [sys.executable, "-I", "-c", child],
                        cwd=root,
                        environment={},
                        timeout_seconds=2,
                    )
                self.assertLess(time.monotonic() - started, 2.0)
                deadline = time.monotonic() + 2
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists(), "detached child did not start")
                leaked_pid = int(pid_path.read_text())
                os.kill(leaked_pid, 0)
            finally:
                if leaked_pid is not None:
                    try:
                        os.kill(leaked_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux broker loss")
    def test_broker_loss_publishes_no_native_evidence_or_trusted_output(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-broker-loss-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            ingress = root / "ingress"
            evidence = root / "evidence"
            pid_path = root / "target.pid"
            ingress.mkdir()
            evidence.mkdir()
            _write_native_smoke_child(
                extracted,
                f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                "os.kill(os.getppid(), __import__('signal').SIGKILL)\n"
                "time.sleep(30)\n",
            )
            collector: dict[str, dict[str, bytes]] = {}
            target_pid: int | None = None
            try:
                with self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^subprocess_containment_lost:",
                ):
                    gate._run_extracted_native_smoke(
                        extracted_root=extracted,
                        ingress_root=ingress,
                        evidence_root=evidence,
                        case_id="broker-loss",
                        environment={},
                        adapter_id="adapter_test",
                        adapter_version="1.2.3",
                        platform_id="platform:test_x86_64",
                        timeout_seconds=2,
                        evidence_collector=collector,
                    )
                self.assertEqual({}, collector)
                self.assertEqual([], list(evidence.iterdir()))
                self.assertNotIn(
                    "_publish_native_smoke_evidence_closure",
                    inspect.getsource(gate._run_extracted_native_smoke),
                )
                target_pid = int(pid_path.read_text())
            finally:
                if target_pid is not None:
                    try:
                        os.kill(target_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper recovery")
    def test_outer_verifier_does_not_mutate_subreaper_state(self) -> None:
        gate = _load_gate()
        source = inspect.getsource(gate._start_linux_contained_process)
        self.assertNotIn("_LinuxDescendantContainment", source)
        self.assertNotIn("PR_SET_CHILD_SUBREAPER", source)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux isolated broker import")
    def test_linux_broker_bootstrap_imports_from_clean_isolated_environment(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-clean-broker-import-") as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    gate._LINUX_PROCESS_BROKER_BOOTSTRAP,
                    "3",
                    "4",
                ],
                cwd=temporary,
                env={},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=2,
                check=False,
            )
        self.assertEqual(70, completed.returncode)
        self.assertEqual(b"", completed.stderr)

    def test_linux_broker_descendant_discovery_enforces_process_bound(self) -> None:
        gate = _load_gate()
        containment = object.__new__(gate._LinuxBrokerDescendantContainment)
        containment._parent_pid = 100
        states = (
            gate._LinuxProcessState(101, 100, 1001),
            gate._LinuxProcessState(102, 100, 1002),
            gate._LinuxProcessState(103, 100, 1003),
        )
        signals: list[tuple[int, int, int]] = []

        with (
            mock.patch.object(gate, "_LINUX_DESCENDANT_MAX_PROCESSES", 2),
            mock.patch.object(gate, "_iter_linux_process_states", return_value=iter(states)),
            mock.patch.object(containment, "_same_process", return_value=True),
            mock.patch.object(
                containment,
                "_signal",
                side_effect=lambda pid, start_time, value: (
                    signals.append((pid, start_time, value)) or True
                ),
            ),
        ):
            tracked: dict[int, int] = {}
            changed, overflow = containment._discover(tracked, freeze=True)

        self.assertTrue(changed)
        self.assertTrue(overflow)
        self.assertEqual({101: 1001, 102: 1002}, tracked)
        self.assertEqual(
            [
                (101, 1001, signal.SIGSTOP),
                (102, 1002, signal.SIGSTOP),
                (103, 1003, signal.SIGKILL),
            ],
            signals,
        )

    def test_linux_broker_rejects_continuing_discovery_at_both_deadlines(self) -> None:
        gate = _load_gate()
        containment = object.__new__(gate._LinuxBrokerDescendantContainment)
        clock = [0.0]

        def discover(_tracked: dict[int, int], *, freeze: bool) -> tuple[bool, bool]:
            del freeze
            clock[0] += 1.1
            return True, False

        process = SimpleNamespace(pid=101, wait=mock.Mock(return_value=0))
        with (
            mock.patch.object(gate, "_linux_process_state", return_value=None),
            mock.patch.object(containment, "_discover", side_effect=discover),
            mock.patch.object(gate.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(gate.time, "sleep"),
        ):
            with self.assertRaisesRegex(OSError, "fixed-point proof"):
                containment.terminate_and_reap(process)

        process.wait.assert_called_once_with(timeout=gate._PROCESS_TREE_REAP_SECONDS)

    def test_linux_broker_rejects_last_scan_adoption_before_deadline(self) -> None:
        gate = _load_gate()
        containment = object.__new__(gate._LinuxBrokerDescendantContainment)
        clock = [0.0]
        discoveries = iter(
            (
                (False, False, 0.1),
                (False, False, 0.1),
                (False, False, 0.8),
                (True, False, 0.3),
            )
        )

        def discover(_tracked: dict[int, int], *, freeze: bool) -> tuple[bool, bool]:
            del freeze
            changed, overflow, elapsed = next(discoveries)
            clock[0] += elapsed
            return changed, overflow

        process = SimpleNamespace(pid=101, wait=mock.Mock(return_value=0))
        with (
            mock.patch.object(gate, "_linux_process_state", return_value=None),
            mock.patch.object(containment, "_discover", side_effect=discover),
            mock.patch.object(gate.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(gate.time, "sleep"),
        ):
            with self.assertRaisesRegex(OSError, "reap fixed-point proof"):
                containment.terminate_and_reap(process)

        process.wait.assert_called_once_with(timeout=gate._PROCESS_TREE_REAP_SECONDS)

    def test_linux_broker_accepts_two_stable_empty_scans_per_phase(self) -> None:
        gate = _load_gate()
        containment = object.__new__(gate._LinuxBrokerDescendantContainment)
        clock = [0.0]

        def discover(_tracked: dict[int, int], *, freeze: bool) -> tuple[bool, bool]:
            del freeze
            clock[0] += 0.1
            return False, False

        process = SimpleNamespace(pid=101, wait=mock.Mock(return_value=0))
        with (
            mock.patch.object(gate, "_linux_process_state", return_value=None),
            mock.patch.object(containment, "_discover", side_effect=discover) as scan,
            mock.patch.object(gate.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(gate.time, "sleep"),
        ):
            self.assertEqual(0, containment.terminate_and_reap(process))

        self.assertEqual(4, scan.call_count)

    def test_linux_broker_never_attests_empty_after_fixed_point_failure(self) -> None:
        gate = _load_gate()
        secret = b"s" * gate._LINUX_BROKER_SECRET_BYTES
        secret_reader, secret_writer = os.pipe()
        os.write(secret_writer, secret)
        os.close(secret_writer)
        target_input = SimpleNamespace(
            write=mock.Mock(),
            flush=mock.Mock(),
            close=mock.Mock(),
        )
        empty_stdout = SimpleNamespace(read=mock.Mock(return_value=b""), close=mock.Mock())
        empty_stderr = SimpleNamespace(read=mock.Mock(return_value=b""), close=mock.Mock())
        process = SimpleNamespace(
            pid=101,
            stdin=target_input,
            stdout=empty_stdout,
            stderr=empty_stderr,
            poll=mock.Mock(return_value=0),
        )
        containment = SimpleNamespace(
            terminate_and_reap=mock.Mock(
                side_effect=OSError("Linux descendant reap fixed-point proof timed out")
            ),
            close=mock.Mock(),
        )
        records: list[dict[str, object]] = []

        def record(
            _descriptor: int,
            payload: dict[str, object],
            *,
            secret: bytes,
        ) -> None:
            self.assertEqual(b"s" * gate._LINUX_BROKER_SECRET_BYTES, secret)
            records.append(dict(payload))

        def process_state(pid: int) -> object:
            return gate._LinuxProcessState(pid=pid, parent_pid=1, start_time=pid + 1000)

        try:
            with (
                mock.patch.object(
                    gate,
                    "_LinuxBrokerDescendantContainment",
                    return_value=containment,
                ),
                mock.patch.object(gate.subprocess, "Popen", return_value=process),
                mock.patch.object(gate, "_linux_process_state", side_effect=process_state),
                mock.patch.object(gate, "_write_linux_broker_record", side_effect=record),
                mock.patch.object(
                    gate.sys,
                    "stdin",
                    SimpleNamespace(buffer=SimpleNamespace(read=mock.Mock(return_value=b"\x01"))),
                ),
            ):
                self.assertEqual(
                    70,
                    gate._linux_process_broker(
                        ("child",),
                        ready_fd=999,
                        secret_fd=secret_reader,
                    ),
                )
        finally:
            try:
                os.close(secret_reader)
            except OSError:
                pass

        self.assertEqual(["target_ready"], [record["event"] for record in records])
        self.assertNotIn("domain_empty", {record["event"] for record in records})

    def test_linux_start_closes_authority_when_release_gate_write_fails(self) -> None:
        gate = _load_gate()
        release_input = SimpleNamespace(
            write=mock.Mock(side_effect=OSError("release failed")),
            flush=mock.Mock(),
            close=mock.Mock(),
        )
        process = SimpleNamespace(
            pid=101,
            stdin=release_input,
            stdout=SimpleNamespace(close=mock.Mock()),
            stderr=SimpleNamespace(close=mock.Mock()),
            wait=mock.Mock(return_value=0),
            returncode=0,
        )
        authority = SimpleNamespace(close=mock.Mock())
        broker_state = gate._LinuxProcessState(pid=101, parent_pid=1, start_time=1001)
        with (
            mock.patch.object(gate.os, "pipe", side_effect=((10, 11), (12, 13))),
            mock.patch.object(gate.os, "close"),
            mock.patch.object(gate.os, "write", return_value=gate._LINUX_BROKER_SECRET_BYTES),
            mock.patch.object(gate, "_linux_process_state", return_value=broker_state),
            mock.patch.object(gate, "_read_linux_broker_record", return_value={}) as read_record,
            mock.patch.object(gate, "_validate_linux_broker_ready", return_value=(102, 1002)),
            mock.patch.object(gate, "_LinuxBrokerAuthority", return_value=authority),
            mock.patch.object(gate, "_terminate_linux_broker"),
        ):
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "Linux broker did not establish containment: release failed",
            ):
                gate._start_linux_contained_process(
                    ("child",),
                    cwd=ROOT,
                    environment={},
                    popen=mock.Mock(return_value=process),
                )

        authority.close.assert_called_once_with()
        read_record.assert_called_once_with(
            10,
            timeout=gate._LINUX_BROKER_READY_TIMEOUT_SECONDS,
            secret=mock.ANY,
        )
        process.wait.assert_called_once_with(timeout=gate._PROCESS_TREE_REAP_SECONDS)

    def test_linux_broker_attests_ready_before_release_and_empty_after_cleanup(self) -> None:
        gate = _load_gate()
        start_source = inspect.getsource(gate._start_linux_contained_process)
        self.assertLess(
            start_source.index("_validate_linux_broker_ready("),
            start_source.index("process.stdin.write(_WINDOWS_PROCESS_START_GATE)"),
        )
        broker_source = inspect.getsource(gate._linux_process_broker)
        self.assertLess(
            broker_source.index('"event": "target_ready"'),
            broker_source.index("gate = sys.stdin.buffer.read(1)"),
        )
        self.assertLess(
            broker_source.index("containment.terminate_and_reap(process)"),
            broker_source.index('"event": "domain_empty"'),
        )

    def test_linux_broker_records_reject_wrong_nonce_and_nonempty_domain(self) -> None:
        gate = _load_gate()
        broker = gate._LinuxProcessState(pid=101, parent_pid=1, start_time=1001)
        ready = {
            "broker_pid": 101,
            "broker_start_time": 1001,
            "event": "target_ready",
            "nonce": "a" * 32,
            "target_pid": 102,
            "target_start_time": 1002,
        }
        with self.assertRaisesRegex(OSError, "ready record was invalid"):
            gate._validate_linux_broker_ready(
                ready,
                nonce="b" * 32,
                broker_state=broker,
            )
        authority = gate._LinuxBrokerAuthority(
            status_fd=-1,
            secret=b"s" * 32,
            nonce="a" * 32,
            broker_pid=101,
            broker_start_time=1001,
            target_pid=102,
            target_start_time=1002,
        )
        with self.assertRaisesRegex(OSError, "domain-empty record was invalid"):
            gate._validate_linux_broker_complete(
                {
                    "broker_pid": 101,
                    "domain_empty": False,
                    "event": "domain_empty",
                    "nonce": "a" * 32,
                    "return_code": 0,
                    "target_pid": 102,
                    "target_start_time": 1002,
                },
                authority=authority,
                return_code=0,
            )

    def test_linux_broker_records_require_a_broker_only_signature_key(self) -> None:
        gate = _load_gate()
        payload = {"event": "domain_empty", "nonce": "a" * 32}
        signed = gate._linux_broker_record(payload, secret=b"s" * 32)
        valid_reader, valid_writer = os.pipe()
        try:
            os.write(valid_writer, signed)
            os.close(valid_writer)
            valid_writer = -1
            self.assertEqual(
                payload,
                gate._read_linux_broker_record(
                    valid_reader,
                    timeout=1,
                    secret=b"s" * 32,
                ),
            )
        finally:
            os.close(valid_reader)
            if valid_writer >= 0:
                os.close(valid_writer)
        reader, writer = os.pipe()
        try:
            os.write(writer, signed)
            os.close(writer)
            writer = -1
            with self.assertRaisesRegex(OSError, "signature was invalid"):
                gate._read_linux_broker_record(reader, timeout=1, secret=b"x" * 32)
        finally:
            os.close(reader)
            if writer >= 0:
                os.close(writer)
        self.assertIn("secret_fd=int(sys.argv[2])", gate._LINUX_PROCESS_BROKER_BOOTSTRAP)
        self.assertNotIn("nonce=sys.argv", gate._LINUX_PROCESS_BROKER_BOOTSTRAP)
        broker_source = inspect.getsource(gate._linux_process_broker)
        self.assertLess(
            broker_source.index("os.close(secret_fd)"),
            broker_source.index("subprocess.Popen("),
        )

    def test_windows_process_containment_configures_and_gates_kill_on_close_job(self) -> None:
        gate = _load_gate()
        events: list[object] = []

        class FakeFunction:
            def __init__(self, callback):
                self.callback = callback
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.callback(*args)

        def create_job(_security: object, _name: object) -> int:
            events.append("create")
            return 91

        def set_information(
            handle: object,
            information_class: int,
            information: object,
            size: int,
        ) -> int:
            details = ctypes.cast(
                information,
                ctypes.POINTER(gate._WindowsJobObjectExtendedLimitInformation),
            ).contents
            events.append(
                (
                    "configure",
                    int(getattr(handle, "value", handle)),
                    information_class,
                    details.BasicLimitInformation.LimitFlags,
                    size,
                )
            )
            return 1

        def assign(handle: object, process_handle: object) -> int:
            events.append(
                (
                    "assign",
                    int(getattr(handle, "value", handle)),
                    int(getattr(process_handle, "value", process_handle)),
                )
            )
            return 1

        def terminate(handle: object, code: int) -> int:
            events.append(("terminate", int(getattr(handle, "value", handle)), code))
            return 1

        def close(handle: object) -> int:
            events.append(("close", int(getattr(handle, "value", handle))))
            return 1

        kernel32 = SimpleNamespace(
            CreateJobObjectW=FakeFunction(create_job),
            SetInformationJobObject=FakeFunction(set_information),
            AssignProcessToJobObject=FakeFunction(assign),
            TerminateJobObject=FakeFunction(terminate),
            CloseHandle=FakeFunction(close),
        )

        class FakeGate:
            def write(self, payload: bytes) -> int:
                events.append(("release", payload))
                return len(payload)

            def flush(self) -> None:
                events.append("flush")

            def close(self) -> None:
                events.append("gate-close")

        process = SimpleNamespace(
            _handle=73,
            stdin=FakeGate(),
            stdout=object(),
            stderr=object(),
        )

        def fake_popen(arguments: list[str], **kwargs: object):
            events.append(("spawn", tuple(arguments), kwargs))
            return process

        started, job = gate._start_windows_contained_process(
            ("native-command", "--flag"),
            cwd=Path("C:/work"),
            environment={"SAFE": "1"},
            kernel32=kernel32,
            popen=fake_popen,
        )
        self.assertIs(process, started)
        self.assertEqual("create", events[0])
        self.assertEqual(
            (
                "configure",
                91,
                gate._WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                gate._WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE,
                ctypes.sizeof(gate._WindowsJobObjectExtendedLimitInformation),
            ),
            events[1],
        )
        assign_index = next(
            index for index, item in enumerate(events) if item == ("assign", 91, 73)
        )
        release_index = next(
            index for index, item in enumerate(events) if item == ("release", b"\x01")
        )
        self.assertLess(assign_index, release_index)
        spawn = next(item for item in events if isinstance(item, tuple) and item[0] == "spawn")
        self.assertEqual(("native-command", "--flag"), spawn[1][-2:])
        self.assertEqual(subprocess.PIPE, spawn[2]["stdin"])

        job.terminate_and_close()
        self.assertEqual(("terminate", 91, 1), events[-2])
        self.assertEqual(("close", 91), events[-1])
        source = inspect.getsource(gate._run_bounded_subprocess)
        self.assertNotIn("taskkill", source.casefold())
        self.assertNotIn("process.kill", source)

    def test_windows_process_gate_stays_closed_when_job_assignment_fails(self) -> None:
        gate = _load_gate()
        events: list[object] = []

        class FakeFunction:
            def __init__(self, callback):
                self.callback = callback
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.callback(*args)

        kernel32 = SimpleNamespace(
            CreateJobObjectW=FakeFunction(lambda *_args: 41),
            SetInformationJobObject=FakeFunction(lambda *_args: 1),
            AssignProcessToJobObject=FakeFunction(
                lambda *_args: events.append("assignment-denied") or 0
            ),
            TerminateJobObject=FakeFunction(lambda *_args: events.append("terminate") or 1),
            CloseHandle=FakeFunction(lambda *_args: events.append("close") or 1),
        )

        class FakeGate:
            def write(self, _payload: bytes) -> int:
                events.append("RELEASED")
                return 1

            def flush(self) -> None:
                events.append("flushed")

            def close(self) -> None:
                events.append("gate-closed")

        process = SimpleNamespace(
            _handle=52,
            stdin=FakeGate(),
            stdout=object(),
            stderr=object(),
            wait=lambda **_kwargs: events.append("waited") or 125,
        )
        with self.assertRaisesRegex(OSError, "could not enter containment"):
            gate._start_windows_contained_process(
                ("untrusted-command",),
                cwd=Path("C:/work"),
                environment={},
                kernel32=kernel32,
                popen=lambda *_args, **_kwargs: process,
            )

        self.assertEqual(
            ["assignment-denied", "gate-closed", "terminate", "close", "waited"],
            events,
        )
        self.assertNotIn("RELEASED", events)

    def test_native_smoke_uses_only_the_report_file_and_persists_bounded_diagnostics(
        self,
    ) -> None:
        gate = _load_gate()
        expected = _native_smoke_report()
        payload = canonical_json_bytes(expected)
        stdout = b'raylib banner\n{"status":"wrong-last-line"}\n'
        stderr = b"raylib diagnostic\n"
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-report-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(
                extracted,
                f"report.write_bytes({payload!r})\n"
                f"sys.stdout.buffer.write({stdout!r})\n"
                f"sys.stderr.buffer.write({stderr!r})\n",
            )
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            before = {
                path.relative_to(extracted).as_posix(): path.read_bytes()
                for path in extracted.rglob("*")
                if path.is_file()
            }

            report = gate._run_extracted_native_smoke(
                extracted_root=extracted,
                ingress_root=ingress_root,
                evidence_root=evidence_root,
                case_id="abstract-puzzle",
                environment={},
                adapter_id="adapter_test",
                adapter_version="1.2.3",
                platform_id="platform:test_x86_64",
            )

            self.assertEqual(expected, report)
            case_evidence = evidence_root / "abstract-puzzle"
            self.assertEqual(payload, (case_evidence / "report.json").read_bytes())
            self.assertEqual(stdout, (case_evidence / "stdout.log").read_bytes())
            self.assertEqual(stderr, (case_evidence / "stderr.log").read_bytes())
            attempt_payload = (case_evidence / "attempt.json").read_bytes()
            attempt = json.loads(attempt_payload)
            self.assertEqual(canonical_json_bytes(attempt), attempt_payload)
            self.assertEqual(
                {
                    "case_id": "abstract-puzzle",
                    "format": "world-forge.native_smoke_attempt",
                    "format_version": 1,
                    "reason_code": None,
                    "report": {
                        "filename": "report.json",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    },
                    "return_code": 0,
                    "state": "passed",
                    "stderr": {
                        "filename": "stderr.log",
                        "sha256": hashlib.sha256(stderr).hexdigest(),
                        "size_bytes": len(stderr),
                        "truncated": False,
                    },
                    "stdout": {
                        "filename": "stdout.log",
                        "sha256": hashlib.sha256(stdout).hexdigest(),
                        "size_bytes": len(stdout),
                        "truncated": False,
                    },
                    "timed_out": False,
                },
                attempt,
            )
            self.assertEqual(
                before,
                {
                    path.relative_to(extracted).as_posix(): path.read_bytes()
                    for path in extracted.rglob("*")
                    if path.is_file()
                },
            )

    def test_native_smoke_separates_untrusted_ingress_from_exact_upload_closure(self) -> None:
        gate = _load_gate()
        payload = canonical_json_bytes(_native_smoke_report())
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-ingress-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(
                extracted,
                f"report.write_bytes({payload!r})\n"
                "report.with_name('rogue-2mib.bin').write_bytes(b'R' * (2 * 1024 * 1024))\n",
            )
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()

            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_ingress_unexpected:",
            ):
                gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=evidence_root,
                    case_id="rogue-ingress",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )

            case_evidence = evidence_root / "rogue-ingress"
            self.assertEqual(
                {"attempt.json", "stderr.log", "stdout.log"},
                {path.name for path in case_evidence.iterdir()},
            )
            attempt = json.loads((case_evidence / "attempt.json").read_bytes())
            self.assertEqual("native_smoke_ingress_unexpected", attempt["reason_code"])
            self.assertFalse(
                any(path.stat().st_size >= 2 * 1024 * 1024 for path in case_evidence.iterdir())
            )
            self.assertFalse((ingress_root / "rogue-ingress").exists())

    def test_native_smoke_parent_publishes_the_canonical_report_copy(self) -> None:
        gate = _load_gate()
        payload = canonical_json_bytes(_native_smoke_report())
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-parent-copy-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(extracted, f"report.write_bytes({payload!r})\n")
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            published: list[tuple[Path, str, bytes]] = []
            real_publish = gate.publish_bytes_noreplace

            def record_publish(parent: Path, name: str, content: bytes, **kwargs: object):
                published.append((Path(parent), name, content))
                return real_publish(parent, name, content, **kwargs)

            with mock.patch.object(
                gate,
                "publish_bytes_noreplace",
                side_effect=record_publish,
            ):
                report = gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=evidence_root,
                    case_id="canonical-copy",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )

            self.assertEqual(_native_smoke_report(), report)
            self.assertEqual(
                ["report.json", "stdout.log", "stderr.log", "attempt.json"],
                [name for _parent, name, _content in published],
            )
            self.assertEqual(payload, published[0][2])
            self.assertTrue(
                all(parent == evidence_root / "canonical-copy" for parent, _, _ in published)
            )
            self.assertFalse((ingress_root / "canonical-copy").exists())

    def test_native_upload_closure_is_deferred_until_every_child_has_finished(self) -> None:
        gate = _load_gate()
        run_case_source = inspect.getsource(gate._run_case)
        run_gate_source = inspect.getsource(gate.run_release_gate)
        self.assertIn('"native-smoke-staging"', run_case_source)
        self.assertNotIn('"native-smoke-evidence"', run_case_source)
        case_loop = run_gate_source.index("for case_id in CASES:")
        final_publish = run_gate_source.index("_publish_native_smoke_evidence_closure(")
        self.assertLess(case_loop, final_publish)
        self.assertNotIn('work_root / "native-smoke-evidence"', run_gate_source)
        output_publish = run_gate_source.index("_publish_native_smoke_github_output(")
        self.assertLess(final_publish, output_publish)
        report_publish = run_gate_source.index("publish_operational_report(")
        self.assertLess(report_publish, final_publish)
        self.assertIn("_TrustedNativeEvidenceFailure", run_gate_source)

    def test_native_optional_unavailable_gap_does_not_publish_empty_diagnostics(self) -> None:
        gate = _load_gate()
        template = _matrix_report("linux", "3.12")
        unavailable_cases = copy.deepcopy(template["cases"])
        for case in unavailable_cases:
            case["native_evidence"] = {
                **gate.native_untested_evidence("off"),
                "reason_code": "native_platform_unsupported",
                "state": "unavailable",
            }
            case["stages"][-1] = {
                "reason_code": "native_platform_unsupported",
                "stage": "native",
                "state": "unavailable",
            }
            case["status"] = "failed"
        case_results = [
            gate._CaseRunResult(report=case, native_failure=None) for case in unavailable_cases
        ]

        with tempfile.TemporaryDirectory(prefix="wf-native-optional-gap-") as temporary:
            root = Path(temporary)
            report_path = root / "report.json"
            with (
                mock.patch.object(gate, "_host_context", return_value=template["host"]),
                mock.patch.object(
                    gate,
                    "_source_context",
                    return_value={"revision": "e" * 40, "tree_state": "clean"},
                ),
                mock.patch.object(
                    gate,
                    "capture_release_inputs",
                    return_value=SimpleNamespace(tree_hash="d" * 64),
                ),
                mock.patch.object(gate, "materialize_release_input_subtree"),
                mock.patch.object(gate, "_toolchain_context", return_value=template["toolchain"]),
                mock.patch.object(gate, "_run_case", side_effect=case_results),
                mock.patch.object(gate, "_publish_native_smoke_evidence_closure") as publish,
            ):
                report = gate.run_release_gate(
                    source_root=ROOT,
                    report_path=report_path,
                    work_root=root / "work",
                    native_mode="optional",
                    native_evidence_parent=root / "trusted",
                )

            self.assertEqual("completed_with_native_gap", report["status"])
            self.assertEqual(["native_platform_unsupported"], report["failure_reasons"])
            self.assertTrue(report_path.is_file())
            publish.assert_not_called()

    def test_final_native_upload_closure_uses_an_exclusive_unpredictable_root(self) -> None:
        gate = _load_gate()
        publisher_source = inspect.getsource(gate._publish_native_smoke_evidence_closure)
        self.assertIn("trusted_parent, create=False", publisher_source)
        self.assertIn("as retained_parent", publisher_source)
        self.assertLess(
            publisher_source.index("open_verified_output_parent("),
            publisher_source.index("for _ in range(_NATIVE_SMOKE_EVIDENCE_ROOT_ATTEMPTS)"),
        )
        self.assertNotIn("candidate.mkdir", publisher_source)
        self.assertIn("retained_parent.assert_current()", publisher_source)
        self.assertIn("request_delete=False", publisher_source)
        payloads = _native_evidence_payloads()
        with tempfile.TemporaryDirectory(prefix="wf-native-final-closure-") as temporary:
            trusted_parent = Path(temporary) / "trusted"
            trusted_parent.mkdir()
            work_root = Path(temporary) / "work"
            work_root.mkdir()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_invalid:",
            ):
                gate._publish_native_smoke_evidence_closure(
                    trusted_parent,
                    {"abstract-puzzle": payloads["abstract-puzzle"]},
                    forbidden_roots=(work_root,),
                )
            self.assertEqual([], list(trusted_parent.iterdir()))

            first_token = "a" * 32
            second_token = "b" * 32
            collision = trusted_parent / f"world-forge-native-smoke-evidence-{first_token}"
            collision.mkdir()
            (collision / "rogue.bin").write_bytes(b"rogue")
            with mock.patch.object(
                gate.secrets,
                "token_hex",
                side_effect=(first_token, second_token),
            ):
                evidence_root = gate._publish_native_smoke_evidence_closure(
                    trusted_parent,
                    payloads,
                    forbidden_roots=(work_root,),
                )

            self.assertEqual(
                trusted_parent / f"world-forge-native-smoke-evidence-{second_token}",
                evidence_root,
            )
            self.assertEqual(b"rogue", (collision / "rogue.bin").read_bytes())
            self.assertEqual(
                {
                    "abstract-puzzle/attempt.json",
                    "abstract-puzzle/stderr.log",
                    "abstract-puzzle/stdout.log",
                    "branching-narrative/attempt.json",
                    "branching-narrative/report.json",
                    "branching-narrative/stderr.log",
                    "branching-narrative/stdout.log",
                },
                {
                    path.relative_to(evidence_root).as_posix()
                    for path in evidence_root.rglob("*")
                    if path.is_file()
                },
            )
            self.assertFalse(evidence_root.is_relative_to(work_root))

    def test_child_preclaim_and_permission_sabotage_are_never_upload_eligible(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-final-preclaim-") as temporary:
            root = Path(temporary)
            work_root = root / "work"
            fixed_child_path = work_root / "native-smoke-evidence"
            fixed_child_path.mkdir(parents=True)
            rogue = fixed_child_path / "rogue-2mib.bin"
            rogue.write_bytes(b"R" * (2 * 1024 * 1024))
            trusted_parent = root / "trusted"
            trusted_parent.mkdir()

            with mock.patch.object(
                Path,
                "rename",
                side_effect=PermissionError("child made work root non-renamable"),
            ) as rename:
                evidence_root = gate._publish_native_smoke_evidence_closure(
                    trusted_parent,
                    _native_evidence_payloads(),
                    forbidden_roots=(work_root,),
                )

            rename.assert_not_called()
            self.assertEqual(2 * 1024 * 1024, rogue.stat().st_size)
            self.assertFalse(evidence_root.is_relative_to(work_root))
            self.assertFalse((evidence_root / rogue.name).exists())
            self.assertEqual(trusted_parent, evidence_root.parent)

            inside_work = work_root / "trusted"
            inside_work.mkdir()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_publish_failed:",
            ):
                gate._publish_native_smoke_evidence_closure(
                    inside_work,
                    _native_evidence_payloads(),
                    forbidden_roots=(work_root,),
                )
            self.assertEqual([], list(inside_work.iterdir()))

    def test_native_child_environment_strips_github_and_runner_secrets(self) -> None:
        gate = _load_gate()
        sanitized = gate._sanitized_release_child_environment(
            {
                "ACTIONS_RUNTIME_TOKEN": "runtime-secret",
                "DISPLAY": ":99",
                "GITHUB_ENV": "/runner/_temp/env",
                "GITHUB_OUTPUT": "/runner/_temp/output",
                "GITHUB_PATH": "/runner/_temp/path",
                "GITHUB_STATE": "/runner/_temp/state",
                "GITHUB_STEP_SUMMARY": "/runner/_temp/summary",
                "PYTHONHOME": "/unsafe/home",
                "PYTHONPATH": "/unsafe/path",
                "RUNNER_TEMP": "/runner/_temp",
                "SAFE_VALUE": "retained",
            }
        )

        self.assertEqual({"DISPLAY": ":99", "SAFE_VALUE": "retained"}, sanitized)
        self.assertIn(
            "_sanitized_release_child_environment(os.environ)",
            inspect.getsource(gate._run_case),
        )

    def test_github_output_context_rejects_missing_or_injected_paths(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-output-context-") as temporary:
            root = Path(temporary)
            output = root / "github-output"
            output.touch()
            output_symlink = root / "github-output-link"
            output_symlink.symlink_to(output)
            self.assertEqual(
                (root.resolve(), output.resolve()),
                gate._native_smoke_ci_publication_context(
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_OUTPUT": str(output),
                        "RUNNER_TEMP": str(root),
                    },
                    native_mode="required",
                ),
            )
            self.assertEqual(
                (None, None),
                gate._native_smoke_ci_publication_context(
                    {"GITHUB_ACTIONS": "false"},
                    native_mode="required",
                ),
            )
            self.assertIn(
                "_native_smoke_ci_publication_context(",
                inspect.getsource(gate.main),
            )
            for environment, reason in (
                (
                    {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(root)},
                    "native_smoke_evidence_output_missing",
                ),
                (
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_OUTPUT": f"{output}\nrogue=true",
                        "RUNNER_TEMP": str(root),
                    },
                    "native_smoke_evidence_output_invalid",
                ),
                (
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_OUTPUT": str(output_symlink),
                        "RUNNER_TEMP": str(root),
                    },
                    "native_smoke_evidence_output_invalid",
                ),
            ):
                with (
                    self.subTest(reason=reason),
                    self.assertRaisesRegex(gate.MultigenreReleaseError, f"^{reason}:"),
                ):
                    gate._native_smoke_ci_publication_context(
                        environment,
                        native_mode="required",
                    )

    def test_github_output_enables_upload_only_after_safe_path_is_appended(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-github-output-") as temporary:
            root = Path(temporary)
            output = root / "github-output"
            output.touch()
            evidence_root = root / ("world-forge-native-smoke-evidence-" + "c" * 32)
            evidence_root.mkdir()
            release_row = evidence_root / "world-forge-Linux-py3.12.json"
            release_row.write_bytes(canonical_json_bytes(_matrix_report("linux", "3.12")))

            gate._publish_native_smoke_github_output(output, evidence_root, release_row)

            self.assertEqual(
                (
                    f"native_smoke_evidence_path={evidence_root.resolve()}\n"
                    "native_smoke_evidence_published=true\n"
                    f"release_row_path={release_row.resolve()}\n"
                    "release_row_published=true\n"
                ).encode(),
                output.read_bytes(),
            )

            injected = root / "world-forge-native-smoke-evidence-safe\nrogue=true"
            injected.mkdir()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_output_invalid:",
            ):
                gate._publish_native_smoke_github_output(output, injected, release_row)

            wrong_name = root / "fixed-native-smoke-evidence"
            wrong_name.mkdir()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_output_invalid:",
            ):
                gate._publish_native_smoke_github_output(output, wrong_name, release_row)

    def test_trusted_release_row_is_exclusive_and_inside_unpredictable_root(self) -> None:
        gate = _load_gate()
        report = _matrix_report("linux", "3.12")
        with tempfile.TemporaryDirectory(prefix="wf-native-release-row-") as temporary:
            root = Path(temporary)
            evidence_root = root / ("world-forge-native-smoke-evidence-" + "a" * 32)
            evidence_root.mkdir()
            row = gate._publish_trusted_native_release_row(evidence_root, report)
            self.assertEqual(evidence_root, row.parent)
            self.assertEqual("world-forge-Linux-py3.12.json", row.name)
            self.assertEqual(canonical_json_bytes(report), row.read_bytes())
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_release_row_publish_failed:",
            ):
                gate._publish_trusted_native_release_row(evidence_root, report)

    def test_github_output_rejects_nonempty_linked_and_missing_boundaries(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-github-boundaries-") as temporary:
            root = Path(temporary)
            evidence_root = root / ("world-forge-native-smoke-evidence-" + "e" * 32)
            evidence_root.mkdir()
            release_row = evidence_root / "world-forge-Linux-py3.12.json"
            release_row.write_bytes(canonical_json_bytes(_matrix_report("linux", "3.12")))
            missing_evidence = root / ("world-forge-native-smoke-evidence-" + "f" * 32)
            nonempty = root / "nonempty-output"
            nonempty.write_bytes(b"preclaimed=true\n")
            original = root / "linked-output"
            original.touch()
            hardlink = root / "hardlink-output"
            os.link(original, hardlink)
            symlink = root / "symlink-output"
            symlink.symlink_to(original)

            for output_path in (nonempty, hardlink, symlink):
                with (
                    self.subTest(output_path=output_path.name),
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        "^native_smoke_evidence_output_failed:",
                    ),
                ):
                    gate._publish_native_smoke_github_output(
                        output_path, evidence_root, release_row
                    )

            empty_output = root / "empty-output"
            empty_output.touch()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_output_failed:",
            ):
                gate._publish_native_smoke_github_output(
                    empty_output,
                    missing_evidence,
                    release_row,
                )

    def test_github_output_failure_never_appends_the_publication_flag(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-github-failure-") as temporary:
            root = Path(temporary)
            output = root / "github-output"
            output.touch()
            evidence_root = root / ("world-forge-native-smoke-evidence-" + "d" * 32)
            evidence_root.mkdir()
            release_row = evidence_root / "world-forge-Linux-py3.12.json"
            release_row.write_bytes(canonical_json_bytes(_matrix_report("linux", "3.12")))
            real_write = gate.os.write

            def fail_enabling_write(descriptor: int, payload: bytes) -> int:
                if b"release_row_published=true" in payload:
                    raise OSError("output unavailable")
                return real_write(descriptor, payload)

            with (
                mock.patch.object(gate.os, "write", side_effect=fail_enabling_write),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_smoke_evidence_output_failed:",
                ),
            ):
                gate._publish_native_smoke_github_output(output, evidence_root, release_row)

            self.assertEqual(b"", output.read_bytes())
            self.assertNotIn(b"native_smoke_evidence_published=true", output.read_bytes())

    def test_prewritten_rogue_github_outputs_cannot_authorize_upload(self) -> None:
        gate = _load_gate()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("Upload parent-attested native smoke diagnostics", workflow)
        with tempfile.TemporaryDirectory(prefix="wf-native-github-preclaim-") as temporary:
            root = Path(temporary)
            output = root / "github-output"
            rogue_root = root / "child-controlled"
            rogue_root.mkdir()
            output.write_text(
                f"native_smoke_evidence_path={rogue_root}\nnative_smoke_evidence_published=true\n",
                encoding="utf-8",
            )
            evidence_root = root / ("world-forge-native-smoke-evidence-" + "9" * 32)
            evidence_root.mkdir()
            release_row = evidence_root / "world-forge-Linux-py3.12.json"
            release_row.write_bytes(canonical_json_bytes(_matrix_report("linux", "3.12")))

            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_evidence_output_failed:",
            ):
                gate._publish_native_smoke_github_output(output, evidence_root, release_row)

            self.assertNotIn("path: native-smoke-evidence", workflow)

    def test_ci_native_main_does_not_forward_a_child_discoverable_report_path(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-dynamic-row-main-") as temporary:
            root = Path(temporary)
            output = root / "github-output"
            output.touch()
            with (
                mock.patch.object(
                    gate,
                    "_native_smoke_ci_publication_context",
                    return_value=(root, output),
                ),
                mock.patch.object(
                    gate,
                    "run_release_gate",
                    return_value=_matrix_report("linux", "3.12"),
                ) as run_gate,
                mock.patch("sys.stdout"),
            ):
                return_code = gate.main(
                    [
                        "--native",
                        "required",
                        "--work-root",
                        str(root / "work"),
                    ]
                )
            self.assertEqual(0, return_code)
            self.assertIsNone(run_gate.call_args.kwargs["report_path"])

    def test_main_fails_closed_after_parent_attested_native_failure(
        self,
    ) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-trusted-failure-") as temporary:
            root = Path(temporary)
            report = root / "report.json"
            work = root / "work"
            trusted = gate._TrustedNativeEvidenceFailure(
                "native_required_incomplete",
                "abstract-puzzle: native_smoke_timeout [native-smoke-evidence/abstract-puzzle]",
            )
            with (
                mock.patch.object(
                    gate,
                    "_native_smoke_ci_publication_context",
                    return_value=(root, root / "github-output"),
                ),
                mock.patch.object(gate, "run_release_gate", side_effect=trusted),
                mock.patch("sys.stderr"),
            ):
                return_code = gate.main(
                    [
                        "--native",
                        "required",
                        "--report",
                        str(report),
                        "--work-root",
                        str(work),
                    ]
                )

            self.assertEqual(1, return_code)

    @unittest.skipUnless(os.name == "posix", "POSIX descendant mutation containment")
    def test_native_smoke_kills_post_exit_mutator_before_tree_validation(self) -> None:
        gate = _load_gate()
        payload = canonical_json_bytes(_native_smoke_report())
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-post-exit-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            marker = extracted / "late-mutation.bin"
            grandchild = (
                "from pathlib import Path\n"
                "import time\n"
                "time.sleep(0.5)\n"
                f"Path({str(marker)!r}).write_bytes(b'late mutation')\n"
            )
            _write_native_smoke_child(
                extracted,
                "import subprocess\n"
                f"report.write_bytes({payload!r})\n"
                "subprocess.Popen(\n"
                f"    [sys.executable, '-I', '-c', {grandchild!r}],\n"
                "    start_new_session=True,\n"
                "    stdin=subprocess.DEVNULL,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n",
            )
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()

            report = gate._run_extracted_native_smoke(
                extracted_root=extracted,
                ingress_root=ingress_root,
                evidence_root=evidence_root,
                case_id="post-exit-mutator",
                environment={},
                adapter_id="adapter_test",
                adapter_version="1.2.3",
                platform_id="platform:test_x86_64",
            )

            self.assertEqual(_native_smoke_report(), report)
            time.sleep(0.6)
            self.assertFalse(marker.exists())

    def test_native_smoke_process_failures_cannot_be_rescued_by_a_report(self) -> None:
        gate = _load_gate()
        payload = canonical_json_bytes(_native_smoke_report())
        cases = [
            (
                "nonzero",
                f"report.write_bytes({payload!r})\n"
                "sys.stderr.buffer.write(b'native failure')\n"
                "raise SystemExit(7)\n",
                "native_smoke_exit_nonzero",
            ),
            (
                "timeout",
                f"report.write_bytes({payload!r})\n"
                "sys.stdout.buffer.write(b'before timeout')\n"
                "sys.stdout.buffer.flush()\n"
                "time.sleep(5)\n",
                "native_smoke_timeout",
            ),
            (
                "stdout-overflow",
                f"report.write_bytes({payload!r})\n"
                "sys.stdout.buffer.write(b'O' * 4096)\n"
                "sys.stdout.buffer.flush()\n"
                "time.sleep(5)\n",
                "native_smoke_stdout_too_large",
            ),
            (
                "stderr-overflow",
                f"report.write_bytes({payload!r})\n"
                "sys.stderr.buffer.write(b'E' * 4096)\n"
                "sys.stderr.buffer.flush()\n"
                "time.sleep(5)\n",
                "native_smoke_stderr_too_large",
            ),
            (
                "both-overflow",
                f"report.write_bytes({payload!r})\n"
                "sys.stdout.buffer.write(b'O' * 4096)\n"
                "sys.stdout.buffer.flush()\n"
                "sys.stderr.buffer.write(b'E' * 4096)\n"
                "sys.stderr.buffer.flush()\n",
                "native_smoke_streams_too_large",
            ),
        ]
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-process-") as temporary:
            root = Path(temporary)
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            for name, body, reason in cases:
                extracted = root / f"extracted-{name}"
                _write_native_smoke_child(extracted, body)
                timeout_seconds = 0.05 if name == "timeout" else 2.0
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(gate.MultigenreReleaseError, f"^{reason}:"),
                ):
                    gate._run_extracted_native_smoke(
                        extracted_root=extracted,
                        ingress_root=ingress_root,
                        evidence_root=evidence_root,
                        case_id=name,
                        environment={},
                        adapter_id="adapter_test",
                        adapter_version="1.2.3",
                        platform_id="platform:test_x86_64",
                        timeout_seconds=timeout_seconds,
                        output_limit=128,
                    )
                attempt_path = evidence_root / name / "attempt.json"
                attempt_payload = attempt_path.read_bytes()
                attempt = json.loads(attempt_payload)
                self.assertEqual(canonical_json_bytes(attempt), attempt_payload)
                self.assertEqual("failed", attempt["state"])
                self.assertEqual(reason, attempt["reason_code"])
                self.assertEqual(
                    name in {"stdout-overflow", "both-overflow"},
                    attempt["stdout"]["truncated"],
                )
                self.assertEqual(
                    name in {"stderr-overflow", "both-overflow"},
                    attempt["stderr"]["truncated"],
                )

    def test_native_smoke_preserves_primary_reason_if_diagnostics_cannot_publish(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(
            prefix="wf-native-smoke-diagnostics-failure-"
        ) as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(
                extracted,
                "sys.stderr.buffer.write(b'primary native failure')\nraise SystemExit(7)\n",
            )
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            with (
                mock.patch.object(
                    gate,
                    "publish_bytes_noreplace",
                    side_effect=gate.PersistenceIOError("diagnostics unavailable"),
                ),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_smoke_exit_nonzero:.*diagnostics could not be published",
                ),
            ):
                gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=evidence_root,
                    case_id="diagnostics-failure",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )

    def test_native_smoke_report_validation_rejects_every_untrusted_shape(self) -> None:
        gate = _load_gate()
        report = _native_smoke_report()
        canonical = canonical_json_bytes(report)
        duplicate = (
            b'{"adapter_id":"adapter_test","adapter_id":"duplicate",'
            b'"adapter_version":"1.2.3","frames":2,'
            b'"platform_id":"platform:test_x86_64",'
            b'"status":"native_smoke_executed"}\n'
        )
        cases = [
            ("missing", "", "native_smoke_report_missing"),
            (
                "extra-field",
                f"report.write_bytes({canonical_json_bytes({**report, 'unexpected': True})!r})\n",
                "native_smoke_report_contract_invalid",
            ),
            (
                "wrong-adapter",
                "report.write_bytes("
                f"{canonical_json_bytes({**report, 'adapter_id': 'other'})!r})\n",
                "native_smoke_report_contract_invalid",
            ),
            (
                "noncanonical",
                f"report.write_bytes({json.dumps(report, sort_keys=True).encode()!r})\n",
                "native_smoke_report_noncanonical",
            ),
            (
                "duplicate",
                f"report.write_bytes({duplicate!r})\n",
                "native_smoke_report_duplicate_keys",
            ),
            ("invalid-json", "report.write_bytes(b'{')\n", "native_smoke_report_invalid"),
            ("invalid-utf8", "report.write_bytes(b'\\xff')\n", "native_smoke_report_invalid"),
            (
                "oversized",
                "report.write_bytes(b'X' * (16 * 1024 + 1))\n",
                "native_smoke_report_too_large",
            ),
            (
                "hardlink",
                f"shared = report.with_name('shared-report.json')\n"
                f"shared.write_bytes({canonical!r})\n"
                "os.link(shared, report)\n",
                "native_smoke_report_unsafe",
            ),
            (
                "extracted-mutation",
                f"report.write_bytes({canonical!r})\n"
                "Path(__file__).with_name('tamper.bin').write_bytes(b'tamper')\n",
                "native_smoke_extracted_tree_changed",
            ),
        ]
        if os.name != "nt":
            cases.append(
                (
                    "symlink",
                    f"shared = report.with_name('shared-report.json')\n"
                    f"shared.write_bytes({canonical!r})\n"
                    "report.symlink_to(shared)\n",
                    "native_smoke_report_unsafe",
                )
            )
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-validation-") as temporary:
            root = Path(temporary)
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            for name, body, reason in cases:
                extracted = root / f"extracted-{name}"
                _write_native_smoke_child(extracted, body)
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(gate.MultigenreReleaseError, f"^{reason}:"),
                ):
                    gate._run_extracted_native_smoke(
                        extracted_root=extracted,
                        ingress_root=ingress_root,
                        evidence_root=evidence_root,
                        case_id=name,
                        environment={},
                        adapter_id="adapter_test",
                        adapter_version="1.2.3",
                        platform_id="platform:test_x86_64",
                    )
                attempt_payload = (evidence_root / name / "attempt.json").read_bytes()
                attempt = json.loads(attempt_payload)
                self.assertEqual(canonical_json_bytes(attempt), attempt_payload)
                self.assertEqual(reason, attempt["reason_code"])

    def test_native_smoke_bounds_post_run_tree_inventory_before_recapture(self) -> None:
        gate = _load_gate()
        payload = canonical_json_bytes(_native_smoke_report())
        cases = [
            (
                "huge-file",
                "Path(__file__).with_name('huge.bin').write_bytes(b'H' * (2 * 1024 * 1024))\n",
            ),
            (
                "many-files",
                "[(Path(__file__).with_name(f'rogue-{index:04d}.bin').write_bytes(b'x')) "
                "for index in range(512)]\n",
            ),
            (
                "size-drift",
                "Path(__file__).parents[1].joinpath('immutable.txt').write_bytes("
                "b'changed and substantially larger than before')\n",
            ),
            (
                "rogue-directory",
                "Path(__file__).with_name('rogue-directory').mkdir()\n",
            ),
        ]
        if os.name != "nt":
            cases.append(
                (
                    "rogue-symlink",
                    "Path(__file__).with_name('rogue-symlink').symlink_to("
                    "Path(__file__).parents[1].joinpath('immutable.txt'))\n",
                )
            )
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-tree-bounds-") as temporary:
            root = Path(temporary)
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            for name, mutation in cases:
                extracted = root / f"extracted-{name}"
                _write_native_smoke_child(
                    extracted,
                    f"report.write_bytes({payload!r})\n{mutation}",
                )
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        gate,
                        "capture_retained_tree",
                        wraps=gate.capture_retained_tree,
                    ) as capture,
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        "^native_smoke_extracted_tree_changed:",
                    ),
                ):
                    gate._run_extracted_native_smoke(
                        extracted_root=extracted,
                        ingress_root=ingress_root,
                        evidence_root=evidence_root,
                        case_id=name,
                        environment={},
                        adapter_id="adapter_test",
                        adapter_version="1.2.3",
                        platform_id="platform:test_x86_64",
                    )
                self.assertEqual(1, capture.call_count)
                attempt = json.loads((evidence_root / name / "attempt.json").read_bytes())
                self.assertEqual("native_smoke_extracted_tree_changed", attempt["reason_code"])

    def test_native_smoke_maps_recursive_and_surrogate_json_to_controlled_attempts(self) -> None:
        gate = _load_gate()
        recursive = b"[" * 2000 + b"0" + b"]" * 2000
        surrogate = (
            b'{"adapter_id":"\\ud800","adapter_version":"1.2.3","frames":2,'
            b'"platform_id":"platform:test_x86_64","status":"native_smoke_executed"}\n'
        )
        cases = (
            ("recursive-json", recursive, "native_smoke_report_invalid"),
            ("surrogate-json", surrogate, "native_smoke_report_noncanonical"),
        )
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-json-boundary-") as temporary:
            root = Path(temporary)
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            for name, invalid, reason in cases:
                extracted = root / f"extracted-{name}"
                _write_native_smoke_child(
                    extracted,
                    f"report.write_bytes({invalid!r})\n",
                )
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(gate.MultigenreReleaseError, f"^{reason}:"),
                ):
                    gate._run_extracted_native_smoke(
                        extracted_root=extracted,
                        ingress_root=ingress_root,
                        evidence_root=evidence_root,
                        case_id=name,
                        environment={},
                        adapter_id="adapter_test",
                        adapter_version="1.2.3",
                        platform_id="platform:test_x86_64",
                    )
                attempt = json.loads((evidence_root / name / "attempt.json").read_bytes())
                self.assertEqual(reason, attempt["reason_code"])

    def test_native_smoke_rejects_report_identity_mutation_and_inside_tree_evidence(self) -> None:
        gate = _load_gate()
        canonical = canonical_json_bytes(_native_smoke_report())
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-mutation-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(extracted, f"report.write_bytes({canonical!r})\n")
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()
            real_read = gate.read_bound_bytes
            report_reads = 0

            def mutate_after_first_read(path: Path, *, limit: int):
                nonlocal report_reads
                result = real_read(path, limit=limit)
                if Path(path).name == "report.json":
                    report_reads += 1
                    if report_reads == 1:
                        changed = {**_native_smoke_report(), "frames": 1}
                        Path(path).write_bytes(canonical_json_bytes(changed))
                return result

            with (
                mock.patch.object(gate, "read_bound_bytes", side_effect=mutate_after_first_read),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_smoke_report_changed:",
                ),
            ):
                gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=evidence_root,
                    case_id="identity-mutation",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )
            self.assertEqual(2, report_reads)

            inside = extracted / "native-smoke-evidence"
            inside.mkdir()
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^native_smoke_report_inside_extracted_tree:",
            ):
                gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=inside,
                    case_id="inside-tree",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )

    @unittest.skipUnless(os.name == "posix", "POSIX ancestry replacement seam")
    def test_native_smoke_evidence_parent_replacement_fails_closed(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-parent-swap-") as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            _write_native_smoke_child(extracted, "")
            ingress_root = root / "native-smoke-ingress"
            evidence_root = root / "native-smoke-evidence"
            ingress_root.mkdir()
            evidence_root.mkdir()

            def replace_parent(arguments: object, **_kwargs: object):
                values = list(arguments)
                report = Path(values[values.index("--report") + 1])
                retained = report.parent.with_name(f"{report.parent.name}-retained")
                report.parent.rename(retained)
                report.parent.mkdir()
                return gate._BoundedProcessResult(0, b"", b"", False, False, False)

            with (
                mock.patch.object(gate, "_run_bounded_subprocess", side_effect=replace_parent),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_smoke_ingress_changed:",
                ),
            ):
                gate._run_extracted_native_smoke(
                    extracted_root=extracted,
                    ingress_root=ingress_root,
                    evidence_root=evidence_root,
                    case_id="parent-swap",
                    environment={},
                    adapter_id="adapter_test",
                    adapter_version="1.2.3",
                    platform_id="platform:test_x86_64",
                )

    def test_private_native_failure_detail_preserves_case_reason_and_evidence_path(self) -> None:
        gate = _load_gate()
        results = (
            gate._CaseRunResult(
                report={"case_id": "abstract-puzzle"},
                native_failure=gate._NativeSmokeFailure(
                    "native_smoke_report_missing",
                    "native-smoke-evidence/abstract-puzzle",
                ),
            ),
            gate._CaseRunResult(
                report={"case_id": "branching-narrative"},
                native_failure=gate._NativeSmokeFailure(
                    "native_smoke_stderr_too_large",
                    "native-smoke-evidence/branching-narrative",
                ),
            ),
        )
        self.assertEqual(
            (
                "abstract-puzzle: native_smoke_report_missing "
                "[native-smoke-evidence/abstract-puzzle]; "
                "branching-narrative: native_smoke_stderr_too_large "
                "[native-smoke-evidence/branching-narrative]"
            ),
            gate._native_failure_detail(results),
        )

    def test_optional_diagnostics_failure_preserves_primary_native_detail(self) -> None:
        gate = _load_gate()
        primary = "abstract-puzzle: native_smoke_timeout [native-smoke-evidence/abstract-puzzle]"
        for reason in (
            "native_smoke_evidence_publish_failed",
            "native_smoke_evidence_output_failed",
        ):
            with (
                self.subTest(reason=reason),
                self.assertRaises(gate.MultigenreReleaseError) as caught,
            ):
                gate._raise_native_evidence_publish_failure(
                    (reason, "trusted native smoke diagnostics were unavailable"),
                    primary_native_detail=primary,
                )
            self.assertEqual(reason, caught.exception.reason_code)
            self.assertIn(primary, str(caught.exception))
            self.assertIn("diagnostics: native_evidence_publisher_failed", str(caught.exception))
        self.assertIn(
            "_raise_native_evidence_publish_failure(",
            inspect.getsource(gate.run_release_gate),
        )

    def test_source_tree_copy_rejects_hardlinks_and_preserves_exact_bytes(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-copy-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            source.joinpath("payload.json").write_text("{}\n", encoding="utf-8")
            destination = root / "destination"
            gate.copy_release_source_tree(source, destination)
            self.assertEqual(destination.joinpath("payload.json").read_bytes(), b"{}\n")

            hostile = root / "hostile"
            hostile.mkdir()
            hostile.joinpath("one.bin").write_bytes(b"same inode")
            os.link(hostile / "one.bin", hostile / "two.bin")
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^release_source_tree_invalid:",
            ):
                gate.copy_release_source_tree(hostile, root / "forbidden")

    def test_release_inputs_remain_bound_to_the_original_retained_bytes(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-release-inputs-") as temporary:
            root = Path(temporary)
            source = root / "repository"
            inputs = source / "examples" / "multigenre-contracts"
            case = inputs / "abstract-puzzle"
            runtime = inputs / "runtime"
            case.mkdir(parents=True)
            runtime.mkdir()
            case.joinpath("fixture.json").write_bytes(b"{}\n")
            runtime.joinpath("snapshot.json").write_bytes(b'{"runtime":true}\n')

            authority = gate.capture_release_inputs(source)
            self.assertEqual(len(authority.tree_hash), 64)

            case.joinpath("fixture.json").write_bytes(b'{"substituted":true}\n')
            runtime.joinpath("snapshot.json").write_bytes(b'{"substituted":true}\n')
            staged = root / "staged"
            gate.materialize_release_input_subtree(
                authority,
                "abstract-puzzle",
                staged / "abstract-puzzle",
            )
            gate.materialize_release_input_subtree(authority, "runtime", staged / "runtime")

            self.assertEqual(
                staged.joinpath("abstract-puzzle/fixture.json").read_bytes(),
                b"{}\n",
            )
            self.assertEqual(
                staged.joinpath("runtime/snapshot.json").read_bytes(),
                b'{"runtime":true}\n',
            )

        tree = ast.parse(
            (ROOT / "scripts/verify_multigenre_release.py").read_text(encoding="utf-8")
        )
        run_case = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_case"
        )
        called_names = {
            node.func.id
            for node in ast.walk(run_case)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("capture_retained_tree", called_names)
        self.assertNotIn("copy_release_source_tree", called_names)

    def test_packaged_runner_exposes_bounded_save_restoration(self) -> None:
        runner = STANDALONE_TEMPLATE_FILES["src/game/runner.py"][0].decode("utf-8")
        self.assertIn('parser.add_argument("--verify-save-slot")', runner)
        self.assertIn('"status": "save_restored"', runner)

    def test_required_native_host_rejection_precedes_output_creation(self) -> None:
        gate = _load_gate()
        arm_host = {
            "architecture": "arm64",
            "os": "linux",
            "platform_id": "platform:linux_arm64",
            "python_minor": "3.12",
        }
        with tempfile.TemporaryDirectory(prefix="wf-native-preflight-") as temporary:
            root = Path(temporary)
            report = root / "evidence" / "report.json"
            work = root / "work"
            with (
                mock.patch.object(gate, "_host_context", return_value=arm_host),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_platform_unsupported:",
                ),
            ):
                gate.run_release_gate(
                    source_root=ROOT,
                    report_path=report,
                    work_root=work,
                    native_mode="required",
                )
            self.assertFalse(report.parent.exists())
            self.assertFalse(work.exists())

    def test_native_off_unsupported_host_fails_before_publishing_passed_report(self) -> None:
        gate = _load_gate()
        arm_host = {
            "architecture": "arm64",
            "os": "linux",
            "platform_id": "platform:linux_arm64",
            "python_abi": "cp312",
            "python_implementation": "cpython",
            "python_minor": "3.12",
            "runner_image": "local",
        }
        with tempfile.TemporaryDirectory(prefix="wf-native-off-preflight-") as temporary:
            root = Path(temporary)
            report = root / "evidence" / "report.json"
            work = root / "work"
            with (
                mock.patch.object(gate, "_host_context", return_value=arm_host),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^release_report_host_invalid:",
                ),
            ):
                gate.run_release_gate(
                    source_root=ROOT,
                    report_path=report,
                    work_root=work,
                    native_mode="off",
                )
            self.assertFalse(report.parent.exists())
            self.assertFalse(work.exists())

    def test_required_native_missing_runtime_wheel_preflight_creates_no_outputs(self) -> None:
        gate = _load_gate()
        host = {
            "architecture": "x86_64",
            "os": "linux",
            "platform_id": "platform:linux_x86_64",
            "python_abi": "cp312",
            "python_implementation": "cpython",
            "python_minor": "3.12",
            "runner_image": "local",
        }
        with tempfile.TemporaryDirectory(prefix="wf-native-wheel-preflight-") as temporary:
            root = Path(temporary)
            report = root / "evidence" / "report.json"
            work = root / "work"
            with (
                mock.patch.object(gate, "_host_context", return_value=host),
                mock.patch.object(
                    gate,
                    "capture_release_inputs",
                    side_effect=AssertionError("release inputs must not be captured"),
                ),
                self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^native_runtime_artifact_missing:",
                ),
            ):
                gate.run_release_gate(
                    source_root=ROOT,
                    report_path=report,
                    work_root=work,
                    native_mode="required",
                )
            self.assertFalse(report.parent.exists())
            self.assertFalse(work.exists())

    def test_required_native_invalid_runtime_wheel_preflight_creates_no_outputs(self) -> None:
        gate = _load_gate()
        host = {
            "architecture": "x86_64",
            "os": "linux",
            "platform_id": "platform:linux_x86_64",
            "python_abi": "cp312",
            "python_implementation": "cpython",
            "python_minor": "3.12",
            "runner_image": "local",
        }
        with tempfile.TemporaryDirectory(prefix="wf-native-wheel-preflight-") as temporary:
            root = Path(temporary)
            missing = root / "missing.whl"
            directory = root / "wheel-dir.whl"
            directory.mkdir()
            target = root / "target.whl"
            target.write_bytes(b"not the locked wheel")
            symlink = root / "symlink.whl"
            try:
                symlink.symlink_to(target)
            except OSError:
                symlink = target
            for runtime_wheel in (missing, directory, symlink):
                report = root / f"evidence-{runtime_wheel.stem}" / "report.json"
                work = root / f"work-{runtime_wheel.stem}"
                with (
                    self.subTest(runtime_wheel=runtime_wheel.name),
                    mock.patch.object(gate, "_host_context", return_value=host),
                    mock.patch.object(
                        gate,
                        "capture_release_inputs",
                        side_effect=AssertionError("release inputs must not be captured"),
                    ),
                    self.assertRaisesRegex(
                        gate.MultigenreReleaseError,
                        "^native_runtime_artifact_mismatch:",
                    ),
                ):
                    gate.run_release_gate(
                        source_root=ROOT,
                        report_path=report,
                        work_root=work,
                        native_mode="required",
                        runtime_wheel=runtime_wheel,
                    )
                self.assertFalse(report.parent.exists())
                self.assertFalse(work.exists())

    def test_native_platform_lock_must_be_bound_into_materialization(self) -> None:
        gate = _load_gate()
        selected = {
            "lock_id": "platform_linux_x86_64_cp312",
            "content_hash": "d" * 64,
            "platform": {"os": "linux", "architecture": "x86_64"},
            "python": {"minor": "3.12"},
        }
        materialization = {
            "platform_locks": {
                "locks": [
                    {
                        "id": selected["lock_id"],
                        "content_hash": selected["content_hash"],
                        "os": "linux",
                        "python_minor": "3.12",
                    }
                ]
            }
        }
        gate.assert_materialized_platform_lock(materialization, selected)
        drifted = copy.deepcopy(materialization)
        drifted["platform_locks"]["locks"][0]["content_hash"] = "e" * 64
        with self.assertRaisesRegex(
            gate.MultigenreReleaseError,
            "^native_platform_lock_identity_mismatch:",
        ):
            gate.assert_materialized_platform_lock(drifted, selected)

    def test_local_native_off_runs_complete_external_chain_deterministically(self) -> None:
        gate = _load_gate()
        with tempfile.TemporaryDirectory(prefix="wf-multigenre-release-test-") as temporary:
            root = Path(temporary)
            first_path = root / "first.json"
            second_path = root / "second.json"
            host = gate._host_context()
            host.update(
                {
                    "architecture": "x86_64",
                    "platform_id": f"platform:{host['os']}_x86_64",
                }
            )
            with (
                mock.patch.object(gate, "_host_context", return_value=host),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
            ):
                first = gate.run_release_gate(
                    source_root=ROOT,
                    report_path=first_path,
                    work_root=root / "work-first",
                    native_mode="off",
                )
                second = gate.run_release_gate(
                    source_root=ROOT,
                    report_path=second_path,
                    work_root=root / "work-second",
                    native_mode="off",
                )
            self.assertEqual(first, second)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(first_path.read_bytes(), canonical_json_bytes(first))
            self.assertEqual(first["status"], "passed")
            self.assertEqual(
                [case["case_id"] for case in first["cases"]],
                ["abstract-puzzle", "branching-narrative"],
            )
            for case in first["cases"]:
                self.assertEqual(case["native_evidence"]["state"], "untested")
                self.assertEqual(case["native_evidence"]["reason_code"], "native_disabled")
                self.assertEqual(
                    set(case["lineage"].values()),
                    {case["hashes"]["gamepack"]},
                )
                self.assertEqual(
                    [stage["stage"] for stage in case["stages"]],
                    list(gate.REQUIRED_CASE_STAGES),
                )
                self.assertTrue(
                    all(
                        stage["state"] == "passed" or stage["stage"] == "native"
                        for stage in case["stages"]
                    )
                )

    def test_operational_report_publication_is_external_and_exclusive(self) -> None:
        gate = _load_gate()
        report = _matrix_report("linux", "3.12")
        with tempfile.TemporaryDirectory(prefix="wf-release-report-") as temporary:
            target = Path(temporary) / "report.json"
            gate.publish_operational_report(target, report, source_root=ROOT)
            self.assertEqual(target.read_bytes(), canonical_json_bytes(report))
            with self.assertRaisesRegex(
                gate.MultigenreReleaseError,
                "^release_report_output_exists:",
            ):
                gate.publish_operational_report(target, report, source_root=ROOT)
        with self.assertRaisesRegex(
            gate.MultigenreReleaseError,
            "^release_output_inside_repository:",
        ):
            gate.publish_operational_report(
                ROOT / "forbidden-release-report.json",
                report,
                source_root=ROOT,
            )
        if os.name != "nt":
            with tempfile.TemporaryDirectory(prefix="wf-release-symlink-") as temporary:
                root = Path(temporary)
                repository = root / "repository"
                repository.mkdir()
                link = root / "external-link"
                link.symlink_to(repository, target_is_directory=True)
                escaped = link / "escaped-report.json"
                with self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^release_output_inside_repository:",
                ):
                    gate.publish_operational_report(
                        escaped,
                        report,
                        source_root=repository,
                    )
                self.assertFalse((repository / escaped.name).exists())

                broken = root / "broken-report.json"
                broken.symlink_to(root / "missing-target")
                with self.assertRaisesRegex(
                    gate.MultigenreReleaseError,
                    "^release_report_output_exists:",
                ):
                    gate.publish_operational_report(
                        broken,
                        report,
                        source_root=repository,
                    )

    def test_ci_declares_four_static_native_axes_and_final_aggregation(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn(
            "${{ runner.temp }}/world-forge-multigenre-work/native-smoke-evidence",
            workflow,
        )
        self.assertNotIn("matrix:", workflow)
        expected_jobs = (
            "ubuntu-py312-core",
            "ubuntu-py311-compat-native",
            "windows-py312-release",
            "windows-py311-compat-native",
            "ci-required",
        )
        for job_id in expected_jobs:
            with self.subTest(job_id=job_id):
                self.assertEqual(1, workflow.count(f"  {job_id}:\n"))
        self.assertEqual(
            4, workflow.count("Verify exact generic release lineage with native raylib")
        )
        self.assertEqual(4, workflow.count("Upload exact native evidence row"))
        self.assertEqual(4, workflow.count("Upload parent-attested native smoke diagnostics"))
        for job_id, artifact_name in (
            ("ubuntu-py312-core", "multigenre-native-diagnostics-Linux-py3.12"),
            ("ubuntu-py311-compat-native", "multigenre-native-diagnostics-Linux-py3.11"),
            ("windows-py312-release", "multigenre-native-diagnostics-Windows-py3.12"),
            ("windows-py311-compat-native", "multigenre-native-diagnostics-Windows-py3.11"),
        ):
            with self.subTest(job_id=job_id):
                job = _workflow_job_block(workflow, job_id)
                self.assertIn(
                    "      - name: Upload parent-attested native smoke diagnostics\n"
                    "        if: always() && steps.native_release_verify.outcome == 'failure' && "
                    "steps.native_release_verify.outputs.native_smoke_evidence_published == "
                    "'true' && steps.native_release_verify.outputs."
                    "native_smoke_evidence_path != ''\n"
                    "        uses: actions/upload-artifact@"
                    "ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2\n"
                    "        with:\n"
                    f"          name: {artifact_name}\n"
                    "          path: ${{ steps.native_release_verify.outputs."
                    "native_smoke_evidence_path }}\n"
                    "          if-no-files-found: error\n"
                    "          retention-days: 90\n",
                    job,
                )
                self.assertIn(
                    "steps.native_release_verify.outcome == 'success' && "
                    "steps.native_release_verify.outputs.release_row_published == 'true'",
                    job,
                )
                self.assertNotIn("path: native-smoke-evidence", job)
        self.assertIn("Download Linux Python 3.11 native evidence", workflow)
        self.assertIn("Download Linux Python 3.12 native evidence", workflow)
        self.assertIn("Download Windows Python 3.11 native evidence", workflow)
        self.assertIn("Download Windows Python 3.12 native evidence", workflow)
        self.assertIn("Aggregate exact four native rows", workflow)
        final = workflow.split("  ci-required:\n", 1)[1]
        self.assertIn("permissions:\n      contents: read\n      id-token: write\n", final)
        self.assertIn("      attestations: write\n", final)
        self.assertIn("Install locked final-gate Python dependencies", workflow)
        self.assertIn("GITHUB_WORKFLOW_REF", final)
        self.assertIn("scripts.verify_hosted_native_release build-candidate", final)
        self.assertIn("scripts.verify_hosted_native_release verify-write-receipt", final)
        self.assertIn("scripts.verify_hosted_native_release reverify", final)
        self.assertIn("github.repository_id == '1305601753'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6", workflow)
        self.assertIn(
            'aggregate["matrix"] == [{"os": "linux", "python_minor": "3.11"}, '
            '{"os": "linux", "python_minor": "3.12"}, '
            '{"os": "windows", "python_minor": "3.11"}, '
            '{"os": "windows", "python_minor": "3.12"}]',
            workflow,
        )

    def test_ci_forbids_untrusted_hosted_release_topologies(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("workflow_run", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("github.event.inputs", workflow)
        self.assertNotIn("github.head_ref", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("run-id:", workflow)
        self.assertNotIn("github-token:", workflow)
        self.assertNotIn("packages:", workflow)
        _assert_all_uses_are_pinned(workflow)

    def test_windows_native_list_contains_exact_nineteen_tests(self) -> None:
        from tests.test_m6_release_readiness import (
            WINDOWS_NATIVE_PYTHON_TESTS,
            _windows_native_result_is_green,
        )

        self.assertEqual(len(WINDOWS_NATIVE_PYTHON_TESTS), 19)
        self.assertEqual(len(set(WINDOWS_NATIVE_PYTHON_TESTS)), 19)
        required = {
            "tests.test_m6_composed_bundle.DirectoryPublicationPortabilityTests."
            "test_native_windows_directory_publication_and_collision",
            "tests.test_bundle_publication.BundlePublicationTests."
            "test_native_windows_bundle_verifier_reads_while_seal_is_active",
            "tests.test_multigenre_game_package.GenericGamePackageTests."
            "test_windows_package_stage_denies_write_and_delete_sharing",
            "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
            "test_windows_native_runtime_tree_retains_root_and_file_bindings",
            "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
            "test_windows_native_runtime_tree_rejects_hardlinks_and_reparse_points",
            "tests.test_studio_shell_package_snapshot.StudioShellPackageSnapshotTests."
            "test_windows_snapshot_root_cleanup_deletes_native_empty_directory_by_handle",
        }
        self.assertTrue(required.issubset(WINDOWS_NATIVE_PYTHON_TESTS))
        result = SimpleNamespace(
            errors=[],
            expectedFailures=[("test", "failure")],
            failures=[],
            skipped=[],
            testsRun=19,
            unexpectedSuccesses=[],
            wasSuccessful=lambda: True,
        )
        self.assertFalse(_windows_native_result_is_green(result))

    def test_release_gate_is_documented_without_claiming_pending_hosted_evidence(self) -> None:
        required_documents = (
            ROOT / "AGENTS.md",
            ROOT / "agents/QUALITY_GATES.md",
            ROOT / "README.md",
            ROOT / "docs/MULTI_GENRE_ARCHITECTURE.md",
            ROOT / "docs/SUPPORT_MATRIX.md",
            ROOT / "docs/ROADMAP.md",
        )
        for path in required_documents:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                marker = text.index("verify_multigenre_release")
                statement = text[max(0, marker - 300) : marker + 1400]
                normalized = " ".join(statement.split())
                self.assertIn("PENDING", normalized)
                self.assertIn("Ubuntu 24.04", normalized)
                self.assertIn("Windows Server 2022", normalized)
                lowered = " ".join(text.split()).casefold()
                for forbidden in (
                    "hosted evidence is complete",
                    "hosted evidence passed",
                    "hosted status is complete",
                    "hosted status passed",
                ):
                    self.assertNotIn(forbidden, lowered)

        architecture = (ROOT / "docs/MULTI_GENRE_ARCHITECTURE.md").read_text(encoding="utf-8")
        table_end = architecture.index("\n\nThe canonical operational closure")
        table = architecture[architecture.index("| Example |") : table_end]
        self.assertEqual(sum(line.startswith("|") for line in table.splitlines()), 6)

        status_path = ROOT / "docs/evidence/multigenre-release-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(
            status,
            {
                "format": "world-forge.multigenre_release_documentation_status",
                "format_version": 1,
                "hosted_evidence": "PENDING",
                "required_matrix": [
                    {"os": "ubuntu-24.04", "python": "3.11"},
                    {"os": "ubuntu-24.04", "python": "3.12"},
                    {"os": "windows-2022", "python": "3.11"},
                    {"os": "windows-2022", "python": "3.12"},
                ],
            },
        )
        for path in required_documents:
            text = path.read_text(encoding="utf-8")
            self.assertIn("docs/evidence/multigenre-release-status.json", text)


if __name__ == "__main__":
    unittest.main()
