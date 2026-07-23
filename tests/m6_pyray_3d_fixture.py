from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from worldforge.asset_formats.gltf import BIN_CHUNK_TYPE, JSON_CHUNK_TYPE, inspect_glb


def _append_aligned(binary: bytearray, payload: bytes) -> tuple[int, int]:
    binary.extend(b"\x00" * (-len(binary) % 4))
    offset = len(binary)
    binary.extend(payload)
    return offset, len(payload)


def _json_chunk(document: dict[str, Any]) -> bytes:
    payload = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return payload + b" " * (-len(payload) % 4)


def write_neutral_skinned_glb(path: Path) -> dict[str, Any]:
    """Write one deterministic embedded skinned triangle and inspect the result."""

    binary = bytearray()
    positions = _append_aligned(
        binary,
        struct.pack(
            "<9f",
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
        ),
    )
    joints = _append_aligned(
        binary,
        bytes(
            (
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
        ),
    )
    weights = _append_aligned(
        binary,
        struct.pack(
            "<12f",
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
        ),
    )
    indices = _append_aligned(binary, struct.pack("<3H", 0, 1, 2))
    inverse_bind = _append_aligned(
        binary,
        struct.pack(
            "<16f",
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ),
    )
    animation_times = _append_aligned(binary, struct.pack("<2f", 0.0, 1.0))
    animation_translations = _append_aligned(
        binary,
        struct.pack("<6f", 0.0, 0.0, 0.0, 0.0, 0.25, 0.0),
    )
    binary.extend(b"\x00" * (-len(binary) % 4))

    views = (
        positions,
        joints,
        weights,
        indices,
        inverse_bind,
        animation_times,
        animation_translations,
    )
    document: dict[str, Any] = {
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "max": [1.0, 1.0, 0.0],
                "min": [0.0, 0.0, 0.0],
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5121,
                "count": 3,
                "type": "VEC4",
            },
            {
                "bufferView": 2,
                "componentType": 5126,
                "count": 3,
                "type": "VEC4",
            },
            {
                "bufferView": 3,
                "componentType": 5123,
                "count": 3,
                "type": "SCALAR",
            },
            {
                "bufferView": 4,
                "componentType": 5126,
                "count": 1,
                "type": "MAT4",
            },
            {
                "bufferView": 5,
                "componentType": 5126,
                "count": 2,
                "max": [1.0],
                "min": [0.0],
                "type": "SCALAR",
            },
            {
                "bufferView": 6,
                "componentType": 5126,
                "count": 2,
                "type": "VEC3",
            },
        ],
        "animations": [
            {
                "channels": [
                    {
                        "sampler": 0,
                        "target": {"node": 1, "path": "translation"},
                    }
                ],
                "name": "idle",
                "samplers": [
                    {
                        "input": 5,
                        "interpolation": "LINEAR",
                        "output": 6,
                    }
                ],
            }
        ],
        "asset": {"generator": "neutral-skinned-test", "version": "2.0"},
        "bufferViews": [
            {
                "buffer": 0,
                "byteLength": length,
                "byteOffset": offset,
                **(
                    {"target": 34962}
                    if index in {0, 1, 2}
                    else {"target": 34963}
                    if index == 3
                    else {}
                ),
            }
            for index, (offset, length) in enumerate(views)
        ],
        "buffers": [{"byteLength": len(binary)}],
        "materials": [
            {
                "name": "Neutral",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.7, 0.7, 0.7, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "meshes": [
            {
                "name": "Triangle",
                "primitives": [
                    {
                        "attributes": {
                            "JOINTS_0": 1,
                            "POSITION": 0,
                            "WEIGHTS_0": 2,
                        },
                        "indices": 3,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "nodes": [
            {"mesh": 0, "name": "Actor", "skin": 0},
            {"name": "RootJoint"},
        ],
        "scene": 0,
        "scenes": [{"name": "Neutral", "nodes": [0, 1]}],
        "skins": [
            {
                "inverseBindMatrices": 4,
                "joints": [1],
                "name": "NeutralSkin",
                "skeleton": 1,
            }
        ],
    }
    json_payload = _json_chunk(document)
    bin_payload = bytes(binary)
    chunks = (
        struct.pack("<II", len(json_payload), JSON_CHUNK_TYPE)
        + json_payload
        + struct.pack("<II", len(bin_payload), BIN_CHUNK_TYPE)
        + bin_payload
    )
    payload = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    inspection = inspect_glb(
        path,
        allow_external_uris=False,
        budgets={
            "max_animations": 1,
            "max_bones": 1,
            "max_influences": 4,
            "max_materials": 1,
            "max_meshes": 1,
            "max_nodes": 2,
            "max_skins": 1,
            "max_triangles": 1,
            "max_vertices": 3,
        },
        required_animation_names={"idle"},
        required_node_names={"RootJoint"},
    )
    if inspection["metrics"]["triangles"] != 1:
        raise AssertionError("neutral skinned GLB must contain exactly one triangle")
    return inspection


__all__ = ["write_neutral_skinned_glb"]
