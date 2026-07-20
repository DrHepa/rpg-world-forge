from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from worldforge.asset_inventory import (
    create_asset_target,
    derive_asset_inventory,
    validate_asset_inventory,
)
from worldforge.asset_io import AssetContractError, bind_content_hash, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"


def _bibles(root: Path, target: dict[str, object]) -> tuple[Path, Path]:
    common = {
        "format_version": 1,
        "world_id": target["world_id"],
        "world_content_hash": target["world_content_hash"],
        "target_id": target["id"],
        "target_hash": target["content_hash"],
        "acceptance_tests": ["loads_in_target"],
        "approved_by": "gpt_lead",
    }
    visual = bind_content_hash(
        {
            **common,
            "format": "rpg-world-forge.visual_bible",
            "camera": {"projection": "isometric"},
            "resolution": {"base": [320, 180]},
            "style": {"palette": ["#000000", "#ffffff"]},
            "silhouettes": {"actors": "readable"},
            "animation": {"ticks": "integer"},
            "ui": {"minimum_text_px": 12},
            "vfx": {"contrast": "high"},
        }
    )
    audio = bind_content_hash(
        {
            **common,
            "format": "rpg-world-forge.audio_bible",
            "format_policy": {"runtime": "wav", "sample_rate": 48000},
            "mix": {"peak_dbfs": -1},
            "timbral_families": ["organic"],
            "ambience": {"layers": 2},
            "music": {"loop": True},
            "sfx": {"variations": 2},
        }
    )
    visual_path = root / "bibles/visual.json"
    audio_path = root / "bibles/audio.json"
    write_json_atomic(visual_path, visual)
    write_json_atomic(audio_path, audio)
    return visual_path, audio_path


class M5InventoryTests(unittest.TestCase):
    def test_target_rejects_reserved_portable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssetContractError, "canonical lowercase ID"):
                create_asset_target(
                    WORLDPACK,
                    Path(directory) / "target.json",
                    target_id="con",
                    dimension="2d",
                )

    def _derive(self, root: Path, dimension: str = "2_5d") -> tuple[dict, Path]:
        target_path = root / "target.json"
        target = create_asset_target(
            WORLDPACK,
            target_path,
            target_id="primary",
            dimension=dimension,
        )
        visual, audio = _bibles(root, target)
        output = root / "inventory/derived.json"
        inventory = derive_asset_inventory(WORLDPACK, target_path, visual, audio, output)
        return inventory, output

    def test_inventory_is_byte_identical_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_inventory, first_path = self._derive(Path(first))
            second_inventory, second_path = self._derive(Path(second))
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(first_inventory["content_hash"], second_inventory["content_hash"])
            ids = [item["id"] for item in first_inventory["requirements"]]
            self.assertEqual(sorted(ids), ids)
            required_slots = {
                slot
                for item in first_inventory["requirements"]
                if item["required"]
                for slot in item["semantic_slots"]
            }
            all_slots = {
                slot for item in first_inventory["requirements"] for slot in item["semantic_slots"]
            }
            self.assertIn("ui:font", all_slots)
            self.assertNotIn("ui:font", required_slots)
            self.assertIn("music:default", required_slots)
            self.assertIn("event:interaction_completed", required_slots)
            worldpack = json.loads(WORLDPACK.read_text(encoding="utf-8"))
            for actor in worldpack["collections"]["actors"]:
                self.assertIn(f"actor:{actor['id']}", required_slots)
            for tile in worldpack["collections"]["tile_types"]:
                self.assertIn(f"tile_type:{tile['id']}", required_slots)

    def test_3d_inventory_changes_visual_contract_but_keeps_2d_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory, _ = self._derive(Path(directory), "3d")
            actors = [item for item in inventory["requirements"] if item["id"].startswith("actor_")]
            gameplay = [item for item in actors if item["id"].endswith("_visual")]
            portraits = [item for item in actors if item["id"].endswith("_portrait")]
            self.assertTrue(gameplay)
            self.assertTrue(all(item["kind"] == "character_3d" for item in gameplay))
            self.assertTrue(all(item["representation"] == "3d" for item in gameplay))
            self.assertTrue(all(item["representation"] == "2d" for item in portraits))

    def test_inventory_rejects_unapproved_or_stale_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_path = root / "target.json"
            target = create_asset_target(
                WORLDPACK,
                target_path,
                target_id="primary",
                dimension="2_5d",
            )
            visual, audio = _bibles(root, target)
            raw = json.loads(visual.read_text(encoding="utf-8"))
            raw["approved_by"] = ""
            raw = bind_content_hash(raw)
            visual.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "must be approved"):
                derive_asset_inventory(
                    WORLDPACK,
                    target_path,
                    visual,
                    audio,
                    root / "inventory.json",
                )

    def test_derivation_never_overwrites_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, output = self._derive(root)
            original = output.read_bytes()
            target = json.loads((root / "target.json").read_text(encoding="utf-8"))
            visual, audio = root / "bibles/visual.json", root / "bibles/audio.json"
            with self.assertRaisesRegex(AssetContractError, "Refusing to overwrite"):
                derive_asset_inventory(
                    WORLDPACK,
                    root / "target.json",
                    visual,
                    audio,
                    output,
                )
            self.assertEqual(original, output.read_bytes())
            self.assertEqual("primary", target["id"])

    def test_inventory_rejects_kind_representation_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, output = self._derive(root)
            inventory["requirements"][0]["kind"] = "material_set"
            inventory["requirements"][0]["representation"] = "2d"
            write_json_atomic(output, bind_content_hash(inventory), overwrite=True)
            self.assertTrue(
                any(
                    "must be 3d for material_set" in issue
                    for issue in validate_asset_inventory(output)
                )
            )

    def test_inventory_malformed_representation_returns_issues_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, output = self._derive(root)
            inventory["requirements"][0]["representation"] = []
            write_json_atomic(output, bind_content_hash(inventory), overwrite=True)

            issues = validate_asset_inventory(output)

            self.assertTrue(any("representation is invalid" in issue for issue in issues), issues)


if __name__ == "__main__":
    unittest.main()
