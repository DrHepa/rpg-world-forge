from __future__ import annotations

import contextlib
import copy
import ctypes
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from scripts.generate_game_runtime_bundle_schema import build_schema
from worldforge import game_runtime_bundle as game_runtime_bundle_module
from worldforge import generic_runtime
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, main
from worldforge.creation_contracts import canonical_creation_hash, read_creation_object
from worldforge.directory_publish import DirectoryPublishError, RetainedStageWriter
from worldforge.game_runtime_bundle import (
    GAME_RUNTIME_BUNDLE_FORMAT,
    GAME_RUNTIME_BUNDLE_MANIFEST,
    GameRuntimeBundleError,
    build_game_runtime_bundle,
    build_game_runtime_bundle_from_objects,
    build_game_runtime_bundle_manifest,
    build_game_runtime_bundle_manifest_from_objects,
    recover_game_runtime_bundle,
    rollback_game_runtime_bundle,
    serialize_game_runtime_bundle,
    validate_game_runtime_bundle_document,
    verify_game_runtime_bundle,
)
from worldforge.gamepack import load_gamepack
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_runtime import capture_trusted_runtime_snapshot_files
from worldforge.integrity import canonical_json_bytes
from worldforge.repository_boundary import (
    repository_kind,
    require_standalone_game_runtime_bundle_root,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str, relative: str) -> Path:
    return ROOT / "examples" / "multigenre-contracts" / name / relative


@contextmanager
def _sealed_fixture(name: str, root: Path):
    source = _resolve_generic_assetpack_cli_source(_fixture(name, "assets/manifest.json"))
    verified = seal_generic_assetpack(root / f"{name}-assetpack", **source)
    try:
        yield verified
    finally:
        verified.close()


def _build_bundle(name: str, root: Path):
    with _sealed_fixture(name, root) as assetpack:
        return build_game_runtime_bundle(
            root / f"{name}-runtime-bundle",
            gamepack_path=_fixture(name, f"artifacts/{name}.gamepack.json"),
            inventory_path=_fixture(name, "assets/inventory.json"),
            assetpack_root=assetpack.root,
            snapshot_path=ROOT / "examples/multigenre-contracts/runtime/snapshot.json",
            registry_path=ROOT / "examples/multigenre-contracts/runtime/registry.json",
            composition_path=_fixture(name, "runtime/composition.json"),
            support_report_path=_fixture(name, "runtime/support-report.json"),
        )


def _publication_kwargs(name: str, assetpack_root: Path) -> dict[str, Path]:
    return {
        "gamepack_path": _fixture(name, f"artifacts/{name}.gamepack.json"),
        "inventory_path": _fixture(name, "assets/inventory.json"),
        "assetpack_root": assetpack_root,
        "snapshot_path": ROOT / "examples/multigenre-contracts/runtime/snapshot.json",
        "registry_path": ROOT / "examples/multigenre-contracts/runtime/registry.json",
        "composition_path": _fixture(name, "runtime/composition.json"),
        "support_report_path": _fixture(name, "runtime/support-report.json"),
    }


def _reseal_bundle_manifest(document: dict[str, object]) -> None:
    seed = {
        key: value for key, value in document.items() if key not in {"bundle_id", "content_hash"}
    }
    document["bundle_id"] = "game_runtime_bundle_" + canonical_creation_hash(seed)[:48]
    document["content_hash"] = canonical_creation_hash(document)


def _reseal_composition(document: dict[str, object]) -> None:
    seed = {
        key: document[key]
        for key in (
            "gamepack",
            "asset_inventory",
            "assetpack",
            "adapter",
            "registry",
            "runtime_snapshot",
            "platforms",
            "bindings",
        )
    }
    document["composition_id"] = "runtime_composition_" + canonical_creation_hash(seed)[:40]
    document["content_hash"] = canonical_creation_hash(document)


def _reseal_support(document: dict[str, object]) -> None:
    seed = {
        key: document[key]
        for key in (
            "gamepack",
            "composition",
            "adapter",
            "evidence",
            "dimensions",
            "compatibility_status",
            "mechanics",
            "features",
            "missing_capabilities",
            "reason_codes",
            "supported",
        )
    }
    document["report_id"] = "runtime_support_" + canonical_creation_hash(seed)[:40]
    document["content_hash"] = canonical_creation_hash(document)


def _replace_manifest_file(
    manifest: dict[str, object],
    relative: str,
    payload: bytes,
) -> None:
    files = manifest["files"]
    assert isinstance(files, list)
    record = {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    for index, existing in enumerate(files):
        if existing["path"] == relative:
            files[index] = record
            break
    else:
        files.append(record)
    files.sort(key=lambda item: item["path"].encode("utf-8"))
    runtime_records = [
        {
            "path": item["path"].removeprefix("runtime/snapshot-tree/"),
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in files
        if item["path"].startswith("runtime/snapshot-tree/")
    ]
    runtime_tree = manifest["runtime_snapshot_tree"]
    assert isinstance(runtime_tree, dict)
    runtime_tree["tree_hash"] = canonical_creation_hash({"files": runtime_records})
    runtime_tree["file_count"] = len(runtime_records)
    runtime_tree["total_bytes"] = sum(item["size_bytes"] for item in runtime_records)
    manifest["tree_hash"] = canonical_creation_hash({"files": files})
    _reseal_bundle_manifest(manifest)


class GameRuntimeBundleContractTests(unittest.TestCase):
    def test_windows_runtime_stage_capability_adds_only_write_sharing(self) -> None:
        root = Path("C:/retained/stage")
        capability = generic_runtime._RuntimeStageReadCapability(  # noqa: SLF001
            root=root,
            require_binding=lambda: None,
        )
        expected_access = (
            generic_runtime._WindowsRuntimeTreeApi._FILE_LIST_DIRECTORY  # noqa: SLF001
            | generic_runtime._WindowsRuntimeTreeApi._FILE_READ_ATTRIBUTES  # noqa: SLF001
            | generic_runtime._WindowsRuntimeTreeApi._SYNCHRONIZE  # noqa: SLF001
        )
        for active_capability, expected_share in (
            (None, 0x00000001),
            (capability, 0x00000003),
        ):
            with self.subTest(stage=active_capability is not None):
                calls: list[tuple[str, int, int]] = []
                api = object.__new__(generic_runtime._WindowsRuntimeTreeApi)  # noqa: SLF001
                api._invalid_handle = -1  # noqa: SLF001
                api._share_mode = api._share_mode_for(active_capability)  # noqa: SLF001

                def create_file(
                    _path: str,
                    access: int,
                    share: int,
                    *_args: object,
                    observed_calls: list[tuple[str, int, int]] = calls,
                ) -> int:
                    observed_calls.append(("path", access, share))
                    return 91

                def nt_create_file(
                    output: object,
                    access: int,
                    _attributes: object,
                    _io_status: object,
                    _allocation: object,
                    _file_attributes: int,
                    share: int,
                    _disposition: int,
                    _options: int,
                    _ea: object,
                    _ea_length: int,
                    observed_calls: list[tuple[str, int, int]] = calls,
                ) -> int:
                    ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 92
                    observed_calls.append(("relative", access, share))
                    return 0

                api._create_file = create_file  # type: ignore[attr-defined]  # noqa: SLF001
                api._nt_create_file = nt_create_file  # type: ignore[attr-defined]  # noqa: SLF001

                self.assertEqual(91, api.open_path_directory(root.parent))
                relative, status = api._nt_open(  # noqa: SLF001
                    91,
                    root.name,
                    directory=True,
                )
                self.assertEqual((92, 0), (relative, status))
                relative_file, file_status = api._nt_open(  # noqa: SLF001
                    91,
                    "payload.bin",
                    directory=False,
                )
                self.assertEqual((92, 0), (relative_file, file_status))
                self.assertEqual(
                    [
                        ("path", expected_access, expected_share),
                        ("relative", expected_access, expected_share),
                        ("relative", expected_access, expected_share),
                    ],
                    calls,
                )
                self.assertTrue(all(access & 0x40000000 == 0 for _, access, _ in calls))
                self.assertTrue(all(share & 0x00000004 == 0 for _, _, share in calls))

    def test_runtime_stage_capability_is_root_bound_and_mutation_sensitive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-stage-capability-") as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            (root / "kernel.py").write_bytes(b"VALUE = 1\n")
            binding_checks: list[int] = []
            capability = generic_runtime._RuntimeStageReadCapability(  # noqa: SLF001
                root=root,
                require_binding=lambda: binding_checks.append(len(binding_checks) + 1),
            )

            captured = generic_runtime._capture_runtime_files(  # noqa: SLF001
                root,
                _stage_capability=capability,
            )

            self.assertEqual(b"VALUE = 1\n", captured["gamepack_runtime/kernel.py"])
            self.assertEqual([1, 2], binding_checks)

            crossed = generic_runtime._RuntimeStageReadCapability(  # noqa: SLF001
                root=root / "other",
                require_binding=lambda: None,
            )
            with self.assertRaisesRegex(
                generic_runtime.RuntimeContractError,
                "runtime_snapshot_root_invalid",
            ):
                generic_runtime._capture_runtime_files(  # noqa: SLF001
                    root,
                    _stage_capability=crossed,
                )

            mutation_checks = 0

            def reject_mutation() -> None:
                nonlocal mutation_checks
                mutation_checks += 1
                if mutation_checks == 2:
                    raise DirectoryPublishError("retained stage binding changed")

            mutation_capability = generic_runtime._RuntimeStageReadCapability(  # noqa: SLF001
                root=root,
                require_binding=reject_mutation,
            )
            with self.assertRaisesRegex(DirectoryPublishError, "binding changed"):
                generic_runtime._capture_runtime_files(  # noqa: SLF001
                    root,
                    _stage_capability=mutation_capability,
                )
            self.assertEqual(2, mutation_checks)

    def test_bundle_verifier_accepts_stage_capability_only_from_its_writer(self) -> None:
        class _CaptureStopped(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="wf-runtime-stage-scope-") as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            writer = object.__new__(RetainedStageWriter)
            writer.stage = root
            require_binding = mock.Mock()
            writer.require_binding = require_binding  # type: ignore[method-assign]
            observed: list[object | None] = []

            def stop_capture(
                _root: Path,
                *,
                hook: object,
                retained_root_fd: int | None = None,
                stage_capability: object | None = None,
            ) -> object:
                del hook, retained_root_fd
                observed.append(stage_capability)
                raise _CaptureStopped

            with (
                mock.patch.object(
                    game_runtime_bundle_module,
                    "_capture_bundle_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaises(_CaptureStopped),
            ):
                verify_game_runtime_bundle(
                    root,
                    _retained_stage_writer=writer,
                )
            self.assertEqual(1, require_binding.call_count)
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
                    game_runtime_bundle_module,
                    "_capture_bundle_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaises(_CaptureStopped),
            ):
                verify_game_runtime_bundle(root)
            self.assertIsNone(observed[-1])

            with self.assertRaisesRegex(
                GameRuntimeBundleError,
                "game_runtime_bundle_stage_capability_invalid",
            ):
                verify_game_runtime_bundle(
                    root / "crossed",
                    _retained_stage_writer=writer,
                )

    def test_publication_scopes_stage_capability_away_from_published_verification(
        self,
    ) -> None:
        observed: list[tuple[Path, object | None]] = []
        original = game_runtime_bundle_module._capture_bundle_tree

        def capture(
            root: Path,
            *,
            hook: object,
            retained_root_fd: int | None = None,
            stage_capability: object | None = None,
        ) -> object:
            observed.append((root, stage_capability))
            return original(
                root,
                hook=hook,
                retained_root_fd=retained_root_fd,
                stage_capability=stage_capability,
            )

        with tempfile.TemporaryDirectory(prefix="wf-runtime-stage-call-scope-") as temporary:
            root = Path(temporary)
            with mock.patch.object(
                game_runtime_bundle_module,
                "_capture_bundle_tree",
                side_effect=capture,
            ):
                verified = _build_bundle("abstract-puzzle", root)
            try:
                destination = verified.root
            finally:
                verified.close()

        stage_calls = [item for item in observed if item[1] is not None]
        self.assertEqual(1, len(stage_calls))
        self.assertTrue(stage_calls[0][0].name.startswith(".abstract-puzzle-runtime-bundle."))
        self.assertTrue(
            any(path == destination and capability is None for path, capability in observed)
        )

    def test_object_input_api_matches_path_input_bytes_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified_assetpack:
                path_manifest, path_files = build_game_runtime_bundle_manifest(
                    **_publication_kwargs("abstract-puzzle", verified_assetpack.root)
                )
                object_manifest, object_files = build_game_runtime_bundle_manifest_from_objects(
                    gamepack=load_gamepack(
                        _fixture(
                            "abstract-puzzle",
                            "artifacts/abstract-puzzle.gamepack.json",
                        )
                    ),
                    inventory=read_creation_object(
                        _fixture("abstract-puzzle", "assets/inventory.json")
                    ),
                    assetpack=verified_assetpack.manifest,
                    assetpack_root=verified_assetpack.root,
                    snapshot=read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/snapshot.json"
                    ),
                    registry=read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/registry.json"
                    ),
                    composition=read_creation_object(
                        _fixture("abstract-puzzle", "runtime/composition.json")
                    ),
                    support_report=read_creation_object(
                        _fixture("abstract-puzzle", "runtime/support-report.json")
                    ),
                )

                self.assertEqual(path_manifest, object_manifest)
                self.assertEqual(dict(path_files), dict(object_files))
                self.assertEqual(path_manifest["content_hash"], object_manifest["content_hash"])
                self.assertEqual(path_manifest["tree_hash"], object_manifest["tree_hash"])

    def test_object_publication_binds_parent_identity_and_rejects_crossed_assetpack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified_assetpack:
                kwargs = {
                    "gamepack": load_gamepack(
                        _fixture(
                            "abstract-puzzle",
                            "artifacts/abstract-puzzle.gamepack.json",
                        )
                    ),
                    "inventory": read_creation_object(
                        _fixture("abstract-puzzle", "assets/inventory.json")
                    ),
                    "assetpack": verified_assetpack.manifest,
                    "assetpack_root": verified_assetpack.root,
                    "snapshot": read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/snapshot.json"
                    ),
                    "registry": read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/registry.json"
                    ),
                    "composition": read_creation_object(
                        _fixture("abstract-puzzle", "runtime/composition.json")
                    ),
                    "support_report": read_creation_object(
                        _fixture("abstract-puzzle", "runtime/support-report.json")
                    ),
                }
                destination = root / "object-runtime-bundle"
                parent_info = destination.parent.stat()
                verified = build_game_runtime_bundle_from_objects(
                    destination,
                    expected_parent_identity=(parent_info.st_dev, parent_info.st_ino),
                    **kwargs,
                )
                try:
                    self.assertEqual(
                        kwargs["gamepack"]["content_hash"],
                        verified.manifest["contracts"]["gamepack"]["content_hash"],
                    )
                finally:
                    verified.close()

                crossed = copy.deepcopy(kwargs["assetpack"])
                crossed["content_hash"] = "f" * 64
                with self.assertRaisesRegex(
                    GameRuntimeBundleError,
                    "assetpack",
                ):
                    build_game_runtime_bundle_manifest_from_objects(
                        **{**kwargs, "assetpack": crossed}
                    )
                mismatched_destination = root / "mismatched-parent-runtime-bundle"
                with self.assertRaisesRegex(
                    GameRuntimeBundleError,
                    "parent identity",
                ):
                    build_game_runtime_bundle_from_objects(
                        mismatched_destination,
                        expected_parent_identity=(parent_info.st_dev, parent_info.st_ino + 1),
                        **kwargs,
                    )
                self.assertFalse(mismatched_destination.exists())

    def test_schema_is_generated_and_accepts_the_built_manifest(self) -> None:
        schema_path = ROOT / "schemas/game-runtime-bundle.schema.json"
        self.assertEqual(
            json.loads(schema_path.read_text(encoding="utf-8")),
            build_schema(),
        )
        schema = build_schema()
        self.assertEqual(
            schema["properties"]["format"]["const"],
            GAME_RUNTIME_BUNDLE_FORMAT,
        )
        self.assertTrue(schema["properties"]["legal"]["properties"]["asset_notices"]["uniqueItems"])
        self.assertEqual(
            schema["properties"]["state"]["const"],
            "pre_execution",
        )

    def test_trusted_snapshot_capture_returns_exact_snapshot_bytes(self) -> None:
        snapshot = json.loads(
            (ROOT / "examples/multigenre-contracts/runtime/snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        registry = json.loads(
            (ROOT / "examples/multigenre-contracts/runtime/registry.json").read_text(
                encoding="utf-8"
            )
        )

        files = capture_trusted_runtime_snapshot_files(
            snapshot=snapshot,
            registry=registry,
        )

        self.assertEqual(
            sorted(files, key=lambda item: item.encode("utf-8")),
            [item["path"] for item in snapshot["files"]],
        )
        self.assertEqual(
            [
                {
                    "path": path,
                    "sha256": hashlib.sha256(files[path]).hexdigest(),
                    "size_bytes": len(files[path]),
                }
                for path in sorted(files, key=lambda item: item.encode("utf-8"))
            ],
            snapshot["files"],
        )
        with self.assertRaises(TypeError):
            files["gamepack_runtime/evil.py"] = b"evil"  # type: ignore[index]

    def test_build_and_verify_puzzle_bundle_is_runtime_only_and_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
            root = Path(temporary)
            verified = _build_bundle("abstract-puzzle", root)
            try:
                manifest = verified.manifest
                self.assertEqual(manifest["format"], GAME_RUNTIME_BUNDLE_FORMAT)
                self.assertEqual(manifest["format_version"], 1)
                self.assertEqual(manifest["state"], "pre_execution")
                self.assertEqual(
                    verified.evidence,
                    {
                        "integrity": "valid",
                        "state": "pre_execution",
                        "release": "blocked",
                        "supported": False,
                        "bundle_id": manifest["bundle_id"],
                        "content_hash": manifest["content_hash"],
                    },
                )
                self.assertEqual(
                    manifest,
                    validate_game_runtime_bundle_document(manifest),
                )
                self.assertEqual(
                    verified.read_bytes(GAME_RUNTIME_BUNDLE_MANIFEST),
                    serialize_game_runtime_bundle(manifest),
                )
                paths = set(verified.files)
                self.assertIn("contracts/gamepack.json", paths)
                self.assertIn("assetpack/assetpack.json", paths)
                self.assertIn(
                    "runtime/snapshot-tree/gamepack_runtime/session.py",
                    paths,
                )
                self.assertIn("licenses/world-forge-mit.txt", paths)
                self.assertNotIn("contracts/asset-inventory.json", paths)
                joined = b"\n".join(verified.files.values()).lower()
                for forbidden in (
                    b"provider_sdk",
                    b"authoring_prompt",
                    b"runtime_evidence",
                ):
                    self.assertNotIn(forbidden, joined)
            finally:
                verified.close()

            checked = verify_game_runtime_bundle(root / "abstract-puzzle-runtime-bundle")
            try:
                self.assertEqual(checked.evidence["integrity"], "valid")
                self.assertFalse(checked.evidence["supported"])
                self.assertEqual(checked.evidence["release"], "blocked")
            finally:
                checked.close()

    def test_bundle_is_deterministic_for_puzzle_and_narrative(self) -> None:
        for fixture in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(fixture=fixture):
                with (
                    tempfile.TemporaryDirectory(prefix="wf-bundle-a-") as first,
                    tempfile.TemporaryDirectory(prefix="wf-bundle-b-") as second,
                ):
                    first_verified = _build_bundle(fixture, Path(first))
                    second_verified = _build_bundle(fixture, Path(second))
                    try:
                        self.assertEqual(
                            serialize_game_runtime_bundle(first_verified.manifest),
                            serialize_game_runtime_bundle(second_verified.manifest),
                        )
                        self.assertEqual(
                            dict(first_verified.files),
                            dict(second_verified.files),
                        )
                    finally:
                        first_verified.close()
                        second_verified.close()

    def test_structural_validator_rejects_resealed_state_and_internal_hash_overclaims(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
            verified = _build_bundle("abstract-puzzle", Path(temporary))
            try:
                baseline = verified.manifest
            finally:
                verified.close()

        for mutation in ("state", "tree_hash", "binding_hash"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(baseline)
                if mutation == "state":
                    document["state"] = "ready"
                elif mutation == "tree_hash":
                    document["tree_hash"] = "f" * 64
                else:
                    document["bindings"][0]["sha256"] = "f" * 64
                seed = {
                    key: value
                    for key, value in document.items()
                    if key not in {"bundle_id", "content_hash"}
                }
                document["bundle_id"] = "game_runtime_bundle_" + canonical_creation_hash(seed)[:48]
                document["content_hash"] = canonical_creation_hash(document)
                with self.assertRaises(GameRuntimeBundleError):
                    validate_game_runtime_bundle_document(document)

    def test_structural_validator_requires_strict_semver_and_unique_legal_notices(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
            verified = _build_bundle("abstract-puzzle", Path(temporary))
            try:
                baseline = verified.manifest
            finally:
                verified.close()

        invalid_semver = copy.deepcopy(baseline)
        adapter = invalid_semver["contracts"]["runtime_adapter"]
        previous_path = adapter["path"]
        adapter["adapter_version"] = "0"
        adapter["path"] = previous_path.rsplit("@", 1)[0] + "@0.json"
        descriptor = next(item for item in invalid_semver["files"] if item["path"] == previous_path)
        descriptor["path"] = adapter["path"]
        invalid_semver["files"].sort(key=lambda item: item["path"].encode("utf-8"))
        runtime_records = [
            {
                **item,
                "path": item["path"].removeprefix("runtime/snapshot-tree/"),
            }
            for item in invalid_semver["files"]
            if item["path"].startswith("runtime/snapshot-tree/")
        ]
        invalid_semver["runtime_snapshot_tree"]["tree_hash"] = canonical_creation_hash(
            {"files": runtime_records}
        )
        invalid_semver["tree_hash"] = canonical_creation_hash({"files": invalid_semver["files"]})
        _reseal_bundle_manifest(invalid_semver)
        with self.assertRaises(GameRuntimeBundleError):
            validate_game_runtime_bundle_document(invalid_semver)

        duplicate_notice = copy.deepcopy(baseline)
        duplicate_notice["legal"]["asset_notices"].append(
            copy.deepcopy(duplicate_notice["legal"]["asset_notices"][0])
        )
        _reseal_bundle_manifest(duplicate_notice)
        with self.assertRaises(GameRuntimeBundleError):
            validate_game_runtime_bundle_document(duplicate_notice)

    def test_integral_verifier_rejects_resealed_crossed_contract_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
            root = Path(temporary)
            verified = _build_bundle("abstract-puzzle", root)
            destination = verified.root
            manifest = verified.manifest
            verified.close()
            manifest["contracts"]["runtime_support_report"]["content_hash"] = "f" * 64
            seed = {
                key: value
                for key, value in manifest.items()
                if key not in {"bundle_id", "content_hash"}
            }
            manifest["bundle_id"] = "game_runtime_bundle_" + canonical_creation_hash(seed)[:48]
            manifest["content_hash"] = canonical_creation_hash(manifest)
            (destination / GAME_RUNTIME_BUNDLE_MANIFEST).write_bytes(
                serialize_game_runtime_bundle(manifest)
            )
            with self.assertRaises(GameRuntimeBundleError):
                verify_game_runtime_bundle(destination)

    def test_integral_verifier_reconstructs_the_exact_allowed_file_closure(self) -> None:
        for relative in (
            "authoring/provider.json",
            "evidence/native.json",
            "runtime/unclassified.json",
        ):
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-closure-") as temporary,
            ):
                root = Path(temporary)
                verified = _build_bundle("abstract-puzzle", root)
                destination = verified.root
                manifest = verified.manifest
                verified.close()
                payload = canonical_json_bytes({"classification": "neither"})
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                _replace_manifest_file(manifest, relative, payload)
                manifest_payload = serialize_game_runtime_bundle(manifest)
                (destination / GAME_RUNTIME_BUNDLE_MANIFEST).write_bytes(manifest_payload)
                self.assertEqual(
                    validate_game_runtime_bundle_document(manifest)["content_hash"],
                    manifest["content_hash"],
                )
                with self.assertRaisesRegex(
                    GameRuntimeBundleError,
                    "game_runtime_bundle_tree_mismatch",
                ):
                    verify_game_runtime_bundle(destination)

    def test_integral_verifier_rejects_crossed_d3_lineage_with_retained_hashes(
        self,
    ) -> None:
        for field in ("assetpack", "asset_inventory"):
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-lineage-") as temporary,
            ):
                root = Path(temporary)
                verified = _build_bundle("abstract-puzzle", root)
                destination = verified.root
                manifest = verified.manifest
                verified.close()
                composition_path = destination / "contracts/runtime-composition.json"
                composition = json.loads(composition_path.read_text(encoding="utf-8"))
                composition[field]["id"] = f"{field}_crossed"
                _reseal_composition(composition)
                composition_payload = canonical_json_bytes(composition)
                composition_path.write_bytes(composition_payload)

                support_path = destination / "status/runtime-support-report.json"
                support = json.loads(support_path.read_text(encoding="utf-8"))
                support["composition"] = {
                    "format": composition["format"],
                    "format_version": composition["format_version"],
                    "id": composition["composition_id"],
                    "content_hash": composition["content_hash"],
                }
                _reseal_support(support)
                support_payload = canonical_json_bytes(support)
                support_path.write_bytes(support_payload)

                manifest["contracts"]["runtime_composition"]["id"] = composition["composition_id"]
                manifest["contracts"]["runtime_composition"]["content_hash"] = composition[
                    "content_hash"
                ]
                manifest["contracts"]["runtime_support_report"]["id"] = support["report_id"]
                manifest["contracts"]["runtime_support_report"]["content_hash"] = support[
                    "content_hash"
                ]
                _replace_manifest_file(
                    manifest,
                    "contracts/runtime-composition.json",
                    composition_payload,
                )
                _replace_manifest_file(
                    manifest,
                    "status/runtime-support-report.json",
                    support_payload,
                )
                (destination / GAME_RUNTIME_BUNDLE_MANIFEST).write_bytes(
                    serialize_game_runtime_bundle(manifest)
                )
                with self.assertRaisesRegex(
                    GameRuntimeBundleError,
                    "game_runtime_bundle_composition_mismatch",
                ):
                    verify_game_runtime_bundle(destination)

    def test_integral_verifier_rejects_nested_asset_runtime_and_legal_tamper(self) -> None:
        mutations = (
            "assetpack/assets/ui/board.png",
            "runtime/snapshot-tree/gamepack_runtime/session.py",
            "licenses/world-forge-mit.txt",
        )
        for relative in mutations:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
                    root = Path(temporary)
                    verified = _build_bundle("abstract-puzzle", root)
                    destination = verified.root
                    verified.close()
                    payload = (destination / relative).read_bytes()
                    (destination / relative).write_bytes(payload + b"\0")
                    with self.assertRaises(GameRuntimeBundleError):
                        verify_game_runtime_bundle(destination)

    def test_integral_verifier_rejects_links_hardlinks_extras_and_empty_directories(
        self,
    ) -> None:
        cases = ("symlink", "hardlink", "extra", "empty_directory", "pycache")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
                    root = Path(temporary)
                    verified = _build_bundle("abstract-puzzle", root)
                    destination = verified.root
                    verified.close()
                    source = destination / "contracts/gamepack.json"
                    if case == "symlink":
                        path = destination / "evil-link"
                        try:
                            path.symlink_to(source)
                        except OSError:
                            self.skipTest("symlink creation is unavailable")
                    elif case == "hardlink":
                        path = destination / "evil-hardlink"
                        try:
                            os.link(source, path)
                        except OSError:
                            self.skipTest("hardlink creation is unavailable")
                    elif case == "extra":
                        (destination / "extra.bin").write_bytes(b"x")
                    elif case == "empty_directory":
                        (destination / "empty").mkdir()
                    else:
                        cache = destination / "runtime/snapshot-tree/__pycache__"
                        cache.mkdir()
                        (cache / "evil.pyc").write_bytes(b"x")
                    with self.assertRaises(GameRuntimeBundleError):
                        verify_game_runtime_bundle(destination)

    def test_build_rejects_evidence_bearing_or_overclaiming_support(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-") as temporary:
            root = Path(temporary)
            support_path = root / "support.json"
            original = json.loads(
                _fixture("abstract-puzzle", "runtime/support-report.json").read_text(
                    encoding="utf-8"
                )
            )
            for mutation in ("supported", "release", "execution", "evidence"):
                with self.subTest(mutation=mutation):
                    support = copy.deepcopy(original)
                    if mutation == "supported":
                        support["supported"] = True
                    elif mutation == "release":
                        support["dimensions"]["release"] = "ready"
                    elif mutation == "execution":
                        support["dimensions"]["execution"][0]["status"] = "native_verified"
                    else:
                        support["evidence"] = [
                            {
                                "format": "world-forge.runtime_evidence",
                                "format_version": 1,
                                "id": "runtime_evidence_fake",
                                "content_hash": "f" * 64,
                                "platform": support["dimensions"]["execution"][0]["platform"],
                                "execution_status": "native_verified",
                                "packaging_status": "verified",
                                "passed_check_kinds": ["native"],
                            }
                        ]
                    support["content_hash"] = canonical_creation_hash(support)
                    support_path.write_text(
                        json.dumps(support, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with _sealed_fixture("abstract-puzzle", root) as assetpack:
                        with self.assertRaises(GameRuntimeBundleError):
                            build_game_runtime_bundle(
                                root / f"bundle-{mutation}",
                                gamepack_path=_fixture(
                                    "abstract-puzzle",
                                    "artifacts/abstract-puzzle.gamepack.json",
                                ),
                                inventory_path=_fixture(
                                    "abstract-puzzle",
                                    "assets/inventory.json",
                                ),
                                assetpack_root=assetpack.root,
                                snapshot_path=ROOT
                                / "examples/multigenre-contracts/runtime/snapshot.json",
                                registry_path=ROOT
                                / "examples/multigenre-contracts/runtime/registry.json",
                                composition_path=_fixture(
                                    "abstract-puzzle",
                                    "runtime/composition.json",
                                ),
                                support_report_path=support_path,
                            )

    def test_cli_build_verify_recover_and_rollback_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-cli-") as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as assetpack:
                destination = root / "bundle"
                argv = [
                    "worldforge",
                    "build-game-runtime-bundle",
                    str(
                        _fixture(
                            "abstract-puzzle",
                            "artifacts/abstract-puzzle.gamepack.json",
                        )
                    ),
                    str(_fixture("abstract-puzzle", "assets/inventory.json")),
                    str(assetpack.root),
                    "--snapshot",
                    str(ROOT / "examples/multigenre-contracts/runtime/snapshot.json"),
                    "--registry",
                    str(ROOT / "examples/multigenre-contracts/runtime/registry.json"),
                    "--composition",
                    str(_fixture("abstract-puzzle", "runtime/composition.json")),
                    "--support-report",
                    str(_fixture("abstract-puzzle", "runtime/support-report.json")),
                    "--output",
                    str(destination),
                ]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch("sys.argv", argv),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(main(), 0)
                result = json.loads(stdout.getvalue())
                self.assertEqual(result["integrity"], "valid")
                self.assertEqual(result["state"], "pre_execution")
                self.assertEqual(result["release"], "blocked")
                self.assertFalse(result["supported"])
                self.assertEqual(stderr.getvalue(), "")

                stdout = io.StringIO()
                with (
                    mock.patch(
                        "sys.argv",
                        [
                            "worldforge",
                            "verify-game-runtime-bundle",
                            str(destination),
                        ],
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(main(), 0)
                self.assertEqual(
                    json.loads(stdout.getvalue())["content_hash"],
                    result["content_hash"],
                )

                with (
                    mock.patch(
                        "sys.argv",
                        [
                            "worldforge",
                            "recover-game-runtime-bundle",
                            str(destination),
                        ],
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as recovered_stdout,
                ):
                    self.assertEqual(main(), 0)
                self.assertEqual(
                    json.loads(recovered_stdout.getvalue())["status"],
                    "verified",
                )

                self.assertEqual(
                    rollback_game_runtime_bundle(root / "not-created"),
                    {"status": "no_operation"},
                )
                recovered = recover_game_runtime_bundle(destination)
                self.assertIsNotNone(recovered)
                assert recovered is not None
                recovered.close()

    def test_publish_collision_never_replaces_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-collision-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            destination.mkdir()
            marker = destination / "owner.txt"
            marker.write_text("other owner", encoding="utf-8")
            with _sealed_fixture("abstract-puzzle", root) as assetpack:
                with self.assertRaises(GameRuntimeBundleError):
                    build_game_runtime_bundle(
                        destination,
                        gamepack_path=_fixture(
                            "abstract-puzzle",
                            "artifacts/abstract-puzzle.gamepack.json",
                        ),
                        inventory_path=_fixture(
                            "abstract-puzzle",
                            "assets/inventory.json",
                        ),
                        assetpack_root=assetpack.root,
                        snapshot_path=ROOT / "examples/multigenre-contracts/runtime/snapshot.json",
                        registry_path=ROOT / "examples/multigenre-contracts/runtime/registry.json",
                        composition_path=_fixture(
                            "abstract-puzzle",
                            "runtime/composition.json",
                        ),
                        support_report_path=_fixture(
                            "abstract-puzzle",
                            "runtime/support-report.json",
                        ),
                    )
            self.assertEqual(marker.read_text(encoding="utf-8"), "other owner")
            self.assertEqual(set(destination.iterdir()), {marker})

    def test_repository_boundary_classifies_only_an_external_integral_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-boundary-") as temporary:
            verified = _build_bundle("abstract-puzzle", Path(temporary))
            destination = verified.root
            verified.close()

            self.assertEqual(repository_kind(destination), "game_runtime_bundle")
            self.assertEqual(
                require_standalone_game_runtime_bundle_root(destination),
                destination.resolve(),
            )
            (destination / GAME_RUNTIME_BUNDLE_MANIFEST).unlink()
            self.assertIsNone(repository_kind(destination))

    def test_publication_failure_leaves_recoverable_identity_bound_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-crash-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            with _sealed_fixture("abstract-puzzle", root) as assetpack:
                with self.assertRaisesRegex(RuntimeError, "injected crash"):
                    build_game_runtime_bundle(
                        destination,
                        gamepack_path=_fixture(
                            "abstract-puzzle",
                            "artifacts/abstract-puzzle.gamepack.json",
                        ),
                        inventory_path=_fixture(
                            "abstract-puzzle",
                            "assets/inventory.json",
                        ),
                        assetpack_root=assetpack.root,
                        snapshot_path=ROOT / "examples/multigenre-contracts/runtime/snapshot.json",
                        registry_path=ROOT / "examples/multigenre-contracts/runtime/registry.json",
                        composition_path=_fixture(
                            "abstract-puzzle",
                            "runtime/composition.json",
                        ),
                        support_report_path=_fixture(
                            "abstract-puzzle",
                            "runtime/support-report.json",
                        ),
                        _publication_hook=lambda event, _relative: (
                            (_ for _ in ()).throw(RuntimeError("injected crash"))
                            if event == "before_destination_publish"
                            else None
                        ),
                    )
                journal = root / ".bundle.game-runtime-bundle.journal.json"
                self.assertTrue(journal.is_file())
                recovered = recover_game_runtime_bundle(destination)
                self.assertIsNotNone(recovered)
                assert recovered is not None
                try:
                    self.assertEqual(recovered.evidence["integrity"], "valid")
                finally:
                    recovered.close()
                self.assertFalse(journal.exists())

    def test_publication_journal_crash_boundaries_recover_or_roll_back_safely(
        self,
    ) -> None:
        cases = (
            ("after_intent_journal_written", "recover_empty"),
            ("after_copying_journal_written", "recover_empty"),
            ("after_manifest_written", "rollback_partial"),
            ("after_ready_journal_written", "recover_complete"),
            ("before_journal_remove", "recover_complete"),
        )
        for event_name, expected_action in cases:
            with (
                self.subTest(event=event_name),
                tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-boundary-") as temporary,
            ):
                root = Path(temporary)
                destination = root / "bundle"
                with _sealed_fixture("abstract-puzzle", root) as assetpack:

                    def crash(
                        event: str,
                        relative: str | None,
                        *,
                        expected_event: str = event_name,
                    ) -> None:
                        matches_manifest = (
                            expected_event == "after_manifest_written"
                            and event == "after_stage_file_write"
                            and relative == GAME_RUNTIME_BUNDLE_MANIFEST
                        )
                        if event == expected_event or matches_manifest:
                            raise RuntimeError(f"injected crash at {expected_event}")

                    expected_error = (
                        GameRuntimeBundleError
                        if event_name == "before_journal_remove"
                        else RuntimeError
                    )
                    expected_message = (
                        "game_runtime_bundle_publication_indeterminate"
                        if event_name == "before_journal_remove"
                        else f"injected crash at {event_name}"
                    )
                    with self.assertRaisesRegex(
                        expected_error,
                        expected_message,
                    ):
                        build_game_runtime_bundle(
                            destination,
                            **_publication_kwargs(
                                "abstract-puzzle",
                                assetpack.root,
                            ),
                            _publication_hook=crash,
                        )

                journal = root / ".bundle.game-runtime-bundle.journal.json"
                self.assertTrue(journal.is_file())
                retained_cleanup = (
                    sys.platform.startswith("linux")
                    and os.name == "posix"
                    and expected_action in {"recover_empty", "rollback_partial"}
                    and event_name != "after_intent_journal_written"
                )
                if retained_cleanup:
                    expected_code = (
                        "game_runtime_bundle_rollback_recovery_required"
                        if expected_action == "rollback_partial"
                        else "game_runtime_bundle_recovery_required"
                    )
                    operation = (
                        rollback_game_runtime_bundle
                        if expected_action == "rollback_partial"
                        else recover_game_runtime_bundle
                    )
                    with self.assertRaises(GameRuntimeBundleError) as raised:
                        operation(destination)
                    self.assertEqual(expected_code, raised.exception.reason_code)
                    self.assertIn("retained", raised.exception.detail)
                    self.assertTrue(journal.is_file())
                    retained_stages = tuple(root.glob(".bundle.game-runtime-bundle-*"))
                    self.assertEqual(1, len(retained_stages))
                    self.assertEqual(
                        retained_stages[0].name,
                        raised.exception.recovery_evidence["stage"]["locator"],
                    )
                    self.assertEqual(
                        journal.name,
                        raised.exception.recovery_evidence["journal"]["locator"],
                    )
                    self.assertTrue(retained_stages[0].is_dir())
                    continue
                if expected_action == "rollback_partial":
                    result = rollback_game_runtime_bundle(destination)
                    self.assertEqual(result["status"], "rolled_back")
                    self.assertFalse(destination.exists())
                else:
                    recovered = recover_game_runtime_bundle(destination)
                    if expected_action == "recover_empty":
                        self.assertIsNone(recovered)
                        self.assertFalse(destination.exists())
                    else:
                        self.assertIsNotNone(recovered)
                        assert recovered is not None
                        try:
                            self.assertEqual(recovered.evidence["integrity"], "valid")
                        finally:
                            recovered.close()
                        self.assertTrue(destination.is_dir())
                self.assertFalse(journal.exists())
                self.assertFalse(
                    any(
                        child.name.startswith(
                            ".bundle.game-runtime-bundle-",
                        )
                        for child in root.iterdir()
                    )
                )

    def test_stage_creation_crash_remains_explicitly_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-stage-created-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            with _sealed_fixture("abstract-puzzle", root) as assetpack:

                def crash(event: str, _relative: str | None) -> None:
                    if event == "after_stage_created":
                        raise RuntimeError("injected crash after stage creation")

                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected crash after stage creation",
                ):
                    build_game_runtime_bundle(
                        destination,
                        **_publication_kwargs("abstract-puzzle", assetpack.root),
                        _publication_hook=crash,
                    )

            journal = root / ".bundle.game-runtime-bundle.journal.json"
            stages = tuple(root.glob(".bundle.game-runtime-bundle-*"))
            self.assertTrue(journal.is_file())
            self.assertEqual(len(stages), 1)
            with self.assertRaisesRegex(
                GameRuntimeBundleError,
                "game_runtime_bundle_recovery_ambiguous",
            ):
                recover_game_runtime_bundle(destination)
            with self.assertRaisesRegex(
                GameRuntimeBundleError,
                "game_runtime_bundle_rollback_ambiguous",
            ):
                rollback_game_runtime_bundle(destination)
            self.assertTrue(journal.is_file())
            self.assertTrue(stages[0].is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "requires native Linux publication semantics",
    )
    def test_publication_finalization_fails_indeterminate_on_late_namespace_changes(
        self,
    ) -> None:
        for mutation in (
            "destination_tree",
            "stage_reappears",
            "lock_replaced",
            "journal_replaced",
            "journal_reappears",
            "destination_displaced",
        ):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory(prefix="wf-runtime-bundle-finalization-") as temporary,
            ):
                root = Path(temporary)
                destination = root / "bundle"
                stage_name: str | None = None
                with _sealed_fixture("abstract-puzzle", root) as assetpack:

                    def mutate(
                        event: str,
                        _relative: str | None,
                        *,
                        case: str = mutation,
                        case_root: Path = root,
                        case_destination: Path = destination,
                    ) -> None:
                        nonlocal stage_name
                        if event == "after_stage_created":
                            stage_name = next(
                                child.name
                                for child in case_root.iterdir()
                                if child.name.startswith(".bundle.game-runtime-bundle-")
                            )
                        if event == "before_journal_remove":
                            if case == "destination_tree":
                                (case_destination / "late.bin").write_bytes(b"late")
                            elif case == "stage_reappears":
                                assert stage_name is not None
                                (case_root / stage_name).mkdir()
                            elif case == "lock_replaced":
                                lock = case_root / ".bundle.game-runtime-bundle.lock"
                                lock.unlink()
                                lock.write_bytes(b"replacement")
                            elif case == "journal_replaced":
                                journal = case_root / ".bundle.game-runtime-bundle.journal.json"
                                journal.unlink()
                                journal.write_bytes(b"replacement")
                        if event == "after_journal_remove":
                            if case == "journal_reappears":
                                journal = case_root / ".bundle.game-runtime-bundle.journal.json"
                                journal.write_bytes(b"replacement")
                            elif case == "destination_displaced":
                                case_destination.rename(case_root / "displaced-bundle")
                                case_destination.mkdir()

                    with self.assertRaisesRegex(
                        GameRuntimeBundleError,
                        "game_runtime_bundle_publication_indeterminate",
                    ):
                        build_game_runtime_bundle(
                            destination,
                            **_publication_kwargs(
                                "abstract-puzzle",
                                assetpack.root,
                            ),
                            _publication_hook=mutate,
                        )


if __name__ == "__main__":
    unittest.main()
