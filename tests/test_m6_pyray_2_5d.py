from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import AssetFile, RenderAsset, RenderPack, load_renderpack
from isoworld.core.app import GameApp
from isoworld.render.pyray_2_5d import (
    PYRAY_2_5D_ADAPTER,
    PYRAY_2_5D_ADAPTER_ID,
    PYRAY_2_5D_ADAPTER_VERSION,
    PYRAY_2_5D_BUDGETS,
    PYRAY_2_5D_CAPABILITY_IDS,
    PYRAY_2_5D_CONTENT_HASH,
    PYRAY_2_5D_KEY,
    PYRAY_2_5D_REGISTRY,
    Pyray25DAdapter,
    Pyray25DError,
)
from isoworld.runtime_adapter import RuntimeAdapterKey, RuntimeAdapterRegistryError
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import build_renderpack
from worldforge.runtime_composition import (
    load_registered_runtime_composition,
    validate_runtime_adapter,
    verify_runtime_composition,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/m6-contracts"
ADAPTER_FIXTURE = FIXTURES / "adapters/isoworld_raylib_2_5d.json"
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"
M5_MANIFEST = ROOT / "examples/m5-neutral/renderpack/manifest.json"

EXPECTED_CAPABILITIES = (
    "action_replay",
    "actor_needs",
    "conditional_dialogue",
    "construction",
    "content_renderpack_v1",
    "content_worldpack_v1_v5",
    "contextual_interactions",
    "costed_abilities",
    "delayed_consequences",
    "directed_relationships",
    "grid_movement",
    "hierarchical_goals",
    "path_navigation",
    "playable_actor_switching",
    "presentation_world_2_5d",
    "reactive_quests",
    "resource_economy",
    "schedules",
    "timed_scenes",
    "typed_knowledge",
    "versioned_persistence",
    "world_clock",
)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _composition(
    *,
    profile: dict[str, object],
    adapter: dict[str, object],
    catalog: dict[str, object],
    worldpack_hash: str,
    renderpack_hash: str,
) -> dict[str, object]:
    profile_id = str(profile["id"])
    packs: dict[str, object] = {
        "renderpack": {
            "content_hash": renderpack_hash,
            "format": "isoworld.renderpack",
            "format_version": 1,
            "path": "packs/renderpack.json",
        },
        "worldpack": {
            "content_hash": worldpack_hash,
            "format": "isoworld.worldpack",
            "format_version": 5,
            "path": "packs/worldpack.json",
        },
    }
    if "3d" in str(profile["mode"]):
        packs["assetpack"] = {
            "content_hash": "1" * 64,
            "format": "rpg-world-forge.assetpack",
            "format_version": 1,
            "path": "packs/unavailable.assetpack.json",
        }

    if profile_id == "profile_2_5d":
        owners = [
            {
                "asset_id": "neutral_sheet",
                "pack": "renderpack",
                "plane": "world_base",
                "representation": "2_5d",
                "slot": "actor:neutral",
            }
        ]
    elif profile_id == "profile_2d":
        owners = [
            {
                "asset_id": "neutral_sheet",
                "pack": "renderpack",
                "plane": "world_base",
                "representation": "2d",
                "slot": "actor:neutral",
            }
        ]
    elif profile_id == "profile_2d_over_2_5d":
        owners = [
            {
                "asset_id": "neutral_sheet",
                "pack": "renderpack",
                "plane": "world_base",
                "representation": "2_5d",
                "slot": "actor:neutral",
            },
            {
                "asset_id": "neutral_font",
                "pack": "renderpack",
                "plane": "world_overlay",
                "representation": "2d",
                "slot": "ui:font",
            },
        ]
    else:
        owners = [
            {
                "asset_id": "neutral_mesh",
                "pack": "assetpack",
                "plane": "world_base",
                "representation": "3d",
                "slot": "actor:neutral",
            }
        ]
        if "_over_3d" in profile_id:
            owners.append(
                {
                    "asset_id": "neutral_font",
                    "pack": "renderpack",
                    "plane": "world_overlay",
                    "representation": "2_5d" if profile_id.startswith("profile_2_5d") else "2d",
                    "slot": "ui:font",
                }
            )
    required = sorted(
        {
            *load_worldpack(WORLDPACK).runtime_requirements.required_features,
            *profile["required_capability_ids"],  # type: ignore[misc]
        }
    )
    document: dict[str, object] = {
        "adapter": {
            "content_hash": adapter["content_hash"],
            "id": adapter["id"],
            "version": adapter["version"],
        },
        "capability_catalog_hash": catalog["content_hash"],
        "format": "rpg-world-forge.runtime_composition",
        "format_version": 1,
        "packs": dict(sorted(packs.items())),
        "profile": {
            "content_hash": profile["content_hash"],
            "id": profile["id"],
        },
        "release_id": "1.0.0",
        "required_capability_ids": required,
        "slot_owners": sorted(
            owners,
            key=lambda item: (
                item["slot"],
                item["plane"],
                item["pack"],
                item["asset_id"],
                item["representation"],
            ),
        ),
        "world_content_hash": worldpack_hash,
        "world_id": "foundation_slice",
    }
    document["content_hash"] = canonical_payload_hash(document)
    return document


class Pyray25DContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="rwf-pyray-2-5d-")
        cls.root = Path(cls.temporary.name)
        packs = cls.root / "packs"
        packs.mkdir()
        cls.worldpack_path = packs / "worldpack.json"
        cls.worldpack_path.write_bytes(WORLDPACK.read_bytes())
        cls.renderpack_path = packs / "renderpack.json"
        cls.renderpack_document = build_renderpack(
            M5_MANIFEST,
            cls.worldpack_path,
            cls.renderpack_path,
        )
        cls.worldpack = load_worldpack(cls.worldpack_path)
        cls.renderpack = load_renderpack(cls.renderpack_path, cls.worldpack)
        cls.catalog = _read_json(FIXTURES / "capability-catalog.json")
        cls.adapter = _read_json(ADAPTER_FIXTURE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.renderpack.close()
        cls.temporary.cleanup()

    def _supported_host(self):
        return patch(
            "isoworld.render.pyray_2_5d._host_target",
            return_value=("posix", "Linux", "x86_64"),
        )

    def test_verified_fixture_matches_exact_code_owned_registry_entry(self) -> None:
        fixture = copy.deepcopy(self.adapter)

        self.assertEqual(fixture, validate_runtime_adapter(fixture))
        self.assertEqual(PYRAY_2_5D_CONTENT_HASH, canonical_payload_hash(fixture))
        self.assertEqual(
            "2628adad118585ffc15c6509a49c92954660d7ea42788eed1ba69fef99e54fa8",
            fixture["content_hash"],
        )
        self.assertEqual(PYRAY_2_5D_ADAPTER_ID, fixture["id"])
        self.assertEqual(PYRAY_2_5D_ADAPTER_VERSION, fixture["version"])
        self.assertEqual("verified", fixture["state"])
        self.assertEqual(["linux_x86_64"], fixture["platforms"])
        self.assertEqual(["2_5d"], fixture["presentation_modes"])
        self.assertIs(PYRAY_2_5D_ADAPTER, PYRAY_2_5D_REGISTRY.resolve(PYRAY_2_5D_KEY))
        self.assertIsInstance(PYRAY_2_5D_ADAPTER, Pyray25DAdapter)
        for near in (
            RuntimeAdapterKey("isoworld_raylib_2d", "0.1.0", PYRAY_2_5D_CONTENT_HASH),
            RuntimeAdapterKey(PYRAY_2_5D_ADAPTER_ID, "0.1.1", PYRAY_2_5D_CONTENT_HASH),
            RuntimeAdapterKey(PYRAY_2_5D_ADAPTER_ID, "0.1.0", "f" * 64),
        ):
            with self.subTest(near=near), self.assertRaises(RuntimeAdapterRegistryError):
                PYRAY_2_5D_REGISTRY.resolve(near)

    def test_capabilities_are_the_exact_static_foundation_and_profile_union(self) -> None:
        profile = _read_json(FIXTURES / "profiles/profile_2_5d.json")
        observed_union = tuple(
            sorted(
                {
                    *self.worldpack.runtime_requirements.required_features,
                    *profile["required_capability_ids"],  # type: ignore[misc]
                }
            )
        )

        self.assertEqual(19, len(self.worldpack.runtime_requirements.required_features))
        self.assertNotIn("locales", self.worldpack.runtime_requirements.required_features)
        self.assertNotIn(
            "personal_campaigns",
            self.worldpack.runtime_requirements.required_features,
        )
        self.assertEqual(22, len(EXPECTED_CAPABILITIES))
        self.assertEqual(EXPECTED_CAPABILITIES, observed_union)
        self.assertEqual(EXPECTED_CAPABILITIES, PYRAY_2_5D_CAPABILITY_IDS)
        self.assertEqual(list(EXPECTED_CAPABILITIES), self.adapter["capability_ids"])

    def test_profile_2_5d_composition_is_compatible_and_resolves_exact_value(self) -> None:
        profile = _read_json(FIXTURES / "profiles/profile_2_5d.json")
        composition = _composition(
            profile=profile,
            adapter=self.adapter,
            catalog=self.catalog,
            worldpack_hash=self.worldpack.content_hash,
            renderpack_hash=str(self.renderpack_document["content_hash"]),
        )
        contracts = self.root / "contracts"
        contracts.mkdir(exist_ok=True)
        paths = {
            "capability_catalog_path": "contracts/capability-catalog.json",
            "presentation_profile_path": "contracts/profile.json",
            "runtime_adapter_path": "contracts/adapter.json",
            "composition_path": "contracts/composition.json",
        }
        for relative, value in (
            (paths["capability_catalog_path"], self.catalog),
            (paths["presentation_profile_path"], profile),
            (paths["runtime_adapter_path"], self.adapter),
            (paths["composition_path"], composition),
        ):
            (self.root / relative).write_bytes(canonical_json_bytes(value))

        loaded = load_registered_runtime_composition(
            self.root,
            **paths,
            platform="linux_x86_64",
            registry=PYRAY_2_5D_REGISTRY,
        )

        self.assertTrue(loaded.verification.compatible)
        self.assertEqual((), loaded.verification.issues)
        self.assertEqual(PYRAY_2_5D_KEY, loaded.adapter_key)
        self.assertIs(PYRAY_2_5D_ADAPTER, loaded.adapter_value)

    def test_other_profiles_and_windows_remain_incompatible(self) -> None:
        profile_2_5d = _read_json(FIXTURES / "profiles/profile_2_5d.json")
        exact = _composition(
            profile=profile_2_5d,
            adapter=self.adapter,
            catalog=self.catalog,
            worldpack_hash=self.worldpack.content_hash,
            renderpack_hash=str(self.renderpack_document["content_hash"]),
        )
        windows = verify_runtime_composition(
            self.catalog,
            profile_2_5d,
            self.adapter,
            exact,
            root=self.root,
            platform="windows_x86_64",
        )
        self.assertFalse(windows.compatible)
        self.assertIn("platform_unsupported", {issue.code for issue in windows.issues})

        unsupported_profiles = (
            "profile_2d",
            "profile_2d_over_2_5d",
            "profile_2_5d_over_3d",
            "profile_2d_over_3d",
            "profile_3d",
        )
        for profile_id in unsupported_profiles:
            with self.subTest(profile=profile_id):
                profile = _read_json(FIXTURES / f"profiles/{profile_id}.json")
                composition = _composition(
                    profile=profile,
                    adapter=self.adapter,
                    catalog=self.catalog,
                    worldpack_hash=self.worldpack.content_hash,
                    renderpack_hash=str(self.renderpack_document["content_hash"]),
                )
                report = verify_runtime_composition(
                    self.catalog,
                    profile,
                    self.adapter,
                    composition,
                    root=self.root,
                    platform="linux_x86_64",
                )
                self.assertFalse(report.compatible)
                self.assertTrue(
                    {"profile_mismatch", "capability_missing"} & {i.code for i in report.issues}
                )

    def test_preflight_is_bounded_and_create_app_does_not_import_pyray(self) -> None:
        with self._supported_host():
            report = PYRAY_2_5D_ADAPTER.preflight(self.worldpack, self.renderpack)
            with patch.dict(sys.modules, {"pyray": None}):
                app = PYRAY_2_5D_ADAPTER.create_app(
                    self.worldpack,
                    self.renderpack,
                    quick_save_path=self.root / "quick-save.json",
                )

        self.assertEqual(PYRAY_2_5D_KEY, report.adapter_key)
        self.assertEqual("linux_x86_64", report.platform)
        self.assertEqual(5, report.asset_count)
        self.assertEqual(3, report.binding_count)
        self.assertEqual(3082, report.loaded_bytes)
        self.assertEqual(1024, report.smoke_draw_call_ceiling)
        self.assertEqual(1000, report.smoke_target_frame_milliseconds)
        self.assertIsInstance(app, GameApp)
        self.assertIs(self.renderpack, app.renderpack)
        self.assertEqual(self.root / "quick-save.json", app.quick_save_path)
        self.assertEqual(
            {
                "max_assets": 5,
                "max_bindings": 3,
                "max_draw_calls": 1024,
                "max_loaded_bytes": 1_048_576,
                "max_triangles": 1,
                "target_frame_milliseconds": 1000,
            },
            dict(PYRAY_2_5D_BUDGETS),
        )

    def test_preflight_rejects_unsupported_host_and_resource_overruns(self) -> None:
        with (
            patch(
                "isoworld.render.pyray_2_5d._host_target",
                return_value=("nt", "Windows", "amd64"),
            ),
            self.assertRaisesRegex(Pyray25DError, "Linux x86_64"),
        ):
            PYRAY_2_5D_ADAPTER.preflight(self.worldpack, self.renderpack)

        too_many_assets = replace(
            self.renderpack,
            assets=(*self.renderpack.assets, self.renderpack.assets[0]),
        )
        with (
            self._supported_host(),
            self.assertRaisesRegex(Pyray25DError, "asset count"),
        ):
            PYRAY_2_5D_ADAPTER.preflight(self.worldpack, too_many_assets)

        too_many_bindings = replace(
            self.renderpack,
            bindings=(*self.renderpack.bindings, self.renderpack.bindings[0]),
        )
        with (
            self._supported_host(),
            self.assertRaisesRegex(Pyray25DError, "binding count"),
        ):
            PYRAY_2_5D_ADAPTER.preflight(self.worldpack, too_many_bindings)

        oversized_path = self.root / "oversized.bin"
        oversized_path.write_bytes(b"x" * (1_048_576 + 1))
        digest = hashlib.sha256(oversized_path.read_bytes()).hexdigest()
        oversized = RenderPack(
            world_id=self.worldpack.world_id,
            world_content_hash=self.worldpack.content_hash,
            content_hash="0" * 64,
            root=self.root,
            assets=(
                RenderAsset(
                    "oversized_asset",
                    "sprite",
                    (AssetFile("texture", "oversized.bin", digest, "image/png"),),
                ),
            ),
            bindings=(self.renderpack.bindings[0],),
        )
        oversized = replace(
            oversized,
            bindings=(replace(oversized.bindings[0], asset_id="oversized_asset"),),
        )
        with (
            self._supported_host(),
            self.assertRaisesRegex(Pyray25DError, "loaded-byte budget"),
        ):
            PYRAY_2_5D_ADAPTER.preflight(self.worldpack, oversized)

    def test_preflight_accepts_an_integral_alternate_world_with_subset_requirements(
        self,
    ) -> None:
        alternate_world_path = self.root / "packs/alternate-worldpack.json"
        alternate_world = _read_json(WORLDPACK)
        alternate_world["world"]["id"] = "alternate_world"  # type: ignore[index]
        alternate_world["world"]["capabilities"] = ["grid_movement"]  # type: ignore[index]
        alternate_world["runtime_requirements"]["required_features"] = [  # type: ignore[index]
            "grid_movement"
        ]
        alternate_world["content_hash"] = canonical_payload_hash(alternate_world)
        alternate_world_path.write_bytes(canonical_json_bytes(alternate_world))
        loaded_world = load_worldpack(alternate_world_path)

        alternate_renderpack_path = self.root / "packs/alternate-renderpack.json"
        alternate_renderpack = _read_json(self.renderpack_path)
        alternate_renderpack["world_id"] = loaded_world.world_id
        alternate_renderpack["world_content_hash"] = loaded_world.content_hash
        alternate_renderpack["content_hash"] = canonical_payload_hash(alternate_renderpack)
        alternate_renderpack_path.write_bytes(canonical_json_bytes(alternate_renderpack))
        loaded_renderpack = load_renderpack(alternate_renderpack_path, loaded_world)
        try:
            with self._supported_host():
                report = PYRAY_2_5D_ADAPTER.preflight(loaded_world, loaded_renderpack)
        finally:
            loaded_renderpack.close()

        self.assertEqual("alternate_world", loaded_world.world_id)
        self.assertEqual(("grid_movement",), loaded_world.runtime_requirements.required_features)
        self.assertEqual(5, report.asset_count)
        self.assertNotEqual(self.worldpack.content_hash, loaded_world.content_hash)

    def test_module_is_stdlib_only_at_native_boundary_and_cataloged(self) -> None:
        source = (ROOT / "src/isoworld/render/pyray_2_5d.py").read_text(encoding="utf-8")
        for forbidden in ("import pyray", "worldforge", "importlib", "__import__", "entry_points"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

        catalog = _read_json(ROOT / "contracts/catalog.json")
        entry = next(
            item
            for item in catalog["contracts"]  # type: ignore[union-attr]
            if item["id"] == "runtime-adapter"
        )
        self.assertIn(
            "examples/m6-contracts/adapters/isoworld_raylib_2_5d.json",
            entry["fixtures"],
        )
        self.assertIn(
            "isoworld.render.pyray_2_5d:PYRAY_2_5D_REGISTRY",
            entry["python_symbols"],
        )
        self.assertIn("tests/test_m6_pyray_2_5d.py", entry["tests"])

        declared = _read_json(FIXTURES / "adapter.declared.json")
        self.assertEqual("declared", declared["state"])
        self.assertEqual(
            "3638d006f787a9c17d019cb7aad6a13dae4f619676a1ad02bd02eda56208c5c7",
            declared["content_hash"],
        )


if __name__ == "__main__":
    unittest.main()
