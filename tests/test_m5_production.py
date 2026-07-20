from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.asset_contracts import validate_asset_spec
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    write_json_atomic,
)
from worldforge.asset_production import (
    create_production_request,
    validate_production_receipt,
    validate_production_request,
)


def _spec(root: Path, *, representation: str = "2d") -> Path:
    technical: dict[str, object] = {
        "runtime_format": "png" if representation == "2d" else "glb",
        "memory_budget_bytes": 8_000_000,
    }
    if representation == "2d":
        technical.update({"width": 64, "height": 64, "alpha_mode": "mask"})
        kind = "sprite"
        expected = [{"role": "texture", "media_type": "image/png"}]
    else:
        technical.update(
            {
                "physical_dimensions_m": [1.0, 2.0, 1.0],
                "budgets": {
                    "max_vertices": 10_000,
                    "max_triangles": 20_000,
                    "max_materials": 4,
                    "max_texture_size": 2048,
                },
            }
        )
        kind = "character_3d"
        expected = [{"role": "model", "media_type": "model/gltf-binary"}]
    raw = bind_content_hash(
        {
            "format": "rpg-world-forge.asset_spec",
            "format_version": 2,
            "id": "hero_visual",
            "kind": kind,
            "representation": representation,
            "target_id": "primary",
            "target_hash": "a" * 64,
            "inventory_hash": "b" * 64,
            "visual_bible_hash": "c" * 64,
            "audio_bible_hash": "d" * 64,
            "purpose": "Gameplay representation of the hero",
            "canonical_sources": ["actors:hero"],
            "acceptance_criteria": ["readable_at_runtime_scale"],
            "semantic_slots": ["actor:hero"],
            "technical": technical,
            "production": {
                "allowed_routes": ["modly", "openai"],
                "allowed_executors": ["blender_mcp", "modly_cli_mcp", "openai_image"],
            },
            "expected_outputs": expected,
        }
    )
    path = root / "specs/hero_visual.json"
    write_json_atomic(path, raw)
    return path


def _request(root: Path, *, executor: str = "openai_image", route: str = "openai") -> Path:
    if not (root / "specs/hero_visual.json").exists():
        _spec(root, representation="3d" if executor == "blender_mcp" else "2d")
    operation = {
        "openai_image": "image_generate",
        "blender_mcp": "model_from_reference",
        "modly_cli_mcp": "workflow_run",
    }[executor]
    path = root / f"requests/{executor}.json"
    reviewed_script: str | None = None
    request_inputs: list[tuple[str, str]] = []
    parent_hashes: list[str] = []
    if executor == "blender_mcp":
        reference_request = root / "requests/blender_reference.json"
        create_production_request(
            root,
            "specs/hero_visual.json",
            reference_request,
            request_id="hero_visual_reference_parent",
            route="openai",
            executor="openai_image",
            operation="concept_reference",
            parameters={
                "model": "gpt-image-2-2026-04-21",
                "background": "opaque",
                "size": "1024x1024",
            },
            expected_outputs=[{"role": "preview", "media_type": "image/png"}],
        )
        reference_receipt_path = _receipt(
            root,
            reference_request,
            executor="openai_image",
        )
        reference_receipt = json.loads(reference_receipt_path.read_text(encoding="utf-8"))
        script = root / "recipes/blender/model.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# reviewed fixture\n", encoding="utf-8")
        reviewed_script = "recipes/blender/model.py"
        request_inputs = [("reference", "generated/hero.png")]
        parent_hashes = [reference_receipt["content_hash"]]
    if executor == "modly_cli_mcp":
        discovery = root / "evidence/modly-discovery.json"
        extension = {
            "id": "example",
            "version": "1.0.0",
            "revision": "c" * 40,
            "manifest_hash": "d" * 64,
            "workflow_hash": "e" * 64,
        }
        model = {
            "id": "example/main",
            "version": "1",
            "weights_hash": "f" * 64,
        }
        write_json_atomic(
            discovery,
            bind_content_hash(
                {
                    "format": "rpg-world-forge.modly_capability_discovery",
                    "format_version": 1,
                    "modly_cli_mcp_version": "0.1.1",
                    "modly_version": "0.4.1",
                    "canonical_surface": "workflow_run",
                    "capability_id": "example/image-to-mesh",
                    "support_state": "supported",
                    "extension": extension,
                    "model": model,
                }
            ),
        )
        request_parameters = {
            "modly_cli_mcp_version": "0.1.1",
            "modly_version": "0.4.1",
            "canonical_surface": "workflow_run",
            "capability_id": "example/image-to-mesh",
            "support_state": "supported",
            "capability_discovery": artifact_reference(root, "evidence/modly-discovery.json"),
            "extension": extension,
            "model": model,
            "setup_reviewed": True,
            "arguments": {"quality": "reviewed"},
        }
    elif executor == "openai_image":
        request_parameters = {
            "model": "gpt-image-2-2026-04-21",
            "background": "opaque",
            "size": "1024x1024",
        }
    else:
        request_parameters = {"quality": "reviewed"}
    create_production_request(
        root,
        "specs/hero_visual.json",
        path,
        request_id=f"hero_visual_{executor}_0001",
        route=route,
        executor=executor,
        operation=operation,
        inputs=request_inputs,
        parameters=request_parameters,
        parent_receipt_hashes=parent_hashes,
        reviewed_script_file=reviewed_script,
    )
    return path


def _receipt(root: Path, request_path: Path, *, executor: str) -> Path:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    expected = request["expected_outputs"][0]
    candidate = root / (
        "generated/hero.glb"
        if expected["media_type"] == "model/gltf-binary"
        else (
            "generated/hero.blend"
            if expected["media_type"] == "application/x-blender"
            else "generated/hero.png"
        )
    )
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if expected["media_type"] == "application/x-blender":
        candidate.write_bytes(b"BLENDERfixture")
    elif expected["media_type"] == "model/gltf-binary":
        document = json.dumps(
            {"asset": {"version": "2.0"}}, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        document += b" " * (-len(document) % 4)
        candidate.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<II", len(document), 0x4E4F534A)
            + document
        )
    else:
        candidate.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    if executor == "openai_image":
        toolchain = {
            "surface": "images_api",
            "requested_model": "gpt-image-2-2026-04-21",
            "resolved_model": "gpt-image-2-2026-04-21",
            "version_resolution": "exact_snapshot",
        }
        replayability = "traceable_not_bit_reproducible"
    elif executor == "blender_mcp":
        toolchain = {
            "blender_version": "4.5.0",
            "blender_mcp_version": "1.6.4",
            "addon_revision": "6641189231caf3752302ae20591bc87fda85fc4e",
            "telemetry_disabled": True,
        }
        replayability = "traceable_not_bit_reproducible"
    else:
        parameters = request["parameters"]
        toolchain = {
            "modly_cli_mcp_version": parameters["modly_cli_mcp_version"],
            "modly_version": parameters["modly_version"],
            "canonical_surface": parameters["canonical_surface"],
            "capability_id": parameters["capability_id"],
            "run_id": "run-1",
            "support_state": "supported",
            "capability_discovery_hash": parameters["capability_discovery"]["sha256"],
            "extension": parameters["extension"],
            "model": parameters["model"],
            "setup_reviewed": parameters["setup_reviewed"],
        }
        replayability = "traceable_not_bit_reproducible"
    raw: dict[str, object] = {
        "format": "rpg-world-forge.asset_production_receipt",
        "format_version": 1,
        "id": f"receipt_{executor}",
        "request": artifact_reference(root, request_path.relative_to(root).as_posix()),
        "asset_id": request["asset_id"],
        "route": request["route"],
        "executor": request["executor"],
        "operation": request["operation"],
        "status": "succeeded",
        "started_at": "2026-07-20T10:00:00Z",
        "completed_at": "2026-07-20T10:01:00Z",
        "parent_receipt_hashes": request["parent_receipt_hashes"],
        "toolchain": toolchain,
        "replayability": replayability,
        "outputs": [
            {
                "role": expected["role"],
                **artifact_reference(root, candidate.relative_to(root).as_posix()),
                "media_type": expected["media_type"],
            }
        ],
    }
    if executor == "blender_mcp":
        raw["reviewed_script"] = request["reviewed_script"]
        raw["approval_mode"] = request["approval_mode"]
    receipt = root / f"receipts/{executor}.json"
    write_json_atomic(receipt, bind_content_hash(raw))
    return receipt


def _blender_receipt(
    root: Path,
    request_path: Path,
    *,
    receipt_name: str,
) -> tuple[Path, dict[str, dict[str, object]]]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    outputs: list[dict[str, object]] = []
    by_role: dict[str, dict[str, object]] = {}
    for index, expected in enumerate(request["expected_outputs"]):
        candidate = root / f"generated/{receipt_name}_{index}_{expected['role']}.glb"
        document = json.dumps(
            {"asset": {"version": "2.0"}}, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        document += b" " * (-len(document) % 4)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<II", len(document), 0x4E4F534A)
            + document
        )
        output = {
            "role": expected["role"],
            **artifact_reference(root, candidate.relative_to(root).as_posix()),
            "media_type": expected["media_type"],
        }
        outputs.append(output)
        by_role[expected["role"]] = output
    raw = bind_content_hash(
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": f"receipt_{receipt_name}",
            "request": artifact_reference(root, request_path.relative_to(root).as_posix()),
            "asset_id": request["asset_id"],
            "route": request["route"],
            "executor": "blender_mcp",
            "operation": request["operation"],
            "status": "succeeded",
            "started_at": "2026-07-20T10:00:00Z",
            "completed_at": "2026-07-20T10:01:00Z",
            "parent_receipt_hashes": request["parent_receipt_hashes"],
            "toolchain": {
                "blender_version": "4.5.0",
                "blender_mcp_version": "1.6.4",
                "addon_revision": "6641189231caf3752302ae20591bc87fda85fc4e",
                "telemetry_disabled": True,
            },
            "replayability": "traceable_not_bit_reproducible",
            "reviewed_script": request["reviewed_script"],
            "approval_mode": "explicit",
            "outputs": outputs,
        }
    )
    receipt_path = root / f"receipts/{receipt_name}.json"
    write_json_atomic(receipt_path, raw)
    return receipt_path, by_role


class M5ProductionTests(unittest.TestCase):
    def test_3d_reference_request_uses_operation_specific_image_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root, representation="3d")
            with self.assertRaisesRegex(AssetContractError, "incompatible with operation"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/invalid_reference.json",
                    request_id="hero_reference_invalid",
                    route="openai",
                    executor="openai_image",
                    operation="concept_reference",
                )

            request_path = root / "requests/reference.json"
            request = create_production_request(
                root,
                "specs/hero_visual.json",
                request_path,
                request_id="hero_reference_0001",
                route="openai",
                executor="openai_image",
                operation="concept_reference",
                expected_outputs=[{"role": "preview", "media_type": "image/png"}],
                parameters={
                    "background": "opaque",
                    "model": "gpt-image-2-2026-04-21",
                    "size": "1024x1024",
                },
            )
            self.assertEqual(
                [{"role": "preview", "media_type": "image/png"}],
                request["expected_outputs"],
            )
            receipt = _receipt(root, request_path, executor="openai_image")
            self.assertEqual([], validate_production_receipt(receipt, asset_root=root))
            reference_receipt = json.loads(receipt.read_text(encoding="utf-8"))

            script = root / "recipes/blender/model.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed model operation\n", encoding="utf-8")
            model_request_path = root / "requests/model.json"
            model_request = create_production_request(
                root,
                "specs/hero_visual.json",
                model_request_path,
                request_id="hero_model_0001",
                route="openai",
                executor="blender_mcp",
                operation="model_from_reference",
                inputs=[("reference", "generated/hero.png")],
                parent_receipt_hashes=[reference_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/model.py",
            )
            self.assertEqual(
                [reference_receipt["content_hash"]],
                model_request["parent_receipt_hashes"],
            )
            model_receipt = _receipt(
                root,
                model_request_path,
                executor="blender_mcp",
            )
            self.assertEqual(
                [],
                validate_production_receipt(model_receipt, asset_root=root),
            )

    def test_blender_chain_binds_model_rig_animation_refinement_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_request_path = _request(root, executor="blender_mcp")
            model_receipt_path = _receipt(root, model_request_path, executor="blender_mcp")
            self.assertEqual(
                [],
                validate_production_receipt(model_receipt_path, asset_root=root),
            )
            model_receipt = json.loads(model_receipt_path.read_text(encoding="utf-8"))
            script = root / "recipes/blender/stage.py"
            script.write_text("# reviewed chained stage\n", encoding="utf-8")

            rig_request_path = root / "requests/rig.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                rig_request_path,
                request_id="hero_rig_stage",
                route="openai",
                executor="blender_mcp",
                operation="rig",
                inputs=[("model", "generated/hero.glb")],
                expected_outputs=[
                    {"role": "model", "media_type": "model/gltf-binary"},
                    {"role": "skeleton", "media_type": "model/gltf-binary"},
                ],
                parent_receipt_hashes=[model_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/stage.py",
            )
            rig_receipt_path, rig_outputs = _blender_receipt(
                root,
                rig_request_path,
                receipt_name="hero_rig_stage",
            )
            self.assertEqual([], validate_production_receipt(rig_receipt_path, asset_root=root))
            rig_receipt = json.loads(rig_receipt_path.read_text(encoding="utf-8"))

            animate_request_path = root / "requests/animate.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                animate_request_path,
                request_id="hero_animate_stage",
                route="openai",
                executor="blender_mcp",
                operation="animate",
                inputs=[
                    ("model", str(rig_outputs["model"]["file"])),
                    ("skeleton", str(rig_outputs["skeleton"]["file"])),
                ],
                parent_receipt_hashes=[rig_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/stage.py",
            )
            animate_receipt_path, animate_outputs = _blender_receipt(
                root,
                animate_request_path,
                receipt_name="hero_animate_stage",
            )
            self.assertEqual(
                [],
                validate_production_receipt(animate_receipt_path, asset_root=root),
            )
            animate_receipt = json.loads(animate_receipt_path.read_text(encoding="utf-8"))

            refine_request_path = root / "requests/refine.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                refine_request_path,
                request_id="hero_refine_stage",
                route="openai",
                executor="blender_mcp",
                operation="refine",
                inputs=[("model", str(animate_outputs["model"]["file"]))],
                parent_receipt_hashes=[animate_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/stage.py",
            )
            refine_receipt_path, refine_outputs = _blender_receipt(
                root,
                refine_request_path,
                receipt_name="hero_refine_stage",
            )
            self.assertEqual(
                [],
                validate_production_receipt(refine_receipt_path, asset_root=root),
            )
            refine_receipt = json.loads(refine_receipt_path.read_text(encoding="utf-8"))

            export_request_path = root / "requests/export.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                export_request_path,
                request_id="hero_export_stage",
                route="openai",
                executor="blender_mcp",
                operation="export_glb",
                inputs=[("model", str(refine_outputs["model"]["file"]))],
                parent_receipt_hashes=[refine_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/stage.py",
            )
            export_receipt_path, _ = _blender_receipt(
                root,
                export_request_path,
                receipt_name="hero_export_stage",
            )
            self.assertEqual(
                [],
                validate_production_receipt(export_receipt_path, asset_root=root),
            )

    def test_modly_model_can_only_enter_blender_as_exact_direct_refinement_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root, representation="3d")
            modly_request_path = _request(root, executor="modly_cli_mcp", route="modly")
            modly_receipt_path = _receipt(
                root,
                modly_request_path,
                executor="modly_cli_mcp",
            )
            self.assertEqual(
                [],
                validate_production_receipt(modly_receipt_path, asset_root=root),
            )
            modly_receipt = json.loads(modly_receipt_path.read_text(encoding="utf-8"))
            script = root / "recipes/blender/refine.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# reviewed Modly repair\n", encoding="utf-8")

            refine_request_path = root / "requests/modly_refine.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                refine_request_path,
                request_id="hero_modly_refine",
                route="modly",
                executor="blender_mcp",
                operation="refine",
                inputs=[("model", "generated/hero.glb")],
                parent_receipt_hashes=[modly_receipt["content_hash"]],
                reviewed_script_file="recipes/blender/refine.py",
            )
            refine_receipt_path, refine_outputs = _blender_receipt(
                root,
                refine_request_path,
                receipt_name="hero_modly_refine",
            )
            self.assertEqual(
                [],
                validate_production_receipt(refine_receipt_path, asset_root=root),
            )
            refine_receipt = json.loads(refine_receipt_path.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(
                AssetContractError,
                "exact output of one direct modly_cli_mcp parent",
            ):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/decorative_modly_parent.json",
                    request_id="decorative_modly_parent",
                    route="modly",
                    executor="blender_mcp",
                    operation="refine",
                    inputs=[("model", str(refine_outputs["model"]["file"]))],
                    parent_receipt_hashes=sorted(
                        [
                            modly_receipt["content_hash"],
                            refine_receipt["content_hash"],
                        ]
                    ),
                    reviewed_script_file="recipes/blender/refine.py",
                )

            copied = root / "generated/copied_modly.glb"
            copied.write_bytes((root / "generated/hero.glb").read_bytes())
            with self.assertRaisesRegex(AssetContractError, "not an exact output"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/modly_refine_copy.json",
                    request_id="hero_modly_refine_copy",
                    route="modly",
                    executor="blender_mcp",
                    operation="refine",
                    inputs=[("model", "generated/copied_modly.glb")],
                    parent_receipt_hashes=[modly_receipt["content_hash"]],
                    reviewed_script_file="recipes/blender/refine.py",
                )

    def test_procedural_receipt_supports_declared_font_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _spec(root)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec.update(
                {
                    "kind": "font",
                    "technical": {
                        "runtime_format": "ttf",
                        "memory_budget_bytes": 1_000_000,
                    },
                    "expected_outputs": [{"role": "font", "media_type": "font/ttf"}],
                }
            )
            spec["production"]["allowed_executors"] = [
                "blender_mcp",
                "modly_cli_mcp",
                "openai_image",
                "procedural",
            ]
            write_json_atomic(spec_path, bind_content_hash(spec), overwrite=True)

            request_path = root / "requests/font.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                request_path,
                request_id="font_procedural_0001",
                route="openai",
                executor="procedural",
                operation="process_run",
            )
            candidate = root / "generated/font.ttf"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"\x00\x01\x00\x00fixture")
            request = json.loads(request_path.read_text(encoding="utf-8"))
            receipt = bind_content_hash(
                {
                    "format": "rpg-world-forge.asset_production_receipt",
                    "format_version": 1,
                    "id": "receipt_font_procedural",
                    "request": artifact_reference(root, "requests/font.json"),
                    "asset_id": request["asset_id"],
                    "route": request["route"],
                    "executor": request["executor"],
                    "operation": request["operation"],
                    "status": "succeeded",
                    "started_at": "2026-07-20T10:00:00Z",
                    "completed_at": "2026-07-20T10:00:01Z",
                    "parent_receipt_hashes": [],
                    "toolchain": {"processor": "reviewed_font_fixture"},
                    "replayability": "deterministic_seeded",
                    "outputs": [
                        {
                            "role": "font",
                            **artifact_reference(root, "generated/font.ttf"),
                            "media_type": "font/ttf",
                        }
                    ],
                }
            )
            receipt_path = root / "receipts/font.json"
            write_json_atomic(receipt_path, receipt)

            self.assertEqual(
                [],
                validate_production_receipt(receipt_path, asset_root=root),
            )

    def test_spec_rejects_kind_representation_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _spec(root)
            raw = json.loads(spec_path.read_text(encoding="utf-8"))
            raw["kind"] = "rig"
            write_json_atomic(spec_path, bind_content_hash(raw), overwrite=True)
            self.assertTrue(
                any(
                    issue.path == "representation" and "require 3d" in issue.message
                    for issue in validate_asset_spec(spec_path)
                )
            )

    def test_spec_accepts_exact_runtime_output_matrix(self) -> None:
        technical_2d = {
            "runtime_format": "png",
            "memory_budget_bytes": 8_000_000,
            "width": 64,
            "height": 64,
            "alpha_mode": "mask",
        }
        technical_audio = {
            "runtime_format": "wav",
            "memory_budget_bytes": 8_000_000,
            "sample_rate": 48_000,
            "channels": 2,
        }
        technical_3d = {
            "runtime_format": "glb",
            "memory_budget_bytes": 8_000_000,
            "physical_dimensions_m": [1.0, 2.0, 1.0],
            "budgets": {
                "max_vertices": 10_000,
                "max_triangles": 20_000,
                "max_materials": 4,
                "max_texture_size": 2048,
            },
        }
        cases = (
            ("sprite", "2d", technical_2d, [("texture", "image/png")]),
            (
                "spritesheet",
                "2_5d",
                technical_2d,
                [("clipset", "application/json"), ("texture", "image/png")],
            ),
            ("music", "audio", technical_audio, [("audio", "audio/wav")]),
            (
                "font",
                "2d",
                {"runtime_format": "ttf", "memory_budget_bytes": 1_000_000},
                [("font", "font/ttf")],
            ),
            (
                "font",
                "2_5d",
                {"runtime_format": "otf", "memory_budget_bytes": 1_000_000},
                [("font", "font/otf")],
            ),
            (
                "shader",
                "2d",
                {"runtime_format": "glsl", "memory_budget_bytes": 1_000_000},
                [("fragment_shader", "text/x-glsl"), ("vertex_shader", "text/x-glsl")],
            ),
            ("animation_3d", "3d", technical_3d, [("animation", "model/gltf-binary")]),
            ("character_3d", "3d", technical_3d, [("model", "model/gltf-binary")]),
            ("collision_3d", "3d", technical_3d, [("collision", "model/gltf-binary")]),
            ("environment_3d", "3d", technical_3d, [("model", "model/gltf-binary")]),
            ("material_set", "3d", technical_3d, [("model", "model/gltf-binary")]),
            ("model_3d", "3d", technical_3d, [("model", "model/gltf-binary")]),
            ("rig", "3d", technical_3d, [("skeleton", "model/gltf-binary")]),
            ("vfx_3d", "3d", technical_3d, [("model", "model/gltf-binary")]),
        )
        for kind, representation, technical, output_pairs in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec_path = _spec(root)
                raw = json.loads(spec_path.read_text(encoding="utf-8"))
                raw["kind"] = kind
                raw["representation"] = representation
                raw["technical"] = technical
                raw["expected_outputs"] = [
                    {"role": role, "media_type": media_type} for role, media_type in output_pairs
                ]
                write_json_atomic(spec_path, bind_content_hash(raw), overwrite=True)
                self.assertEqual([], validate_asset_spec(spec_path))

    def test_spec_rejects_nonruntime_outputs_wrong_3d_primary_and_nonportable_qa(self) -> None:
        mutations = {
            "2d webp": lambda value: value["expected_outputs"][0].__setitem__(
                "media_type", "image/webp"
            ),
            "qa prose": lambda value: value.__setitem__(
                "acceptance_criteria", ["Readable at runtime scale"]
            ),
            "audio ogg": lambda value: value.update(
                {
                    "kind": "music",
                    "representation": "audio",
                    "technical": {
                        "runtime_format": "wav",
                        "memory_budget_bytes": 8_000_000,
                        "sample_rate": 48_000,
                        "channels": 2,
                    },
                    "expected_outputs": [{"role": "audio", "media_type": "audio/ogg"}],
                }
            ),
            "font format mismatch": lambda value: value.update(
                {
                    "kind": "font",
                    "technical": {
                        "runtime_format": "ttf",
                        "memory_budget_bytes": 1_000_000,
                    },
                    "expected_outputs": [{"role": "font", "media_type": "font/otf"}],
                }
            ),
            "shader preview": lambda value: value.update(
                {
                    "kind": "shader",
                    "technical": {
                        "runtime_format": "glsl",
                        "memory_budget_bytes": 1_000_000,
                    },
                    "expected_outputs": [{"role": "preview", "media_type": "image/png"}],
                }
            ),
            "wrong animation primary": lambda value: value.update(
                {
                    "kind": "animation_3d",
                    "representation": "3d",
                    "technical": {
                        "runtime_format": "glb",
                        "memory_budget_bytes": 8_000_000,
                        "physical_dimensions_m": [1.0, 2.0, 1.0],
                        "budgets": {
                            "max_vertices": 10_000,
                            "max_triangles": 20_000,
                            "max_materials": 4,
                            "max_texture_size": 2048,
                        },
                    },
                    "expected_outputs": [{"role": "model", "media_type": "model/gltf-binary"}],
                }
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec_path = _spec(root)
                raw = json.loads(spec_path.read_text(encoding="utf-8"))
                mutate(raw)
                write_json_atomic(spec_path, bind_content_hash(raw), overwrite=True)
                self.assertTrue(validate_asset_spec(spec_path))

    def test_malformed_container_values_return_issues_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = _request(root)
            spec_path = root / "specs/hero_visual.json"
            original_spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec_mutations = (
                ("representation", lambda value: value.__setitem__("representation", [])),
                ("kind", lambda value: value.__setitem__("kind", {})),
                (
                    "runtime_format",
                    lambda value: value["technical"].__setitem__("runtime_format", []),
                ),
                (
                    "routes",
                    lambda value: value["production"].__setitem__("allowed_routes", [{}]),
                ),
                (
                    "output role",
                    lambda value: value["expected_outputs"][0].__setitem__("role", []),
                ),
            )
            for label, mutate in spec_mutations:
                with self.subTest(spec_field=label):
                    changed = json.loads(json.dumps(original_spec))
                    mutate(changed)
                    spec_path.write_text(json.dumps(bind_content_hash(changed)), encoding="utf-8")
                    self.assertTrue(validate_asset_spec(spec_path))
            spec_path.write_text(json.dumps(original_spec), encoding="utf-8")

            original_request = json.loads(request_path.read_text(encoding="utf-8"))
            for field, value in (
                ("route", []),
                ("executor", {}),
                ("operation", []),
                ("parent_receipt_hashes", [{}]),
                ("inputs", [[]]),
            ):
                with self.subTest(request_field=field):
                    changed = dict(original_request)
                    changed[field] = value
                    request_path.write_text(
                        json.dumps(bind_content_hash(changed)), encoding="utf-8"
                    )
                    self.assertTrue(validate_production_request(request_path, asset_root=root))

            request_path.write_text(json.dumps(original_request), encoding="utf-8")
            receipt_path = _receipt(root, request_path, executor="openai_image")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["outputs"][0]["media_type"] = []
            receipt_path.write_text(json.dumps(bind_content_hash(receipt)), encoding="utf-8")
            self.assertTrue(validate_production_receipt(receipt_path, asset_root=root))

    def test_openai_request_and_receipt_are_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = _request(root)
            self.assertEqual([], validate_production_request(request, asset_root=root))
            receipt = _receipt(root, request, executor="openai_image")
            self.assertEqual([], validate_production_receipt(receipt, asset_root=root))
            (root / "generated/hero.png").write_bytes(b"tampered")
            messages = [
                issue.message for issue in validate_production_receipt(receipt, asset_root=root)
            ]
            self.assertTrue(any("SHA-256" in message for message in messages))

    def test_request_rejects_secrets_urls_and_wrong_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root)
            with self.assertRaisesRegex(AssetContractError, "credential-like"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/secret.json",
                    request_id="secret_attempt",
                    route="openai",
                    executor="openai_image",
                    operation="image_generate",
                    parameters={"api_key": "forbidden"},
                )
            with self.assertRaisesRegex(AssetContractError, "credential-like value"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/secret-value.json",
                    request_id="secret_value_attempt",
                    route="openai",
                    executor="openai_image",
                    operation="image_generate",
                    parameters={"note": "Bearer abcdefghijklmnop"},
                )
            with self.assertRaisesRegex(AssetContractError, "URLs are forbidden"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/url.json",
                    request_id="url_attempt",
                    route="openai",
                    executor="openai_image",
                    operation="image_generate",
                    parameters={"note": "https://example.invalid/signed"},
                )
            with self.assertRaisesRegex(AssetContractError, "incompatible"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/wrong.json",
                    request_id="wrong_attempt",
                    route="openai",
                    executor="modly_cli_mcp",
                    operation="workflow_run",
                )
            with self.assertRaisesRegex(AssetContractError, "does not support transparent"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/impossible_transparency.json",
                    request_id="impossible_transparency",
                    route="openai",
                    executor="openai_image",
                    operation="image_generate",
                    parameters={
                        "background": "transparent",
                        "model": "gpt-image-2-2026-04-21",
                    },
                )
            request_path = root / "requests/valid_transparency.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                request_path,
                request_id="valid_transparency",
                route="openai",
                executor="openai_image",
                operation="image_generate",
                parameters={"background": "transparent", "model": "gpt-image-1"},
            )
            raw = json.loads(request_path.read_text(encoding="utf-8"))
            raw["parameters"]["model"] = "gpt-image-2"
            request_path.write_text(json.dumps(bind_content_hash(raw)), encoding="utf-8")
            self.assertTrue(
                any(
                    "does not support transparent" in issue.message
                    for issue in validate_production_request(request_path, asset_root=root)
                )
            )

    def test_every_blender_operation_requires_inputs_and_parent_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root, representation="3d")
            script = root / "recipes/blender/stage.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed stage\n", encoding="utf-8")
            for operation in (
                "animate",
                "collision",
                "export_glb",
                "material_bake",
                "model_from_reference",
                "refine",
                "retopology",
                "rig",
                "uv_unwrap",
            ):
                with (
                    self.subTest(operation=operation),
                    self.assertRaisesRegex(
                        AssetContractError,
                        "requires parent-produced inputs",
                    ),
                ):
                    create_production_request(
                        root,
                        "specs/hero_visual.json",
                        root / f"requests/{operation}.json",
                        request_id=f"hero_{operation}",
                        route="openai",
                        executor="blender_mcp",
                        operation=operation,
                        reviewed_script_file="recipes/blender/stage.py",
                    )

    def test_invalid_parent_receipt_cannot_preapprove_blender_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root, representation="3d")
            reference_request = root / "requests/reference_parent.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                reference_request,
                request_id="reference_parent",
                route="openai",
                executor="openai_image",
                operation="concept_reference",
                parameters={"model": "gpt-image-2-2026-04-21"},
                expected_outputs=[{"role": "preview", "media_type": "image/png"}],
            )
            reference_receipt_path = _receipt(
                root,
                reference_request,
                executor="openai_image",
            )
            self.assertEqual(
                [],
                validate_production_receipt(reference_receipt_path, asset_root=root),
            )
            forged = json.loads(reference_receipt_path.read_text(encoding="utf-8"))
            forged["toolchain"]["requested_model"] = "different-unapproved-model"
            forged = bind_content_hash(forged)
            reference_receipt_path.write_text(json.dumps(forged), encoding="utf-8")
            script = root / "recipes/blender/model.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# reviewed model\n", encoding="utf-8")

            with self.assertRaisesRegex(AssetContractError, "cannot resolve parent receipt"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/model_from_forged_parent.json",
                    request_id="model_from_forged_parent",
                    route="openai",
                    executor="blender_mcp",
                    operation="model_from_reference",
                    inputs=[("reference", "generated/hero.png")],
                    parent_receipt_hashes=[forged["content_hash"]],
                    reviewed_script_file="recipes/blender/model.py",
                )

    def test_invalid_grandparent_cannot_preapprove_blender_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_request_path = _request(root, executor="blender_mcp")
            model_receipt_path = _receipt(
                root,
                model_request_path,
                executor="blender_mcp",
            )
            model_receipt = json.loads(model_receipt_path.read_text(encoding="utf-8"))

            reference_receipt_path = root / "receipts/openai_image.json"
            forged_reference = json.loads(reference_receipt_path.read_text(encoding="utf-8"))
            forged_reference["toolchain"]["requested_model"] = "unapproved-grandparent"
            reference_receipt_path.write_text(
                json.dumps(bind_content_hash(forged_reference)),
                encoding="utf-8",
            )

            script = root / "recipes/blender/rig.py"
            script.write_text("# reviewed rig\n", encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "cannot resolve parent receipt"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/rig_from_invalid_chain.json",
                    request_id="rig_from_invalid_chain",
                    route="openai",
                    executor="blender_mcp",
                    operation="rig",
                    inputs=[("model", "generated/hero.glb")],
                    parent_receipt_hashes=[model_receipt["content_hash"]],
                    reviewed_script_file="recipes/blender/rig.py",
                )

    def test_modly_request_binds_evidence_and_receipt_identity_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root)
            with self.assertRaisesRegex(AssetContractError, "missing fields"):
                create_production_request(
                    root,
                    "specs/hero_visual.json",
                    root / "requests/modly_incomplete.json",
                    request_id="modly_incomplete",
                    route="modly",
                    executor="modly_cli_mcp",
                    operation="workflow_run",
                    parameters={"capability_id": "example/image-to-mesh"},
                )

            request = _request(root, executor="modly_cli_mcp", route="modly")
            self.assertEqual([], validate_production_request(request, asset_root=root))
            receipt = _receipt(root, request, executor="modly_cli_mcp")
            self.assertEqual([], validate_production_receipt(receipt, asset_root=root))

            raw = json.loads(receipt.read_text(encoding="utf-8"))
            raw["toolchain"]["extension"]["version"] = "unapproved-version"
            receipt.write_text(json.dumps(bind_content_hash(raw)), encoding="utf-8")
            issues = validate_production_receipt(receipt, asset_root=root)
            self.assertTrue(
                any(
                    issue.path == "toolchain/extension"
                    and "approved before execution" in issue.message
                    for issue in issues
                ),
                issues,
            )

            discovery = root / "evidence/modly-discovery.json"
            discovery.write_text('{"support_state":"changed"}', encoding="utf-8")
            issues = validate_production_request(request, asset_root=root)
            self.assertTrue(
                any(
                    "capability_discovery" in issue.path and "SHA-256" in issue.message
                    for issue in issues
                ),
                issues,
            )

    def test_blender_receipt_requires_telemetry_and_explicit_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = _request(root, executor="blender_mcp")
            receipt = _receipt(root, request, executor="blender_mcp")
            self.assertEqual([], validate_production_receipt(receipt, asset_root=root))
            raw = json.loads(receipt.read_text(encoding="utf-8"))
            raw["toolchain"]["telemetry_disabled"] = False
            receipt.write_text(json.dumps(bind_content_hash(raw)), encoding="utf-8")
            messages = [
                issue.message for issue in validate_production_receipt(receipt, asset_root=root)
            ]
            self.assertIn("must be true", messages)

    def test_blender_receipt_reads_only_the_file_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _spec(root, representation="3d")
            reference_request = root / "requests/reference_for_blender.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                reference_request,
                request_id="hero_reference_for_blender_0001",
                route="openai",
                executor="openai_image",
                operation="concept_reference",
                parameters={
                    "model": "gpt-image-2-2026-04-21",
                    "background": "opaque",
                    "size": "1024x1024",
                },
                expected_outputs=[{"role": "preview", "media_type": "image/png"}],
            )
            reference_receipt = _receipt(root, reference_request, executor="openai_image")
            parent_hash = json.loads(reference_receipt.read_text(encoding="utf-8"))["content_hash"]
            script = root / "recipes/blender/model.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# reviewed fixture\n", encoding="utf-8")
            request = root / "requests/blender_source.json"
            create_production_request(
                root,
                "specs/hero_visual.json",
                request,
                request_id="hero_blender_source_0001",
                route="openai",
                executor="blender_mcp",
                operation="model_from_reference",
                inputs=[("reference", "generated/hero.png")],
                parent_receipt_hashes=[parent_hash],
                parameters={"quality": "reviewed"},
                expected_outputs=[
                    {"role": "authoring_source", "media_type": "application/x-blender"}
                ],
                reviewed_script_file="recipes/blender/model.py",
            )
            receipt = _receipt(root, request, executor="blender_mcp")

            with patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("full-file read is forbidden for Blender validation"),
            ):
                self.assertEqual([], validate_production_receipt(receipt, asset_root=root))

    def test_modly_receipt_fails_closed_without_live_supported_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = _request(root, executor="modly_cli_mcp", route="modly")
            receipt = _receipt(root, request, executor="modly_cli_mcp")
            self.assertEqual([], validate_production_receipt(receipt, asset_root=root))
            raw = json.loads(receipt.read_text(encoding="utf-8"))
            raw["toolchain"]["support_state"] = "known_but_unavailable"
            receipt.write_text(json.dumps(bind_content_hash(raw)), encoding="utf-8")
            messages = [
                issue.message for issue in validate_production_receipt(receipt, asset_root=root)
            ]
            self.assertTrue(any("must report supported" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
