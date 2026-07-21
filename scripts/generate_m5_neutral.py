#!/usr/bin/env python3
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import math
import platform
import struct
import wave
import zlib
from pathlib import Path
from typing import Any

from worldforge.asset_processing import process_asset_recipe

REPO_ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = REPO_ROOT / "content/compiled/foundation.worldpack.json"
FIXTURE_NAME = "m5-neutral"
STAMP_START = "2026-07-21T00:00:00Z"
STAMP_DONE = "2026-07-21T00:00:01Z"


def canonical_hash(payload: dict[str, Any], field: str = "content_hash") -> str:
    raw = dict(payload)
    raw.pop(field, None)
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bind(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["content_hash"] = canonical_hash(result)
    return result


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_json(path: Path, payload: dict[str, Any], *, hashed: bool = True) -> dict[str, Any]:
    data = bind(payload) if hashed else payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(data))
    return data


def write_text_lf(path: Path, text: str) -> None:
    if "\r" in text:
        raise ValueError("generated text must use LF line endings")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(root: Path, path: Path | str) -> dict[str, Any]:
    rel = path if isinstance(path, str) else path.relative_to(root).as_posix()
    return {"file": rel, "sha256": sha(root / rel)}


def chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def png(path: Path, width: int, height: int, colors: list[tuple[int, int, int, int]]) -> None:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(colors[(x + y) % len(colors)])
    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
    payload += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def wav(path: Path) -> tuple[int, int, int, int]:
    rate = 8000
    frames = 800
    samples = []
    for i in range(frames):
        value = int(math.sin(2 * math.pi * 220 * i / rate) * 12000)
        samples.append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return rate, 1, frames, max(abs(v) for v in samples)


def sfnt_checksum(data: bytes | bytearray) -> int:
    padded = bytes(data) + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF


def simple_glyph(
    bounds: tuple[int, int, int, int],
    end_point: int,
    flags: bytes,
    x_coordinates: bytes,
    y_coordinates: bytes,
) -> bytes:
    glyph = struct.pack(">hhhhh", 1, *bounds)
    glyph += struct.pack(">HH", end_point, 0)
    glyph += flags + x_coordinates + y_coordinates
    return glyph + b"\0" * (len(glyph) % 2)


def ttf_bytes() -> bytes:
    notdef = simple_glyph(
        (50, 0, 550, 700),
        3,
        bytes((0x33, 0x21, 0x11, 0x21)),
        bytes((50,)) + struct.pack(">hh", 500, -500),
        struct.pack(">h", 700),
    )
    capital_a = simple_glyph(
        (50, 0, 550, 700),
        2,
        bytes((0x33, 0x13, 0x13)),
        bytes((50, 250, 250)),
        struct.pack(">hh", 700, -700),
    )
    glyf = notdef + capital_a

    cmap_subtable = struct.pack(">7H", 4, 32, 0, 4, 4, 1, 0)
    cmap_subtable += struct.pack(">2H", 0x0041, 0xFFFF)
    cmap_subtable += struct.pack(">H", 0)
    cmap_subtable += struct.pack(">2H", 0x0041, 0xFFFF)
    cmap_subtable += struct.pack(">2H", 0xFFC0, 1)
    cmap_subtable += struct.pack(">2H", 0, 0)
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_subtable

    name_values = (
        (1, "RWF Neutral"),
        (2, "Regular"),
        (4, "RWF Neutral Regular"),
        (6, "RWFNeutral-Regular"),
    )
    name_storage = bytearray()
    name_records = bytearray()
    for name_id, value in name_values:
        encoded = value.encode("utf-16-be")
        name_records.extend(
            struct.pack(">HHHHHH", 3, 1, 0x0409, name_id, len(encoded), len(name_storage))
        )
        name_storage.extend(encoded)
    name = (
        struct.pack(">HHH", 0, len(name_values), 6 + len(name_records))
        + name_records
        + name_storage
    )

    os2 = struct.pack(
        ">HhHHH11h",
        0,
        600,
        400,
        5,
        0,
        650,
        600,
        0,
        75,
        650,
        600,
        0,
        350,
        50,
        250,
        0,
    )
    os2 += bytes(10)
    os2 += struct.pack(
        ">4I4sHHHhhhHH",
        1,
        0,
        0,
        0,
        b"RWF ",
        0x0040,
        65,
        65,
        800,
        -200,
        0,
        800,
        200,
    )

    tables = {
        "OS/2": os2,
        "cmap": cmap,
        "glyf": glyf,
        "head": struct.pack(
            ">IIIIHHQQhhhhHHhhh",
            0x00010000,
            0x00010000,
            0,
            0x5F0F3CF5,
            0x000B,
            1000,
            0,
            0,
            50,
            0,
            550,
            700,
            0,
            8,
            2,
            0,
            0,
        ),
        "hhea": struct.pack(
            ">IhhhH11hH",
            0x00010000,
            800,
            -200,
            0,
            600,
            0,
            0,
            600,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            2,
        ),
        "hmtx": struct.pack(">HhHh", 600, 50, 600, 50),
        "loca": struct.pack(">3H", 0, len(notdef) // 2, len(glyf) // 2),
        "maxp": struct.pack(">I14H", 0x00010000, 2, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0),
        "name": bytes(name),
        "post": struct.pack(">IIhhIIIII", 0x00030000, 0, -75, 50, 0, 0, 0, 0, 0),
    }
    ordered = sorted(tables.items())
    table_count = len(ordered)
    max_power = 1 << (table_count.bit_length() - 1)
    search_range = max_power * 16
    entry_selector = max_power.bit_length() - 1
    range_shift = table_count * 16 - search_range
    offset = 12 + table_count * 16
    records: list[tuple[str, int, int, int]] = []
    for tag, data in ordered:
        offset += -offset % 4
        records.append((tag, sfnt_checksum(data), offset, len(data)))
        offset += len(data)

    font = bytearray(
        struct.pack(">IHHHH", 0x00010000, table_count, search_range, entry_selector, range_shift)
    )
    for tag, checksum, table_offset, length in records:
        font.extend(struct.pack(">4sIII", tag.encode("ascii"), checksum, table_offset, length))
    for (tag, data), (_, _, table_offset, _) in zip(ordered, records, strict=True):
        del tag
        font.extend(b"\0" * (table_offset - len(font)))
        font.extend(data)
    font.extend(b"\0" * (-len(font) % 4))

    head_offset = next(table_offset for tag, _, table_offset, _ in records if tag == "head")
    adjustment = (0xB1B0AFBA - sfnt_checksum(font)) & 0xFFFFFFFF
    struct.pack_into(">I", font, head_offset + 8, adjustment)
    return bytes(font)


def ttf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ttf_bytes())


def glsl(path: Path, body: str) -> None:
    write_text_lf(path, body)


def glb_bytes(document: dict[str, Any], binary: bytes) -> bytes:
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(raw), 0x4E4F534A) + raw
    chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def glb(path: Path) -> dict[str, Any]:
    positions = struct.pack("<9f", -0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0, 0.0)
    indices = struct.pack("<3H", 0, 1, 2)
    animation_time = struct.pack("<f", 0.0)
    animation_translation = struct.pack("<3f", 0.0, 0.0, 0.0)
    binary = positions + indices + b"\0\0" + animation_time + animation_translation
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "rwf-neutral-fixture"},
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "max": [0.5, 1.0, 0.0],
                "min": [-0.5, 0.0, 0.0],
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5123,
                "count": 3,
                "max": [2],
                "min": [0],
                "type": "SCALAR",
            },
            {
                "bufferView": 2,
                "componentType": 5126,
                "count": 1,
                "max": [0.0],
                "min": [0.0],
                "type": "SCALAR",
            },
            {"bufferView": 3, "componentType": 5126, "count": 1, "type": "VEC3"},
        ],
        "animations": [
            {
                "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
                "name": "idle",
                "samplers": [{"input": 2, "interpolation": "STEP", "output": 3}],
            }
        ],
        "bufferViews": [
            {"buffer": 0, "byteLength": 36, "byteOffset": 0, "target": 34962},
            {"buffer": 0, "byteLength": 6, "byteOffset": 36, "target": 34963},
            {"buffer": 0, "byteLength": 4, "byteOffset": 44},
            {"buffer": 0, "byteLength": 12, "byteOffset": 48},
        ],
        "buffers": [{"byteLength": len(binary)}],
        "extensionsRequired": ["KHR_materials_unlit"],
        "extensionsUsed": ["KHR_materials_unlit"],
        "materials": [{"extensions": {"KHR_materials_unlit": {}}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "nodes": [{"mesh": 0, "name": "Root"}],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(glb_bytes(doc, binary))
    raw_json = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "bin_chunk_bytes": len(binary),
        "byte_length": len(path.read_bytes()),
        "embedded_uris": 0,
        "extensions_required": ["KHR_materials_unlit"],
        "extensions_used": ["KHR_materials_unlit"],
        "external_uris": [],
        "json_chunk_bytes": len(raw_json) + (-len(raw_json) % 4),
        "max_texture_dimension": 0,
        "metrics": {
            "animations": 1,
            "bones": 0,
            "external_uris": 0,
            "influences": 0,
            "materials": 1,
            "meshes": 1,
            "nodes": 1,
            "skins": 0,
            "textures": 0,
            "triangles": 1,
            "vertices": 3,
        },
    }


def recipe(root: Path, path: str, operation: str, body: dict[str, Any]) -> dict[str, Any]:
    return write_json(
        root / path,
        {
            "format": "rpg-world-forge.asset_processing_recipe",
            "format_version": 1,
            "operation": operation,
            **body,
        },
    )


def processing_receipt(
    root: Path,
    out_dir: Path,
    operation: str,
    recipe_path: Path,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    toolchain: dict[str, Any],
) -> dict[str, Any]:
    return write_json(
        out_dir / "processing.receipt.json",
        {
            "format": "rpg-world-forge.asset_processing_receipt",
            "format_version": 1,
            "operation": operation,
            "recipe": {
                "content_hash": json.loads(recipe_path.read_text())["content_hash"],
                "sha256": sha(recipe_path),
            },
            "inputs": inputs,
            "outputs": outputs,
            "toolchain": toolchain,
        },
    )


def target(root: Path, world: dict[str, Any], target_id: str, dimension: str) -> dict[str, Any]:

    if dimension == "2_5d":
        coord = {
            "origin": "tile_anchor",
            "x_axis": "east",
            "y_axis": "south",
            "up_axis": "screen_up",
            "tile_width_pixels": 64,
            "tile_height_pixels": 32,
        }
        adapter = "isoworld_raylib_2_5d"
    elif dimension == "2d":
        coord = {"origin": "top_left", "x_axis": "right", "y_axis": "down", "pixels_per_unit": 32}
        adapter = "isoworld_raylib_2_5d"
    else:
        coord = {
            "handedness": "right",
            "up_axis": "Y",
            "forward_axis": "-Z",
            "units_per_meter": 1.0,
        }
        adapter = None
    return write_json(
        root / "target.json",
        {
            "format": "rpg-world-forge.asset_target",
            "format_version": 1,
            "id": target_id,
            "world_id": world["world"]["id"],
            "world_content_hash": world["content_hash"],
            "dimension": dimension,
            "delivery_profile": "assetpack_v1" if dimension == "3d" else "renderpack_v1",
            "runtime_adapter": adapter,
            "coordinate_system": coord,
        },
    )


def bibles(
    root: Path, world: dict[str, Any], tgt: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    common = {
        "format_version": 1,
        "world_id": world["world"]["id"],
        "world_content_hash": world["content_hash"],
        "target_id": tgt["id"],
        "target_hash": tgt["content_hash"],
        "acceptance_tests": ["loads"],
        "approved_by": "fixture",
    }
    visual = write_json(
        root / "bibles/visual.json",
        {
            **common,
            "format": "rpg-world-forge.visual_bible",
            "camera": {"projection": "orthographic"},
            "resolution": {"base": [16, 16]},
            "style": {"palette": ["#203040", "#6080a0"]},
            "silhouettes": {"minimum_separation": "one_pixel"},
            "animation": {"clock": "integer_ticks"},
            "ui": {"minimum_text_px": 12},
            "vfx": {"photosensitivity": "safe"},
        },
    )
    audio = write_json(
        root / "bibles/audio.json",
        {
            **common,
            "format": "rpg-world-forge.audio_bible",
            "format_policy": {"runtime": "wav", "sample_rate": 8000},
            "mix": {"peak_dbfs": -6},
            "timbral_families": ["neutral"],
            "ambience": {"layers": 1},
            "music": {"loop": False},
            "sfx": {"variations": 1},
        },
    )
    return visual, audio


def spec(
    root: Path,
    item: dict[str, Any],
    tgt: dict[str, Any],
    inv: dict[str, Any],
    visual: dict[str, Any],
    audio: dict[str, Any],
    technical: dict[str, Any],
    outputs: list[dict[str, str]],
) -> dict[str, Any]:
    return write_json(
        root / f"specs/{item['id']}.json",
        {
            "format": "rpg-world-forge.asset_spec",
            "format_version": 2,
            "id": item["id"],
            "kind": item["kind"],
            "representation": item["representation"],
            "target_id": tgt["id"],
            "target_hash": tgt["content_hash"],
            "inventory_hash": inv["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "purpose": item["purpose"],
            "canonical_sources": item["canonical_sources"],
            "acceptance_criteria": ["loads"],
            "semantic_slots": item["semantic_slots"],
            "technical": technical,
            "production": {"allowed_routes": ["openai"], "allowed_executors": ["procedural"]},
            "expected_outputs": outputs,
        },
    )


def production(
    root: Path,
    asset_id: str,
    spec_path: Path,
    req_id: str,
    operation: str,
    outputs: list[dict[str, Any]],
    expected: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sp = json.loads(spec_path.read_text())
    request = write_json(
        root / f"requests/{req_id}.json",
        {
            "format": "rpg-world-forge.asset_production_request",
            "format_version": 1,
            "id": req_id,
            "asset_id": asset_id,
            "specification": ref(root, spec_path),
            "target_id": sp["target_id"],
            "target_hash": sp["target_hash"],
            "orchestrator": "gpt",
            "route": "openai",
            "executor": "procedural",
            "operation": operation,
            "inputs": [],
            "parameters": {"deterministic": True, "fixture": FIXTURE_NAME},
            "expected_outputs": expected,
            "parent_receipt_hashes": [],
        },
    )
    receipt = write_json(
        root / f"receipts/{req_id}.json",
        {
            "format": "rpg-world-forge.asset_production_receipt",
            "format_version": 1,
            "id": f"receipt_{req_id}",
            "request": ref(root, f"requests/{req_id}.json"),
            "asset_id": asset_id,
            "route": "openai",
            "executor": "procedural",
            "operation": operation,
            "status": "succeeded",
            "started_at": STAMP_START,
            "completed_at": STAMP_DONE,
            "parent_receipt_hashes": [],
            "toolchain": {
                "generator": "scripts/generate_m5_neutral.py",
                "processor": "procedural_fixture_v1",
                "python_version": platform.python_version(),
                "network": "none",
            },
            "replayability": "deterministic_seeded",
            "outputs": outputs,
        },
    )
    return request, receipt


def license_qa(
    root: Path, asset_id: str, tgt: dict[str, Any], output_hashes: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = root / "evidence" / f"{asset_id}.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(evidence, f"Neutral procedural fixture evidence for {asset_id}.\n")
    notice = root / "evidence" / f"{asset_id}_NOTICE.txt"
    write_text_lf(notice, f"{asset_id}: CC0-1.0 self-generated fixture output.\n")
    lic = write_json(
        root / f"licenses/{asset_id}.json",
        {
            "format": "rpg-world-forge.asset_license_record",
            "format_version": 1,
            "asset_id": asset_id,
            "output_hashes": sorted(output_hashes),
            "components": [
                {
                    "scope": scope,
                    "license_expression": "CC0-1.0",
                    "redistribution": "permitted",
                    "evidence": ref(root, evidence),
                }
                for scope in ("asset", "dataset", "model", "output", "source", "weights")
            ],
            "notices": ref(root, notice),
            "approved_by": "fixture",
        },
    )
    qa = write_json(
        root / f"qa/{asset_id}.json",
        {
            "format": "rpg-world-forge.asset_qa_report",
            "format_version": 1,
            "asset_id": asset_id,
            "target_hash": tgt["content_hash"],
            "output_hashes": sorted(output_hashes),
            "checks": [{"id": "loads", "passed": True, "evidence": [ref(root, evidence)]}],
            "blockers": [],
            "approved_by": "fixture",
        },
    )
    return lic, qa


def build_render_fixture(base: Path, world: dict[str, Any]) -> None:
    root = base / "renderpack"
    tgt = target(root, world, "neutral_2_5d", "2_5d")
    visual, audio = bibles(root, world, tgt)
    reqs = [
        {
            "id": "neutral_font",
            "kind": "font",
            "representation": "2_5d",
            "required": False,
            "purpose": "Bounded runtime font fixture",
            "canonical_sources": ["world:foundation_slice"],
            "semantic_slots": ["ui:font"],
        },
        {
            "id": "neutral_fragment_shader",
            "kind": "shader",
            "representation": "2_5d",
            "required": False,
            "purpose": "Runtime fragment GLSL fixture",
            "canonical_sources": ["world:foundation_slice"],
            "semantic_slots": [],
        },
        {
            "id": "neutral_vertex_shader",
            "kind": "shader",
            "representation": "2_5d",
            "required": False,
            "purpose": "Runtime vertex GLSL fixture",
            "canonical_sources": ["world:foundation_slice"],
            "semantic_slots": [],
        },
        {
            "id": "neutral_sheet",
            "kind": "spritesheet",
            "representation": "2_5d",
            "required": True,
            "purpose": "Neutral actor spritesheet fixture",
            "canonical_sources": ["world:foundation_slice"],
            "semantic_slots": ["actor:neutral"],
        },
        {
            "id": "neutral_sfx",
            "kind": "sfx",
            "representation": "audio",
            "required": False,
            "purpose": "Short neutral WAV fixture",
            "canonical_sources": ["world:foundation_slice"],
            "semantic_slots": ["event:neutral"],
        },
    ]
    inv = write_json(
        root / "inventory/assets.json",
        {
            "format": "rpg-world-forge.asset_inventory",
            "format_version": 1,
            "world_id": world["world"]["id"],
            "world_content_hash": world["content_hash"],
            "target_id": tgt["id"],
            "target_hash": tgt["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "requirements": [reqs[3]],
            "manual_additions": [reqs[0], reqs[1], reqs[4], reqs[2]],
        },
    )
    assets = []
    bindings = []
    # spritesheet
    sheet = reqs[3]
    spec(
        root,
        sheet,
        tgt,
        inv,
        visual,
        audio,
        {
            "runtime_format": "png",
            "memory_budget_bytes": 1048576,
            "width": 32,
            "height": 16,
            "alpha_mode": "blend",
        },
        [
            {"role": "texture", "media_type": "image/png"},
            {"role": "clipset", "media_type": "application/json"},
        ],
    )
    png(root / "generated/neutral_sheet_idle.png", 16, 16, [(32, 48, 64, 255), (64, 96, 128, 255)])
    png(
        root / "generated/neutral_sheet_walk.png",
        16,
        16,
        [(96, 128, 160, 255), (128, 160, 192, 255)],
    )
    outs = []
    receipts = []
    for frame in ("idle", "walk"):
        cand = root / f"generated/neutral_sheet_{frame}.png"
        expected = [{"role": "preview", "media_type": "image/png"}]
        _, rec = production(
            root,
            "neutral_sheet",
            root / "specs/neutral_sheet.json",
            f"neutral_sheet_{frame}",
            "image_generate",
            [
                {
                    "role": "preview",
                    **ref(root, cand),
                    "media_type": "image/png",
                    "width": 16,
                    "height": 16,
                }
            ],
            expected,
        )
        receipts.append(rec)
        outs.append(
            {
                "file": cand.relative_to(root).as_posix(),
                "sha256": sha(cand),
                "approved_by": "fixture",
            }
        )
    proc_dir = root / "processed/neutral_sheet"
    recipe(
        root,
        "recipes/neutral_sheet_atlas.json",
        "atlas",
        {
            "inputs": [
                {
                    "id": "idle",
                    "clip_id": "idle",
                    "duration_ticks": 6,
                    "loop": True,
                    "pivot": [8, 14],
                    "artifact": ref(root, "generated/neutral_sheet_idle.png"),
                },
                {
                    "id": "walk",
                    "clip_id": "walk",
                    "duration_ticks": 6,
                    "loop": True,
                    "pivot": [8, 14],
                    "artifact": ref(root, "generated/neutral_sheet_walk.png"),
                },
            ],
            "output": {
                "texture_file": "neutral_sheet.png",
                "clipset_file": "neutral_sheet.clipset.json",
            },
            "options": {"cell_width": 16, "cell_height": 16, "columns": 2},
        },
    )
    process_asset_recipe(root / "recipes/neutral_sheet_atlas.json", proc_dir, asset_root=root)
    atlas = proc_dir / "neutral_sheet.png"
    clipset_path = proc_dir / "neutral_sheet.clipset.json"
    lic, qa = license_qa(root, "neutral_sheet", tgt, {sha(atlas), sha(clipset_path)})
    assets.append(
        {
            "id": "neutral_sheet",
            "kind": "spritesheet",
            "representation": "2_5d",
            "required": True,
            "status": "processed",
            "specification": ref(root, root / "specs/neutral_sheet.json"),
            "production_receipts": [
                ref(root, root / "receipts/neutral_sheet_idle.json"),
                ref(root, root / "receipts/neutral_sheet_walk.json"),
            ],
            "selected_candidates": sorted(outs, key=lambda x: (x["file"], x["sha256"])),
            "processing_receipt": ref(root, proc_dir / "processing.receipt.json"),
            "license": ref(root, root / "licenses/neutral_sheet.json"),
            "qa": ref(root, root / "qa/neutral_sheet.json"),
            "outputs": [
                {
                    "role": "texture",
                    "runtime_file": "processed/neutral_sheet/neutral_sheet.png",
                    "sha256": sha(atlas),
                    "size": atlas.stat().st_size,
                    "media_type": "image/png",
                },
                {
                    "role": "clipset",
                    "runtime_file": "processed/neutral_sheet/neutral_sheet.clipset.json",
                    "sha256": sha(clipset_path),
                    "size": clipset_path.stat().st_size,
                    "media_type": "application/json",
                },
            ],
        }
    )
    bindings.append(
        {
            "slot": "actor:neutral",
            "asset_id": "neutral_sheet",
            "representation": "2_5d",
            "clip": "idle",
            "moving_clip": "walk",
            "scale": 1,
            "layer": 0,
        }
    )
    # simple one-output assets
    simple_defs = [
        (
            reqs[0],
            {"runtime_format": "ttf", "memory_budget_bytes": 65536},
            [{"role": "font", "media_type": "font/ttf"}],
            lambda p: ttf(p),
            "file_validate",
            "font",
            "font/ttf",
            "process_run",
        ),
        (
            reqs[4],
            {
                "runtime_format": "wav",
                "memory_budget_bytes": 65536,
                "sample_rate": 8000,
                "channels": 1,
            },
            [{"role": "audio", "media_type": "audio/wav"}],
            lambda p: wav(p),
            "wav_pcm",
            "audio",
            "audio/wav",
            "process_run",
        ),
    ]
    for item, tech, expected, writer, op, role, media, prodop in simple_defs:
        spec(root, item, tgt, inv, visual, audio, tech, expected)
        ext = {"font/ttf": "ttf", "audio/wav": "wav"}[media]
        cand = root / f"generated/{item['id']}.{ext}"
        writer(cand)
        _, rec = production(
            root,
            item["id"],
            root / f"specs/{item['id']}.json",
            item["id"],
            prodop,
            [{"role": role, **ref(root, cand), "media_type": media}],
            expected,
        )
        proc_dir = root / f"processed/{item['id']}"
        if op == "file_validate":
            recipe(
                root,
                f"recipes/{item['id']}.json",
                op,
                {
                    "input": ref(root, cand),
                    "output": {"file": cand.name, "role": role, "media_type": media},
                    "options": {},
                },
            )
        else:
            recipe(
                root,
                f"recipes/{item['id']}.json",
                op,
                {
                    "input": ref(root, cand),
                    "output": {"file": cand.name},
                    "options": {
                        "channel_mode": "mono",
                        "peak": 12000,
                        "sample_rate": 8000,
                        "trim_threshold": 0,
                    },
                },
            )
        process_asset_recipe(root / f"recipes/{item['id']}.json", proc_dir, asset_root=root)
        runtime = proc_dir / cand.name
        lic, qa = license_qa(root, item["id"], tgt, {sha(runtime)})
        assets.append(
            {
                "id": item["id"],
                "kind": item["kind"],
                "representation": item["representation"],
                "required": item["required"],
                "status": "processed",
                "specification": ref(root, root / f"specs/{item['id']}.json"),
                "production_receipts": [ref(root, root / f"receipts/{item['id']}.json")],
                "selected_candidates": [
                    {
                        "file": cand.relative_to(root).as_posix(),
                        "sha256": sha(cand),
                        "approved_by": "fixture",
                    }
                ],
                "processing_receipt": ref(root, proc_dir / "processing.receipt.json"),
                "license": ref(root, root / f"licenses/{item['id']}.json"),
                "qa": ref(root, root / f"qa/{item['id']}.json"),
                "outputs": [
                    {
                        "role": role,
                        "runtime_file": runtime.relative_to(root).as_posix(),
                        "sha256": sha(runtime),
                        "size": runtime.stat().st_size,
                        "media_type": media,
                    }
                ],
            }
        )
        if item["semantic_slots"]:
            bindings.append(
                {
                    "slot": item["semantic_slots"][0],
                    "asset_id": item["id"],
                    "representation": item["representation"],
                    "scale": 1,
                    "layer": 0,
                }
            )
    # shader assets (one validated file per manifest asset)
    for shader, filename, role in (
        (reqs[1], "neutral.frag", "fragment_shader"),
        (reqs[2], "neutral.vert", "vertex_shader"),
    ):
        spec(
            root,
            shader,
            tgt,
            inv,
            visual,
            audio,
            {"runtime_format": "glsl", "memory_budget_bytes": 65536},
            [{"role": role, "media_type": "text/x-glsl"}],
        )
        cand = root / f"generated/{filename}"
        glsl(cand, "void main() {\n}\n")
        _, rec = production(
            root,
            shader["id"],
            root / f"specs/{shader['id']}.json",
            shader["id"],
            "process_run",
            [{"role": role, **ref(root, cand), "media_type": "text/x-glsl"}],
            [{"role": role, "media_type": "text/x-glsl"}],
        )
        proc_dir = root / f"processed/{shader['id']}"
        recipe(
            root,
            f"recipes/{shader['id']}.json",
            "file_validate",
            {
                "input": ref(root, cand),
                "output": {"file": filename, "role": role, "media_type": "text/x-glsl"},
                "options": {},
            },
        )
        process_asset_recipe(root / f"recipes/{shader['id']}.json", proc_dir, asset_root=root)
        runtime = proc_dir / filename
        lic, qa = license_qa(root, shader["id"], tgt, {sha(runtime)})
        assets.append(
            {
                "id": shader["id"],
                "kind": shader["kind"],
                "representation": shader["representation"],
                "required": False,
                "status": "processed",
                "specification": ref(root, root / f"specs/{shader['id']}.json"),
                "production_receipts": [ref(root, root / f"receipts/{shader['id']}.json")],
                "selected_candidates": [
                    {
                        "file": cand.relative_to(root).as_posix(),
                        "sha256": sha(cand),
                        "approved_by": "fixture",
                    }
                ],
                "processing_receipt": ref(root, proc_dir / "processing.receipt.json"),
                "license": ref(root, root / f"licenses/{shader['id']}.json"),
                "qa": ref(root, root / f"qa/{shader['id']}.json"),
                "outputs": [
                    {
                        "role": role,
                        "runtime_file": runtime.relative_to(root).as_posix(),
                        "sha256": sha(runtime),
                        "size": runtime.stat().st_size,
                        "media_type": "text/x-glsl",
                    }
                ],
            }
        )
    write_json(
        root / "manifest.json",
        {
            "format": "rpg-world-forge.asset_manifest",
            "format_version": 3,
            "world_id": world["world"]["id"],
            "world_content_hash": world["content_hash"],
            "target": ref(root, "target.json"),
            "phase": "production",
            "generation_policy": {
                "orchestrator": "gpt",
                "enabled_routes": ["openai"],
                "local_model_route": "modly",
                "executors": ["procedural"],
            },
            "bibles": {
                "visual": ref(root, "bibles/visual.json"),
                "audio": ref(root, "bibles/audio.json"),
            },
            "inventory": ref(root, "inventory/assets.json"),
            "assets": sorted(assets, key=lambda a: a["id"]),
            "bindings": sorted(bindings, key=lambda b: b["slot"]),
        },
    )


def build_asset_fixture(base: Path, world: dict[str, Any]) -> None:
    root = base / "assetpack"
    tgt = target(root, world, "neutral_3d", "3d")
    visual, audio = bibles(root, world, tgt)
    item = {
        "id": "neutral_actor_3d",
        "kind": "character_3d",
        "representation": "3d",
        "required": True,
        "purpose": "Neutral embedded GLB actor fixture",
        "canonical_sources": ["world:foundation_slice"],
        "semantic_slots": ["actor:neutral"],
    }
    inv = write_json(
        root / "inventory/assets.json",
        {
            "format": "rpg-world-forge.asset_inventory",
            "format_version": 1,
            "world_id": world["world"]["id"],
            "world_content_hash": world["content_hash"],
            "target_id": tgt["id"],
            "target_hash": tgt["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "requirements": [item],
            "manual_additions": [],
        },
    )
    spec(
        root,
        item,
        tgt,
        inv,
        visual,
        audio,
        {
            "runtime_format": "glb",
            "memory_budget_bytes": 1048576,
            "physical_dimensions_m": [1, 1, 1],
            "budgets": {
                "max_vertices": 10,
                "max_triangles": 10,
                "max_materials": 2,
                "max_texture_size": 2048,
            },
            "required_animations": ["idle"],
        },
        [{"role": "model", "media_type": "model/gltf-binary"}],
    )
    glb(root / "generated/neutral_actor_3d.glb")
    _, rec = production(
        root,
        item["id"],
        root / f"specs/{item['id']}.json",
        item["id"],
        "model_from_reference",
        [
            {
                "role": "model",
                **ref(root, "generated/neutral_actor_3d.glb"),
                "media_type": "model/gltf-binary",
            }
        ],
        [{"role": "model", "media_type": "model/gltf-binary"}],
    )
    proc_dir = root / "processed/neutral_actor_3d"
    recipe(
        root,
        "recipes/neutral_actor_3d.json",
        "glb_validate",
        {
            "input": ref(root, "generated/neutral_actor_3d.glb"),
            "output": {"file": "neutral_actor_3d.glb", "role": "model"},
            "options": {
                "budgets": {
                    "max_vertices": 10,
                    "max_triangles": 10,
                    "max_materials": 2,
                    "max_texture_size": 2048,
                },
                "max_bytes": 1048576,
            },
        },
    )
    process_asset_recipe(root / "recipes/neutral_actor_3d.json", proc_dir, asset_root=root)
    lic, qa = license_qa(root, item["id"], tgt, {sha(proc_dir / "neutral_actor_3d.glb")})
    asset = {
        "id": item["id"],
        "kind": item["kind"],
        "representation": "3d",
        "required": True,
        "status": "processed",
        "specification": ref(root, "specs/neutral_actor_3d.json"),
        "production_receipts": [ref(root, "receipts/neutral_actor_3d.json")],
        "selected_candidates": [
            {
                "file": "generated/neutral_actor_3d.glb",
                "sha256": sha(root / "generated/neutral_actor_3d.glb"),
                "approved_by": "fixture",
            }
        ],
        "processing_receipt": ref(root, "processed/neutral_actor_3d/processing.receipt.json"),
        "license": ref(root, "licenses/neutral_actor_3d.json"),
        "qa": ref(root, "qa/neutral_actor_3d.json"),
        "outputs": [
            {
                "role": "model",
                "runtime_file": "processed/neutral_actor_3d/neutral_actor_3d.glb",
                "sha256": sha(proc_dir / "neutral_actor_3d.glb"),
                "size": (proc_dir / "neutral_actor_3d.glb").stat().st_size,
                "media_type": "model/gltf-binary",
            }
        ],
    }
    write_json(
        root / "manifest.json",
        {
            "format": "rpg-world-forge.asset_manifest",
            "format_version": 3,
            "world_id": world["world"]["id"],
            "world_content_hash": world["content_hash"],
            "target": ref(root, "target.json"),
            "phase": "production",
            "generation_policy": {
                "orchestrator": "gpt",
                "enabled_routes": ["openai"],
                "local_model_route": "modly",
                "executors": ["procedural"],
            },
            "bibles": {
                "visual": ref(root, "bibles/visual.json"),
                "audio": ref(root, "bibles/audio.json"),
            },
            "inventory": ref(root, "inventory/assets.json"),
            "assets": [asset],
            "bindings": [
                {
                    "slot": "actor:neutral",
                    "asset_id": item["id"],
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


def lock(base: Path) -> None:
    files = []
    for path in sorted(p for p in base.rglob("*") if p.is_file() and p.name != "fixture.lock.json"):
        files.append(
            {
                "path": path.relative_to(base).as_posix(),
                "sha256": sha(path),
                "size": path.stat().st_size,
            }
        )
    write_json(
        base / "fixture.lock.json",
        {
            "format": "rpg-world-forge.m5_neutral_fixture_lock",
            "format_version": 1,
            "worldpack_anchor": {
                "path": "content/compiled/foundation.worldpack.json",
                "sha256": sha(WORLDPACK),
            },
            "files": files,
        },
        hashed=False,
    )


def readme(base: Path) -> None:
    text = (
        "# M5 neutral production fixture\n\n"
        "Narrative-neutral fixture anchored to "
        "`content/compiled/foundation.worldpack.json`. Regenerate outside the repository "
        "with `scripts/generate_m5_neutral.py --target /tmp/m5-neutral`. It executes "
        "locally, procedurally, and offline; the `openai` route is only the required "
        "contract namespace, and no provider, ML-model, or network call occurs.\n"
        "The lock records the exact committed bytes. Regeneration is byte-stable within "
        "one supported toolchain; Pillow or zlib version changes may produce different "
        "PNG and receipt hashes while preserving the validated semantics.\n"
    )
    write_text_lf(base / "README.md", text)


def generate(target_dir: Path, *, allow_repo: bool) -> None:
    target = target_dir.resolve()
    repo = REPO_ROOT.resolve()
    if not allow_repo and (target == repo or repo in target.parents):
        raise SystemExit("refusing to write inside the repository without --allow-repo")
    if target.exists():
        raise SystemExit(f"refusing to overwrite existing target: {target}")
    target.mkdir(parents=True)
    base = target / FIXTURE_NAME
    base.mkdir()
    world = json.loads(WORLDPACK.read_text(encoding="utf-8"))
    readme(base)
    build_render_fixture(base, world)
    build_asset_fixture(base, world)
    lock(base)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--allow-repo", action="store_true")
    args = parser.parse_args()
    generate(args.target, allow_repo=args.allow_repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
