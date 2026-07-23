from __future__ import annotations

import copy
import hashlib
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.assetpack as assetpack_module
from worldforge.asset_contracts import (
    ASSET_RUNTIME_OUTPUT_CONTRACTS,
    runtime_output_contract_issue,
)
from worldforge.asset_formats.gltf import (
    BIN_CHUNK_TYPE,
    JSON_CHUNK_TYPE,
    GLBError,
    inspect_glb,
)
from worldforge.asset_io import AssetContractError, artifact_reference, bind_content_hash
from worldforge.asset_manifest_v3 import finalize_asset_release
from worldforge.asset_processing import process_asset_recipe
from worldforge.asset_production import create_production_request
from worldforge.assetpack import (
    AssetPackError,
    _validate_runtime_json,
    build_assetpack,
    verify_assetpack,
)
from worldforge.assets import validate_asset_manifest
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(document: object) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return encoded + b" " * (-len(encoded) % 4)


def _glb_bytes(
    document: dict[str, object],
    *,
    binary: bytes | None = None,
    raw_json: bytes | None = None,
) -> bytes:
    json_chunk = raw_json if raw_json is not None else _json_bytes(document)
    json_chunk += b" " * (-len(json_chunk) % 4)
    chunks = struct.pack("<II", len(json_chunk), JSON_CHUNK_TYPE) + json_chunk
    if binary is not None:
        binary += b"\x00" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(binary), BIN_CHUNK_TYPE) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def _document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "asset": {"version": "2.0", "generator": "neutral-test"},
        "accessors": [
            {"componentType": 5126, "count": 3, "type": "VEC3"},
            {"componentType": 5123, "count": 3, "type": "SCALAR"},
            {"componentType": 5126, "count": 1, "type": "SCALAR"},
            {"componentType": 5126, "count": 1, "type": "VEC3"},
        ],
        "nodes": [{"mesh": 0, "name": "Root", "skin": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "mode": 4,
                    }
                ]
            }
        ],
        "materials": [
            {
                "extensions": {
                    "KHR_materials_unlit": {},
                }
            }
        ],
        "textures": [{}],
        "skins": [{"joints": [0]}],
        "animations": [
            {
                "channels": [
                    {
                        "sampler": 0,
                        "target": {"node": 0, "path": "translation"},
                    }
                ],
                "name": "idle",
                "samplers": [{"input": 2, "output": 3}],
            }
        ],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "extensionsUsed": ["KHR_materials_unlit"],
        "extensionsRequired": ["KHR_materials_unlit"],
    }
    document.update(updates)
    return document


def _write_hashed_json(path: Path, value: dict[str, object]) -> dict[str, object]:
    value = bind_content_hash(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    return value


def _reference(root: Path, path: Path) -> dict[str, object]:
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _fixture(
    root: Path,
    *,
    document: dict[str, object] | None = None,
    binary: bytes | None = None,
    budget_overrides: dict[str, int] | None = None,
    processing_budget_overrides: dict[str, int] | None = None,
    required_animations: list[str] | None = None,
) -> dict[str, Path]:
    authoring = root / "authoring"
    authoring.mkdir(parents=True)
    worldpack_path = root / "neutral.worldpack.json"
    worldpack_source = json.loads(
        (ROOT / "content/compiled/foundation.worldpack.json").read_text(encoding="utf-8")
    )
    worldpack_source.pop("content_hash", None)
    worldpack_source["world"]["id"] = "neutral_world"
    worldpack_source["world"]["title"] = "Neutral"
    worldpack = _write_hashed_json(worldpack_path, worldpack_source)
    coordinates = {
        "handedness": "right",
        "up_axis": "Y",
        "forward_axis": "-Z",
        "units_per_meter": 1.0,
    }
    target_path = authoring / "contracts/target.json"
    target = _write_hashed_json(
        target_path,
        {
            "format": "rpg-world-forge.asset_target",
            "format_version": 1,
            "id": "neutral_3d",
            "world_id": "neutral_world",
            "world_content_hash": worldpack["content_hash"],
            "dimension": "3d",
            "delivery_profile": "assetpack_v1",
            "runtime_adapter": None,
            "coordinate_system": coordinates,
        },
    )
    common_bible: dict[str, object] = {
        "format_version": 1,
        "world_id": "neutral_world",
        "world_content_hash": worldpack["content_hash"],
        "target_id": "neutral_3d",
        "target_hash": target["content_hash"],
        "acceptance_tests": ["loads"],
        "approved_by": "lead",
    }
    visual_path = authoring / "bibles/visual.json"
    visual = _write_hashed_json(
        visual_path,
        {
            **common_bible,
            "format": "rpg-world-forge.visual_bible",
            "camera": {"reference": "orthographic"},
            "resolution": {"texture_max": 2048},
            "style": {"family": "neutral"},
            "silhouettes": {"readable": True},
            "animation": {"clips": ["idle"]},
            "ui": {"mode": "2d_overlay"},
            "vfx": {"photosensitivity": "safe"},
        },
    )
    audio_path = authoring / "bibles/audio.json"
    audio = _write_hashed_json(
        audio_path,
        {
            **common_bible,
            "format": "rpg-world-forge.audio_bible",
            "format_policy": {"runtime": "wav"},
            "mix": {"peak_dbfs": -1},
            "timbral_families": ["neutral"],
            "ambience": ["quiet"],
            "music": ["none"],
            "sfx": ["none"],
        },
    )
    inventory_path = authoring / "inventory.json"
    inventory = _write_hashed_json(
        inventory_path,
        {
            "format": "rpg-world-forge.asset_inventory",
            "format_version": 1,
            "world_id": "neutral_world",
            "world_content_hash": worldpack["content_hash"],
            "target_id": "neutral_3d",
            "target_hash": target["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "requirements": [
                {
                    "id": "neutral_actor",
                    "kind": "character_3d",
                    "representation": "3d",
                    "required": True,
                    "purpose": "Neutral runtime model",
                    "canonical_sources": ["world:neutral"],
                    "semantic_slots": ["actor:neutral"],
                }
            ],
            "manual_additions": [],
        },
    )
    spec_path = authoring / "specs/neutral_actor.json"
    budgets = {
        "max_vertices": 10,
        "max_triangles": 10,
        "max_materials": 2,
        "max_texture_size": 2048,
        **(budget_overrides or {}),
    }
    technical: dict[str, object] = {
        "runtime_format": "glb",
        "memory_budget_bytes": 1_048_576,
        "physical_dimensions_m": [1, 1, 1],
        "budgets": budgets,
    }
    if required_animations is not None:
        technical["required_animations"] = required_animations
    spec = _write_hashed_json(
        spec_path,
        {
            "format": "rpg-world-forge.asset_spec",
            "format_version": 2,
            "id": "neutral_actor",
            "kind": "character_3d",
            "representation": "3d",
            "target_id": "neutral_3d",
            "target_hash": target["content_hash"],
            "inventory_hash": inventory["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "purpose": "Neutral runtime model",
            "canonical_sources": ["world:neutral"],
            "acceptance_criteria": ["loads"],
            "technical": technical,
            "semantic_slots": ["actor:neutral"],
            "production": {
                "allowed_routes": ["openai"],
                "allowed_executors": ["blender_mcp", "openai_image"],
            },
            "expected_outputs": [{"role": "model", "media_type": "model/gltf-binary"}],
        },
    )
    reference_request_path = authoring / "requests/neutral_actor_reference.json"
    create_production_request(
        authoring,
        spec_path.relative_to(authoring).as_posix(),
        reference_request_path,
        request_id="neutral_actor_reference",
        route="openai",
        executor="openai_image",
        operation="concept_reference",
        parameters={"model": "gpt-image-1", "size": "1024x1024"},
        expected_outputs=[{"role": "preview", "media_type": "image/png"}],
    )
    reference_candidate_path = authoring / "generated/neutral_actor_reference.png"
    reference_candidate_path.parent.mkdir(parents=True)
    try:
        from PIL import Image
    except ImportError as exc:
        raise unittest.SkipTest("Pillow is not installed") from exc
    Image.new("RGBA", (2, 2), (20, 40, 60, 255)).save(
        reference_candidate_path,
        format="PNG",
    )
    reference_receipt_path = authoring / "receipts/neutral_actor_reference.json"
    reference_receipt = _write_hashed_json(
        reference_receipt_path,
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": "receipt_neutral_actor_reference",
            "request": _reference(authoring, reference_request_path),
            "asset_id": "neutral_actor",
            "route": "openai",
            "executor": "openai_image",
            "operation": "concept_reference",
            "status": "succeeded",
            "started_at": "2026-07-20T09:55:00Z",
            "completed_at": "2026-07-20T09:56:00Z",
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
                    "role": "preview",
                    **_reference(authoring, reference_candidate_path),
                    "media_type": "image/png",
                }
            ],
        },
    )
    model_script_path = authoring / "recipes/blender/model.py"
    model_script_path.parent.mkdir(parents=True, exist_ok=True)
    model_script_path.write_text("# reviewed model fixture\n", encoding="utf-8")
    model_request_path = authoring / "requests/neutral_actor_model.json"
    create_production_request(
        authoring,
        spec_path.relative_to(authoring).as_posix(),
        model_request_path,
        request_id="neutral_actor_model",
        route="openai",
        executor="blender_mcp",
        operation="model_from_reference",
        inputs=[("reference", "generated/neutral_actor_reference.png")],
        parent_receipt_hashes=[reference_receipt["content_hash"]],
        reviewed_script_file=model_script_path.relative_to(authoring).as_posix(),
    )
    model_request = json.loads(model_request_path.read_text(encoding="utf-8"))
    model_candidate_path = authoring / "generated/neutral_actor_model.glb"
    model_candidate_path.write_bytes(_glb_bytes(_document()))
    model_receipt_path = authoring / "receipts/neutral_actor_model.json"
    model_receipt = _write_hashed_json(
        model_receipt_path,
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": "receipt_neutral_actor_model",
            "request": _reference(authoring, model_request_path),
            "asset_id": "neutral_actor",
            "route": "openai",
            "executor": "blender_mcp",
            "operation": "model_from_reference",
            "status": "succeeded",
            "started_at": "2026-07-20T09:57:00Z",
            "completed_at": "2026-07-20T09:59:00Z",
            "parent_receipt_hashes": [reference_receipt["content_hash"]],
            "toolchain": {
                "blender_version": "4.5.0",
                "blender_mcp_version": "1.6.4",
                "addon_revision": "6641189231caf3752302ae20591bc87fda85fc4e",
                "telemetry_disabled": True,
            },
            "replayability": "traceable_not_bit_reproducible",
            "reviewed_script": model_request["reviewed_script"],
            "approval_mode": "explicit",
            "outputs": [
                {
                    "role": "model",
                    **_reference(authoring, model_candidate_path),
                    "media_type": "model/gltf-binary",
                }
            ],
        },
    )
    script_path = authoring / "recipes/blender/export.py"
    script_path.write_text("# reviewed export fixture\n", encoding="utf-8")
    request_path = authoring / "requests/neutral_actor.json"
    create_production_request(
        authoring,
        spec_path.relative_to(authoring).as_posix(),
        request_path,
        request_id="neutral_actor_export",
        route="openai",
        executor="blender_mcp",
        operation="export_glb",
        inputs=[("model", "generated/neutral_actor_model.glb")],
        parameters={"export": "embedded_glb"},
        parent_receipt_hashes=[model_receipt["content_hash"]],
        reviewed_script_file=script_path.relative_to(authoring).as_posix(),
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))
    candidate_path = authoring / "generated/neutral_actor.glb"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(_glb_bytes(document or _document(), binary=binary))
    production_receipt_path = authoring / "receipts/neutral_actor.json"
    _write_hashed_json(
        production_receipt_path,
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": "receipt_neutral_actor",
            "request": _reference(authoring, request_path),
            "asset_id": "neutral_actor",
            "route": "openai",
            "executor": "blender_mcp",
            "operation": "export_glb",
            "status": "succeeded",
            "started_at": "2026-07-20T10:00:00Z",
            "completed_at": "2026-07-20T10:01:00Z",
            "parent_receipt_hashes": [model_receipt["content_hash"]],
            "toolchain": {
                "blender_version": "4.5.0",
                "blender_mcp_version": "1.6.4",
                "addon_revision": "6641189231caf3752302ae20591bc87fda85fc4e",
                "telemetry_disabled": True,
            },
            "replayability": "traceable_not_bit_reproducible",
            "reviewed_script": request["reviewed_script"],
            "approval_mode": "explicit",
            "outputs": [
                {
                    "role": "model",
                    **_reference(authoring, candidate_path),
                    "media_type": "model/gltf-binary",
                }
            ],
        },
    )
    # Processing recipes resolve every input relative to their own directory and
    # deliberately reject parent traversal. Keep this fixture recipe at the
    # authoring root so the generated candidate remains inside that trust root.
    recipe_path = authoring / "neutral_actor.glb.recipe.json"
    _write_hashed_json(
        recipe_path,
        {
            "format": "rpg-world-forge.asset_processing_recipe",
            "format_version": 1,
            "operation": "glb_validate",
            "input": artifact_reference(authoring, "generated/neutral_actor.glb"),
            "output": {"file": "neutral_actor.glb", "role": "model"},
            "options": {
                "budgets": {
                    **spec["technical"]["budgets"],
                    **(processing_budget_overrides or {}),
                },
                "max_bytes": spec["technical"]["memory_budget_bytes"],
            },
        },
    )
    processed_directory = authoring / "processed/neutral_actor"
    processing_receipt = process_asset_recipe(
        recipe_path,
        processed_directory,
        asset_root=authoring,
    )
    processing_receipt_path = processed_directory / "processing.receipt.json"
    model_path = processed_directory / "neutral_actor.glb"
    license_evidence = authoring / "evidence/license.txt"
    license_evidence.parent.mkdir(parents=True, exist_ok=True)
    license_evidence.write_text("Neutral fixture license evidence.\n", encoding="utf-8")
    notices_path = authoring / "evidence/NOTICE.txt"
    notices_path.write_text("CC0-1.0 neutral fixture.\n", encoding="utf-8")
    output_hash = processing_receipt["outputs"][0]["artifact"]["sha256"]
    license_path = authoring / "licenses/neutral_actor.json"
    _write_hashed_json(
        license_path,
        {
            "format": "rpg-world-forge.asset_license_record",
            "format_version": 1,
            "asset_id": "neutral_actor",
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
            "notices": _reference(authoring, notices_path),
            "approved_by": "lead",
        },
    )
    qa_evidence = authoring / "evidence/qa.txt"
    qa_evidence.write_text("GLB inspection and load check passed.\n", encoding="utf-8")
    qa_path = authoring / "qa/neutral_actor.json"
    _write_hashed_json(
        qa_path,
        {
            "format": "rpg-world-forge.asset_qa_report",
            "format_version": 1,
            "asset_id": "neutral_actor",
            "target_hash": target["content_hash"],
            "output_hashes": [output_hash],
            "checks": [
                {"id": "loads", "passed": True, "evidence": [_reference(authoring, qa_evidence)]}
            ],
            "blockers": [],
            "approved_by": "lead",
        },
    )
    manifest_path = authoring / "manifest.json"
    manifest = _write_hashed_json(
        manifest_path,
        {
            "format": "rpg-world-forge.asset_manifest",
            "format_version": 3,
            "world_id": "neutral_world",
            "world_content_hash": worldpack["content_hash"],
            "target": _reference(authoring, target_path),
            "phase": "production",
            "generation_policy": {
                "orchestrator": "gpt",
                "enabled_routes": ["openai"],
                "local_model_route": "modly",
                "executors": ["blender_mcp", "openai_image"],
            },
            "bibles": {
                "visual": _reference(authoring, visual_path),
                "audio": _reference(authoring, audio_path),
            },
            "inventory": _reference(authoring, inventory_path),
            "assets": [
                {
                    "id": "neutral_actor",
                    "kind": "character_3d",
                    "representation": "3d",
                    "required": True,
                    "status": "processed",
                    "specification": _reference(authoring, spec_path),
                    "production_receipts": [
                        _reference(authoring, reference_receipt_path),
                        _reference(authoring, model_receipt_path),
                        _reference(authoring, production_receipt_path),
                    ],
                    "processing_receipt": _reference(authoring, processing_receipt_path),
                    "selected_candidates": [
                        {
                            "file": candidate_path.relative_to(authoring).as_posix(),
                            "sha256": _sha256(candidate_path),
                            "approved_by": "lead",
                        }
                    ],
                    "license": _reference(authoring, license_path),
                    "qa": _reference(authoring, qa_path),
                    "outputs": [
                        {
                            "role": "model",
                            "runtime_file": "processed/neutral_actor/neutral_actor.glb",
                            "sha256": _sha256(model_path),
                            "size": model_path.stat().st_size,
                            "media_type": "model/gltf-binary",
                        }
                    ],
                }
            ],
            "bindings": [
                {
                    "slot": "actor:neutral",
                    "asset_id": "neutral_actor",
                    "representation": "3d",
                    "presentation": {
                        "node": "Root",
                        "default_animation": "idle",
                        "scale": 1,
                        "layer": 0,
                    },
                }
            ],
        },
    )
    assert manifest["content_hash"] == canonical_payload_hash(manifest)
    return {
        "authoring": authoring,
        "worldpack": worldpack_path,
        "target": target_path,
        "spec": spec_path,
        "model": model_path,
        "manifest": manifest_path,
    }


def _rewrite_hashed(path: Path, value: dict[str, object]) -> None:
    value["content_hash"] = canonical_payload_hash(value)
    path.write_bytes(canonical_json_bytes(value))


class GLBInspectorTests(unittest.TestCase):
    def test_inspects_v2_chunks_extensions_and_geometry_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.glb"
            path.write_bytes(_glb_bytes(_document()))

            result = inspect_glb(
                path,
                required_node_names={"Root"},
                required_animation_names={"idle"},
            )

            self.assertEqual(0, result["bin_chunk_bytes"])
            self.assertEqual(["KHR_materials_unlit"], result["extensions_required"])
            self.assertEqual([], result["external_uris"])
            self.assertEqual(
                {
                    "nodes": 1,
                    "meshes": 1,
                    "materials": 1,
                    "textures": 1,
                    "skins": 1,
                    "bones": 1,
                    "influences": 0,
                    "animations": 1,
                    "vertices": 3,
                    "triangles": 1,
                    "external_uris": 0,
                },
                result["metrics"],
            )

    def test_rejects_invalid_header_length_version_and_chunk_layout(self) -> None:
        valid = _glb_bytes(_document())
        cases: dict[str, bytes] = {
            "magic": b"nope" + valid[4:],
            "version": valid[:4] + struct.pack("<I", 1) + valid[8:],
            "length": valid[:8] + struct.pack("<I", len(valid) + 4) + valid[12:],
            "first chunk": valid[:16] + struct.pack("<I", BIN_CHUNK_TYPE) + valid[20:],
            "truncated": valid[:-1],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.glb"
            for label, data in cases.items():
                with self.subTest(label=label):
                    path.write_bytes(data)
                    with self.assertRaises(GLBError):
                        inspect_glb(path)

    def test_rejects_duplicate_json_keys_and_unknown_chunks(self) -> None:
        raw_json = b'{"asset":{"version":"2.0"},"nodes":[],"nodes":[]}'
        duplicate = _glb_bytes({}, binary=None, raw_json=raw_json)
        non_finite = _glb_bytes(
            {},
            binary=None,
            raw_json=b'{"asset":{"version":"2.0"},"extras":{"value":1e999}}',
        )
        document = {"asset": {"version": "2.0"}}
        base = _glb_bytes(document, binary=None)
        unknown_chunk = struct.pack("<II", 4, 0x12345678) + b"test"
        unknown = base[:8] + struct.pack("<I", len(base) + len(unknown_chunk)) + base[12:]
        unknown += unknown_chunk
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.glb"
            for data in (duplicate, non_finite, unknown):
                path.write_bytes(data)
                with self.assertRaises(GLBError):
                    inspect_glb(path)

    def test_rejects_authoring_provider_and_secret_metadata(self) -> None:
        secret = _document(extras={"api_key": "sk-fixture-not-a-real-secret"})
        authoring = _document(asset={"version": "2.0", "generator": "Blender 4.5 authoring export"})
        credential = _document(nodes=[{"mesh": 0, "name": "Bearer abcdefghijklmnop"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.glb"
            for expected, document in (
                ("forbidden", secret),
                ("authoring/provider", authoring),
                ("credential-like", credential),
            ):
                with self.subTest(expected=expected):
                    path.write_bytes(_glb_bytes(document))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path)

    def test_rejects_unreferenced_and_hidden_bin_payloads(self) -> None:
        unreferenced_bin = _document(buffers=[{"byteLength": 4}])
        unreferenced_view = _document(
            buffers=[{"byteLength": 4}],
            bufferViews=[{"buffer": 0, "byteLength": 4}],
        )
        hidden_prefix = _document(
            buffers=[{"byteLength": 16}],
            bufferViews=[{"buffer": 0, "byteLength": 12, "byteOffset": 4}],
        )
        hidden_prefix["accessors"][0]["bufferView"] = 0
        hidden_prefix["accessors"][0]["count"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hidden.glb"
            for expected, document, binary in (
                ("BIN chunk is not referenced", unreferenced_bin, b"LEAK"),
                ("unreferenced bufferViews", unreferenced_view, b"\0" * 4),
                ("unreferenced non-zero bytes", hidden_prefix, b"LEAK" + b"\0" * 12),
            ):
                with self.subTest(expected=expected):
                    path.write_bytes(_glb_bytes(document, binary=binary))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path)

    def test_external_uris_are_reported_but_forbidden_by_default(self) -> None:
        document = _document(images=[{"uri": "textures/albedo.png"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external.glb"
            path.write_bytes(_glb_bytes(document))
            with self.assertRaisesRegex(GLBError, "external URIs"):
                inspect_glb(path)
            result = inspect_glb(path, allow_external_uris=True)
            self.assertEqual(["textures/albedo.png"], result["external_uris"])
            self.assertEqual(1, result["metrics"]["external_uris"])

    def test_embedded_data_uris_are_limited_to_safe_media_and_valid_base64(self) -> None:
        safe = _document(images=[{"uri": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"}])
        unsafe = _document(images=[{"uri": "data:image/svg+xml;base64,PHN2Zy8+"}])
        malformed = _document(images=[{"uri": "data:image/png;base64,%%%"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data-uri.glb"
            path.write_bytes(_glb_bytes(safe))
            self.assertEqual(1, inspect_glb(path)["embedded_uris"])
            for expected, document in (
                ("not runtime-safe", unsafe),
                ("not valid base64", malformed),
            ):
                with self.subTest(expected=expected):
                    path.write_bytes(_glb_bytes(document))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path)

    def test_rejects_disallowed_undeclared_and_inconsistent_extensions(self) -> None:
        disallowed = _document(
            extensionsUsed=["EXT_mesh_gpu_instancing"],
            extensionsRequired=["EXT_mesh_gpu_instancing"],
            materials=[],
        )
        undeclared = _document(extensionsUsed=[], extensionsRequired=[])
        required_only = _document(extensionsUsed=[], extensionsRequired=["KHR_materials_unlit"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extensions.glb"
            for expected, document in (
                ("disallowed", disallowed),
                ("not declared", undeclared),
                ("subset", required_only),
            ):
                with self.subTest(expected=expected):
                    path.write_bytes(_glb_bytes(document))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path)

    def test_enforces_structural_budgets_and_accessor_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.glb"
            path.write_bytes(_glb_bytes(_document()))
            for budget in (
                {"max_vertices": 2},
                {"max_triangles": 0},
                {"materials": 0},
                {"textures": 0},
                {"bones": 0},
            ):
                with self.subTest(budget=budget):
                    with self.assertRaisesRegex(GLBError, "budget exceeded"):
                        inspect_glb(path, budgets=budget)

            invalid = _document(meshes=[{"primitives": [{"attributes": {"POSITION": 99}}]}])
            path.write_bytes(_glb_bytes(invalid))
            with self.assertRaisesRegex(GLBError, "invalid accessor"):
                inspect_glb(path)

    def test_skin_attributes_require_valid_joint_and_weight_component_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skin-attributes.glb"
            document = _document()
            document["accessors"].extend(
                [
                    {"componentType": 5126, "count": 3, "type": "VEC4"},
                    {"componentType": 5126, "count": 3, "type": "VEC4"},
                ]
            )
            document["meshes"][0]["primitives"][0]["attributes"].update(
                {"JOINTS_0": 4, "WEIGHTS_0": 5}
            )
            path.write_bytes(_glb_bytes(document))
            with self.assertRaisesRegex(GLBError, "JOINTS_0 must use"):
                inspect_glb(path)

            document["accessors"][4] = {
                "componentType": 5121,
                "count": 3,
                "type": "VEC4",
            }
            document["accessors"][5] = {
                "componentType": 5121,
                "count": 3,
                "type": "VEC4",
            }
            path.write_bytes(_glb_bytes(document))
            with self.assertRaisesRegex(GLBError, "WEIGHTS_0 must use"):
                inspect_glb(path)

            document["accessors"][5]["normalized"] = True
            path.write_bytes(_glb_bytes(document))
            self.assertEqual(4, inspect_glb(path)["metrics"]["influences"])
            with self.assertRaisesRegex(GLBError, "influences budget exceeded"):
                inspect_glb(path, budgets={"max_influences": 3})

    def test_rejects_incomplete_accessors_and_invalid_buffer_view_ranges(self) -> None:
        malformed_accessors = (
            ({"count": 3, "type": "VEC3"}, "componentType"),
            ({"componentType": [], "count": 3, "type": "VEC3"}, "componentType"),
            ({"componentType": 5126, "count": 3}, "type"),
            ({"componentType": 5126, "count": 0, "type": "VEC3"}, "count"),
            (
                {
                    "byteOffset": 4,
                    "componentType": 5126,
                    "count": 3,
                    "type": "VEC3",
                },
                "requires a bufferView",
            ),
            (
                {
                    "bufferView": 99,
                    "componentType": 5126,
                    "count": 3,
                    "type": "VEC3",
                },
                "invalid index",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accessor.glb"
            for accessor, expected in malformed_accessors:
                with self.subTest(expected=expected):
                    document = _document()
                    document["accessors"] = [accessor, *document["accessors"][1:]]
                    path.write_bytes(_glb_bytes(document))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path)

            document = _document(
                buffers=[{"byteLength": 12}],
                bufferViews=[{"buffer": 0, "byteLength": 8}],
            )
            document["accessors"][0]["bufferView"] = 0
            document["accessors"][0]["count"] = 1
            path.write_bytes(_glb_bytes(document, binary=b"\0" * 12))
            with self.assertRaisesRegex(GLBError, "range exceeds its bufferView"):
                inspect_glb(path)

            document = _document(
                buffers=[{"byteLength": 12}],
                bufferViews=[{"buffer": 0, "byteLength": 10, "byteOffset": 2}],
            )
            document["accessors"][0]["bufferView"] = 0
            document["accessors"][0]["count"] = 1
            path.write_bytes(_glb_bytes(document, binary=b"\0" * 12))
            with self.assertRaisesRegex(GLBError, "not component-aligned"):
                inspect_glb(path)

    def test_rejects_dangling_hierarchy_animation_and_texture_references(self) -> None:
        cases = (
            (_document(nodes=[{"children": [99], "name": "Root"}]), "invalid index"),
            (_document(scene=3), "invalid index"),
            (
                _document(
                    nodes=[
                        {"children": [1], "name": "First"},
                        {"children": [0], "name": "Second"},
                    ]
                ),
                "hierarchy contains a cycle",
            ),
            (
                _document(
                    animations=[
                        {
                            "channels": [
                                {
                                    "sampler": 0,
                                    "target": {"node": 0, "path": "translation"},
                                }
                            ],
                            "name": "idle",
                            "samplers": [{"input": 2, "output": 99}],
                        }
                    ]
                ),
                "invalid accessor",
            ),
            (_document(images=[{"uri": "albedo.png"}], textures=[{"source": 7}]), "invalid index"),
            (
                _document(
                    extensions={"KHR_lights_punctual": {"lights": [{"type": "point"}]}},
                    extensionsUsed=["KHR_materials_unlit", "KHR_lights_punctual"],
                    nodes=[
                        {
                            "extensions": {"KHR_lights_punctual": {"light": 7}},
                            "mesh": 0,
                            "name": "Root",
                            "skin": 0,
                        }
                    ],
                ),
                "invalid index",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "references.glb"
            for document, expected in cases:
                with self.subTest(expected=expected):
                    path.write_bytes(_glb_bytes(document))
                    with self.assertRaisesRegex(GLBError, expected):
                        inspect_glb(path, allow_external_uris=True)

    def test_required_runtime_names_must_exist_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "names.glb"
            path.write_bytes(_glb_bytes(_document()))
            with self.assertRaisesRegex(GLBError, "missing required nodes names"):
                inspect_glb(path, required_node_names={"Missing"})
            with self.assertRaisesRegex(GLBError, "missing required animations names"):
                inspect_glb(path, required_animation_names={"walk"})

            duplicate = _document(
                nodes=[
                    {"mesh": 0, "name": "Root", "skin": 0},
                    {"name": "Root"},
                ]
            )
            path.write_bytes(_glb_bytes(duplicate))
            with self.assertRaisesRegex(GLBError, "ambiguous required nodes names"):
                inspect_glb(path, required_node_names={"Root"})
            with self.assertRaisesRegex(GLBError, "must be a collection"):
                inspect_glb(path, required_node_names="Root")


class AssetPackTests(unittest.TestCase):
    def test_manifest_receipt_lineage_never_discovers_filesystem_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))

            with (
                patch.object(
                    Path,
                    "rglob",
                    side_effect=AssertionError("receipt lineage must not call Path.rglob"),
                ),
                patch.object(
                    Path,
                    "glob",
                    side_effect=AssertionError("receipt lineage must not call Path.glob"),
                ),
                patch(
                    "os.scandir",
                    side_effect=AssertionError("receipt lineage must not call os.scandir"),
                ),
            ):
                issues = validate_asset_manifest(
                    fixture["manifest"],
                    profile="build",
                    worldpack_path=fixture["worldpack"],
                )

            self.assertEqual([], issues)

    def test_manifest_receipt_lineage_rejects_an_unlisted_parent_without_discovery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            references = manifest["assets"][0]["production_receipts"]
            omitted_reference = references.pop(0)
            omitted_receipt = json.loads(
                (fixture["authoring"] / omitted_reference["file"]).read_text(encoding="utf-8")
            )
            _rewrite_hashed(fixture["manifest"], manifest)

            with (
                patch.object(
                    Path,
                    "rglob",
                    side_effect=AssertionError("receipt lineage must not call Path.rglob"),
                ),
                patch.object(
                    Path,
                    "glob",
                    side_effect=AssertionError("receipt lineage must not call Path.glob"),
                ),
                patch(
                    "os.scandir",
                    side_effect=AssertionError("receipt lineage must not call os.scandir"),
                ),
            ):
                issues = validate_asset_manifest(
                    fixture["manifest"],
                    profile="build",
                    worldpack_path=fixture["worldpack"],
                )

            messages = [str(issue) for issue in issues]
            self.assertTrue(
                any(
                    f"cannot resolve parent receipt {omitted_receipt['content_hash']}" in message
                    for message in messages
                ),
                messages,
            )
            self.assertTrue(
                any(
                    f"unknown parent {omitted_receipt['content_hash']}" in message
                    for message in messages
                ),
                messages,
            )

    def test_manifest_rejects_duplicate_and_conflicting_receipt_authority(self) -> None:
        mutations = (
            "duplicate-reference",
            "conflicting-reference",
            "duplicate-content-hash",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                references = manifest["assets"][0]["production_receipts"]
                if mutation == "duplicate-reference":
                    references.append(copy.deepcopy(references[0]))
                    expected = "duplicate production receipt reference"
                elif mutation == "conflicting-reference":
                    conflicting = copy.deepcopy(references[0])
                    conflicting["size"] = (
                        (fixture["authoring"] / conflicting["file"]).stat().st_size
                    )
                    references.append(conflicting)
                    expected = "conflicting production receipt references for the same path"
                else:
                    source = fixture["authoring"] / references[0]["file"]
                    copy_path = fixture["authoring"] / "receipts/reference-copy.json"
                    copy_path.write_bytes(source.read_bytes())
                    references.append(_reference(fixture["authoring"], copy_path))
                    expected = (
                        "duplicate receipt content hash across conflicting production "
                        "receipt references"
                    )
                _rewrite_hashed(fixture["manifest"], manifest)

                issues = validate_asset_manifest(
                    fixture["manifest"],
                    profile="build",
                    worldpack_path=fixture["worldpack"],
                )

                messages = [str(issue) for issue in issues]
                self.assertTrue(
                    any(expected in message for message in messages),
                    messages,
                )

    def test_closed_receipt_index_rejects_authorized_path_rebinding(self) -> None:
        from worldforge.asset_production import ProductionReceiptIndex

        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            references = manifest["assets"][0]["production_receipts"]
            receipt_index, resolved, issues = ProductionReceiptIndex.from_manifest_references(
                fixture["authoring"],
                references,
            )
            self.assertEqual([], issues)
            self.assertEqual(3, len(resolved))

            first = resolved[0]
            first.path.write_bytes(first.path.read_bytes() + b"\n")

            with self.assertRaisesRegex(AssetContractError, "SHA-256 does not match"):
                receipt_index.read(first.content_hash)

    def test_publishers_reject_parent_replaced_after_identity_capture(self) -> None:
        for publisher in ("runtime", "json"):
            with self.subTest(publisher=publisher), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.bin"
                source.write_bytes(b"runtime fixture")
                parent = root / "publish"
                parent.mkdir()
                info = parent.lstat()
                identity = (info.st_dev, info.st_ino)
                moved = root / "publish-original"
                outside = root / "outside"
                outside.mkdir()
                parent.rename(moved)
                parent.symlink_to(outside, target_is_directory=True)

                with self.assertRaisesRegex(AssetPackError, "safe directory|changed"):
                    if publisher == "runtime":
                        assetpack_module._copy_exclusive(
                            source,
                            parent / "asset.bin",
                            identity,
                        )
                    else:
                        assetpack_module._publish_json_exclusive(
                            parent / "assetpack.json",
                            {"safe": True},
                            identity,
                        )

                self.assertEqual([], list(outside.iterdir()))
                self.assertEqual([], list(moved.iterdir()))

    def test_runtime_metadata_rejects_provider_and_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, payload in enumerate(
                (
                    {"engine": "openai"},
                    {"tool": "modly_cli_mcp"},
                    {"result": "sk-proj-abcdefghijklmnop"},
                )
            ):
                path = root / f"metadata-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(AssetPackError):
                    _validate_runtime_json(path, context="runtime metadata")

    def test_build_enforces_optional_bone_budget_from_the_specification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(
                Path(directory),
                budget_overrides={"max_bones": 0},
                processing_budget_overrides={"max_bones": 1},
            )
            output = fixture["authoring"] / "release/assetpack.json"

            with self.assertRaisesRegex(AssetPackError, "bones budget exceeded"):
                build_assetpack(fixture["manifest"], fixture["worldpack"], output)

    def test_build_enforces_every_required_animation_from_the_specification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(
                Path(directory),
                required_animations=["idle", "walk"],
            )
            output = fixture["authoring"] / "release/assetpack.json"

            with self.assertRaisesRegex(AssetPackError, "required animation 'walk'"):
                build_assetpack(fixture["manifest"], fixture["worldpack"], output)

    def test_manifest_malformed_container_values_return_issues(self) -> None:
        mutations = {
            "phase": lambda value: value.__setitem__("phase", []),
            "route": lambda value: value["generation_policy"].__setitem__("enabled_routes", [{}]),
            "executor": lambda value: value["generation_policy"].__setitem__("executors", [[]]),
            "kind": lambda value: value["assets"][0].__setitem__("kind", []),
            "representation": lambda value: value["assets"][0].__setitem__("representation", {}),
            "required": lambda value: value["assets"][0].__setitem__("required", []),
            "status": lambda value: value["assets"][0].__setitem__("status", []),
            "output role": lambda value: value["assets"][0]["outputs"][0].__setitem__("role", []),
            "output media": lambda value: value["assets"][0]["outputs"][0].__setitem__(
                "media_type", []
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                mutate(manifest)
                _rewrite_hashed(fixture["manifest"], manifest)
                self.assertTrue(
                    validate_asset_manifest(
                        fixture["manifest"],
                        profile="build",
                        worldpack_path=fixture["worldpack"],
                    )
                )

    def test_manifest_rejects_duplicate_3d_primary_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["assets"][0]["outputs"].append(
                copy.deepcopy(manifest["assets"][0]["outputs"][0])
            )
            _rewrite_hashed(fixture["manifest"], manifest)

            issues = validate_asset_manifest(
                fixture["manifest"],
                profile="build",
                worldpack_path=fixture["worldpack"],
            )

            self.assertTrue(
                any("runtime output roles must be unique" in str(issue) for issue in issues),
                issues,
            )

    def test_builder_rejects_unbound_qa_or_license_evidence(self) -> None:
        for field in ("qa", "license"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = _fixture(root)
                manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                manifest["assets"][0][field] = {
                    "file": f"evidence/{field}-does-not-exist.json",
                    "sha256": "0" * 64,
                }
                _rewrite_hashed(fixture["manifest"], manifest)

                with self.assertRaisesRegex(AssetPackError, field):
                    build_assetpack(
                        fixture["manifest"],
                        fixture["worldpack"],
                        root / "out/assetpack.json",
                    )

    def test_build_finalize_and_release_reverification_are_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = fixture["authoring"] / "release/assetpack.json"
            build_assetpack(fixture["manifest"], fixture["worldpack"], output)
            before = json.loads(fixture["manifest"].read_text(encoding="utf-8"))

            released = finalize_asset_release(
                fixture["manifest"],
                output,
                fixture["worldpack"],
                expected_manifest_hash=before["content_hash"],
            )

            self.assertEqual("release", released["phase"])
            self.assertEqual("release/assetpack.json", released["deliverable"]["file"])
            self.assertEqual(
                [],
                validate_asset_manifest(
                    fixture["manifest"],
                    profile="release",
                    worldpack_path=fixture["worldpack"],
                ),
            )
            packaged = output.parent / verify_assetpack(output)["assets"][0]["files"][0]["path"]
            packaged.write_bytes(packaged.read_bytes()[:-4] + b"bad!")
            issues = validate_asset_manifest(
                fixture["manifest"],
                profile="release",
                worldpack_path=fixture["worldpack"],
            )
            self.assertTrue(any("runtime verification failed" in str(issue) for issue in issues))

    def test_builds_and_verifies_runtime_only_assetpack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"

            payload = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            self.assertEqual(
                {
                    "format",
                    "format_version",
                    "world_id",
                    "world_content_hash",
                    "target_id",
                    "target_hash",
                    "dimension",
                    "delivery_profile",
                    "coordinate_system",
                    "assets",
                    "bindings",
                    "content_hash",
                },
                set(payload),
            )
            serialized = json.dumps(payload).casefold()
            for authoring_term in (
                ".blend",
                "evidence",
                "license",
                "provider",
                "receipt",
                "blender_mcp",
            ):
                self.assertNotIn(authoring_term, serialized)
            model = payload["assets"][0]["files"][0]
            self.assertEqual("model", model["role"])
            self.assertEqual("model/gltf-binary", model["media_type"])
            self.assertEqual(_sha256(output.parent / model["path"]), model["sha256"])
            self.assertEqual(1, payload["assets"][0]["metrics"]["triangles"])
            self.assertEqual("Root", payload["bindings"][0]["entrypoint"]["node"])
            self.assertEqual(payload, verify_assetpack(output, fixture["worldpack"]))

    def test_build_rejects_missing_or_ambiguous_3d_entrypoint_names(self) -> None:
        missing_node = _document()
        missing_node["nodes"][0]["name"] = "Other"
        duplicate_node = _document()
        duplicate_node["nodes"].append({"name": "Root"})
        missing_animation = _document()
        missing_animation["animations"][0]["name"] = "walk"
        duplicate_animation = _document()
        duplicate_animation["animations"].append(
            copy.deepcopy(duplicate_animation["animations"][0])
        )
        cases = (
            (missing_node, "missing required nodes names"),
            (duplicate_node, "ambiguous required nodes names"),
            (missing_animation, "animation 'idle'.*found 0"),
            (duplicate_animation, "ambiguous required animations names"),
        )

        for document, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = _fixture(root, document=document)
                output = root / "handoff/assetpack.json"
                with self.assertRaisesRegex(AssetPackError, expected):
                    build_assetpack(fixture["manifest"], fixture["worldpack"], output)
                self.assertFalse(output.exists())

    def test_verify_rejects_unbound_and_cross_file_ambiguous_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            original = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            for field, value, expected in (
                ("node", "Missing", "missing required nodes names"),
                ("default_animation", "walk", "animation 'walk'.*found 0"),
                ("moving_animation", "walk", "animation 'walk'.*found 0"),
            ):
                with self.subTest(field=field):
                    changed = copy.deepcopy(original)
                    changed["bindings"][0]["entrypoint"][field] = value
                    changed["content_hash"] = canonical_payload_hash(changed)
                    output.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(AssetPackError, expected):
                        verify_assetpack(output)

            ambiguous = copy.deepcopy(original)
            asset = ambiguous["assets"][0]
            model = next(file for file in asset["files"] if file["role"] == "model")
            model_path = output.parent / model["path"]
            animation_path = model_path.with_name("01_animation.glb")
            shutil.copyfile(model_path, animation_path)
            animation = {
                **model,
                "role": "animation",
                "path": animation_path.relative_to(output.parent).as_posix(),
            }
            asset["files"] = sorted(
                [animation, model],
                key=lambda item: (item["role"], item["path"]),
            )
            asset["metrics"] = {name: value * 2 for name, value in asset["metrics"].items()}
            ambiguous["content_hash"] = canonical_payload_hash(ambiguous)
            output.write_text(json.dumps(ambiguous), encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "animation 'idle'.*found 2"):
                verify_assetpack(output)

    def test_build_rejects_hash_size_media_and_authoring_storage(self) -> None:
        mutations = {
            "SHA-256": lambda output: output.__setitem__("sha256", "0" * 64),
            "size": lambda output: output.__setitem__("size", output["size"] + 1),
            "media_type": lambda output: output.__setitem__("media_type", "image/png"),
            "authoring-only": lambda output: output.__setitem__(
                "runtime_file", "evidence/neutral_actor.glb"
            ),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = _fixture(root)
                manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                output = manifest["assets"][0]["outputs"][0]
                if expected == "authoring-only":
                    evidence = fixture["authoring"] / "evidence/neutral_actor.glb"
                    evidence.parent.mkdir(parents=True, exist_ok=True)
                    evidence.write_bytes(fixture["model"].read_bytes())
                    output["sha256"] = _sha256(evidence)
                    output["size"] = evidence.stat().st_size
                mutate(output)
                _rewrite_hashed(fixture["manifest"], manifest)
                with self.assertRaisesRegex(AssetPackError, expected):
                    build_assetpack(
                        fixture["manifest"],
                        fixture["worldpack"],
                        root / "out/assetpack.json",
                    )

    def test_processing_rejects_external_glb_before_assetpack_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(AssetContractError, "external URIs"):
                _fixture(
                    root,
                    document=_document(images=[{"uri": "albedo.png"}]),
                )

    def test_processing_enforces_embedded_texture_dimension_budget(self) -> None:
        png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 4096, 1)
        document = _document(
            buffers=[{"byteLength": len(png_header)}],
            bufferViews=[{"buffer": 0, "byteOffset": 0, "byteLength": len(png_header)}],
            images=[{"bufferView": 0, "mimeType": "image/png"}],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(AssetContractError, "max_texture_size"):
                _fixture(root, document=document, binary=png_header)

    def test_optional_output_size_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["assets"][0]["outputs"][0].pop("size")
            _rewrite_hashed(fixture["manifest"], manifest)

            payload = build_assetpack(
                fixture["manifest"],
                fixture["worldpack"],
                root / "out/assetpack.json",
            )

            self.assertGreater(payload["assets"][0]["files"][0]["size"], 0)

    def test_required_unprocessed_asset_and_missing_3d_presentation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["assets"].append(
                {
                    "id": "required_model",
                    "kind": "model_3d",
                    "representation": "3d",
                    "required": True,
                    "status": "planned",
                }
            )
            _rewrite_hashed(fixture["manifest"], manifest)
            with self.assertRaisesRegex(AssetPackError, "must be processed"):
                build_assetpack(
                    fixture["manifest"],
                    fixture["worldpack"],
                    root / "out/assetpack.json",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["bindings"][0].pop("presentation")
            _rewrite_hashed(fixture["manifest"], manifest)
            with self.assertRaisesRegex(AssetPackError, "missing fields: presentation"):
                build_assetpack(
                    fixture["manifest"],
                    fixture["worldpack"],
                    root / "out/assetpack.json",
                )

    def test_verify_rejects_content_hash_file_and_metric_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            original = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            bad_hash = copy.deepcopy(original)
            bad_hash["content_hash"] = "0" * 64
            output.write_text(json.dumps(bad_hash), encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "content hash"):
                verify_assetpack(output)

            metric_tamper = copy.deepcopy(original)
            metric_tamper["assets"][0]["metrics"]["triangles"] = 2
            metric_tamper["content_hash"] = canonical_payload_hash(metric_tamper)
            output.write_text(json.dumps(metric_tamper), encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "metrics do not match"):
                verify_assetpack(output)

            output.write_text(json.dumps(original), encoding="utf-8")
            packaged = output.parent / original["assets"][0]["files"][0]["path"]
            packaged.write_bytes(packaged.read_bytes()[:-4] + b"bad!")
            with self.assertRaisesRegex(AssetPackError, "sha256 does not match"):
                verify_assetpack(output)

    def test_verify_rejects_worldpack_mismatch_and_noncanonical_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            payload = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            other_world_source = json.loads(fixture["worldpack"].read_text(encoding="utf-8"))
            other_world_source.pop("content_hash", None)
            other_world_source["world"]["id"] = "other_world"
            other_world = _write_hashed_json(
                root / "other.worldpack.json",
                other_world_source,
            )
            self.assertIsInstance(other_world, dict)
            with self.assertRaisesRegex(AssetPackError, "does not match"):
                verify_assetpack(output, root / "other.worldpack.json")

            duplicate = copy.deepcopy(payload)
            duplicate["bindings"].append(copy.deepcopy(duplicate["bindings"][0]))
            duplicate["content_hash"] = canonical_payload_hash(duplicate)
            output.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "duplicate runtime binding slot"):
                verify_assetpack(output)

    def test_shared_runtime_output_matrix_is_complete_and_closed(self) -> None:
        expected = {
            "animation_3d": ("3d", ["animation"]),
            "character_3d": ("3d", ["model"]),
            "collision_3d": ("3d", ["collision"]),
            "environment_3d": ("3d", ["model"]),
            "font": ("2d", ["font"]),
            "material_set": ("3d", ["model"]),
            "model_3d": ("3d", ["model"]),
            "music": ("audio", ["audio"]),
            "portrait": ("2d", ["texture"]),
            "rig": ("3d", ["skeleton"]),
            "sfx": ("audio", ["audio"]),
            "shader": ("2_5d", ["fragment_shader"]),
            "sprite": ("2d", ["texture"]),
            "spritesheet": ("2_5d", ["clipset", "texture"]),
            "tileset": ("2d", ["clipset", "texture"]),
            "ui": ("2d", ["texture"]),
            "vfx": ("2_5d", ["texture"]),
            "vfx_3d": ("3d", ["model"]),
        }
        self.assertEqual(set(expected), set(ASSET_RUNTIME_OUTPUT_CONTRACTS))
        for kind, (representation, roles) in expected.items():
            with self.subTest(kind=kind):
                self.assertIsNone(runtime_output_contract_issue(kind, representation, roles))
                self.assertIsNotNone(
                    runtime_output_contract_issue(kind, representation, roles + [roles[0]])
                )
        self.assertIsNone(
            runtime_output_contract_issue(
                "character_3d",
                "3d",
                ["animation", "collision", "model", "skeleton"],
            )
        )
        self.assertIsNone(
            runtime_output_contract_issue(
                "shader",
                "2d",
                ["fragment_shader", "vertex_shader"],
            )
        )
        self.assertIsNotNone(runtime_output_contract_issue("shader", "2d", []))

    def test_verify_rejects_role_matrix_and_casefold_path_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            original = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            wrong_roles = copy.deepcopy(original)
            wrong_roles["assets"][0]["kind"] = "sprite"
            wrong_roles["assets"][0]["representation"] = "2d"
            wrong_roles["content_hash"] = canonical_payload_hash(wrong_roles)
            output.write_text(json.dumps(wrong_roles), encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "runtime output"):
                verify_assetpack(output)

            collision = copy.deepcopy(original)
            asset = collision["assets"][0]
            model = asset["files"][0]
            animation = {
                **model,
                "role": "animation",
                "path": model["path"].upper(),
            }
            asset["files"] = sorted(
                [animation, model],
                key=lambda item: (item["role"], item["path"]),
            )
            collision["content_hash"] = canonical_payload_hash(collision)
            output.write_text(json.dumps(collision), encoding="utf-8")
            zero_metrics = dict.fromkeys(original["assets"][0]["metrics"], 0)
            model_metrics = original["assets"][0]["metrics"]
            model_source = output.parent / model["path"]

            def inspected(
                _root: Path,
                _asset_id: str,
                entry: dict[str, object],
                *,
                context: str,
            ) -> tuple[dict[str, object], dict[str, int], Path]:
                del context
                metrics = model_metrics if entry["role"] == "model" else zero_metrics
                return entry, metrics, model_source

            with (
                patch.object(
                    assetpack_module,
                    "_verify_runtime_file",
                    side_effect=inspected,
                ),
                self.assertRaisesRegex(AssetPackError, "NFC/casefold"),
            ):
                verify_assetpack(output)

    def test_build_rejects_casefold_colliding_runtime_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            original = manifest["assets"][0]["outputs"][0]
            manifest["assets"][0]["outputs"].append(
                {
                    **original,
                    "role": "animation",
                    "runtime_file": original["runtime_file"].upper(),
                }
            )
            _rewrite_hashed(fixture["manifest"], manifest)
            output = root / "handoff/assetpack.json"
            with (
                patch(
                    "worldforge.assets.validate_asset_manifest",
                    return_value=[],
                ),
                self.assertRaisesRegex(AssetPackError, "NFC/casefold"),
            ):
                build_assetpack(fixture["manifest"], fixture["worldpack"], output)
            self.assertFalse(output.exists())

    def test_integral_worldpack_loader_rejects_shallow_hash_only_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            malformed_path = root / "malformed.worldpack.json"
            _write_hashed_json(
                malformed_path,
                {
                    "format": "isoworld.worldpack",
                    "format_version": 5,
                    "world": {"id": "neutral_world", "title": "Neutral"},
                    "collections": {},
                },
            )
            output = root / "handoff/assetpack.json"
            with self.assertRaises(AssetPackError):
                build_assetpack(fixture["manifest"], malformed_path, output)
            self.assertFalse(output.exists())

    def test_verify_rejects_incompatible_kind_representations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            original = build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            for kind in ("music", "sfx"):
                with self.subTest(kind=kind):
                    changed = copy.deepcopy(original)
                    changed["assets"][0]["kind"] = kind
                    changed["content_hash"] = canonical_payload_hash(changed)
                    output.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(AssetPackError, "requires audio representation"):
                        verify_assetpack(output)

            for kind in (
                "animation_3d",
                "character_3d",
                "collision_3d",
                "environment_3d",
                "material_set",
                "model_3d",
                "rig",
                "vfx_3d",
            ):
                with self.subTest(kind=kind):
                    changed = copy.deepcopy(original)
                    changed["assets"][0]["kind"] = kind
                    changed["assets"][0]["representation"] = "2d"
                    changed["content_hash"] = canonical_payload_hash(changed)
                    output.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(AssetPackError, "requires 3d representation"):
                        verify_assetpack(output)

    def test_builder_rejects_symlinked_publication_paths_including_broken_links(self) -> None:
        cases = (
            "output_broken",
            "parent",
            "parent_broken",
            "assets_root",
            "assets_root_broken",
            "asset_directory",
            "asset_directory_broken",
            "runtime_broken",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = _fixture(root)
                output = root / "handoff/assetpack.json"
                outside = root / "outside"
                outside.mkdir()
                sentinel = outside / "keep.txt"
                sentinel.write_text("keep", encoding="utf-8")

                if case == "output_broken":
                    output.parent.mkdir()
                    output.symlink_to(root / "missing-output.json")
                    expected = "symbolic link"
                    preserved_link = output
                elif case in {"parent", "parent_broken"}:
                    target = outside if case == "parent" else root / "missing-parent"
                    output.parent.symlink_to(target, target_is_directory=True)
                    expected = "safe directory"
                    preserved_link = output.parent
                elif case in {"assets_root", "assets_root_broken"}:
                    output.parent.mkdir()
                    target = outside if case == "assets_root" else root / "missing-assets"
                    asset_root = output.parent / "assets"
                    asset_root.symlink_to(target, target_is_directory=True)
                    expected = "safe directory"
                    preserved_link = asset_root
                elif case in {"asset_directory", "asset_directory_broken"}:
                    asset_root = output.parent / "assets"
                    asset_root.mkdir(parents=True)
                    target = outside if case == "asset_directory" else root / "missing-asset"
                    asset_directory = asset_root / "neutral_actor"
                    asset_directory.symlink_to(target, target_is_directory=True)
                    expected = "safe directory"
                    preserved_link = asset_directory
                else:
                    asset_directory = output.parent / "assets/neutral_actor"
                    asset_directory.mkdir(parents=True)
                    runtime_file = asset_directory / "00_model.glb"
                    runtime_file.symlink_to(root / "missing-runtime.glb")
                    expected = "symbolic link"
                    preserved_link = runtime_file

                with self.assertRaisesRegex(AssetPackError, expected):
                    build_assetpack(fixture["manifest"], fixture["worldpack"], output)

                self.assertTrue(preserved_link.is_symlink())
                self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
                self.assertFalse((outside / "00_model.glb").exists())

    def test_builder_never_overwrites_runtime_files_and_rolls_back_only_new_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            asset_directory = output.parent / "assets/neutral_actor"
            asset_directory.mkdir(parents=True)
            runtime_file = asset_directory / "00_model.glb"
            runtime_file.write_bytes(b"preexisting runtime")

            with self.assertRaisesRegex(AssetPackError, "Refusing to overwrite"):
                build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            self.assertEqual(b"preexisting runtime", runtime_file.read_bytes())
            self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            asset_directory = output.parent / "assets/neutral_actor"
            asset_directory.mkdir(parents=True)
            sentinel = asset_directory / "keep.txt"
            sentinel.write_text("preexisting", encoding="utf-8")

            with patch(
                "worldforge.assetpack.verify_assetpack",
                side_effect=AssetPackError("forced final verification failure"),
            ):
                with self.assertRaisesRegex(AssetPackError, "forced final verification"):
                    build_assetpack(fixture["manifest"], fixture["worldpack"], output)

            self.assertEqual("preexisting", sentinel.read_text(encoding="utf-8"))
            self.assertFalse((asset_directory / "00_model.glb").exists())
            self.assertFalse(output.exists())

    def test_builder_refuses_to_overwrite_an_existing_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _fixture(root)
            output = root / "handoff/assetpack.json"
            output.parent.mkdir(parents=True)
            output.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(AssetPackError, "Refusing to overwrite"):
                build_assetpack(fixture["manifest"], fixture["worldpack"], output)
            self.assertEqual("do not overwrite", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
