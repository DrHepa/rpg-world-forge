from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.asset_manifest_v3 as asset_manifest_module
from worldforge.__main__ import build_parser
from worldforge.asset_inventory import derive_asset_inventory
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    write_json_atomic,
)
from worldforge.asset_manifest_v3 import bind_asset_plan
from worldforge.assets import init_asset_manifest, validate_asset_manifest
from worldforge.compiler import compile_project
from worldforge.scaffold import create_world_project
from worldforge.workflow import PHASES, complete_phase, reopen_phase, validate_phase_report


def _write_bibles(root: Path) -> tuple[Path, Path]:
    target = json.loads((root / "assets/target.json").read_text(encoding="utf-8"))
    common = {
        "format_version": 1,
        "world_id": target["world_id"],
        "world_content_hash": target["world_content_hash"],
        "target_id": target["id"],
        "target_hash": target["content_hash"],
        "acceptance_tests": ["target_smoke_passes"],
        "approved_by": "authorized_lead",
    }
    visual = bind_content_hash(
        {
            **common,
            "format": "rpg-world-forge.visual_bible",
            "camera": {"projection": "isometric"},
            "resolution": {"base": [640, 360]},
            "style": {"palette": ["#111111", "#eeeeee"]},
            "silhouettes": {"minimum_separation": "one_pixel"},
            "animation": {"clock": "integer_ticks"},
            "ui": {"minimum_text_px": 14},
            "vfx": {"photosensitivity": "safe"},
        }
    )
    audio = bind_content_hash(
        {
            **common,
            "format": "rpg-world-forge.audio_bible",
            "format_policy": {"runtime": "wav", "sample_rate": 22050},
            "mix": {"peak_dbfs": -1},
            "timbral_families": ["neutral"],
            "ambience": {"layers": 1},
            "music": {"loop": True},
            "sfx": {"variations": 2},
        }
    )
    visual_path = root / "assets/bibles/visual.json"
    audio_path = root / "assets/bibles/audio.json"
    write_json_atomic(visual_path, visual)
    write_json_atomic(audio_path, audio)
    return visual_path, audio_path


def _write_specs(root: Path, inventory: dict[str, object]) -> None:
    target = json.loads((root / "assets/target.json").read_text(encoding="utf-8"))
    entries = [*inventory["requirements"], *inventory["manual_additions"]]  # type: ignore[index]
    for item in entries:
        representation = item["representation"]
        kind = item["kind"]
        technical: dict[str, object] = {
            "runtime_format": "wav"
            if representation == "audio"
            else "ttf"
            if kind == "font"
            else "png",
            "memory_budget_bytes": 4_000_000,
        }
        expected: list[dict[str, str]]
        if representation == "audio":
            technical.update({"sample_rate": 22050, "channels": 1})
            expected = [{"role": "audio", "media_type": "audio/wav"}]
        elif kind == "font":
            expected = [{"role": "font", "media_type": "font/ttf"}]
        else:
            technical.update({"width": 64, "height": 64, "alpha_mode": "blend"})
            expected = [{"role": "texture", "media_type": "image/png"}]
            if kind in {"spritesheet", "tileset"}:
                expected.append({"role": "clipset", "media_type": "application/json"})
        spec = bind_content_hash(
            {
                "format": "rpg-world-forge.asset_spec",
                "format_version": 2,
                "id": item["id"],
                "kind": kind,
                "representation": representation,
                "target_id": target["id"],
                "target_hash": target["content_hash"],
                "inventory_hash": inventory["content_hash"],
                "visual_bible_hash": inventory["visual_bible_hash"],
                "audio_bible_hash": inventory["audio_bible_hash"],
                "purpose": item["purpose"],
                "canonical_sources": item["canonical_sources"],
                "acceptance_criteria": ["matches_bible", "meets_runtime_budget"],
                "semantic_slots": item["semantic_slots"],
                "technical": technical,
                "production": {
                    "allowed_routes": ["openai"],
                    "allowed_executors": ["human", "openai_image", "procedural"],
                },
                "expected_outputs": expected,
            }
        )
        write_json_atomic(root / f"assets/specs/{item['id']}.json", spec)


def _report(phase: str, deliverables: list[str], **extra: str) -> dict[str, object]:
    return {
        "format": "rpg-world-forge.phase_report",
        "format_version": 1,
        "phase": phase,
        "status": "ready",
        "summary": f"{phase} contracts validate.",
        "deliverables": deliverables,
        "decisions": [],
        "blockers": [],
        "validations": [{"name": "contracts", "passed": True, "evidence": "offline validation"}],
        "reviewed_by": "authorized_lead",
        **extra,
    }


def _prepare_bound_plan(root: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    source_manifest = create_world_project(
        root,
        world_id="m5_continuity_world",
        title="M5 Continuity World",
        language="en",
        actor_id="hero",
        actor_name="Hero",
    )
    worldpack = root / "build/world.worldpack.json"
    compiled = compile_project(source_manifest, worldpack)
    manifest = root / "assets/manifest.json"
    initial = init_asset_manifest(
        worldpack,
        manifest,
        target_dimension="2_5d",
        target_id="primary",
    )
    visual, audio = _write_bibles(root)
    status_path = root / ".worldforge/status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "current_phase": "p11_art_audio",
            "completed_phases": [phase.id for phase in PHASES[:11]],
            "revision": 11,
            "canon_locked": True,
            "worldpack_hash": compiled["content_hash"],
            "worldpack_path": "build/world.worldpack.json",
        }
    )
    status_path.write_text(json.dumps(status), encoding="utf-8")
    p11_path = root / "p11.json"
    write_json_atomic(
        p11_path,
        _report(
            "p11_art_audio",
            ["assets/target.json", "assets/bibles/visual.json", "assets/bibles/audio.json"],
            asset_target_path="assets/target.json",
            visual_bible_path="assets/bibles/visual.json",
            audio_bible_path="assets/bibles/audio.json",
        ),
    )
    complete_phase(root, p11_path)
    inventory_path = root / "assets/inventory/derived.json"
    inventory = derive_asset_inventory(
        worldpack,
        root / "assets/target.json",
        visual,
        audio,
        inventory_path,
    )
    _write_specs(root, inventory)
    bind_asset_plan(
        manifest,
        visual_bible_path=visual,
        audio_bible_path=audio,
        inventory_path=inventory_path,
        expected_manifest_hash=initial["content_hash"],
    )
    return worldpack, manifest, inventory_path, inventory


class M5PlanTests(unittest.TestCase):
    def test_v3_initialization_is_an_honest_art_direction_draft(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            manifest = root / "assets/manifest.json"
            worldpack = (
                Path(__file__).resolve().parents[1] / "content/compiled/foundation.worldpack.json"
            )
            raw = init_asset_manifest(
                worldpack,
                manifest,
                target_dimension="2_5d",
                target_id="primary",
            )
            self.assertEqual(3, raw["format_version"])
            self.assertEqual({"visual": None, "audio": None}, raw["bibles"])
            self.assertIsNone(raw["inventory"])
            self.assertEqual(["openai"], raw["generation_policy"]["enabled_routes"])
            self.assertNotIn("modly_cli_mcp", raw["generation_policy"]["executors"])
            self.assertEqual([], validate_asset_manifest(manifest, worldpack_path=worldpack))
        finally:
            shutil.rmtree(root)

    def test_v3_initialization_enables_modly_only_with_explicit_flag(self) -> None:
        worldpack = (
            Path(__file__).resolve().parents[1] / "content/compiled/foundation.worldpack.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "assets/manifest.json"
            raw = init_asset_manifest(
                worldpack,
                manifest,
                target_dimension="2_5d",
                enable_modly=True,
            )
            self.assertEqual(
                ["modly", "openai"],
                raw["generation_policy"]["enabled_routes"],
            )
            self.assertIn("modly_cli_mcp", raw["generation_policy"]["executors"])
            self.assertEqual([], validate_asset_manifest(manifest, worldpack_path=worldpack))

        args = build_parser().parse_args(
            [
                "init-assets",
                "worldpack.json",
                "--output",
                "assets/manifest.json",
                "--target-dimension",
                "3d",
                "--enable-modly",
            ]
        )
        self.assertTrue(args.enable_modly)

    def test_v3_initialization_rejects_a_symlinked_asset_root_without_external_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            asset_root = root / "assets"
            try:
                asset_root.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            worldpack = (
                Path(__file__).resolve().parents[1] / "content/compiled/foundation.worldpack.json"
            )
            with self.assertRaises(AssetContractError):
                init_asset_manifest(
                    worldpack,
                    asset_root / "manifest.json",
                    target_dimension="2_5d",
                )
            self.assertEqual([], list(external.iterdir()))

    def test_p11_and_p12_require_real_bound_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source_manifest = create_world_project(
                root,
                world_id="m5_plan_world",
                title="M5 Plan World",
                language="en",
                actor_id="hero",
                actor_name="Hero",
            )
            worldpack = root / "build/world.worldpack.json"
            compiled = compile_project(source_manifest, worldpack)
            manifest = root / "assets/manifest.json"
            initial = init_asset_manifest(
                worldpack,
                manifest,
                target_dimension="2_5d",
                target_id="primary",
            )
            visual, audio = _write_bibles(root)
            status_path = root / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update(
                {
                    "current_phase": "p11_art_audio",
                    "completed_phases": [phase.id for phase in PHASES[:11]],
                    "revision": 11,
                    "canon_locked": True,
                    "worldpack_hash": compiled["content_hash"],
                    "worldpack_path": "build/world.worldpack.json",
                }
            )
            status_path.write_text(json.dumps(status), encoding="utf-8")
            p11_path = root / "p11.json"
            write_json_atomic(
                p11_path,
                _report(
                    "p11_art_audio",
                    ["assets/target.json", "assets/bibles/visual.json", "assets/bibles/audio.json"],
                    asset_target_path="assets/target.json",
                    visual_bible_path="assets/bibles/visual.json",
                    audio_bible_path="assets/bibles/audio.json",
                ),
            )
            after_p11 = complete_phase(root, p11_path)
            self.assertEqual("p12_asset_specs", after_p11["current_phase"])

            inventory_path = root / "assets/inventory/derived.json"
            inventory = derive_asset_inventory(
                worldpack,
                root / "assets/target.json",
                visual,
                audio,
                inventory_path,
            )
            _write_specs(root, inventory)
            bound = bind_asset_plan(
                manifest,
                visual_bible_path=visual,
                audio_bible_path=audio,
                inventory_path=inventory_path,
                expected_manifest_hash=initial["content_hash"],
            )
            self.assertEqual([], validate_asset_manifest(manifest, worldpack_path=worldpack))
            p12_path = root / "p12.json"
            write_json_atomic(
                p12_path,
                _report(
                    "p12_asset_specs",
                    ["assets/inventory/derived.json", "assets/manifest.json"],
                    asset_inventory_path="assets/inventory/derived.json",
                    asset_manifest_path="assets/manifest.json",
                ),
            )
            after_p12 = complete_phase(root, p12_path)
            self.assertEqual("p13_asset_production", after_p12["current_phase"])
            self.assertEqual("assets/inventory/derived.json", after_p12["asset_inventory"])
            self.assertEqual("assets/manifest.json", after_p12["asset_manifest"])
            self.assertGreater(len(bound["assets"]), 3)

    def test_p12_rejects_an_equivalent_inventory_at_a_different_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _, manifest, inventory_path, _ = _prepare_bound_plan(root)
            alternate = root / "assets/inventory/equivalent.json"
            alternate.write_bytes(inventory_path.read_bytes())
            report_path = root / "p12-substitution.json"
            write_json_atomic(
                report_path,
                _report(
                    "p12_asset_specs",
                    ["assets/inventory/equivalent.json", "assets/manifest.json"],
                    asset_inventory_path="assets/inventory/equivalent.json",
                    asset_manifest_path="assets/manifest.json",
                ),
            )

            _, errors = validate_phase_report(root, report_path)

            self.assertTrue(
                any(
                    "P12 inventory path" in error and "prior phase path" in error
                    for error in errors
                ),
                errors,
            )

            target_copy = root / "assets/target-copy.json"
            target_copy.write_bytes((root / "assets/target.json").read_bytes())
            manifest_raw = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_raw["target"] = artifact_reference(
                manifest.parent,
                "target-copy.json",
            )
            write_json_atomic(
                manifest,
                bind_content_hash(manifest_raw),
                overwrite=True,
            )
            canonical_report = root / "p12-target-substitution.json"
            write_json_atomic(
                canonical_report,
                _report(
                    "p12_asset_specs",
                    ["assets/inventory/derived.json", "assets/manifest.json"],
                    asset_inventory_path="assets/inventory/derived.json",
                    asset_manifest_path="assets/manifest.json",
                ),
            )

            _, target_errors = validate_phase_report(root, canonical_report)

            self.assertTrue(
                any("P12 target path" in error for error in target_errors),
                target_errors,
            )

    def test_p13_rejects_manifest_and_pack_path_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            worldpack, manifest, inventory_path, _ = _prepare_bound_plan(root)
            p12_path = root / "p12.json"
            write_json_atomic(
                p12_path,
                _report(
                    "p12_asset_specs",
                    ["assets/inventory/derived.json", "assets/manifest.json"],
                    asset_inventory_path="assets/inventory/derived.json",
                    asset_manifest_path="assets/manifest.json",
                ),
            )
            complete_phase(root, p12_path)

            worldpack_raw = json.loads(worldpack.read_text(encoding="utf-8"))
            pack = bind_content_hash(
                {
                    "format": "isoworld.renderpack",
                    "format_version": 1,
                    "world_id": "m5_continuity_world",
                    "world_content_hash": worldpack_raw["content_hash"],
                }
            )
            pack_path = root / "assets/release/renderpack.json"
            alternate_pack_path = root / "assets/release/equivalent-renderpack.json"
            write_json_atomic(pack_path, pack)
            write_json_atomic(alternate_pack_path, pack)

            manifest_raw = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_raw["phase"] = "release"
            manifest_raw["deliverable"] = {
                "format": "isoworld.renderpack",
                **artifact_reference(manifest.parent, "release/renderpack.json"),
                "content_hash": pack["content_hash"],
            }
            manifest_raw = bind_content_hash(manifest_raw)
            write_json_atomic(manifest, manifest_raw, overwrite=True)
            manifest_copy = root / "assets/manifest-copy.json"
            manifest_copy.write_bytes(manifest.read_bytes())

            matching_report = _report(
                "p13_asset_production",
                ["assets/manifest.json", "assets/release/renderpack.json"],
                asset_manifest_path="assets/manifest.json",
                renderpack_path="assets/release/renderpack.json",
            )
            substituted_manifest_report = {
                **matching_report,
                "deliverables": [
                    "assets/manifest-copy.json",
                    "assets/release/renderpack.json",
                ],
                "asset_manifest_path": "assets/manifest-copy.json",
            }
            substituted_pack_report = {
                **matching_report,
                "deliverables": [
                    "assets/manifest.json",
                    "assets/release/equivalent-renderpack.json",
                ],
                "renderpack_path": "assets/release/equivalent-renderpack.json",
            }
            matching_path = root / "p13-matching.json"
            manifest_substitution_path = root / "p13-manifest-substitution.json"
            pack_substitution_path = root / "p13-pack-substitution.json"
            write_json_atomic(matching_path, matching_report)
            write_json_atomic(manifest_substitution_path, substituted_manifest_report)
            write_json_atomic(pack_substitution_path, substituted_pack_report)

            with (
                patch("worldforge.assets.validate_asset_manifest", return_value=[]),
                patch("isoworld.content.renderpack.load_renderpack", return_value=object()),
            ):
                _, matching_errors = validate_phase_report(root, matching_path)
                _, manifest_errors = validate_phase_report(root, manifest_substitution_path)
                _, pack_errors = validate_phase_report(root, pack_substitution_path)

                wrong_content_hash = bind_content_hash(
                    {
                        **manifest_raw,
                        "deliverable": {
                            **manifest_raw["deliverable"],
                            "content_hash": "0" * 64,
                        },
                    }
                )
                write_json_atomic(manifest, wrong_content_hash, overwrite=True)
                _, content_hash_errors = validate_phase_report(root, matching_path)

                wrong_sha = bind_content_hash(
                    {
                        **manifest_raw,
                        "deliverable": {
                            **manifest_raw["deliverable"],
                            "sha256": "0" * 64,
                        },
                    }
                )
                write_json_atomic(manifest, wrong_sha, overwrite=True)
                _, sha_errors = validate_phase_report(root, matching_path)

                write_json_atomic(manifest, manifest_raw, overwrite=True)
                completed = complete_phase(root, matching_path)

            self.assertEqual([], matching_errors)
            self.assertTrue(
                any("exactly match the P12 asset manifest" in error for error in manifest_errors),
                manifest_errors,
            )
            self.assertTrue(
                any("does not match manifest.deliverable" in error for error in pack_errors),
                pack_errors,
            )
            self.assertTrue(
                any("content_hash does not match" in error for error in content_hash_errors),
                content_hash_errors,
            )
            self.assertTrue(
                any("SHA-256 does not match" in error for error in sha_errors),
                sha_errors,
            )
            self.assertEqual("assets/manifest.json", completed["asset_manifest"])
            self.assertEqual("assets/release/renderpack.json", completed["renderpack"])
            reopened = reopen_phase(
                root,
                "p13_asset_production",
                reason="Rebuild the delivery pack",
                approved_by="authorized_lead",
            )
            self.assertEqual("assets/manifest.json", reopened["asset_manifest"])
            self.assertIsNone(reopened["renderpack"])

    def test_bind_plan_uses_optimistic_manifest_hash(self) -> None:
        worldpack = (
            Path(__file__).resolve().parents[1] / "content/compiled/foundation.worldpack.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "assets/manifest.json"
            init_asset_manifest(worldpack, manifest, target_dimension="2d")
            visual, audio = _write_bibles(root)
            inventory_path = root / "assets/inventory/derived.json"
            inventory = derive_asset_inventory(
                worldpack,
                root / "assets/target.json",
                visual,
                audio,
                inventory_path,
            )
            _write_specs(root, inventory)
            with self.assertRaisesRegex(AssetContractError, "changed"):
                bind_asset_plan(
                    manifest,
                    visual_bible_path=visual,
                    audio_bible_path=audio,
                    inventory_path=inventory_path,
                    expected_manifest_hash="0" * 64,
                )

    def test_bind_plan_cas_preserves_manifest_changed_during_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _, manifest, inventory_path, _ = _prepare_bound_plan(root)
            initial = json.loads(manifest.read_text(encoding="utf-8"))
            concurrent = json.loads(json.dumps(initial))
            concurrent["generation_policy"]["enabled_routes"] = ["local", "openai"]
            concurrent = bind_content_hash(concurrent)
            original_write = asset_manifest_module.write_json_atomic

            def change_manifest_before_publish(
                path: str | Path,
                value: object,
                **kwargs: object,
            ) -> None:
                if Path(path) == manifest:
                    manifest.write_text(
                        json.dumps(concurrent, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                original_write(path, value, **kwargs)  # type: ignore[arg-type]

            with (
                patch.object(
                    asset_manifest_module,
                    "write_json_atomic",
                    side_effect=change_manifest_before_publish,
                ),
                self.assertRaisesRegex(AssetContractError, "Content changed before publishing"),
            ):
                bind_asset_plan(
                    manifest,
                    visual_bible_path=root / "assets/bibles/visual.json",
                    audio_bible_path=root / "assets/bibles/audio.json",
                    inventory_path=inventory_path,
                    expected_manifest_hash=initial["content_hash"],
                )

            self.assertEqual(
                concurrent,
                json.loads(manifest.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
