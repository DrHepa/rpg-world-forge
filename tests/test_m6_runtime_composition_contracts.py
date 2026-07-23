from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.runtime_composition import (
    COMPATIBILITY_CHECK_IDS,
    COMPATIBILITY_ISSUE_CODES,
    PRESENTATION_PROFILES,
    RUNTIME_CAPABILITIES,
    RuntimeCompositionError,
    validate_runtime_adapter,
    validate_runtime_capability_catalog,
    validate_runtime_compatibility_report,
    validate_runtime_composition,
    validate_runtime_presentation_profile,
    verify_runtime_composition,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/m6-contracts"


def _read_fixture(name: str) -> dict[str, object]:
    path = FIXTURES / name
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _reseal(value: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(value)
    result["content_hash"] = canonical_payload_hash(result)
    return result


class RuntimeCompositionContractTests(unittest.TestCase):
    def test_static_capability_catalog_is_exact_and_sorted(self) -> None:
        expected_ids = {
            "action_replay",
            "actor_needs",
            "animation_gltf",
            "catalog_multi_world_v1",
            "collision_gltf",
            "conditional_dialogue",
            "construction",
            "content_assetpack_v1",
            "content_renderpack_v1",
            "content_worldpack_v1_v5",
            "contextual_interactions",
            "costed_abilities",
            "delayed_consequences",
            "directed_relationships",
            "grid_movement",
            "hierarchical_goals",
            "locales",
            "packaging_standalone",
            "path_navigation",
            "personal_campaigns",
            "playable_actor_switching",
            "presentation_audio",
            "presentation_ui_2d",
            "presentation_world_2_5d",
            "presentation_world_2d",
            "presentation_world_3d",
            "presentation_world_mixed",
            "reactive_quests",
            "resource_economy",
            "schedules",
            "simulation_fixed_step",
            "timed_scenes",
            "typed_knowledge",
            "versioned_persistence",
            "world_clock",
        }
        self.assertEqual(expected_ids, set(RUNTIME_CAPABILITIES))
        self.assertEqual(sorted(expected_ids), list(RUNTIME_CAPABILITIES))
        self.assertEqual(
            {"domain", "determinism"},
            set(next(iter(RUNTIME_CAPABILITIES.values()))),
        )

        catalog = validate_runtime_capability_catalog(_read_fixture("capability-catalog.json"))
        self.assertEqual(sorted(expected_ids), [entry["id"] for entry in catalog["capabilities"]])
        self.assertEqual(
            RUNTIME_CAPABILITIES,
            {
                entry["id"]: {
                    "domain": entry["domain"],
                    "determinism": entry["determinism"],
                }
                for entry in catalog["capabilities"]
            },
        )

    def test_six_profiles_are_exact_world_presentation_contracts(self) -> None:
        expected = {
            "profile_2d": ("2d", ("2d",), ("renderpack",)),
            "profile_2_5d": ("2_5d", ("2_5d",), ("renderpack",)),
            "profile_3d": ("3d", ("3d",), ("assetpack",)),
            "profile_2d_over_2_5d": (
                "2d_over_2_5d",
                ("2_5d", "2d"),
                ("renderpack",),
            ),
            "profile_2d_over_3d": (
                "2d_over_3d",
                ("3d", "2d"),
                ("assetpack", "renderpack"),
            ),
            "profile_2_5d_over_3d": (
                "2_5d_over_3d",
                ("3d", "2_5d"),
                ("assetpack", "renderpack"),
            ),
        }
        self.assertEqual(set(expected), set(PRESENTATION_PROFILES))
        for profile_id, (mode, layers, packs) in expected.items():
            with self.subTest(profile=profile_id):
                profile = validate_runtime_presentation_profile(
                    _read_fixture(f"profiles/{profile_id}.json")
                )
                self.assertEqual(profile_id, profile["id"])
                self.assertEqual(mode, profile["mode"])
                self.assertEqual(layers, tuple(profile["layers"]))
                self.assertEqual(packs, tuple(profile["required_packs"]))
                self.assertNotIn("presentation_audio", profile["required_capability_ids"])
                self.assertNotIn("presentation_ui_2d", profile["required_capability_ids"])
                if "3d" in layers:
                    self.assertIn("animation_gltf", profile["required_capability_ids"])
                    self.assertIn("collision_gltf", profile["required_capability_ids"])
                if len(layers) > 1:
                    self.assertIn(
                        "presentation_world_mixed",
                        profile["required_capability_ids"],
                    )

    def test_contracts_are_closed_hash_bound_and_canonically_ordered(self) -> None:
        fixtures_and_validators = (
            ("capability-catalog.json", validate_runtime_capability_catalog),
            ("profiles/profile_2d.json", validate_runtime_presentation_profile),
            ("adapter.declared.json", validate_runtime_adapter),
            ("composition.json", validate_runtime_composition),
            ("compatibility-report.json", validate_runtime_compatibility_report),
        )
        for relative, validator in fixtures_and_validators:
            with self.subTest(relative=relative):
                value = _read_fixture(relative)
                validator(value)
                self.assertEqual(
                    canonical_json_bytes(value),
                    (FIXTURES / relative).read_bytes(),
                )
                unknown = _reseal({**value, "unknown": True})
                with self.assertRaisesRegex(RuntimeCompositionError, "unknown fields"):
                    validator(unknown)
                tampered = copy.deepcopy(value)
                tampered["content_hash"] = "0" * 64
                with self.assertRaisesRegex(RuntimeCompositionError, "content hash"):
                    validator(tampered)
                for version in (True, False):
                    invalid_version = _reseal({**value, "format_version": version})
                    with self.assertRaisesRegex(
                        RuntimeCompositionError,
                        "format or format_version",
                    ):
                        validator(invalid_version)

        catalog = _read_fixture("capability-catalog.json")
        catalog["capabilities"] = list(reversed(catalog["capabilities"]))
        with self.assertRaisesRegex(RuntimeCompositionError, "canonical ID order"):
            validate_runtime_capability_catalog(_reseal(catalog))

        composition = _read_fixture("composition.json")
        composition["required_capability_ids"] = list(
            reversed(composition["required_capability_ids"])
        )
        with self.assertRaisesRegex(RuntimeCompositionError, "sorted unique"):
            validate_runtime_composition(_reseal(composition))

    def test_unknown_ids_and_resealed_semantic_mutations_are_rejected(self) -> None:
        catalog = _read_fixture("capability-catalog.json")
        catalog["capabilities"][0]["id"] = "action_unknown"
        with self.assertRaisesRegex(RuntimeCompositionError, "static capability catalog"):
            validate_runtime_capability_catalog(_reseal(catalog))

        profile = _read_fixture("profiles/profile_2d.json")
        profile["mode"] = "3d"
        with self.assertRaisesRegex(RuntimeCompositionError, "static profile"):
            validate_runtime_presentation_profile(_reseal(profile))

        adapter = _read_fixture("adapter.declared.json")
        adapter["capability_ids"].append("unknown_capability")
        adapter["capability_ids"].sort()
        with self.assertRaisesRegex(RuntimeCompositionError, "unknown capability"):
            validate_runtime_adapter(_reseal(adapter))

        composition = _read_fixture("composition.json")
        duplicate = copy.deepcopy(composition["slot_owners"][0])
        composition["slot_owners"].append(duplicate)
        with self.assertRaisesRegex(RuntimeCompositionError, "duplicate semantic slot"):
            validate_runtime_composition(_reseal(composition))

    def test_profile_schema_and_python_share_the_exact_complete_matrix(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/runtime-presentation-profile.schema.json").read_text(encoding="utf-8")
        )
        branches = schema["allOf"][0]["oneOf"]
        schema_matrix = {
            branch["properties"]["id"]["const"]: {
                field: branch["properties"][field]["const"]
                for field in (
                    "mode",
                    "layers",
                    "required_packs",
                    "required_capability_ids",
                )
            }
            for branch in branches
        }
        expected = {
            profile_id: {
                "mode": profile["mode"],
                "layers": list(profile["layers"]),
                "required_packs": list(profile["required_packs"]),
                "required_capability_ids": list(profile["required_capability_ids"]),
            }
            for profile_id, profile in PRESENTATION_PROFILES.items()
        }
        self.assertEqual(expected, schema_matrix)

        for field, replacement in (
            ("layers", ["2d", "3d"]),
            ("required_packs", ["assetpack", "renderpack"]),
            ("required_capability_ids", ["presentation_world_2d"]),
        ):
            with self.subTest(field=field):
                profile = _read_fixture("profiles/profile_2d.json")
                profile[field] = replacement
                with self.assertRaisesRegex(RuntimeCompositionError, "static profile"):
                    validate_runtime_presentation_profile(_reseal(profile))

    def test_declared_adapter_report_is_explicitly_incompatible(self) -> None:
        adapter = validate_runtime_adapter(_read_fixture("adapter.declared.json"))
        report = validate_runtime_compatibility_report(_read_fixture("compatibility-report.json"))
        self.assertEqual("declared", adapter["state"])
        self.assertFalse(report["compatible"])
        self.assertEqual(list(COMPATIBILITY_CHECK_IDS), [item["id"] for item in report["checks"]])
        self.assertEqual(
            ["adapter_not_verified", "pack_unverified"],
            [issue["code"] for check in report["checks"] for issue in check["issues"]],
        )
        self.assertEqual(
            {
                "adapter_not_verified",
                "asset_binding_missing",
                "capability_missing",
                "pack_hash_mismatch",
                "pack_kind_missing",
                "pack_unverified",
                "platform_unsupported",
                "profile_mismatch",
                "representation_mismatch",
                "runtime_api_incompatible",
                "semantic_slot_duplicate",
                "semantic_slot_missing",
                "world_identity_mismatch",
            },
            set(COMPATIBILITY_ISSUE_CODES),
        )

    def test_verifier_correlates_integral_pack_loaders_without_mutating_inputs(self) -> None:
        catalog = _read_fixture("capability-catalog.json")
        profile = _read_fixture("profiles/profile_2d.json")
        adapter = _read_fixture("adapter.declared.json")
        composition = _read_fixture("composition.json")
        originals = copy.deepcopy((catalog, profile, adapter, composition))

        worldpack = load_worldpack(ROOT / "content/compiled/foundation.worldpack.json")
        renderpack = SimpleNamespace(
            world_id=composition["world_id"],
            world_content_hash=composition["world_content_hash"],
            content_hash=composition["packs"]["renderpack"]["content_hash"],
            assets=(SimpleNamespace(id="neutral_sprite", kind="sprite"),),
            bindings=(
                SimpleNamespace(
                    slot="actor:neutral",
                    asset_id="neutral_sprite",
                ),
            ),
        )
        renderpack.__enter__ = lambda self: self
        renderpack.__exit__ = lambda self, *_args: None

        class _LoadedRenderPack:
            def __enter__(self) -> object:
                return renderpack

            def __exit__(self, *_args: object) -> None:
                return None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for pack in composition["packs"].values():
                path = root / pack["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            with (
                patch(
                    "worldforge.runtime_composition.load_worldpack",
                    return_value=worldpack,
                ) as world_loader,
                patch(
                    "worldforge.runtime_composition.load_renderpack",
                    return_value=_LoadedRenderPack(),
                ) as render_loader,
            ):
                result = verify_runtime_composition(
                    catalog,
                    profile,
                    adapter,
                    composition,
                    root=root,
                    platform="linux_x86_64",
                    runtime_api_version="0.5.0",
                )

        world_loader.assert_called_once()
        render_loader.assert_called_once()
        self.assertFalse(result.compatible)
        self.assertEqual(
            {"adapter_not_verified"},
            {issue.code for issue in result.issues},
        )
        self.assertEqual(originals, (catalog, profile, adapter, composition))
        self.assertEqual(result.report, validate_runtime_compatibility_report(result.report))

    def test_checked_in_report_matches_real_integral_fixture_verification(self) -> None:
        result = verify_runtime_composition(
            _read_fixture("capability-catalog.json"),
            _read_fixture("profiles/profile_2d.json"),
            _read_fixture("adapter.declared.json"),
            _read_fixture("composition.json"),
            root=ROOT,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
        )

        self.assertFalse(result.compatible)
        self.assertEqual(
            {"adapter_not_verified", "pack_unverified"},
            {issue.code for issue in result.issues},
        )
        self.assertEqual(_read_fixture("compatibility-report.json"), result.report)

    def test_negative_pack_diagnostics_retain_semantic_slot_paths(self) -> None:
        catalog = _read_fixture("capability-catalog.json")
        worldpack = load_worldpack(ROOT / "content/compiled/foundation.worldpack.json")

        render_profile = _read_fixture("profiles/profile_2d.json")
        render_adapter = _read_fixture("adapter.declared.json")
        render_composition = _read_fixture("composition.json")
        render_composition["slot_owners"][0]["asset_id"] = "wrong_sprite"
        render_composition["slot_owners"][0]["plane"] = "world_overlay"
        render_composition = _reseal(render_composition)

        class _RenderPack:
            world_id = worldpack.world_id
            world_content_hash = worldpack.content_hash
            content_hash = render_composition["packs"]["renderpack"]["content_hash"]
            assets = (SimpleNamespace(id="neutral_sprite", kind="sprite"),)
            bindings = (SimpleNamespace(slot="actor:neutral", asset_id="neutral_sprite"),)

            def __enter__(self) -> _RenderPack:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for pack in render_composition["packs"].values():
                path = root / pack["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            with (
                patch("worldforge.runtime_composition.load_worldpack", return_value=worldpack),
                patch("worldforge.runtime_composition.load_renderpack", return_value=_RenderPack()),
            ):
                render_result = verify_runtime_composition(
                    catalog,
                    render_profile,
                    render_adapter,
                    render_composition,
                    root=root,
                    platform="linux_x86_64",
                )

        render_paths = {issue.path for issue in render_result.issues}
        self.assertIn("renderpack/bindings/actor:neutral", render_paths)
        self.assertIn("composition/slot_owners/actor:neutral", render_paths)
        validate_runtime_compatibility_report(render_result.report)

        asset_profile = _read_fixture("profiles/profile_3d.json")
        asset_adapter = copy.deepcopy(render_adapter)
        asset_adapter["capability_ids"] = sorted(
            set(asset_profile["required_capability_ids"])
            | set(worldpack.runtime_requirements.required_features)
        )
        asset_adapter = _reseal(asset_adapter)
        asset_composition = copy.deepcopy(render_composition)
        asset_composition["profile"] = {
            "id": asset_profile["id"],
            "content_hash": asset_profile["content_hash"],
        }
        asset_composition["adapter"]["content_hash"] = asset_adapter["content_hash"]
        asset_composition["packs"] = {
            "assetpack": {
                "path": "assetpack.json",
                "format": "rpg-world-forge.assetpack",
                "format_version": 1,
                "content_hash": "2" * 64,
            },
            "worldpack": render_composition["packs"]["worldpack"],
        }
        asset_composition["required_capability_ids"] = asset_adapter["capability_ids"]
        asset_composition["slot_owners"] = [
            {
                "slot": "actor:neutral:variant",
                "plane": "world_base",
                "pack": "assetpack",
                "asset_id": "neutral_mesh",
                "representation": "3d",
            }
        ]
        asset_composition = _reseal(asset_composition)
        assetpack = {
            "content_hash": "2" * 64,
            "world_id": worldpack.world_id,
            "world_content_hash": worldpack.content_hash,
            "assets": [{"id": "neutral_mesh", "representation": "2d"}],
            "bindings": [
                {
                    "slot": "actor:neutral:variant",
                    "asset_id": "neutral_mesh",
                    "representation": "2d",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for pack in asset_composition["packs"].values():
                path = root / pack["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            with (
                patch("worldforge.runtime_composition.load_worldpack", return_value=worldpack),
                patch("worldforge.runtime_composition.verify_assetpack", return_value=assetpack),
            ):
                asset_result = verify_runtime_composition(
                    catalog,
                    asset_profile,
                    asset_adapter,
                    asset_composition,
                    root=root,
                    platform="linux_x86_64",
                )

        self.assertIn(
            "assetpack/bindings/actor:neutral:variant",
            {issue.path for issue in asset_result.issues},
        )
        validate_runtime_compatibility_report(asset_result.report)
        schema = json.loads(
            (ROOT / "schemas/runtime-compatibility-report.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "^[a-z0-9_/:-]+$",
            schema["$defs"]["issue"]["properties"]["path"]["pattern"],
        )

    def test_adapter_contract_has_no_execution_or_provider_locators(self) -> None:
        adapter = _read_fixture("adapter.declared.json")
        serialized = json.dumps(adapter, sort_keys=True).casefold()
        for forbidden in (
            '"command"',
            '"executable"',
            '"module"',
            '"path"',
            '"provider"',
            '"model"',
            '"mcp"',
            "openai",
            "ollama",
            "blender",
            "modly",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_isoworld_boundary_and_m5_contract_bytes_are_unchanged(self) -> None:
        tracked = (
            ROOT / "schemas/worldpack.schema.json",
            ROOT / "schemas/renderpack.schema.json",
            ROOT / "schemas/assetpack.schema.json",
            ROOT / "schemas/runtime-bundle.schema.json",
            ROOT / "content/compiled/foundation.worldpack.json",
        )
        before = {path: path.read_bytes() for path in tracked}
        for source in (ROOT / "src/isoworld").rglob("*.py"):
            self.assertNotIn("worldforge", source.read_text(encoding="utf-8"))
        validate_runtime_capability_catalog(_read_fixture("capability-catalog.json"))
        validate_runtime_composition(_read_fixture("composition.json"))
        self.assertEqual(before, {path: path.read_bytes() for path in tracked})

    def test_composition_keeps_integral_worldpack_v1_through_v5_loading(self) -> None:
        source = json.loads(
            (ROOT / "content/compiled/foundation.worldpack.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for version in range(1, 6):
                with self.subTest(version=version):
                    payload = copy.deepcopy(source)
                    payload["format_version"] = version
                    if version < 5:
                        payload.pop("runtime_requirements", None)
                        payload["world"].pop("default_locale", None)
                        payload["world"].pop("supported_locales", None)
                    payload["content_hash"] = canonical_payload_hash(payload)
                    path = root / f"worldpack-v{version}.json"
                    path.write_bytes(canonical_json_bytes(payload))
                    loaded = load_worldpack(path)
                    self.assertEqual(version, loaded.format_version)
                    self.assertEqual(payload["content_hash"], loaded.content_hash)


if __name__ == "__main__":
    unittest.main()
