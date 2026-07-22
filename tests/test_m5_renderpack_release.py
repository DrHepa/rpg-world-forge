from __future__ import annotations

import binascii
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import worldforge.asset_manifest_v3 as asset_manifest_module
import worldforge.renderpack as renderpack_module
from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from worldforge.asset_inventory import create_asset_target
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    sha256_file,
    write_json_atomic,
)
from worldforge.asset_manifest_v3 import finalize_asset_release
from worldforge.asset_processing import process_asset_recipe
from worldforge.asset_production import create_production_request
from worldforge.assets import validate_asset_manifest
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import RenderPackBuildError, build_renderpack

WORLDPACK = Path(__file__).resolve().parents[1] / "content/compiled/foundation.worldpack.json"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))
    )


def _write_png(path: Path, *, width: int = 16, height: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = b"\x00" + bytes((70, 120, 180, 255)) * width
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(row * height))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def _write_hashed(path: Path, value: dict[str, object]) -> dict[str, object]:
    result = bind_content_hash(value)
    write_json_atomic(path, result)
    return result


def _reference(root: Path, path: Path) -> dict[str, object]:
    return artifact_reference(root, path.relative_to(root).as_posix())


def _fixture(root: Path) -> dict[str, Path]:
    authoring = root / "authoring"
    authoring.mkdir()
    worldpack = json.loads(WORLDPACK.read_text(encoding="utf-8"))
    world_id = worldpack["world"]["id"]
    target_path = authoring / "target.json"
    target = create_asset_target(
        WORLDPACK,
        target_path,
        target_id="neutral_2_5d",
        dimension="2_5d",
    )
    common: dict[str, object] = {
        "format_version": 1,
        "world_id": world_id,
        "world_content_hash": worldpack["content_hash"],
        "target_id": target["id"],
        "target_hash": target["content_hash"],
        "acceptance_tests": ["loads"],
        "approved_by": "lead",
    }
    visual_path = authoring / "bibles/visual.json"
    visual = _write_hashed(
        visual_path,
        {
            **common,
            "format": "rpg-world-forge.visual_bible",
            "camera": {"projection": "isometric"},
            "resolution": {"base": [16, 16]},
            "style": {"palette": ["#4678b4"]},
            "silhouettes": {"minimum_separation": "one_pixel"},
            "animation": {"clock": "integer_ticks"},
            "ui": {"minimum_text_px": 14},
            "vfx": {"photosensitivity": "safe"},
        },
    )
    audio_path = authoring / "bibles/audio.json"
    audio = _write_hashed(
        audio_path,
        {
            **common,
            "format": "rpg-world-forge.audio_bible",
            "format_policy": {"runtime": "wav", "sample_rate": 22050},
            "mix": {"peak_dbfs": -1},
            "timbral_families": ["neutral"],
            "ambience": {"layers": 1},
            "music": {"loop": True},
            "sfx": {"variations": 2},
        },
    )
    requirement = {
        "id": "neutral_portrait",
        "kind": "portrait",
        "representation": "2d",
        "required": True,
        "purpose": "Neutral portrait for a runtime renderpack fixture",
        "canonical_sources": ["world:foundation"],
        "semantic_slots": ["portrait:neutral"],
    }
    inventory_path = authoring / "inventory.json"
    inventory = _write_hashed(
        inventory_path,
        {
            "format": "rpg-world-forge.asset_inventory",
            "format_version": 1,
            "world_id": world_id,
            "world_content_hash": worldpack["content_hash"],
            "target_id": target["id"],
            "target_hash": target["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "requirements": [requirement],
            "manual_additions": [],
        },
    )
    spec_path = authoring / "specs/neutral_portrait.json"
    _write_hashed(
        spec_path,
        {
            "format": "rpg-world-forge.asset_spec",
            "format_version": 2,
            "id": "neutral_portrait",
            "kind": "portrait",
            "representation": "2d",
            "target_id": target["id"],
            "target_hash": target["content_hash"],
            "inventory_hash": inventory["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "purpose": requirement["purpose"],
            "canonical_sources": requirement["canonical_sources"],
            "acceptance_criteria": ["loads"],
            "semantic_slots": requirement["semantic_slots"],
            "technical": {
                "runtime_format": "png",
                "memory_budget_bytes": 1_048_576,
                "width": 16,
                "height": 16,
                "alpha_mode": "blend",
            },
            "production": {
                "allowed_routes": ["openai"],
                "allowed_executors": ["openai_image"],
            },
            "expected_outputs": [{"role": "texture", "media_type": "image/png"}],
        },
    )
    request_path = authoring / "requests/neutral_portrait.json"
    create_production_request(
        authoring,
        spec_path.relative_to(authoring).as_posix(),
        request_path,
        request_id="neutral_portrait_generate",
        route="openai",
        executor="openai_image",
        operation="image_generate",
        parameters={"model": "gpt-image-1", "size": "1024x1024"},
    )
    candidate_path = authoring / "generated/neutral_portrait.png"
    _write_png(candidate_path)
    receipt_path = authoring / "receipts/neutral_portrait.json"
    _write_hashed(
        receipt_path,
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": "receipt_neutral_portrait",
            "request": _reference(authoring, request_path),
            "asset_id": "neutral_portrait",
            "route": "openai",
            "executor": "openai_image",
            "operation": "image_generate",
            "status": "succeeded",
            "started_at": "2026-07-20T10:00:00Z",
            "completed_at": "2026-07-20T10:01:00Z",
            "parent_receipt_hashes": [],
            "toolchain": {
                "surface": "images_api",
                "requested_model": "gpt-image-1",
                "resolved_model": "gpt-image-1-2025-04-15",
                "version_resolution": "exact_snapshot",
            },
            "replayability": "traceable_not_bit_reproducible",
            "outputs": [
                {
                    "role": "texture",
                    **_reference(authoring, candidate_path),
                    "media_type": "image/png",
                    "width": 16,
                    "height": 16,
                }
            ],
        },
    )
    recipe_path = authoring / "neutral_portrait.recipe.json"
    _write_hashed(
        recipe_path,
        {
            "format": "rpg-world-forge.asset_processing_recipe",
            "format_version": 1,
            "operation": "png_canonical",
            "input": artifact_reference(authoring, "generated/neutral_portrait.png"),
            "output": {"file": "neutral_portrait.png"},
            "options": {},
        },
    )
    processed_directory = authoring / "processed/neutral_portrait"
    processing = process_asset_recipe(recipe_path, processed_directory, asset_root=authoring)
    processing_receipt_path = processed_directory / "processing.receipt.json"
    processed_path = processed_directory / "neutral_portrait.png"
    output_hash = processing["outputs"][0]["artifact"]["sha256"]

    license_evidence = authoring / "evidence/license.txt"
    license_evidence.parent.mkdir(parents=True)
    license_evidence.write_text("CC0 fixture evidence.\n", encoding="utf-8")
    notice_path = authoring / "evidence/NOTICE.txt"
    notice_path.write_text("CC0-1.0 neutral fixture.\n", encoding="utf-8")
    license_path = authoring / "licenses/neutral_portrait.json"
    _write_hashed(
        license_path,
        {
            "format": "rpg-world-forge.asset_license_record",
            "format_version": 1,
            "asset_id": "neutral_portrait",
            "output_hashes": [output_hash],
            "components": [
                {
                    "scope": scope,
                    "license_expression": "CC0-1.0",
                    "redistribution": "permitted",
                    "evidence": _reference(authoring, license_evidence),
                }
                for scope in ("asset", "dataset", "model", "output", "source", "weights")
            ],
            "notices": _reference(authoring, notice_path),
            "approved_by": "lead",
        },
    )
    qa_evidence = authoring / "evidence/qa.txt"
    qa_evidence.write_text("Runtime load passed.\n", encoding="utf-8")
    qa_path = authoring / "qa/neutral_portrait.json"
    _write_hashed(
        qa_path,
        {
            "format": "rpg-world-forge.asset_qa_report",
            "format_version": 1,
            "asset_id": "neutral_portrait",
            "target_hash": target["content_hash"],
            "output_hashes": [output_hash],
            "checks": [
                {
                    "id": "loads",
                    "passed": True,
                    "evidence": [_reference(authoring, qa_evidence)],
                }
            ],
            "blockers": [],
            "approved_by": "lead",
        },
    )
    manifest_path = authoring / "manifest.json"
    _write_hashed(
        manifest_path,
        {
            "format": "rpg-world-forge.asset_manifest",
            "format_version": 3,
            "world_id": world_id,
            "world_content_hash": worldpack["content_hash"],
            "target": _reference(authoring, target_path),
            "phase": "production",
            "generation_policy": {
                "orchestrator": "gpt",
                "enabled_routes": ["openai"],
                "local_model_route": "modly",
                "executors": ["openai_image"],
            },
            "bibles": {
                "visual": _reference(authoring, visual_path),
                "audio": _reference(authoring, audio_path),
            },
            "inventory": _reference(authoring, inventory_path),
            "assets": [
                {
                    "id": "neutral_portrait",
                    "kind": "portrait",
                    "representation": "2d",
                    "required": True,
                    "status": "processed",
                    "specification": _reference(authoring, spec_path),
                    "production_receipts": [_reference(authoring, receipt_path)],
                    "selected_candidates": [
                        {
                            "file": candidate_path.relative_to(authoring).as_posix(),
                            "sha256": sha256_file(candidate_path),
                            "approved_by": "lead",
                        }
                    ],
                    "processing_receipt": _reference(
                        authoring,
                        processing_receipt_path,
                    ),
                    "license": _reference(authoring, license_path),
                    "qa": _reference(authoring, qa_path),
                    "outputs": [
                        {
                            "role": "texture",
                            "runtime_file": processed_path.relative_to(authoring).as_posix(),
                            "sha256": sha256_file(processed_path),
                            "size": processed_path.stat().st_size,
                            "media_type": "image/png",
                        }
                    ],
                }
            ],
            "bindings": [
                {
                    "slot": "portrait:neutral",
                    "asset_id": "neutral_portrait",
                    "representation": "2d",
                    "scale": 1,
                    "layer": 0,
                }
            ],
        },
    )
    return {
        "authoring": authoring,
        "manifest": manifest_path,
        "processed": processed_path,
    }


class M5RenderPackReleaseTests(unittest.TestCase):
    def test_manifest_rejects_non_png_or_duplicate_final_2d_outputs(self) -> None:
        mutations = {
            "non-png": lambda asset: asset["outputs"][0].__setitem__("media_type", "image/webp"),
            "duplicate": lambda asset: asset["outputs"].append(dict(asset["outputs"][0])),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                mutate(manifest["assets"][0])
                manifest["content_hash"] = canonical_payload_hash(manifest)
                fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

                issues = validate_asset_manifest(
                    fixture["manifest"],
                    profile="build",
                    worldpack_path=WORLDPACK,
                )

                self.assertTrue(
                    any("do not match specification outputs" in str(issue) for issue in issues),
                    issues,
                )

    def test_manifest_rejects_processing_input_path_substitution_with_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            authoring = fixture["authoring"]
            selected = authoring / "generated/neutral_portrait.png"
            alias = authoring / "generated/neutral_portrait_alias.png"
            alias.write_bytes(selected.read_bytes())
            receipt_path = authoring / "processed/neutral_portrait/processing.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["inputs"][0]["artifact"]["file"] = alias.relative_to(authoring).as_posix()
            receipt["content_hash"] = canonical_payload_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["assets"][0]["processing_receipt"] = _reference(
                authoring,
                receipt_path,
            )
            manifest["content_hash"] = canonical_payload_hash(manifest)
            fixture["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

            issues = validate_asset_manifest(
                fixture["manifest"],
                profile="build",
                worldpack_path=WORLDPACK,
            )

            self.assertTrue(
                any("consume every approved candidate" in str(issue) for issue in issues),
                issues,
            )

    def test_v3_build_finalize_and_release_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            output = fixture["authoring"] / "release/renderpack.json"
            self.assertEqual(
                [],
                validate_asset_manifest(
                    fixture["manifest"],
                    profile="build",
                    worldpack_path=WORLDPACK,
                ),
            )

            built = build_renderpack(fixture["manifest"], WORLDPACK, output)
            runtime_file = output.parent / built["assets"][0]["files"][0]["path"]
            output_before = output.read_bytes()
            runtime_before = runtime_file.read_bytes()
            with self.assertRaisesRegex(RenderPackBuildError, "Refusing to overwrite"):
                build_renderpack(fixture["manifest"], WORLDPACK, output)
            self.assertEqual(output_before, output.read_bytes())
            self.assertEqual(runtime_before, runtime_file.read_bytes())

            production = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            released = finalize_asset_release(
                fixture["manifest"],
                output,
                WORLDPACK,
                expected_manifest_hash=production["content_hash"],
            )

            self.assertEqual("release", released["phase"])
            self.assertEqual("release/renderpack.json", released["deliverable"]["file"])
            self.assertEqual(sha256_file(output), released["deliverable"]["sha256"])
            self.assertEqual(built["content_hash"], released["deliverable"]["content_hash"])
            self.assertEqual(
                [],
                validate_asset_manifest(
                    fixture["manifest"],
                    profile="release",
                    worldpack_path=WORLDPACK,
                ),
            )
            loaded = load_renderpack(output, load_worldpack(WORLDPACK))
            self.assertEqual("neutral_portrait", loaded.assets[0].id)
            self.assertEqual("portrait:neutral", loaded.bindings[0].slot)

    def test_finalize_cas_preserves_manifest_changed_during_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = fixture["manifest"]
            output = fixture["authoring"] / "release/renderpack.json"
            build_renderpack(manifest, WORLDPACK, output)
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
                    manifest.write_bytes(canonical_json_bytes(concurrent))
                original_write(path, value, **kwargs)  # type: ignore[arg-type]

            with (
                patch.object(
                    asset_manifest_module,
                    "write_json_atomic",
                    side_effect=change_manifest_before_publish,
                ),
                self.assertRaisesRegex(AssetContractError, "Content changed before publishing"),
            ):
                finalize_asset_release(
                    manifest,
                    output,
                    WORLDPACK,
                    expected_manifest_hash=initial["content_hash"],
                )

            self.assertEqual(
                concurrent,
                json.loads(manifest.read_text(encoding="utf-8")),
            )

    def test_builder_refuses_existing_deliverable_or_runtime_tree(self) -> None:
        for collision in ("deliverable", "runtime"):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                output = fixture["authoring"] / "release/renderpack.json"
                output.parent.mkdir(parents=True)
                if collision == "deliverable":
                    output.write_text("do not replace\n", encoding="utf-8")
                    protected = output
                else:
                    protected = output.parent / "runtime-assets/sentinel.txt"
                    protected.parent.mkdir()
                    protected.write_text("do not replace\n", encoding="utf-8")

                with self.assertRaisesRegex(RenderPackBuildError, "Refusing to overwrite"):
                    build_renderpack(fixture["manifest"], WORLDPACK, output)

                self.assertEqual("do not replace\n", protected.read_text(encoding="utf-8"))
                if collision == "deliverable":
                    self.assertFalse((output.parent / "runtime-assets").exists())
                else:
                    self.assertFalse(output.exists())

    def test_publication_failure_removes_only_new_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            output = fixture["authoring"] / "release/renderpack.json"
            original_publish = renderpack_module._publish_new_file

            def fail_on_deliverable(
                source: Path,
                destination: Path,
                parent_identity: tuple[int, int],
            ) -> tuple[int, int]:
                if destination.resolve(strict=False) == output.resolve(strict=False):
                    raise OSError("injected deliverable publication failure")
                return original_publish(source, destination, parent_identity)

            with (
                patch.object(
                    renderpack_module,
                    "_publish_new_file",
                    side_effect=fail_on_deliverable,
                ),
                self.assertRaisesRegex(RenderPackBuildError, "Could not publish renderpack"),
            ):
                build_renderpack(fixture["manifest"], WORLDPACK, output)

            self.assertFalse(output.exists())
            self.assertFalse((output.parent / "runtime-assets").exists())
            self.assertEqual([], list(output.parent.glob(".renderpack.json.stage-*")))

    def test_builder_rejects_asset_parent_replaced_before_file_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = fixture["authoring"] / "release/renderpack.json"
            outside = root / "outside"
            outside.mkdir()
            moved = root / "moved-runtime-asset"
            original_publish = renderpack_module._publish_new_file
            replaced = False

            def replace_parent_then_publish(
                source: Path,
                destination: Path,
                parent_identity: tuple[int, int],
            ) -> tuple[int, int]:
                nonlocal replaced
                if not replaced and "runtime-assets" in destination.parts:
                    destination.parent.rename(moved)
                    destination.parent.symlink_to(outside, target_is_directory=True)
                    replaced = True
                return original_publish(source, destination, parent_identity)

            with (
                patch.object(
                    renderpack_module,
                    "_publish_new_file",
                    side_effect=replace_parent_then_publish,
                ),
                self.assertRaisesRegex(RenderPackBuildError, "safe directory|changed"),
            ):
                build_renderpack(fixture["manifest"], WORLDPACK, output)

            self.assertTrue(replaced)
            self.assertEqual([], list(outside.iterdir()))
            self.assertEqual([], list(moved.iterdir()))
            self.assertFalse(output.exists())

    def test_final_validation_failure_removes_published_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            output = fixture["authoring"] / "release/renderpack.json"
            original_load = renderpack_module.load_renderpack
            calls = 0

            def fail_final_load(path: Path, worldpack: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RenderPackError("injected final validation failure")
                return original_load(path, worldpack)

            with (
                patch.object(
                    renderpack_module,
                    "load_renderpack",
                    side_effect=fail_final_load,
                ),
                self.assertRaisesRegex(RenderPackBuildError, "Published renderpack failed"),
            ):
                build_renderpack(fixture["manifest"], WORLDPACK, output)

            self.assertEqual(2, calls)
            self.assertFalse(output.exists())
            self.assertFalse((output.parent / "runtime-assets").exists())
            self.assertEqual([], list(output.parent.glob(".renderpack.json.stage-*")))


if __name__ == "__main__":
    unittest.main()
