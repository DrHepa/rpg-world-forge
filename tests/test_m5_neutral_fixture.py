from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import load_renderpack
from scripts.generate_m5_neutral import generate as generate_neutral_fixture
from worldforge.asset_manifest_v3 import finalize_asset_release
from worldforge.assetpack import build_assetpack, verify_assetpack
from worldforge.assets import validate_asset_manifest
from worldforge.contract_catalog import audit_contracts
from worldforge.renderpack import build_renderpack

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/m5-neutral"
SCRIPT = ROOT / "scripts/generate_m5_neutral.py"
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_map(root: Path) -> dict[str, dict[str, int | str]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _lock_map(lock: dict[str, object]) -> dict[str, dict[str, int | str]]:
    return {
        item["path"]: {"sha256": item["sha256"], "size": item["size"]} for item in lock["files"]
    }


def _run_generator(target: Path) -> Path:
    generate_neutral_fixture(target, allow_repo=False)
    return target / "m5-neutral"


def _read_glb(path: Path) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    if len(raw) < 12:
        raise ValueError("GLB is shorter than its header")
    magic, version, declared_length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or declared_length != len(raw):
        raise ValueError("GLB header is invalid")
    offset = 12
    chunks: dict[int, bytes] = {}
    while offset < len(raw):
        if offset % 4 or offset + 8 > len(raw):
            raise ValueError("GLB chunk header is not aligned or complete")
        length, chunk_type = struct.unpack_from("<II", raw, offset)
        offset += 8
        if length % 4 or offset + length > len(raw):
            raise ValueError("GLB chunk is not aligned or complete")
        if chunk_type in chunks:
            raise ValueError("GLB contains duplicate chunk types")
        chunks[chunk_type] = raw[offset : offset + length]
        offset += length
    if offset != len(raw) or 0x4E4F534A not in chunks or 0x004E4942 not in chunks:
        raise ValueError("GLB must contain one JSON chunk and one BIN chunk")
    document = json.loads(chunks[0x4E4F534A].rstrip(b" ").decode("utf-8"))
    return document, chunks[0x004E4942]


def _decode_accessor(
    document: dict[str, object], binary: bytes, accessor_index: int
) -> list[int | tuple[float, ...]]:
    accessors = document["accessors"]
    buffer_views = document["bufferViews"]
    if not isinstance(accessors, list) or not isinstance(buffer_views, list):
        raise ValueError("GLB accessors and bufferViews must be arrays")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise ValueError("GLB accessor must be an object")
    view = buffer_views[accessor["bufferView"]]
    if not isinstance(view, dict):
        raise ValueError("GLB bufferView must be an object")
    component_type = accessor["componentType"]
    value_type = accessor["type"]
    component = {5123: ("H", 2), 5126: ("f", 4)}.get(component_type)
    component_count = {"SCALAR": 1, "VEC3": 3}.get(value_type)
    if component is None or component_count is None:
        raise ValueError("test decoder received an unsupported accessor")
    component_format, component_size = component
    element_size = component_size * component_count
    stride = view.get("byteStride", element_size)
    if not isinstance(stride, int) or stride < element_size:
        raise ValueError("GLB accessor stride is invalid")
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    if not isinstance(start, int) or not isinstance(count, int):
        raise ValueError("GLB accessor offsets and counts must be integers")
    end = start + (count - 1) * stride + element_size if count else start
    view_end = view.get("byteOffset", 0) + view["byteLength"]
    if end > view_end or view_end > len(binary):
        raise ValueError("GLB accessor exceeds its bufferView")
    decoded: list[int | tuple[float, ...]] = []
    for index in range(count):
        values = struct.unpack_from(
            "<" + component_format * component_count,
            binary,
            start + index * stride,
        )
        decoded.append(values[0] if component_count == 1 else values)
    return decoded


class M5NeutralFixtureTests(unittest.TestCase):
    def test_committed_fixture_lock_is_self_consistent_and_regeneration_is_stable(self) -> None:
        lock = json.loads((FIXTURE / "fixture.lock.json").read_text(encoding="utf-8"))
        locked = _lock_map(lock)
        actual = _file_map(FIXTURE)
        expected_paths = set(locked) | {"fixture.lock.json"}
        self.assertEqual(lock["worldpack_anchor"]["sha256"], _sha256(WORLDPACK))
        self.assertEqual(expected_paths, set(actual))
        for relative, expected in locked.items():
            self.assertEqual(expected, actual[relative], relative)

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_fixture = _run_generator(Path(first) / "out")
            second_fixture = _run_generator(Path(second) / "out")
            first_map = _file_map(first_fixture)
            second_map = _file_map(second_fixture)
            self.assertEqual(first_map, second_map)
            self.assertEqual(expected_paths, set(first_map))

    def test_committed_synthetic_font_loads_and_renders_a_glyph(self) -> None:
        try:
            from PIL import ImageFont
        except ImportError as exc:
            raise unittest.SkipTest("Pillow is required for TrueType consumer validation") from exc

        for relative in (
            "renderpack/generated/neutral_font.ttf",
            "renderpack/processed/neutral_font/neutral_font.ttf",
        ):
            with self.subTest(relative=relative):
                font = ImageFont.truetype(str(FIXTURE / relative), size=24)
                mask = font.getmask("A")
                self.assertIsNotNone(mask.getbbox())
                self.assertGreater(sum(mask), 0)

    def test_committed_glb_decodes_to_one_nondegenerate_triangle(self) -> None:
        generated = FIXTURE / "assetpack/generated/neutral_actor_3d.glb"
        processed = FIXTURE / "assetpack/processed/neutral_actor_3d/neutral_actor_3d.glb"
        self.assertEqual(_sha256(generated), _sha256(processed))
        for path in (generated, processed):
            with self.subTest(path=path.relative_to(FIXTURE).as_posix()):
                self._assert_triangle_glb(path)

        receipt = json.loads(
            (FIXTURE / "assetpack/processed/neutral_actor_3d/processing.receipt.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"file": "generated/neutral_actor_3d.glb", "sha256": _sha256(generated)},
            receipt["inputs"][0]["artifact"],
        )
        details = receipt["outputs"][0]["details"]
        self.assertGreater(details["bin_chunk_bytes"], 0)
        self.assertEqual(3, details["metrics"]["vertices"])
        self.assertEqual(1, details["metrics"]["triangles"])

    def test_committed_procedural_glb_uses_direct_export_lineage(self) -> None:
        request = json.loads(
            (FIXTURE / "assetpack/requests/neutral_actor_3d.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (FIXTURE / "assetpack/receipts/neutral_actor_3d.json").read_text(encoding="utf-8")
        )

        self.assertEqual("procedural", request["executor"])
        self.assertEqual("export_glb", request["operation"])
        self.assertEqual([], request["inputs"])
        self.assertEqual([], request["parent_receipt_hashes"])
        self.assertEqual(
            {
                "deterministic": True,
                "fixture": "m5-neutral",
                "source": "direct_procedural_triangle_geometry",
            },
            request["parameters"],
        )
        self.assertEqual("export_glb", receipt["operation"])
        self.assertEqual([], receipt["parent_receipt_hashes"])
        self.assertNotEqual("model_from_reference", request["operation"])

    def test_generator_refuses_repository_targets_by_default(self) -> None:
        blocked = subprocess.run(
            [sys.executable, str(SCRIPT), "--target", str(ROOT / "examples/blocked-neutral")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("refusing to write inside the repository", blocked.stderr)
        self.assertFalse((ROOT / "examples/blocked-neutral").exists())

    def test_regenerated_manifests_build_verify_load_finalize_and_preserve_atlas_lineage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_fixture = _run_generator(Path(directory) / "out")
            loaded_world = load_worldpack(WORLDPACK)
            self.assertEqual("foundation_slice", loaded_world.world_id)

            self._assert_atlas_lineage(temp_fixture / "renderpack")
            render_root = temp_fixture / "renderpack"
            render_manifest = render_root / "manifest.json"
            self.assertEqual(
                [],
                validate_asset_manifest(render_manifest, profile="build", worldpack_path=WORLDPACK),
            )
            render_output = render_root / "build/renderpack.json"
            built_render = build_renderpack(render_manifest, WORLDPACK, render_output)
            loaded_render = load_renderpack(render_output, loaded_world)
            self.assertEqual(built_render["content_hash"], loaded_render.content_hash)
            self.assertEqual(
                {
                    "neutral_font",
                    "neutral_fragment_shader",
                    "neutral_sheet",
                    "neutral_sfx",
                    "neutral_vertex_shader",
                },
                {asset.id for asset in loaded_render.assets},
            )
            render_manifest_hash = json.loads(render_manifest.read_text(encoding="utf-8"))[
                "content_hash"
            ]
            finalize_asset_release(
                render_manifest,
                render_output,
                WORLDPACK,
                expected_manifest_hash=render_manifest_hash,
            )
            self.assertEqual(
                [],
                validate_asset_manifest(
                    render_manifest, profile="release", worldpack_path=WORLDPACK
                ),
            )

            asset_root = temp_fixture / "assetpack"
            asset_manifest = asset_root / "manifest.json"
            self.assertEqual(
                [],
                validate_asset_manifest(asset_manifest, profile="build", worldpack_path=WORLDPACK),
            )
            asset_output = asset_root / "build/assetpack.json"
            built_assetpack = build_assetpack(asset_manifest, WORLDPACK, asset_output)
            verified_assetpack = verify_assetpack(asset_output, WORLDPACK)
            self.assertEqual(built_assetpack, verified_assetpack)
            asset_manifest_hash = json.loads(asset_manifest.read_text(encoding="utf-8"))[
                "content_hash"
            ]
            finalize_asset_release(
                asset_manifest,
                asset_output,
                WORLDPACK,
                expected_manifest_hash=asset_manifest_hash,
            )
            self.assertEqual(
                [],
                validate_asset_manifest(
                    asset_manifest, profile="release", worldpack_path=WORLDPACK
                ),
            )

    def test_catalog_source_audit_includes_neutral_fixture_traces(self) -> None:
        result = audit_contracts(ROOT)
        self.assertEqual("source", result.mode)

    def _assert_atlas_lineage(self, render_root: Path) -> None:
        try:
            import PIL
            from PIL import Image
        except ImportError as exc:
            raise unittest.SkipTest("Pillow is required for atlas lineage inspection") from exc

        manifest = json.loads((render_root / "manifest.json").read_text(encoding="utf-8"))
        sheet = next(asset for asset in manifest["assets"] if asset["id"] == "neutral_sheet")
        selected = {(item["file"], item["sha256"]) for item in sheet["selected_candidates"]}
        receipt = json.loads(
            (render_root / sheet["processing_receipt"]["file"]).read_text(encoding="utf-8")
        )
        recorded_inputs = {
            (item["artifact"]["file"], item["artifact"]["sha256"]) for item in receipt["inputs"]
        }
        self.assertEqual(selected, recorded_inputs)
        self.assertEqual("worldforge.asset_processing", receipt["toolchain"]["processor"])
        self.assertEqual(str(PIL.__version__), receipt["toolchain"]["pillow_version"])

        with Image.open(render_root / "generated/neutral_sheet_idle.png") as idle_image:
            idle_pixels = idle_image.convert("RGBA").tobytes()
        with Image.open(render_root / "generated/neutral_sheet_walk.png") as walk_image:
            walk_pixels = walk_image.convert("RGBA").tobytes()
        with Image.open(render_root / "processed/neutral_sheet/neutral_sheet.png") as atlas_image:
            atlas = atlas_image.convert("RGBA")
            self.assertEqual((32, 16), atlas.size)
            self.assertEqual(idle_pixels, atlas.crop((0, 0, 16, 16)).tobytes())
            self.assertEqual(walk_pixels, atlas.crop((16, 0, 32, 16)).tobytes())

    def _assert_triangle_glb(self, path: Path) -> None:
        document, binary = _read_glb(path)
        buffers = document["buffers"]
        self.assertIsInstance(buffers, list)
        self.assertEqual(len(binary), buffers[0]["byteLength"])
        scene_index = document["scene"]
        scene = document["scenes"][scene_index]
        self.assertEqual([0], scene["nodes"])
        node = document["nodes"][0]
        primitive = document["meshes"][node["mesh"]]["primitives"][0]
        self.assertEqual(4, primitive["mode"])
        positions = _decode_accessor(document, binary, primitive["attributes"]["POSITION"])
        indices = _decode_accessor(document, binary, primitive["indices"])
        self.assertEqual(
            [(-0.5, 0.0, 0.0), (0.5, 0.0, 0.0), (0.0, 1.0, 0.0)],
            positions,
        )
        self.assertEqual([0, 1, 2], indices)
        a, b, c = positions
        ab = tuple(b[index] - a[index] for index in range(3))
        ac = tuple(c[index] - a[index] for index in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        self.assertGreater(sum(component * component for component in cross), 0.0)


if __name__ == "__main__":
    unittest.main()
