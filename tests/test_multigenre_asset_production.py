from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import os
import socket
import struct
import subprocess
import tempfile
import unittest
import wave
import zlib
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import worldforge.asset_formats.gltf as generic_gltf
import worldforge.generic_asset_production as production_module
from scripts.generate_generic_asset_fixtures import _narrative_ttf
from scripts.generate_generic_asset_production_schemas import (
    build_schemas as build_production_schemas,
)
from worldforge.gamepack import load_gamepack
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    _expected_component_keys,
    _font_cmap_ranges,
    _glb_metadata,
    _inspect_candidate,
    _png_metadata,
    build_asset_license_record,
    build_asset_production_receipt,
    build_asset_production_request,
    build_asset_provenance_record,
    build_asset_selection,
    inspect_runtime_asset_bytes,
    load_asset_license_record,
    load_asset_production_receipt,
    load_asset_production_request,
    load_asset_provenance_record,
    load_asset_selection,
    publish_asset_license_record,
    publish_asset_production_receipt,
    publish_asset_production_request,
    publish_asset_provenance_record,
    publish_asset_selection,
    read_verified_artifact_bytes,
    serialize_production_contract,
    validate_asset_license_record,
    validate_asset_license_record_document,
    validate_asset_production_receipt,
    validate_asset_production_receipt_document,
    validate_asset_production_request_document,
    validate_asset_provenance_record,
    validate_asset_provenance_record_document,
    validate_asset_selection,
    validate_asset_selection_document,
)
from worldforge.generic_assets import (
    build_asset_inventory,
    build_asset_specification,
    build_asset_style,
    build_asset_subject,
    build_asset_target,
    load_asset_inventory,
    load_asset_specification,
    load_asset_style,
    load_asset_subject,
    load_asset_target,
)
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
_JWT_TEST_KEY = b"world-forge-d2a-fixture-key"


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _signed_hs256_jwt(header: str, *, payload_padding: int = 8) -> str:
    payload = _base64url(
        json.dumps(
            {"pad": "x" * payload_padding, "sub": "fixture"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    signing_input = f"{header}.{payload}"
    signature = _base64url(
        hmac.new(_JWT_TEST_KEY, signing_input.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{signing_input}.{signature}"


def _valid_hs256_jwt(*, header_padding: int, payload_padding: int = 8) -> str:
    header = _base64url(
        json.dumps(
            {"alg": "HS256", "pad": "x" * header_padding, "typ": "JWT"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return _signed_hs256_jwt(header, payload_padding=payload_padding)


def _canonical_and_noncanonical_hs256_jwts() -> tuple[str, str]:
    header_bytes = json.dumps(
        {"alg": "HS256", "x": "a"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    canonical_header = _base64url(header_bytes)
    noncanonical_header = None
    for suffix in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_":
        candidate = f"{canonical_header[:-1]}{suffix}"
        padded = f"{candidate}{'=' * (-len(candidate) % 4)}"
        if (
            candidate != canonical_header
            and base64.b64decode(
                padded,
                altchars=b"-_",
                validate=True,
            )
            == header_bytes
        ):
            noncanonical_header = candidate
            break
    if noncanonical_header is None:
        raise AssertionError("unable to construct noncanonical JWT header")
    return (
        _signed_hs256_jwt(canonical_header),
        _signed_hs256_jwt(noncanonical_header),
    )


def _glb_json_bytes(document: object) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return encoded + b" " * (-len(encoded) % 4)


def _glb_bytes(document: dict[str, object]) -> bytes:
    json_chunk = _glb_json_bytes(document)
    chunks = struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def _bounded_glb(*, generator: str = "neutral-test") -> bytes:
    return _glb_bytes(
        {
            "asset": {"version": "2.0", "generator": generator},
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
            "materials": [{}],
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
        }
    )


def _tiny_png() -> bytes:
    scanline = b"\0\x20\x40\x60\xff"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanline, level=9))
        + chunk(b"IEND", b"")
    )


def _tiny_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(struct.pack("<8h", 0, 500, -500, 0, 250, -250, 0, 0))
    return output.getvalue()


def _neutral_otf() -> bytes:
    font = bytearray(_narrative_ttf())
    font[:4] = b"OTTO"
    table_count = struct.unpack_from(">H", font, 4)[0]
    head_offset = next(
        offset
        for index in range(table_count)
        for tag, _, offset, _ in [struct.unpack_from(">4sIII", font, 12 + index * 16)]
        if tag == b"head"
    )
    struct.pack_into(">I", font, head_offset + 8, 0)
    padded = font + b"\0" * (-len(font) % 4)
    checksum = sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF
    struct.pack_into(">I", font, head_offset + 8, (0xB1B0AFBA - checksum) & 0xFFFFFFFF)
    return bytes(font)


def _schema_json(*, schema_id: str, records: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "records": records,
            "schema_id": schema_id,
            "schema_version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_planning(case: str, asset_id: str) -> dict[str, object]:
    case_root = EXAMPLES / case
    gamepack_path = case_root / "artifacts" / f"{case}.gamepack.json"
    subject_path = case_root / "assets" / "subject.json"
    target_path = case_root / "assets" / "target.json"
    style_path = case_root / "assets" / "style.json"
    inventory_path = case_root / "assets" / "inventory.json"
    spec_path = case_root / "assets" / "specs" / f"{asset_id}.json"
    gamepack = load_gamepack(gamepack_path)
    subject = load_asset_subject(subject_path, gamepack_path=gamepack_path)
    target = load_asset_target(
        target_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
    )
    style = load_asset_style(
        style_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
        target_path=target_path,
    )
    inventory = load_asset_inventory(
        inventory_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
        target_path=target_path,
        style_path=style_path,
    )
    spec = load_asset_specification(
        spec_path,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
    )
    return {
        "root": case_root,
        "gamepack": gamepack,
        "subject": subject,
        "target": target,
        "style": style,
        "inventory": inventory,
        "specification": spec,
    }


def _load_lineage(case: str, asset_id: str) -> dict[str, object]:
    values = _load_planning(case, asset_id)
    root = values["root"]
    assert isinstance(root, Path)
    paths = root / "assets" / "production" / asset_id
    request = load_asset_production_request(
        paths / "request.json",
        **{
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        },
    )
    receipt = load_asset_production_receipt(
        paths / "receipt.json",
        request=request,
        artifact_root=root,
        **{
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        },
    )
    selection = load_asset_selection(
        paths / "selection.json",
        request=request,
        receipt=receipt,
        artifact_root=root,
        **{
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        },
    )
    provenance = load_asset_provenance_record(
        paths / "provenance.json",
        request=request,
        receipt=receipt,
        selection=selection,
        artifact_root=root,
        **{
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        },
    )
    license_record = load_asset_license_record(
        paths / "license.json",
        request=request,
        receipt=receipt,
        selection=selection,
        provenance=provenance,
        artifact_root=root,
        **{
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        },
    )
    return {
        **values,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license": license_record,
    }


def _media_matrix_cases() -> tuple[dict[str, object], ...]:
    png = _tiny_png()
    clipset = _schema_json(
        schema_id="world-forge.fixture-clipset",
        records=[{"frame": 0, "name": "idle"}],
    )
    wav = _tiny_wav()
    ttf = _narrative_ttf()
    otf = _neutral_otf()
    vertex = b"#version 330\nvoid main() { gl_Position = vec4(0.0); }\n"
    fragment = b"#version 330\nout vec4 color;\nvoid main() { color = vec4(1.0); }\n"
    localized = _schema_json(
        schema_id="world-forge.fixture-localization",
        records=[{"key": "fixture.title", "value": "Fixture"}],
    )
    model = _bounded_glb(generator="neutral-model")
    skeleton = _bounded_glb(generator="neutral-skeleton")

    def png_output(role: str, runtime_path: str) -> dict[str, object]:
        return {
            "role": role,
            "media_type": "image/png",
            "runtime_path": runtime_path,
            "expectations": {
                "kind": "png",
                "width": 1,
                "height": 1,
                "color_type": "rgba8",
                "max_bytes": len(png),
            },
            "payload": png,
        }

    def json_output(
        role: str,
        runtime_path: str,
        *,
        schema_id: str,
        payload: bytes,
    ) -> dict[str, object]:
        return {
            "role": role,
            "media_type": "application/json",
            "runtime_path": runtime_path,
            "expectations": {
                "kind": "schema_json",
                "schema_id": schema_id,
                "schema_version": 1,
                "max_records": 4,
                "max_bytes": len(payload),
            },
            "payload": payload,
        }

    def glb_output(role: str, runtime_path: str, payload: bytes) -> dict[str, object]:
        return {
            "role": role,
            "media_type": "model/gltf-binary",
            "runtime_path": runtime_path,
            "expectations": {
                "kind": "glb",
                "max_nodes": 1,
                "max_meshes": 1,
                "max_primitives": 1,
                "max_materials": 1,
                "max_joints": 1,
                "max_animations": 1,
                "max_triangles": 1,
                "max_bytes": len(payload),
            },
            "payload": payload,
        }

    return (
        {
            "case_id": "png",
            "selected_format": "asset:png",
            "kind": "ui",
            "representation": "2d",
            "outputs": [png_output("texture", "assets/matrix/texture.png")],
        },
        {
            "case_id": "atlas",
            "selected_format": "asset:png",
            "kind": "spritesheet",
            "representation": "2d",
            "outputs": [
                json_output(
                    "clipset",
                    "assets/matrix/atlas.json",
                    schema_id="world-forge.fixture-clipset",
                    payload=clipset,
                ),
                png_output("texture", "assets/matrix/atlas.png"),
            ],
        },
        {
            "case_id": "wav",
            "selected_format": "asset:wav",
            "kind": "sfx",
            "representation": "audio",
            "outputs": [
                {
                    "role": "audio",
                    "media_type": "audio/wav",
                    "runtime_path": "assets/matrix/effect.wav",
                    "expectations": {
                        "kind": "wav_pcm16",
                        "channels": 1,
                        "sample_rate": 8000,
                        "frames": 8,
                        "max_bytes": len(wav),
                    },
                    "payload": wav,
                }
            ],
        },
        {
            "case_id": "ttf",
            "selected_format": "asset:font",
            "kind": "font",
            "representation": "2d",
            "outputs": [
                {
                    "role": "font",
                    "media_type": "font/ttf",
                    "runtime_path": "assets/matrix/interface.ttf",
                    "expectations": {
                        "kind": "font",
                        "container": "ttf",
                        "glyph_ranges": ["U+0020-007E"],
                        "max_glyphs": 256,
                        "max_bytes": len(ttf),
                    },
                    "payload": ttf,
                }
            ],
        },
        {
            "case_id": "otf",
            "selected_format": "asset:font",
            "kind": "font",
            "representation": "2d",
            "outputs": [
                {
                    "role": "font",
                    "media_type": "font/otf",
                    "runtime_path": "assets/matrix/interface.otf",
                    "expectations": {
                        "kind": "font",
                        "container": "otf",
                        "glyph_ranges": ["U+0020-007E"],
                        "max_glyphs": 256,
                        "max_bytes": len(otf),
                    },
                    "payload": otf,
                }
            ],
        },
        {
            "case_id": "glsl",
            "selected_format": "asset:glsl",
            "kind": "shader",
            "representation": "2d",
            "outputs": [
                {
                    "role": "fragment_shader",
                    "media_type": "text/x-glsl",
                    "runtime_path": "assets/matrix/fixture.frag",
                    "expectations": {
                        "kind": "glsl",
                        "stage": "fragment",
                        "max_lines": 4,
                        "max_bytes": len(fragment),
                    },
                    "payload": fragment,
                },
                {
                    "role": "vertex_shader",
                    "media_type": "text/x-glsl",
                    "runtime_path": "assets/matrix/fixture.vert",
                    "expectations": {
                        "kind": "glsl",
                        "stage": "vertex",
                        "max_lines": 4,
                        "max_bytes": len(vertex),
                    },
                    "payload": vertex,
                },
            ],
        },
        {
            "case_id": "json",
            "selected_format": "asset:json",
            "kind": "localization",
            "representation": "text",
            "outputs": [
                json_output(
                    "localized_text",
                    "assets/matrix/en.json",
                    schema_id="world-forge.fixture-localization",
                    payload=localized,
                )
            ],
        },
        {
            "case_id": "glb",
            "selected_format": "asset:glb",
            "kind": "model_3d",
            "representation": "3d",
            "outputs": [
                glb_output("model", "assets/matrix/model.glb", model),
            ],
        },
        {
            "case_id": "glb_pair",
            "selected_format": "asset:glb",
            "kind": "character_3d",
            "representation": "3d",
            "outputs": [
                glb_output("model", "assets/matrix/character.glb", model),
                glb_output("skeleton", "assets/matrix/skeleton.glb", skeleton),
            ],
        },
    )


def _build_media_planning(case: dict[str, object]) -> dict[str, object]:
    base = _load_planning("abstract-puzzle", "board_ui")
    gamepack = copy.deepcopy(base["gamepack"])
    assert isinstance(gamepack, dict)
    selected_format = case["selected_format"]
    gamepack["runtime_requirements"]["asset_formats"] = [selected_format]
    gamepack["asset_requirements"][0]["accepted_formats"] = [selected_format]
    if case["representation"] == "3d":
        gamepack["presentation"]["mode"] = "3d"
        gamepack["runtime_requirements"]["presentation"]["mode"] = "3d"
    gamepack["content_hash"] = _reseal(gamepack)

    subject = build_asset_subject(gamepack)
    output_bindings = [
        {
            "role": output["role"],
            "media_type": output["media_type"],
        }
        for output in case["outputs"]
    ]
    target = build_asset_target(
        gamepack,
        subject,
        review=base["target"]["review"],
        bindings=[
            {
                "binding_id": gamepack["asset_requirements"][0]["binding_id"],
                "asset_id": f"matrix_{case['case_id']}",
                "selected_format": selected_format,
                "kind": case["kind"],
                "representation": case["representation"],
                "outputs": output_bindings,
                "sharing": {"policy": "exclusive", "group_id": None},
            }
        ],
    )
    visual = copy.deepcopy(base["style"]["visual"])
    visual["presentation_mode"] = gamepack["presentation"]["mode"]
    visual["coordinate_system"] = (
        "world_3d"
        if case["representation"] == "3d"
        else "text_flow"
        if case["representation"] == "text"
        else "screen_2d"
    )
    audio: dict[str, object]
    if case["representation"] == "audio":
        audio = {
            "status": "defined",
            "role_direction": "Audio confirms the exact bounded fixture state.",
            "mix_direction": "The deterministic fixture remains mono and unclipped.",
            "music_direction": "No music is required by this fixture.",
            "sfx_direction": "One short PCM16 cue confirms interaction feedback.",
            "voice_direction": "No voice production is required by this fixture.",
            "caption_direction": "Equivalent visible feedback accompanies the cue.",
            "runtime_formats": ["asset:wav"],
        }
    else:
        audio = copy.deepcopy(base["style"]["audio"])
    style = build_asset_style(
        gamepack,
        subject,
        target,
        reviewer=base["style"]["review"],
        visual=visual,
        audio=audio,
    )
    inventory = build_asset_inventory(gamepack, subject, target, style)
    specification = build_asset_specification(
        gamepack,
        subject,
        target,
        style,
        inventory,
        asset_id=inventory["assets"][0]["asset_id"],
        outputs=[
            {key: value for key, value in output.items() if key != "payload"}
            for output in case["outputs"]
        ],
        acceptance_criteria=[
            "Every candidate byte sequence is validated against its exact media contract.",
            "Every selected output preserves the immutable gamepack subject hash.",
        ],
        production_class="procedural_offline",
        review_requirements={
            "human_review_required": True,
            "qa_profile": "generic_media_integrity",
            "evidence_required": True,
        },
    )
    return {
        "root": base["root"],
        "gamepack": gamepack,
        "subject": subject,
        "target": target,
        "style": style,
        "inventory": inventory,
        "specification": specification,
    }


def _production_toolchain(
    production_class: str,
    *,
    dataset_count: int = 1,
    operation_id: str = "generate_png",
) -> dict[str, object]:
    if production_class == "human":
        return {
            "production_class": "human",
            "creator_id": "fixture_artist",
            "operation_id": operation_id,
            "work_attestation_hash": "1" * 64,
        }
    if production_class == "procedural_offline":
        return {
            "production_class": "procedural_offline",
            "tool_id": "fixture_generator",
            "tool_version": "1.0.0",
            "operation_id": operation_id,
            "seed": 11,
        }
    if production_class == "external_authoring":
        return {
            "production_class": "external_authoring",
            "tool_id": "fixture_editor",
            "tool_version": "1.0.0",
            "operation_id": operation_id,
        }
    return {
        "production_class": "generative_authoring",
        "provider_id": "fixture_provider",
        "tool_id": "fixture_generator",
        "tool_version": "1.0.0",
        "operation_id": operation_id,
        "model_id": "fixture_model",
        "model_version": "1.0.0",
        "weights_id": "fixture_weights",
        "weights_version": "1.0.0",
        "dataset_ids": [f"dataset_{index:02d}" for index in range(dataset_count)],
        "seed_policy": "fixed",
        "seed": 11,
        "instruction_artifact_hash": "2" * 64,
    }


def _component_evidence(
    request: dict[str, object],
) -> list[dict[str, object]]:
    production_class = request["production_class"]
    toolchain = request["toolchain_requirements"]
    assert isinstance(toolchain, dict)
    if production_class == "human":
        keys = [
            ("creator", toolchain["creator_id"], "not_applicable"),
            ("original_work", request["asset"]["asset_id"], "not_applicable"),
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    elif production_class == "procedural_offline":
        keys = [("generator_tool", toolchain["tool_id"], toolchain["tool_version"])]
    elif production_class == "external_authoring":
        keys = [
            ("authoring_tool", toolchain["tool_id"], toolchain["tool_version"]),
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    else:
        keys = [
            ("provider", toolchain["provider_id"], "not_applicable"),
            ("authoring_tool", toolchain["tool_id"], toolchain["tool_version"]),
            ("model", toolchain["model_id"], toolchain["model_version"]),
            ("weights", toolchain["weights_id"], toolchain["weights_version"]),
            *[("dataset", dataset_id, "not_applicable") for dataset_id in toolchain["dataset_ids"]],
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    if request["input_artifacts"]:
        keys.append(("input_license", "request_inputs", "not_applicable"))
    return [
        {
            "scope": scope,
            "component_id": component_id,
            "component_version": version,
            "evidence_hash": hashlib.sha256(
                f"{scope}:{component_id}:{version}".encode()
            ).hexdigest(),
        }
        for scope, component_id, version in sorted(
            keys,
            key=lambda item: (str(item[0]).encode(), str(item[1]).encode()),
        )
    ]


def _build_complete_class_chain(
    values: dict[str, object],
    artifact_root: Path,
    production_class: str,
    *,
    dataset_count: int = 1,
    include_input: bool = False,
    media_case: dict[str, object] | None = None,
) -> dict[str, object]:
    specification = copy.deepcopy(values["specification"])
    assert isinstance(specification, dict)
    specification["production_class"] = production_class
    specification["content_hash"] = _reseal(specification)
    common = {
        "gamepack": values["gamepack"],
        "subject": values["subject"],
        "target": values["target"],
        "style": values["style"],
        "inventory": values["inventory"],
        "specification": specification,
    }
    if media_case is None:
        operation_id = "generate_png"
        candidate_descriptors = [
            {
                "role": "texture",
                "candidate_artifact_id": f"candidate_{production_class}",
                "locator": "candidates/board.png",
                "payload": (
                    EXAMPLES
                    / "abstract-puzzle"
                    / "assets"
                    / "production"
                    / "board_ui"
                    / "candidates"
                    / "board.png"
                ).read_bytes(),
            }
        ]
    else:
        case_id = str(media_case["case_id"])
        operation_id = f"produce_{case_id}"
        outputs = media_case["outputs"]
        assert isinstance(outputs, list)
        candidate_descriptors = []
        for index, output in enumerate(outputs):
            assert isinstance(output, dict)
            runtime_path = Path(str(output["runtime_path"]))
            candidate_descriptors.append(
                {
                    "role": output["role"],
                    "candidate_artifact_id": (f"candidate_{production_class}_{output['role']}"),
                    "locator": (Path("candidates") / f"{index:02d}-{runtime_path.name}").as_posix(),
                    "payload": output["payload"],
                }
            )
    for descriptor in candidate_descriptors:
        candidate_path = artifact_root / str(descriptor["locator"])
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        payload = descriptor["payload"]
        assert isinstance(payload, bytes)
        candidate_path.write_bytes(payload)
    inputs: list[dict[str, object]] = []
    if include_input:
        input_relative = Path("inputs") / "source.bin"
        input_path = artifact_root / input_relative
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_payload = b"independent reviewed source bytes\n"
        input_path.write_bytes(input_payload)
        inputs.append(
            {
                "artifact_id": "source_input",
                "role": "reference",
                "locator": input_relative.as_posix(),
                "size_bytes": len(input_payload),
                "sha256": hashlib.sha256(input_payload).hexdigest(),
            }
        )
    toolchain = _production_toolchain(
        production_class,
        dataset_count=dataset_count,
        operation_id=operation_id,
    )
    request = build_asset_production_request(
        **common,
        request_id=f"request_{production_class}",
        production_class=production_class,
        operation={"operation_id": operation_id, "version": 1},
        input_artifacts=inputs,
        reproducibility={
            "mode": (
                "reviewed_nondeterministic"
                if production_class in {"human", "external_authoring"}
                else "deterministic"
            ),
            "seed_policy": (
                "fixed"
                if production_class in {"procedural_offline", "generative_authoring"}
                else "forbidden"
            ),
        },
        rights_requirements={
            "commercial_use_review_required": True,
            "evidence_required": True,
            "human_review_required": True,
            "redistribution_review_required": True,
        },
        toolchain_requirements=toolchain,
    )
    receipt = build_asset_production_receipt(
        request,
        **common,
        receipt_id=f"receipt_{production_class}",
        status="completed",
        executed_toolchain=toolchain,
        candidates=[
            {
                "role": descriptor["role"],
                "candidate_artifact_id": descriptor["candidate_artifact_id"],
                "locator": descriptor["locator"],
            }
            for descriptor in candidate_descriptors
        ],
        artifact_root=artifact_root,
        execution_evidence={
            "started_evidence_hash": "3" * 64,
            "completed_evidence_hash": "4" * 64,
            "sanitized_log_hashes": [],
        },
        rights_attestation={
            "basis": "fixture_public_domain",
            "evidence_hashes": ["5" * 64],
        },
    )
    selection = build_asset_selection(
        receipt,
        request=request,
        **common,
        artifact_root=artifact_root,
        selection_id=f"selection_{production_class}",
        review={
            "reviewer_id": "fixture_reviewer",
            "rationale": "The exact candidate satisfies the bounded fixture requirements.",
            "evidence_hashes": ["6" * 64],
        },
    )
    components = _component_evidence(request)
    provenance = build_asset_provenance_record(
        selection,
        receipt=receipt,
        request=request,
        **common,
        artifact_root=artifact_root,
        provenance_id=f"provenance_{production_class}",
        component_evidence=components,
    )
    component_licenses = [
        {
            "scope": item["scope"],
            "component_id": item["component_id"],
            "identifier": "CC0-1.0",
            "evidence_hash": item["evidence_hash"],
        }
        for item in components
    ]
    license_records = []
    for descriptor in candidate_descriptors:
        candidate_id = str(descriptor["candidate_artifact_id"])
        license_records.append(
            build_asset_license_record(
                provenance,
                selection=selection,
                receipt=receipt,
                request=request,
                **common,
                artifact_root=artifact_root,
                license_record_id=(
                    f"license_{production_class}"
                    if media_case is None
                    else f"license_{production_class}_{descriptor['role']}"
                ),
                candidate_artifact_id=candidate_id,
                license_basis={"kind": "spdx", "identifier": "CC0-1.0"},
                copyright={
                    "holder": "World Forge fixture authors",
                    "year_policy": "fixed",
                    "year": 2026,
                },
                permissions={
                    "commercial_use": True,
                    "modification": True,
                    "redistribution": True,
                },
                obligations={
                    "attribution_required": False,
                    "notice_required": True,
                    "source_offer_required": False,
                },
                component_licenses=component_licenses,
                runtime_notice_text="Fixture asset license notice.",
                evidence_hashes=["7" * 64],
            )
        )
    return {
        **common,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license": license_records[0],
        "licenses": license_records,
    }


class GenericAssetProductionTests(unittest.TestCase):
    def test_public_runtime_asset_snapshot_reuses_integral_media_validation(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
        output = media_case["outputs"][0]
        with tempfile.TemporaryDirectory(prefix="world-forge-d2a-runtime-snapshot-") as temporary:
            root = Path(temporary)
            locator = Path("processed") / "texture.png"
            artifact = root / locator
            artifact.parent.mkdir(parents=True)
            payload = output["payload"]
            artifact.write_bytes(payload)

            snapshot = read_verified_artifact_bytes(
                root,
                locator.as_posix(),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_size_bytes=len(payload),
            )
            self.assertEqual(snapshot, payload)
            self.assertEqual(
                inspect_runtime_asset_bytes(
                    snapshot,
                    role=output["role"],
                    media_type=output["media_type"],
                    expectations=output["expectations"],
                ),
                {
                    "height": 1,
                    "kind": "png",
                    "mode": "rgba8",
                    "width": 1,
                },
            )

            artifact.write_bytes(b"\0" * len(payload))
            with self.assertRaisesRegex(
                GenericAssetProductionError,
                "production_artifact_hash_mismatch",
            ):
                read_verified_artifact_bytes(
                    root,
                    locator.as_posix(),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                    expected_size_bytes=len(payload),
                )

    @unittest.skipUnless(os.name == "posix", "POSIX retained-ancestry replacement regression")
    def test_candidate_snapshot_rejects_root_replacement_after_the_retained_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-candidate-swap-") as temporary:
            parent = Path(temporary)
            root = parent / "candidate-root"
            retained = parent / "candidate-root-retained"
            artifact = root / "processed" / "texture.png"
            artifact.parent.mkdir(parents=True)
            payload = b"retained candidate bytes"
            artifact.write_bytes(payload)
            original_snapshot = production_module._safe_entry_snapshot

            def replace_root_after_snapshot(*args: object, **kwargs: object):
                snapshot = original_snapshot(*args, **kwargs)
                root.rename(retained)
                (root / "processed").mkdir(parents=True)
                (root / "processed" / "texture.png").write_bytes(b"foreign replacement")
                return snapshot

            with (
                mock.patch.object(
                    production_module,
                    "_safe_entry_snapshot",
                    side_effect=replace_root_after_snapshot,
                ),
                self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "production_artifact_read_failed",
                ),
            ):
                read_verified_artifact_bytes(
                    root,
                    "processed/texture.png",
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                    expected_size_bytes=len(payload),
                )

            self.assertEqual(payload, (retained / "processed" / "texture.png").read_bytes())
            self.assertEqual(
                b"foreign replacement",
                (root / "processed" / "texture.png").read_bytes(),
            )

    def test_production_schemas_are_canonical_generator_outputs(self) -> None:
        for name, schema in build_production_schemas().items():
            with self.subTest(schema=name):
                self.assertEqual(
                    canonical_json_bytes(schema),
                    (ROOT / "schemas" / name).read_bytes(),
                )

    def test_canonical_fixture_lineage_is_integrally_loadable(self) -> None:
        cases = (
            ("abstract-puzzle", "board_ui"),
            ("branching-narrative", "narrative_ui_font"),
        )
        for case, asset_id in cases:
            with self.subTest(case=case):
                values = _load_planning(case, asset_id)
                root = values["root"]
                assert isinstance(root, Path)
                paths = root / "assets" / "production" / asset_id
                request = load_asset_production_request(
                    paths / "request.json",
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                )
                receipt = load_asset_production_receipt(
                    paths / "receipt.json",
                    request=request,
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                    artifact_root=root,
                )
                selection = load_asset_selection(
                    paths / "selection.json",
                    receipt=receipt,
                    request=request,
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                    artifact_root=root,
                )
                provenance = load_asset_provenance_record(
                    paths / "provenance.json",
                    selection=selection,
                    receipt=receipt,
                    request=request,
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                    artifact_root=root,
                )
                license_record = load_asset_license_record(
                    paths / "license.json",
                    provenance=provenance,
                    selection=selection,
                    receipt=receipt,
                    request=request,
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                    artifact_root=root,
                )
                for document in (request, receipt, selection, provenance, license_record):
                    serialized = serialize_production_contract(document)
                    self.assertTrue(serialized.endswith(b"\n"))
                    self.assertEqual(json.loads(serialized), document)

    def test_all_production_classes_have_closed_conditional_toolchains(self) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        common = {
            key: values[key]
            for key in (
                "gamepack",
                "subject",
                "target",
                "style",
                "inventory",
                "specification",
            )
        }
        toolchains = {
            "human": {
                "production_class": "human",
                "creator_id": "fixture_artist",
                "operation_id": "generate_png",
                "work_attestation_hash": "1" * 64,
            },
            "procedural_offline": {
                "production_class": "procedural_offline",
                "tool_id": "world_forge_fixture_generator",
                "tool_version": "1.0.0",
                "operation_id": "generate_png",
                "seed": 11,
            },
            "external_authoring": {
                "production_class": "external_authoring",
                "tool_id": "fixture_editor",
                "tool_version": "1.0.0",
                "operation_id": "generate_png",
            },
            "generative_authoring": {
                "production_class": "generative_authoring",
                "provider_id": "fixture_provider",
                "tool_id": "fixture_generator",
                "tool_version": "1.0.0",
                "operation_id": "generate_png",
                "model_id": "fixture_model",
                "model_version": "1.0.0",
                "weights_id": "fixture_weights",
                "weights_version": "1.0.0",
                "dataset_ids": ["fixture_dataset"],
                "seed_policy": "fixed",
                "seed": 11,
                "instruction_artifact_hash": "2" * 64,
            },
        }
        for production_class, toolchain in toolchains.items():
            with self.subTest(production_class=production_class):
                specification = copy.deepcopy(common["specification"])
                assert isinstance(specification, dict)
                specification["production_class"] = production_class
                specification["content_hash"] = _reseal(specification)
                request = build_asset_production_request(
                    **{**common, "specification": specification},
                    request_id=f"request_{production_class}",
                    production_class=production_class,
                    operation={"operation_id": "generate_png", "version": 1},
                    input_artifacts=[],
                    reproducibility={
                        "mode": (
                            "reviewed_nondeterministic"
                            if production_class in {"human", "external_authoring"}
                            else "deterministic"
                        ),
                        "seed_policy": (
                            "fixed"
                            if production_class in {"procedural_offline", "generative_authoring"}
                            else "forbidden"
                        ),
                    },
                    rights_requirements={
                        "commercial_use_review_required": True,
                        "evidence_required": True,
                        "human_review_required": True,
                        "redistribution_review_required": True,
                    },
                    toolchain_requirements=toolchain,
                )
                self.assertEqual(request["production_class"], production_class)
                crossed = copy.deepcopy(request)
                forbidden_field = "model_id" if production_class == "human" else "creator_id"
                crossed["toolchain_requirements"][forbidden_field] = "invented_component"
                crossed["content_hash"] = _reseal(crossed)
                with self.assertRaises(GenericAssetProductionError):
                    validate_asset_production_request_document(crossed)

    def test_maximal_generative_components_close_the_full_reviewed_lineage(
        self,
    ) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        with tempfile.TemporaryDirectory(prefix="world-forge-production-maximal-") as temporary:
            chain = _build_complete_class_chain(
                values,
                Path(temporary),
                "generative_authoring",
                dataset_count=64,
                include_input=True,
            )
        provenance = chain["provenance"]
        license_record = chain["license"]
        assert isinstance(provenance, dict)
        assert isinstance(license_record, dict)
        self.assertEqual(len(provenance["components"]), 70)
        self.assertEqual(len(license_record["component_licenses"]), 70)
        self.assertEqual(
            chain["request"]["content_hash"],
            chain["receipt"]["request"]["content_hash"],
        )

    def test_all_production_classes_bind_input_licenses_exactly_when_inputs_exist(
        self,
    ) -> None:
        for production_class in (
            "human",
            "procedural_offline",
            "external_authoring",
            "generative_authoring",
        ):
            for include_input in (False, True):
                with self.subTest(
                    production_class=production_class,
                    include_input=include_input,
                ):
                    request = {
                        "asset": {"asset_id": "matrix_asset"},
                        "toolchain_requirements": _production_toolchain(production_class),
                        "input_artifacts": (
                            [{"artifact_id": "source_input"}] if include_input else []
                        ),
                    }
                    scopes = [
                        scope
                        for scope, _, _ in _expected_component_keys(
                            production_class,
                            request,
                        )
                    ]
                    self.assertEqual(
                        scopes.count("input_license"),
                        1 if include_input else 0,
                    )

    def test_media_matrix_and_every_class_input_pair_close_full_lineages(
        self,
    ) -> None:
        media_cases = _media_matrix_cases()
        assignments = (
            ("human", False),
            ("human", True),
            ("procedural_offline", False),
            ("procedural_offline", True),
            ("external_authoring", False),
            ("external_authoring", True),
            ("generative_authoring", False),
            ("generative_authoring", True),
            ("procedural_offline", True),
        )
        self.assertEqual(
            {
                (production_class, include_input)
                for production_class in (
                    "human",
                    "procedural_offline",
                    "external_authoring",
                    "generative_authoring",
                )
                for include_input in (False, True)
            },
            set(assignments),
        )
        self.assertEqual(
            {
                ("application/json", "clipset"),
                ("application/json", "localized_text"),
                ("audio/wav", "audio"),
                ("font/otf", "font"),
                ("font/ttf", "font"),
                ("image/png", "texture"),
                ("model/gltf-binary", "model"),
                ("model/gltf-binary", "skeleton"),
                ("text/x-glsl", "fragment_shader"),
                ("text/x-glsl", "vertex_shader"),
            },
            {
                (output["media_type"], output["role"])
                for case in media_cases
                for output in case["outputs"]
            },
        )
        self.assertTrue(any(len(case["outputs"]) > 1 for case in media_cases))
        for media_case in media_cases:
            for output in media_case["outputs"]:
                payload = output["payload"]
                expectations = output["expectations"]
                self.assertIsInstance(
                    _inspect_candidate(
                        payload,
                        role=output["role"],
                        media_type=output["media_type"],
                        expectations=expectations,
                    ),
                    dict,
                )
                crossed_budget = copy.deepcopy(expectations)
                crossed_budget["max_bytes"] = len(payload) - 1
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "byte budget",
                ):
                    _inspect_candidate(
                        payload,
                        role=output["role"],
                        media_type=output["media_type"],
                        expectations=crossed_budget,
                    )

        for media_case, (production_class, include_input) in zip(
            media_cases,
            assignments,
            strict=True,
        ):
            values = _build_media_planning(media_case)
            with self.subTest(
                media=media_case["case_id"],
                production_class=production_class,
                include_input=include_input,
            ):
                with tempfile.TemporaryDirectory(
                    prefix=(f"world-forge-media-{media_case['case_id']}-{production_class}-")
                ) as temporary:
                    chain = _build_complete_class_chain(
                        values,
                        Path(temporary),
                        production_class,
                        include_input=include_input,
                        media_case=media_case,
                    )
                expected_outputs = len(media_case["outputs"])
                self.assertEqual(
                    expected_outputs,
                    len(chain["request"]["expected_outputs"]),
                )
                self.assertEqual(expected_outputs, len(chain["receipt"]["outputs"]))
                self.assertEqual(
                    expected_outputs,
                    len(chain["selection"]["selected_outputs"]),
                )
                self.assertEqual(
                    expected_outputs,
                    len(chain["provenance"]["candidates"]),
                )
                self.assertEqual(expected_outputs, len(chain["licenses"]))
                for license_record in chain["licenses"]:
                    self.assertEqual(
                        chain["selection"]["content_hash"],
                        license_record["selection"]["content_hash"],
                    )
                    scopes = [item["scope"] for item in license_record["component_licenses"]]
                    self.assertEqual(
                        scopes.count("input_license"),
                        1 if include_input else 0,
                    )
                self.assertEqual(
                    chain["gamepack"]["content_hash"],
                    chain["request"]["gamepack"]["content_hash"],
                )
                self.assertEqual(
                    chain["request"]["content_hash"],
                    chain["receipt"]["request"]["content_hash"],
                )

    def test_recorded_procedural_seed_requires_a_concrete_integer(self) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        with tempfile.TemporaryDirectory(prefix="world-forge-recorded-procedural-") as temporary:
            chain = _build_complete_class_chain(
                values,
                Path(temporary),
                "procedural_offline",
            )
        request = copy.deepcopy(chain["request"])
        assert isinstance(request, dict)
        request["reproducibility"]["seed_policy"] = "recorded"
        request["content_hash"] = _reseal(request)
        self.assertEqual(
            validate_asset_production_request_document(request),
            request,
        )

        missing_seed = copy.deepcopy(request)
        missing_seed["toolchain_requirements"]["seed"] = None
        missing_seed["content_hash"] = _reseal(missing_seed)
        with self.assertRaisesRegex(
            GenericAssetProductionError,
            "recorded procedural production requires a seed",
        ):
            validate_asset_production_request_document(missing_seed)

    def test_candidate_role_media_and_shader_stage_are_structurally_discriminated(
        self,
    ) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        invalid_documents = []
        for document_name, collection_name in (
            ("receipt", "outputs"),
            ("selection", "selected_outputs"),
            ("provenance", "candidates"),
        ):
            document = copy.deepcopy(values[document_name])
            assert isinstance(document, dict)
            document[collection_name][0]["role"] = "audio"
            document["content_hash"] = _reseal(document)
            invalid_documents.append((document_name, document))

        license_record = copy.deepcopy(values["license"])
        assert isinstance(license_record, dict)
        license_record["candidate"]["role"] = "audio"
        license_record["content_hash"] = _reseal(license_record)
        invalid_documents.append(("license", license_record))

        validators = {
            "receipt": validate_asset_production_receipt_document,
            "selection": validate_asset_selection_document,
            "provenance": validate_asset_provenance_record_document,
            "license": validate_asset_license_record_document,
        }
        for label, document in invalid_documents:
            with self.subTest(document=label):
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "role.*media|media.*role",
                ):
                    validators[label](document)

        shader_receipt = copy.deepcopy(values["receipt"])
        assert isinstance(shader_receipt, dict)
        shader_receipt["outputs"][0]["role"] = "vertex_shader"
        shader_receipt["outputs"][0]["media_type"] = "text/x-glsl"
        shader_receipt["outputs"][0]["metadata"] = {
            "kind": "glsl",
            "stage": "fragment",
            "line_count": 2,
        }
        shader_receipt["content_hash"] = _reseal(shader_receipt)
        with self.assertRaisesRegex(
            GenericAssetProductionError,
            "role.*stage|stage.*role",
        ):
            validate_asset_production_receipt_document(shader_receipt)

    def test_document_validators_are_structural_not_lineage_claims(self) -> None:
        root = EXAMPLES / "abstract-puzzle" / "assets" / "production" / "board_ui"
        validators = (
            (validate_asset_production_request_document, "request.json"),
            (validate_asset_production_receipt_document, "receipt.json"),
            (validate_asset_selection_document, "selection.json"),
            (validate_asset_provenance_record_document, "provenance.json"),
            (validate_asset_license_record_document, "license.json"),
        )
        for validator, name in validators:
            value = json.loads((root / name).read_text(encoding="utf-8"))
            checked = validator(value)
            self.assertEqual(checked, value)
            self.assertIsNot(checked, value)

    def test_failed_receipt_has_no_outputs_and_cannot_be_selected(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        request = values["request"]
        assert isinstance(request, dict)
        failed = build_asset_production_receipt(
            request,
            gamepack=values["gamepack"],
            subject=values["subject"],
            target=values["target"],
            style=values["style"],
            inventory=values["inventory"],
            specification=values["specification"],
            receipt_id="board_ui_failed_receipt",
            status="failed",
            executed_toolchain=request["toolchain_requirements"],
            candidates=[],
            artifact_root=values["root"],
            execution_evidence={
                "started_evidence_hash": "b" * 64,
                "completed_evidence_hash": "c" * 64,
                "sanitized_log_hashes": [],
            },
            rights_attestation={
                "basis": "fixture_public_domain",
                "evidence_hashes": ["d" * 64],
            },
            failure_reasons=["candidate_generation_failed"],
        )
        self.assertEqual(failed["outputs"], [])
        with self.assertRaisesRegex(GenericAssetProductionError, "failed"):
            build_asset_selection(
                failed,
                request=request,
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
                selection_id="invalid_selection",
                review={
                    "reviewer_id": "fixture_reviewer",
                    "rationale": "A failed result cannot be selected.",
                    "evidence_hashes": ["e" * 64],
                },
            )

    def test_receipt_metadata_and_selection_are_exact_lineage(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        receipt = copy.deepcopy(values["receipt"])
        assert isinstance(receipt, dict)
        receipt["outputs"][0]["metadata"]["width"] = 255
        receipt["content_hash"] = _reseal(receipt)
        validate_asset_production_receipt_document(receipt)
        with self.assertRaisesRegex(GenericAssetProductionError, "byte-derived"):
            validate_asset_production_receipt(
                receipt,
                request=values["request"],
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
            )

        unknown_parent = copy.deepcopy(values["receipt"])
        assert isinstance(unknown_parent, dict)
        unknown_parent["lineage_parents"] = [
            {
                "receipt_id": "missing_parent",
                "content_hash": "1" * 64,
            }
        ]
        unknown_parent["content_hash"] = _reseal(unknown_parent)
        with self.assertRaisesRegex(GenericAssetProductionError, "unknown parent"):
            validate_asset_production_receipt(
                unknown_parent,
                request=values["request"],
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
            )

        selection = copy.deepcopy(values["selection"])
        assert isinstance(selection, dict)
        selection["selected_outputs"][0]["sha256"] = "f" * 64
        selection["content_hash"] = _reseal(selection)
        validate_asset_selection_document(selection)
        with self.assertRaisesRegex(GenericAssetProductionError, "exactly cover"):
            validate_asset_selection(
                selection,
                receipt=values["receipt"],
                request=values["request"],
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
            )

    def test_structural_limits_and_media_metadata_fail_closed(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        request = copy.deepcopy(values["request"])
        assert isinstance(request, dict)
        request["expected_outputs"][0].pop("expectations")
        request["content_hash"] = _reseal(request)
        with self.assertRaisesRegex(GenericAssetProductionError, "invalid|missing"):
            validate_asset_production_request_document(request)

        excessive_version = copy.deepcopy(values["request"])
        assert isinstance(excessive_version, dict)
        excessive_version["operation"]["version"] = 65_536
        excessive_version["content_hash"] = _reseal(excessive_version)
        with self.assertRaisesRegex(GenericAssetProductionError, "65535"):
            validate_asset_production_request_document(excessive_version)

        duplicate_inputs = copy.deepcopy(values["request"])
        assert isinstance(duplicate_inputs, dict)
        duplicate_inputs["input_artifacts"] = [
            {
                "artifact_id": "input_a",
                "role": "reference",
                "locator": "inputs/a.bin",
                "size_bytes": 1,
                "sha256": "1" * 64,
            },
            {
                "artifact_id": "input_b",
                "role": "reference",
                "locator": "inputs/b.bin",
                "size_bytes": 1,
                "sha256": "1" * 64,
            },
        ]
        duplicate_inputs["content_hash"] = _reseal(duplicate_inputs)
        with self.assertRaisesRegex(GenericAssetProductionError, "reuses content hash"):
            validate_asset_production_request_document(duplicate_inputs)

        receipt = copy.deepcopy(values["receipt"])
        assert isinstance(receipt, dict)
        receipt["outputs"][0]["metadata"]["width"] = 0
        receipt["content_hash"] = _reseal(receipt)
        with self.assertRaisesRegex(GenericAssetProductionError, "minimum|invalid"):
            validate_asset_production_receipt_document(receipt)

        too_many_failures = copy.deepcopy(values["receipt"])
        assert isinstance(too_many_failures, dict)
        too_many_failures["status"] = "failed"
        too_many_failures["outputs"] = []
        too_many_failures["failure_reasons"] = [f"failure_{index:02d}" for index in range(65)]
        too_many_failures["content_hash"] = _reseal(too_many_failures)
        with self.assertRaisesRegex(GenericAssetProductionError, "exceeds limit"):
            validate_asset_production_receipt_document(too_many_failures)

        narrative = _load_lineage("branching-narrative", "narrative_ui_font")
        wrong_container = copy.deepcopy(narrative["receipt"])
        assert isinstance(wrong_container, dict)
        wrong_container["outputs"][0]["metadata"]["container"] = "otf"
        wrong_container["content_hash"] = _reseal(wrong_container)
        with self.assertRaisesRegex(GenericAssetProductionError, "container must be ttf"):
            validate_asset_production_receipt_document(wrong_container)

        wrong_count = copy.deepcopy(narrative["receipt"])
        assert isinstance(wrong_count, dict)
        wrong_count["outputs"][0]["metadata"]["glyph_count"] += 1
        wrong_count["content_hash"] = _reseal(wrong_count)
        with self.assertRaisesRegex(GenericAssetProductionError, "glyph_count"):
            validate_asset_production_receipt_document(wrong_count)

        reversed_ranges = copy.deepcopy(narrative["receipt"])
        assert isinstance(reversed_ranges, dict)
        reversed_ranges["outputs"][0]["metadata"]["glyph_ranges"] = ["U+007E-0020"]
        reversed_ranges["content_hash"] = _reseal(reversed_ranges)
        with self.assertRaisesRegex(GenericAssetProductionError, "canonical"):
            validate_asset_production_receipt_document(reversed_ranges)

    def test_bounded_png_and_unicode_font_parsers_reject_ambiguous_bytes(self) -> None:
        def png_chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        def png(scanlines: bytes) -> bytes:
            return (
                b"\x89PNG\r\n\x1a\n"
                + png_chunk(
                    b"IHDR",
                    struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0),
                )
                + png_chunk(b"IDAT", zlib.compress(scanlines))
                + png_chunk(b"IEND", b"")
            )

        expectations = {
            "width": 1,
            "height": 1,
            "color_type": "rgba8",
        }
        with self.assertRaisesRegex(GenericAssetProductionError, "filter"):
            _png_metadata(png(b"\x05\x00\x00\x00\x00"), expectations)
        with self.assertRaisesRegex(GenericAssetProductionError, "exceeds|byte count"):
            _png_metadata(png(b"\x00" + b"\x00" * 4096), expectations)

        cmap_header = struct.pack(">HHHHI", 0, 1, 3, 1, 12)
        format_four = (
            struct.pack(">HHHHHHH", 4, 32, 0, 4, 4, 1, 0)
            + struct.pack(">HH", 0x0041, 0xFFFF)
            + struct.pack(">H", 0)
            + struct.pack(">HH", 0x0041, 0xFFFF)
            + struct.pack(">HH", (-0x0041) & 0xFFFF, 1)
            + struct.pack(">HH", 0, 0)
        )
        zero_glyph_cmap = cmap_header + format_four
        with self.assertRaisesRegex(GenericAssetProductionError, "no supported"):
            _font_cmap_ranges(
                zero_glyph_cmap,
                0,
                len(zero_glyph_cmap),
                2,
            )

        format_twelve = struct.pack(">HHIII", 12, 0, 28, 0, 1) + struct.pack(
            ">III", 0x10000, 0x10002, 1
        )
        supplementary_cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + format_twelve
        self.assertEqual(
            _font_cmap_ranges(
                supplementary_cmap,
                0,
                len(supplementary_cmap),
                4,
            ),
            [(0x10000, 0x10002)],
        )

    def test_glb_inspection_is_byte_bound_and_enforces_every_exact_budget(self) -> None:
        payload = _bounded_glb()
        expectations = {
            "kind": "glb",
            "max_nodes": 1,
            "max_meshes": 1,
            "max_primitives": 1,
            "max_materials": 1,
            "max_joints": 1,
            "max_animations": 1,
            "max_triangles": 1,
            "max_bytes": len(payload),
        }
        with tempfile.TemporaryDirectory(prefix="world-forge-legacy-glb-inspection-") as temporary:
            path = Path(temporary) / "fixture.glb"
            path.write_bytes(payload)
            legacy = generic_gltf.inspect_glb(path)
        self.assertNotIn("production_metrics", legacy)
        with (
            mock.patch(
                "tempfile.NamedTemporaryFile",
                side_effect=AssertionError("GLB inspection must not create named temporaries"),
            ),
            mock.patch(
                "os.unlink",
                side_effect=AssertionError("GLB inspection must not unlink by pathname"),
            ),
        ):
            direct = generic_gltf.inspect_glb_bytes(payload, max_bytes=len(payload))
            metadata = _glb_metadata(payload, expectations)
        self.assertEqual(
            direct["production_metrics"],
            {
                "nodes": 1,
                "meshes": 1,
                "primitives": 1,
                "materials": 1,
                "joints": 1,
                "animations": 1,
                "triangles": 1,
            },
        )
        self.assertEqual(
            legacy,
            {key: value for key, value in direct.items() if key != "production_metrics"},
        )
        self.assertEqual(metadata["metrics"], direct["production_metrics"])

        for field in (
            "max_nodes",
            "max_meshes",
            "max_primitives",
            "max_materials",
            "max_joints",
            "max_animations",
            "max_triangles",
        ):
            crossed = dict(expectations)
            crossed[field] = 0
            with self.subTest(budget=field):
                with self.assertRaisesRegex(GenericAssetProductionError, "budget"):
                    _glb_metadata(payload, crossed)

    def test_selection_revalidates_the_exact_integral_receipt_chain(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        forged_receipt = copy.deepcopy(values["receipt"])
        assert isinstance(forged_receipt, dict)
        forged_receipt["request"]["content_hash"] = "8" * 64
        forged_receipt["content_hash"] = _reseal(forged_receipt)
        forged_selection = copy.deepcopy(values["selection"])
        assert isinstance(forged_selection, dict)
        forged_selection["receipt"] = {
            "format": forged_receipt["format"],
            "format_version": forged_receipt["format_version"],
            "id": forged_receipt["receipt_id"],
            "content_hash": forged_receipt["content_hash"],
        }
        forged_selection["content_hash"] = _reseal(forged_selection)
        with self.assertRaisesRegex(GenericAssetProductionError, "request identity"):
            validate_asset_selection(
                forged_selection,
                receipt=forged_receipt,
                request=values["request"],
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
            )

    def test_receipt_revalidates_retained_input_bytes(self) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                values,
                root,
                "external_authoring",
                include_input=True,
            )
            input_path = root / "inputs" / "source.bin"
            original = input_path.read_bytes()
            validation = {
                key: chain[key]
                for key in (
                    "request",
                    "gamepack",
                    "subject",
                    "target",
                    "style",
                    "inventory",
                    "specification",
                )
            }
            input_path.write_bytes(b"x" * len(original))
            with self.assertRaisesRegex(GenericAssetProductionError, "sha256"):
                validate_asset_production_receipt(
                    chain["receipt"],
                    artifact_root=root,
                    **validation,
                )

            input_path.write_bytes(original[:-1])
            with self.assertRaisesRegex(GenericAssetProductionError, "size_bytes"):
                validate_asset_production_receipt(
                    chain["receipt"],
                    artifact_root=root,
                    **validation,
                )

    def test_receipt_graph_is_exactly_the_root_transitive_closure(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        common = {
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        }
        request = values["request"]
        assert isinstance(request, dict)
        root = values["root"]
        assert isinstance(root, Path)

        def build_receipt(
            receipt_id: str,
            candidate_id: str,
            parents: tuple[dict[str, object], ...] = (),
        ) -> dict[str, object]:
            return build_asset_production_receipt(
                request,
                **common,
                receipt_id=receipt_id,
                status="completed",
                executed_toolchain=request["toolchain_requirements"],
                candidates=[
                    {
                        "role": "texture",
                        "candidate_artifact_id": candidate_id,
                        "locator": ("assets/production/board_ui/candidates/board.png"),
                    }
                ],
                artifact_root=root,
                parent_receipts=parents,
                execution_evidence={
                    "started_evidence_hash": hashlib.sha256(
                        f"{receipt_id}:start".encode()
                    ).hexdigest(),
                    "completed_evidence_hash": hashlib.sha256(
                        f"{receipt_id}:complete".encode()
                    ).hexdigest(),
                    "sanitized_log_hashes": [],
                },
                rights_attestation={
                    "basis": "fixture_public_domain",
                    "evidence_hashes": [
                        hashlib.sha256(f"{receipt_id}:rights".encode()).hexdigest()
                    ],
                },
            )

        parent_a = build_receipt("parent_receipt_a", "parent_candidate_a")
        parent_b = build_receipt(
            "parent_receipt_b",
            "parent_candidate_b",
            (parent_a,),
        )

        with self.assertRaisesRegex(GenericAssetProductionError, "repeats the root"):
            validate_asset_production_receipt(
                values["receipt"],
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(values["receipt"],),
            )
        with self.assertRaisesRegex(GenericAssetProductionError, "outside the root"):
            validate_asset_production_receipt(
                values["receipt"],
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(parent_a,),
            )

        root_receipt = build_receipt(
            "root_receipt",
            "root_candidate",
            (parent_a, parent_b),
        )
        root_receipt["lineage_parents"] = [
            {
                "receipt_id": parent_b["receipt_id"],
                "content_hash": parent_b["content_hash"],
            }
        ]
        root_receipt["content_hash"] = _reseal(root_receipt)
        closure = (parent_a, parent_b)
        validated_root = validate_asset_production_receipt(
            root_receipt,
            request=request,
            **common,
            artifact_root=root,
            parent_receipts=closure,
        )
        selection = build_asset_selection(
            validated_root,
            request=request,
            **common,
            artifact_root=root,
            parent_receipts=closure,
            selection_id="root_selection",
            review={
                "reviewer_id": "fixture_reviewer",
                "rationale": "The transitive receipt graph is complete.",
                "evidence_hashes": ["9" * 64],
            },
        )
        provenance = build_asset_provenance_record(
            selection,
            receipt=validated_root,
            request=request,
            **common,
            artifact_root=root,
            parent_receipts=closure,
            provenance_id="root_provenance",
            component_evidence=_component_evidence(request),
        )
        lineage = {item["node_id"]: item for item in provenance["lineage"]}
        self.assertEqual(
            lineage["parent_receipt_b"]["parent_hashes"],
            [parent_a["content_hash"]],
        )
        self.assertEqual(
            lineage["root_candidate"]["parent_hashes"],
            [parent_b["content_hash"]],
        )

        direct_root = build_receipt(
            "integral_parent_root",
            "integral_parent_candidate",
            (parent_a,),
        )

        def root_for(parent: dict[str, object]) -> dict[str, object]:
            result = copy.deepcopy(direct_root)
            result["lineage_parents"] = [
                {
                    "receipt_id": parent["receipt_id"],
                    "content_hash": parent["content_hash"],
                }
            ]
            result["content_hash"] = _reseal(result)
            return result

        crossed_request_parent = copy.deepcopy(parent_a)
        crossed_request_parent["request"]["content_hash"] = "8" * 64
        crossed_request_parent["content_hash"] = _reseal(crossed_request_parent)
        with self.assertRaisesRegex(GenericAssetProductionError, "request identity"):
            validate_asset_production_receipt(
                root_for(crossed_request_parent),
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(crossed_request_parent,),
            )

        crossed_toolchain_parent = copy.deepcopy(parent_a)
        crossed_toolchain_parent["executed_toolchain"]["tool_id"] = "crossed_generator"
        crossed_toolchain_parent["content_hash"] = _reseal(crossed_toolchain_parent)
        with self.assertRaisesRegex(GenericAssetProductionError, "toolchain"):
            validate_asset_production_receipt(
                root_for(crossed_toolchain_parent),
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(crossed_toolchain_parent,),
            )

        forged_output_parent = copy.deepcopy(parent_a)
        forged_output_parent["outputs"][0]["metadata"]["width"] = 255
        forged_output_parent["content_hash"] = _reseal(forged_output_parent)
        with self.assertRaisesRegex(GenericAssetProductionError, "byte-derived"):
            validate_asset_production_receipt(
                root_for(forged_output_parent),
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(forged_output_parent,),
            )

        failed_parent = build_asset_production_receipt(
            request,
            **common,
            receipt_id="failed_lineage_parent",
            status="failed",
            executed_toolchain=request["toolchain_requirements"],
            candidates=[],
            artifact_root=root,
            execution_evidence={
                "started_evidence_hash": "1" * 64,
                "completed_evidence_hash": "2" * 64,
                "sanitized_log_hashes": [],
            },
            rights_attestation={
                "basis": "fixture_public_domain",
                "evidence_hashes": ["3" * 64],
            },
            failure_reasons=["candidate_generation_failed"],
        )
        with self.assertRaisesRegex(GenericAssetProductionError, "must be completed"):
            validate_asset_production_receipt(
                root_for(failed_parent),
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=(failed_parent,),
            )

    def test_selected_and_rejected_receipts_use_independent_explicit_closures(
        self,
    ) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        common = {
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        }
        root = values["root"]
        assert isinstance(root, Path)
        request = load_asset_production_request(
            root / "assets" / "production" / "board_ui" / "request.json",
            **common,
        )

        def build_receipt(
            receipt_id: str,
            candidate_id: str,
            parents: tuple[dict[str, object], ...] = (),
        ) -> dict[str, object]:
            return build_asset_production_receipt(
                request,
                **common,
                receipt_id=receipt_id,
                status="completed",
                executed_toolchain=request["toolchain_requirements"],
                candidates=[
                    {
                        "role": "texture",
                        "candidate_artifact_id": candidate_id,
                        "locator": "assets/production/board_ui/candidates/board.png",
                    }
                ],
                artifact_root=root,
                parent_receipts=parents,
                execution_evidence={
                    "started_evidence_hash": hashlib.sha256(
                        f"{receipt_id}:start".encode()
                    ).hexdigest(),
                    "completed_evidence_hash": hashlib.sha256(
                        f"{receipt_id}:complete".encode()
                    ).hexdigest(),
                    "sanitized_log_hashes": [],
                },
                rights_attestation={
                    "basis": "fixture_public_domain",
                    "evidence_hashes": [
                        hashlib.sha256(f"{receipt_id}:rights".encode()).hexdigest()
                    ],
                },
            )

        selected_parent = build_receipt(
            "selected_parent_receipt",
            "selected_parent_candidate",
        )
        rejected_parent = build_receipt(
            "rejected_parent_receipt",
            "rejected_parent_candidate",
        )
        selected_root = build_receipt(
            "selected_root_receipt",
            "selected_root_candidate",
            (selected_parent,),
        )
        rejected_root = build_receipt(
            "rejected_root_receipt",
            "rejected_root_candidate",
            (rejected_parent,),
        )
        closures = {
            "selected_root_receipt": (selected_parent,),
            "rejected_root_receipt": (rejected_parent,),
        }
        rejected = [
            {
                "candidate_artifact_id": "rejected_root_candidate",
                "receipt": {
                    "format": rejected_root["format"],
                    "format_version": rejected_root["format_version"],
                    "id": rejected_root["receipt_id"],
                    "content_hash": rejected_root["content_hash"],
                },
                "reason_code": "candidate_not_selected",
            }
        ]
        selection = build_asset_selection(
            selected_root,
            request=request,
            **common,
            artifact_root=root,
            receipt_parent_closures=closures,
            rejected_receipts=(rejected_root,),
            rejected_candidates=rejected,
            selection_id="independent_closure_selection",
            review={
                "reviewer_id": "fixture_reviewer",
                "rationale": "Each candidate receipt has an exact independent closure.",
                "evidence_hashes": ["9" * 64],
            },
        )
        self.assertEqual(selection["receipt_lineage"]["format_version"], 1)
        self.assertEqual(
            [item["root"]["id"] for item in selection["receipt_lineage"]["closures"]],
            ["rejected_root_receipt", "selected_root_receipt"],
        )
        provenance = build_asset_provenance_record(
            selection,
            receipt=selected_root,
            request=request,
            **common,
            artifact_root=root,
            receipt_parent_closures=closures,
            rejected_receipts=(rejected_root,),
            provenance_id="independent_closure_provenance",
            component_evidence=_component_evidence(request),
        )
        license_record = build_asset_license_record(
            provenance,
            selection=selection,
            receipt=selected_root,
            request=request,
            **common,
            artifact_root=root,
            receipt_parent_closures=closures,
            rejected_receipts=(rejected_root,),
            license_record_id="independent_closure_license",
            candidate_artifact_id="selected_root_candidate",
            license_basis={"kind": "spdx", "identifier": "CC0-1.0"},
            copyright={
                "holder": "World Forge fixture authors",
                "year_policy": "fixed",
                "year": 2026,
            },
            permissions={
                "commercial_use": True,
                "modification": True,
                "redistribution": True,
            },
            obligations={
                "attribution_required": False,
                "notice_required": True,
                "source_offer_required": False,
            },
            component_licenses=[
                {
                    "scope": component["scope"],
                    "component_id": component["component_id"],
                    "identifier": "CC0-1.0",
                    "evidence_hash": component["evidence_hash"],
                }
                for component in provenance["components"]
            ],
            runtime_notice_text="Fixture asset license notice.",
            evidence_hashes=["a" * 64],
        )
        self.assertEqual(
            license_record["candidate"]["candidate_artifact_id"],
            "selected_root_candidate",
        )

        invalid_closures = {
            "missing": {
                "selected_root_receipt": (selected_parent,),
            },
            "extra": {
                **closures,
                "orphan_root_receipt": (),
            },
            "swapped": {
                "selected_root_receipt": (rejected_parent,),
                "rejected_root_receipt": (selected_parent,),
            },
            "orphan_parent": {
                "selected_root_receipt": (selected_parent, rejected_parent),
                "rejected_root_receipt": (rejected_parent,),
            },
        }
        for label, invalid in invalid_closures.items():
            with self.subTest(closure=label):
                with self.assertRaises(GenericAssetProductionError):
                    build_asset_selection(
                        selected_root,
                        request=request,
                        **common,
                        artifact_root=root,
                        receipt_parent_closures=invalid,
                        rejected_receipts=(rejected_root,),
                        rejected_candidates=rejected,
                        selection_id=f"invalid_{label}_selection",
                        review={
                            "reviewer_id": "fixture_reviewer",
                            "rationale": "Invalid closure evidence must fail closed.",
                            "evidence_hashes": ["b" * 64],
                        },
                    )

        cyclic_parent = copy.deepcopy(selected_parent)
        cyclic_parent["lineage_parents"] = [
            {
                "receipt_id": selected_root["receipt_id"],
                "content_hash": selected_root["content_hash"],
            }
        ]
        cyclic_parent["content_hash"] = _reseal(cyclic_parent)
        cyclic_root = copy.deepcopy(selected_root)
        cyclic_root["lineage_parents"] = [
            {
                "receipt_id": cyclic_parent["receipt_id"],
                "content_hash": cyclic_parent["content_hash"],
            }
        ]
        cyclic_root["content_hash"] = _reseal(cyclic_root)
        with self.assertRaisesRegex(GenericAssetProductionError, "cycle|unknown parent"):
            build_asset_selection(
                cyclic_root,
                request=request,
                **common,
                artifact_root=root,
                receipt_parent_closures={
                    "selected_root_receipt": (cyclic_parent,),
                    "rejected_root_receipt": (rejected_parent,),
                },
                rejected_receipts=(rejected_root,),
                rejected_candidates=rejected,
                selection_id="cyclic_closure_selection",
                review={
                    "reviewer_id": "fixture_reviewer",
                    "rationale": "Cyclic closure evidence must fail closed.",
                    "evidence_hashes": ["c" * 64],
                },
            )

    def test_receipt_graph_reuses_only_exact_shared_input_nodes(self) -> None:
        values = _load_planning("abstract-puzzle", "board_ui")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                values,
                root,
                "external_authoring",
                include_input=True,
            )
            common = {
                key: chain[key]
                for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
            }
            request = chain["request"]
            assert isinstance(request, dict)

            def build_receipt(
                receipt_id: str,
                candidate_id: str,
                parents: tuple[dict[str, object], ...] = (),
            ) -> dict[str, object]:
                return build_asset_production_receipt(
                    request,
                    **common,
                    receipt_id=receipt_id,
                    status="completed",
                    executed_toolchain=request["toolchain_requirements"],
                    candidates=[
                        {
                            "role": "texture",
                            "candidate_artifact_id": candidate_id,
                            "locator": "candidates/board.png",
                        }
                    ],
                    artifact_root=root,
                    parent_receipts=parents,
                    execution_evidence={
                        "started_evidence_hash": hashlib.sha256(
                            f"{receipt_id}:start".encode()
                        ).hexdigest(),
                        "completed_evidence_hash": hashlib.sha256(
                            f"{receipt_id}:complete".encode()
                        ).hexdigest(),
                        "sanitized_log_hashes": [],
                    },
                    rights_attestation={
                        "basis": "fixture_public_domain",
                        "evidence_hashes": [
                            hashlib.sha256(f"{receipt_id}:rights".encode()).hexdigest()
                        ],
                    },
                )

            parent_a = build_receipt("shared_parent_a", "shared_candidate_a")
            parent_b = build_receipt(
                "shared_parent_b",
                "shared_candidate_b",
                (parent_a,),
            )
            root_receipt = build_receipt(
                "shared_root_receipt",
                "shared_root_candidate",
                (parent_a, parent_b),
            )
            root_receipt["lineage_parents"] = [
                {
                    "receipt_id": parent_b["receipt_id"],
                    "content_hash": parent_b["content_hash"],
                }
            ]
            root_receipt["content_hash"] = _reseal(root_receipt)
            closure = (parent_a, parent_b)
            validated = validate_asset_production_receipt(
                root_receipt,
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=closure,
            )
            selection = build_asset_selection(
                validated,
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=closure,
                selection_id="shared_root_selection",
                review={
                    "reviewer_id": "fixture_reviewer",
                    "rationale": "The exact shared source is represented once.",
                    "evidence_hashes": ["9" * 64],
                },
            )
            provenance = build_asset_provenance_record(
                selection,
                receipt=validated,
                request=request,
                **common,
                artifact_root=root,
                parent_receipts=closure,
                provenance_id="shared_root_provenance",
                component_evidence=_component_evidence(request),
            )
            lineage = {item["node_id"]: item for item in provenance["lineage"]}
            self.assertEqual(
                [item["node_id"] for item in provenance["lineage"]].count("source_input"),
                1,
            )
            input_hash = request["input_artifacts"][0]["sha256"]
            self.assertEqual(lineage["shared_parent_a"]["parent_hashes"], [input_hash])
            self.assertEqual(
                lineage["shared_parent_b"]["parent_hashes"],
                sorted([input_hash, parent_a["content_hash"]]),
            )
            self.assertEqual(
                lineage["shared_root_candidate"]["parent_hashes"],
                sorted([input_hash, parent_b["content_hash"]]),
            )

            direct_root = build_receipt(
                "inconsistent_root_receipt",
                "inconsistent_root_candidate",
                (parent_a,),
            )
            inconsistent_parent = copy.deepcopy(parent_a)
            inconsistent_parent["input_artifacts"][0]["role"] = "source"
            inconsistent_parent["content_hash"] = _reseal(inconsistent_parent)
            direct_root["lineage_parents"] = [
                {
                    "receipt_id": inconsistent_parent["receipt_id"],
                    "content_hash": inconsistent_parent["content_hash"],
                }
            ]
            direct_root["content_hash"] = _reseal(direct_root)
            with self.assertRaisesRegex(GenericAssetProductionError, "inputs do not match"):
                validate_asset_production_receipt(
                    direct_root,
                    request=request,
                    **common,
                    artifact_root=root,
                    parent_receipts=(inconsistent_parent,),
                )

            alias_parent = copy.deepcopy(parent_a)
            alias_parent["input_artifacts"][0]["artifact_id"] = "source_alias"
            alias_parent["content_hash"] = _reseal(alias_parent)
            alias_root = copy.deepcopy(direct_root)
            alias_root["lineage_parents"] = [
                {
                    "receipt_id": alias_parent["receipt_id"],
                    "content_hash": alias_parent["content_hash"],
                }
            ]
            alias_root["content_hash"] = _reseal(alias_root)
            with self.assertRaisesRegex(GenericAssetProductionError, "inputs do not match"):
                validate_asset_production_receipt(
                    alias_root,
                    request=request,
                    **common,
                    artifact_root=root,
                    parent_receipts=(alias_parent,),
                )

    def test_procedural_lineage_cannot_fabricate_ai_components_or_licenses(self) -> None:
        values = _load_lineage("branching-narrative", "narrative_ui_font")
        provenance = copy.deepcopy(values["provenance"])
        assert isinstance(provenance, dict)
        provenance["components"].append(
            {
                "scope": "model",
                "component_id": "invented_model",
                "component_version": "1.0.0",
                "evidence_hash": "1" * 64,
            }
        )
        provenance["components"].sort(
            key=lambda item: (item["scope"].encode(), item["component_id"].encode())
        )
        provenance["content_hash"] = _reseal(provenance)
        validate_asset_provenance_record_document(provenance)
        with self.assertRaisesRegex(GenericAssetProductionError, "component"):
            validate_asset_provenance_record(
                provenance,
                selection=values["selection"],
                receipt=values["receipt"],
                request=values["request"],
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                artifact_root=values["root"],
            )

        license_record = copy.deepcopy(values["license"])
        assert isinstance(license_record, dict)
        license_record["component_licenses"] = []
        license_record["content_hash"] = _reseal(license_record)
        with self.assertRaises(GenericAssetProductionError):
            validate_asset_license_record_document(license_record)

    def test_contracts_reject_prompt_url_secret_and_nonportable_locator_fields(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        request = copy.deepcopy(values["request"])
        assert isinstance(request, dict)
        for mutate in (
            lambda value: value.update({"prompt": "draw a board"}),
            lambda value: value["input_artifacts"].append(
                {
                    "artifact_id": "unsafe_input",
                    "role": "reference",
                    "locator": "../secret.png",
                    "size_bytes": 1,
                    "sha256": "1" * 64,
                }
            ),
            lambda value: value["operation"].update(
                {"operation_id": "https://example.invalid/generate"}
            ),
        ):
            candidate = copy.deepcopy(request)
            mutate(candidate)
            candidate["content_hash"] = _reseal(candidate)
            with self.assertRaises(GenericAssetProductionError):
                validate_asset_production_request_document(candidate)

        for authoring_notice in (
            "Provider model seed details.",
            "Dataset details are unavailable.",
            "Datasets and models are unavailable.",
            "MCPs are unavailable.",
        ):
            license_record = copy.deepcopy(values["license"])
            assert isinstance(license_record, dict)
            license_record["runtime_notice"]["text"] = authoring_notice
            license_record["runtime_notice"]["sha256"] = hashlib.sha256(
                license_record["runtime_notice"]["text"].encode()
            ).hexdigest()
            license_record["content_hash"] = _reseal(license_record)
            with self.subTest(runtime_notice=authoring_notice):
                with self.assertRaisesRegex(GenericAssetProductionError, "authoring"):
                    validate_asset_license_record_document(license_record)

        for credential_notice in (
            "eyJhbGciOiJIUzI1NiJ9.payload.signature",
            (
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
            "Bearer abcdefghijklmnop",
            "  bearer ABCDEFGHIJKLMNOP==",
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            (
                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
            "sk-abcdefghijklmnop1234567890",
            "AKIAABCDEFGHIJKLMNOP",
            "Authorization: Bearer abcdefghijklmnop",
            (
                "Authorization: Bearer "
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
            "Authorization: Basic Zml4dHVyZTpwYXNzd29yZA==",
            "-----BEGIN PRIVATE KEY-----",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        ):
            license_record = copy.deepcopy(values["license"])
            assert isinstance(license_record, dict)
            license_record["runtime_notice"]["text"] = credential_notice
            license_record["runtime_notice"]["sha256"] = hashlib.sha256(
                credential_notice.encode()
            ).hexdigest()
            license_record["content_hash"] = _reseal(license_record)
            with self.subTest(runtime_notice=credential_notice):
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "secret|credential|unsafe",
                ):
                    validate_asset_license_record_document(license_record)

        for narrative_notice in (
            "The bearer crossed the valley.",
            "The bearer approached the eastern gate.",
            "THE BEARER APPROACHED THE EASTERN GATE.",
            "The Bearer, approached the eastern gate.",
            "The bearer—approached the eastern gate.",
            "The bearer approached the eastern gate!",
            "A skillful scout keeps the key to the eastern gate.",
            "The privateer keeps watch.",
            "LicenseRef.WorldForge.Fixture",
            "chapter.one.final",
            "Chapter.one.final remains the narrative identifier.",
        ):
            license_record = copy.deepcopy(values["license"])
            assert isinstance(license_record, dict)
            license_record["runtime_notice"]["text"] = narrative_notice
            license_record["runtime_notice"]["sha256"] = hashlib.sha256(
                narrative_notice.encode()
            ).hexdigest()
            license_record["content_hash"] = _reseal(license_record)
            with self.subTest(runtime_notice=narrative_notice):
                self.assertEqual(
                    validate_asset_license_record_document(license_record),
                    license_record,
                )

        for code_points, utf8_bytes, accepted in (
            (1000, 4000, True),
            (1001, 4004, True),
            (1024, 4096, True),
            (1025, 4100, False),
        ):
            astral_notice = "😀" * code_points
            self.assertEqual(len(astral_notice), code_points)
            self.assertEqual(len(astral_notice.encode("utf-8")), utf8_bytes)
            license_record = copy.deepcopy(values["license"])
            assert isinstance(license_record, dict)
            license_record["runtime_notice"]["text"] = astral_notice
            license_record["runtime_notice"]["sha256"] = hashlib.sha256(
                astral_notice.encode()
            ).hexdigest()
            license_record["content_hash"] = _reseal(license_record)
            with self.subTest(code_points=code_points, utf8_bytes=utf8_bytes):
                if accepted:
                    self.assertEqual(
                        validate_asset_license_record_document(license_record),
                        license_record,
                    )
                else:
                    with self.assertRaisesRegex(
                        GenericAssetProductionError,
                        "exceeds 1024 characters",
                    ):
                        validate_asset_license_record_document(license_record)

        unapproved = copy.deepcopy(values["license"])
        assert isinstance(unapproved, dict)
        unapproved["license_basis"] = {
            "kind": "custom",
            "identifier": "LicenseRef-Unreviewed-Custom-Terms",
        }
        unapproved["content_hash"] = _reseal(unapproved)
        with self.assertRaisesRegex(GenericAssetProductionError, "not approved"):
            validate_asset_license_record_document(unapproved)

        crossed_spdx = copy.deepcopy(values["license"])
        assert isinstance(crossed_spdx, dict)
        crossed_spdx["license_basis"] = {
            "kind": "spdx",
            "identifier": "LicenseRef-WorldForge-Fixture-Public-Domain",
        }
        crossed_spdx["content_hash"] = _reseal(crossed_spdx)
        with self.assertRaisesRegex(GenericAssetProductionError, "cannot use LicenseRef"):
            validate_asset_license_record_document(crossed_spdx)

        unapproved_component = copy.deepcopy(values["license"])
        assert isinstance(unapproved_component, dict)
        unapproved_component["component_licenses"][0]["identifier"] = (
            "LicenseRef-Unreviewed-Custom-Terms"
        )
        unapproved_component["content_hash"] = _reseal(unapproved_component)
        with self.assertRaisesRegex(GenericAssetProductionError, "not approved"):
            validate_asset_license_record_document(unapproved_component)

        for policy, year in (("fixed", None), ("not_applicable", 2026)):
            invalid_year = copy.deepcopy(values["license"])
            assert isinstance(invalid_year, dict)
            invalid_year["copyright"]["year_policy"] = policy
            invalid_year["copyright"]["year"] = year
            invalid_year["content_hash"] = _reseal(invalid_year)
            with self.subTest(year_policy=policy):
                with self.assertRaisesRegex(GenericAssetProductionError, "year"):
                    validate_asset_license_record_document(invalid_year)

        for unsafe_text in (
            r"C:\Build\artifacts\authoring.bin",
            r"\\server\share\authoring.bin",
            "@scope/package@1.0.0",
            "Cafe\u0301 public domain.",
        ):
            unsafe_selection = copy.deepcopy(values["selection"])
            assert isinstance(unsafe_selection, dict)
            unsafe_selection["review"]["rationale"] = unsafe_text
            unsafe_selection["content_hash"] = _reseal(unsafe_selection)
            with self.assertRaisesRegex(GenericAssetProductionError, "unsafe|NFC"):
                validate_asset_selection_document(unsafe_selection)

    def test_oversized_jwt_headers_fail_closed_at_every_public_license_entry(
        self,
    ) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")

        def license_with_notice(notice: str) -> dict[str, object]:
            candidate = copy.deepcopy(values["license"])
            assert isinstance(candidate, dict)
            candidate["runtime_notice"]["text"] = notice
            candidate["runtime_notice"]["sha256"] = hashlib.sha256(notice.encode()).hexdigest()
            candidate["content_hash"] = _reseal(candidate)
            return candidate

        boundary_tokens = (
            (_valid_hs256_jwt(header_padding=348), 512),
            (_valid_hs256_jwt(header_padding=349), 514),
            (_valid_hs256_jwt(header_padding=600), 848),
        )
        for token, header_length in boundary_tokens:
            header, payload, signature = token.split(".")
            self.assertEqual(len(header), header_length)
            expected_signature = _base64url(
                hmac.new(
                    _JWT_TEST_KEY,
                    f"{header}.{payload}".encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
            self.assertTrue(hmac.compare_digest(signature, expected_signature))
            with self.subTest(header_length=header_length):
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "credential",
                ):
                    validate_asset_license_record_document(license_with_notice(token))

        oversized_jwt = boundary_tokens[-1][0]
        self.assertEqual(len(oversized_jwt), 939)
        canonical_jwt, noncanonical_jwt = _canonical_and_noncanonical_hs256_jwts()
        canonical_header, canonical_payload, canonical_signature = canonical_jwt.split(".")
        (
            noncanonical_header,
            noncanonical_payload,
            noncanonical_signature,
        ) = noncanonical_jwt.split(".")
        self.assertNotEqual(noncanonical_header, canonical_header)
        self.assertEqual(
            base64.urlsafe_b64decode(
                f"{noncanonical_header}{'=' * (-len(noncanonical_header) % 4)}"
            ),
            base64.urlsafe_b64decode(f"{canonical_header}{'=' * (-len(canonical_header) % 4)}"),
        )
        for header, payload, signature in (
            (canonical_header, canonical_payload, canonical_signature),
            (noncanonical_header, noncanonical_payload, noncanonical_signature),
        ):
            self.assertEqual(
                signature,
                _base64url(
                    hmac.new(
                        _JWT_TEST_KEY,
                        f"{header}.{payload}".encode("ascii"),
                        hashlib.sha256,
                    ).digest()
                ),
            )

        lineage = {
            key: values[key]
            for key in (
                "provenance",
                "selection",
                "receipt",
                "request",
                "gamepack",
                "subject",
                "target",
                "style",
                "inventory",
                "specification",
                "artifact_root",
            )
            if key in values
        }
        lineage["artifact_root"] = values["root"]

        license_fixture = values["license"]
        assert isinstance(license_fixture, dict)
        candidate = license_fixture["candidate"]
        assert isinstance(candidate, dict)

        def public_entries(
            notice: str,
            *,
            destination: Path,
            record_id: str,
        ) -> tuple[Callable[[], object], ...]:
            return (
                lambda: validate_asset_license_record_document(license_with_notice(notice)),
                lambda: validate_asset_license_record(
                    license_with_notice(notice),
                    **lineage,
                ),
                lambda: serialize_production_contract(license_with_notice(notice)),
                lambda: publish_asset_license_record(
                    destination,
                    license_with_notice(notice),
                    **lineage,
                ),
                lambda: build_asset_license_record(
                    values["provenance"],
                    selection=values["selection"],
                    receipt=values["receipt"],
                    request=values["request"],
                    gamepack=values["gamepack"],
                    subject=values["subject"],
                    target=values["target"],
                    style=values["style"],
                    inventory=values["inventory"],
                    specification=values["specification"],
                    artifact_root=values["root"],
                    license_record_id=record_id,
                    candidate_artifact_id=str(candidate["candidate_artifact_id"]),
                    license_basis=copy.deepcopy(license_fixture["license_basis"]),
                    copyright=copy.deepcopy(license_fixture["copyright"]),
                    permissions=copy.deepcopy(license_fixture["permissions"]),
                    obligations=copy.deepcopy(license_fixture["obligations"]),
                    component_licenses=copy.deepcopy(license_fixture["component_licenses"]),
                    runtime_notice_text=notice,
                    evidence_hashes=copy.deepcopy(license_fixture["evidence_hashes"]),
                ),
            )

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for label, notice in (
                ("oversized", oversized_jwt),
                ("canonical", canonical_jwt),
                ("noncanonical", noncanonical_jwt),
            ):
                destination = temporary_root / f"{label}-jwt-license.json"
                for entry in public_entries(
                    notice,
                    destination=destination,
                    record_id=f"{label}_jwt_license",
                ):
                    with self.subTest(label=label, entry=entry):
                        with self.assertRaisesRegex(
                            GenericAssetProductionError,
                            "credential",
                        ):
                            entry()

                source = temporary_root / f"{label}-jwt-source.json"
                source.write_text(
                    json.dumps(license_with_notice(notice)),
                    encoding="utf-8",
                )
                with self.subTest(label=label, entry="load"):
                    with self.assertRaisesRegex(
                        GenericAssetProductionError,
                        "credential",
                    ):
                        load_asset_license_record(source, **lineage)

            dotted_notice = "chapter.one.final"
            destination = temporary_root / "dotted-license.json"

            def write_fixture(
                path: Path,
                document: object,
                *,
                durable_parent: bool,
            ) -> None:
                self.assertTrue(durable_parent)
                path.write_bytes(canonical_json_bytes(document) + b"\n")

            with mock.patch(
                "worldforge.generic_asset_production.write_json_atomic",
                side_effect=write_fixture,
            ):
                accepted = [
                    entry()
                    for entry in public_entries(
                        dotted_notice,
                        destination=destination,
                        record_id="dotted_notice_license",
                    )
                ]
            self.assertEqual(len(accepted), 5)
            source = temporary_root / "dotted-source.json"
            source.write_text(
                json.dumps(license_with_notice(dotted_notice)),
                encoding="utf-8",
            )
            loaded = load_asset_license_record(source, **lineage)
            self.assertEqual(loaded["runtime_notice"]["text"], dotted_notice)

    def test_runtime_notice_preflight_bounds_every_public_license_entry(self) -> None:
        class CountingNotice(str):
            reads: int

            def __new__(cls, value: str) -> CountingNotice:
                instance = super().__new__(cls, value)
                instance.reads = 0
                return instance

            def __getitem__(self, key: object) -> str:
                self.reads += 1
                if self.reads > 1025:
                    raise AssertionError("runtime notice preflight exceeded 1025 indexed reads")
                return super().__getitem__(key)

        values = _load_lineage("abstract-puzzle", "board_ui")

        counted_notice = CountingNotice("x" * 1_000_000)
        counted_license = copy.deepcopy(values["license"])
        assert isinstance(counted_license, dict)
        counted_license["runtime_notice"]["text"] = counted_notice
        with (
            mock.patch(
                "worldforge.creation_contracts.unicodedata.normalize",
                side_effect=AssertionError("NFC normalization ran before the raw notice limit"),
            ) as normalize,
            mock.patch(
                "worldforge.generic_asset_production._validate_license_structure",
                side_effect=AssertionError("license traversal ran before the raw notice limit"),
            ) as structural_validation,
        ):
            with self.assertRaisesRegex(
                GenericAssetProductionError,
                "exceeds 1024 characters",
            ):
                validate_asset_license_record_document(counted_license)
        self.assertEqual(counted_notice.reads, 1025)
        normalize.assert_not_called()
        structural_validation.assert_not_called()

        def oversized_license() -> dict[str, object]:
            candidate = copy.deepcopy(values["license"])
            assert isinstance(candidate, dict)
            candidate["runtime_notice"]["text"] = "x" * 1_000_000
            return candidate

        lineage = {
            "provenance": None,
            "selection": None,
            "receipt": None,
            "request": None,
            "gamepack": None,
            "subject": None,
            "target": None,
            "style": None,
            "inventory": None,
            "specification": None,
            "artifact_root": values["root"],
        }
        public_entries = (
            lambda: validate_asset_license_record_document(oversized_license()),
            lambda: validate_asset_license_record(oversized_license(), **lineage),
            lambda: serialize_production_contract(oversized_license()),
            lambda: publish_asset_license_record(
                values["root"] / "oversized-license.json",
                oversized_license(),
                **lineage,
            ),
        )
        for entry in public_entries:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "exceeds 1024 characters",
                ):
                    entry()

        with mock.patch(
            "worldforge.generic_asset_production.validate_asset_production_request",
            side_effect=AssertionError("lineage validation ran before the raw notice limit"),
        ) as request_validation:
            with self.assertRaisesRegex(
                GenericAssetProductionError,
                "exceeds 1024 characters",
            ):
                build_asset_license_record(
                    None,
                    selection=None,
                    receipt=None,
                    request=None,
                    gamepack=None,
                    subject=None,
                    target=None,
                    style=None,
                    inventory=None,
                    specification=None,
                    artifact_root=values["root"],
                    license_record_id="oversized_license",
                    candidate_artifact_id="oversized_candidate",
                    license_basis=None,
                    copyright=None,
                    permissions=None,
                    obligations=None,
                    component_licenses=None,
                    runtime_notice_text="x" * 1_000_000,
                    evidence_hashes=None,
                )
        request_validation.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "license.json"
            path.write_text(
                json.dumps(oversized_license(), ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch(
                "worldforge.creation_contracts._validate_json_structure",
                side_effect=AssertionError(
                    "creation JSON traversal ran before the raw notice limit"
                ),
            ) as json_validation:
                with self.assertRaisesRegex(
                    GenericAssetProductionError,
                    "exceeds 1024 characters",
                ):
                    load_asset_license_record(
                        path,
                        provenance=None,
                        selection=None,
                        receipt=None,
                        request=None,
                        artifact_root=directory,
                    )
            json_validation.assert_not_called()

    def test_failed_receipt_builder_never_discards_candidate_evidence(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        request = values["request"]
        assert isinstance(request, dict)
        with self.assertRaisesRegex(GenericAssetProductionError, "discard"):
            build_asset_production_receipt(
                request,
                gamepack=values["gamepack"],
                subject=values["subject"],
                target=values["target"],
                style=values["style"],
                inventory=values["inventory"],
                specification=values["specification"],
                receipt_id="dishonest_failed_receipt",
                status="failed",
                executed_toolchain=request["toolchain_requirements"],
                candidates=[
                    {
                        "role": "texture",
                        "candidate_artifact_id": "discarded_candidate",
                        "locator": "assets/production/board_ui/candidates/board.png",
                    }
                ],
                artifact_root=values["root"],
                execution_evidence={
                    "started_evidence_hash": "1" * 64,
                    "completed_evidence_hash": "2" * 64,
                    "sanitized_log_hashes": [],
                },
                rights_attestation={
                    "basis": "fixture_public_domain",
                    "evidence_hashes": ["3" * 64],
                },
                failure_reasons=["candidate_generation_failed"],
            )

    @unittest.skipUnless(hasattr(os, "link"), "hard-link test requires os.link")
    def test_candidate_snapshot_rejects_symlinks_and_hardlinks(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        request = values["request"]
        assert isinstance(request, dict)
        source = (
            EXAMPLES
            / "abstract-puzzle"
            / "assets"
            / "production"
            / "board_ui"
            / "candidates"
            / "board.png"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.png"
            target.write_bytes(source.read_bytes())
            for name, make_link in (
                ("hard.png", lambda path: os.link(target, path)),
                ("linked.png", lambda path: path.symlink_to(target)),
            ):
                locator = root / name
                with self.subTest(name=name):
                    try:
                        make_link(locator)
                    except OSError as exc:
                        if name == "linked.png":
                            raise unittest.SkipTest(
                                f"symlink creation is unavailable: {exc}"
                            ) from exc
                        raise
                    with self.assertRaises(GenericAssetProductionError):
                        build_asset_production_receipt(
                            request,
                            gamepack=values["gamepack"],
                            subject=values["subject"],
                            target=values["target"],
                            style=values["style"],
                            inventory=values["inventory"],
                            specification=values["specification"],
                            receipt_id=f"{name.split('.')[0]}_receipt",
                            status="completed",
                            executed_toolchain=request["toolchain_requirements"],
                            candidates=[
                                {
                                    "role": "texture",
                                    "candidate_artifact_id": f"{name.split('.')[0]}_candidate",
                                    "locator": name,
                                }
                            ],
                            artifact_root=root,
                            execution_evidence={
                                "started_evidence_hash": "1" * 64,
                                "completed_evidence_hash": "2" * 64,
                                "sanitized_log_hashes": [],
                            },
                            rights_attestation={
                                "basis": "fixture_public_domain",
                                "evidence_hashes": ["3" * 64],
                            },
                        )

    def test_publishers_are_integral_create_only_and_byte_exact(self) -> None:
        values = _load_lineage("abstract-puzzle", "board_ui")
        chain = {
            key: values[key]
            for key in ("gamepack", "subject", "target", "style", "inventory", "specification")
        }
        cases = (
            (
                "request",
                values["request"],
                publish_asset_production_request,
                chain,
            ),
            (
                "receipt",
                values["receipt"],
                publish_asset_production_receipt,
                {
                    **chain,
                    "request": values["request"],
                    "artifact_root": values["root"],
                },
            ),
            (
                "selection",
                values["selection"],
                publish_asset_selection,
                {
                    **chain,
                    "request": values["request"],
                    "receipt": values["receipt"],
                    "artifact_root": values["root"],
                },
            ),
            (
                "provenance",
                values["provenance"],
                publish_asset_provenance_record,
                {
                    **chain,
                    "request": values["request"],
                    "receipt": values["receipt"],
                    "selection": values["selection"],
                    "artifact_root": values["root"],
                },
            ),
            (
                "license",
                values["license"],
                publish_asset_license_record,
                {
                    **chain,
                    "request": values["request"],
                    "receipt": values["receipt"],
                    "selection": values["selection"],
                    "provenance": values["provenance"],
                    "artifact_root": values["root"],
                },
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name, document, publisher, dependencies in cases:
                with self.subTest(name=name):
                    path = Path(temporary) / f"{name}.json"
                    published = publisher(path, document, **dependencies)
                    self.assertEqual(path.read_bytes(), serialize_production_contract(document))
                    self.assertEqual(published.content_hash, document["content_hash"])
                    with self.assertRaisesRegex(GenericAssetProductionError, "output_exists"):
                        publisher(path, document, **dependencies)

    def test_network_and_process_execution_are_never_used(self) -> None:
        from scripts.generate_generic_asset_fixtures import build_fixture_documents

        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("process")),
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
        ):
            first = build_fixture_documents("abstract-puzzle")
            second = build_fixture_documents("abstract-puzzle")
        self.assertEqual(
            [(path.as_posix(), payload) for path, _, payload in first],
            [(path.as_posix(), payload) for path, _, payload in second],
        )

    def test_fixture_bytes_are_root_independent(self) -> None:
        from scripts.generate_generic_asset_fixtures import build_fixture_documents

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = build_fixture_documents("branching-narrative", artifact_root=Path(first))
            right = build_fixture_documents("branching-narrative", artifact_root=Path(second))
        self.assertEqual(
            [(path.name, payload) for path, _, payload in left],
            [(path.name, payload) for path, _, payload in right],
        )

    @unittest.skipUnless(hasattr(os, "link"), "hard-link test requires os.link")
    def test_fixture_check_rejects_link_substitution(self) -> None:
        from scripts.generate_generic_asset_fixtures import _fixture_bytes_match

        payload = b"canonical-fixture"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standalone = root / "standalone.bin"
            standalone.write_bytes(payload)
            self.assertTrue(_fixture_bytes_match(standalone, payload, root=root))

            hardlink = root / "hardlink.bin"
            os.link(standalone, hardlink)
            self.assertFalse(_fixture_bytes_match(hardlink, payload, root=root))

            symlink = root / "symlink.bin"
            with self.subTest(kind="symlink"):
                try:
                    symlink.symlink_to(standalone)
                except OSError as exc:
                    raise unittest.SkipTest(f"symlink creation is unavailable: {exc}") from exc
                self.assertFalse(_fixture_bytes_match(symlink, payload, root=root))

    @unittest.skipUnless(hasattr(os, "link"), "hard-link test requires os.link")
    def test_fixture_binary_publication_is_create_only_and_link_safe(self) -> None:
        from scripts import generate_generic_asset_fixtures as generator

        payload = b"canonical-fixture"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "fixtures"
            output_root.mkdir()
            with mock.patch.object(generator, "ROOT", root):
                created = output_root / "created.bin"
                generator._write_binary_create_only(created, payload)
                self.assertEqual(created.read_bytes(), payload)
                self.assertEqual(created.stat().st_nlink, 1)
                generator._write_binary_create_only(created, payload)

                differing = output_root / "differing.bin"
                differing.write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                    generator._write_binary_create_only(differing, payload)

                interrupted = output_root / "interrupted.bin"
                with (
                    mock.patch.object(os, "fsync", side_effect=OSError("interrupted")),
                    self.assertRaisesRegex(ValueError, "interrupted"),
                ):
                    generator._write_binary_create_only(interrupted, payload)
                self.assertEqual(interrupted.read_bytes(), payload)

                hardlink = output_root / "hardlink.bin"
                os.link(created, hardlink)
                with self.assertRaises(ValueError):
                    generator._write_binary_create_only(hardlink, payload)

                symlink = output_root / "symlink.bin"
                with self.subTest(kind="symlink"):
                    try:
                        symlink.symlink_to(created)
                    except OSError as exc:
                        raise unittest.SkipTest(f"symlink creation is unavailable: {exc}") from exc
                    with self.assertRaises(ValueError):
                        generator._write_binary_create_only(symlink, payload)

    def test_narrative_fixture_font_is_a_renderable_shared_ui_font(self) -> None:
        from PIL import ImageFont

        path = (
            EXAMPLES
            / "branching-narrative"
            / "assets"
            / "production"
            / "narrative_ui_font"
            / "candidates"
            / "narrative-ui.ttf"
        )
        font = ImageFont.truetype(path, 16)
        bounds = font.getbbox("World Forge 123!?")
        self.assertGreater(bounds[2] - bounds[0], 0)
        self.assertGreater(bounds[3] - bounds[1], 0)


def _reseal(document: dict[str, object]) -> str:
    payload = copy.deepcopy(document)
    payload.pop("content_hash", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
