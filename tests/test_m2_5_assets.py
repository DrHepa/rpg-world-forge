from __future__ import annotations

import binascii
import hashlib
import json
import struct
import tempfile
import unittest
import wave
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from isoworld.render.render_state import build_render_state
from isoworld.render.resources import RaylibAssetRegistry, ResourceError
from isoworld.world.state import DomainEvent, initial_world_state
from worldforge.assets import AssetManifestError, init_asset_manifest, validate_asset_manifest
from worldforge.renderpack import build_renderpack
from worldforge.workflow import PHASES, complete_phase, initial_status, reopen_phase

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))
    )


def _write_png(path: Path, width: int = 64, height: int = 64) -> None:
    row = b"\x00" + bytes((120, 90, 160, 255)) * width
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(row * height))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22050)
        target.writeframes(b"\x00\x00" * 64)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_spec(root: Path, asset_id: str, kind: str) -> str:
    relative = f"specs/{asset_id}.json"
    path = root / relative
    technical: dict[str, object] = {
        "runtime_format": "wav" if kind in {"music", "sfx"} else "png",
        "memory_budget_bytes": 1048576,
    }
    if kind in {"portrait", "sprite", "spritesheet", "tileset", "ui", "vfx"}:
        technical.update({"width": 64, "height": 64})
    if kind in {"music", "sfx"}:
        technical.update({"sample_rate": 22050, "channels": 1})
    path.write_text(
        json.dumps(
            {
                "format": "rpg-world-forge.asset_spec",
                "format_version": 1,
                "id": asset_id,
                "kind": kind,
                "purpose": "Neutral integration fixture",
                "acceptance_criteria": ["Loads through the runtime renderpack"],
                "technical": technical,
            }
        ),
        encoding="utf-8",
    )
    return relative


def _license() -> dict[str, str]:
    return {
        "asset_license": "CC0-1.0",
        "source_license": "CC0-1.0",
        "model_license": "not_applicable",
        "weights_license": "not_applicable",
        "dataset_license": "not_applicable",
    }


def _release_manifest(directory: Path) -> Path:
    manifest_path = directory / "assets/manifest.json"
    init_asset_manifest(COMPILED, manifest_path)
    asset_root = manifest_path.parent
    texture = asset_root / "processed/neutral_atlas.png"
    clipset = asset_root / "processed/neutral_atlas.clips.json"
    audio = asset_root / "processed/neutral.wav"
    _write_png(texture)
    clipset.write_text(
        json.dumps(
            {
                "format": "isoworld.clipset",
                "format_version": 1,
                "clips": [
                    {
                        "id": "tile",
                        "pivot": [32, 16],
                        "loop": True,
                        "frames": [
                            {"x": 0, "y": 0, "width": 64, "height": 32, "duration_ticks": 1}
                        ],
                    },
                    {
                        "id": "actor_idle",
                        "pivot": [16, 60],
                        "loop": True,
                        "frames": [
                            {"x": 0, "y": 0, "width": 32, "height": 64, "duration_ticks": 4}
                        ],
                    },
                    {
                        "id": "actor_walk",
                        "pivot": [16, 60],
                        "loop": True,
                        "frames": [
                            {"x": 32, "y": 0, "width": 32, "height": 64, "duration_ticks": 2}
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_wav(audio)
    qa_report = asset_root / "qa/integration.md"
    qa_report.parent.mkdir(parents=True, exist_ok=True)
    qa_report.write_text("Validated in the neutral raylib integration fixture.\n", encoding="utf-8")
    qa = {
        "report_file": "qa/integration.md",
        "in_engine_passed": True,
        "raylib_load_passed": True,
    }
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["phase"] = "release"
    raw["assets"] = [
        {
            "id": "neutral_atlas",
            "kind": "spritesheet",
            "status": "processed",
            "specification_file": _write_spec(asset_root, "neutral_atlas", "spritesheet"),
            "provenance": {"origin": "human"},
            "license": _license(),
            "approved_by": "authorized_lead",
            "qa": qa,
            "outputs": [
                {
                    "role": "texture",
                    "runtime_file": "processed/neutral_atlas.png",
                    "sha256": _sha256(texture),
                    "media_type": "image/png",
                },
                {
                    "role": "clipset",
                    "runtime_file": "processed/neutral_atlas.clips.json",
                    "sha256": _sha256(clipset),
                    "media_type": "application/json",
                },
            ],
        },
        {
            "id": "neutral_sfx",
            "kind": "sfx",
            "status": "processed",
            "specification_file": _write_spec(asset_root, "neutral_sfx", "sfx"),
            "provenance": {"origin": "procedural"},
            "license": _license(),
            "approved_by": "authorized_lead",
            "qa": qa,
            "outputs": [
                {
                    "role": "audio",
                    "runtime_file": "processed/neutral.wav",
                    "sha256": _sha256(audio),
                    "media_type": "audio/wav",
                }
            ],
        },
        {
            "id": "neutral_music",
            "kind": "music",
            "status": "processed",
            "specification_file": _write_spec(asset_root, "neutral_music", "music"),
            "provenance": {"origin": "procedural"},
            "license": _license(),
            "approved_by": "authorized_lead",
            "qa": qa,
            "outputs": [
                {
                    "role": "audio",
                    "runtime_file": "processed/neutral.wav",
                    "sha256": _sha256(audio),
                    "media_type": "audio/wav",
                }
            ],
        },
    ]
    pack = json.loads(COMPILED.read_text(encoding="utf-8"))
    raw["bindings"] = [
        *(
            {
                "slot": f"actor:{item['id']}",
                "asset_id": "neutral_atlas",
                "clip": "actor_idle",
                "moving_clip": "actor_walk",
            }
            for item in pack["collections"]["actors"]
        ),
        *(
            {
                "slot": f"tile_type:{item['id']}",
                "asset_id": "neutral_atlas",
                "clip": "tile",
            }
            for item in pack["collections"]["tile_types"]
        ),
        {
            "slot": "event:interaction_completed",
            "asset_id": "neutral_sfx",
        },
        {"slot": "music:default", "asset_id": "neutral_music"},
    ]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    return manifest_path


class _FakePyray:
    WHITE = "white"

    def __init__(self, texture_size: int = 64, *, valid_texture: bool = True) -> None:
        self.calls: list[tuple[str, object]] = []
        self.texture_size = texture_size
        self.valid_texture = valid_texture

    def init_audio_device(self) -> None:
        self.calls.append(("init_audio", None))

    def close_audio_device(self) -> None:
        self.calls.append(("close_audio", None))

    def load_texture(self, path: str):
        return SimpleNamespace(path=path, width=self.texture_size, height=self.texture_size)

    def is_texture_valid(self, _value) -> bool:
        return self.valid_texture

    def unload_texture(self, value) -> None:
        self.calls.append(("unload_texture", value.path))

    def load_sound(self, path: str):
        return SimpleNamespace(path=path)

    def unload_sound(self, value) -> None:
        self.calls.append(("unload_sound", value.path))

    def load_music_stream(self, path: str):
        return SimpleNamespace(path=path)

    def unload_music_stream(self, value) -> None:
        self.calls.append(("unload_music", value.path))

    def play_sound(self, value) -> None:
        self.calls.append(("play_sound", value.path))

    def play_music_stream(self, value) -> None:
        self.calls.append(("play_music", value.path))

    def update_music_stream(self, value) -> None:
        self.calls.append(("update_music", value.path))

    def stop_music_stream(self, value) -> None:
        self.calls.append(("stop_music", value.path))

    def Rectangle(self, x, y, width, height):
        return SimpleNamespace(x=x, y=y, width=width, height=height)

    def Vector2(self, x, y):
        return SimpleNamespace(x=x, y=y)

    def draw_texture_pro(self, texture, source, destination, origin, rotation, tint) -> None:
        self.calls.append(("draw_texture", texture.path))


class M25AssetTests(unittest.TestCase):
    def test_asset_initialization_rejects_tampered_worldpack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.loads(COMPILED.read_text(encoding="utf-8"))
            raw["world"]["title"] = "Tampered"
            tampered = root / "tampered.worldpack.json"
            tampered.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(AssetManifestError, "does not match its contents"):
                init_asset_manifest(tampered, root / "assets/manifest.json")

    def test_local_models_require_a_modly_extension_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest)
            asset_root = manifest.parent
            _write_spec(asset_root, "local_candidate", "sprite")
            (asset_root / "recipes/local.json").write_text("{}", encoding="utf-8")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["generation_policy"]["enabled_routes"] = ["openai", "modly"]
            raw["assets"] = [
                {
                    "id": "local_candidate",
                    "kind": "sprite",
                    "status": "generated",
                    "specification_file": "specs/local_candidate.json",
                    "provenance": {
                        "origin": "local_model",
                        "model_id": "local/example",
                        "model_version": "1",
                        "recipe_file": "recipes/local.json",
                        "generation_route": "openai",
                    },
                }
            ]
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            messages = [issue.message for issue in validate_asset_manifest(manifest)]
            self.assertIn("local models must run through the modly route", messages)
            self.assertEqual(3, messages.count("required for the Modly route"))

    def test_openai_and_complete_modly_routes_validate_in_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest)
            asset_root = manifest.parent
            for asset_id in ("openai_candidate", "modly_candidate"):
                _write_spec(asset_root, asset_id, "sprite")
            (asset_root / "recipes/openai.json").write_text("{}", encoding="utf-8")
            (asset_root / "recipes/modly.json").write_text("{}", encoding="utf-8")
            (asset_root / "recipes/modly.workflow.json").write_text("{}", encoding="utf-8")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["generation_policy"]["enabled_routes"] = ["openai", "modly"]
            raw["assets"] = [
                {
                    "id": "openai_candidate",
                    "kind": "sprite",
                    "status": "generated",
                    "specification_file": "specs/openai_candidate.json",
                    "provenance": {
                        "origin": "gpt_image",
                        "model_id": "gpt-image",
                        "model_version": "recorded-version",
                        "recipe_file": "recipes/openai.json",
                        "generation_route": "openai",
                    },
                },
                {
                    "id": "modly_candidate",
                    "kind": "sprite",
                    "status": "generated",
                    "specification_file": "specs/modly_candidate.json",
                    "provenance": {
                        "origin": "local_model",
                        "model_id": "local/example",
                        "model_version": "recorded-revision",
                        "recipe_file": "recipes/modly.json",
                        "generation_route": "modly",
                        "extension_id": "example-extension",
                        "extension_version": "1.0.0",
                        "workflow_file": "recipes/modly.workflow.json",
                    },
                },
            ]
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual([], validate_asset_manifest(manifest))

    def test_manifest_defaults_to_openai_and_can_opt_into_modly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(["openai"], raw["generation_policy"]["enabled_routes"])
            raw["generation_policy"]["enabled_routes"] = ["openai", "modly"]
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual([], validate_asset_manifest(manifest))

    def test_malformed_manifest_values_return_issues_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest)
            asset_root = manifest.parent
            _write_spec(asset_root, "malformed_asset", "sprite")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["assets"] = [
                {
                    "id": "malformed_asset",
                    "kind": "sprite",
                    "status": "planned",
                    "specification_file": "specs/malformed_asset.json",
                    "outputs": [
                        {
                            "role": [],
                            "runtime_file": "processed/missing.png",
                            "sha256": "0" * 64,
                            "media_type": [],
                        }
                    ],
                }
            ]
            manifest.write_text(json.dumps(raw), encoding="utf-8")

            messages = [issue.message for issue in validate_asset_manifest(manifest)]

            self.assertIn("unknown output role", messages)
            self.assertIn("unknown media type", messages)
            self.assertIn("processed file is missing", messages)

    def test_release_builds_runtime_only_renderpack_and_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            self.assertEqual(
                [],
                validate_asset_manifest(manifest, profile="release", worldpack_path=COMPILED),
            )
            output = root / "bundle/renderpack.json"
            payload = build_renderpack(manifest, COMPILED, output)
            serialized = json.dumps(payload)
            self.assertNotIn("provenance", serialized)
            self.assertNotIn("license", serialized)
            self.assertNotIn("approved_by", serialized)
            pack = load_worldpack(COMPILED)
            renderpack = load_renderpack(output, pack)
            self.assertIsNotNone(renderpack.binding("actor:explorer"))

            fake = _FakePyray()
            registry = RaylibAssetRegistry(fake, renderpack)
            registry.load()
            actor_binding = registry.binding("actor:explorer")
            assert actor_binding is not None
            self.assertTrue(
                registry.draw_binding(
                    actor_binding,
                    anchor_x=10,
                    anchor_y=20,
                    tick=3,
                    moving=True,
                )
            )
            state = replace(
                initial_world_state(pack),
                recent_events=(DomainEvent("interaction_completed", "explorer", "stone"),),
            )
            snapshot = build_render_state(state, pack, revision=1)
            registry.sync_audio(snapshot)
            registry.close()
            names = [name for name, _ in fake.calls]
            self.assertIn("draw_texture", names)
            self.assertIn("play_sound", names)
            self.assertIn("play_music", names)
            self.assertIn("unload_texture", names)
            self.assertIn("close_audio", names)

    def test_registry_rejects_clip_rectangles_outside_loaded_texture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            output = root / "bundle/renderpack.json"
            build_renderpack(manifest, COMPILED, output)
            fake = _FakePyray(texture_size=16)
            registry = RaylibAssetRegistry(
                fake,
                load_renderpack(output, load_worldpack(COMPILED)),
            )
            with self.assertRaisesRegex(ResourceError, "exceeds its 16x16 texture"):
                registry.load()
            names = [name for name, _ in fake.calls]
            self.assertIn("unload_texture", names)
            self.assertIn("close_audio", names)

    def test_registry_rejects_an_invalid_raylib_handle_and_closes_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            output = root / "bundle/renderpack.json"
            build_renderpack(manifest, COMPILED, output)
            fake = _FakePyray(valid_texture=False)
            registry = RaylibAssetRegistry(
                fake,
                load_renderpack(output, load_worldpack(COMPILED)),
            )

            with self.assertRaisesRegex(ResourceError, "raylib rejected texture neutral_atlas"):
                registry.load()

            self.assertIn("close_audio", [name for name, _ in fake.calls])

    def test_release_rejects_media_type_that_does_not_match_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            texture = manifest.parent / "processed/neutral_atlas.png"
            texture.write_text("not a PNG", encoding="utf-8")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["assets"][0]["outputs"][0]["sha256"] = _sha256(texture)
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            messages = [
                issue.message
                for issue in validate_asset_manifest(
                    manifest,
                    profile="release",
                    worldpack_path=COMPILED,
                )
            ]
            self.assertIn("does not match the file contents", messages)

    def test_release_rejects_media_type_incompatible_with_output_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["assets"][0]["outputs"][0]["media_type"] = "audio/wav"
            manifest.write_text(json.dumps(raw), encoding="utf-8")

            messages = [
                issue.message
                for issue in validate_asset_manifest(
                    manifest,
                    profile="release",
                    worldpack_path=COMPILED,
                )
            ]

            self.assertIn("media type audio/wav is incompatible with the texture role", messages)

    def test_release_checks_processed_dimensions_against_specification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            texture = manifest.parent / "processed/neutral_atlas.png"
            _write_png(texture, width=32, height=32)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            raw["assets"][0]["outputs"][0]["sha256"] = _sha256(texture)
            manifest.write_text(json.dumps(raw), encoding="utf-8")
            messages = [
                issue.message
                for issue in validate_asset_manifest(
                    manifest,
                    profile="release",
                    worldpack_path=COMPILED,
                )
            ]
            self.assertIn("PNG dimensions (32, 32) do not match specification (64, 64)", messages)

    def test_renderpack_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            output = root / "bundle/renderpack.json"
            build_renderpack(manifest, COMPILED, output)
            raw = json.loads(output.read_text(encoding="utf-8"))
            raw["bindings"][0]["scale"] = 2
            output.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(RenderPackError, "hash does not match"):
                load_renderpack(output, load_worldpack(COMPILED))

    def test_p13_records_renderpack_and_reopen_invalidates_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _release_manifest(root)
            worldpack_path = root / "build/foundation.worldpack.json"
            worldpack_path.parent.mkdir(parents=True, exist_ok=True)
            worldpack_path.write_bytes(COMPILED.read_bytes())
            renderpack_path = root / "build/runtime/renderpack.json"
            build_renderpack(manifest, worldpack_path, renderpack_path)

            status = initial_status("foundation_slice")
            status.update(
                {
                    "current_phase": "p13_asset_production",
                    "completed_phases": [phase.id for phase in PHASES[:13]],
                    "canon_locked": True,
                    "worldpack_hash": load_worldpack(worldpack_path).content_hash,
                    "worldpack_path": "build/foundation.worldpack.json",
                }
            )
            status_path = root / ".worldforge/status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(json.dumps(status), encoding="utf-8")
            report = root / "p13.json"
            report.write_text(
                json.dumps(
                    {
                        "format": "rpg-world-forge.phase_report",
                        "format_version": 1,
                        "phase": "p13_asset_production",
                        "status": "ready",
                        "summary": "Processed assets compile and load.",
                        "deliverables": [
                            "assets/manifest.json",
                            "build/runtime/renderpack.json",
                        ],
                        "decisions": [],
                        "blockers": [],
                        "validations": [
                            {"name": "renderpack", "passed": True, "evidence": "runtime load"}
                        ],
                        "reviewed_by": "authorized_lead",
                        "asset_manifest_path": "assets/manifest.json",
                        "renderpack_path": "build/runtime/renderpack.json",
                    }
                ),
                encoding="utf-8",
            )
            completed = complete_phase(root, report)
            self.assertEqual("build/runtime/renderpack.json", completed["renderpack"])
            reopened = reopen_phase(
                root,
                "p12_asset_specs",
                reason="Change presentation requirements",
                approved_by="authorized_lead",
            )
            self.assertIsNone(reopened["asset_manifest"])
            self.assertIsNone(reopened["renderpack"])


if __name__ == "__main__":
    unittest.main()
