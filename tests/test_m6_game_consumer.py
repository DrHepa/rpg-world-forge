from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import isoworld.content.assetpack as runtime_assetpack_module
import isoworld.content.composed_catalog as composed_catalog_module
import isoworld.content.gltf as gltf_module
import isoworld.content.resource_snapshot as resource_snapshot_module
import worldforge.composed_bundle as composed_bundle_module
import worldforge.composed_game as composed_game_module
import worldforge.directory_publish as directory_publish_module
import worldforge.game_control_io as game_control_io_module
from isoworld.content.composed_catalog import (
    CATALOG_GENERATION_NAME,
    CATALOG_GENERATIONS_RELATIVE_PATH,
    ComposedCatalogError,
    load_composed_catalog,
    load_composed_catalog_state,
    validate_cross_catalog_world_hashes,
    verify_composed_release,
)
from isoworld.content.loader import load_worldpack
from isoworld.content.publication_journal import (
    audit_publication_journals,
    canonical_journal_record,
    journal_frame,
)
from isoworld.render.composition_plan import build_composition_plan
from isoworld.render.pyray_2_5d import PYRAY_2_5D_REGISTRY
from isoworld.runtime_adapter import RuntimeAdapterKey, StaticRuntimeAdapterRegistry
from tests.test_m6_pyray_2_5d import _composition
from worldforge.assetpack import build_assetpack
from worldforge.composed_bundle import build_composed_runtime_bundle
from worldforge.composed_game import ComposedGameError, import_composed_bundle
from worldforge.game_boundary import audit_game_repository
from worldforge.game_scaffold import create_game_project
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import build_renderpack
from worldforge.runtime_composition import RUNTIME_CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"
M6_FIXTURES = ROOT / "examples/m6-contracts"


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    return environment


class M6GameConsumerTests(unittest.TestCase):
    @staticmethod
    def _reseal_catalog_tip(
        game: Path,
        mutation: Callable[[dict[str, Any]], None],
    ):
        state = load_composed_catalog_state(game)
        generation = game / CATALOG_GENERATIONS_RELATIVE_PATH / state.head_hash
        catalog_path = generation / CATALOG_GENERATION_NAME
        catalog = json.loads(catalog_path.read_bytes())
        mutation(catalog)
        catalog["content_hash"] = canonical_payload_hash(catalog)
        replacement = generation.with_name(catalog["content_hash"])
        catalog_path.write_bytes(canonical_json_bytes(catalog))
        generation.rename(replacement)
        return load_composed_catalog(game)[0]

    @staticmethod
    def _reseal_catalog_tip_unchecked(
        game: Path,
        mutation: Callable[[dict[str, Any]], None],
    ) -> None:
        state = load_composed_catalog_state(game)
        generation = game / CATALOG_GENERATIONS_RELATIVE_PATH / state.head_hash
        catalog_path = generation / CATALOG_GENERATION_NAME
        catalog = json.loads(catalog_path.read_bytes())
        mutation(catalog)
        catalog["content_hash"] = canonical_payload_hash(catalog)
        replacement = generation.with_name(catalog["content_hash"])
        catalog_path.write_bytes(canonical_json_bytes(catalog))
        generation.rename(replacement)

    @staticmethod
    def _forge_installed_contract(
        game: Path,
        relative: str,
        mutation: Callable[[dict[str, Any]], None],
    ):
        contract = {
            "contracts/runtime-composition.json": (
                "runtime_composition",
                "composition_hash",
            ),
            "contracts/runtime-presentation-profile.json": (
                "presentation_profile",
                "profile_hash",
            ),
            "contracts/runtime-capability-catalog.json": (
                "capability_catalog",
                None,
            ),
            "contracts/runtime-adapter.json": (
                "runtime_adapter",
                "adapter_hash",
            ),
        }[relative]
        release = load_composed_catalog(game)[0]
        bundle_root = game / release.path
        document_path = bundle_root / relative
        document = json.loads(document_path.read_bytes())
        mutation(document)
        document["content_hash"] = canonical_payload_hash(document)
        document_bytes = canonical_json_bytes(document)
        document_path.write_bytes(document_bytes)

        manifest_path = bundle_root / "composed-bundle.manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        for record in manifest["files"]:
            if record["path"] == relative:
                record["sha256"] = hashlib.sha256(document_bytes).hexdigest()
                record["size"] = len(document_bytes)
                break
        else:
            raise AssertionError(f"contract record is absent: {relative}")
        manifest["contracts"][contract[0]]["content_hash"] = document["content_hash"]
        if contract[0] == "runtime_composition":
            manifest["compatibility_evidence"]["composition_hash"] = document["content_hash"]
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        def update_catalog(catalog: dict[str, Any]) -> None:
            if contract[1] is not None:
                catalog["entries"][0][contract[1]] = document["content_hash"]
            catalog["entries"][0]["bundle_hash"] = manifest["bundle_hash"]

        return M6GameConsumerTests._reseal_catalog_tip(game, update_catalog)

    @classmethod
    def _forge_installed_composition(
        cls,
        game: Path,
        mutation: Callable[[dict[str, Any]], None],
    ):
        return cls._forge_installed_contract(
            game,
            "contracts/runtime-composition.json",
            mutation,
        )

    @classmethod
    def _forge_manifest_and_payload(
        cls,
        game: Path,
        *,
        payload_relative: str | None = None,
        payload_mutation: Callable[[dict[str, Any]], None] | None = None,
        manifest_mutation: Callable[[dict[str, Any]], None] | None = None,
    ):
        release = load_composed_catalog(game)[0]
        bundle_root = game / release.path
        manifest_path = bundle_root / "composed-bundle.manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        if payload_relative is not None:
            payload_path = bundle_root / payload_relative
            payload = json.loads(payload_path.read_bytes())
            assert payload_mutation is not None
            payload_mutation(payload)
            payload["content_hash"] = canonical_payload_hash(payload)
            payload_bytes = canonical_json_bytes(payload)
            payload_path.write_bytes(payload_bytes)
            for record in manifest["files"]:
                if record["path"] == payload_relative:
                    record["sha256"] = hashlib.sha256(payload_bytes).hexdigest()
                    record["size"] = len(payload_bytes)
                    break
            else:
                raise AssertionError(f"payload record is absent: {payload_relative}")
            if payload_relative == "evidence/runtime-compatibility-report.json":
                manifest["compatibility_evidence"]["content_hash"] = payload["content_hash"]
        if manifest_mutation is not None:
            manifest_mutation(manifest)
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        return cls._reseal_catalog_tip(
            game,
            lambda catalog: catalog["entries"][0].__setitem__(
                "bundle_hash",
                manifest["bundle_hash"],
            ),
        )

    @staticmethod
    def _next_generation_entries(
        game: Path,
        *,
        version: str = "1.0.1",
        digest_character: str = "4",
    ):
        from worldforge import composed_game as module

        state = load_composed_catalog_state(game)
        primary = module._release_entry(state.entries[0])
        secondary = dict(primary)
        secondary["bundle_version"] = version
        secondary["bundle_hash"] = digest_character * 64
        secondary["path"] = str(secondary["path"]).rsplit("/", 1)[0] + f"/{version}"
        return state, [primary, secondary]

    def _assert_identity_swap_rejected(self, target: Path, operation) -> None:
        original = resource_snapshot_module._source_directory_snapshot  # noqa: SLF001
        swapped = False

        def replace_after_snapshot(root: Path, relative):
            nonlocal swapped
            result = original(root, relative)
            source = result[0]
            if source == target and not swapped:
                replacement = target.with_name(f".{target.name}.injected-swap")
                replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target)
                swapped = True
            return result

        with (
            patch.object(
                resource_snapshot_module,
                "_source_directory_snapshot",
                side_effect=replace_after_snapshot,
            ),
            self.assertRaisesRegex(ComposedCatalogError, "changed while opening"),
        ):
            operation()
        self.assertTrue(swapped)

    def _build_bundle(
        self,
        root: Path,
        *,
        name: str = "primary",
        bundle_version: str = "1.0.0",
        different_world_hash: bool = False,
    ) -> tuple[Path, str]:
        neutral = root / f"neutral-{name}"
        shutil.copytree(ROOT / "examples/m5-neutral", neutral)
        renderpack_path = neutral / "renderpack/build/renderpack.json"
        renderpack_path.parent.mkdir()
        renderpack = build_renderpack(
            neutral / "renderpack/manifest.json",
            WORLDPACK,
            renderpack_path,
        )
        worldpack_path = WORLDPACK
        if different_world_hash:
            worldpack_document = json.loads(WORLDPACK.read_bytes())
            worldpack_document["world"]["title"] = (
                f"{worldpack_document['world']['title']} ({name})"
            )
            worldpack_document["content_hash"] = canonical_payload_hash(worldpack_document)
            worldpack_path = root / f"worldpack-{name}.json"
            worldpack_path.write_bytes(canonical_json_bytes(worldpack_document))
            renderpack["world_content_hash"] = worldpack_document["content_hash"]
            renderpack["content_hash"] = canonical_payload_hash(renderpack)
            renderpack_path.write_bytes(canonical_json_bytes(renderpack))
        documents = root / f"documents-{name}"
        documents.mkdir()
        catalog = json.loads((M6_FIXTURES / "capability-catalog.json").read_bytes())
        profile = json.loads((M6_FIXTURES / "profiles/profile_2_5d.json").read_bytes())
        adapter = json.loads((M6_FIXTURES / "adapters/isoworld_raylib_2_5d.json").read_bytes())
        worldpack = json.loads(worldpack_path.read_bytes())
        composition = _composition(
            profile=profile,
            adapter=adapter,
            catalog=catalog,
            worldpack_hash=worldpack["content_hash"],
            renderpack_hash=renderpack["content_hash"],
        )
        composition["packs"]["worldpack"]["path"] = "packs/worldpack/worldpack.json"
        composition["packs"]["renderpack"]["path"] = "packs/renderpack/renderpack.json"
        composition["content_hash"] = canonical_payload_hash(composition)
        paths = {}
        for file_name, value in (
            ("catalog.json", catalog),
            ("profile.json", profile),
            ("adapter.json", adapter),
            ("composition.json", composition),
        ):
            paths[file_name] = documents / file_name
            paths[file_name].write_bytes(canonical_json_bytes(value))
        notice = root / f"NOTICE-{name}.txt"
        notice.write_text("Synthetic neutral test assets only.\n", encoding="utf-8")
        bundle_path = root / f"bundle-{name}"
        bundle = build_composed_runtime_bundle(
            paths["catalog.json"],
            paths["profile.json"],
            paths["adapter.json"],
            paths["composition.json"],
            worldpack_path,
            bundle_path,
            bundle_id="neutral_composed",
            bundle_version=bundle_version,
            platform="linux_x86_64",
            registry=PYRAY_2_5D_REGISTRY,
            license_sources={"NOTICE.txt": notice},
            renderpack_path=renderpack_path,
        )
        bundle_hash = bundle.bundle_hash
        bundle.close()
        return bundle_path, bundle_hash

    def _build_asset_bundle(
        self,
        root: Path,
        *,
        name: str = "asset",
        mixed: bool = False,
    ) -> tuple[Path, str, StaticRuntimeAdapterRegistry[object]]:
        neutral = root / f"neutral-{name}"
        shutil.copytree(ROOT / "examples/m5-neutral", neutral)
        assetpack_path = neutral / "assetpack/build/assetpack.json"
        assetpack_path.parent.mkdir()
        assetpack = build_assetpack(
            neutral / "assetpack/manifest.json",
            WORLDPACK,
            assetpack_path,
        )
        renderpack_path: Path | None = None
        renderpack: dict[str, Any] | None = None
        if mixed:
            renderpack_path = neutral / "renderpack/build/renderpack.json"
            renderpack_path.parent.mkdir()
            renderpack = build_renderpack(
                neutral / "renderpack/manifest.json",
                WORLDPACK,
                renderpack_path,
            )
        worldpack = load_worldpack(WORLDPACK)
        catalog = json.loads((M6_FIXTURES / "capability-catalog.json").read_bytes())
        profile_name = "profile_2d_over_3d.json" if mixed else "profile_3d.json"
        profile = json.loads((M6_FIXTURES / f"profiles/{profile_name}").read_bytes())
        adapter = json.loads((M6_FIXTURES / "adapter.declared.json").read_bytes())
        adapter["state"] = "verified"
        adapter["capability_ids"] = sorted(RUNTIME_CAPABILITIES)
        adapter["content_hash"] = canonical_payload_hash(adapter)
        adapter_key = RuntimeAdapterKey(
            adapter["id"],
            adapter["version"],
            adapter["content_hash"],
        )
        registry = StaticRuntimeAdapterRegistry([(adapter_key, object())])
        required = sorted(
            set(worldpack.runtime_requirements.required_features)
            | set(profile["required_capability_ids"])
        )
        composition: dict[str, Any] = {
            "format": "rpg-world-forge.runtime_composition",
            "format_version": 1,
            "world_id": worldpack.world_id,
            "world_content_hash": worldpack.content_hash,
            "release_id": "1.0.0",
            "profile": {
                "id": profile["id"],
                "content_hash": profile["content_hash"],
            },
            "capability_catalog_hash": catalog["content_hash"],
            "adapter": {
                "id": adapter["id"],
                "version": adapter["version"],
                "content_hash": adapter["content_hash"],
            },
            "packs": {
                "worldpack": {
                    "format": "isoworld.worldpack",
                    "format_version": worldpack.format_version,
                    "path": "packs/worldpack/worldpack.json",
                    "content_hash": worldpack.content_hash,
                },
                "assetpack": {
                    "format": "rpg-world-forge.assetpack",
                    "format_version": 1,
                    "path": "packs/assetpack/assetpack.json",
                    "content_hash": assetpack["content_hash"],
                },
            },
            "required_capability_ids": required,
            "slot_owners": [
                {
                    "slot": "actor:neutral",
                    "plane": "world_base",
                    "pack": "assetpack",
                    "asset_id": "neutral_actor_3d",
                    "representation": "3d",
                }
            ],
        }
        if mixed:
            assert renderpack is not None
            composition["packs"]["renderpack"] = {
                "format": "isoworld.renderpack",
                "format_version": 1,
                "path": "packs/renderpack/renderpack.json",
                "content_hash": renderpack["content_hash"],
            }
            composition["slot_owners"].append(
                {
                    "slot": "ui:font",
                    "plane": "world_overlay",
                    "pack": "renderpack",
                    "asset_id": "neutral_font",
                    "representation": "2d",
                }
            )
            composition["slot_owners"].sort(
                key=lambda item: (
                    item["slot"],
                    item["plane"],
                    item["pack"],
                    item["asset_id"],
                    item["representation"],
                )
            )
        composition["content_hash"] = canonical_payload_hash(composition)
        documents = root / f"documents-{name}"
        documents.mkdir()
        paths: dict[str, Path] = {}
        for filename, document in (
            ("catalog.json", catalog),
            ("profile.json", profile),
            ("adapter.json", adapter),
            ("composition.json", composition),
        ):
            paths[filename] = documents / filename
            paths[filename].write_bytes(canonical_json_bytes(document))
        notice = root / f"NOTICE-{name}.txt"
        notice.write_text("Synthetic neutral test assets only.\n", encoding="utf-8")
        bundle_path = root / f"bundle-{name}"
        bundle = build_composed_runtime_bundle(
            paths["catalog.json"],
            paths["profile.json"],
            paths["adapter.json"],
            paths["composition.json"],
            WORLDPACK,
            bundle_path,
            bundle_id="neutral_composed",
            bundle_version="1.0.0",
            platform="linux_x86_64",
            registry=registry,
            license_sources={"NOTICE.txt": notice},
            renderpack_path=renderpack_path,
            assetpack_path=assetpack_path,
        )
        bundle_hash = bundle.bundle_hash
        bundle.close()
        return bundle_path, bundle_hash, registry

    @staticmethod
    def _reseal_installed_resource(
        game: Path,
        relative: str,
        payload: bytes,
    ):
        release = load_composed_catalog(game)[0]
        root = game / release.path
        target = root / relative
        target.write_bytes(payload)
        manifest_path = root / "composed-bundle.manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        for record in manifest["files"]:
            if record["path"] == relative:
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                record["size"] = len(payload)
                break
        else:
            raise AssertionError(f"resource record is absent: {relative}")
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        return M6GameConsumerTests._reseal_catalog_tip(
            game,
            lambda catalog: catalog["entries"][0].__setitem__(
                "bundle_hash",
                manifest["bundle_hash"],
            ),
        )

    @classmethod
    def _reseal_composition_owner(
        cls,
        game: Path,
        mutation: Callable[[dict[str, Any]], None],
    ):
        release = load_composed_catalog(game)[0]
        root = game / release.path
        composition_path = root / "contracts/runtime-composition.json"
        report_path = root / "evidence/runtime-compatibility-report.json"
        manifest_path = root / "composed-bundle.manifest.json"

        composition = json.loads(composition_path.read_bytes())
        mutation(composition)
        composition["content_hash"] = canonical_payload_hash(composition)
        composition_bytes = canonical_json_bytes(composition)
        composition_path.write_bytes(composition_bytes)

        report = json.loads(report_path.read_bytes())
        report["composition_hash"] = composition["content_hash"]
        report["content_hash"] = canonical_payload_hash(report)
        report_bytes = canonical_json_bytes(report)
        report_path.write_bytes(report_bytes)

        manifest = json.loads(manifest_path.read_bytes())
        manifest["contracts"]["runtime_composition"]["content_hash"] = composition["content_hash"]
        manifest["compatibility_evidence"]["content_hash"] = report["content_hash"]
        manifest["compatibility_evidence"]["composition_hash"] = composition["content_hash"]
        replacements = {
            "contracts/runtime-composition.json": composition_bytes,
            "evidence/runtime-compatibility-report.json": report_bytes,
        }
        for record in manifest["files"]:
            payload = replacements.get(record["path"])
            if payload is not None:
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                record["size"] = len(payload)
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        return cls._reseal_catalog_tip(
            game,
            lambda catalog: (
                catalog["entries"][0].__setitem__(
                    "composition_hash",
                    composition["content_hash"],
                ),
                catalog["entries"][0].__setitem__(
                    "bundle_hash",
                    manifest["bundle_hash"],
                ),
            ),
        )

    def _foreign_world_hash_conflict(self, root: Path):
        first_bundle, first_hash = self._build_bundle(root)
        second_bundle, second_hash = self._build_bundle(
            root,
            name="same-hash",
            bundle_version="1.0.1",
        )
        game = root / "foreign-conflict"
        create_game_project(
            game,
            game_id="foreign_conflict",
            title="Foreign Conflict",
        )
        import_composed_bundle(first_bundle, game, expected_bundle_hash=first_hash)
        import_composed_bundle(second_bundle, game, expected_bundle_hash=second_hash)
        authorized = load_composed_catalog(game)[0]
        self._reseal_catalog_tip_unchecked(
            game,
            lambda catalog: catalog["entries"][1].__setitem__(
                "world_content_hash",
                "0" * 64,
            ),
        )
        return game, authorized

    @staticmethod
    def _run(game: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "run_game.py", *arguments],
            cwd=game,
            env=_subprocess_environment(),
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _open_regular_test_descriptor(root: Path) -> int:
        path = root / "descriptor.bin"
        path.write_bytes(b"descriptor")
        return os.open(path, os.O_RDONLY)

    def test_subprocess_environment_preserves_windows_home_contract(self) -> None:
        windows_environment = {
            "HOMEDRIVE": "C:",
            "HOMEPATH": "\\Users\\runneradmin",
            "PATH": "C:\\Windows\\System32",
            "SYSTEMROOT": "C:\\Windows",
            "USERPROFILE": "C:\\Users\\runneradmin",
        }
        with patch.dict(os.environ, windows_environment, clear=True):
            environment = _subprocess_environment()
        self.assertEqual(
            windows_environment,
            {key: environment[key] for key in windows_environment},
        )
        self.assertEqual("1", environment["PYTHONUTF8"])

    def test_owned_bundle_copy_uses_portable_directory_flush(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_path, bundle_hash = self._build_bundle(root)
            stage = root / "copied-stage"
            loaded = composed_bundle_module.verify_composed_runtime_bundle(
                bundle_path,
                expected_bundle_hash=bundle_hash,
                platform="linux_x86_64",
                runtime_api_version=composed_game_module.RUNTIME_API_VERSION,
                registry=PYRAY_2_5D_REGISTRY,
            )
            real_open = os.open

            def reject_directory_descriptor(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                if flags & getattr(os, "O_DIRECTORY", 0):
                    raise PermissionError("Windows rejects Python directory descriptors")
                return real_open(path, flags, *args, **kwargs)

            try:
                with (
                    patch.object(
                        composed_game_module,
                        "fsync_directory",
                        create=True,
                    ) as flush_directory,
                    patch.object(
                        composed_game_module.os,
                        "open",
                        side_effect=reject_directory_descriptor,
                    ),
                ):
                    composed_game_module._copy_owned_bundle(loaded, stage)  # noqa: SLF001
            finally:
                loaded.close()

            expected = {
                stage.parent,
                stage,
                *(path for path in stage.rglob("*") if path.is_dir()),
            }
            flushed = {call.args[0] for call in flush_directory.call_args_list}
            self.assertEqual(expected, flushed)

    def test_first_import_flushes_created_ancestor_entries_bottom_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "first-import-ancestor-durability"
            create_game_project(
                game,
                game_id="first_import_ancestor_durability",
                title="First Import Ancestor Durability",
            )
            flush = composed_game_module.fsync_directory
            calls: list[tuple[Path, str]] = []

            def record_flush(path: Path, *, context: str) -> None:
                calls.append((path, context))
                flush(path, context=context)

            with patch.object(
                composed_game_module,
                "fsync_directory",
                side_effect=record_flush,
            ):
                imported = import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            game_data = game / "game_data"
            expected_composition: list[Path] = []
            current = imported.parent.parent
            while current != game_data.parent:
                expected_composition.append(current)
                current = current.parent
            self.assertEqual(
                expected_composition,
                [path for path, context in calls if context == "composed import ancestor"],
            )
            self.assertEqual(
                [game_data],
                [path for path, context in calls if context == "composed catalog parent"],
            )
            self.assertNotIn(
                game,
                {
                    path
                    for path, context in calls
                    if context
                    in {
                        "composed import ancestor",
                        "composed catalog parent",
                    }
                },
            )

    def test_recovery_reflushes_composition_and_catalog_ancestor_entries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "recovery-ancestor-durability"
            create_game_project(
                game,
                game_id="recovery_ancestor_durability",
                title="Recovery Ancestor Durability",
            )
            game_data = game / "game_data"
            flush = composed_game_module.fsync_directory

            def fail_composition_parent(path: Path, *, context: str) -> None:
                if context == "composed import ancestor" and path == game_data:
                    raise OSError("injected composition ancestor flush failure")
                flush(path, context=context)

            with (
                patch.object(
                    composed_game_module,
                    "fsync_directory",
                    side_effect=fail_composition_parent,
                ),
                self.assertRaisesRegex(OSError, "composition ancestor flush failure"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            def fail_catalog_parent(path: Path, *, context: str) -> None:
                if context == "composed catalog parent":
                    raise OSError("injected catalog parent flush failure")
                flush(path, context=context)

            with (
                patch.object(
                    composed_game_module,
                    "fsync_directory",
                    side_effect=fail_catalog_parent,
                ),
                self.assertRaisesRegex(OSError, "catalog parent flush failure"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertEqual(1, len(load_composed_catalog(game)))

            calls: list[tuple[Path, str]] = []

            def record_flush(path: Path, *, context: str) -> None:
                calls.append((path, context))
                flush(path, context=context)

            with patch.object(
                composed_game_module,
                "fsync_directory",
                side_effect=record_flush,
            ):
                imported = import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            expected_composition: list[Path] = []
            current = imported.parent.parent
            while current != game_data.parent:
                expected_composition.append(current)
                current = current.parent
            self.assertEqual(
                expected_composition,
                [path for path, context in calls if context == "composed import ancestor"],
            )
            self.assertEqual(
                [game_data],
                [path for path, context in calls if context == "composed catalog parent"],
            )

    def test_staged_bundle_flush_failure_is_retried_before_recovery_publish(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "staged-bundle-durability"
            create_game_project(
                game,
                game_id="staged_bundle_durability",
                title="Staged Bundle Durability",
            )
            copy_seam_name = (
                "_copy_owned_import_stage" if os.name == "nt" else "_copy_owned_bundle_into_claim"
            )
            copy_into_stage = getattr(composed_game_module, copy_seam_name)

            def copy_then_fail(*args: object, **kwargs: object) -> None:
                copy_into_stage(*args, **kwargs)
                raise OSError("injected staged bundle flush failure")

            with (
                patch.object(
                    composed_game_module,
                    copy_seam_name,
                    side_effect=copy_then_fail,
                ),
                self.assertRaisesRegex(OSError, "staged bundle flush failure"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertEqual(
                1,
                len([path for path in (game / "game_data").rglob(".*.import-*") if path.is_dir()]),
            )

            imported = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(imported.is_dir())

    def test_stage_mkdir_failure_leaves_no_compositions_tree_and_retry_succeeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "pre-stage-recovery"
            create_game_project(
                game,
                game_id="pre_stage_recovery",
                title="Pre-stage Recovery",
            )
            if os.name == "nt":
                failure = patch.object(
                    composed_game_module.resource_snapshot_module,
                    "_windows_create_private_directory",
                    side_effect=composed_game_module.resource_snapshot_module.ResourceSnapshotError(
                        "injected composed destination claim failure"
                    ),
                )
            else:
                failure = patch.object(
                    composed_game_module,
                    "claim_directory_noreplace",
                    side_effect=composed_game_module.DirectoryPublishError(
                        "injected composed destination claim failure"
                    ),
                )
            with (
                failure,
                self.assertRaisesRegex(
                    ComposedGameError,
                    "destination claim failure",
                ),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            self.assertFalse((game / "game_data/compositions").exists())
            imported = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(imported.is_dir())
            self.assertEqual(1, len(load_composed_catalog(game)))
            committed = composed_game_module._read_catalog_journal_record(game)  # noqa: SLF001
            self.assertIsNotNone(committed)
            assert committed is not None
            self.assertEqual(1, committed[0]["format_version"])
            self.assertEqual("committed", committed[0]["state"])
            self.assertIsInstance(committed[0]["directory_identity"], dict)
            journal_audit = audit_publication_journals(game)
            self.assertEqual((), journal_audit.issues)
            self.assertEqual(
                ("game_data/.composed-catalog-publication.json",),
                tuple(path.as_posix() for path in journal_audit.terminal_paths),
            )

    def test_first_import_uses_exclusive_destination_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "same-parent-publication"
            create_game_project(
                game,
                game_id="same_parent_publication",
                title="Same-parent Publication",
            )
            calls: list[Path] = []

            if os.name == "nt":
                create_private = composed_game_module._create_generation_stage

                def record_private_stage(destination: Path) -> None:
                    calls.append(destination)
                    create_private(destination)

                exclusive_stage = patch.object(
                    composed_game_module,
                    "_create_generation_stage",
                    side_effect=record_private_stage,
                )
            else:
                claim = composed_game_module.claim_directory_noreplace

                @contextmanager
                def record_claim(destination: Path, **kwargs: object):
                    calls.append(destination)
                    with claim(destination, **kwargs) as retained:
                        yield retained

                exclusive_stage = patch.object(
                    composed_game_module,
                    "claim_directory_noreplace",
                    side_effect=record_claim,
                )

            with exclusive_stage:
                imported = import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            self.assertTrue(imported.is_dir())
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(game / "game_data", calls[0].parent)
            self.assertTrue(calls[0].name.startswith(".compositions.import-"))
            self.assertEqual(
                game / CATALOG_GENERATIONS_RELATIVE_PATH,
                calls[-1].parent,
            )

    def test_first_missing_claim_revalidates_the_existing_game_data_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "game-data-parent-swap"
            create_game_project(
                game,
                game_id="game_data_parent_swap",
                title="Game Data Parent Swap",
            )
            game = game.resolve(strict=True)
            game_data = game / "game_data"
            displaced = game / "game_data-owned"
            sentinel = game_data / "foreign.txt"
            claim = composed_game_module.claim_directory_noreplace
            swapped = False

            def replace_game_data() -> None:
                nonlocal swapped
                if not swapped:
                    game_data.rename(displaced)
                    game_data.mkdir()
                    sentinel.write_bytes(b"foreign-game-data\n")
                    swapped = True

            @contextmanager
            def swap_before_claim(destination: Path, **kwargs: object):
                if destination.parent == game_data and destination.name.startswith(
                    ".compositions.import-"
                ):
                    replace_game_data()
                with claim(destination, **kwargs) as retained:
                    yield retained

            if os.name == "nt":
                lock_directory = (
                    composed_game_module.resource_snapshot_module._windows_lock_directory
                )

                def swap_before_parent_lock(path: Path) -> int:
                    if path == game_data:
                        replace_game_data()
                    return lock_directory(path)

                parent_swap = patch.object(
                    composed_game_module.resource_snapshot_module,
                    "_windows_lock_directory",
                    side_effect=swap_before_parent_lock,
                )
            else:
                parent_swap = patch.object(
                    composed_game_module,
                    "claim_directory_noreplace",
                    side_effect=swap_before_claim,
                )

            with (
                parent_swap,
                self.assertRaisesRegex(
                    ComposedGameError,
                    "parent identity changed|ancestor identity changed",
                ),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            self.assertTrue(swapped)
            self.assertEqual(b"foreign-game-data\n", sentinel.read_bytes())
            self.assertEqual(["foreign.txt"], sorted(path.name for path in game_data.iterdir()))
            self.assertTrue((displaced / "compositions.lock.json").is_file())

    def test_windows_private_import_stage_revalidates_the_locked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent"
            parent.mkdir()
            stage = parent / ".compositions.import-test"
            expected_identity = composed_game_module.directory_identity(
                parent,
                context="Windows import-stage seam parent",
            )
            displaced = root / "parent-owned"
            closed: list[int] = []

            def swap_before_lock(path: Path) -> int:
                self.assertEqual(parent, path)
                parent.rename(displaced)
                parent.mkdir()
                return 71

            with (
                patch.object(
                    composed_game_module,
                    "_generation_platform",
                    return_value="windows",
                ),
                patch.object(
                    composed_game_module.resource_snapshot_module,
                    "_windows_lock_directory",
                    side_effect=swap_before_lock,
                ),
                patch.object(
                    composed_game_module.resource_snapshot_module,
                    "_windows_close_handle",
                    side_effect=closed.append,
                ),
                patch.object(
                    composed_game_module,
                    "_create_generation_stage",
                ) as create_stage,
                self.assertRaisesRegex(
                    ComposedGameError,
                    "parent identity changed",
                ),
            ):
                with composed_game_module._private_import_stage(  # noqa: SLF001
                    stage,
                    expected_parent_identity=expected_identity,
                ):
                    self.fail("changed Windows stage parent must fail before yield")

            create_stage.assert_not_called()
            self.assertEqual([71], closed)
            self.assertTrue(displaced.is_dir())
            self.assertFalse(stage.exists())

    def test_generation_stage_flush_failure_is_retried_before_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "generation-stage-durability"
            create_game_project(
                game,
                game_id="generation_stage_durability",
                title="Generation Stage Durability",
            )
            game = game.resolve(strict=True)
            injected = False

            if os.name == "nt":
                flush_directory = composed_game_module.fsync_directory

                def fail_generation_directory(path: Path, *, context: str) -> None:
                    nonlocal injected
                    if (
                        not injected
                        and context == "catalog generation stage"
                        and path.parent == game / CATALOG_GENERATIONS_RELATIVE_PATH
                    ):
                        injected = True
                        raise OSError("injected generation stage flush failure")
                    flush_directory(path, context=context)

                flush_failure = patch.object(
                    composed_game_module,
                    "fsync_directory",
                    side_effect=fail_generation_directory,
                )
            else:
                fsync_claim = composed_game_module.DirectoryClaim.fsync

                def fail_generation_claim(claim: object) -> None:
                    nonlocal injected
                    path = claim.path
                    if not injected and path.parent == game / CATALOG_GENERATIONS_RELATIVE_PATH:
                        injected = True
                        raise OSError("injected generation stage flush failure")
                    fsync_claim(claim)

                flush_failure = patch.object(
                    composed_game_module.DirectoryClaim,
                    "fsync",
                    new=fail_generation_claim,
                )

            with (
                flush_failure,
                self.assertRaisesRegex(OSError, "generation stage flush failure"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertTrue(injected)
            generations = game / CATALOG_GENERATIONS_RELATIVE_PATH
            stages = list(
                generations.glob(f"{composed_game_module.CATALOG_GENERATION_STAGE_PREFIX}*")
            )
            self.assertEqual(1, len(stages))

            imported = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(imported.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux descriptor-relative catalog stages",
    )
    def test_catalog_mid_write_process_loss_preserves_exact_private_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "catalog-mid-write"
            create_game_project(
                game,
                game_id="catalog_mid_write",
                title="Catalog Mid Write",
            )
            interrupted = False

            def write_partial_then_stop(
                stage: Path,
                payload: bytes,
                *,
                directory_descriptor: int | None,
            ) -> None:
                nonlocal interrupted
                if directory_descriptor is None:
                    self.fail("Linux catalog generation must be descriptor-relative")
                descriptor = os.open(
                    CATALOG_GENERATION_NAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=directory_descriptor,
                )
                try:
                    os.write(descriptor, payload[: max(1, len(payload) // 2)])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                interrupted = True
                raise KeyboardInterrupt("injected catalog mid-write process loss")

            with (
                patch.object(
                    composed_game_module,
                    "_write_generation_payload",
                    side_effect=write_partial_then_stop,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "catalog mid-write"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            self.assertTrue(interrupted)
            journal = game / composed_game_module.CATALOG_PUBLICATION_JOURNAL
            self.assertTrue(journal.is_file())
            generation_directories = list((game / CATALOG_GENERATIONS_RELATIVE_PATH).iterdir())
            self.assertEqual(1, len(generation_directories))
            staged_payload = generation_directories[0] / CATALOG_GENERATION_NAME
            self.assertTrue(staged_payload.is_file())
            staged_bytes = staged_payload.read_bytes()

            with self.assertRaisesRegex(
                ComposedGameError,
                "not exact|payload (?:hash|identity) changed",
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertEqual(staged_bytes, staged_payload.read_bytes())
            active = composed_game_module._read_catalog_journal_record(game)  # noqa: SLF001
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual("copying", active[0]["state"])

    def test_generation_root_flush_failure_is_retried_before_recovered_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "generation-root-durability"
            create_game_project(
                game,
                game_id="generation_root_durability",
                title="Generation Root Durability",
            )
            flush = composed_game_module.fsync_directory

            def fail_generation_root(path: Path, *, context: str) -> None:
                if context == "composed catalog generation root":
                    raise OSError("injected generation root flush failure")
                flush(path, context=context)

            for attempt in ("initial", "recovery"):
                with (
                    self.subTest(attempt=attempt),
                    patch.object(
                        composed_game_module,
                        "fsync_directory",
                        side_effect=fail_generation_root,
                    ),
                    self.assertRaisesRegex(OSError, "generation root flush failure"),
                ):
                    import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                self.assertEqual(1, len(load_composed_catalog(game)))

            imported = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(imported.is_dir())

    def test_empty_catalog_is_canonical_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game_data = root / "game_data"
            game_data.mkdir()
            payload: dict[str, object] = {
                "entries": [],
                "format": "isoworld.composed_runtime_catalog",
                "format_version": 1,
            }
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            payload["content_hash"] = hashlib.sha256(encoded).hexdigest()
            catalog_path = game_data / "compositions.lock.json"
            catalog_bytes = (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            catalog_path.write_bytes(catalog_bytes)
            self.assertEqual(canonical_json_bytes(payload), catalog_path.read_bytes())
            self.assertEqual((), load_composed_catalog(root))

    def test_multiple_imports_append_immutable_catalog_generations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_bundle, first_hash = self._build_bundle(root)
            second_bundle, second_hash = self._build_bundle(
                root,
                name="secondary",
                bundle_version="1.0.1",
            )
            game = root / "multiple-imports"
            create_game_project(game, game_id="multiple_imports", title="Multiple Imports")
            import_composed_bundle(
                first_bundle,
                game,
                expected_bundle_hash=first_hash,
            )
            import_composed_bundle(
                second_bundle,
                game,
                expected_bundle_hash=second_hash,
            )
            entries = load_composed_catalog(game)
            state = load_composed_catalog_state(game)
            self.assertEqual(["1.0.0", "1.0.1"], [entry.bundle_version for entry in entries])
            self.assertEqual(state.entries, entries)
            self.assertEqual(
                2,
                len(
                    tuple(
                        (game / CATALOG_GENERATIONS_RELATIVE_PATH).glob(
                            f"*/{CATALOG_GENERATION_NAME}"
                        )
                    )
                ),
            )
            base_path = game / "game_data/compositions.lock.json"
            base_bytes = base_path.read_bytes()
            base = json.loads(base_bytes)
            self.assertEqual([], base["entries"])
            self.assertEqual(canonical_json_bytes(base), base_bytes)

    def test_same_world_hash_allows_multiple_verified_composed_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_bundle, first_hash = self._build_bundle(root)
            second_bundle, second_hash = self._build_bundle(
                root,
                name="same-world-variant",
                bundle_version="1.0.1",
            )
            game = root / "same-world-variants"
            create_game_project(
                game,
                game_id="same_world_variants",
                title="Same World Variants",
            )
            import_composed_bundle(first_bundle, game, expected_bundle_hash=first_hash)
            import_composed_bundle(second_bundle, game, expected_bundle_hash=second_hash)
            entries = load_composed_catalog(game)
            self.assertEqual(2, len(entries))
            self.assertEqual(1, len({entry.world_content_hash for entry in entries}))
            for entry in entries:
                with verify_composed_release(entry, game):
                    pass
            run = self._run(
                game,
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_2_5d",
                "--adapter-id",
                "isoworld_raylib_2_5d",
                "--adapter-version",
                "0.1.0",
                "--bundle-id",
                "neutral_composed",
                "--bundle-version",
                "1.0.1",
                "--headless-ticks",
                "0",
            )
            self.assertEqual(0, run.returncode, run.stderr)
            package = root / "same-world-variants.zip"
            packaged = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "scripts/package_game.py",
                    "--output",
                    str(package),
                ],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, packaged.returncode, packaged.stderr)
            self.assertTrue(package.is_file())
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
            self.assertNotIn(
                "game_data/.composed-catalog-publication.json",
                names,
            )
            self.assertNotIn("game_data/bundle-import.journal.json", names)

    def test_conflicting_world_hash_is_rejected_before_import_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_bundle, first_hash = self._build_bundle(root)
            conflicting_bundle, conflicting_hash = self._build_bundle(
                root,
                name="different-hash",
                bundle_version="1.0.2",
                different_world_hash=True,
            )
            game = root / "import-conflict"
            create_game_project(game, game_id="import_conflict", title="Import Conflict")
            import_composed_bundle(first_bundle, game, expected_bundle_hash=first_hash)
            with self.assertRaisesRegex(
                ComposedGameError,
                "multiple world content hashes",
            ):
                import_composed_bundle(
                    conflicting_bundle,
                    game,
                    expected_bundle_hash=conflicting_hash,
                )
            self.assertEqual(1, len(load_composed_catalog(game)))
            self.assertEqual(
                1,
                len(
                    tuple((game / "game_data/compositions").rglob("composed-bundle.manifest.json"))
                ),
            )

    def test_orphan_import_stage_scan_uses_exact_operation_id_shape_not_bundle_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "operation-id-orphan"
            create_game_project(
                game,
                game_id="operation_id_orphan",
                title="Operation ID Orphan",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            game_data = game / "game_data"

            operation_id = "1" * 32
            exact_stage = game_data / f".compositions.import-{operation_id}"
            exact_stage.mkdir()
            with self.assertRaisesRegex(
                ComposedGameError,
                "retains a staging directory",
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertTrue(exact_stage.is_dir())
            exact_stage.rmdir()

            bundle_hash_stage = game_data / f".compositions.import-{bundle_hash}"
            bundle_hash_stage.mkdir()
            with self.assertRaisesRegex(
                ComposedGameError,
                "32 lowercase hex",
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertTrue(bundle_hash_stage.is_dir())

    def test_foreign_world_hash_conflict_is_rejected_during_catalog_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, _authorized = self._foreign_world_hash_conflict(Path(directory))
            from worldforge.bundle import BundleError, _load_verified_catalog

            with self.assertRaisesRegex(
                ComposedCatalogError,
                "multiple world content hashes",
            ):
                load_composed_catalog(game)
            with self.assertRaisesRegex(
                BundleError,
                "multiple world content hashes",
            ):
                _load_verified_catalog(game)

    def test_foreign_world_hash_conflict_revokes_release_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, authorized = self._foreign_world_hash_conflict(Path(directory))
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "multiple world content hashes",
            ):
                verify_composed_release(authorized, game)

    def test_foreign_world_hash_conflict_is_a_concise_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, _authorized = self._foreign_world_hash_conflict(Path(directory))
            run = self._run(
                game,
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_2_5d",
                "--headless-ticks",
                "0",
            )
            self.assertNotEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertIn("multiple world content hashes", run.stderr)
            self.assertNotIn("Traceback", run.stderr)

    def test_foreign_world_hash_conflict_fails_standalone_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, _authorized = self._foreign_world_hash_conflict(Path(directory))
            verified = subprocess.run(
                [sys.executable, "-I", "scripts/verify_game.py"],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, verified.returncode, verified.stdout + verified.stderr)
            self.assertIn("multiple world content hashes", verified.stderr)
            self.assertNotIn("Traceback", verified.stderr)

    def test_foreign_world_hash_conflict_cannot_be_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, _authorized = self._foreign_world_hash_conflict(root)
            package = root / "conflicting.zip"
            packaged = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "scripts/package_game.py",
                    "--output",
                    str(package),
                ],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, packaged.returncode, packaged.stdout + packaged.stderr)
            self.assertIn("multiple world content hashes", packaged.stderr)
            self.assertNotIn("Traceback", packaged.stderr)
            self.assertFalse(package.exists())

    def test_concurrent_same_generation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "same-generation"
            create_game_project(game, game_id="same_generation", title="Same Generation")
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state = load_composed_catalog_state(game)
            primary = module._release_entry(state.entries[0])
            secondary = dict(primary)
            secondary["bundle_version"] = "1.0.1"
            secondary["bundle_hash"] = "1" * 64
            secondary["path"] = str(secondary["path"]).rsplit("/", 1)[0] + "/1.0.1"
            entries = [primary, secondary]
            entries.sort(
                key=lambda item: (
                    item["world_id"],
                    item["release_id"],
                    item["profile_id"],
                    item["adapter_id"],
                    item["adapter_version"],
                    item["bundle_id"],
                    item["bundle_version"],
                )
            )
            publish = module.publish_directory_noreplace

            def move_then_stop(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> None:
                with publish(source, destination, **kwargs):
                    pass
                raise KeyboardInterrupt("injected post-rename process loss")

            with (
                patch.object(
                    module,
                    "publish_directory_noreplace",
                    side_effect=move_then_stop,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "post-rename"),
            ):
                module._publish_catalog_generation(game, state, entries)
            published = module._publish_catalog_generation(game, state, entries)
            self.assertEqual(2, len(published.entries))

    def test_concurrent_different_generations_fork_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "different-generations"
            create_game_project(
                game,
                game_id="different_generations",
                title="Different Generations",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state = load_composed_catalog_state(game)
            primary = module._release_entry(state.entries[0])

            def alternative(version: str, digest: str) -> dict[str, Any]:
                entry = dict(primary)
                entry["bundle_version"] = version
                entry["bundle_hash"] = digest * 64
                entry["path"] = str(entry["path"]).rsplit("/", 1)[0] + f"/{version}"
                return entry

            requested = [primary, alternative("1.0.1", "1")]
            competing = [primary, alternative("1.0.2", "2")]
            competing.sort(key=lambda item: item["bundle_version"])
            competing_document = {
                "format": composed_catalog_module.CATALOG_GENERATION_FORMAT,
                "format_version": 1,
                "previous_hash": state.head_hash,
                "entries": competing,
            }
            competing_document["content_hash"] = canonical_payload_hash(competing_document)
            publish = module.publish_directory_noreplace
            competing_written = False

            def publish_with_competing(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                nonlocal competing_written
                competing_path = destination.parent / competing_document["content_hash"]
                if not competing_written:
                    competing_written = True
                    competing_path.mkdir()
                    (competing_path / CATALOG_GENERATION_NAME).write_bytes(
                        canonical_json_bytes(competing_document)
                    )
                return publish(source, destination, **kwargs)

            with (
                patch.object(
                    module,
                    "publish_directory_noreplace",
                    side_effect=publish_with_competing,
                ),
                self.assertRaisesRegex(ComposedGameError, "fork"),
            ):
                module._publish_catalog_generation(game, state, requested)
            with self.assertRaisesRegex(ComposedCatalogError, "fork"):
                load_composed_catalog_state(game)

    def test_foreign_generation_claim_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "foreign-generation"
            create_game_project(game, game_id="foreign_generation", title="Foreign Generation")
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state = load_composed_catalog_state(game)
            primary = module._release_entry(state.entries[0])
            secondary = dict(primary)
            secondary["bundle_version"] = "1.0.1"
            secondary["bundle_hash"] = "3" * 64
            secondary["path"] = str(secondary["path"]).rsplit("/", 1)[0] + "/1.0.1"
            foreign = b"foreign-generation-bytes\n"
            replacement: dict[str, Path | int] = {}
            publish = module.publish_directory_noreplace

            def inject_foreign(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                destination.mkdir()
                target = destination / CATALOG_GENERATION_NAME
                target.write_bytes(foreign)
                target.chmod(0o640)
                replacement["path"] = target
                replacement["mode"] = stat.S_IMODE(target.stat().st_mode)
                return publish(source, destination, **kwargs)

            with (
                patch.object(
                    module,
                    "publish_directory_noreplace",
                    side_effect=inject_foreign,
                ),
                self.assertRaises(ComposedGameError),
            ):
                module._publish_catalog_generation(game, state, [primary, secondary])
            target = replacement["path"]
            assert isinstance(target, Path)
            self.assertEqual(foreign, target.read_bytes())
            self.assertEqual(replacement["mode"], stat.S_IMODE(target.stat().st_mode))

    def test_catalog_journal_transition_never_replaces_foreign_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "catalog-journal-cas"
            create_game_project(
                game,
                game_id="catalog_journal_cas",
                title="Catalog Journal CAS",
            )
            document: dict[str, Any] = {
                "format": composed_catalog_module.CATALOG_GENERATION_FORMAT,
                "format_version": 1,
                "previous_hash": "0" * 64,
                "entries": [{"synthetic": True}],
            }
            document["content_hash"] = canonical_payload_hash(document)
            generation_hash = str(document["content_hash"])
            generation = game / CATALOG_GENERATIONS_RELATIVE_PATH / generation_hash
            generation.parent.mkdir()
            generation.mkdir()
            identity = directory_publish_module.directory_identity(
                generation,
                context="catalog CAS generation",
            )
            operation_id = "1" * 32
            current = composed_game_module._catalog_journal_document(
                operation_id=operation_id,
                state="copying",
                generation_hash=generation_hash,
                directory_identity_value=identity,
                document=document,
            )
            updated = composed_game_module._catalog_journal_document(
                operation_id=operation_id,
                state="ready",
                generation_hash=generation_hash,
                directory_identity_value=identity,
                document=document,
            )
            prior = composed_game_module._write_catalog_journal(
                game,
                current,
                create=True,
            )
            path = game / composed_game_module.CATALOG_PUBLICATION_JOURNAL
            owned = path.with_name(f"{path.name}.owned")
            foreign = b'{"foreign":true}\n'
            real_lseek = os.lseek
            swapped = False
            swap_blocked = False

            def swap_before_final_check(
                descriptor: int,
                offset: int,
                whence: int,
            ) -> int:
                nonlocal swap_blocked, swapped
                if not swapped:
                    swapped = True
                    try:
                        path.rename(owned)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) != 32:
                            raise
                        swap_blocked = True
                        raise OSError("journal path binding changed before transition") from exc
                    path.write_bytes(foreign)
                return real_lseek(descriptor, offset, whence)

            with (
                patch.object(
                    composed_game_module.os,
                    "lseek",
                    side_effect=swap_before_final_check,
                ),
                self.assertRaisesRegex(
                    ComposedGameError,
                    "changed before transition|path binding changed",
                ),
            ):
                composed_game_module._write_catalog_journal(
                    game,
                    updated,
                    create=False,
                    expected=prior,
                )

            self.assertTrue(swapped)
            if swap_blocked:
                self.assertFalse(owned.exists())
                self.assertEqual(canonical_json_bytes(current), path.read_bytes())
            else:
                self.assertEqual(foreign, path.read_bytes())
                self.assertEqual(canonical_json_bytes(current), owned.read_bytes())

    def test_active_partial_and_foreign_catalog_journals_block_audit_and_package(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "catalog-journal-policy"
            create_game_project(
                game,
                game_id="catalog_journal_policy",
                title="Catalog Journal Policy",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            journal = game / composed_game_module.CATALOG_PUBLICATION_JOURNAL
            terminal_bytes = journal.read_bytes()
            loaded = composed_game_module._read_catalog_journal_record(game)  # noqa: SLF001
            self.assertIsNotNone(loaded)
            assert loaded is not None
            terminal_record = loaded[0]
            active_document = {
                **terminal_record["document"],
                "previous_hash": terminal_record["generation_hash"],
            }
            active_document["content_hash"] = canonical_payload_hash(active_document)
            active_record = {
                **terminal_record,
                "operation_id": "f" * 32,
                "state": "intent",
                "generation_hash": active_document["content_hash"],
                "directory_identity": None,
                "document": active_document,
            }
            active_frame = journal_frame(canonical_journal_record(active_record))
            cases = (
                ("active_publication_journal", terminal_bytes + active_frame),
                ("partial_publication_journal", terminal_bytes + active_frame[:31]),
                ("invalid_publication_journal", b'{"foreign":true}\n'),
            )
            for rule, payload in cases:
                with self.subTest(rule=rule):
                    journal.write_bytes(payload)
                    findings = audit_game_repository(game)
                    self.assertTrue(
                        any(
                            finding.path == composed_game_module.CATALOG_PUBLICATION_JOURNAL
                            and finding.rule == rule
                            for finding in findings
                        ),
                        findings,
                    )
                    verified = subprocess.run(
                        [sys.executable, "-I", "scripts/verify_game.py"],
                        cwd=game,
                        env=_subprocess_environment(),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(1, verified.returncode, verified.stdout + verified.stderr)
                    self.assertIn("JOURNAL_", verified.stderr)
                    self.assertNotIn("Traceback", verified.stderr)
                    package = root / f"{rule}.zip"
                    packaged = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "scripts/package_game.py",
                            "--output",
                            str(package),
                        ],
                        cwd=game,
                        env=_subprocess_environment(),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(2, packaged.returncode, packaged.stdout + packaged.stderr)
                    self.assertIn("JOURNAL_", packaged.stderr)
                    self.assertNotIn("Traceback", packaged.stderr)
                    self.assertFalse(package.exists())

    def test_generation_parent_swap_is_rejected_without_touching_foreign(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "generation-stage-swap"
            create_game_project(
                game,
                game_id="generation_stage_swap",
                title="Generation Stage Swap",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state, entries = self._next_generation_entries(game)
            generations = game / CATALOG_GENERATIONS_RELATIVE_PATH
            displaced = generations.with_name(f"{generations.name}.owned")
            real_claim = directory_publish_module._posix_mkdir_noreplace
            swapped = False

            def replace_generation_parent() -> None:
                nonlocal swapped
                generations.rename(displaced)
                generations.mkdir()
                (generations / "sentinel.txt").write_bytes(b"foreign-parent\n")
                swapped = True

            def swap_parent(parent_fd: int, name: str, mode: int) -> None:
                replace_generation_parent()
                real_claim(parent_fd, name, mode)

            if os.name == "nt":
                lock_directory = module.resource_snapshot_module._windows_lock_directory

                def swap_before_parent_lock(path: Path) -> int:
                    if path == generations and not swapped:
                        replace_generation_parent()
                    return lock_directory(path)

                parent_swap = patch.object(
                    module.resource_snapshot_module,
                    "_windows_lock_directory",
                    side_effect=swap_before_parent_lock,
                )
            else:
                parent_swap = patch.object(
                    directory_publish_module,
                    "_posix_mkdir_noreplace",
                    side_effect=swap_parent,
                )

            with (
                parent_swap,
                self.assertRaises((OSError, ComposedGameError)),
            ):
                module._publish_catalog_generation(game, state, entries)
            self.assertTrue(swapped)
            self.assertEqual(
                b"foreign-parent\n",
                (generations / "sentinel.txt").read_bytes(),
            )
            self.assertEqual(
                [],
                [path for path in generations.iterdir() if path.name != "sentinel.txt"],
            )
            self.assertTrue(any(path.is_dir() for path in displaced.iterdir()))

    def test_generation_payload_write_requests_binary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            payload = b'{"format":"test","line_endings":"canonical\\n"}\n'
            host_binary_flag = getattr(os, "O_BINARY", 0)
            binary_flag = host_binary_flag or 0x8000
            opened_flags: list[int] = []
            original_open = os.open

            def record_open(
                target: object,
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                opened_flags.append(flags)
                return original_open(
                    target,
                    flags if host_binary_flag else flags & ~binary_flag,
                    *args,
                    **kwargs,
                )

            with (
                patch.object(
                    composed_game_module.os,
                    "O_BINARY",
                    binary_flag,
                    create=True,
                ),
                patch.object(
                    composed_game_module.os,
                    "open",
                    side_effect=record_open,
                ),
            ):
                composed_game_module._write_generation_payload(  # noqa: SLF001
                    stage,
                    payload,
                    directory_descriptor=None,
                )

            self.assertEqual(1, len(opened_flags))
            self.assertTrue(opened_flags[0] & binary_flag)
            self.assertEqual(
                payload,
                (stage / CATALOG_GENERATION_NAME).read_bytes(),
            )

    def test_generation_destination_symlink_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "generation-destination-symlink"
            create_game_project(
                game,
                game_id="generation_destination_symlink",
                title="Generation Destination Symlink",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state, entries = self._next_generation_entries(game)
            foreign = root / "foreign-generation-destination"
            foreign.mkdir()
            sentinel = foreign / "sentinel.txt"
            sentinel.write_bytes(b"foreign-destination\n")
            observed: dict[str, Path] = {}
            publish = module.publish_directory_noreplace

            def inject_symlink(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                try:
                    os.symlink(foreign, destination, target_is_directory=True)
                except (NotImplementedError, OSError):
                    self.skipTest("directory symlinks are unavailable")
                observed["destination"] = destination
                return publish(source, destination, **kwargs)

            with (
                patch.object(
                    module,
                    "publish_directory_noreplace",
                    side_effect=inject_symlink,
                ),
                self.assertRaises(ComposedGameError),
            ):
                module._publish_catalog_generation(game, state, entries)
            self.assertEqual(b"foreign-destination\n", sentinel.read_bytes())
            self.assertTrue(observed["destination"].is_symlink())

    @unittest.skipUnless(os.name == "posix", "POSIX-backed Windows generation seam")
    def test_windows_generation_stage_handle_spans_write_and_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "windows-generation-seam"
            create_game_project(
                game,
                game_id="windows_generation_seam",
                title="Windows Generation Seam",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state, entries = self._next_generation_entries(game)
            write = module._write_generation_payload
            verify_generation = module._verify_generation_directory
            opened_handles: dict[int, Path] = {}
            active_handles: dict[int, Path] = {}
            close_calls: list[int] = []
            catalog_stage_path: Path | None = None
            catalog_stage_handle: int | None = None
            catalog_stage_verifications = 0
            publication_destination: Path | None = None
            publication_lease_active = False
            verification_during_lease = False

            def create_private(path: Path) -> None:
                path.mkdir(mode=0o700)

            def lock_directory(path: Path) -> int:
                handle = len(opened_handles) + 1
                opened_handles[handle] = path
                active_handles[handle] = path
                return handle

            def close_handle(handle: int) -> None:
                self.assertIn(handle, active_handles)
                if handle == catalog_stage_handle:
                    self.assertGreaterEqual(catalog_stage_verifications, 2)
                del active_handles[handle]
                close_calls.append(handle)

            def assert_pinned_write(
                stage: Path,
                payload: bytes,
                *,
                directory_descriptor: int | None,
            ) -> None:
                nonlocal catalog_stage_handle, catalog_stage_path
                stage_handles = [
                    handle
                    for handle, retained_path in active_handles.items()
                    if retained_path == stage
                ]
                self.assertEqual(1, len(stage_handles))
                catalog_stage_path = stage
                catalog_stage_handle = stage_handles[0]
                self.assertIsNone(directory_descriptor)
                write(
                    stage,
                    payload,
                    directory_descriptor=directory_descriptor,
                )

            @contextmanager
            def simulate_windows_publish(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> Iterator[tuple[int, int]]:
                nonlocal publication_destination, publication_lease_active
                del kwargs
                identity = module.directory_identity(
                    source,
                    context="simulated Windows publication source",
                )
                source.rename(destination)
                publication_destination = destination
                publication_lease_active = True
                try:
                    yield identity
                finally:
                    publication_lease_active = False

            def assert_verified_within_lease(
                path: Path,
                payload: bytes,
                *,
                expected_identity: tuple[int, int],
            ) -> None:
                nonlocal catalog_stage_verifications, verification_during_lease
                stage_handle = catalog_stage_handle
                is_catalog_stage = path == catalog_stage_path
                if is_catalog_stage:
                    self.assertIsNotNone(stage_handle)
                    assert stage_handle is not None
                    self.assertEqual(path, active_handles.get(stage_handle))
                if path == publication_destination and publication_lease_active:
                    verification_during_lease = True
                verify_generation(
                    path,
                    payload,
                    expected_identity=expected_identity,
                )
                if is_catalog_stage:
                    self.assertEqual(path, active_handles.get(stage_handle))
                    catalog_stage_verifications += 1

            with (
                patch.object(module, "_generation_platform", return_value="windows"),
                patch.object(
                    module.resource_snapshot_module,
                    "_windows_create_private_directory",
                    side_effect=create_private,
                ),
                patch.object(
                    module.resource_snapshot_module,
                    "_windows_lock_directory",
                    side_effect=lock_directory,
                ),
                patch.object(
                    module.resource_snapshot_module,
                    "_windows_close_handle",
                    side_effect=close_handle,
                ),
                patch.object(
                    module,
                    "_write_generation_payload",
                    side_effect=assert_pinned_write,
                ),
                patch.object(
                    module,
                    "_verify_generation_directory",
                    side_effect=assert_verified_within_lease,
                ),
                patch.object(
                    module,
                    "publish_directory_noreplace",
                    side_effect=simulate_windows_publish,
                ),
            ):
                published = module._publish_catalog_generation(game, state, entries)
            self.assertEqual(2, len(published.entries))
            self.assertTrue(verification_during_lease)
            self.assertEqual(2, catalog_stage_verifications)
            self.assertIsNotNone(catalog_stage_handle)
            assert catalog_stage_handle is not None
            self.assertEqual(1, close_calls.count(catalog_stage_handle))
            self.assertEqual({}, active_handles)
            self.assertEqual(set(opened_handles), set(close_calls))
            self.assertEqual(len(opened_handles), len(close_calls))

    @unittest.skipUnless(os.name == "nt", "native Windows no-delete handle semantics")
    def test_native_windows_generation_stage_handle_blocks_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "windows-native-generation"
            create_game_project(
                game,
                game_id="windows_native_generation",
                title="Windows Native Generation",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            from worldforge import composed_game as module

            state, entries = self._next_generation_entries(game)
            write = module._write_generation_payload
            attempts: list[str] = []

            def attempt_swap(
                stage: Path,
                payload: bytes,
                *,
                directory_descriptor: int | None,
            ) -> None:
                try:
                    stage.rename(stage.with_name(f"{stage.name}.swapped"))
                except OSError:
                    attempts.append("blocked")
                else:
                    attempts.append("renamed")
                write(
                    stage,
                    payload,
                    directory_descriptor=directory_descriptor,
                )

            with patch.object(
                module,
                "_write_generation_payload",
                side_effect=attempt_swap,
            ):
                published = module._publish_catalog_generation(game, state, entries)
            self.assertEqual(["blocked"], attempts)
            self.assertEqual(2, len(published.entries))

    def test_cross_catalog_world_hash_must_match(self) -> None:
        legacy = (
            SimpleNamespace(
                world_id="neutral_world",
                release_id="1.0.0",
                worldpack_hash="a" * 64,
            ),
        )
        composed = (
            SimpleNamespace(
                world_id="neutral_world",
                release_id="1.0.0",
                world_content_hash="b" * 64,
            ),
        )
        with self.assertRaisesRegex(ComposedCatalogError, "disagree"):
            validate_cross_catalog_world_hashes(legacy, composed)  # type: ignore[arg-type]

    def test_composition_plan_orders_planes_layers_and_audio_independently(self) -> None:
        plan = build_composition_plan(
            ("3d", "2d"),
            (
                {
                    "plane": "ui_overlay",
                    "representation": "2d",
                    "slot": "ui:font",
                    "asset_id": "font",
                    "pack": "renderpack",
                },
                {
                    "plane": "audio",
                    "representation": "audio",
                    "slot": "music:theme",
                    "asset_id": "theme",
                    "pack": "renderpack",
                },
                {
                    "plane": "world_base",
                    "representation": "3d",
                    "slot": "actor:hero",
                    "asset_id": "hero",
                    "pack": "assetpack",
                },
                {
                    "plane": "world_overlay",
                    "representation": "2d",
                    "slot": "fx:weather",
                    "asset_id": "rain",
                    "pack": "renderpack",
                },
            ),
        )
        self.assertEqual(
            ["world_base", "world_overlay", "ui_overlay"],
            [item.plane for item in plan.draws],
        )
        self.assertEqual(["music:theme"], [item.slot for item in plan.audio])

    def test_resource_snapshot_owner_exit_preserves_primary_identity(self) -> None:
        owner = object.__new__(resource_snapshot_module.ResourceSnapshotOwner)
        owner._closed = False  # noqa: SLF001
        primary = RuntimeError("snapshot body primary")
        cleanup = resource_snapshot_module.ResourceSnapshotError(
            "snapshot cleanup\nwith unsafe whitespace"
        )
        caught: BaseException | None = None
        try:
            try:
                with patch.object(
                    resource_snapshot_module.ResourceSnapshotOwner,
                    "close",
                    side_effect=cleanup,
                    autospec=True,
                ):
                    with owner:
                        raise primary
            except RuntimeError as exc:
                caught = exc
        finally:
            owner._closed = True  # noqa: SLF001
        self.assertIs(primary, caught)
        notes = getattr(primary, "__notes__", ())
        self.assertTrue(any("snapshot cleanup with unsafe whitespace" in note for note in notes))
        self.assertTrue(all("\n" not in note for note in notes))

    def test_resource_snapshot_owner_exit_cleanup_only_is_snapshot_error(self) -> None:
        owner = object.__new__(resource_snapshot_module.ResourceSnapshotOwner)
        owner._closed = False  # noqa: SLF001
        cleanup = resource_snapshot_module.ResourceSnapshotError("snapshot cleanup-only")
        try:
            with (
                patch.object(
                    resource_snapshot_module.ResourceSnapshotOwner,
                    "close",
                    side_effect=cleanup,
                    autospec=True,
                ),
                self.assertRaises(resource_snapshot_module.ResourceSnapshotError) as raised,
            ):
                with owner:
                    pass
        finally:
            owner._closed = True  # noqa: SLF001
        self.assertIs(cleanup, raised.exception)

    def test_glb_read_preserves_primary_when_descriptor_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource.glb"
            path.write_bytes(b"glTF")
            opened: list[int] = []
            original_open = os.open
            original_close = os.close

            def record_open(*args, **kwargs):
                descriptor = original_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            cleanup = OSError("GLB close\ncleanup")
            try:
                with (
                    patch.object(gltf_module.os, "open", side_effect=record_open),
                    patch.object(gltf_module.os, "read", side_effect=OSError("GLB read primary")),
                    patch.object(gltf_module.os, "close", side_effect=cleanup),
                    self.assertRaises(gltf_module.GLBError) as raised,
                ):
                    gltf_module._read_regular_file(path, max_bytes=16)  # noqa: SLF001
            finally:
                for descriptor in opened:
                    original_close(descriptor)
        self.assertIn("GLB read primary", str(raised.exception))
        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(any("GLB close cleanup" in note for note in notes))
        self.assertTrue(all("\n" not in note for note in notes))

    def test_glb_descriptor_read_requests_binary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource.glb"
            payload = b"glTF\x1a\r\nbinary"
            path.write_bytes(payload)
            host_binary_flag = getattr(os, "O_BINARY", 0)
            binary_flag = host_binary_flag or 0x8000
            opened_flags: list[int] = []
            original_open = os.open

            def record_open(target: object, flags: int, *args: object, **kwargs: object) -> int:
                opened_flags.append(flags)
                return original_open(
                    target,
                    flags if host_binary_flag else flags & ~binary_flag,
                    *args,
                    **kwargs,
                )

            with (
                patch.object(gltf_module.os, "O_BINARY", binary_flag, create=True),
                patch.object(gltf_module.os, "open", side_effect=record_open),
            ):
                self.assertEqual(
                    payload,
                    gltf_module._read_regular_file(path, max_bytes=len(payload)),  # noqa: SLF001
                )

        self.assertEqual(1, len(opened_flags))
        self.assertTrue(opened_flags[0] & binary_flag)

    def test_glb_descriptor_cleanup_only_is_glb_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource.glb"
            path.write_bytes(b"glTF")
            opened: list[int] = []
            original_open = os.open
            original_close = os.close

            def record_open(*args, **kwargs):
                descriptor = original_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            cleanup = OSError("GLB cleanup-only")
            try:
                with (
                    patch.object(gltf_module.os, "open", side_effect=record_open),
                    patch.object(gltf_module.os, "close", side_effect=cleanup),
                    self.assertRaises(gltf_module.GLBError) as raised,
                ):
                    gltf_module._read_regular_file(path, max_bytes=16)  # noqa: SLF001
            finally:
                for descriptor in opened:
                    original_close(descriptor)
        self.assertIs(cleanup, raised.exception.__cause__)

    def test_stable_file_capture_preserves_programmer_value_error_identity(self) -> None:
        primary = ValueError("stable-file programmer error")

        class FailingOwner:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def materialize_file(self, *_args, **_kwargs):
                raise primary

        with (
            patch.object(
                composed_catalog_module,
                "ResourceSnapshotOwner",
                return_value=FailingOwner(),
            ),
            self.assertRaises(ValueError) as raised,
        ):
            composed_catalog_module._stable_file_record(Path("contract.json"))  # noqa: SLF001
        self.assertIs(primary, raised.exception)

    def test_stable_file_capture_normalizes_documented_snapshot_error(self) -> None:
        malformed = resource_snapshot_module.ResourceSnapshotError("malformed stable-file input")

        class FailingOwner:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def materialize_file(self, *_args, **_kwargs):
                raise malformed

        with (
            patch.object(
                composed_catalog_module,
                "ResourceSnapshotOwner",
                return_value=FailingOwner(),
            ),
            self.assertRaises(ComposedCatalogError) as raised,
        ):
            composed_catalog_module._stable_file_record(Path("contract.json"))  # noqa: SLF001
        self.assertIs(malformed, raised.exception.__cause__)
        self.assertNotIn("Traceback", str(raised.exception))

    def test_composed_catalog_non_finite_json_remains_a_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_bytes(b'{"value":NaN}')
            with self.assertRaises(ComposedCatalogError) as raised:
                composed_catalog_module._read_object(path)  # noqa: SLF001
        self.assertIn("non-finite JSON number", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))

    def test_import_verifier_preserves_programmer_value_error_identity(self) -> None:
        primary = ValueError("verifier programmer error")
        with (
            patch.object(
                composed_game_module,
                "_platform_from_manifest",
                return_value="linux_x86_64",
            ),
            patch.object(
                composed_game_module,
                "verify_composed_runtime_bundle",
                side_effect=primary,
            ),
            self.assertRaises(ValueError) as raised,
        ):
            import_composed_bundle(
                "bundle",
                "game",
                expected_bundle_hash="0" * 64,
            )
        self.assertIs(primary, raised.exception)

    def test_import_verifier_normalizes_documented_contract_error(self) -> None:
        malformed = composed_bundle_module.ComposedBundleError("malformed composed bundle")
        with (
            patch.object(
                composed_game_module,
                "_platform_from_manifest",
                return_value="linux_x86_64",
            ),
            patch.object(
                composed_game_module,
                "verify_composed_runtime_bundle",
                side_effect=malformed,
            ),
            self.assertRaises(ComposedGameError) as raised,
        ):
            import_composed_bundle(
                "bundle",
                "game",
                expected_bundle_hash="0" * 64,
            )
        self.assertIs(malformed, raised.exception.__cause__)
        self.assertNotIn("Traceback", str(raised.exception))

    def test_game_root_boundary_preserves_programmer_value_error_identity(self) -> None:
        primary = ValueError("game-root programmer error")
        with (
            patch.object(
                composed_game_module,
                "require_standalone_game_root",
                side_effect=primary,
            ),
            self.assertRaises(ValueError) as raised,
        ):
            composed_game_module._import_verified(  # noqa: SLF001
                SimpleNamespace(),
                "game",
            )
        self.assertIs(primary, raised.exception)

    def test_composed_bundle_descriptor_cleanup_preserves_primary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            primary = composed_bundle_module.ComposedBundleError("bundle primary")
            cleanup = OSError("bundle close\ncleanup")
            caught: BaseException | None = None
            try:
                try:
                    with patch.object(composed_bundle_module.os, "close", side_effect=cleanup):
                        try:
                            raise primary
                        finally:
                            composed_bundle_module._close_descriptor(  # noqa: SLF001
                                descriptor,
                                context="composed bundle descriptor cleanup",
                            )
                except composed_bundle_module.ComposedBundleError as exc:
                    caught = exc
            finally:
                original_close(descriptor)
        self.assertIs(primary, caught)
        self.assertTrue(
            any("bundle close cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_composed_bundle_descriptor_cleanup_only_is_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            cleanup = OSError("bundle cleanup-only")
            try:
                with (
                    patch.object(composed_bundle_module.os, "close", side_effect=cleanup),
                    self.assertRaises(composed_bundle_module.ComposedBundleError) as raised,
                ):
                    composed_bundle_module._close_descriptor(  # noqa: SLF001
                        descriptor,
                        context="composed bundle descriptor cleanup",
                    )
            finally:
                original_close(descriptor)
        self.assertIs(cleanup, raised.exception.__cause__)

    def test_composed_game_descriptor_cleanup_preserves_primary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            primary = ComposedGameError("game primary")
            cleanup = OSError("game close\ncleanup")
            caught: BaseException | None = None
            try:
                try:
                    with patch.object(composed_game_module.os, "close", side_effect=cleanup):
                        try:
                            raise primary
                        finally:
                            composed_game_module._close_descriptor(  # noqa: SLF001
                                descriptor,
                                context="composed game descriptor cleanup",
                            )
                except ComposedGameError as exc:
                    caught = exc
            finally:
                original_close(descriptor)
        self.assertIs(primary, caught)
        self.assertTrue(
            any("game close cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_composed_game_descriptor_cleanup_only_is_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            cleanup = OSError("game cleanup-only")
            try:
                with (
                    patch.object(composed_game_module.os, "close", side_effect=cleanup),
                    self.assertRaises(ComposedGameError) as raised,
                ):
                    composed_game_module._close_descriptor(  # noqa: SLF001
                        descriptor,
                        context="composed game descriptor cleanup",
                    )
            finally:
                original_close(descriptor)
        self.assertIs(cleanup, raised.exception.__cause__)

    def test_game_control_descriptor_cleanup_preserves_primary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            primary = game_control_io_module.GameControlIOError("control primary")
            cleanup = OSError("control close\ncleanup")
            caught: BaseException | None = None
            try:
                try:
                    with patch.object(game_control_io_module.os, "close", side_effect=cleanup):
                        try:
                            raise primary
                        finally:
                            game_control_io_module._close_descriptor(  # noqa: SLF001
                                descriptor,
                                context="game control descriptor cleanup",
                            )
                except game_control_io_module.GameControlIOError as exc:
                    caught = exc
            finally:
                original_close(descriptor)
        self.assertIs(primary, caught)
        self.assertTrue(
            any("control close cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_game_control_descriptor_cleanup_only_is_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self._open_regular_test_descriptor(Path(directory))
            original_close = os.close
            cleanup = OSError("control cleanup-only")
            try:
                with (
                    patch.object(game_control_io_module.os, "close", side_effect=cleanup),
                    self.assertRaises(game_control_io_module.GameControlIOError) as raised,
                ):
                    game_control_io_module._close_descriptor(  # noqa: SLF001
                        descriptor,
                        context="game control descriptor cleanup",
                    )
            finally:
                original_close(descriptor)
        self.assertIs(cleanup, raised.exception.__cause__)

    def test_composed_cleanup_failure_is_a_public_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "cleanup-only"
            create_game_project(game, game_id="cleanup_only", title="Cleanup Only")
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            verified = verify_composed_release(load_composed_catalog(game)[0], game)
            owner = verified._owner  # noqa: SLF001
            close = resource_snapshot_module.ResourceSnapshotOwner.close

            def fail_selected_owner(candidate) -> None:
                if candidate is owner:
                    raise resource_snapshot_module.ResourceSnapshotError(
                        "injected cleanup-only failure"
                    )
                close(candidate)

            try:
                with (
                    patch.object(
                        resource_snapshot_module.ResourceSnapshotOwner,
                        "close",
                        side_effect=fail_selected_owner,
                        autospec=True,
                    ),
                    self.assertRaisesRegex(
                        ComposedCatalogError,
                        "could not close composed release snapshot",
                    ) as raised,
                ):
                    verified.close()
                self.assertIsInstance(
                    raised.exception.__cause__,
                    resource_snapshot_module.ResourceSnapshotError,
                )
            finally:
                verified.close()

    def test_composed_body_exception_survives_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "primary-and-cleanup"
            create_game_project(
                game,
                game_id="primary_and_cleanup",
                title="Primary And Cleanup",
            )
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            verified = verify_composed_release(load_composed_catalog(game)[0], game)
            owner = verified._owner  # noqa: SLF001
            close = resource_snapshot_module.ResourceSnapshotOwner.close
            primary = RuntimeError("primary body failure")

            def fail_selected_owner(candidate) -> None:
                if candidate is owner:
                    raise resource_snapshot_module.ResourceSnapshotError(
                        "injected cleanup-after-primary failure"
                    )
                close(candidate)

            try:
                caught: BaseException | None = None
                try:
                    with patch.object(
                        resource_snapshot_module.ResourceSnapshotOwner,
                        "close",
                        side_effect=fail_selected_owner,
                        autospec=True,
                    ):
                        with verified:
                            raise primary
                except BaseException as exc:  # noqa: BLE001
                    caught = exc
                self.assertIs(primary, caught)
                self.assertTrue(
                    any(
                        "cleanup-after-primary" in note
                        for note in getattr(primary, "__notes__", ())
                    )
                )
            finally:
                verified.close()

    def test_snapshot_bundle_primary_identity_survives_cleanup_failure(self) -> None:
        primary = ComposedCatalogError("snapshot capture primary")
        cleanup = resource_snapshot_module.ResourceSnapshotError("snapshot capture cleanup")

        class FailingOwner:
            def materialize_file(self, *_args, **_kwargs):
                raise primary

            def close(self):
                raise cleanup

        with (
            patch.object(
                composed_catalog_module,
                "ResourceSnapshotOwner",
                return_value=FailingOwner(),
            ),
            self.assertRaises(ComposedCatalogError) as raised,
        ):
            composed_catalog_module._snapshot_bundle(Path("."), {}, b"")  # noqa: SLF001
        self.assertIs(primary, raised.exception)
        self.assertTrue(
            any("snapshot capture cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_public_composed_verification_primary_survives_cleanup_failure(self) -> None:
        primary = ComposedCatalogError("public verification primary")
        cleanup = resource_snapshot_module.ResourceSnapshotError("public verification cleanup")
        release = SimpleNamespace()

        class FailingOwner:
            def close(self):
                raise cleanup

        owner = FailingOwner()

        def fail_after_owner(_release, _project_root, owner_out):
            owner_out.append(owner)
            raise primary

        with (
            patch.object(
                composed_catalog_module,
                "load_composed_catalog",
                return_value=(release,),
            ),
            patch.object(
                composed_catalog_module,
                "_verify_composed_release",
                side_effect=fail_after_owner,
            ),
            self.assertRaises(ComposedCatalogError) as raised,
        ):
            composed_catalog_module.verify_composed_release(release, Path("."))  # type: ignore[arg-type]
        self.assertIs(primary, raised.exception)
        self.assertTrue(
            any("public verification cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_installed_bundle_identity_primary_survives_cleanup_failure(self) -> None:
        primary = composed_bundle_module.ComposedBundleError("post-snapshot identity primary")
        cleanup = composed_bundle_module.ComposedBundleError("post-snapshot cleanup")

        class FailingLoaded:
            def close(self):
                raise cleanup

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            expected_identity = (1, 2)
            with (
                patch.object(
                    composed_bundle_module,
                    "directory_identity",
                    side_effect=(expected_identity, primary),
                ),
                patch.object(
                    composed_bundle_module,
                    "_snapshot_bundle_root",
                    return_value=(SimpleNamespace(), {}, []),
                ),
                patch.object(
                    composed_bundle_module,
                    "_load_from_snapshot",
                    return_value=FailingLoaded(),
                ),
                self.assertRaises(composed_bundle_module.ComposedBundleError) as raised,
            ):
                composed_bundle_module.verify_installed_composed_runtime_bundle(
                    root,
                    expected_directory_identity=expected_identity,
                    expected_bundle_hash="0" * 64,
                    platform="linux_x86_64",
                    runtime_api_version="0.5.0",
                    registry=StaticRuntimeAdapterRegistry([]),
                )
        self.assertIs(primary, raised.exception)
        self.assertTrue(
            any("post-snapshot cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_import_candidate_primary_survives_cleanup_failure(self) -> None:
        primary = ComposedGameError("import entry correlation primary")
        cleanup = composed_bundle_module.ComposedBundleError("import candidate cleanup")

        class FailingVerified:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, exc, _traceback):
                try:
                    raise cleanup
                except composed_bundle_module.ComposedBundleError as cleanup_error:
                    if exc is None:
                        raise
                    exc.add_note(f"composed bundle snapshot cleanup failed: {cleanup_error}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = SimpleNamespace(bundle_hash="0" * 64)
            with (
                patch.object(
                    composed_game_module,
                    "directory_identity",
                    return_value=(1, 2),
                ),
                patch.object(
                    composed_game_module,
                    "_platform_from_manifest",
                    return_value="linux_x86_64",
                ),
                patch.object(
                    composed_game_module,
                    "verify_installed_composed_runtime_bundle",
                    return_value=FailingVerified(),
                ),
                patch.object(
                    composed_game_module,
                    "_catalog_entry",
                    side_effect=primary,
                ),
                self.assertRaises(ComposedGameError) as raised,
            ):
                composed_game_module._verify_import_candidate(  # noqa: SLF001
                    root,
                    bundle,  # type: ignore[arg-type]
                    {"path": "game_data/compositions/test"},
                )
        self.assertIs(primary, raised.exception)
        self.assertTrue(
            any("import candidate cleanup" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_valid_mixed_pack_slot_owners_use_exact_integral_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash, registry = self._build_asset_bundle(
                root,
                name="mixed",
                mixed=True,
            )
            game = root / "mixed-game"
            create_game_project(game, game_id="mixed_game", title="Mixed Game")
            with patch(
                "worldforge.composed_game.BUILTIN_COMPOSED_ADAPTERS",
                registry,
            ):
                import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            release = load_composed_catalog(game)[0]
            with verify_composed_release(release, game) as verified:
                self.assertEqual(
                    ["actor:neutral", "ui:font"],
                    [draw.slot for draw in verified.presentation_plan.draws],
                )

    def test_resealed_renderpack_ghost_owner_fails_standalone_import_and_package(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "render-ghost"
            create_game_project(game, game_id="render_ghost", title="Render Ghost")
            import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            release = self._reseal_composition_owner(
                game,
                lambda composition: composition["slot_owners"][0].__setitem__(
                    "asset_id",
                    "ghost_render_asset",
                ),
            )
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "slot ownership",
            ):
                verify_composed_release(release, game)

            second_game = root / "render-ghost-import"
            create_game_project(
                second_game,
                game_id="render_ghost_import",
                title="Render Ghost Import",
            )
            resealed_source = root / "resealed-render-ghost"
            shutil.copytree(game / release.path, resealed_source)
            with self.assertRaisesRegex(
                ComposedGameError,
                "incompatible|slot ownership",
            ):
                import_composed_bundle(
                    resealed_source,
                    second_game,
                    expected_bundle_hash=release.bundle_hash,
                )

            package = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "scripts/package_game.py",
                    "--output",
                    str(root / "render-ghost.zip"),
                ],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, package.returncode, package.stdout + package.stderr)
            self.assertIn("slot ownership", package.stderr)
            self.assertNotIn("Traceback", package.stderr)

    def test_resealed_assetpack_ghost_owner_fails_integral_standalone_verification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash, registry = self._build_asset_bundle(root)
            game = root / "asset-ghost"
            create_game_project(game, game_id="asset_ghost", title="Asset Ghost")
            with patch(
                "worldforge.composed_game.BUILTIN_COMPOSED_ADAPTERS",
                registry,
            ):
                import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            release = self._reseal_composition_owner(
                game,
                lambda composition: composition["slot_owners"][0].__setitem__(
                    "asset_id",
                    "ghost_assetpack_asset",
                ),
            )
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "slot ownership",
            ):
                verify_composed_release(release, game)

    def test_runtime_tree_walkers_reject_windows_reparse_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            reparse_directory = assets / "reparse"
            reparse_directory.mkdir(parents=True)
            original_asset_stat = runtime_assetpack_module.path_file_stat

            def asset_stat(path):
                info = original_asset_stat(path)
                if Path(path) == reparse_directory:
                    return SimpleNamespace(
                        st_mode=info.st_mode,
                        st_nlink=info.st_nlink,
                        st_file_attributes=0x400,
                    )
                return info

            with (
                patch.object(
                    runtime_assetpack_module,
                    "path_file_stat",
                    side_effect=asset_stat,
                ),
                self.assertRaisesRegex(
                    runtime_assetpack_module.AssetPackError,
                    "unsafe directory",
                ),
            ):
                runtime_assetpack_module._walk_asset_tree(root)  # noqa: SLF001

            composed_root = root / "composed"
            composed_root.mkdir()
            reparse_file = composed_root / "payload.json"
            reparse_file.write_text("{}\n", encoding="utf-8")
            original_composed_stat = composed_catalog_module.path_file_stat

            def composed_stat(path):
                info = original_composed_stat(path)
                if Path(path) == reparse_file:
                    return SimpleNamespace(
                        st_mode=info.st_mode,
                        st_nlink=info.st_nlink,
                        st_file_attributes=0x400,
                    )
                return info

            with (
                patch.object(
                    composed_catalog_module,
                    "path_file_stat",
                    side_effect=composed_stat,
                ),
                self.assertRaisesRegex(
                    ComposedCatalogError,
                    "unsafe file",
                ),
            ):
                composed_catalog_module._walk_exact_regular_tree(  # noqa: SLF001
                    composed_root
                )

    def test_runtime_tree_walkers_bound_empty_directory_nodes_before_recursion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            for name in ("a", "b", "c"):
                (assets / name).mkdir()
            with self.assertRaisesRegex(
                runtime_assetpack_module.AssetPackError,
                "tree node bound",
            ):
                runtime_assetpack_module._walk_asset_tree(  # noqa: SLF001
                    root,
                    max_nodes=4,
                )

            composed_root = root / "composed"
            composed_root.mkdir()
            for name in ("a", "b", "c"):
                (composed_root / name).mkdir()
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "tree node bound",
            ):
                composed_catalog_module._walk_exact_regular_tree(  # noqa: SLF001
                    composed_root,
                    max_nodes=3,
                )

    def test_resealed_malformed_assetpack_fails_run_verify_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash, registry = self._build_asset_bundle(root)
            game = root / "assetpack-game"
            create_game_project(
                game,
                game_id="assetpack_game",
                title="Assetpack Game",
            )
            with patch(
                "worldforge.composed_game.BUILTIN_COMPOSED_ADAPTERS",
                registry,
            ):
                import_composed_bundle(bundle, game, expected_bundle_hash=bundle_hash)
            original_release = load_composed_catalog(game)[0]
            assetpack_path = game / original_release.path / "packs/assetpack/assetpack.json"
            assetpack = json.loads(assetpack_path.read_bytes())
            glb = next(
                item
                for asset in assetpack["assets"]
                for item in asset["files"]
                if item["media_type"] == "model/gltf-binary"
            )
            resource_relative = f"packs/assetpack/{glb['path']}"
            release = self._reseal_installed_resource(
                game,
                resource_relative,
                b"not a valid GLB\n",
            )
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "composed assetpack is invalid",
            ):
                verify_composed_release(release, game)

            selectors = (
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_3d",
                "--headless-ticks",
                "0",
            )
            run = self._run(game, *selectors)
            self.assertEqual(2, run.returncode, run.stdout + run.stderr)
            self.assertIn("assetpack", run.stderr)
            self.assertNotIn("Traceback", run.stderr)
            verified = subprocess.run(
                [sys.executable, "-I", "scripts/verify_game.py"],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, verified.returncode, verified.stdout + verified.stderr)
            self.assertIn("assetpack", verified.stderr)
            self.assertNotIn("Traceback", verified.stderr)
            package = root / "malformed-assetpack.zip"
            packaged = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "scripts/package_game.py",
                    "--output",
                    str(package),
                ],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, packaged.returncode, packaged.stdout + packaged.stderr)
            self.assertIn("assetpack", packaged.stderr)
            self.assertNotIn("Traceback", packaged.stderr)
            self.assertFalse(package.exists())

    def test_generated_game_preserves_legacy_empty_headless_and_exact_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            create_game_project(
                game,
                game_id="m6_consumer",
                title="M6 Consumer",
                source_revision="test",
            )
            environment = _subprocess_environment()
            headless = subprocess.run(
                [sys.executable, "-I", "run_game.py", "--headless-ticks", "0"],
                cwd=game,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, headless.returncode, headless.stderr)
            self.assertIn("status=empty_catalog", headless.stdout)
            partial = subprocess.run(
                [sys.executable, "-I", "run_game.py", "--profile", "profile_2_5d"],
                cwd=game,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, partial.returncode)
            self.assertIn(
                "composed selection requires --world, --release, and --profile",
                partial.stderr,
            )
            self.assertNotIn("Traceback", partial.stderr)
            abbreviated = subprocess.run(
                [sys.executable, "-I", "run_game.py", "--prof", "profile_2_5d"],
                cwd=game,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, abbreviated.returncode)
            self.assertIn("unrecognized arguments", abbreviated.stderr)

    def test_isoworld_consumer_remains_worldforge_free(self) -> None:
        self.assertIn(".world" + "forge", composed_catalog_module.FORBIDDEN_COMPONENTS)
        for relative in (
            "src/isoworld/content/composed_catalog.py",
            "src/isoworld/render/composition_plan.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import worldforge", source)
            self.assertNotIn("from worldforge", source)

    def test_real_composed_import_headless_replay_verify_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "game"
            create_game_project(game, game_id="composed_game", title="Composed Game")
            imported = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(imported.is_dir())
            self.assertFalse((game / "game_data/.compositions.lock.json.lock").exists())
            self.assertFalse((game / ".composed-import.journal.json").exists())
            self.assertFalse((game / ".composed-import.journal.json.lock").exists())
            release = load_composed_catalog(game)[0]
            with verify_composed_release(release, game) as verified:
                self.assertEqual(
                    sys.platform.startswith("linux")
                    and platform.machine().casefold() in {"amd64", "x86_64"},
                    verified.native_compatible,
                )
            selectors = (
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_2_5d",
            )
            user_data = root / "user-data"
            saved = self._run(
                game,
                *selectors,
                "--headless-ticks",
                "3",
                "--save-on-exit-slot",
                "shared",
                "--record-replay-slot",
                "shared",
                "--user-data",
                str(user_data),
            )
            self.assertEqual(0, saved.returncode, saved.stderr)
            replayed = self._run(
                game,
                *selectors,
                "--replay-slot",
                "shared",
                "--user-data",
                str(user_data),
            )
            self.assertEqual(0, replayed.returncode, replayed.stderr)
            verified = subprocess.run(
                [sys.executable, "-I", "scripts/verify_game.py"],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, verified.returncode, verified.stderr)
            package = root / "game.zip"
            packaged = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "scripts/package_game.py",
                    "--output",
                    str(package),
                ],
                cwd=game,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, packaged.returncode, packaged.stderr)
            extracted = root / "extracted"
            with zipfile.ZipFile(package) as archive:
                package_manifest = json.loads(archive.read("PACKAGE-MANIFEST.json"))
                self.assertEqual(
                    load_composed_catalog_state(game).head_hash,
                    package_manifest["composed_catalog_hash"],
                )
                archive.extractall(extracted)
            extracted_run = self._run(
                extracted,
                *selectors,
                "--headless-ticks",
                "1",
                "--user-data",
                str(root / "extracted-data"),
            )
            self.assertEqual(0, extracted_run.returncode, extracted_run.stderr)

    def test_package_fails_closed_on_valid_generation_swap_before_enumeration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_bundle, first_hash = self._build_bundle(root)
            second_bundle, second_hash = self._build_bundle(
                root,
                name="package-swap",
                bundle_version="1.0.1",
            )
            game = root / "package-swap-game"
            create_game_project(
                game,
                game_id="package_swap_game",
                title="Package Swap Game",
            )
            import_composed_bundle(first_bundle, game, expected_bundle_hash=first_hash)
            script = game / "scripts/package_game.py"
            module_name = f"_package_game_{id(game)}"
            specification = importlib.util.spec_from_file_location(module_name, script)
            assert specification is not None and specification.loader is not None
            module = importlib.util.module_from_spec(specification)
            prior_verify = sys.modules.pop("verify_game", None)
            prior_catalog = sys.modules.pop("isoworld.content.catalog", None)
            sys.path.insert(0, str(game / "src"))
            sys.path.insert(0, str(game / "scripts"))
            try:
                catalog_specification = importlib.util.spec_from_file_location(
                    "isoworld.content.catalog",
                    game / "src/isoworld/content/catalog.py",
                )
                assert (
                    catalog_specification is not None and catalog_specification.loader is not None
                )
                catalog_module = importlib.util.module_from_spec(catalog_specification)
                sys.modules["isoworld.content.catalog"] = catalog_module
                catalog_specification.loader.exec_module(catalog_module)
                specification.loader.exec_module(module)
                enumerate_inputs = module._input_files
                swapped = False

                def publish_then_enumerate(*args, **kwargs):
                    nonlocal swapped
                    import_composed_bundle(
                        second_bundle,
                        game,
                        expected_bundle_hash=second_hash,
                    )
                    swapped = True
                    return enumerate_inputs(*args, **kwargs)

                output = root / "generation-swap.zip"
                with (
                    patch.object(
                        module,
                        "_input_files",
                        side_effect=publish_then_enumerate,
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        "game data differs from the verified package allowlist",
                    ),
                ):
                    module._build_package_locked(output)
                self.assertTrue(swapped)
                self.assertEqual(2, len(load_composed_catalog(game)))
                self.assertFalse(output.exists())
            finally:
                sys.path.remove(str(game / "scripts"))
                sys.path.remove(str(game / "src"))
                sys.modules.pop(module_name, None)
                sys.modules.pop("verify_game", None)
                sys.modules.pop("isoworld.content.catalog", None)
                if prior_verify is not None:
                    sys.modules["verify_game"] = prior_verify
                if prior_catalog is not None:
                    sys.modules["isoworld.content.catalog"] = prior_catalog

    def test_move_then_raise_is_recovered_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "game"
            create_game_project(game, game_id="recovery_game", title="Recovery Game")
            from worldforge import composed_game as module

            catalog_path = game / "game_data/compositions.lock.json"
            catalog_bytes = catalog_path.read_bytes()
            self.assertEqual(
                canonical_json_bytes(json.loads(catalog_bytes)),
                catalog_bytes,
            )
            copy_into_claim = module._copy_owned_bundle_into_claim

            def copy_then_raise(*args: object, **kwargs: object) -> None:
                copy_into_claim(*args, **kwargs)
                raise OSError("injected after claimed directory copy")

            with (
                patch.object(
                    module,
                    "_copy_owned_bundle_into_claim",
                    side_effect=copy_then_raise,
                ),
                self.assertRaisesRegex(OSError, "claimed directory copy"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            journal = game / composed_game_module.CATALOG_PUBLICATION_JOURNAL
            self.assertTrue(journal.is_file())
            active = module._read_catalog_journal_record(game)
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual("copying", active[0]["state"])
            self.assertEqual(catalog_bytes, catalog_path.read_bytes())
            catalog = json.loads(catalog_bytes)
            self.assertEqual([], catalog["entries"])
            recovered = import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self.assertTrue(recovered.is_dir())
            committed = module._read_catalog_journal_record(game)
            self.assertIsNotNone(committed)
            assert committed is not None
            self.assertEqual("committed", committed[0]["state"])
            self.assertFalse((game / "game_data/.compositions.lock.json.lock").exists())
            self.assertEqual(1, len(load_composed_catalog(game)))

    def test_raise_before_claim_copy_preserves_incomplete_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "staged-recovery"
            create_game_project(game, game_id="staged_recovery", title="Staged Recovery")
            from worldforge import composed_game as module

            with (
                patch.object(
                    module,
                    "_copy_owned_bundle_into_claim",
                    side_effect=OSError("injected before claimed directory copy"),
                ),
                self.assertRaisesRegex(OSError, "claimed directory copy"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            stages = tuple(
                path for path in (game / "game_data").rglob(".*.import-*") if path.is_dir()
            )
            self.assertEqual(1, len(stages))
            incomplete = game / "game_data/compositions"
            self.assertFalse(incomplete.exists())
            with self.assertRaisesRegex(
                ComposedGameError,
                "incomplete|not exact|lost its exact|manifest",
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertEqual(
                stages,
                tuple(path for path in (game / "game_data").rglob(".*.import-*") if path.is_dir()),
            )

    def test_recoverable_stage_does_not_authorize_foreign_ancestor_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "foreign-stage-ancestor"
            create_game_project(
                game,
                game_id="foreign_stage_ancestor",
                title="Foreign Stage Ancestor",
            )

            copy_into_claim = composed_game_module._copy_owned_bundle_into_claim

            def copy_then_raise(*args: object, **kwargs: object) -> None:
                copy_into_claim(*args, **kwargs)
                raise OSError("injected after private stage copy")

            with (
                patch.object(
                    composed_game_module,
                    "_copy_owned_bundle_into_claim",
                    side_effect=copy_then_raise,
                ),
                self.assertRaisesRegex(OSError, "private stage copy"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            foreign = game / "game_data/compositions"
            foreign.mkdir()
            (foreign / "sentinel.txt").write_text("foreign\n", encoding="utf-8")
            foreign_identity = composed_game_module.directory_identity(
                foreign,
                context="foreign composition ancestor",
            )

            with self.assertRaisesRegex(
                ComposedGameError,
                "publication root is occupied|ambiguous",
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )

            self.assertEqual(
                foreign_identity,
                composed_game_module.directory_identity(
                    foreign,
                    context="foreign composition ancestor",
                ),
            )
            self.assertEqual(
                "foreign\n",
                (foreign / "sentinel.txt").read_text(encoding="utf-8"),
            )

    def test_catalog_manifest_and_payload_identity_swaps_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            for case, relative in (
                ("catalog", "game_data/compositions.lock.json"),
                (
                    "manifest",
                    "composed-bundle.manifest.json",
                ),
                (
                    "payload",
                    "contracts/runtime-composition.json",
                ),
            ):
                with self.subTest(case=case):
                    game = root / f"game-{case}"
                    create_game_project(
                        game,
                        game_id=f"swap_{case}",
                        title=f"Swap {case}",
                    )
                    imported = import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    entries = load_composed_catalog(game)
                    self.assertEqual(1, len(entries))
                    release = entries[0]
                    if case == "catalog":
                        target = game / relative
                        operation = partial(load_composed_catalog, game)
                    else:
                        target = imported / relative
                        operation = partial(verify_composed_release, release, game)
                    self._assert_identity_swap_rejected(target, operation)

    def test_undeclared_empty_and_symlink_namespaces_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            for case in ("empty", "symlink"):
                with self.subTest(case=case):
                    game = root / f"namespace-{case}"
                    create_game_project(
                        game,
                        game_id=f"namespace_{case}",
                        title=f"Namespace {case}",
                    )
                    imported = import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    release = load_composed_catalog(game)[0]
                    if case == "empty":
                        (imported / "undeclared-empty").mkdir()
                    else:
                        outside = root / "outside-symlink-target"
                        outside.mkdir(exist_ok=True)
                        try:
                            os.symlink(
                                outside,
                                game / "game_data/compositions/undeclared-link",
                                target_is_directory=True,
                            )
                        except (NotImplementedError, OSError):
                            self.skipTest("directory symlinks are unavailable")
                    with self.assertRaisesRegex(
                        ComposedCatalogError,
                        "namespace|unsafe directory",
                    ):
                        if case == "empty":
                            verify_composed_release(release, game)
                        else:
                            load_composed_catalog(game)

    def test_resealed_contract_extras_and_nested_types_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)

            def catalog_extra(document: dict[str, Any]) -> None:
                document["capabilities"][0]["extra"] = True

            def profile_extra(document: dict[str, Any]) -> None:
                document["extra"] = True

            def adapter_bad_budget(document: dict[str, Any]) -> None:
                document["budgets"]["max_assets"] = "5"

            def composition_owner_extra(document: dict[str, Any]) -> None:
                document["slot_owners"][0]["extra"] = True

            cases = (
                (
                    "catalog-extra",
                    "contracts/runtime-capability-catalog.json",
                    catalog_extra,
                ),
                (
                    "profile-extra",
                    "contracts/runtime-presentation-profile.json",
                    profile_extra,
                ),
                (
                    "adapter-budget-type",
                    "contracts/runtime-adapter.json",
                    adapter_bad_budget,
                ),
                (
                    "composition-owner-extra",
                    "contracts/runtime-composition.json",
                    composition_owner_extra,
                ),
            )
            for case, relative, mutation in cases:
                with self.subTest(case=case):
                    game = root / case
                    create_game_project(
                        game,
                        game_id=case.replace("-", "_"),
                        title=case,
                    )
                    import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    release = self._forge_installed_contract(
                        game,
                        relative,
                        mutation,
                    )
                    with self.assertRaises(ComposedCatalogError):
                        verify_composed_release(release, game)

    def test_resealed_compatibility_report_shape_and_correlations_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)

            def add_extra(document: dict[str, Any]) -> None:
                document["extra"] = True

            def replace_passed_type(document: dict[str, Any]) -> None:
                document["checks"][0]["passed"] = "yes"

            def misbind_adapter(document: dict[str, Any]) -> None:
                document["adapter_hash"] = "0" * 64

            for case, mutation in (
                ("extra", add_extra),
                ("passed-type", replace_passed_type),
                ("adapter-correlation", misbind_adapter),
            ):
                with self.subTest(case=case):
                    game = root / f"compatibility-{case}"
                    create_game_project(
                        game,
                        game_id=f"compatibility_{case.replace('-', '_')}",
                        title=f"Compatibility {case}",
                    )
                    import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    release = self._forge_manifest_and_payload(
                        game,
                        payload_relative="evidence/runtime-compatibility-report.json",
                        payload_mutation=mutation,
                    )
                    with self.assertRaises(ComposedCatalogError):
                        verify_composed_release(release, game)

    def test_resealed_license_inventory_must_be_exact_and_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)

            def remove_inventory(manifest: dict[str, Any]) -> None:
                manifest["licenses"] = []

            def duplicate_inventory(manifest: dict[str, Any]) -> None:
                manifest["licenses"].append(dict(manifest["licenses"][0]))

            def add_record_field(manifest: dict[str, Any]) -> None:
                manifest["licenses"][0]["extra"] = True

            for case, mutation in (
                ("empty", remove_inventory),
                ("duplicate", duplicate_inventory),
                ("record-extra", add_record_field),
            ):
                with self.subTest(case=case):
                    game = root / f"licenses-{case}"
                    create_game_project(
                        game,
                        game_id=f"licenses_{case.replace('-', '_')}",
                        title=f"Licenses {case}",
                    )
                    import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    release = self._forge_manifest_and_payload(
                        game,
                        manifest_mutation=mutation,
                    )
                    with self.assertRaises(ComposedCatalogError):
                        verify_composed_release(release, game)

    def test_resealed_composition_document_misbindings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            mutations = {
                "profile": lambda composition: composition.__setitem__(
                    "profile",
                    {"id": "profile_2d", "content_hash": "f" * 64},
                ),
                "adapter": lambda composition: composition.__setitem__(
                    "adapter",
                    {
                        "id": "pyray_3d_v1",
                        "version": "0.1.0",
                        "content_hash": "f" * 64,
                    },
                ),
                "capability_catalog": lambda composition: composition.__setitem__(
                    "capability_catalog_hash",
                    "f" * 64,
                ),
            }
            for case, mutation in mutations.items():
                with self.subTest(case=case):
                    game = root / f"misbound-{case}"
                    create_game_project(
                        game,
                        game_id=f"misbound_{case}",
                        title=f"Misbound {case}",
                    )
                    import_composed_bundle(
                        bundle,
                        game,
                        expected_bundle_hash=bundle_hash,
                    )
                    release = self._forge_installed_composition(game, mutation)
                    with self.assertRaisesRegex(
                        ComposedCatalogError,
                        f"composition {case.replace('_', ' ')}",
                    ):
                        verify_composed_release(release, game)

    def test_malformed_capabilities_are_contractual_cli_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "malformed-capabilities"
            create_game_project(
                game,
                game_id="malformed_capabilities",
                title="Malformed capabilities",
            )
            import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            self._forge_installed_composition(
                game,
                lambda composition: composition.__setitem__(
                    "required_capability_ids",
                    [{}],
                ),
            )
            result = self._run(
                game,
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_2_5d",
                "--headless-ticks",
                "0",
            )
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("required_capability_ids", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_standalone_adapter_frame_budget_matches_exact_schema_maximum(self) -> None:
        maximum = json.loads((M6_FIXTURES / "adapters/isoworld_raylib_2_5d.json").read_bytes())
        maximum["budgets"]["target_frame_milliseconds"] = 1000
        maximum["content_hash"] = canonical_payload_hash(maximum)
        composed_catalog_module._validate_adapter(maximum)  # noqa: SLF001

        one_over = json.loads((M6_FIXTURES / "adapters/isoworld_raylib_2_5d.json").read_bytes())
        one_over["budgets"]["target_frame_milliseconds"] = 1001
        one_over["content_hash"] = canonical_payload_hash(one_over)
        with self.assertRaisesRegex(
            ComposedCatalogError,
            "target_frame_milliseconds.*at most 1000",
        ):
            composed_catalog_module._validate_adapter(one_over)  # noqa: SLF001

        with self.assertRaisesRegex(
            ComposedCatalogError,
            "target_frame_milliseconds.*at most 1000",
        ):
            composed_catalog_module._positive_bounded_number(  # noqa: SLF001
                10**10000,
                "runtime adapter/budgets/target_frame_milliseconds",
                maximum=1000,
            )

        class OverflowingFloat(float):
            def __float__(self) -> float:
                raise OverflowError("injected numeric conversion overflow")

        with self.assertRaisesRegex(
            ComposedCatalogError,
            "target_frame_milliseconds.*at most 1000",
        ):
            composed_catalog_module._positive_bounded_number(  # noqa: SLF001
                OverflowingFloat(1.0),
                "runtime adapter/budgets/target_frame_milliseconds",
                maximum=1000,
            )

    def test_resealed_adapter_huge_frame_budget_is_a_concise_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "huge-frame-budget"
            create_game_project(
                game,
                game_id="huge_frame_budget",
                title="Huge Frame Budget",
            )
            import_composed_bundle(
                bundle,
                game,
                expected_bundle_hash=bundle_hash,
            )
            release = self._forge_installed_contract(
                game,
                "contracts/runtime-adapter.json",
                lambda adapter: adapter["budgets"].__setitem__(
                    "target_frame_milliseconds",
                    10**1000,
                ),
            )
            with self.assertRaisesRegex(
                ComposedCatalogError,
                "target_frame_milliseconds.*at most 1000",
            ):
                verify_composed_release(release, game)

            result = self._run(
                game,
                "--world",
                "foundation_slice",
                "--release",
                "1.0.0",
                "--profile",
                "profile_2_5d",
                "--headless-ticks",
                "0",
            )
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("target_frame_milliseconds", result.stderr)
            self.assertNotIn("OverflowError", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_recovery_never_removes_foreign_control_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            game = root / "journal-replacement"
            create_game_project(game, game_id="journal_replacement", title="Journal replacement")
            from worldforge import composed_game as module

            copy_into_claim = module._copy_owned_bundle_into_claim

            def copy_then_raise(*args: object, **kwargs: object) -> None:
                copy_into_claim(*args, **kwargs)
                raise OSError("injected after claimed directory copy")

            with (
                patch.object(
                    module,
                    "_copy_owned_bundle_into_claim",
                    side_effect=copy_then_raise,
                ),
                self.assertRaisesRegex(OSError, "claimed directory copy"),
            ):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            control = game / module.CATALOG_PUBLICATION_JOURNAL
            foreign = canonical_json_bytes({"foreign": True})
            control.write_bytes(foreign)
            control.chmod(0o640)
            foreign_mode = stat.S_IMODE(control.stat().st_mode)
            with self.assertRaises(ComposedGameError):
                import_composed_bundle(
                    bundle,
                    game,
                    expected_bundle_hash=bundle_hash,
                )
            self.assertEqual(foreign, control.read_bytes())
            self.assertEqual(foreign_mode, stat.S_IMODE(control.stat().st_mode))

    def test_recovery_fails_closed_on_malformed_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, bundle_hash = self._build_bundle(root)
            from worldforge import composed_game as module

            for case in ("legacy", "composed"):
                with self.subTest(case=case):
                    game = root / f"recovery-{case}"
                    create_game_project(
                        game,
                        game_id=f"recovery_{case}",
                        title=f"Recovery {case}",
                    )
                    copy_into_claim = module._copy_owned_bundle_into_claim

                    def copy_then_raise(
                        *args: object,
                        copy_exact=copy_into_claim,
                        **kwargs: object,
                    ) -> None:
                        copy_exact(*args, **kwargs)
                        raise OSError("injected after claimed directory copy")

                    with (
                        patch.object(
                            module,
                            "_copy_owned_bundle_into_claim",
                            side_effect=copy_then_raise,
                        ),
                        self.assertRaisesRegex(OSError, "claimed directory copy"),
                    ):
                        import_composed_bundle(
                            bundle,
                            game,
                            expected_bundle_hash=bundle_hash,
                        )
                    evidence = tuple((game / "game_data").rglob("composed-bundle.manifest.json"))
                    self.assertEqual(1, len(evidence))
                    target = (
                        game / "game_data/worlds.lock.json"
                        if case == "legacy"
                        else game / "game_data/compositions.lock.json"
                    )
                    target.write_bytes(canonical_json_bytes({"malformed": True}))
                    with self.assertRaises((ComposedGameError, ValueError)):
                        import_composed_bundle(
                            bundle,
                            game,
                            expected_bundle_hash=bundle_hash,
                        )
                    self.assertEqual(
                        evidence,
                        tuple((game / "game_data").rglob("composed-bundle.manifest.json")),
                    )


if __name__ == "__main__":
    unittest.main()
