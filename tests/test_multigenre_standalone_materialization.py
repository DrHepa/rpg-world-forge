from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worldforge.generic_runtime as generic_runtime
import worldforge.standalone_game as standalone_game_module
from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    PLATFORM_LOCK_PATH,
    RUNTIME_BUNDLE_ROOT,
    canonical_contract_bytes,
    canonical_contract_hash,
)
from gamepack_runtime.headless import (
    build_game_execution_script,
    serialize_game_execution_script,
)
from tests.test_multigenre_materialization_contracts import _fixture, _runtime_bundle
from worldforge.directory_publish import (
    DirectoryPublishError,
    RetainedStageWriter,
    create_retained_stage,
)
from worldforge.game_materialization_bundle import build_game_materialization_bundle
from worldforge.integrity import canonical_json_bytes
from worldforge.repository_boundary import repository_kind
from worldforge.runtime_implementation import load_runtime_implementation
from worldforge.runtime_platform_lock import load_runtime_platform_lock
from worldforge.standalone_game import (
    STANDALONE_GAME_FORMAT,
    STANDALONE_GAME_LOCK_FORMAT,
    STANDALONE_PLATFORM_FORMAT,
    StandaloneGameError,
    materialize_game,
    recover_standalone_game,
    require_standalone_materialization_source,
    rollback_standalone_game,
    validate_standalone_game_document,
    validate_standalone_game_lock_document,
    validate_standalone_platform_document,
    verify_standalone_game,
)
from worldforge.standalone_templates import STANDALONE_TEMPLATE_FILES

ROOT = Path(__file__).resolve().parents[1]


def _platform_locks() -> list[dict[str, object]]:
    return [
        load_runtime_platform_lock(path)
        for path in sorted(
            (ROOT / "examples/multigenre-contracts/runtime/platform-locks").glob("*.json")
        )
    ]


def _headless_scenarios(name: str) -> list[dict[str, object]]:
    if name == "abstract-puzzle":
        return [
            {
                "scenario_id": "restart_board",
                "actions": [{"action_id": "restart_board", "parameters": {}}],
            },
            {
                "scenario_id": "swap_tiles",
                "actions": [
                    {
                        "action_id": "swap_tiles",
                        "parameters": {"first_index": 0, "second_index": 1},
                    }
                ],
            },
        ]
    return [
        {
            "scenario_id": "choose_left",
            "actions": [{"action_id": "choose_left", "parameters": {}}],
        },
        {
            "scenario_id": "choose_right",
            "actions": [{"action_id": "choose_right", "parameters": {}}],
        },
    ]


def _runtime_document(bundle: object, relative: str) -> dict[str, object]:
    value = json.loads(bundle.read_bytes(relative))
    if type(value) is not dict:
        raise RuntimeError(f"{relative} is not an object")
    return value


@contextlib.contextmanager
def _ready_materialization(name: str, root: Path):
    with _runtime_bundle(name, root) as runtime:
        adapter_path = runtime.manifest["contracts"]["runtime_adapter"]["path"]
        script = build_game_execution_script(
            runtime.manifest,
            gamepack=_runtime_document(runtime, "contracts/gamepack.json"),
            composition=_runtime_document(runtime, "contracts/runtime-composition.json"),
            adapter=_runtime_document(runtime, adapter_path),
            runtime_snapshot=_runtime_document(runtime, "contracts/runtime-snapshot.json"),
            scenarios=_headless_scenarios(name),
        )
        (root / f"{name}-execution-script.json").write_bytes(
            serialize_game_execution_script(script)
        )
        materialization = build_game_materialization_bundle(
            root / f"{name}-materialization",
            runtime_bundle_root=runtime.root,
            platform_locks=_platform_locks(),
        )
        try:
            yield materialization
        finally:
            materialization.close()


class StandaloneMaterializationTests(unittest.TestCase):
    def test_standalone_verifier_scopes_nested_runtime_capture_to_retained_subroot(
        self,
    ) -> None:
        class _NestedStopped(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="wf-standalone-nested-stage-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                payload, manifest, lock, platform = standalone_game_module._build_payload(  # noqa: SLF001
                    source
                )
            stage = root / "stage"
            all_files = {
                GAME_MANIFEST_PATH: canonical_contract_bytes(manifest),
                GAME_LOCK_PATH: canonical_contract_bytes(lock),
                PLATFORM_LOCK_PATH: canonical_contract_bytes(platform),
                **payload,
            }
            observed: list[tuple[Path, object | None]] = []

            def stop_nested(root_arg: str | Path, **kwargs: object) -> object:
                observed.append(
                    (
                        Path(os.path.abspath(os.fspath(root_arg))),
                        kwargs.get("_stage_capability"),
                    )
                )
                raise _NestedStopped

            with create_retained_stage(stage) as writer:
                for relative, payload_bytes in all_files.items():
                    writer.write_file(relative, payload_bytes)
                writer.fsync()
                with (
                    mock.patch.object(
                        standalone_game_module,
                        "_verify_game_runtime_bundle_with_stage_capability",
                        side_effect=stop_nested,
                        create=True,
                    ),
                    self.assertRaises(_NestedStopped),
                ):
                    verify_standalone_game(
                        stage,
                        expected_content_hash=manifest["content_hash"],
                        _retained_stage_writer=writer,
                    )

            self.assertEqual(1, len(observed))
            nested_root, capability = observed[0]
            self.assertEqual(stage / RUNTIME_BUNDLE_ROOT, nested_root)
            self.assertIsInstance(
                capability,
                generic_runtime._RuntimeStageReadCapability,  # noqa: SLF001
            )
            assert isinstance(capability, generic_runtime._RuntimeStageReadCapability)  # noqa: SLF001
            self.assertEqual(nested_root, capability.root)
            share_mode = generic_runtime._WindowsRuntimeTreeApi._share_mode_for(  # noqa: SLF001
                capability
            )
            self.assertEqual(0x00000003, share_mode)
            self.assertEqual(0, share_mode & 0x00000004)

    def test_standalone_verifier_requires_active_outer_stage_writer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-stage-authority-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                payload, manifest, lock, platform = standalone_game_module._build_payload(  # noqa: SLF001
                    source
                )
            stage = root / "stage"
            all_files = {
                GAME_MANIFEST_PATH: canonical_contract_bytes(manifest),
                GAME_LOCK_PATH: canonical_contract_bytes(lock),
                PLATFORM_LOCK_PATH: canonical_contract_bytes(platform),
                **payload,
            }
            with create_retained_stage(stage) as writer:
                for relative, payload_bytes in all_files.items():
                    writer.write_file(relative, payload_bytes)
                writer.fsync()
                crossed = root / "crossed-stage"
                shutil.copytree(stage, crossed)

                forged = object.__new__(RetainedStageWriter)
                forged.stage = stage
                forged.require_binding = lambda: None  # type: ignore[method-assign]
                for candidate, root_arg in (
                    (forged, stage),
                    (object(), stage),
                    (writer, crossed),
                ):
                    with self.subTest(candidate=type(candidate).__name__, root=root_arg):
                        with self.assertRaisesRegex(
                            StandaloneGameError,
                            "standalone_game_stage_capability_invalid",
                        ):
                            verify_standalone_game(
                                root_arg,
                                _retained_stage_writer=candidate,  # type: ignore[arg-type]
                            )

            with self.assertRaisesRegex(
                StandaloneGameError,
                "standalone_game_stage_capability_invalid",
            ):
                verify_standalone_game(stage, _retained_stage_writer=writer)

    def test_standalone_nested_runtime_rechecks_writer_after_verification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-nested-mutation-") as temporary:
            root = Path(temporary)
            outer_stage = root / "stage"
            nested_root = outer_stage / RUNTIME_BUNDLE_ROOT
            mutation_checks = 0
            real_authority = RetainedStageWriter._require_active_binding  # noqa: SLF001

            def reject_post_nested_mutation(
                writer: object,
                *,
                expected_stage: Path,
            ) -> None:
                nonlocal mutation_checks
                mutation_checks += 1
                real_authority(writer, expected_stage=expected_stage)
                if mutation_checks == 2:
                    raise DirectoryPublishError("retained standalone stage changed")

            class VerifiedNested:
                manifest = {}
                files = {}

                def close(self) -> None:
                    pass

            with create_retained_stage(outer_stage) as writer:
                with (
                    mock.patch.object(
                        RetainedStageWriter,
                        "_require_active_binding",
                        side_effect=reject_post_nested_mutation,
                    ),
                    mock.patch.object(
                        standalone_game_module,
                        "_verify_game_runtime_bundle_with_stage_capability",
                        return_value=VerifiedNested(),
                    ) as nested_verify,
                    self.assertRaisesRegex(
                        StandaloneGameError,
                        "standalone_game_stage_capability_invalid",
                    ),
                ):
                    standalone_game_module._verify_nested_runtime_bundle_from_retained_standalone_stage(  # noqa: SLF001
                        nested_root,
                        expected_content_hash="0" * 64,
                        expected_outer_stage=outer_stage,
                        _retained_stage_writer=writer,
                    )

            nested_verify.assert_called_once()
            self.assertEqual(2, mutation_checks)

    def test_standalone_nested_runtime_rejects_wrong_retained_subroot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-nested-cross-root-") as temporary:
            root = Path(temporary)
            outer_stage = root / "stage"
            wrong_nested_root = outer_stage / "wrong-runtime-bundle"

            with create_retained_stage(outer_stage) as writer:
                with (
                    mock.patch.object(
                        standalone_game_module,
                        "_verify_game_runtime_bundle_with_stage_capability",
                        side_effect=AssertionError("wrong nested root reached runtime verifier"),
                    ),
                    self.assertRaisesRegex(
                        StandaloneGameError,
                        "standalone_game_stage_capability_invalid",
                    ),
                ):
                    standalone_game_module._verify_nested_runtime_bundle_from_retained_standalone_stage(  # noqa: SLF001
                        wrong_nested_root,
                        expected_content_hash="0" * 64,
                        expected_outer_stage=outer_stage,
                        _retained_stage_writer=writer,
                    )

    def test_public_standalone_verifier_rejects_raw_stage_capability(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-raw-cap-public-") as temporary:
            root = Path(temporary) / "stage"
            capability = generic_runtime._create_runtime_stage_read_capability(  # noqa: SLF001
                root=root / RUNTIME_BUNDLE_ROOT,
                require_binding=lambda: None,
            )
            with self.assertRaises(TypeError):
                verify_standalone_game(
                    root,
                    _stage_capability=capability,  # type: ignore[call-arg]
                )

    def test_materialization_scopes_stage_capability_to_private_verification_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-stage-calls-") as temporary:
            root = Path(temporary)
            target = root / "game"
            with _ready_materialization("abstract-puzzle", root) as source:
                original = standalone_game_module.verify_standalone_game
                with mock.patch.object(
                    standalone_game_module,
                    "verify_standalone_game",
                    wraps=original,
                ) as verify_calls:
                    verified = materialize_game(source.root, target)
                    verified.close()

            stage_calls = [
                call
                for call in verify_calls.call_args_list
                if call.kwargs.get("_retained_stage_writer") is not None
            ]
            self.assertEqual(1, len(stage_calls))
            writer = stage_calls[0].kwargs["_retained_stage_writer"]
            self.assertIs(type(writer), RetainedStageWriter)
            self.assertEqual(Path(os.path.abspath(stage_calls[0].args[0])), writer.stage)
            strict_destination_calls = [
                call
                for call in verify_calls.call_args_list
                if Path(os.path.abspath(call.args[0])) == target
                and call.kwargs.get("_retained_stage_writer") is None
            ]
            self.assertGreaterEqual(len(strict_destination_calls), 2)

    def test_package_launcher_keeps_its_argument_parser_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-package-launcher-parser-") as temporary:
            script = Path(temporary) / "scripts/package_game.py"
            script.parent.mkdir()
            script.write_bytes(STANDALONE_TEMPLATE_FILES["scripts/package_game.py"][0])
            result = subprocess.run(
                [sys.executable, "-I", str(script), "--help"],
                cwd=Path(temporary),
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("usage:", result.stdout)
            self.assertNotIn("NameError", result.stderr)

    def test_native_smoke_launcher_publishes_external_report_without_forge(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-native-smoke-launcher-") as temporary:
            root = Path(temporary)
            game_root = root / "relocated-game"
            script = game_root / "scripts/native_smoke.py"
            script.parent.mkdir(parents=True)
            script.write_bytes(STANDALONE_TEMPLATE_FILES["scripts/native_smoke.py"][0])
            snapshot = game_root / "game_data/runtime-bundle/runtime/snapshot-tree"
            shutil.copytree(ROOT / "src/gamepack_runtime", snapshot / "gamepack_runtime")
            snapshot.joinpath("worldforge.py").write_text(
                "raise AssertionError('native launcher imported Forge authoring code')\n",
                encoding="utf-8",
            )
            adapter = snapshot / "gamepack_raylib_2d"
            adapter.mkdir()
            adapter.joinpath("__init__.py").write_text("", encoding="utf-8")
            adapter.joinpath("native_smoke.py").write_text(
                "class NativeSmokeError(RuntimeError):\n"
                "    pass\n\n"
                "def native_smoke(_root, *, max_frames, hidden):\n"
                "    assert max_frames == 2\n"
                "    assert hidden is True\n"
                "    return {\n"
                "        'adapter_id': 'adapter_test',\n"
                "        'adapter_version': '1.2.3',\n"
                "        'frames': 2,\n"
                "        'platform_id': 'platform:test_x86_64',\n"
                "        'status': 'native_smoke_executed',\n"
                "    }\n",
                encoding="utf-8",
            )
            evidence = root / "native-evidence"
            evidence.mkdir()
            parent_state = evidence.stat()
            report = evidence / "report.json"
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            arguments = [
                sys.executable,
                "-I",
                str(script),
                "--report",
                str(report),
                "--report-parent-device",
                str(parent_state.st_dev),
                "--report-parent-inode",
                str(parent_state.st_ino),
            ]
            result = subprocess.run(
                arguments,
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            expected = {
                "adapter_id": "adapter_test",
                "adapter_version": "1.2.3",
                "frames": 2,
                "platform_id": "platform:test_x86_64",
                "status": "native_smoke_executed",
            }
            self.assertEqual(canonical_json_bytes(expected), report.read_bytes())

            repeated = subprocess.run(
                arguments,
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(1, repeated.returncode)
            self.assertIn("ERROR", repeated.stderr)
            self.assertEqual(canonical_json_bytes(expected), report.read_bytes())

            manual = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, manual.returncode, manual.stderr)
            self.assertEqual(expected, json.loads(manual.stdout))

            incomplete = subprocess.run(
                [sys.executable, "-I", str(script), "--report", str(evidence / "partial.json")],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(2, incomplete.returncode)
            self.assertNotIn("Traceback", incomplete.stderr)
            self.assertFalse((evidence / "partial.json").exists())

            inside_parent = game_root / "forbidden-evidence"
            inside_parent.mkdir()
            inside_state = inside_parent.stat()
            inside_report = inside_parent / "report.json"
            inside = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(script),
                    "--report",
                    str(inside_report),
                    "--report-parent-device",
                    str(inside_state.st_dev),
                    "--report-parent-inode",
                    str(inside_state.st_ino),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(1, inside.returncode)
            self.assertIn("ERROR", inside.stderr)
            self.assertFalse(inside_report.exists())

    def test_ready_envelope_has_exact_complete_launcher_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-ready-materialization-") as temporary:
            with _ready_materialization("abstract-puzzle", Path(temporary)) as bundle:
                manifest = bundle.manifest
                self.assertEqual(manifest["state"], "materialization_ready")
                self.assertIs(manifest["materialization_ready"], True)
                self.assertEqual(manifest["missing_launcher_roles"], [])
                roles = {item["role"] for item in manifest["launchers"]["inventory"]}
                self.assertEqual(
                    roles,
                    {
                        "game_launcher",
                        "game_package",
                        "game_packager",
                        "game_readme",
                        "game_source",
                        "game_test",
                        "game_verifier",
                        "gitignore",
                        "materialization_policy",
                        "native_smoke_launcher",
                        "offline_smoke_launcher",
                        "requirements",
                        "third_party_notices",
                    },
                )
                self.assertEqual(
                    require_standalone_materialization_source(bundle.root).manifest,
                    manifest,
                )

    def test_native_smoke_launcher_hash_rotation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-native-launcher-hash-") as temporary:
            root = Path(temporary)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            with (
                _ready_materialization("abstract-puzzle", first_root) as first,
                _ready_materialization("abstract-puzzle", second_root) as second,
            ):
                payload = STANDALONE_TEMPLATE_FILES["scripts/native_smoke.py"][0]
                records = []
                for bundle in (first, second):
                    record = next(
                        item
                        for item in bundle.manifest["launchers"]["inventory"]
                        if item["role"] == "native_smoke_launcher"
                    )
                    self.assertEqual("launchers/templates/scripts/native_smoke.py", record["path"])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
                    self.assertEqual(len(payload), record["size_bytes"])
                    self.assertEqual(payload, bundle.read_bytes(record["path"]))
                    records.append(record)
                self.assertEqual(records[0], records[1])
                self.assertEqual(first.manifest, second.manifest)

    def test_materializes_and_independently_verifies_both_verticals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-e2e-") as temporary:
            root = Path(temporary)
            for name in ("abstract-puzzle", "branching-narrative"):
                with self.subTest(name=name), _ready_materialization(name, root) as source:
                    target = root / f"{name}-game"
                    verified = materialize_game(
                        source.root,
                        target,
                        expected_content_hash=source.manifest["content_hash"],
                    )
                    try:
                        manifest = verified.manifest
                        lock = verified.lock
                        platform = verified.platform
                        self.assertEqual(manifest["format"], STANDALONE_GAME_FORMAT)
                        self.assertEqual(lock["format"], STANDALONE_GAME_LOCK_FORMAT)
                        self.assertEqual(platform["format"], STANDALONE_PLATFORM_FORMAT)
                        self.assertEqual(manifest["state"], "materialized")
                        self.assertEqual(
                            validate_standalone_game_document(manifest),
                            manifest,
                        )
                        self.assertEqual(
                            validate_standalone_game_lock_document(lock),
                            lock,
                        )
                        self.assertEqual(
                            validate_standalone_platform_document(platform),
                            platform,
                        )
                        self.assertEqual(
                            manifest["lineage"]["runtime_bundle_hash"],
                            source.manifest["lineage"]["runtime_bundle_hash"],
                        )
                        nested_source = source.root / "runtime-bundle"
                        nested_target = target / "game_data/runtime-bundle"
                        source_files = {
                            path.relative_to(nested_source).as_posix(): path.read_bytes()
                            for path in nested_source.rglob("*")
                            if path.is_file()
                        }
                        target_files = {
                            path.relative_to(nested_target).as_posix(): path.read_bytes()
                            for path in nested_target.rglob("*")
                            if path.is_file()
                        }
                        self.assertEqual(target_files, source_files)
                        for relative, payload in target_files.items():
                            self.assertNotIn("qa-review", relative)
                            self.assertNotIn("release-authority", relative)
                            self.assertNotIn(
                                b"world-forge.asset_qa_review_receipt",
                                payload,
                            )
                            self.assertNotIn(
                                b"world-forge.asset_release_authority",
                                payload,
                            )
                    finally:
                        verified.close()

                    unrelated = root / "unrelated"
                    unrelated.mkdir(exist_ok=True)
                    environment = {
                        key: value
                        for key, value in os.environ.items()
                        if key not in {"PYTHONHOME", "PYTHONPATH"}
                    }
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(target / "scripts/verify_game.py"),
                        ],
                        cwd=unrelated,
                        env=environment,
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(report["status"], "verified")
                    self.assertEqual(report["authoring_dependencies"], 0)
                    self.assertEqual(report["runtime_ai_capabilities"], 0)
                    if name == "abstract-puzzle":
                        with self.assertRaisesRegex(
                            ValueError,
                            "^standalone_game_destination_invalid:",
                        ):
                            materialize_game(source.root, target / "nested-game")

    def test_headless_shell_runs_without_pyray_or_forge_on_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-headless-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "puzzle-game"
                materialized = materialize_game(source.root, target)
                materialized.close()
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "run_game.py"),
                    "--headless-script",
                    str(root / "abstract-puzzle-execution-script.json"),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(
                {item["scenario_id"] for item in report["scenarios"]},
                {"restart_board", "swap_tiles"},
            )
            user_data = root / "puzzle-user-data"
            persisted = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "run_game.py"),
                    "--headless-script",
                    str(root / "abstract-puzzle-execution-script.json"),
                    "--scenario",
                    "swap_tiles",
                    "--user-data",
                    str(user_data),
                    "--save-on-exit-slot",
                    "solved",
                    "--record-replay-slot",
                    "solve",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(persisted.returncode, 0, persisted.stderr)
            self.assertEqual(
                json.loads(persisted.stdout)["scenarios"][0]["scenario_id"],
                "swap_tiles",
            )
            self.assertTrue(list((user_data / "saves").rglob("*.json")))
            self.assertTrue(list((user_data / "replays").rglob("*.json")))
            replayed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "run_game.py"),
                    "--user-data",
                    str(user_data),
                    "--replay-slot",
                    "solve",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertEqual(
                json.loads(replayed.stdout)["classification"]["ending_ids"],
                ["puzzle_complete"],
            )

    def test_temp_live_headless_script_executes_without_forge_on_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-live-headless-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "puzzle-game"
                materialized = materialize_game(source.root, target)
                materialized.close()
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "run_game.py"),
                    "--headless-script",
                    str(root / "abstract-puzzle-execution-script.json"),
                ],
                cwd=root,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"PYTHONHOME", "PYTHONPATH"}
                },
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(
                {item["scenario_id"] for item in report["scenarios"]},
                {"restart_board", "swap_tiles"},
            )

    def test_branching_standalone_persists_and_replays_both_endings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-endings-") as temporary:
            root = Path(temporary)
            with _ready_materialization("branching-narrative", root) as source:
                target = root / "narrative-game"
                materialized = materialize_game(source.root, target)
                materialized.close()
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            user_data = root / "narrative-user-data"
            for scenario, ending in (
                ("choose_left", "ending_left"),
                ("choose_right", "ending_right"),
            ):
                recorded = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(target / "run_game.py"),
                        "--headless-script",
                        str(root / "branching-narrative-execution-script.json"),
                        "--scenario",
                        scenario,
                        "--user-data",
                        str(user_data),
                        "--save-on-exit-slot",
                        scenario,
                        "--record-replay-slot",
                        scenario,
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(recorded.returncode, 0, recorded.stderr)
                replayed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(target / "run_game.py"),
                        "--user-data",
                        str(user_data),
                        "--replay-slot",
                        scenario,
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(replayed.returncode, 0, replayed.stderr)
                self.assertEqual(
                    json.loads(replayed.stdout)["classification"]["ending_ids"],
                    [ending],
                )

    def test_cli_conflicts_are_usage_errors_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-cli-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "puzzle-game"
                materialized = materialize_game(source.root, target)
                materialized.close()
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "run_game.py"),
                    "--headless-script",
                    str(_fixture("abstract-puzzle", "runtime/headless/execution-script.json")),
                    "--replay-slot",
                    "solve",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("not allowed with argument", result.stderr)

    def test_bare_and_contract_only_sources_fail_with_precise_reasons(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-source-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime:
                with self.assertRaisesRegex(
                    ValueError,
                    "^runtime_implementation_identity_missing:",
                ):
                    require_standalone_materialization_source(runtime.root)
                blocked = build_game_materialization_bundle(
                    root / "blocked-materialization",
                    runtime_bundle_root=runtime.root,
                    runtime_implementation=load_runtime_implementation(
                        _fixture("abstract-puzzle", "runtime/runtime-implementation.json")
                    ),
                    platform_locks=_platform_locks(),
                    include_standalone_launchers=False,
                )
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "^materialization_bundle_not_ready:",
                    ):
                        require_standalone_materialization_source(blocked.root)
                finally:
                    blocked.close()

    def test_materialized_tree_rejects_extra_file_and_self_resealed_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-standalone-tamper-") as temporary:
            root = Path(temporary)
            with _ready_materialization("branching-narrative", root) as source:
                target = root / "narrative-game"
                materialized = materialize_game(source.root, target)
                materialized.close()
            (target / "unexpected.txt").write_text("tamper", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "standalone_game_file_closure_invalid"):
                verify_standalone_game(target)

            (target / "unexpected.txt").unlink()
            unexpected_directory = target / "unexpected-empty"
            unexpected_directory.mkdir()
            with self.assertRaisesRegex(
                ValueError,
                "standalone_game_directory_closure_invalid",
            ):
                verify_standalone_game(target)
            isolated = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(target / "scripts/verify_game.py"),
                ],
                cwd=root,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"PYTHONHOME", "PYTHONPATH"}
                },
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(isolated.returncode, 1)
            self.assertIn(
                "standalone_game_directory_closure_invalid",
                isolated.stderr,
            )
            unexpected_directory.rmdir()

            platform_path = target / "platform.lock.json"
            platform = json.loads(platform_path.read_text(encoding="utf-8"))
            platform["runtime_snapshot"]["content_hash"] = "0" * 64
            platform["platform_set_id"] = (
                "standalone_platform_"
                + canonical_contract_hash(
                    {
                        key: platform[key]
                        for key in (
                            "requires_python",
                            "dependency",
                            "adapter",
                            "runtime_implementation",
                            "runtime_snapshot",
                            "platform_locks",
                        )
                    }
                )[:40]
            )
            platform["content_hash"] = canonical_contract_hash(platform)
            platform_payload = canonical_contract_bytes(platform)
            platform_path.write_bytes(platform_payload)

            manifest_path = target / "game-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["platform_set"]["id"] = platform["platform_set_id"]
            manifest["platform_set"]["content_hash"] = platform["content_hash"]
            manifest["content_hash"] = canonical_contract_hash(manifest)
            manifest_path.write_bytes(canonical_contract_bytes(manifest))

            lock_path = target / "game.lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            for record in lock["files"]:
                if record["path"] == "platform.lock.json":
                    record["sha256"] = hashlib.sha256(platform_payload).hexdigest()
                    record["size_bytes"] = len(platform_payload)
            lock["tree_hash"] = canonical_contract_hash({"files": lock["files"]})
            lock["lock_id"] = "standalone_game_lock_" + lock["tree_hash"][:40]
            lock["content_hash"] = canonical_contract_hash(lock)
            manifest["payload_lock"] = {
                "format": STANDALONE_GAME_LOCK_FORMAT,
                "format_version": 1,
                "id": lock["lock_id"],
                "content_hash": lock["content_hash"],
                "tree_hash": lock["tree_hash"],
            }
            manifest["content_hash"] = canonical_contract_hash(manifest)
            manifest_path.write_bytes(canonical_contract_bytes(manifest))
            lock_path.write_bytes(canonical_contract_bytes(lock))
            with self.assertRaisesRegex(ValueError, "standalone_game_lineage_mismatch"):
                verify_standalone_game(target)

    def test_worldforge_materialize_game_cli_returns_machine_readable_result(self) -> None:
        from worldforge.__main__ import main

        with tempfile.TemporaryDirectory(prefix="wf-materialize-cli-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "cli-game"
                stdout = io.StringIO()
                with (
                    contextlib.redirect_stdout(stdout),
                    unittest.mock.patch.object(
                        sys,
                        "argv",
                        [
                            "worldforge",
                            "materialize-game",
                            str(source.root),
                            str(target),
                            "--expected-hash",
                            source.manifest["content_hash"],
                        ],
                    ),
                ):
                    self.assertEqual(main(), 0)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["status"], "materialized")
                self.assertEqual(report["path"], str(target))

    def test_generic_audit_and_recovery_cli_surfaces_are_closed(self) -> None:
        from worldforge.__main__ import main

        def invoke(arguments: list[str]) -> tuple[int, str, str]:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch.object(sys, "argv", ["worldforge", *arguments]),
            ):
                status = main()
            return status, stdout.getvalue(), stderr.getvalue()

        with tempfile.TemporaryDirectory(prefix="wf-standalone-control-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "recover-cli-game"

                def crash(stage: str, _path: Path | None) -> None:
                    if stage == "after_ready_journal_written":
                        raise RuntimeError("simulated crash")

                with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                    materialize_game(source.root, target, _publication_hook=crash)
                status, stdout, stderr = invoke(["recover-game-materialization", str(target)])
                self.assertEqual((status, stderr), (0, ""))
                self.assertEqual(json.loads(stdout)["status"], "materialized")
                self.assertEqual(repository_kind(target), "generic_game")
                status, stdout, stderr = invoke(["audit-game", str(target)])
                self.assertEqual((status, stderr), (0, ""))
                self.assertIn("authoring_leaks=0", stdout)

                rollback_target = root / "rollback-cli-game"

                def stop(stage: str, _path: Path | None) -> None:
                    if stage == "after_copying_journal_written":
                        raise RuntimeError("stop")

                with self.assertRaisesRegex(RuntimeError, "stop"):
                    materialize_game(
                        source.root,
                        rollback_target,
                        _publication_hook=stop,
                    )
                status, stdout, stderr = invoke(
                    ["rollback-game-materialization", str(rollback_target)]
                )
                if sys.platform.startswith("linux") and os.name == "posix":
                    self.assertEqual(status, 1)
                    self.assertEqual(stdout, "")
                    self.assertIn(
                        "standalone_game_rollback_recovery_required",
                        stderr,
                    )
                else:
                    self.assertEqual((status, stderr), (0, ""))
                    self.assertEqual(json.loads(stdout)["status"], "rolled_back")

    def test_journal_recovery_and_identity_owned_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialize-recovery-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                target = root / "recover-game"

                def crash(stage: str, _path: Path | None) -> None:
                    if stage == "after_ready_journal_written":
                        raise RuntimeError("simulated crash")

                with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                    materialize_game(
                        source.root,
                        target,
                        _publication_hook=crash,
                    )
                recovered = recover_standalone_game(target)
                self.assertIsNotNone(recovered)
                assert recovered is not None
                try:
                    self.assertEqual(recovered.manifest["state"], "materialized")
                finally:
                    recovered.close()
                self.assertFalse((root / ".recover-game.standalone-game.journal.json").exists())

                rollback_target = root / "rollback-game"

                def stop_before_publish(stage: str, _path: Path | None) -> None:
                    if stage == "after_copying_journal_written":
                        raise RuntimeError("stop before publish")

                with self.assertRaisesRegex(RuntimeError, "stop before publish"):
                    materialize_game(
                        source.root,
                        rollback_target,
                        _publication_hook=stop_before_publish,
                    )
                if sys.platform.startswith("linux") and os.name == "posix":
                    with self.assertRaises(StandaloneGameError) as recovery:
                        recover_standalone_game(rollback_target)
                    self.assertEqual(
                        "standalone_game_recovery_required",
                        recovery.exception.reason_code,
                    )
                    self.assertEqual(
                        next(root.glob(".rollback-game.standalone-stage-*")).name,
                        recovery.exception.recovery_evidence["stage"]["locator"],
                    )
                    with self.assertRaises(StandaloneGameError) as raised:
                        rollback_standalone_game(rollback_target)
                    self.assertEqual(
                        "standalone_game_rollback_recovery_required",
                        raised.exception.reason_code,
                    )
                    self.assertIn("retained", raised.exception.detail)
                    self.assertEqual(
                        (root / ".rollback-game.standalone-game.journal.json").name,
                        raised.exception.recovery_evidence["journal"]["locator"],
                    )
                    self.assertTrue(next(root.glob(".rollback-game.standalone-stage-*")).is_dir())
                    self.assertTrue(
                        (root / ".rollback-game.standalone-game.journal.json").is_file()
                    )
                else:
                    result = rollback_standalone_game(rollback_target)
                    self.assertEqual(result["status"], "rolled_back")
                    self.assertFalse(rollback_target.exists())

    def test_recovery_rejects_transplanted_journal_and_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialize-transplant-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                for container_kind in ("plain", "world"):
                    with self.subTest(container_kind=container_kind):
                        target = root / f"{container_kind}-game"
                        retained_stage: Path | None = None

                        def crash(stage: str, path: Path | None) -> None:
                            nonlocal retained_stage
                            if stage == "after_ready_journal_written":
                                retained_stage = path
                                raise RuntimeError("simulated crash")

                        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                            materialize_game(
                                source.root,
                                target,
                                _publication_hook=crash,
                            )
                        assert retained_stage is not None
                        relocated_parent = root / f"relocated-{container_kind}"
                        relocated_parent.mkdir()
                        if container_kind == "world":
                            marker = relocated_parent / ".worldforge/project.json"
                            marker.parent.mkdir()
                            marker.write_text("{}\n", encoding="utf-8")
                            self.assertEqual(repository_kind(relocated_parent), "world")
                        retained_stage.rename(relocated_parent / retained_stage.name)
                        journal = root / (f".{target.name}.standalone-game.journal.json")
                        journal.rename(relocated_parent / journal.name)
                        relocated_target = relocated_parent / target.name
                        with self.assertRaisesRegex(
                            ValueError,
                            "^standalone_game_(?:destination|journal)_invalid:",
                        ):
                            recover_standalone_game(relocated_target)
                        self.assertFalse(relocated_target.exists())

    def test_rollback_preserves_foreign_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialize-rollback-empty-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                for partial_state in ("empty", "manifest_only", "lock_only"):
                    with self.subTest(partial_state=partial_state):
                        target = root / f"rollback-{partial_state}-game"
                        retained_stage: Path | None = None

                        def stop(
                            stage: str,
                            path: Path | None,
                            partial_state: str = partial_state,
                        ) -> None:
                            nonlocal retained_stage
                            if (
                                partial_state == "empty"
                                and stage == "after_copying_journal_written"
                            ):
                                retained_stage = path
                                raise RuntimeError("stop before copy")
                            if (
                                partial_state == "manifest_only"
                                and stage == "after_file_written"
                                and path is not None
                                and path.name == "game-manifest.json"
                            ):
                                retained_stage = path.parent
                                raise RuntimeError("stop after manifest")
                            if (
                                partial_state == "lock_only"
                                and stage == "after_file_written"
                                and path is not None
                                and path.name == "game.lock.json"
                            ):
                                retained_stage = path.parent
                                raise RuntimeError("stop after lock")

                        with self.assertRaisesRegex(RuntimeError, "^stop "):
                            materialize_game(
                                source.root,
                                target,
                                _publication_hook=stop,
                            )
                        assert retained_stage is not None
                        foreign = retained_stage / (
                            "game_data" if partial_state == "lock_only" else "foreign-empty"
                        )
                        foreign.mkdir()
                        journal = root / (f".{target.name}.standalone-game.journal.json")
                        self.assertTrue(journal.is_file())
                        with self.assertRaisesRegex(
                            ValueError,
                            "^standalone_game_rollback_ambiguous:",
                        ):
                            rollback_standalone_game(target)
                        self.assertTrue(retained_stage.is_dir())
                        self.assertTrue(foreign.is_dir())
                        self.assertTrue(journal.is_file())

    def test_concurrent_materializers_publish_at_most_one_exact_game(self) -> None:
        import concurrent.futures

        with tempfile.TemporaryDirectory(prefix="wf-materialize-race-") as temporary:
            root = Path(temporary)
            with _ready_materialization("branching-narrative", root) as source:
                target = root / "race-game"

                def attempt() -> str:
                    try:
                        verified = materialize_game(source.root, target)
                        verified.close()
                        return "published"
                    except ValueError as exc:
                        return str(exc).split(":", 1)[0]

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _index: attempt(), range(2)))
                self.assertEqual(results.count("published"), 1)
                self.assertTrue(
                    set(results)
                    <= {
                        "published",
                        "standalone_game_publication_busy",
                        "standalone_game_destination_exists",
                    }
                )
                verified = verify_standalone_game(target)
                verified.close()


if __name__ == "__main__":
    unittest.main()
