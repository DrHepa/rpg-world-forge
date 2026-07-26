from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
STUDIO_PACKAGE = ROOT / "apps/studio/package.json"
STUDIO_PACKAGE_LOCK = ROOT / "apps/studio/package-lock.json"
SETUP_NODE_SHA = "49933ea5288caeca8642d1e84afbd3f7d6820020"
EXPECTED_STUDIO_JOB_SHA256 = "42e747079c1166c526a43bf9e2fe2de35921fb3c4fdd6c7e70d5aba785f08a86"
NPM_BOOTSTRAP_COMMAND = "npm install --global --ignore-scripts --no-audit --no-fund npm@11.13.0"
STUDIO_PYTHON_ENVIRONMENTS = (
    "PYTHON",
    "RWF_STUDIO_BUILD_PYTHON",
    "RWF_STUDIO_TEST_PYTHON",
)
STUDIO_JOB_ENVIRONMENT = (
    "    env:\n"
    '      CSC_IDENTITY_AUTO_DISCOVERY: "false"\n'
    '      PYTHONDONTWRITEBYTECODE: "1"\n'
    '      PYTHONNOUSERSITE: "1"\n'
    '      PYTHONUTF8: "1"\n'
)
STUDIO_PYTHON_OWNER_STEPS = (
    "Bind exact Linux Python for Studio subprocesses",
    "Bind exact Windows Python for Studio subprocesses",
)
WINDOWS_NATIVE_SHELL_STEP = "Exercise native Windows shell handle contract without skips"
WINDOWS_NATIVE_PYTHON_READ = "& $env:RWF_STUDIO_BUILD_PYTHON"
WINDOWS_NATIVE_PYTHON_TESTS = (
    "tests.test_m4_world_lifecycle.BumpWorldVersionTests."
    "test_windows_lock_rename_denial_is_fail_closed",
    "tests.test_renderpack_resources.DirectMediaValidationTests."
    "test_native_windows_path_and_descriptor_file_states_agree",
    "tests.test_renderpack_resources.RenderPackResourceBoundaryTests."
    "test_windows_created_private_acl_passes_native_validation",
    "tests.test_runtime_io.AtomicRuntimeWriterTests."
    "test_windows_parent_rename_denial_is_fail_closed",
    "tests.test_studio_authoring.StudioAuthoringTests."
    "test_overview_exercises_native_windows_pinned_directory_path",
    "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
    "test_native_windows_handles_block_target_swap_through_final_read",
    "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
    "test_native_windows_owned_temp_revalidates_blocks_rename_and_cleans",
    "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
    "test_native_windows_owned_temp_publishes_and_reopens",
    "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
    "test_native_windows_owned_temp_preserves_mismatched_winner",
    "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
    "test_native_windows_backend_assembles_and_zips_windows_target",
    "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
    "test_native_windows_pinned_reads_reject_hardlinks_and_retained_swaps",
    "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
    "test_native_windows_retained_handles_block_after_write_parent_swaps",
    "tests.test_m6_game_consumer.M6GameConsumerTests."
    "test_native_windows_generation_stage_handle_blocks_swap",
)
WINDOWS_NATIVE_SHELL_TEST = "retains the native Windows package root against parent replacement"


def _studio_job(workflow: str) -> str:
    studio = list(re.finditer(r"(?m)^  studio-m6-readiness:[ \t]*\r?\n", workflow))
    boundary = list(re.finditer(r"(?m)^  graphical-raylib-smoke:[ \t]*\r?\n", workflow))
    if len(studio) != 1 or len(boundary) != 1 or studio[0].start() >= boundary[0].start():
        raise ValueError("invalid Studio job boundary")
    return workflow[studio[0].end() : boundary[0].start()]


def _studio_job_sha256(studio: str) -> str:
    universal_newlines = studio.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(universal_newlines.encode("utf-8")).hexdigest()


def _workflow_step(job: str, name: str) -> str:
    matching = [step for step_name, step in _workflow_steps(job) if step_name == name]
    if len(matching) != 1:
        raise ValueError(f"expected exactly one workflow step named {name!r}")
    return matching[0]


def _workflow_steps(job: str) -> tuple[tuple[str | None, str], ...]:
    boundaries = list(re.finditer(r"(?m)^      - ", job))
    steps: list[tuple[str | None, str]] = []
    for index, boundary in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(job)
        step = job[boundary.start() : end]
        header = re.match(
            r"^      - name:[ \t]*(?P<name>[^\r\n]+?)[ \t]*(?:\r?\n|$)",
            step,
        )
        steps.append((header["name"] if header is not None else None, step))
    return tuple(steps)


def _studio_environment_contract_errors(workflow: str) -> tuple[str, ...]:
    errors: list[str] = []
    jobs = re.search(r"(?m)^jobs:\s*$", workflow)
    if jobs is None:
        return ("missing_jobs",)
    workflow_preamble = workflow[: jobs.start()]
    root_fields = re.findall(r"(?m)^\S.*$", workflow_preamble)
    if any(
        not line.startswith(("name:", "on:", "permissions:")) for line in root_fields
    ) or re.search(r"(?i)(?<![A-Z0-9_])env(?![A-Z0-9_])", workflow_preamble):
        errors.append("workflow_environment")

    try:
        studio = _studio_job(workflow)
    except ValueError:
        return (*errors, "studio_job_boundary")
    if _studio_job_sha256(studio) != EXPECTED_STUDIO_JOB_SHA256:
        errors.append("studio_job_sha256")
    steps_marker = "    steps:\n"
    if studio.count(steps_marker) != 1:
        return (*errors, "studio_steps")
    preamble, _step_source = studio.split(steps_marker, 1)
    job_fields = re.findall(r"(?m)^ {4}(\S.*)$", studio)
    if job_fields.count("env:") != 1 or any(
        not field.startswith(("name:", "runs-on:", "strategy:", "env:", "steps:"))
        for field in job_fields
    ):
        errors.append("studio_environment")
    if not preamble.endswith(STUDIO_JOB_ENVIRONMENT):
        errors.append("studio_environment")
    elif re.search(
        r"(?i)(?<![A-Z0-9_])env(?![A-Z0-9_])",
        preamble[: -len(STUDIO_JOB_ENVIRONMENT)],
    ):
        errors.append("duplicate_studio_environment")

    steps = _workflow_steps(studio)
    step_names = [(name or "").strip() for name, _step in steps]
    expected_step_names = (
        "Check out source",
        "Set up Python",
        "Set up Node",
        "Install exact npm toolchain",
        *STUDIO_PYTHON_OWNER_STEPS,
        "Verify pinned Node and npm toolchain",
        "Install exact Studio dependencies",
        "Run complete Studio verification",
        "Exercise synthetic assembly and real fail-before-output policy",
        "Exercise native Windows Python handle contracts without skips",
        WINDOWS_NATIVE_SHELL_STEP,
        "Build and reverify unpacked Linux shell",
        "Build and reverify unpacked Windows shell",
    )
    if (
        not step_names
        or any(name in {"", '""', "''"} for name in step_names)
        or len(step_names) != len(set(step_names))
    ):
        errors.append("studio_step_identity")
    elif tuple(step_names) != expected_step_names:
        errors.append("studio_step_order")
    expected_actions = {
        "Check out source": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "Set up Python": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "Set up Node": f"actions/setup-node@{SETUP_NODE_SHA}",
    }
    allowed_step_fields = ("uses:", "with:", "if:", "shell:", "run:", "working-directory:")
    for name, step in steps:
        fields = tuple(
            line[8:]
            for line in step.splitlines()
            if line.startswith("        ") and not line.startswith("         ")
        )
        if any(not field.startswith(allowed_step_fields) for field in fields):
            errors.append("step_environment")
            break
        uses = tuple(
            field.removeprefix("uses:").split("#", 1)[0].strip()
            for field in fields
            if field.startswith("uses:")
        )
        run_count = sum(field.startswith("run:") for field in fields)
        expected_uses = (expected_actions[name],) if name in expected_actions else ()
        if uses != expected_uses or run_count != (0 if expected_uses else 1):
            errors.append("studio_step_kind")
            break

    remainder = studio
    try:
        for owner in STUDIO_PYTHON_OWNER_STEPS:
            owner_step = _workflow_step(studio, owner)
            remainder = remainder.replace(owner_step, "", 1)
        consumer_step = _workflow_step(studio, WINDOWS_NATIVE_SHELL_STEP)
    except ValueError:
        return (*errors, "studio_python_step")

    if consumer_step.count(WINDOWS_NATIVE_PYTHON_READ) != 1:
        errors.append("windows_python_consumer")
    remainder = remainder.replace(WINDOWS_NATIVE_PYTHON_READ, "", 1)
    if re.search("GITHUB_ENV", remainder, re.IGNORECASE):
        errors.append("github_environment_outside_owner")
    for name in STUDIO_PYTHON_ENVIRONMENTS:
        token = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        windows_reference = rf"(?i)\$(?:env:{name}(?![A-Z0-9_])|\{{env:{name}\}})"
        if re.search(token, remainder) or re.search(windows_reference, remainder):
            errors.append(f"protected_python_name:{name}")
    return tuple(errors)


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
    source = str(ROOT / "src")
    if root not in sys.path:
        sys.path.insert(0, root)
    if source not in sys.path:
        sys.path.insert(0, source)
    suite = unittest.defaultTestLoader.loadTestsFromNames(WINDOWS_NATIVE_PYTHON_TESTS)
    if suite.countTestCases() != len(WINDOWS_NATIVE_PYTHON_TESTS):
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


class M6ReleaseReadinessContractTests(unittest.TestCase):
    def test_studio_environment_contract_rejects_structural_mutations(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count(STUDIO_JOB_ENVIRONMENT), 1)
        verify_step = (
            "      - name: Run complete Studio verification\n"
            "        working-directory: apps/studio\n"
            "        run: npm run verify\n"
        )
        self.assertEqual(workflow.count(verify_step), 1)
        linux_owner = (
            f"      - name: {STUDIO_PYTHON_OWNER_STEPS[0]}\n        if: runner.os == 'Linux'\n"
        )
        self.assertEqual(workflow.count(linux_owner), 1)
        linux_owner_step = _workflow_step(_studio_job(workflow), STUDIO_PYTHON_OWNER_STEPS[0])
        checkout = _workflow_step(_studio_job(workflow), "Check out source")
        checkout_identity = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        self.assertEqual(checkout.count(checkout_identity), 1)
        npm_step = _workflow_step(_studio_job(workflow), "Install exact npm toolchain")
        cross_job_alias = workflow.replace(
            "      - name: Check out source\n",
            "      - &shared_checkout\n        name: Check out source\n",
            1,
        )
        studio_start = cross_job_alias.index("  studio-m6-readiness:\n")
        cross_job_alias = cross_job_alias[:studio_start] + cross_job_alias[studio_start:].replace(
            checkout,
            "      - *shared_checkout\n",
            1,
        )

        def replace_verify(command: str) -> str:
            return workflow.replace(
                verify_step,
                verify_step.replace("run: npm run verify", f"run: {command}"),
                1,
            )

        def move_before_owners(name: str) -> str:
            step = _workflow_step(_studio_job(workflow), name)
            return workflow.replace(step, "", 1).replace(linux_owner, step + linux_owner, 1)

        job_environments = {
            "flow": '    env: {CSC_IDENTITY_AUTO_DISCOVERY: "false"}\n',
            "explicit": '    ? env\n    : {CSC_IDENTITY_AUTO_DISCOVERY: "false"}\n',
            "escaped": '    "\\u0065nv":\n      CSC_IDENTITY_AUTO_DISCOVERY: "false"\n',
            "alias": "    env: *studio_environment\n",
        }
        mutations = {
            label: (
                workflow.replace(STUDIO_JOB_ENVIRONMENT, replacement, 1),
                "studio_environment",
            )
            for label, replacement in job_environments.items()
        }
        mutations.update(
            {
                "step alias": (
                    workflow.replace(
                        linux_owner,
                        linux_owner.replace(
                            "        if:",
                            "        env: *studio_environment\n        if:",
                        ),
                        1,
                    ),
                    "step_environment",
                ),
                "braced PowerShell environment": (
                    replace_verify("Write-Output ${env:GITHUB_ENV}"),
                    "github_environment_outside_owner",
                ),
                "protected PowerShell environment": (
                    replace_verify("Write-Output ${Env:rwf_studio_build_python}"),
                    "protected_python_name:RWF_STUDIO_BUILD_PYTHON",
                ),
                "absolute tee": (
                    replace_verify("printf 'PYTHON=x' | /usr/bin/tee -a \"$GITHUB_ENV\""),
                    "github_environment_outside_owner",
                ),
                "later direct assignment": (
                    replace_verify("PYTHON=python npm run verify"),
                    "protected_python_name:PYTHON",
                ),
                "extra indirect owner write": (
                    workflow.replace(
                        linux_owner_step,
                        linux_owner_step.replace(
                            '          test -x "${python_path}"\n',
                            '          test -x "${python_path}"\n'
                            '          environment_target="${GITHUB_ENV}"\n'
                            "          printf 'EXTRA=1\\n' >> "
                            '"${environment_target}"\n',
                        ),
                        1,
                    ),
                    "studio_job_sha256",
                ),
                "disabled verification": (
                    workflow.replace(
                        verify_step,
                        verify_step.replace(
                            "        working-directory:",
                            "        if: false\n        working-directory:",
                        ),
                        1,
                    ),
                    "studio_job_sha256",
                ),
                "cross-job step alias": (cross_job_alias, "studio_step_identity"),
                "flow-style step": (
                    workflow.replace(verify_step, "      - {name: Verify, run: true}\n", 1),
                    "studio_step_identity",
                ),
                "duplicate step name": (
                    workflow.replace(
                        "      - name: Run complete Studio verification\n",
                        "      - name: Install exact Studio dependencies\n",
                        1,
                    ),
                    "studio_step_identity",
                ),
                "unexpected named step": (
                    workflow.replace(
                        linux_owner,
                        "      - name: Unexpected action\n        run: true\n" + linux_owner,
                        1,
                    ),
                    "studio_step_order",
                ),
                "attacker checkout action": (
                    workflow[: workflow.index("  studio-m6-readiness:\n")]
                    + workflow[workflow.index("  studio-m6-readiness:\n") :].replace(
                        checkout_identity, f"attacker/checkout@{'a' * 40}", 1
                    ),
                    "studio_step_kind",
                ),
                "run step converted to action": (
                    workflow.replace(
                        npm_step,
                        "      - name: Install exact npm toolchain\n"
                        f"        uses: attacker/action@{'b' * 40}\n",
                        1,
                    ),
                    "studio_step_kind",
                ),
                "duplicate Studio job after boundary": (
                    workflow + "\n  studio-m6-readiness:\n    steps:\n",
                    "studio_job_boundary",
                ),
            }
        )
        for name in ("Install exact Studio dependencies", "Run complete Studio verification"):
            mutations[f"{name} before owners"] = (move_before_owners(name), "studio_step_order")
        for label, (mutation, expected_error) in mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(mutation, workflow)
                errors = _studio_environment_contract_errors(mutation)
                if expected_error == "studio_job_sha256":
                    self.assertEqual(errors, ("studio_job_sha256",))
                else:
                    self.assertIn(expected_error, errors)

    def test_studio_matrix_pins_exact_runners_languages_and_actions(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        studio = _studio_job(workflow)
        self.assertIn("runs-on: ${{ matrix.os }}", studio)
        self.assertIn("          - ubuntu-24.04\n          - windows-2022", studio)
        self.assertIn('          - "3.11"\n          - "3.12"', studio)
        self.assertIn(f"uses: actions/setup-node@{SETUP_NODE_SHA}", studio)
        self.assertIn('node-version: "24.14.1"', studio)
        self.assertIn('test "$(node --version)" = "v24.14.1"', studio)
        self.assertIn('test "$(npm --version)" = "11.13.0"', studio)
        self.assertIn("cache-dependency-path: apps/studio/package-lock.json", studio)

        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s]+)", workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 10)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")

    def test_studio_bootstraps_exact_npm_once_at_repository_root(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        studio = _studio_job(workflow)
        step_name = "      - name: Install exact npm toolchain\n"
        exact_step = f"{step_name}        run: {NPM_BOOTSTRAP_COMMAND}\n"

        self.assertEqual(workflow.count(NPM_BOOTSTRAP_COMMAND), 1)
        self.assertEqual(studio.count(step_name), 1)
        step_start = studio.index(step_name)
        step_end = studio.index("      - name:", step_start + len(step_name))
        self.assertEqual(studio[step_start:step_end], exact_step)

        setup_node = studio.index(f"uses: actions/setup-node@{SETUP_NODE_SHA}")
        npm_bootstrap = studio.index(exact_step)
        npm_assertion = studio.index("      - name: Verify pinned Node and npm toolchain")
        self.assertLess(setup_node, npm_bootstrap)
        self.assertLess(npm_bootstrap, npm_assertion)

    def test_studio_npm_bootstrap_correlates_manifest_and_lock_pins(self) -> None:
        studio = _studio_job(WORKFLOW.read_text(encoding="utf-8"))
        package = json.loads(STUDIO_PACKAGE.read_text(encoding="utf-8"))
        package_lock = json.loads(STUDIO_PACKAGE_LOCK.read_text(encoding="utf-8"))
        package_engines = package["engines"]
        lock_engines = package_lock["packages"][""]["engines"]

        self.assertEqual(package_engines, lock_engines)
        self.assertEqual(package_engines, {"node": "24.14.1", "npm": "11.13.0"})
        self.assertEqual(package["packageManager"], f"npm@{package_engines['npm']}")
        self.assertEqual(
            NPM_BOOTSTRAP_COMMAND,
            (
                "npm install --global --ignore-scripts --no-audit --no-fund "
                f"npm@{package_engines['npm']}"
            ),
        )
        verify_step_name = "      - name: Verify pinned Node and npm toolchain\n"
        verify_start = studio.index(verify_step_name)
        verify_end = studio.index("      - name:", verify_start + len(verify_step_name))
        verify_step = studio[verify_start:verify_end]
        self.assertIn("        shell: bash\n", verify_step)
        self.assertEqual(
            verify_step.count(f'test "$(node --version)" = "v{package_engines["node"]}"'),
            1,
        )
        self.assertEqual(
            verify_step.count(f'test "$(npm --version)" = "{package_engines["npm"]}"'),
            1,
        )
        self.assertEqual(verify_step.count('npm_root="$(npm root --global)"'), 1)
        self.assertEqual(
            verify_step.count('"${npm_root}/npm/package.json"'),
            1,
        )
        self.assertEqual(
            verify_step.count(
                f'if (manifest.version !== "{package_engines["npm"]}") process.exit(1);'
            ),
            1,
        )

    def test_all_rows_bind_python_and_run_complete_studio_and_runtime_gates(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        studio = _studio_job(workflow)
        actual_studio_sha256 = _studio_job_sha256(studio)
        self.assertEqual(
            actual_studio_sha256,
            EXPECTED_STUDIO_JOB_SHA256,
            (
                "Studio job changed; intentionally review and update its sealed SHA-256: "
                f"expected={EXPECTED_STUDIO_JOB_SHA256} actual={actual_studio_sha256}"
            ),
        )
        self.assertEqual(_studio_environment_contract_errors(workflow), ())
        self.assertEqual(studio.count("run: npm ci"), 1)
        self.assertEqual(studio.count("run: npm run verify"), 1)
        setup_python_name = "Set up Python"
        linux_name, windows_name = STUDIO_PYTHON_OWNER_STEPS
        steps = _workflow_steps(studio)
        for step_name in (setup_python_name, linux_name, windows_name):
            self.assertEqual(
                sum(name == step_name for name, _step in steps),
                1,
            )

        setup_python_step = _workflow_step(studio, setup_python_name)
        self.assertRegex(
            setup_python_step,
            r"(?m)^\s*uses:\s*actions/setup-python@[0-9a-f]{40}\s*(?:#.*)?$",
        )
        self.assertRegex(
            setup_python_step,
            r"(?m)^\s*python-version:\s*\$\{\{\s*matrix\.python-version\s*\}\}\s*$",
        )
        setup_python_position = studio.index(f"      - name: {setup_python_name}\n")
        self.assertLess(setup_python_position, studio.index(f"      - name: {linux_name}\n"))
        self.assertLess(setup_python_position, studio.index(f"      - name: {windows_name}\n"))

        linux_step = _workflow_step(studio, linux_name)
        self.assertRegex(
            linux_step,
            r"(?m)^\s*if:\s*runner\.os\s*==\s*(['\"])Linux\1\s*$",
        )
        self.assertRegex(linux_step, r"(?m)^\s*shell:\s*bash\s*$")
        self.assertEqual(
            len(re.findall(r"(?m)^\s*python_path=", linux_step)),
            1,
        )
        self.assertRegex(
            linux_step,
            (
                r'(?m)^\s*python_path=(?:"\$(?:\{pythonLocation\}|pythonLocation)'
                r'/bin/python"|\$(?:\{pythonLocation\}|pythonLocation)/bin/python)\s*$'
            ),
        )
        linux_printf_commands = list(re.finditer(r"(?m)^[ \t]*printf\b", linux_step))
        self.assertEqual(len(linux_printf_commands), 1)
        self.assertEqual(
            len(re.findall(r"\$(?:GITHUB_ENV\b|\{GITHUB_ENV\})", linux_step)),
            1,
        )
        linux_printf = linux_printf_commands[0]
        linux_command_lines: list[str] = []
        for line in linux_step[linux_printf.start() :].splitlines():
            linux_command_lines.append(line)
            if not line.rstrip().endswith("\\"):
                break
        linux_tokens = shlex.split("\n".join(linux_command_lines).replace("\\\n", " "))
        self.assertEqual(
            linux_tokens[:2],
            [
                "printf",
                "".join(f"{name}=%s\\n" for name in STUDIO_PYTHON_ENVIRONMENTS),
            ],
        )
        self.assertEqual(len(linux_tokens), 7)
        for value in linux_tokens[2:5]:
            self.assertIn(value, ("$python_path", "${python_path}"))
        self.assertEqual(linux_tokens[5], ">>")
        self.assertIn(linux_tokens[6], ("$GITHUB_ENV", "${GITHUB_ENV}"))

        windows_step = _workflow_step(studio, windows_name)
        self.assertRegex(
            windows_step,
            r"(?m)^\s*if:\s*runner\.os\s*==\s*(['\"])Windows\1\s*$",
        )
        self.assertRegex(windows_step, r"(?m)^\s*shell:\s*pwsh\s*$")
        self.assertEqual(
            len(re.findall(r"(?mi)^\s*\$pythonPath\s*=", windows_step)),
            1,
        )
        self.assertRegex(
            windows_step,
            (
                r"(?mi)^\s*\$pythonPath\s*=\s*Join-Path\s+"
                r"\$env:pythonLocation\s+(?:\"python\.exe\"|'python\.exe')\s*$"
            ),
        )
        self.assertEqual(
            len(re.findall(r"(?i)\$env:GITHUB_ENV\b", windows_step)),
            1,
        )
        self.assertEqual(len(re.findall(r"(?i)\bOut-File\b", windows_step)), 1)
        windows_exports = list(
            re.finditer(
                r"(?ms)@\(\s*(?P<entries>.*?)\s*\)\s*\|\s*"
                r"(?P<out_file>Out-File[^\n]*)",
                windows_step,
            )
        )
        self.assertEqual(len(windows_exports), 1)
        windows_export = windows_exports[0]
        entries = re.findall(r'"([^"\r\n]*)"', windows_export["entries"])
        self.assertRegex(
            re.sub(r'"[^"\r\n]*"', "", windows_export["entries"]),
            r"\A[\s,]*\Z",
        )
        bindings: list[str] = []
        for entry in entries:
            binding = re.fullmatch(
                rf"({'|'.join(STUDIO_PYTHON_ENVIRONMENTS)})="
                r"\$(?:pythonPath|\{pythonPath\})",
                entry,
            )
            self.assertIsNotNone(binding)
            bindings.append(binding.group(1))
        self.assertCountEqual(bindings, STUDIO_PYTHON_ENVIRONMENTS)
        self.assertRegex(
            windows_export["out_file"],
            r"(?:^|\s)-FilePath\s+\$env:GITHUB_ENV(?:\s|$)",
        )
        self.assertRegex(windows_export["out_file"], r"(?:^|\s)-Append(?:\s|$)")
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
                "tests.test_m4_world_lifecycle.BumpWorldVersionTests."
                "test_windows_lock_rename_denial_is_fail_closed",
                "tests.test_renderpack_resources.DirectMediaValidationTests."
                "test_native_windows_path_and_descriptor_file_states_agree",
                "tests.test_renderpack_resources.RenderPackResourceBoundaryTests."
                "test_windows_created_private_acl_passes_native_validation",
                "tests.test_runtime_io.AtomicRuntimeWriterTests."
                "test_windows_parent_rename_denial_is_fail_closed",
                "tests.test_studio_authoring.StudioAuthoringTests."
                "test_overview_exercises_native_windows_pinned_directory_path",
                "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
                "test_native_windows_handles_block_target_swap_through_final_read",
                "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
                "test_native_windows_owned_temp_revalidates_blocks_rename_and_cleans",
                "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
                "test_native_windows_owned_temp_publishes_and_reopens",
                "tests.test_studio_runtime_inputs.StudioRuntimeInputsTests."
                "test_native_windows_owned_temp_preserves_mismatched_winner",
                "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
                "test_native_windows_backend_assembles_and_zips_windows_target",
                "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
                "test_native_windows_pinned_reads_reject_hardlinks_and_retained_swaps",
                "tests.test_studio_runtime_assembly.StudioRuntimeAssemblyTest."
                "test_native_windows_retained_handles_block_after_write_parent_swaps",
                "tests.test_m6_game_consumer.M6GameConsumerTests."
                "test_native_windows_generation_stage_handle_blocks_swap",
            ),
        )
        self.assertIn("--run-windows-native-python", studio)
        self.assertIn(f'--testNamePattern "{WINDOWS_NATIVE_SHELL_TEST}"', studio)
        self.assertIn("--assert-vitest-passed $report", studio)
        self.assertIn("if: runner.os == 'Windows'", studio)

    @unittest.skipIf(os.name == "nt", "isolated import probe is exercised off Windows")
    def test_windows_native_loader_resolves_source_tree_without_install(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                str(Path(__file__).resolve()),
                "--run-windows-native-python",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout + completed.stderr

        self.assertEqual(1, completed.returncode, output)
        self.assertNotIn("_FailedTest", output)
        self.assertNotIn("ModuleNotFoundError", output)
        self.assertNotIn("FAILED", output)
        self.assertIn("Ran 13 tests", output)
        self.assertIn("OK (skipped=13)", output)

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
