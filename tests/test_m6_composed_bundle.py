from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import worldforge.composed_bundle as composed_module
from isoworld.content.loader import load_worldpack
from isoworld.runtime_adapter import RuntimeAdapterKey, StaticRuntimeAdapterRegistry
from worldforge.assetpack import build_assetpack
from worldforge.composed_bundle import (
    COMPOSED_BUNDLE_MANIFEST,
    ComposedBundleError,
    build_composed_runtime_bundle,
    validate_composed_runtime_bundle_manifest,
    verify_composed_runtime_bundle,
)
from worldforge.directory_publish import DirectoryPublishError, directory_identity
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import build_renderpack
from worldforge.runtime_composition import RUNTIME_CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"
M6_FIXTURES = ROOT / "examples/m6-contracts"


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ComposedRuntimeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)
        cls.neutral = cls.work / "neutral"
        shutil.copytree(ROOT / "examples/m5-neutral", cls.neutral)
        (cls.neutral / "renderpack/build").mkdir()
        (cls.neutral / "assetpack/build").mkdir()
        cls.renderpack = cls.neutral / "renderpack/build/renderpack.json"
        cls.assetpack = cls.neutral / "assetpack/build/assetpack.json"
        cls.renderpack_document = build_renderpack(
            cls.neutral / "renderpack/manifest.json",
            WORLDPACK,
            cls.renderpack,
        )
        cls.assetpack_document = build_assetpack(
            cls.neutral / "assetpack/manifest.json",
            WORLDPACK,
            cls.assetpack,
        )
        cls.worldpack = load_worldpack(WORLDPACK)
        cls.catalog = _read(M6_FIXTURES / "capability-catalog.json")
        cls.adapter = _read(M6_FIXTURES / "adapter.declared.json")
        cls.adapter["state"] = "verified"
        cls.adapter["capability_ids"] = sorted(RUNTIME_CAPABILITIES)
        cls.adapter["content_hash"] = canonical_payload_hash(cls.adapter)
        cls.adapter_key = RuntimeAdapterKey(
            str(cls.adapter["id"]),
            str(cls.adapter["version"]),
            str(cls.adapter["content_hash"]),
        )
        cls.adapter_value = object()
        cls.registry = StaticRuntimeAdapterRegistry([(cls.adapter_key, cls.adapter_value)])
        cls.notice = cls.work / "NOTICE.txt"
        cls.notice.write_bytes(b"Synthetic neutral test assets only.\n")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _documents(
        self,
        name: str,
        *,
        profile_id: str,
        renderpack: bool,
        assetpack: bool,
    ) -> Path:
        root = self.work / f"documents-{name}-{uuid.uuid4().hex}"
        root.mkdir()
        profile = _read(M6_FIXTURES / f"profiles/{profile_id}.json")
        required = sorted(
            set(self.worldpack.runtime_requirements.required_features)
            | set(profile["required_capability_ids"])
        )
        packs: dict[str, object] = {
            "worldpack": {
                "format": "isoworld.worldpack",
                "format_version": self.worldpack.format_version,
                "path": "packs/worldpack/worldpack.json",
                "content_hash": self.worldpack.content_hash,
            }
        }
        owners: list[dict[str, str]] = []
        if assetpack:
            packs["assetpack"] = {
                "format": "rpg-world-forge.assetpack",
                "format_version": 1,
                "path": "packs/assetpack/assetpack.json",
                "content_hash": self.assetpack_document["content_hash"],
            }
            owners.append(
                {
                    "slot": "actor:neutral",
                    "plane": "world_base",
                    "pack": "assetpack",
                    "asset_id": "neutral_actor_3d",
                    "representation": "3d",
                }
            )
        if renderpack:
            packs["renderpack"] = {
                "format": "isoworld.renderpack",
                "format_version": 1,
                "path": "packs/renderpack/renderpack.json",
                "content_hash": self.renderpack_document["content_hash"],
            }
            owners.append(
                {
                    "slot": "ui:font" if assetpack else "actor:neutral",
                    "plane": "world_overlay" if assetpack else "world_base",
                    "pack": "renderpack",
                    "asset_id": "neutral_font" if assetpack else "neutral_sheet",
                    "representation": "2d",
                }
            )
        owners.sort(
            key=lambda item: (
                item["slot"],
                item["plane"],
                item["pack"],
                item["asset_id"],
                item["representation"],
            )
        )
        composition: dict[str, object] = {
            "format": "rpg-world-forge.runtime_composition",
            "format_version": 1,
            "world_id": self.worldpack.world_id,
            "world_content_hash": self.worldpack.content_hash,
            "release_id": "1.0.0",
            "profile": {
                "id": profile["id"],
                "content_hash": profile["content_hash"],
            },
            "capability_catalog_hash": self.catalog["content_hash"],
            "adapter": {
                "id": self.adapter["id"],
                "version": self.adapter["version"],
                "content_hash": self.adapter["content_hash"],
            },
            "packs": packs,
            "required_capability_ids": required,
            "slot_owners": owners,
        }
        composition["content_hash"] = canonical_payload_hash(composition)
        for filename, document in (
            ("catalog.json", self.catalog),
            ("profile.json", profile),
            ("adapter.json", self.adapter),
            ("composition.json", composition),
        ):
            (root / filename).write_bytes(canonical_json_bytes(document))
        return root

    def _build(
        self,
        name: str,
        *,
        profile_id: str = "profile_2d",
        renderpack: bool = True,
        assetpack: bool = False,
        platform: str = "linux_x86_64",
        destination: Path | None = None,
        license_sources: dict[str, Path] | None = None,
    ):
        documents = self._documents(
            name,
            profile_id=profile_id,
            renderpack=renderpack,
            assetpack=assetpack,
        )
        destination = destination or self.work / f"bundle-{name}-{uuid.uuid4().hex}"
        return build_composed_runtime_bundle(
            documents / "catalog.json",
            documents / "profile.json",
            documents / "adapter.json",
            documents / "composition.json",
            WORLDPACK,
            destination,
            bundle_id="neutral_bundle",
            bundle_version="1.0.0",
            platform=platform,
            registry=self.registry,
            license_sources=license_sources or {"NOTICE.txt": self.notice},
            renderpack_path=self.renderpack if renderpack else None,
            assetpack_path=self.assetpack if assetpack else None,
        )

    def test_builds_deterministic_exact_render_only_bundles_across_roots(self) -> None:
        first_root = self.work / f"deterministic-a-{uuid.uuid4().hex}"
        second_root = self.work / f"deterministic-b-{uuid.uuid4().hex}"
        first = self._build("deterministic-a", destination=first_root)
        second = self._build("deterministic-b", destination=second_root)
        try:
            self.assertEqual(first.bundle_hash, second.bundle_hash)
            self.assertEqual(_tree_bytes(first_root), _tree_bytes(second_root))
            self.assertIs(self.adapter_value, first.registered.adapter_value)
            self.assertTrue(first.verification.compatible)
            self.assertIsNotNone(first.renderpack)
            self.assertIsNone(first.assetpack)
            manifest = first.manifest
            self.assertIsNone(manifest["packs"]["assetpack"])
            self.assertNotIn(
                str(self.work), first_root.joinpath(COMPOSED_BUNDLE_MANIFEST).read_text()
            )
        finally:
            first.close()
            second.close()

    def test_builds_asset_only_and_combined_profiles(self) -> None:
        asset_only = self._build(
            "asset-only",
            profile_id="profile_3d",
            renderpack=False,
            assetpack=True,
        )
        combined = self._build(
            "combined",
            profile_id="profile_2d_over_3d",
            renderpack=True,
            assetpack=True,
        )
        try:
            self.assertIsNone(asset_only.renderpack)
            self.assertIsNotNone(asset_only.assetpack)
            self.assertIsNotNone(combined.renderpack)
            self.assertIsNotNone(combined.assetpack)
            self.assertEqual(
                "profile_2d_over_3d", combined.registered.documents.presentation_profile["id"]
            )
        finally:
            asset_only.close()
            combined.close()

    def test_loaded_bundle_survives_published_tree_mutation(self) -> None:
        destination = self.work / f"mutation-{uuid.uuid4().hex}"
        loaded = self._build("mutation", destination=destination)
        assert loaded.renderpack is not None
        item = loaded.renderpack.assets[0].files[0]
        before = loaded.renderpack.resolve_file(item).read_bytes()
        public = destination / "licenses/NOTICE.txt"
        public.write_bytes(b"tampered after load\n")

        self.assertEqual("neutral_bundle", loaded.bundle_id)
        self.assertEqual(before, loaded.renderpack.resolve_file(item).read_bytes())
        loaded.close()

    def test_tampered_persisted_evidence_is_recomputed_and_rejected(self) -> None:
        destination = self.work / f"evidence-{uuid.uuid4().hex}"
        built = self._build("evidence", destination=destination)
        built.close()
        report_path = destination / "evidence/runtime-compatibility-report.json"
        report = _read(report_path)
        report["platform"] = "windows_x86_64"
        report["content_hash"] = canonical_payload_hash(report)
        report_path.write_bytes(canonical_json_bytes(report))
        manifest_path = destination / COMPOSED_BUNDLE_MANIFEST
        manifest = _read(manifest_path)
        for record in manifest["files"]:
            if record["path"] == "evidence/runtime-compatibility-report.json":
                record["sha256"] = _sha256(report_path)
                record["size"] = report_path.stat().st_size
        manifest["compatibility_evidence"]["content_hash"] = report["content_hash"]
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        with self.assertRaisesRegex(ComposedBundleError, "freshly recomputed"):
            verify_composed_runtime_bundle(
                destination,
                expected_bundle_hash=manifest["bundle_hash"],
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=self.registry,
            )

    def test_wrong_registry_key_and_tree_attacks_fail_closed(self) -> None:
        original = self.work / f"attacks-{uuid.uuid4().hex}"
        built = self._build("attacks", destination=original)
        bundle_hash = built.bundle_hash
        built.close()
        wrong_registry: StaticRuntimeAdapterRegistry[object] = StaticRuntimeAdapterRegistry()
        with self.assertRaisesRegex(ComposedBundleError, "exact code-owned"):
            verify_composed_runtime_bundle(
                original,
                expected_bundle_hash=bundle_hash,
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=wrong_registry,
            )

        for attack in ("extra", "symlink", "hardlink"):
            with self.subTest(attack=attack):
                target = self.work / f"attack-{attack}-{uuid.uuid4().hex}"
                shutil.copytree(original, target)
                notice = target / "licenses/NOTICE.txt"
                outside = self.work / f"outside-{attack}-{uuid.uuid4().hex}.txt"
                if attack == "extra":
                    (target / "licenses/EXTRA.txt").write_text("extra\n")
                elif attack == "symlink":
                    outside.write_text("replacement\n")
                    notice.unlink()
                    notice.symlink_to(outside)
                else:
                    outside.write_text("outside\n")
                    notice.unlink()
                    os.link(outside, notice)
                with self.assertRaises(ComposedBundleError):
                    verify_composed_runtime_bundle(
                        target,
                        expected_bundle_hash=bundle_hash,
                        platform="linux_x86_64",
                        runtime_api_version="0.5.0",
                        registry=self.registry,
                    )

    def test_runtime_boundary_rejects_provider_metadata_in_notice_json(self) -> None:
        notice = self.work / f"provider-{uuid.uuid4().hex}.json"
        notice.write_bytes(
            canonical_json_bytes(
                {
                    "format": "example.notice",
                    "provider": "must-not-enter-runtime",
                }
            )
        )
        with self.assertRaisesRegex(ComposedBundleError, "provider"):
            self._build(
                "provider",
                license_sources={"NOTICE.json": notice},
            )

    def test_existing_destination_is_preserved_and_unsupported_platform_is_early(
        self,
    ) -> None:
        destination = self.work / f"existing-{uuid.uuid4().hex}"
        destination.mkdir()
        marker = destination / "marker.txt"
        marker.write_text("preserve\n")
        with self.assertRaisesRegex(ComposedBundleError, "already exists"):
            self._build("existing", destination=destination)
        self.assertEqual("preserve\n", marker.read_text())

        documents = self._documents(
            "unsupported",
            profile_id="profile_2d",
            renderpack=True,
            assetpack=False,
        )
        unsupported = self.work / f"unsupported-{uuid.uuid4().hex}"
        with (
            patch.object(composed_module.sys, "platform", "darwin"),
            self.assertRaisesRegex(ComposedBundleError, "Linux and Windows"),
        ):
            build_composed_runtime_bundle(
                documents / "catalog.json",
                documents / "profile.json",
                documents / "adapter.json",
                documents / "composition.json",
                WORLDPACK,
                unsupported,
                bundle_id="neutral_bundle",
                bundle_version="1.0.0",
                platform="linux_x86_64",
                registry=self.registry,
                license_sources={"NOTICE.txt": self.notice},
                renderpack_path=self.renderpack,
            )
        self.assertFalse(unsupported.exists())

    def test_copying_journal_recovers_owned_stage_before_new_build(self) -> None:
        destination = self.work / f"recovery-{uuid.uuid4().hex}"
        operation_id = uuid.uuid4().hex
        stage = destination.parent / f".{destination.name}.composed-{operation_id}"
        (stage / "contracts").mkdir(parents=True)
        (stage / "contracts/runtime-composition.json").write_text("{}\n")
        identity = directory_identity(stage, context="test recovery stage")
        journal = composed_module._journal_document(
            operation_id=operation_id,
            state="copying",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash=None,
        )
        journal_path = composed_module._journal_path(destination)
        journal_path.write_bytes(canonical_json_bytes(journal))

        built = self._build("recovery", destination=destination)
        try:
            self.assertFalse(stage.exists())
            self.assertFalse(journal_path.exists())
        finally:
            built.close()

    def test_injected_publication_failure_rolls_back_only_owned_stage(self) -> None:
        destination = self.work / f"failure-{uuid.uuid4().hex}"
        with (
            patch.object(
                composed_module,
                "publish_directory_noreplace",
                side_effect=DirectoryPublishError("injected"),
            ),
            self.assertRaisesRegex(ComposedBundleError, "injected"),
        ):
            self._build("failure", destination=destination)
        self.assertFalse(destination.exists())
        self.assertFalse(composed_module._journal_path(destination).exists())
        self.assertEqual(
            [],
            [
                path
                for path in destination.parent.glob(f".{destination.name}.composed-*")
                if len(path.name.rsplit("-", 1)[-1]) == 32
            ],
        )

    def test_move_then_raise_preserves_ready_journal_until_matching_recovery(
        self,
    ) -> None:
        def move_then_raise(source: Path, destination: Path) -> None:
            source.rename(destination)
            raise DirectoryPublishError("injected post-move verification failure")

        for platform in ("linux_x86_64", "windows_x86_64"):
            with self.subTest(platform=platform):
                destination = self.work / f"moved-{platform}-{uuid.uuid4().hex}"
                with (
                    patch.object(
                        composed_module,
                        "publish_directory_noreplace",
                        side_effect=move_then_raise,
                    ),
                    self.assertRaisesRegex(
                        ComposedBundleError,
                        "post-move verification failure",
                    ),
                ):
                    self._build(
                        f"moved-{platform}",
                        destination=destination,
                        platform=platform,
                    )

                journal_path = composed_module._journal_path(destination)
                journal = _read(journal_path)
                expected_identity = (
                    journal["stage_identity"]["device"],
                    journal["stage_identity"]["inode"],
                )
                self.assertEqual("ready", journal["state"])
                self.assertEqual(
                    expected_identity,
                    directory_identity(destination, context="moved destination"),
                )
                self.assertFalse(destination.parent.joinpath(journal["stage_name"]).exists())

                with self.assertRaisesRegex(ComposedBundleError, "already exists"):
                    self._build(
                        f"recover-{platform}",
                        destination=destination,
                        platform=platform,
                    )
                self.assertFalse(journal_path.exists())
                with verify_composed_runtime_bundle(
                    destination,
                    expected_bundle_hash=journal["bundle_hash"],
                    platform=platform,
                    runtime_api_version="0.5.0",
                    registry=self.registry,
                ):
                    pass

    def test_move_then_raise_recovery_preserves_mismatched_destinations(self) -> None:
        def move_then_raise(source: Path, destination: Path) -> None:
            source.rename(destination)
            raise DirectoryPublishError("injected post-move verification failure")

        for mismatch in ("content", "identity"):
            with self.subTest(mismatch=mismatch):
                destination = self.work / f"mismatch-{mismatch}-{uuid.uuid4().hex}"
                with (
                    patch.object(
                        composed_module,
                        "publish_directory_noreplace",
                        side_effect=move_then_raise,
                    ),
                    self.assertRaises(ComposedBundleError),
                ):
                    self._build(f"mismatch-{mismatch}", destination=destination)
                journal_path = composed_module._journal_path(destination)
                journal_before = journal_path.read_bytes()

                if mismatch == "content":
                    marker = destination / "licenses/NOTICE.txt"
                    marker.write_text("replacement content\n", encoding="utf-8")
                else:
                    shutil.rmtree(destination)
                    destination.mkdir()
                    marker = destination / "replacement.txt"
                    marker.write_text("replacement directory\n", encoding="utf-8")

                with self.assertRaises(ComposedBundleError):
                    self._build(f"retry-{mismatch}", destination=destination)
                self.assertEqual(journal_before, journal_path.read_bytes())
                self.assertTrue(destination.exists())
                self.assertTrue(marker.exists())

    def test_publication_failure_never_infers_rollback_from_missing_stage(self) -> None:
        destination = self.work / f"missing-stage-{uuid.uuid4().hex}"

        def remove_then_raise(source: Path, _destination: Path) -> None:
            shutil.rmtree(source)
            raise DirectoryPublishError("injected ambiguous publication failure")

        with (
            patch.object(
                composed_module,
                "publish_directory_noreplace",
                side_effect=remove_then_raise,
            ),
            self.assertRaisesRegex(
                ComposedBundleError,
                "ambiguous publication failure",
            ),
        ):
            self._build("missing-stage", destination=destination)

        journal_path = composed_module._journal_path(destination)
        journal_before = journal_path.read_bytes()
        self.assertEqual("ready", _read(journal_path)["state"])
        self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ComposedBundleError, "ambiguous"):
            self._build("missing-stage-retry", destination=destination)
        self.assertEqual(journal_before, journal_path.read_bytes())

    def test_manifest_rejects_casefold_prefix_collisions_and_false_selection(
        self,
    ) -> None:
        destination = self.work / f"manifest-{uuid.uuid4().hex}"
        built = self._build("manifest", destination=destination)
        built.close()
        manifest = copy.deepcopy(_read(destination / COMPOSED_BUNDLE_MANIFEST))
        record = copy.deepcopy(manifest["licenses"][0])
        record["path"] = "licenses/notice.TXT"
        manifest["files"].append(record)
        manifest["files"].sort(key=lambda item: item["path"])
        manifest["licenses"].append(record)
        manifest["licenses"].sort(key=lambda item: item["path"])
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        with self.assertRaisesRegex(ComposedBundleError, "casefold"):
            validate_composed_runtime_bundle_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
