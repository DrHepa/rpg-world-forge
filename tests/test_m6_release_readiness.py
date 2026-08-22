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
NPM_BOOTSTRAP_COMMAND = "npm install --global --ignore-scripts --no-audit --no-fund npm@11.13.0"
EXPECTED_WORKFLOW_JOBS = (
    "ubuntu-py312-core",
    "ubuntu-py311-compat-native",
    "windows-py312-release",
    "windows-py311-compat-native",
    "ci-required",
)
PY312_STUDIO_JOB_IDS = ("ubuntu-py312-core", "windows-py312-release")
PY312_STUDIO_ENVIRONMENT = (
    "    env:\n"
    '      CSC_IDENTITY_AUTO_DISCOVERY: "false"\n'
    '      PYTHONDONTWRITEBYTECODE: "1"\n'
    '      PYTHONNOUSERSITE: "1"\n'
    '      PYTHONUTF8: "1"\n'
)
STUDIO_PYTHON_ENVIRONMENTS = (
    "PYTHON",
    "WORLD_FORGE_STUDIO_BUILD_PYTHON",
    "WORLD_FORGE_STUDIO_TEST_PYTHON",
)
STUDIO_PYTHON_OWNER_STEPS = (
    "Bind exact Linux Python for Studio subprocesses",
    "Bind exact Windows Python for Studio subprocesses",
)
WINDOWS_NATIVE_SHELL_STEP = "Exercise native Windows shell handle contract without skips"
WINDOWS_NATIVE_PYTHON_READ = "& $env:WORLD_FORGE_STUDIO_BUILD_PYTHON"
WINDOWS_NATIVE_PYTHON_TESTS = (
    "tests.test_m6_composed_bundle.DirectoryPublicationPortabilityTests."
    "test_native_windows_directory_publication_and_collision",
    "tests.test_bundle_publication.BundlePublicationTests."
    "test_native_windows_bundle_verifier_reads_while_seal_is_active",
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
    "tests.test_multigenre_game_package.GenericGamePackageTests."
    "test_windows_package_stage_denies_write_and_delete_sharing",
    "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
    "test_windows_native_runtime_tree_retains_root_and_file_bindings",
    "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
    "test_windows_native_runtime_tree_rejects_hardlinks_and_reparse_points",
    "tests.test_studio_shell_package_snapshot.StudioShellPackageSnapshotTests."
    "test_windows_snapshot_root_cleanup_deletes_native_empty_directory_by_handle",
)
WINDOWS_NATIVE_SHELL_TEST = "retains the native Windows package root against parent replacement"


def _workflow_job_ids(workflow: str) -> tuple[str, ...]:
    jobs = re.search(r"(?m)^jobs:\s*$", workflow)
    source = workflow[jobs.end() :] if jobs is not None else workflow
    return tuple(
        match.group("job")
        for match in re.finditer(r"(?m)^  (?P<job>[A-Za-z0-9_-]+):[ \t]*\r?\n", source)
    )


def _studio_job(workflow: str) -> str:
    """Return the combined Python 3.12 release jobs that own Studio/M6 gates."""
    return "\n".join(_workflow_job(workflow, job_id) for job_id in PY312_STUDIO_JOB_IDS)


def _linux_studio_job(workflow: str) -> str:
    return _workflow_job(workflow, "ubuntu-py312-core")


def _windows_studio_job(workflow: str) -> str:
    return _workflow_job(workflow, "windows-py312-release")


def _workflow_job(workflow: str, job_id: str) -> str:
    matches = list(re.finditer(rf"(?m)^  {re.escape(job_id)}:[ \t]*\r?\n", workflow))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {job_id!r} job")
    start = matches[0].end()
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:[ \t]*\r?\n", workflow[start:])
    end = start + next_job.start() if next_job is not None else len(workflow)
    return workflow[start:end]


def _npm_bootstrap_contract_errors(workflow: str) -> tuple[str, ...]:
    errors: list[str] = []
    jobs = {
        "ubuntu-py312-core": ("Set up Node", "Install audited toolchain and Forge", None),
        "windows-py312-release": ("Set up Node", "Install audited toolchains and Forge", None),
        "ci-required": (
            "Set up pinned Studio Node",
            "Install exact Studio npm audit toolchain",
            "Verify pinned Studio dependency audit toolchain",
        ),
    }
    for job_id, (setup_name, install_name, verify_name) in jobs.items():
        try:
            job = _workflow_job(workflow, job_id)
            steps = _workflow_steps(job)
            step_by_name = {name: step for name, step in steps if name is not None}
            setup_step = step_by_name[setup_name]
            install_step = step_by_name[install_name]
            verify_step = step_by_name[verify_name] if verify_name is not None else None
        except (KeyError, ValueError):
            errors.append(f"{job_id}:npm_bootstrap_step")
            continue
        install_steps = [step for name, step in steps if name == install_name]
        if len(install_steps) != 1:
            errors.append(f"{job_id}:npm_bootstrap_step")
            continue
        if install_step.count(NPM_BOOTSTRAP_COMMAND) != 1:
            errors.append(f"{job_id}:npm_bootstrap_command")
        if job_id == "ci-required":
            exact_run = f"        run: {NPM_BOOTSTRAP_COMMAND}\n"
        else:
            exact_run = f"          {NPM_BOOTSTRAP_COMMAND}\n"
        if install_step.count(exact_run) != 1 or "        working-directory:" in install_step:
            errors.append(f"{job_id}:npm_bootstrap_command")
        npm_versions = re.findall(r"npm install --global[^\r\n]+npm@([^\s]+)", install_step)
        if any(version != "11.13.0" for version in npm_versions):
            errors.append(f"{job_id}:npm_bootstrap_floating")
        if verify_step is not None and not (
            job.index(setup_step) < job.index(install_step) < job.index(verify_step)
        ):
            errors.append(f"{job_id}:npm_bootstrap_order")
        if verify_step is None and not (job.index(setup_step) < job.index(install_step)):
            errors.append(f"{job_id}:npm_bootstrap_order")
    return tuple(errors)


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
    if _workflow_job_ids(workflow) != EXPECTED_WORKFLOW_JOBS:
        errors.append("workflow_job_identity")
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
        linux_studio = _linux_studio_job(workflow)
        windows_studio = _windows_studio_job(workflow)
    except ValueError:
        return (*errors, "studio_job_boundary")
    for job_id, job in (
        ("ubuntu-py312-core", linux_studio),
        ("windows-py312-release", windows_studio),
    ):
        steps_marker = "    steps:\n"
        if job.count(steps_marker) != 1:
            return (*errors, f"{job_id}:studio_steps")
        preamble, _step_source = job.split(steps_marker, 1)
        job_fields = re.findall(r"(?m)^ {4}(\S.*)$", job)
        if job_fields.count("env:") != 1 or any(
            not field.startswith(("name:", "runs-on:", "timeout-minutes:", "env:", "steps:"))
            for field in job_fields
        ):
            errors.append(f"{job_id}:studio_environment")
        if PY312_STUDIO_ENVIRONMENT not in preamble:
            errors.append(f"{job_id}:studio_environment")
        elif re.search(
            r"(?i)(?<![A-Z0-9_])env(?![A-Z0-9_])",
            preamble[: preamble.index(PY312_STUDIO_ENVIRONMENT)],
        ):
            errors.append(f"{job_id}:duplicate_studio_environment")

    linux_steps = [(name or "").strip() for name, _step in _workflow_steps(linux_studio)]
    expected_linux_step_names = (
        "Check out source",
        "Set up Python",
        "Set up Node",
        "Install audited toolchain and Forge",
        STUDIO_PYTHON_OWNER_STEPS[0],
        "Install exact Studio dependencies",
        "Run complete Studio verification",
        "Lint and formatting",
        "Compile Python",
        "Audit source contracts",
        "Audit runtime AI boundary",
        "Validate foundation release profile",
        "Analyze neutral narrative fixture",
        "Audit phase skills",
        "Verify neutral standalone and reproducible releases",
        "Install Linux virtual display",
        "Exercise graphical raylib runtime under Xvfb",
        "Exercise bounded pyray GLB animation proof under Xvfb",
        "Download, attest, and install the locked raylib wheel",
        "Verify exact generic release lineage with native raylib",
        "Run bounded full unittest suite once",
        "Upload exact native evidence row",
        "Upload parent-attested native smoke diagnostics",
    )
    windows_steps = [(name or "").strip() for name, _step in _workflow_steps(windows_studio)]
    expected_windows_step_names = (
        "Check out source",
        "Set up Python",
        "Set up Node",
        "Install audited toolchains and Forge",
        STUDIO_PYTHON_OWNER_STEPS[1],
        "Initialize strict external Windows native work root",
        "Install exact Studio dependencies",
        "Run complete Studio verification",
        "Run Windows fail-fast publication contract gate",
        "Run native Windows world-project migration gate",
        "Exercise native Windows Python handle contracts without skips",
        WINDOWS_NATIVE_SHELL_STEP,
        "Build and reverify unpacked Windows shell",
        "Exercise raylib CPU image encode and decode on Windows",
        "Verify pyray 3D ABI without claiming native graphics",
        "Download, attest, and install the locked raylib wheel",
        "Verify exact generic release lineage with native raylib",
        "Upload exact native evidence row",
        "Upload parent-attested native smoke diagnostics",
        "Cleanup strict external Windows native work root",
    )
    for job_id, step_names, expected_step_names in (
        ("ubuntu-py312-core", linux_steps, expected_linux_step_names),
        ("windows-py312-release", windows_steps, expected_windows_step_names),
    ):
        if (
            not step_names
            or any(name in {"", '""', "''"} for name in step_names)
            or len(step_names) != len(set(step_names))
        ):
            errors.append(f"{job_id}:studio_step_identity")
        elif tuple(step_names) != expected_step_names:
            errors.append(f"{job_id}:studio_step_order")
    expected_actions = {
        "Check out source": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "Set up Python": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "Set up Node": f"actions/setup-node@{SETUP_NODE_SHA}",
        "Upload exact native evidence row": (
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
        ),
        "Upload parent-attested native smoke diagnostics": (
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
        ),
    }
    allowed_step_fields = (
        "uses:",
        "with:",
        "if:",
        "id:",
        "shell:",
        "run:",
        "working-directory:",
    )
    for job_id, job in (
        ("ubuntu-py312-core", linux_studio),
        ("windows-py312-release", windows_studio),
    ):
        for name, step in _workflow_steps(job):
            fields = tuple(
                line[8:]
                for line in step.splitlines()
                if line.startswith("        ") and not line.startswith("         ")
            )
            if any(not field.startswith(allowed_step_fields) for field in fields):
                errors.append(f"{job_id}:step_environment")
                break
            uses = tuple(
                field.removeprefix("uses:").split("#", 1)[0].strip()
                for field in fields
                if field.startswith("uses:")
            )
            run_count = sum(field.startswith("run:") for field in fields)
            expected_uses = (expected_actions[name],) if name in expected_actions else ()
            if uses != expected_uses or run_count != (0 if expected_uses else 1):
                errors.append(f"{job_id}:studio_step_kind")
                break

    remainder = linux_studio + "\n" + windows_studio
    try:
        linux_owner_step = _workflow_step(linux_studio, STUDIO_PYTHON_OWNER_STEPS[0])
        windows_owner_step = _workflow_step(windows_studio, STUDIO_PYTHON_OWNER_STEPS[1])
        remainder = remainder.replace(linux_owner_step, "", 1)
        remainder = remainder.replace(windows_owner_step, "", 1)
        consumer_step = _workflow_step(windows_studio, WINDOWS_NATIVE_SHELL_STEP)
    except ValueError:
        return (*errors, "studio_python_step")

    if consumer_step.count(WINDOWS_NATIVE_PYTHON_READ) != 1:
        errors.append("windows_python_consumer")
    remainder = remainder.replace(WINDOWS_NATIVE_PYTHON_READ, "", 1)
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
    return 0 if _windows_native_result_is_green(result) else 1


def _windows_native_result_is_green(result: object) -> bool:
    return bool(
        result.wasSuccessful()
        and result.testsRun == len(WINDOWS_NATIVE_PYTHON_TESTS)
        and not result.skipped
        and not result.failures
        and not result.errors
        and not result.expectedFailures
        and not result.unexpectedSuccesses
    )


class M6ReleaseReadinessContractTests(unittest.TestCase):
    def test_studio_environment_contract_rejects_structural_mutations(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(_workflow_job_ids(workflow), EXPECTED_WORKFLOW_JOBS)
        self.assertEqual(workflow.count(PY312_STUDIO_ENVIRONMENT), 2)
        verify_step = (
            "      - name: Run complete Studio verification\n"
            "        working-directory: apps/studio\n"
            "        run: npm run verify\n"
        )
        self.assertEqual(workflow.count(verify_step), 2)
        linux_job = _linux_studio_job(workflow)
        windows_job = _windows_studio_job(workflow)
        linux_owner_step = _workflow_step(linux_job, STUDIO_PYTHON_OWNER_STEPS[0])
        checkout = _workflow_step(linux_job, "Check out source")
        checkout_identity = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        self.assertEqual(checkout.count(checkout_identity), 1)
        npm_step = _workflow_step(linux_job, "Install audited toolchain and Forge")
        native_diagnostics_step = _workflow_step(
            linux_job, "Upload parent-attested native smoke diagnostics"
        )

        def replace_first_verify(command: str) -> str:
            return workflow.replace(
                verify_step,
                verify_step.replace("run: npm run verify", f"run: {command}"),
                1,
            )

        def move_before_linux_owner(name: str) -> str:
            step = _workflow_step(linux_job, name)
            return workflow.replace(step, "", 1).replace(
                linux_owner_step, step + linux_owner_step, 1
            )

        mutations = {
            "flow env": (
                workflow.replace(
                    PY312_STUDIO_ENVIRONMENT, '    env: {CSC_IDENTITY_AUTO_DISCOVERY: "false"}\n', 1
                ),
                "ubuntu-py312-core:studio_environment",
            ),
            "step alias": (
                workflow.replace(
                    f"      - name: {STUDIO_PYTHON_OWNER_STEPS[0]}\n        shell: bash\n",
                    (
                        f"      - name: {STUDIO_PYTHON_OWNER_STEPS[0]}\n"
                        "        env: *studio_environment\n"
                        "        shell: bash\n"
                    ),
                    1,
                ),
                "ubuntu-py312-core:step_environment",
            ),
            "github env later": (
                replace_first_verify("printf 'PYTHON=x' >> \"$GITHUB_ENV\""),
                "protected_python_name:PYTHON",
            ),
            "protected python later": (
                replace_first_verify("PYTHON=python npm run verify"),
                "protected_python_name:PYTHON",
            ),
            "duplicate workflow job": (
                workflow + "\n  ubuntu-py312-core:\n    steps:\n",
                "workflow_job_identity",
            ),
            "flow-style step": (
                workflow.replace(verify_step, "      - {name: Verify, run: true}\n", 1),
                "ubuntu-py312-core:studio_step_identity",
            ),
            "duplicate step name": (
                workflow.replace(
                    "      - name: Run complete Studio verification\n",
                    "      - name: Install exact Studio dependencies\n",
                    1,
                ),
                "ubuntu-py312-core:studio_step_identity",
            ),
            "unexpected named step": (
                workflow.replace(
                    linux_owner_step,
                    "      - name: Unexpected action\n        run: true\n" + linux_owner_step,
                    1,
                ),
                "ubuntu-py312-core:studio_step_order",
            ),
            "attacker checkout action": (
                workflow.replace(checkout_identity, f"attacker/checkout@{'a' * 40}", 1),
                "ubuntu-py312-core:studio_step_kind",
            ),
            "run step converted to action": (
                workflow.replace(
                    npm_step,
                    "      - name: Install audited toolchain and Forge\n"
                    f"        uses: attacker/action@{'b' * 40}\n",
                    1,
                ),
                "ubuntu-py312-core:studio_step_kind",
            ),
            "attacker native diagnostics action": (
                workflow.replace(
                    native_diagnostics_step,
                    native_diagnostics_step.replace(
                        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
                        f"attacker/upload-artifact@{'c' * 40}",
                    ),
                    1,
                ),
                "ubuntu-py312-core:studio_step_kind",
            ),
            "complete verification before owner": (
                move_before_linux_owner("Run complete Studio verification"),
                "ubuntu-py312-core:studio_step_order",
            ),
        }
        self.assertIn(WINDOWS_NATIVE_PYTHON_READ, windows_job)
        for label, (mutation, expected_error) in mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(mutation, workflow)
                self.assertIn(expected_error, _studio_environment_contract_errors(mutation))

    def test_studio_matrix_pins_exact_runners_languages_and_actions(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(_workflow_job_ids(workflow), EXPECTED_WORKFLOW_JOBS)
        linux_job = _linux_studio_job(workflow)
        windows_job = _windows_studio_job(workflow)
        compat_linux = _workflow_job(workflow, "ubuntu-py311-compat-native")
        compat_windows = _workflow_job(workflow, "windows-py311-compat-native")
        self.assertIn("runs-on: ubuntu-24.04", linux_job)
        self.assertIn("runs-on: windows-2022", windows_job)
        self.assertIn('python-version: "3.12"', linux_job)
        self.assertIn('python-version: "3.12"', windows_job)
        self.assertIn('python-version: "3.11"', compat_linux)
        self.assertIn('python-version: "3.11"', compat_windows)
        self.assertIn(f"uses: actions/setup-node@{SETUP_NODE_SHA}", linux_job)
        self.assertIn(f"uses: actions/setup-node@{SETUP_NODE_SHA}", windows_job)
        self.assertIn('node-version: "24.14.1"', linux_job)
        self.assertIn('node-version: "24.14.1"', windows_job)
        self.assertIn("cache-dependency-path: apps/studio/package-lock.json", linux_job)
        self.assertIn("cache-dependency-path: apps/studio/package-lock.json", windows_job)

        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s]+)", workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 10)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")

    def test_isolated_jobs_bootstrap_exact_npm_once_at_repository_root(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(_npm_bootstrap_contract_errors(workflow), ())
        self.assertEqual(
            workflow.count(NPM_BOOTSTRAP_COMMAND),
            3,
            "each Studio/CI npm owner job must bootstrap the exact npm once",
        )

    def test_npm_bootstrap_contract_rejects_per_job_mutations(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        linux_command = f"          {NPM_BOOTSTRAP_COMMAND}\n"
        windows_command = f"          {NPM_BOOTSTRAP_COMMAND}\n"
        dependency_command = (
            "      - name: Install exact Studio npm audit toolchain\n"
            "        run: " + NPM_BOOTSTRAP_COMMAND + "\n"
        )

        mutations = {
            "ubuntu-py312-core:npm_bootstrap_command": workflow.replace(linux_command, "", 1),
            "ci-required:npm_bootstrap_step": workflow.replace(
                dependency_command, dependency_command + dependency_command, 1
            ),
            "ci-required:npm_bootstrap_floating": workflow.replace(
                dependency_command,
                dependency_command.replace("npm@11.13.0", "npm@latest"),
                1,
            ),
            "windows-py312-release:npm_bootstrap_command": workflow.replace(windows_command, "", 2),
        }
        for expected_error, mutated in mutations.items():
            with self.subTest(expected_error=expected_error):
                self.assertIn(expected_error, _npm_bootstrap_contract_errors(mutated))

    def test_studio_npm_bootstrap_correlates_manifest_and_lock_pins(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        linux_job = _linux_studio_job(workflow)
        windows_job = _windows_studio_job(workflow)
        ci_required = _workflow_job(workflow, "ci-required")
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
        for job in (linux_job, windows_job, ci_required):
            with self.subTest(job_hash=hashlib.sha256(job.encode()).hexdigest()):
                self.assertIn(NPM_BOOTSTRAP_COMMAND, job)
        verify_step = _workflow_step(ci_required, "Verify pinned Studio dependency audit toolchain")
        self.assertIn("        shell: bash\n", verify_step)
        self.assertEqual(
            verify_step.count(f'test "$(node --version)" = "v{package_engines["node"]}"'),
            1,
        )
        self.assertEqual(
            verify_step.count(f'test "$(npm --version)" = "{package_engines["npm"]}"'),
            1,
        )

    def test_all_rows_bind_python_and_run_complete_studio_and_runtime_gates(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        linux_job = _linux_studio_job(workflow)
        windows_job = _windows_studio_job(workflow)
        self.assertEqual(_studio_environment_contract_errors(workflow), ())
        self.assertEqual((linux_job + windows_job).count("run: npm ci"), 2)
        self.assertEqual((linux_job + windows_job).count("run: npm run verify"), 2)
        setup_python_name = "Set up Python"
        linux_name, windows_name = STUDIO_PYTHON_OWNER_STEPS

        for job, owner_name, python_binary in (
            (linux_job, linux_name, r"(?m)^\s*python-version:\s*\"3\.12\"\s*$"),
            (windows_job, windows_name, r"(?m)^\s*python-version:\s*\"3\.12\"\s*$"),
        ):
            steps = _workflow_steps(job)
            self.assertEqual(sum(name == setup_python_name for name, _step in steps), 1)
            self.assertEqual(sum(name == owner_name for name, _step in steps), 1)
            setup_python_step = _workflow_step(job, setup_python_name)
            self.assertRegex(
                setup_python_step,
                r"(?m)^\s*uses:\s*actions/setup-python@[0-9a-f]{40}\s*(?:#.*)?$",
            )
            self.assertRegex(setup_python_step, python_binary)
            self.assertLess(
                job.index(f"      - name: {setup_python_name}\n"),
                job.index(f"      - name: {owner_name}\n"),
            )

        linux_step = _workflow_step(linux_job, linux_name)
        self.assertRegex(linux_step, r"(?m)^\s*shell:\s*bash\s*$")
        self.assertEqual(len(re.findall(r"(?m)^\s*python_path=", linux_step)), 1)
        self.assertRegex(
            linux_step,
            (
                r'(?m)^\s*python_path=(?:"\$(?:\{pythonLocation\}|pythonLocation)'
                r'/bin/python"|\$(?:\{pythonLocation\}|pythonLocation)/bin/python)\s*$'
            ),
        )
        linux_printf_commands = list(re.finditer(r"(?m)^[ \t]*printf\b", linux_step))
        self.assertEqual(len(linux_printf_commands), 1)
        self.assertEqual(len(re.findall(r"\$(?:GITHUB_ENV\b|\{GITHUB_ENV\})", linux_step)), 1)
        linux_printf = linux_printf_commands[0]
        linux_command_lines: list[str] = []
        for line in linux_step[linux_printf.start() :].splitlines():
            linux_command_lines.append(line)
            if not line.rstrip().endswith("\\"):
                break
        linux_tokens = shlex.split("\n".join(linux_command_lines).replace("\\\n", " "))
        self.assertEqual(
            linux_tokens[:2],
            ["printf", "".join(f"{name}=%s\\n" for name in STUDIO_PYTHON_ENVIRONMENTS)],
        )
        self.assertEqual(len(linux_tokens), 7)
        for value in linux_tokens[2:5]:
            self.assertIn(value, ("$python_path", "${python_path}"))
        self.assertEqual(linux_tokens[5], ">>")
        self.assertIn(linux_tokens[6], ("$GITHUB_ENV", "${GITHUB_ENV}"))

        windows_step = _workflow_step(windows_job, windows_name)
        self.assertRegex(windows_step, r"(?m)^\s*shell:\s*pwsh\s*$")
        self.assertEqual(len(re.findall(r"(?mi)^\s*\$pythonPath\s*=", windows_step)), 1)
        self.assertRegex(
            windows_step,
            (
                r"(?mi)^\s*\$pythonPath\s*=\s*Join-Path\s+"
                r"\$env:pythonLocation\s+(?:\"python\.exe\"|'python\.exe')\s*$"
            ),
        )
        self.assertEqual(len(re.findall(r"(?i)\$env:GITHUB_ENV\b", windows_step)), 1)
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
        bindings: list[str] = []
        for entry in entries:
            binding = re.fullmatch(
                rf"({'|'.join(STUDIO_PYTHON_ENVIRONMENTS)})=" r"\$(?:pythonPath|\{pythonPath\})",
                entry,
            )
            self.assertIsNotNone(binding)
            bindings.append(binding.group(1))
        self.assertCountEqual(bindings, STUDIO_PYTHON_ENVIRONMENTS)
        self.assertRegex(
            windows_export["out_file"], r"(?:^|\s)-FilePath\s+\$env:GITHUB_ENV(?:\s|$)"
        )
        self.assertRegex(windows_export["out_file"], r"(?:^|\s)-Append(?:\s|$)")
        self.assertIn("Verify neutral standalone and reproducible releases", linux_job)
        self.assertIn("Run Windows fail-fast publication contract gate", windows_job)
        self.assertIn("Run native Windows world-project migration gate", windows_job)
        self.assertNotIn("continue-on-error", linux_job + windows_job)

    def test_windows_rows_run_exact_native_handle_tests_and_reject_skips(self) -> None:
        studio = _windows_studio_job(WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(
            WINDOWS_NATIVE_PYTHON_TESTS,
            (
                "tests.test_m6_composed_bundle.DirectoryPublicationPortabilityTests."
                "test_native_windows_directory_publication_and_collision",
                "tests.test_bundle_publication.BundlePublicationTests."
                "test_native_windows_bundle_verifier_reads_while_seal_is_active",
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
                "tests.test_multigenre_game_package.GenericGamePackageTests."
                "test_windows_package_stage_denies_write_and_delete_sharing",
                "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
                "test_windows_native_runtime_tree_retains_root_and_file_bindings",
                "tests.test_multigenre_runtime_review.GenericRuntimeReviewTests."
                "test_windows_native_runtime_tree_rejects_hardlinks_and_reparse_points",
                "tests.test_studio_shell_package_snapshot.StudioShellPackageSnapshotTests."
                "test_windows_snapshot_root_cleanup_deletes_native_empty_directory_by_handle",
            ),
        )
        self.assertIn("--run-windows-native-python", studio)
        self.assertIn(f'--testNamePattern "{WINDOWS_NATIVE_SHELL_TEST}"', studio)
        self.assertIn("--assert-vitest-passed $report", studio)
        self.assertIn("runs-on: windows-2022", studio)

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
        self.assertIn("Ran 19 tests", output)
        self.assertIn("OK (skipped=19)", output)

    def test_python_quality_gates_cover_the_shell_handle_backend(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        path = "apps/studio/scripts/shell_package_snapshot.py"
        self.assertIn(f"ruff check src tests scripts {path}", workflow)
        self.assertIn(f"ruff format --check src tests scripts {path}", workflow)
        self.assertIn(f"src scripts tests {path}", workflow)

    def test_python_312_builds_host_shell_only_under_runner_temp_and_reverifies(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        linux_job = _linux_studio_job(workflow)
        windows_job = _windows_studio_job(workflow)
        self.assertIn('CSC_IDENTITY_AUTO_DISCOVERY: "false"', linux_job)
        self.assertIn('CSC_IDENTITY_AUTO_DISCOVERY: "false"', windows_job)
        self.assertIn('python-version: "3.12"', linux_job)
        self.assertIn('python-version: "3.12"', windows_job)
        self.assertIn("Verify neutral standalone and reproducible releases", linux_job)
        self.assertIn("Verify exact generic release lineage with native raylib", linux_job)
        self.assertIn(
            '$output = Join-Path $env:WORLD_FORGE_NATIVE_WORK_ROOT "rwf-studio-shell-win32-x64"',
            windows_job,
        )
        self.assertIn("npm run package:dir -- --output $output --target win32-x64", windows_job)
        self.assertIn("npm run package:verify -- --path $unpacked --target win32-x64", windows_job)
        self.assertNotIn('output="${RUNNER_TEMP}/rwf-studio-shell-linux-x64"', workflow)

    def test_studio_job_does_not_acquire_publish_sign_or_build_installers(self) -> None:
        studio = _studio_job(WORKFLOW.read_text(encoding="utf-8"))
        prohibited = (
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
                self.assertNotIn(command, studio)
        self.assertNotRegex(
            studio,
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
