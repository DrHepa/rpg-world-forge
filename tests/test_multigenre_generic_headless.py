from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import worldforge.generic_headless as generic_headless
import worldforge.generic_runtime as generic_runtime
from gamepack_runtime import GameLogicError
from gamepack_runtime.headless import (
    GAME_EXECUTION_SCRIPT_FORMAT,
    HEADLESS_EXECUTION_RECEIPT_FORMAT,
    build_game_execution_script,
    canonical_headless_hash,
    execute_game_execution_script,
    execution_audit_guard,
    serialize_game_execution_script,
    serialize_headless_execution_receipt,
    validate_game_execution_script,
    validate_headless_execution_receipt,
)
from scripts.generate_generic_headless_schemas import build_schemas
from tests.test_multigenre_game_runtime_bundle import _build_bundle
from worldforge.__main__ import main
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.directory_publish import (
    DirectoryPublishError,
    RetainedStageWriter,
    create_retained_stage,
    directory_identity,
)
from worldforge.generic_headless import (
    HEADLESS_EVIDENCE_COMMIT,
    HEADLESS_EVIDENCE_SET_FORMAT,
    HEADLESS_EVIDENCE_SET_MANIFEST,
    GenericHeadlessError,
    build_headless_evidence_set,
    recover_headless_evidence_set,
    serialize_headless_evidence_set,
    verify_headless_evidence_set,
)
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _bundle_document(bundle: object, relative: str) -> dict[str, object]:
    value = json.loads(bundle.read_bytes(relative))
    assert isinstance(value, dict)
    return value


def _scenario_inputs(fixture: str) -> list[dict[str, object]]:
    if fixture == "abstract-puzzle":
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


def _reseal_script(document: dict[str, object]) -> None:
    seed = {
        key: value for key, value in document.items() if key not in {"script_id", "content_hash"}
    }
    document["script_id"] = "game_execution_script_" + canonical_headless_hash(seed)[:40]
    document["content_hash"] = canonical_headless_hash(document)


def _reseal_evidence_manifest(
    root: Path,
    manifest: dict[str, object],
    *,
    changed_path: str,
    changed_payload: bytes,
) -> None:
    (root / changed_path).write_bytes(changed_payload)
    for record in manifest["files"]:
        if record["path"] == changed_path:
            record["sha256"] = hashlib.sha256(changed_payload).hexdigest()
            record["size_bytes"] = len(changed_payload)
            break
    else:
        raise AssertionError(f"evidence inventory lacks {changed_path}")
    manifest["tree_hash"] = canonical_creation_hash({"files": manifest["files"]})
    manifest["total_bytes"] = sum(record["size_bytes"] for record in manifest["files"])
    seed = {
        key: manifest[key]
        for key in (
            "state",
            "runtime_bundle",
            "execution_script",
            "headless_receipt",
            "runtime_evidence",
            "support",
            "files",
            "tree_hash",
            "file_count",
            "total_bytes",
        )
    }
    manifest["evidence_set_id"] = "headless_evidence_set_" + canonical_creation_hash(seed)[:40]
    manifest["content_hash"] = canonical_creation_hash(manifest)
    (root / HEADLESS_EVIDENCE_SET_MANIFEST).write_bytes(serialize_headless_evidence_set(manifest))
    (root / HEADLESS_EVIDENCE_COMMIT).write_bytes(
        canonical_json_bytes(
            {
                "format": "world-forge.headless_evidence_commit",
                "format_version": 1,
                "evidence_set": {
                    "format": manifest["format"],
                    "format_version": manifest["format_version"],
                    "id": manifest["evidence_set_id"],
                    "content_hash": manifest["content_hash"],
                },
                "tree_hash": manifest["tree_hash"],
            }
        )
    )


class GenericHeadlessExecutionTests(unittest.TestCase):
    def _build_script(
        self,
        fixture: str,
        root: Path,
    ) -> tuple[object, dict[str, object]]:
        bundle = _build_bundle(fixture, root)
        script = build_game_execution_script(
            bundle.manifest,
            gamepack=_bundle_document(bundle, "contracts/gamepack.json"),
            composition=_bundle_document(bundle, "contracts/runtime-composition.json"),
            adapter=_bundle_document(
                bundle,
                bundle.manifest["contracts"]["runtime_adapter"]["path"],
            ),
            runtime_snapshot=_bundle_document(bundle, "contracts/runtime-snapshot.json"),
            scenarios=_scenario_inputs(fixture),
        )
        return bundle, script

    def test_schema_generator_exposes_three_closed_v1_contracts(self) -> None:
        schemas = build_schemas()
        self.assertEqual(
            set(schemas),
            {
                "schemas/game-execution-script.schema.json",
                "schemas/headless-evidence-set.schema.json",
                "schemas/headless-execution-receipt.schema.json",
            },
        )
        self.assertEqual(
            schemas["schemas/game-execution-script.schema.json"]["properties"]["format"]["const"],
            GAME_EXECUTION_SCRIPT_FORMAT,
        )
        self.assertEqual(
            schemas["schemas/headless-execution-receipt.schema.json"]["properties"]["format"][
                "const"
            ],
            HEADLESS_EXECUTION_RECEIPT_FORMAT,
        )
        self.assertEqual(
            schemas["schemas/headless-evidence-set.schema.json"]["properties"]["format"]["const"],
            HEADLESS_EVIDENCE_SET_FORMAT,
        )
        for schema in schemas.values():
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"]["format_version"]["const"], 1)

    def test_puzzle_and_narrative_scripts_execute_byte_identically(self) -> None:
        expected_endings = {
            "abstract-puzzle": [[], ["puzzle_complete"]],
            "branching-narrative": [["ending_left"], ["ending_right"]],
        }
        for fixture in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(fixture=fixture):
                with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
                    bundle, script = self._build_script(fixture, Path(temporary))
                    try:
                        with mock.patch(
                            "gamepack_runtime.headless._native_machine",
                            return_value="x86_64",
                        ):
                            first = execute_game_execution_script(
                                bundle.manifest,
                                script,
                                gamepack=_bundle_document(bundle, "contracts/gamepack.json"),
                                composition=_bundle_document(
                                    bundle,
                                    "contracts/runtime-composition.json",
                                ),
                                adapter=_bundle_document(
                                    bundle,
                                    bundle.manifest["contracts"]["runtime_adapter"]["path"],
                                ),
                                runtime_snapshot=_bundle_document(
                                    bundle,
                                    "contracts/runtime-snapshot.json",
                                ),
                            )
                            second = execute_game_execution_script(
                                bundle.manifest,
                                copy.deepcopy(script),
                                gamepack=_bundle_document(bundle, "contracts/gamepack.json"),
                                composition=_bundle_document(
                                    bundle,
                                    "contracts/runtime-composition.json",
                                ),
                                adapter=_bundle_document(
                                    bundle,
                                    bundle.manifest["contracts"]["runtime_adapter"]["path"],
                                ),
                                runtime_snapshot=_bundle_document(
                                    bundle,
                                    "contracts/runtime-snapshot.json",
                                ),
                            )
                    finally:
                        bundle.close()
                    self.assertEqual(first.receipt_bytes, second.receipt_bytes)
                    self.assertEqual(first.save_bytes, second.save_bytes)
                    self.assertEqual(first.replay_bytes, second.replay_bytes)
                    receipt = validate_headless_execution_receipt(first.receipt)
                    self.assertEqual(
                        [item["classification"]["ending_ids"] for item in receipt["scenarios"]],
                        expected_endings[fixture],
                    )
                    self.assertTrue(receipt["coverage"]["complete"])
                    self.assertEqual(
                        [item["check_id"] for item in receipt["checks"]],
                        ["check:headless_determinism", "check:save_replay"],
                    )
                    self.assertTrue(all(item["status"] == "passed" for item in receipt["checks"]))
                    self.assertFalse(receipt["native_execution"])

    def test_script_binding_order_coverage_rejection_and_limits_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
            bundle, script = self._build_script("abstract-puzzle", Path(temporary))
            try:
                inputs = {
                    "gamepack": _bundle_document(bundle, "contracts/gamepack.json"),
                    "composition": _bundle_document(
                        bundle,
                        "contracts/runtime-composition.json",
                    ),
                    "adapter": _bundle_document(
                        bundle,
                        bundle.manifest["contracts"]["runtime_adapter"]["path"],
                    ),
                    "runtime_snapshot": _bundle_document(
                        bundle,
                        "contracts/runtime-snapshot.json",
                    ),
                }
                self.assertEqual(
                    validate_game_execution_script(bundle.manifest, script, **inputs),
                    script,
                )
                for mutation, reason in (
                    ("bundle", "binding_mismatch"),
                    ("order", "script_invalid"),
                    ("coverage", "coverage_violation"),
                    ("actions", "action_limit"),
                ):
                    with self.subTest(mutation=mutation):
                        tampered = copy.deepcopy(script)
                        if mutation == "bundle":
                            tampered["bindings"]["runtime_bundle"]["content_hash"] = "f" * 64
                        elif mutation == "order":
                            tampered["scenarios"].reverse()
                        elif mutation == "coverage":
                            tampered["scenarios"] = tampered["scenarios"][1:]
                        else:
                            source_action = tampered["scenarios"][0]["actions"][0]
                            tampered["scenarios"][0]["actions"] = [
                                copy.deepcopy(source_action) for _index in range(129)
                            ]
                        _reseal_script(tampered)
                        with self.assertRaisesRegex(GameLogicError, reason):
                            validate_game_execution_script(
                                bundle.manifest,
                                tampered,
                                **inputs,
                            )
            finally:
                bundle.close()

    def test_rejected_action_and_expected_state_or_classification_fail_at_exact_index(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
            bundle, script = self._build_script("branching-narrative", Path(temporary))
            inputs = {
                "gamepack": _bundle_document(bundle, "contracts/gamepack.json"),
                "composition": _bundle_document(
                    bundle,
                    "contracts/runtime-composition.json",
                ),
                "adapter": _bundle_document(
                    bundle,
                    bundle.manifest["contracts"]["runtime_adapter"]["path"],
                ),
                "runtime_snapshot": _bundle_document(
                    bundle,
                    "contracts/runtime-snapshot.json",
                ),
            }
            try:
                rejected = copy.deepcopy(script)
                rejected["scenarios"][0]["actions"][0]["action_id"] = "choose_right"
                rejected["scenarios"][1]["actions"][0]["action_id"] = "choose_left"
                _reseal_script(rejected)
                with (
                    self.assertRaisesRegex(GameLogicError, "expected_state_violation"),
                    mock.patch(
                        "gamepack_runtime.headless._native_machine",
                        return_value="x86_64",
                    ),
                ):
                    execute_game_execution_script(bundle.manifest, rejected, **inputs)

                classified = copy.deepcopy(script)
                classified["scenarios"][0]["expected_classification"]["ending_ids"] = [
                    "ending_right"
                ]
                _reseal_script(classified)
                with (
                    self.assertRaisesRegex(
                        GameLogicError,
                        "expected_classification_violation",
                    ),
                    mock.patch(
                        "gamepack_runtime.headless._native_machine",
                        return_value="x86_64",
                    ),
                ):
                    execute_game_execution_script(bundle.manifest, classified, **inputs)

                rejected_action = copy.deepcopy(script)
                rejected_action["scenarios"][0]["actions"].append(
                    {"action_id": "choose_right", "parameters": {}}
                )
                _reseal_script(rejected_action)
                with (
                    self.assertRaisesRegex(GameLogicError, "action_rejected"),
                    mock.patch(
                        "gamepack_runtime.headless._native_machine",
                        return_value="x86_64",
                    ),
                ):
                    execute_game_execution_script(bundle.manifest, rejected_action, **inputs)
            finally:
                bundle.close()

    def test_active_audit_guard_blocks_network_process_and_dynamic_code_only_in_scope(
        self,
    ) -> None:
        outside = socket.socket()
        outside.close()
        for operation in (
            lambda: socket.socket(),
            lambda: subprocess.run(  # noqa: S603
                [sys.executable, "-c", "pass"],
                check=True,
            ),
            lambda: compile("1 + 1", "<headless-test>", "eval"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(GameLogicError, "headless_audit_violation"):
                    with execution_audit_guard():
                        operation()

    def test_unsupported_architecture_fails_before_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
            bundle, script = self._build_script("abstract-puzzle", Path(temporary))
            try:
                inputs = {
                    "gamepack": _bundle_document(bundle, "contracts/gamepack.json"),
                    "composition": _bundle_document(
                        bundle,
                        "contracts/runtime-composition.json",
                    ),
                    "adapter": _bundle_document(
                        bundle,
                        bundle.manifest["contracts"]["runtime_adapter"]["path"],
                    ),
                    "runtime_snapshot": _bundle_document(
                        bundle,
                        "contracts/runtime-snapshot.json",
                    ),
                }
                with (
                    self.assertRaisesRegex(GameLogicError, "platform_unsupported"),
                    mock.patch(
                        "gamepack_runtime.headless._native_machine",
                        return_value="aarch64",
                    ),
                ):
                    execute_game_execution_script(bundle.manifest, script, **inputs)
            finally:
                bundle.close()

    def test_external_evidence_is_exact_release_blocked_and_cli_round_trips(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle_hash = bundle.manifest["content_hash"]
            bundle.close()
            script_path = root / "script.json"
            script_path.write_bytes(serialize_game_execution_script(script))
            evidence_root = root / "headless-evidence"
            host = mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            )
            with host:
                verified = build_headless_evidence_set(
                    evidence_root,
                    bundle_root=bundle_root,
                    script_path=script_path,
                    expected_bundle_hash=bundle_hash,
                )
            try:
                manifest = verified.manifest
                self.assertEqual(manifest["format"], HEADLESS_EVIDENCE_SET_FORMAT)
                self.assertEqual(
                    verified.evidence["execution_status"],
                    "headless_verified",
                )
                self.assertEqual(verified.evidence["release"], "blocked")
                self.assertFalse(verified.evidence["supported"])
                self.assertEqual(
                    manifest["runtime_evidence"]["execution_status"],
                    "headless_verified",
                )
                self.assertEqual(manifest["support"]["compatibility_status"], "partially_supported")
                self.assertEqual(manifest["support"]["release"], "blocked")
                self.assertEqual(
                    manifest["runtime_evidence"]["platform"]["platform_family"],
                    "platform:linux" if sys.platform.startswith("linux") else "platform:windows",
                )
                self.assertNotIn(
                    "check:native_raylib",
                    verified.read_bytes("runtime/evidence.json").decode(),
                )
            finally:
                verified.close()

            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                checked = verify_headless_evidence_set(
                    evidence_root,
                    bundle_root=bundle_root,
                )
            checked.close()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "worldforge",
                        "verify-game-headless-evidence",
                        str(evidence_root),
                        "--bundle",
                        str(bundle_root),
                    ],
                ),
            ):
                code = main()
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            result = json.loads(stdout.getvalue())
            self.assertEqual(
                set(result),
                {
                    "content_hash",
                    "evidence_set_id",
                    "execution_status",
                    "integrity",
                    "path",
                    "release",
                    "supported",
                },
            )
            self.assertEqual(result["execution_status"], "headless_verified")
            self.assertEqual(result["release"], "blocked")
            self.assertFalse(result["supported"])
            self.assertEqual(result["path"], str(evidence_root.resolve()))

    def test_integral_verifier_rejects_extra_and_self_resealed_receipt_tamper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-tamper-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle.close()
            script_path = root / "script.json"
            script_path.write_bytes(serialize_game_execution_script(script))
            evidence_root = root / "headless-evidence"
            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                verified = build_headless_evidence_set(
                    evidence_root,
                    bundle_root=bundle_root,
                    script_path=script_path,
                )
            manifest = copy.deepcopy(verified.manifest)
            verified.close()

            (evidence_root / "unexpected.json").write_text("{}\n", encoding="utf-8")
            with (
                self.assertRaisesRegex(GenericHeadlessError, "evidence_tree_unsafe"),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
            ):
                verify_headless_evidence_set(
                    evidence_root,
                    bundle_root=bundle_root,
                )
            (evidence_root / "unexpected.json").unlink()

            receipt_path = evidence_root / "receipts/headless.json"
            receipt = json.loads(receipt_path.read_bytes())
            receipt["scenarios"][0]["final_state_hash"] = "f" * 64
            receipt["scenarios"][0]["save"]["restored_state_hash"] = "f" * 64
            receipt["scenarios"][0]["replay"]["replayed_state_hash"] = "f" * 64
            receipt_seed = {
                key: value
                for key, value in receipt.items()
                if key not in {"receipt_id", "content_hash"}
            }
            receipt["receipt_id"] = (
                "headless_execution_receipt_" + canonical_headless_hash(receipt_seed)[:40]
            )
            receipt["content_hash"] = canonical_headless_hash(receipt)
            receipt_payload = serialize_headless_execution_receipt(receipt)
            manifest["headless_receipt"]["id"] = receipt["receipt_id"]
            manifest["headless_receipt"]["content_hash"] = receipt["content_hash"]
            _reseal_evidence_manifest(
                evidence_root,
                manifest,
                changed_path="receipts/headless.json",
                changed_payload=receipt_payload,
            )
            with (
                self.assertRaisesRegex(GenericHeadlessError, "evidence_receipt_mismatch"),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
            ):
                verify_headless_evidence_set(
                    evidence_root,
                    bundle_root=bundle_root,
                )

    def test_retained_tree_identity_is_consistent_after_pre_capture_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-tree-before-") as temporary:
            parent = Path(temporary)
            root = parent / "evidence"
            retired = parent / "retired"
            root.mkdir()
            (root / "a.txt").write_bytes(b"old")
            capture = generic_headless._capture_runtime_files

            def replace_then_capture(
                path: Path,
                *,
                _verification_hook: object = None,
            ) -> dict[str, bytes]:
                root.rename(retired)
                root.mkdir()
                (root / "a.txt").write_bytes(b"NEW")
                return capture(
                    path,
                    _verification_hook=_verification_hook,  # type: ignore[arg-type]
                )

            with mock.patch.object(
                generic_headless,
                "_capture_runtime_files",
                side_effect=replace_then_capture,
            ):
                snapshot = generic_headless._capture_tree(root)

            self.assertEqual(snapshot.files["a.txt"], b"NEW")
            self.assertEqual(
                snapshot.root_identity,
                directory_identity(root, context="replacement evidence root"),
            )

    def test_retained_tree_rejects_root_replacement_during_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-tree-during-") as temporary:
            parent = Path(temporary)
            root = parent / "evidence"
            retired = parent / "retired"
            root.mkdir()
            (root / "a.txt").write_bytes(b"old")
            capture = generic_headless._capture_runtime_files

            def capture_during_replacement(
                path: Path,
                *,
                _verification_hook: object = None,
            ) -> dict[str, bytes]:
                def synchronized_hook(event: str, relative: str | None) -> None:
                    if callable(_verification_hook):
                        _verification_hook(event, relative)
                    if event == "before_final_verification":
                        root.rename(retired)
                        root.mkdir()
                        (root / "a.txt").write_bytes(b"NEW")

                return capture(
                    path,
                    _verification_hook=synchronized_hook,
                )

            with (
                self.assertRaisesRegex(GenericHeadlessError, "evidence_tree_unsafe"),
                mock.patch.object(
                    generic_headless,
                    "_capture_runtime_files",
                    side_effect=capture_during_replacement,
                ),
            ):
                generic_headless._capture_tree(root)

    def test_retained_tree_identity_cannot_cross_a_post_capture_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-tree-") as temporary:
            parent = Path(temporary)
            root = parent / "evidence"
            retired = parent / "retired"
            root.mkdir()
            (root / "a.txt").write_bytes(b"old")
            capture = generic_headless._capture_runtime_files

            def capture_then_replace(
                path: Path,
                *,
                _verification_hook: object = None,
            ) -> dict[str, bytes]:
                files = capture(
                    path,
                    _verification_hook=_verification_hook,  # type: ignore[arg-type]
                )
                root.rename(retired)
                root.mkdir()
                (root / "a.txt").write_bytes(b"NEW")
                return files

            with mock.patch.object(
                generic_headless,
                "_capture_runtime_files",
                side_effect=capture_then_replace,
            ):
                snapshot = generic_headless._capture_tree(root)

            self.assertEqual(snapshot.files["a.txt"], b"old")
            self.assertEqual(
                snapshot.root_identity,
                directory_identity(retired, context="retired evidence root"),
            )
            self.assertNotEqual(
                snapshot.root_identity,
                directory_identity(root, context="replacement evidence root"),
            )

    def test_retained_stage_capability_is_root_bound_and_mutation_sensitive(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-stage-capability-") as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            (root / "a.txt").write_bytes(b"retained\n")
            binding_checks: list[int] = []
            capability = generic_runtime._create_runtime_stage_read_capability(  # noqa: SLF001
                root=root,
                require_binding=lambda: binding_checks.append(len(binding_checks) + 1),
            )

            snapshot = generic_headless._capture_tree(  # noqa: SLF001
                root,
                _stage_capability=capability,
            )

            self.assertEqual(b"retained\n", snapshot.files["a.txt"])
            self.assertEqual([1, 2], binding_checks)
            share_mode = generic_runtime._WindowsRuntimeTreeApi._share_mode_for(  # noqa: SLF001
                capability
            )
            self.assertEqual(0x00000003, share_mode)
            self.assertEqual(0, share_mode & 0x00000004)

            crossed = generic_runtime._create_runtime_stage_read_capability(  # noqa: SLF001
                root=root / "other",
                require_binding=lambda: None,
            )
            with self.assertRaisesRegex(GenericHeadlessError, "evidence_tree_unsafe"):
                generic_headless._capture_tree(  # noqa: SLF001
                    root,
                    _stage_capability=crossed,
                )

            mutation_checks = 0

            def reject_mutation() -> None:
                nonlocal mutation_checks
                mutation_checks += 1
                if mutation_checks == 2:
                    raise DirectoryPublishError("retained stage binding changed")

            mutation_capability = generic_runtime._create_runtime_stage_read_capability(  # noqa: SLF001
                root=root,
                require_binding=reject_mutation,
            )
            with self.assertRaisesRegex(GenericHeadlessError, "evidence_tree_unsafe"):
                generic_headless._capture_tree(  # noqa: SLF001
                    root,
                    _stage_capability=mutation_capability,
                )
            self.assertEqual(2, mutation_checks)

    def test_evidence_verifier_accepts_stage_capability_only_from_its_writer(
        self,
    ) -> None:
        class _CaptureStopped(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="wf-headless-stage-scope-") as temporary:
            root = Path(temporary) / "stage"
            observed: list[object | None] = []

            def stop_capture(
                _root: Path,
                *,
                _stage_capability: object | None = None,
            ) -> object:
                observed.append(_stage_capability)
                raise _CaptureStopped

            bundle = mock.Mock()
            forged = object.__new__(RetainedStageWriter)
            forged.stage = root
            forged.require_binding = lambda: None  # type: ignore[method-assign]
            with (
                mock.patch.object(
                    generic_headless,
                    "verify_game_runtime_bundle",
                    return_value=bundle,
                ),
                mock.patch.object(
                    generic_headless,
                    "_capture_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaisesRegex(
                    GenericHeadlessError,
                    "evidence_stage_capability_invalid",
                ),
            ):
                verify_headless_evidence_set(
                    root,
                    bundle_root=root,
                    _retained_stage_writer=forged,
                )

            with create_retained_stage(root) as writer:
                with (
                    mock.patch.object(
                        generic_headless,
                        "verify_game_runtime_bundle",
                        return_value=bundle,
                    ),
                    mock.patch.object(
                        generic_headless,
                        "_capture_tree",
                        side_effect=stop_capture,
                    ),
                    self.assertRaises(_CaptureStopped),
                ):
                    verify_headless_evidence_set(
                        root,
                        bundle_root=root,
                        _retained_stage_writer=writer,
                    )
                with (
                    mock.patch.object(
                        generic_headless,
                        "verify_game_runtime_bundle",
                        return_value=bundle,
                    ),
                    self.assertRaisesRegex(
                        GenericHeadlessError,
                        "evidence_stage_capability_invalid",
                    ),
                ):
                    verify_headless_evidence_set(
                        root / "crossed",
                        bundle_root=root,
                        _retained_stage_writer=writer,
                    )

            self.assertEqual(1, len(observed))
            capability = observed[0]
            self.assertIsInstance(
                capability,
                generic_runtime._RuntimeStageReadCapability,  # noqa: SLF001
            )
            assert isinstance(capability, generic_runtime._RuntimeStageReadCapability)  # noqa: SLF001
            self.assertEqual(root, capability.root)

            with (
                mock.patch.object(
                    generic_headless,
                    "verify_game_runtime_bundle",
                    return_value=bundle,
                ),
                self.assertRaisesRegex(
                    GenericHeadlessError,
                    "evidence_stage_capability_invalid",
                ),
            ):
                verify_headless_evidence_set(
                    root,
                    bundle_root=root,
                    _retained_stage_writer=writer,
                )

            with (
                mock.patch.object(
                    generic_headless,
                    "verify_game_runtime_bundle",
                    return_value=bundle,
                ),
                mock.patch.object(
                    generic_headless,
                    "_capture_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaises(_CaptureStopped),
            ):
                verify_headless_evidence_set(root, bundle_root=root)
            self.assertIsNone(observed[-1])

            with (
                mock.patch.object(
                    generic_headless,
                    "verify_game_runtime_bundle",
                    return_value=bundle,
                ),
                self.assertRaisesRegex(
                    GenericHeadlessError,
                    "evidence_stage_capability_invalid",
                ),
            ):
                verify_headless_evidence_set(
                    root,
                    bundle_root=root,
                    _retained_stage_writer=object(),  # type: ignore[arg-type]
                )

    def test_publication_scopes_exactly_three_stage_capabilities(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-stage-calls-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle.close()
            script_bytes = serialize_game_execution_script(script)
            script_path = root / "script.json"
            script_path.write_bytes(script_bytes)
            worker = root / "worker-evidence"
            published = root / "published-evidence"
            direct = root / "direct-evidence"
            original = generic_headless.verify_headless_evidence_set
            with (
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                mock.patch.object(
                    generic_headless,
                    "verify_headless_evidence_set",
                    wraps=original,
                ) as verify_calls,
            ):
                worker_verified = generic_headless.build_headless_evidence_tree(
                    worker,
                    bundle_root=bundle_root,
                    script_bytes=script_bytes,
                )
                worker_manifest = worker_verified.manifest
                worker_identity = worker_verified.root_identity
                worker_verified.close()
                published_verified = generic_headless.publish_headless_evidence_tree(
                    worker,
                    published,
                    bundle_root=bundle_root,
                    expected_content_hash=worker_manifest["content_hash"],
                    expected_tree_hash=worker_manifest["tree_hash"],
                    expected_source_identity=worker_identity,
                )
                published_verified.close()
                direct_verified = build_headless_evidence_set(
                    direct,
                    bundle_root=bundle_root,
                    script_path=script_path,
                )
                direct_verified.close()

            stage_calls = [
                call
                for call in verify_calls.call_args_list
                if call.kwargs.get("_retained_stage_writer") is not None
            ]
            self.assertEqual(3, len(stage_calls))
            for call in stage_calls:
                writer = call.kwargs["_retained_stage_writer"]
                self.assertIs(type(writer), RetainedStageWriter)
                self.assertEqual(Path(os.path.abspath(call.args[0])), writer.stage)
            for final_root in (worker, published, direct):
                self.assertTrue(
                    any(
                        Path(os.path.abspath(call.args[0])) == final_root
                        and call.kwargs.get("_retained_stage_writer") is None
                        for call in verify_calls.call_args_list
                    )
                )

            mutation_checks = 0
            real_authority = RetainedStageWriter._require_active_binding  # noqa: SLF001

            def reject_post_capture_mutation(
                writer: object,
                *,
                expected_stage: Path,
            ) -> None:
                nonlocal mutation_checks
                mutation_checks += 1
                real_authority(writer, expected_stage=expected_stage)
                if mutation_checks == 4:
                    raise DirectoryPublishError("retained stage binding changed")

            with (
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                mock.patch.object(
                    RetainedStageWriter,
                    "_require_active_binding",
                    side_effect=reject_post_capture_mutation,
                ),
                self.assertRaisesRegex(
                    GenericHeadlessError,
                    "evidence_stage_capability_invalid",
                ),
            ):
                generic_headless.build_headless_evidence_tree(
                    root / "mutated-evidence",
                    bundle_root=bundle_root,
                    script_bytes=script_bytes,
                )
            self.assertEqual(4, mutation_checks)

    def test_ready_publication_journal_recovers_exact_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-recovery-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle.close()
            script_path = root / "script.json"
            script_path.write_bytes(serialize_game_execution_script(script))
            destination = root / "headless-evidence"

            def crash(event: str, _relative: str | None) -> None:
                if event == "after_ready_journal_written":
                    raise RuntimeError("injected headless publication crash")

            with (
                self.assertRaisesRegex(RuntimeError, "injected headless publication crash"),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
            ):
                build_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                    script_path=script_path,
                    _publication_hook=crash,
                )
            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                recovered = recover_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                )
            self.assertIsNotNone(recovered)
            assert recovered is not None
            try:
                self.assertEqual(
                    recovered.manifest["runtime_evidence"]["execution_status"],
                    "headless_verified",
                )
            finally:
                recovered.close()
            self.assertFalse((root / ".headless-evidence.headless-evidence.journal.json").exists())

    def test_publication_lock_replacement_fails_before_destination_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-lock-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle.close()
            script_path = root / "script.json"
            script_path.write_bytes(serialize_game_execution_script(script))
            destination = root / "headless-evidence"
            lock_path = root / ".headless-evidence.headless-evidence.lock"

            def replace_lock(event: str, _relative: str | None) -> None:
                if event != "after_lock_acquired":
                    return
                replacement = root / "replacement.lock"
                replacement.write_bytes(b"\0")
                os.replace(replacement, lock_path)

            with (
                self.assertRaisesRegex(
                    GenericHeadlessError,
                    "evidence_publication_failed",
                ),
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
            ):
                build_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                    script_path=script_path,
                    _publication_hook=replace_lock,
                )
            self.assertFalse(destination.exists())

    def test_concurrent_identical_publication_converges_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-concurrent-") as temporary:
            root = Path(temporary)
            bundle, script = self._build_script("abstract-puzzle", root)
            bundle_root = bundle.root
            bundle.close()
            script_path = root / "script.json"
            script_path.write_bytes(serialize_game_execution_script(script))
            destination = root / "headless-evidence"

            def publish() -> tuple[str, tuple[int, int]]:
                verified = build_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                    script_path=script_path,
                )
                try:
                    return (
                        verified.manifest["content_hash"],
                        verified.root_identity,
                    )
                finally:
                    verified.close()

            with (
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                results = list(pool.map(lambda _index: publish(), range(2)))
            self.assertEqual(results[0], results[1])
            self.assertTrue(destination.is_dir())
            self.assertFalse((root / ".headless-evidence.headless-evidence.journal.json").exists())

    def test_cli_contract_failure_is_stderr_json_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="wf-headless-") as temporary:
            root = Path(temporary)
            missing = root / "missing"
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "worldforge",
                        "verify-game-headless",
                        str(missing),
                        str(missing),
                        "--output",
                        str(root / "evidence"),
                    ],
                ),
            ):
                code = main()
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
