from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
import wave
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import worldforge.asset_processing as asset_processing_module
from worldforge.__main__ import main
from worldforge.asset_formats.gltf import inspect_glb
from worldforge.asset_io import AssetContractError, artifact_reference
from worldforge.asset_processing import (
    RECEIPT_NAME,
    RECIPE_FORMAT,
    process_asset_recipe,
    validate_processing_recipe,
    verify_processing_receipt,
)
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash


def _pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, PngImagePlugin
    except ImportError as exc:
        raise unittest.SkipTest("Pillow is not installed") from exc
    return Image, PngImagePlugin


def _write_recipe(path: Path, value: dict[str, object]) -> Path:
    payload = {
        "format": RECIPE_FORMAT,
        "format_version": 1,
        **value,
    }
    payload["content_hash"] = canonical_payload_hash(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def _rewrite_receipt(path: Path, mutate: object) -> dict[str, object]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    mutate(receipt)
    receipt["content_hash"] = canonical_payload_hash(receipt)
    path.write_bytes(canonical_json_bytes(receipt))
    return receipt


def _write_color_png(path: Path, color: tuple[int, int, int, int], size: tuple[int, int]) -> None:
    image_module, _ = _pillow()
    path.parent.mkdir(parents=True, exist_ok=True)
    image_module.new("RGBA", size, color).save(path, format="PNG")


def _write_png_header(path: Path, *, width: int, height: int) -> None:
    def chunk(name: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(name + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


def _write_pcm16(
    path: Path,
    frames: list[tuple[int, ...]],
    *,
    channels: int,
    sample_rate: int,
) -> None:
    payload = struct.pack(
        f"<{sum(len(frame) for frame in frames)}h",
        *(sample for frame in frames for sample in frame),
    )
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload)


def _write_declared_pcm16_header(
    path: Path,
    *,
    channels: int,
    sample_rate: int,
    frame_count: int,
) -> None:
    data_size = frame_count * channels * 2
    path.write_bytes(
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            sample_rate,
            sample_rate * channels * 2,
            channels * 2,
            16,
            b"data",
            data_size,
        )
    )


def _write_model_glb(path: Path, *, external_uri: str | None = None) -> None:
    document: dict[str, object] = {
        "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3"}],
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0, "name": "Root"}],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
    }
    if external_uri is not None:
        document["images"] = [{"mimeType": "image/png", "uri": external_uri}]
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    payload = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(encoded))
    payload += struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    path.write_bytes(payload)


def _sfnt_bytes(signature: bytes) -> bytes:
    payload = signature + struct.pack(">HHHH", 1, 16, 0, 0)
    payload += struct.pack(">4sIII", b"head", 0, 28, 4)
    payload += b"\0\0\0\0"
    return payload


def _write_sfnt(path: Path, signature: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_sfnt_bytes(signature))


class DeterministicAssetProcessingTests(unittest.TestCase):
    def test_verify_processing_cli_requires_asset_root_only_for_v2(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.vert"
            source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "file_validate",
                    "input": artifact_reference(root, "source.vert"),
                    "output": {
                        "file": "source.vert",
                        "media_type": "text/x-glsl",
                        "role": "vertex_shader",
                    },
                    "options": {},
                },
            )
            output = root / "output"
            process_asset_recipe(recipe, output, asset_root=root)
            receipt_path = output / RECEIPT_NAME

            stdout = io.StringIO()
            with (
                patch("sys.argv", ["worldforge", "verify-processing", str(receipt_path)]),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(1, main())
            self.assertIn("asset_root is required", stdout.getvalue())

            stdout = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "verify-processing",
                        str(receipt_path),
                        "--asset-root",
                        str(root),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(0, main())
            self.assertIn("OK receipt=", stdout.getvalue())

            _rewrite_receipt(
                receipt_path,
                lambda value: (
                    value.__setitem__("format_version", 1),
                    value.__setitem__(
                        "recipe",
                        {
                            "content_hash": value["recipe_ref"]["content_hash"],
                            "sha256": value["recipe_ref"]["sha256"],
                        },
                    ),
                    value.pop("recipe_ref"),
                ),
            )
            recipe.unlink()
            stdout = io.StringIO()
            with (
                patch("sys.argv", ["worldforge", "verify-processing", str(receipt_path)]),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(0, main())
            self.assertIn("OK receipt=", stdout.getvalue())

    def test_new_receipt_v2_binds_exact_nested_recipe_and_requires_asset_root(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "generated/source.vert"
            source.parent.mkdir()
            source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
            recipe = _write_recipe(
                root / "recipes/nested/source.json",
                {
                    "operation": "file_validate",
                    "input": artifact_reference(root, "generated/source.vert"),
                    "output": {
                        "file": "shaders/source.vert",
                        "media_type": "text/x-glsl",
                        "role": "vertex_shader",
                    },
                    "options": {},
                },
            )
            output = root / "processed/source"

            receipt = process_asset_recipe(recipe, output, asset_root=root)

            expected_recipe_ref = {
                **artifact_reference(root, "recipes/nested/source.json"),
                "content_hash": json.loads(recipe.read_text(encoding="utf-8"))["content_hash"],
            }
            self.assertEqual(2, receipt["format_version"])
            self.assertEqual(expected_recipe_ref, receipt["recipe_ref"])
            self.assertNotIn("recipe", receipt)
            with self.assertRaisesRegex(AssetContractError, "asset_root is required"):
                verify_processing_receipt(output / RECEIPT_NAME)
            self.assertEqual(
                receipt,
                verify_processing_receipt(output / RECEIPT_NAME, asset_root=root),
            )

    def test_legacy_v1_receipt_remains_identity_only(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.vert"
            source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "file_validate",
                    "input": artifact_reference(root, "source.vert"),
                    "output": {
                        "file": "source.vert",
                        "media_type": "text/x-glsl",
                        "role": "vertex_shader",
                    },
                    "options": {},
                },
            )
            output = root / "output"
            process_asset_recipe(recipe, output, asset_root=root)
            receipt_path = output / RECEIPT_NAME

            legacy = _rewrite_receipt(
                receipt_path,
                lambda value: (
                    value.__setitem__("format_version", 1),
                    value.__setitem__(
                        "recipe",
                        {
                            "content_hash": value["recipe_ref"]["content_hash"],
                            "sha256": value["recipe_ref"]["sha256"],
                        },
                    ),
                    value.pop("recipe_ref"),
                ),
            )
            recipe.unlink()

            self.assertEqual(legacy, verify_processing_receipt(receipt_path))
            self.assertEqual(legacy, verify_processing_receipt(receipt_path, asset_root=root))
            _rewrite_receipt(
                receipt_path,
                lambda value: value.__setitem__("operation", []),
            )
            with self.assertRaisesRegex(AssetContractError, "unsupported operation"):
                verify_processing_receipt(receipt_path)

    def test_validate_processing_recipe_is_pure_and_has_no_output_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "candidate.glb"
            source.write_bytes(b"not decoded by pure validation")
            recipe = _write_recipe(
                root / "recipes/candidate.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "candidate.glb"),
                    "output": {"file": "runtime/candidate.glb", "role": "model"},
                    "options": {"budgets": {"max_vertices": 10}, "max_bytes": 1_048_576},
                },
            )
            output = root / "processed/candidate"

            with (
                patch.object(
                    asset_processing_module,
                    "inspect_glb",
                    side_effect=AssertionError("pure validation decoded a GLB"),
                ),
                patch.object(
                    asset_processing_module,
                    "_open_rgba",
                    side_effect=AssertionError("pure validation decoded an image"),
                ),
                patch.object(
                    asset_processing_module,
                    "_read_pcm16",
                    side_effect=AssertionError("pure validation decoded audio"),
                ),
                patch.object(
                    asset_processing_module,
                    "_inspect_validated_file",
                    side_effect=AssertionError("pure validation executed file inspection"),
                ),
                patch.object(
                    asset_processing_module.tempfile,
                    "mkdtemp",
                    side_effect=AssertionError("pure validation created a stage"),
                ),
            ):
                validated = validate_processing_recipe(recipe, asset_root=root)

            self.assertEqual("glb_validate", validated["operation"])
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob("**/.candidate.stage-*")))

    def test_recipe_validation_rejects_bad_root_path_hash_content_and_file_identity(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root = base / "assets"
            root.mkdir()
            source = root / "source.vert"
            source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")

            def recipe_payload(**overrides: object) -> dict[str, object]:
                return {
                    "operation": "file_validate",
                    "input": artifact_reference(root, "source.vert"),
                    "output": {
                        "file": "source.vert",
                        "media_type": "text/x-glsl",
                        "role": "vertex_shader",
                    },
                    "options": {},
                    **overrides,
                }

            with (
                self.subTest("unsafe asset root"),
                self.assertRaisesRegex(AssetContractError, "asset_root"),
            ):
                validate_processing_recipe(
                    _write_recipe(root / "root.json", recipe_payload()),
                    asset_root=root / "missing",
                )

            outside = _write_recipe(base / "outside.json", recipe_payload())
            with (
                self.subTest("recipe outside root"),
                self.assertRaisesRegex(AssetContractError, "must live under asset_root"),
            ):
                validate_processing_recipe(outside, asset_root=root)

            bad_hash = _write_recipe(
                root / "bad-hash.json",
                recipe_payload(
                    input={
                        **artifact_reference(root, "source.vert"),
                        "sha256": "0" * 64,
                    }
                ),
            )
            with self.subTest("input hash"), self.assertRaisesRegex(AssetContractError, "SHA-256"):
                validate_processing_recipe(bad_hash, asset_root=root)

            bad_content = _write_recipe(root / "bad-content.json", recipe_payload())
            value = json.loads(bad_content.read_text(encoding="utf-8"))
            value["content_hash"] = "0" * 64
            bad_content.write_bytes(canonical_json_bytes(value))
            with (
                self.subTest("content hash"),
                self.assertRaisesRegex(AssetContractError, "content hash"),
            ):
                validate_processing_recipe(bad_content, asset_root=root)

            typed_version = _write_recipe(
                root / "typed-version.json",
                recipe_payload(format_version=True),
            )
            with (
                self.subTest("typed version"),
                self.assertRaisesRegex(AssetContractError, "format"),
            ):
                validate_processing_recipe(typed_version, asset_root=root)

            target = _write_recipe(root / "target.json", recipe_payload())
            symlink = root / "linked.json"
            symlink.symlink_to(target.name)
            with (
                self.subTest("symlink"),
                self.assertRaisesRegex(AssetContractError, "standalone regular file"),
            ):
                validate_processing_recipe(symlink, asset_root=root)

            hardlink_target = _write_recipe(root / "hardlink-target.json", recipe_payload())
            hardlink = root / "hardlink.json"
            os.link(hardlink_target, hardlink)
            with (
                self.subTest("hardlink"),
                self.assertRaisesRegex(AssetContractError, "standalone regular file"),
            ):
                validate_processing_recipe(hardlink, asset_root=root)

    def test_v2_receipt_rejects_recipe_reference_and_exact_lineage_tampering(self) -> None:
        mutations = {
            "recipe path": (
                lambda value: value["recipe_ref"].__setitem__("file", "../recipe.json"),
                "recipe_ref/file",
            ),
            "recipe sha": (
                lambda value: value["recipe_ref"].__setitem__("sha256", "0" * 64),
                "SHA-256",
            ),
            "recipe content": (
                lambda value: value["recipe_ref"].__setitem__("content_hash", "0" * 64),
                "content hash",
            ),
            "operation": (
                lambda value: value.__setitem__("operation", "png_canonical"),
                "does not match recipe",
            ),
            "input id": (
                lambda value: value["inputs"][0].__setitem__("id", "other"),
                "inputs do not match recipe",
            ),
            "input file": (
                lambda value: value["inputs"][0]["artifact"].__setitem__(
                    "file", "generated/other.vert"
                ),
                "inputs do not match recipe",
            ),
            "input sha": (
                lambda value: value["inputs"][0]["artifact"].__setitem__("sha256", "0" * 64),
                "inputs do not match recipe",
            ),
            "output file": (
                lambda value: value["outputs"][0]["artifact"].__setitem__("file", "other.vert"),
                "outputs do not match recipe",
            ),
            "output role": (
                lambda value: value["outputs"][0].__setitem__("role", "fragment_shader"),
                "outputs do not match recipe",
            ),
            "output media": (
                lambda value: value["outputs"][0].__setitem__("media_type", "font/ttf"),
                "outputs do not match recipe",
            ),
        }
        for label, (mutate, message) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root / "generated/source.vert"
                source.parent.mkdir()
                source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
                recipe = _write_recipe(
                    root / "recipes/source.json",
                    {
                        "operation": "file_validate",
                        "input": artifact_reference(root, "generated/source.vert"),
                        "output": {
                            "file": "source.vert",
                            "media_type": "text/x-glsl",
                            "role": "vertex_shader",
                        },
                        "options": {},
                    },
                )
                output = root / "processed/source"
                process_asset_recipe(recipe, output, asset_root=root)
                receipt_path = output / RECEIPT_NAME
                _rewrite_receipt(receipt_path, mutate)

                with self.assertRaisesRegex(AssetContractError, message):
                    verify_processing_receipt(receipt_path, asset_root=root)

    def test_v2_receipt_rejects_linked_recipe_file(self) -> None:
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root / "source.vert"
                source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
                recipe = _write_recipe(
                    root / "recipe.json",
                    {
                        "operation": "file_validate",
                        "input": artifact_reference(root, "source.vert"),
                        "output": {
                            "file": "source.vert",
                            "media_type": "text/x-glsl",
                            "role": "vertex_shader",
                        },
                        "options": {},
                    },
                )
                output = root / "output"
                process_asset_recipe(recipe, output, asset_root=root)
                if link_kind == "symlink":
                    moved = root / "moved-recipe.json"
                    recipe.rename(moved)
                    recipe.symlink_to(moved.name)
                else:
                    os.link(recipe, root / "recipe-hardlink.json")

                with self.assertRaisesRegex(AssetContractError, "standalone regular file"):
                    verify_processing_receipt(output / RECEIPT_NAME, asset_root=root)

    def test_recipe_is_rechecked_before_receipt_publication(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            output = root / "output"
            original = asset_processing_module._process_png

            def mutate_recipe_after_processing(
                value: dict[str, object],
                recipe_root: Path,
                stage: Path,
            ) -> object:
                result = original(value, recipe_root, stage)
                changed = json.loads(recipe.read_text(encoding="utf-8"))
                changed["output"] = {"file": "changed.png"}
                changed["content_hash"] = canonical_payload_hash(changed)
                recipe.write_bytes(canonical_json_bytes(changed))
                return result

            with (
                patch.object(
                    asset_processing_module,
                    "_process_png",
                    side_effect=mutate_recipe_after_processing,
                ),
                self.assertRaisesRegex(AssetContractError, "changed during processing"),
            ):
                process_asset_recipe(recipe, output, asset_root=root)

            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_recipe_is_rechecked_after_receipt_publication(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            output = root / "output"
            original_publish = asset_processing_module._publish_directory_noreplace

            def publish_then_mutate_recipe(source: Path, destination: Path) -> None:
                original_publish(source, destination)
                changed = json.loads(recipe.read_text(encoding="utf-8"))
                changed["output"] = {"file": "changed.png"}
                changed["content_hash"] = canonical_payload_hash(changed)
                recipe.write_bytes(canonical_json_bytes(changed))

            with (
                patch.object(
                    asset_processing_module,
                    "_publish_directory_noreplace",
                    side_effect=publish_then_mutate_recipe,
                ),
                self.assertRaisesRegex(
                    AssetContractError,
                    "post-publication validation",
                ),
            ):
                process_asset_recipe(recipe, output, asset_root=root)

            self.assertTrue((output / "result.png").is_file())
            self.assertFalse((output / RECEIPT_NAME).exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_post_publication_cleanup_preserves_an_unowned_receipt_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            output = root / "output"
            foreign = b"foreign recovery evidence\n"

            def replace_receipt_then_fail(
                receipt_path: str | Path,
                *,
                asset_root: str | Path | None = None,
            ) -> dict[str, object]:
                del asset_root
                replacement = root / "foreign-receipt"
                replacement.write_bytes(foreign)
                os.replace(replacement, receipt_path)
                raise AssetContractError("forced post-publication verification failure")

            with (
                patch.object(
                    asset_processing_module,
                    "verify_processing_receipt",
                    side_effect=replace_receipt_then_fail,
                ),
                self.assertRaisesRegex(
                    AssetContractError,
                    "ownership could not be proven",
                ),
            ):
                process_asset_recipe(recipe, output, asset_root=root)

            self.assertEqual(foreign, (output / RECEIPT_NAME).read_bytes())
            self.assertTrue((output / "result.png").is_file())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_publication_does_not_replace_concurrently_created_directory(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            output = root / "output"
            original_rename = asset_processing_module._rename_directory_noreplace

            def create_destination_then_rename(source: Path, destination: Path) -> bool:
                destination.mkdir()
                (destination / "concurrent.txt").write_text(
                    "preserve me\n",
                    encoding="utf-8",
                )
                return original_rename(source, destination)

            with (
                patch.object(
                    asset_processing_module,
                    "_rename_directory_noreplace",
                    side_effect=create_destination_then_rename,
                ),
                self.assertRaisesRegex(
                    AssetContractError,
                    "Refusing to overwrite output directory",
                ),
            ):
                process_asset_recipe(recipe, output, asset_root=root)

            self.assertEqual(
                "preserve me\n",
                (output / "concurrent.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse((output / "result.png").exists())
            self.assertFalse((output / RECEIPT_NAME).exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_compatible_publication_fallback_rolls_back_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            output = root / "output"
            canonical_output = output.resolve()
            original_link = asset_processing_module.os.link
            publication_links = 0

            def fail_second_publication_link(
                source: str | Path,
                destination: str | Path,
                **kwargs: object,
            ) -> None:
                nonlocal publication_links
                if not kwargs and Path(destination).resolve().is_relative_to(canonical_output):
                    publication_links += 1
                    if publication_links == 2:
                        raise OSError("injected publication failure")
                original_link(source, destination, **kwargs)

            with (
                patch.object(
                    asset_processing_module,
                    "_rename_directory_noreplace",
                    return_value=False,
                ),
                patch.object(
                    asset_processing_module.os,
                    "link",
                    side_effect=fail_second_publication_link,
                ),
                self.assertRaisesRegex(OSError, "injected publication failure"),
            ):
                process_asset_recipe(recipe, output, asset_root=root)

            self.assertEqual(2, publication_links)
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_file_validate_copies_fonts_and_glsl_exactly_from_a_shared_asset_root(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "generated").mkdir()
            (root / "recipes").mkdir()
            font_cases = (
                ("modern.ttf", b"\x00\x01\x00\x00", "font/ttf"),
                ("apple.ttf", b"true", "font/ttf"),
                ("type1.ttf", b"typ1", "font/ttf"),
                ("display.otf", b"OTTO", "font/otf"),
            )
            for filename, signature, media_type in font_cases:
                with self.subTest(filename=filename):
                    source = root / "generated" / filename
                    _write_sfnt(source, signature)
                    recipe = _write_recipe(
                        root / "recipes" / f"{filename}.json",
                        {
                            "operation": "file_validate",
                            "input": artifact_reference(root, f"generated/{filename}"),
                            "output": {"file": filename, "media_type": media_type, "role": "font"},
                            "options": {},
                        },
                    )
                    output = root / "processed" / filename
                    receipt = process_asset_recipe(recipe, output, asset_root=root)

                    self.assertEqual(source.read_bytes(), (output / filename).read_bytes())
                    self.assertEqual(
                        f"generated/{filename}", receipt["inputs"][0]["artifact"]["file"]
                    )
                    self.assertEqual(
                        receipt,
                        verify_processing_receipt(output / RECEIPT_NAME, asset_root=root),
                    )

            shaders = (
                ("world.vert", "vertex_shader"),
                ("world.frag", "fragment_shader"),
            )
            shader_payload = (
                b"#version 330 core\n"
                b"layout(location = 0) in vec3 position;\n"
                b"void main() { gl_Position = vec4(position, 1.0); }\n"
            )
            for filename, role in shaders:
                with self.subTest(filename=filename):
                    source = root / "generated" / filename
                    source.write_bytes(shader_payload)
                    recipe = _write_recipe(
                        root / "recipes" / f"{filename}.json",
                        {
                            "operation": "file_validate",
                            "input": artifact_reference(root, f"generated/{filename}"),
                            "output": {
                                "file": f"shaders/{filename}",
                                "media_type": "text/x-glsl",
                                "role": role,
                            },
                            "options": {},
                        },
                    )
                    first = process_asset_recipe(
                        recipe,
                        root / "processed" / f"{filename}-first",
                        asset_root=root,
                    )
                    second = process_asset_recipe(
                        recipe,
                        root / "processed" / f"{filename}-second",
                        asset_root=root,
                    )

                    self.assertEqual(first, second)
                    self.assertEqual(
                        shader_payload,
                        (root / f"processed/{filename}-first/shaders/{filename}").read_bytes(),
                    )
                    verify_processing_receipt(
                        root / f"processed/{filename}-first/{RECEIPT_NAME}",
                        asset_root=root,
                    )

    def test_file_validate_rejects_bad_headers_unsafe_glsl_pairs_and_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "generated").mkdir()
            (root / "recipes").mkdir()
            invalid_payloads = (
                ("bad.ttf", b"OTTO" + b"\0" * 24, "font/ttf", "font", "sfnt header"),
                (
                    "overlap.ttf",
                    b"\x00\x01\x00\x00"
                    + struct.pack(">HHHH", 2, 32, 1, 0)
                    + struct.pack(">4sIII", b"head", 0, 44, 4)
                    + struct.pack(">4sIII", b"cmap", 0, 44, 4)
                    + b"\0" * 4,
                    "font/ttf",
                    "font",
                    "overlaps",
                ),
                (
                    "trailing.ttf",
                    _sfnt_bytes(b"\x00\x01\x00\x00") + b"opaque",
                    "font/ttf",
                    "font",
                    "trailing",
                ),
                ("utf8.vert", b"\xff", "text/x-glsl", "vertex_shader", "UTF-8"),
                (
                    "control.vert",
                    b"#version 330 core\n\0void main() {}\n",
                    "text/x-glsl",
                    "vertex_shader",
                    "control",
                ),
                (
                    "url.vert",
                    b"// https://example.invalid/shader\nvoid main() {}\n",
                    "text/x-glsl",
                    "vertex_shader",
                    "URL",
                ),
                (
                    "secret.vert",
                    b"// api_key=fixture_value\nvoid main() {}\n",
                    "text/x-glsl",
                    "vertex_shader",
                    "secret",
                ),
                (
                    "provider.vert",
                    b"// generated by OpenAI\nvoid main() {}\n",
                    "text/x-glsl",
                    "vertex_shader",
                    "provider",
                ),
                (
                    "include.vert",
                    b'#include "shared.glsl"\nvoid main() {}\n',
                    "text/x-glsl",
                    "vertex_shader",
                    "include",
                ),
                (
                    "large.vert",
                    b" " * (1024 * 1024 + 1),
                    "text/x-glsl",
                    "vertex_shader",
                    "exceeds",
                ),
            )
            for filename, payload, media_type, role, message in invalid_payloads:
                with self.subTest(filename=filename):
                    source = root / "generated" / filename
                    source.write_bytes(payload)
                    recipe = _write_recipe(
                        root / "recipes" / f"{filename}.json",
                        {
                            "operation": "file_validate",
                            "input": artifact_reference(root, f"generated/{filename}"),
                            "output": {
                                "file": filename,
                                "media_type": media_type,
                                "role": role,
                            },
                            "options": {},
                        },
                    )
                    output = root / "rejected" / filename
                    with self.assertRaisesRegex(AssetContractError, message):
                        process_asset_recipe(recipe, output, asset_root=root)
                    self.assertFalse(output.exists())

            valid_shader = root / "generated/valid.vert"
            valid_shader.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
            invalid_contracts = (
                ("wrong-pair.glsl", "font", "text/x-glsl", "role/media_type"),
                ("wrong-extension.txt", "vertex_shader", "text/x-glsl", "extensions"),
            )
            for filename, role, media_type, message in invalid_contracts:
                with self.subTest(filename=filename):
                    recipe = _write_recipe(
                        root / "recipes" / f"{filename}.json",
                        {
                            "operation": "file_validate",
                            "input": artifact_reference(root, "generated/valid.vert"),
                            "output": {
                                "file": filename,
                                "media_type": media_type,
                                "role": role,
                            },
                            "options": {},
                        },
                    )
                    with self.assertRaisesRegex(AssetContractError, message):
                        process_asset_recipe(
                            recipe,
                            root / "rejected" / filename,
                            asset_root=root,
                        )

    def test_file_validate_receipt_rechecks_sanitized_glsl(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.vert"
            source.write_text("#version 330 core\nvoid main() {}\n", encoding="utf-8")
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "file_validate",
                    "input": artifact_reference(root, "source.vert"),
                    "output": {
                        "file": "shader.vert",
                        "media_type": "text/x-glsl",
                        "role": "vertex_shader",
                    },
                    "options": {},
                },
            )
            output = root / "output"
            process_asset_recipe(recipe, output, asset_root=root)
            shader = output / "shader.vert"
            shader.write_text("// provider: modly\nvoid main() {}\n", encoding="utf-8")
            receipt_path = output / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["outputs"][0]["artifact"] = artifact_reference(output, "shader.vert")
            receipt["outputs"][0]["details"] = {"byte_length": shader.stat().st_size}
            receipt["content_hash"] = canonical_payload_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            with self.assertRaisesRegex(AssetContractError, "provider"):
                verify_processing_receipt(receipt_path, asset_root=root)

    def test_existing_processing_outputs_require_format_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "frame.png", (20, 40, 60, 255), (2, 2))
            _write_pcm16(root / "source.wav", [(0,), (100,)], channels=1, sample_rate=8000)
            frame = {
                "id": "frame_one",
                "clip_id": "idle",
                "artifact": artifact_reference(root, "frame.png"),
                "duration_ticks": 1,
                "pivot": [0, 0],
                "loop": True,
            }
            invalid_recipes = (
                (
                    "png",
                    {
                        "operation": "png_canonical",
                        "input": artifact_reference(root, "frame.png"),
                        "output": {"file": "texture.bin"},
                        "options": {},
                    },
                ),
                (
                    "atlas-texture",
                    {
                        "operation": "atlas",
                        "inputs": [frame],
                        "output": {"texture_file": "atlas.bin", "clipset_file": "atlas.json"},
                        "options": {"cell_width": 2, "cell_height": 2, "columns": 1},
                    },
                ),
                (
                    "atlas-clipset",
                    {
                        "operation": "atlas",
                        "inputs": [frame],
                        "output": {"texture_file": "atlas.png", "clipset_file": "atlas.bin"},
                        "options": {"cell_width": 2, "cell_height": 2, "columns": 1},
                    },
                ),
                (
                    "wav",
                    {
                        "operation": "wav_pcm",
                        "input": artifact_reference(root, "source.wav"),
                        "output": {"file": "sound.bin"},
                        "options": {
                            "channel_mode": "mono",
                            "sample_rate": 8000,
                            "trim_threshold": 0,
                            "peak": 30000,
                        },
                    },
                ),
            )
            for case, payload in invalid_recipes:
                with self.subTest(case=case):
                    recipe = _write_recipe(root / f"{case}.json", payload)
                    output = root / f"out-{case}"
                    with self.assertRaisesRegex(AssetContractError, "extensions"):
                        process_asset_recipe(recipe, output, asset_root=root)
                    self.assertFalse(output.exists())

    def test_glb_validation_is_deterministic_and_reinspectable(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_model_glb(root / "candidate.glb")
            recipe = _write_recipe(
                root / "glb.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "candidate.glb"),
                    "output": {"file": "runtime/model.glb", "role": "model"},
                    "options": {
                        "budgets": {
                            "max_materials": 1,
                            "max_triangles": 1,
                            "max_vertices": 3,
                        },
                        "max_bytes": 1_048_576,
                    },
                },
            )

            first = process_asset_recipe(recipe, root / "first", asset_root=root)
            second = process_asset_recipe(recipe, root / "second", asset_root=root)

            self.assertEqual(first, second)
            self.assertEqual(
                (root / "candidate.glb").read_bytes(),
                (root / "first/runtime/model.glb").read_bytes(),
            )
            self.assertEqual(1, first["outputs"][0]["details"]["metrics"]["meshes"])
            self.assertEqual(
                first,
                verify_processing_receipt(
                    root / f"first/{RECEIPT_NAME}",
                    asset_root=root,
                ),
            )

    def test_glb_validation_rejects_external_resources_and_tampered_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_model_glb(root / "external.glb", external_uri="texture.png")
            invalid = _write_recipe(
                root / "external.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "external.glb"),
                    "output": {"file": "model.glb", "role": "model"},
                    "options": {"budgets": {}, "max_bytes": 1_048_576},
                },
            )
            with self.assertRaisesRegex(AssetContractError, "external URI"):
                process_asset_recipe(invalid, root / "invalid-output", asset_root=root)

            _write_model_glb(root / "candidate.glb")
            valid = _write_recipe(
                root / "valid.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "candidate.glb"),
                    "output": {"file": "model.glb", "role": "model"},
                    "options": {"budgets": {}, "max_bytes": 1_048_576},
                },
            )
            process_asset_recipe(valid, root / "valid-output", asset_root=root)
            receipt_path = root / f"valid-output/{RECEIPT_NAME}"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["outputs"][0]["details"]["metrics"]["meshes"] = 2
            receipt["content_hash"] = canonical_payload_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "GLB inspection"):
                verify_processing_receipt(receipt_path, asset_root=root)

    def test_glb_receipt_reverification_enforces_role_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_model_glb(root / "candidate.glb")
            recipe = _write_recipe(
                root / "valid.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "candidate.glb"),
                    "output": {"file": "model.glb", "role": "model"},
                    "options": {"budgets": {}, "max_bytes": 1_048_576},
                },
            )
            process_asset_recipe(recipe, root / "output", asset_root=root)

            document = json.dumps(
                {"asset": {"version": "2.0"}},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            document += b" " * (-len(document) % 4)
            output_glb = root / "output/model.glb"
            output_glb.write_bytes(
                struct.pack("<4sII", b"glTF", 2, 20 + len(document))
                + struct.pack("<II", len(document), 0x4E4F534A)
                + document
            )
            receipt_path = root / f"output/{RECEIPT_NAME}"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["outputs"][0]["artifact"] = artifact_reference(
                root / "output",
                "model.glb",
            )
            receipt["outputs"][0]["details"] = inspect_glb(output_glb)
            receipt["content_hash"] = canonical_payload_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            with self.assertRaisesRegex(AssetContractError, "requires at least one meshes"):
                verify_processing_receipt(receipt_path, asset_root=root)

    def test_png_canonical_is_byte_identical_and_strips_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            image_module, png_module = _pillow()
            root = Path(name)
            source = root / "source.png"
            image = image_module.new("RGB", (3, 2), (255, 0, 255))
            image.putpixel((1, 0), (200, 10, 20))
            metadata = png_module.PngInfo()
            metadata.add_text("comment", "must not survive")
            image.save(source, format="PNG", pnginfo=metadata)
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "canonical.png"},
                    "options": {
                        "matte_alpha_key": {"rgb": [255, 0, 255], "tolerance": 0},
                        "crop": {"left": 0, "top": 0, "right": 3, "bottom": 2},
                        "resize": {"width": 6, "height": 4},
                        "pad": {
                            "left": 1,
                            "top": 1,
                            "right": 1,
                            "bottom": 1,
                            "color": [0, 0, 0, 0],
                        },
                    },
                },
            )

            first = process_asset_recipe(recipe, root / "first", asset_root=root)
            second = process_asset_recipe(recipe, root / "second", asset_root=root)

            self.assertEqual(first, second)
            self.assertEqual(
                (root / "first/canonical.png").read_bytes(),
                (root / "second/canonical.png").read_bytes(),
            )
            self.assertEqual(
                (root / f"first/{RECEIPT_NAME}").read_bytes(),
                (root / f"second/{RECEIPT_NAME}").read_bytes(),
            )
            self.assertEqual(
                first,
                verify_processing_receipt(
                    root / f"first/{RECEIPT_NAME}",
                    asset_root=root,
                ),
            )
            with image_module.open(root / "first/canonical.png") as output:
                self.assertEqual("RGBA", output.mode)
                self.assertEqual((8, 6), output.size)
                self.assertNotIn("comment", output.info)
                self.assertEqual((0, 0, 0, 0), output.getpixel((1, 1)))
                self.assertEqual((200, 10, 20, 255), output.getpixel((3, 1)))

    def test_image_processing_binds_supported_decoded_formats_and_png_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            image_module, _ = _pillow()
            root = Path(name)
            for image_format, suffix in (("JPEG", "jpg"), ("WEBP", "webp")):
                with self.subTest(image_format=image_format):
                    source = root / f"source.{suffix}"
                    mode = "RGB" if image_format == "JPEG" else "RGBA"
                    color = (20, 40, 60) if mode == "RGB" else (20, 40, 60, 255)
                    image_module.new(mode, (2, 2), color).save(source, format=image_format)
                    recipe = _write_recipe(
                        root / f"{suffix}.json",
                        {
                            "operation": "png_canonical",
                            "input": artifact_reference(root, source.name),
                            "output": {"file": "result.png"},
                            "options": {},
                        },
                    )
                    destination = root / f"output-{suffix}"
                    process_asset_recipe(recipe, destination, asset_root=root)
                    verify_processing_receipt(destination / RECEIPT_NAME, asset_root=root)

            first_frame = image_module.new("RGBA", (2, 2), (20, 40, 60, 255))
            second_frame = image_module.new("RGBA", (2, 2), (60, 40, 20, 255))
            first_frame.save(
                root / "animated.webp",
                format="WEBP",
                save_all=True,
                append_images=[second_frame],
                duration=10,
                loop=0,
                lossless=True,
            )
            animated_recipe = _write_recipe(
                root / "animated.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "animated.webp"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            with self.assertRaisesRegex(AssetContractError, "exactly one image frame"):
                process_asset_recipe(
                    animated_recipe,
                    root / "animated-output",
                    asset_root=root,
                )

            image_module.new("RGB", (2, 2), (20, 40, 60)).save(
                root / "unsupported.png",
                format="BMP",
            )
            recipe = _write_recipe(
                root / "unsupported.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "unsupported.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            with self.assertRaisesRegex(AssetContractError, "unsupported decoded image format"):
                process_asset_recipe(recipe, root / "unsupported-output", asset_root=root)

            source = root / "canonical-source.png"
            _write_color_png(source, (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "canonical.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, source.name),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            destination = root / "canonical-output"
            process_asset_recipe(recipe, destination, asset_root=root)
            output = destination / "result.png"
            image_module.new("RGB", (2, 2), (20, 40, 60)).save(output, format="JPEG")
            receipt_path = destination / RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["outputs"][0]["artifact"] = artifact_reference(destination, "result.png")
            receipt["content_hash"] = canonical_payload_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "not a decoded PNG"):
                verify_processing_receipt(receipt_path, asset_root=root)

    def test_png_dimensions_are_rejected_before_full_decode(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            _pillow()
            root = Path(name)
            _write_png_header(root / "oversized.png", width=16385, height=1)
            recipe = _write_recipe(
                root / "oversized.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "oversized.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            with self.assertRaisesRegex(AssetContractError, "edge limit"):
                process_asset_recipe(recipe, root / "output", asset_root=root)
            self.assertFalse((root / "output").exists())

    def test_receipt_rejects_output_and_document_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            recipe = _write_recipe(
                root / "recipe.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            process_asset_recipe(recipe, root / "output", asset_root=root)
            receipt = root / f"output/{RECEIPT_NAME}"
            verify_processing_receipt(receipt, asset_root=root)

            output = root / "output/result.png"
            output.write_bytes(output.read_bytes() + b"tamper")
            with self.assertRaisesRegex(AssetContractError, "SHA-256"):
                verify_processing_receipt(receipt, asset_root=root)

            process_asset_recipe(recipe, root / "document-tamper", asset_root=root)
            changed_receipt = root / f"document-tamper/{RECEIPT_NAME}"
            changed = json.loads(changed_receipt.read_text(encoding="utf-8"))
            changed["operation"] = "wav_pcm"
            changed_receipt.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "content hash"):
                verify_processing_receipt(changed_receipt, asset_root=root)

    def test_invalid_operation_paths_and_hashes_fail_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "source.png").write_bytes(b"not decoded by these invalid recipes")
            valid_reference = artifact_reference(root, "source.png")

            invalid_cases: list[tuple[str, dict[str, object], str]] = [
                (
                    "operation",
                    {
                        "operation": "run_shell",
                        "input": valid_reference,
                        "output": {"file": "result.png"},
                        "options": {},
                    },
                    "Unsupported asset-processing operation",
                ),
                (
                    "input-path",
                    {
                        "operation": "png_canonical",
                        "input": {
                            **valid_reference,
                            "file": "../source.png",
                        },
                        "output": {"file": "result.png"},
                        "options": {},
                    },
                    "Unsafe artifact path",
                ),
                (
                    "output-path",
                    {
                        "operation": "png_canonical",
                        "input": valid_reference,
                        "output": {"file": "../escape.png"},
                        "options": {},
                    },
                    "unsafe output path",
                ),
                (
                    "input-hash",
                    {
                        "operation": "png_canonical",
                        "input": {**valid_reference, "sha256": "0" * 64},
                        "output": {"file": "result.png"},
                        "options": {},
                    },
                    "SHA-256",
                ),
            ]
            for case, payload, message in invalid_cases:
                with self.subTest(case=case):
                    recipe = _write_recipe(root / f"{case}.json", payload)
                    output = root / f"out-{case}"
                    with self.assertRaisesRegex(AssetContractError, message):
                        process_asset_recipe(recipe, output, asset_root=root)
                    self.assertFalse(output.exists())

            bad_content = json.loads((root / "input-hash.json").read_text(encoding="utf-8"))
            bad_content["content_hash"] = "f" * 64
            bad_recipe = root / "bad-content.json"
            bad_recipe.write_text(json.dumps(bad_content), encoding="utf-8")
            with self.assertRaisesRegex(AssetContractError, "content hash"):
                process_asset_recipe(bad_recipe, root / "out-bad-content", asset_root=root)

    def test_atlas_uses_canonical_frame_and_clip_order(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            image_module, _ = _pillow()
            root = Path(name)
            _write_color_png(root / "frames/red.png", (255, 0, 0, 255), (2, 2))
            _write_color_png(root / "frames/green.png", (0, 255, 0, 255), (2, 2))
            _write_color_png(root / "frames/blue.png", (0, 0, 255, 255), (2, 2))

            def frame(
                frame_id: str,
                clip_id: str,
                file: str,
                pivot: list[int],
            ) -> dict[str, object]:
                return {
                    "id": frame_id,
                    "clip_id": clip_id,
                    "artifact": artifact_reference(root, file),
                    "duration_ticks": 2,
                    "pivot": pivot,
                    "loop": True,
                }

            recipe = _write_recipe(
                root / "atlas.json",
                {
                    "operation": "atlas",
                    "inputs": [
                        frame("z_frame", "walk", "frames/red.png", [1, 1]),
                        frame("a_frame", "idle", "frames/green.png", [0, 1]),
                        frame("m_frame", "walk", "frames/blue.png", [1, 1]),
                    ],
                    "output": {
                        "texture_file": "nested/atlas.png",
                        "clipset_file": "nested/atlas.clips.json",
                    },
                    "options": {"cell_width": 2, "cell_height": 2, "columns": 2},
                },
            )
            receipt = process_asset_recipe(recipe, root / "output", asset_root=root)
            verify_processing_receipt(root / f"output/{RECEIPT_NAME}", asset_root=root)

            self.assertEqual(
                ["a_frame", "m_frame", "z_frame"],
                [item["id"] for item in receipt["inputs"]],
            )
            with image_module.open(root / "output/nested/atlas.png") as atlas:
                self.assertEqual((4, 4), atlas.size)
                self.assertEqual((0, 255, 0, 255), atlas.getpixel((0, 0)))
                self.assertEqual((0, 0, 255, 255), atlas.getpixel((2, 0)))
                self.assertEqual((255, 0, 0, 255), atlas.getpixel((0, 2)))
                self.assertEqual((0, 0, 0, 0), atlas.getpixel((2, 2)))
            clipset = json.loads(
                (root / "output/nested/atlas.clips.json").read_text(encoding="utf-8")
            )
            self.assertEqual(["idle", "walk"], [clip["id"] for clip in clipset["clips"]])
            self.assertEqual(
                [{"x": 2, "y": 0}, {"x": 0, "y": 2}],
                [{"x": frame["x"], "y": frame["y"]} for frame in clipset["clips"][1]["frames"]],
            )

    def test_wav_processing_is_deterministic_and_has_requested_properties(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _write_pcm16(
                root / "source.wav",
                [(0, 0), (0, 0), (1000, -500), (2000, -1000), (0, 0)],
                channels=2,
                sample_rate=8000,
            )
            recipe = _write_recipe(
                root / "wav.json",
                {
                    "operation": "wav_pcm",
                    "input": artifact_reference(root, "source.wav"),
                    "output": {"file": "normalized.wav"},
                    "options": {
                        "channel_mode": "mono",
                        "sample_rate": 16000,
                        "trim_threshold": 10,
                        "peak": 12000,
                    },
                },
            )
            first = process_asset_recipe(recipe, root / "first", asset_root=root)
            second = process_asset_recipe(recipe, root / "second", asset_root=root)
            self.assertEqual(first, second)
            self.assertEqual(
                (root / "first/normalized.wav").read_bytes(),
                (root / "second/normalized.wav").read_bytes(),
            )
            self.assertEqual(
                (root / f"first/{RECEIPT_NAME}").read_bytes(),
                (root / f"second/{RECEIPT_NAME}").read_bytes(),
            )
            verify_processing_receipt(root / f"first/{RECEIPT_NAME}", asset_root=root)

            with wave.open(str(root / "first/normalized.wav"), "rb") as result:
                self.assertEqual(1, result.getnchannels())
                self.assertEqual(2, result.getsampwidth())
                self.assertEqual(16000, result.getframerate())
                self.assertEqual(4, result.getnframes())
                samples = struct.unpack("<4h", result.readframes(4))
            self.assertEqual((6000, 9000, 12000, 12000), samples)
            self.assertEqual(12000, first["outputs"][0]["details"]["peak"])

    def test_receipt_toolchains_are_closed_per_operation(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)

            def reject_tamper(
                receipt_path: Path,
                mutate: object,
                message: str,
            ) -> None:
                original = json.loads(receipt_path.read_text(encoding="utf-8"))
                changed = json.loads(json.dumps(original))
                mutate(changed)
                changed["content_hash"] = canonical_payload_hash(changed)
                receipt_path.write_text(json.dumps(changed), encoding="utf-8")
                try:
                    with self.assertRaisesRegex(AssetContractError, message):
                        verify_processing_receipt(receipt_path, asset_root=root)
                finally:
                    receipt_path.write_text(json.dumps(original), encoding="utf-8")

            _write_color_png(root / "source.png", (20, 40, 60, 255), (2, 2))
            png_recipe = _write_recipe(
                root / "png.json",
                {
                    "operation": "png_canonical",
                    "input": artifact_reference(root, "source.png"),
                    "output": {"file": "result.png"},
                    "options": {},
                },
            )
            process_asset_recipe(png_recipe, root / "png-output", asset_root=root)
            png_receipt = root / f"png-output/{RECEIPT_NAME}"
            reject_tamper(
                png_receipt,
                lambda value: value["toolchain"].__setitem__("extra", "forbidden"),
                "unknown extra",
            )
            reject_tamper(
                png_receipt,
                lambda value: value["toolchain"].pop("pillow_version"),
                "missing pillow_version",
            )
            reject_tamper(
                png_receipt,
                lambda value: value["toolchain"].__setitem__("processor", "other"),
                "image processing toolchain is invalid",
            )

            _write_pcm16(root / "source.wav", [(0,), (1000,)], channels=1, sample_rate=8000)
            wav_recipe = _write_recipe(
                root / "wav-toolchain.json",
                {
                    "operation": "wav_pcm",
                    "input": artifact_reference(root, "source.wav"),
                    "output": {"file": "result.wav"},
                    "options": {
                        "channel_mode": "mono",
                        "sample_rate": 8000,
                        "trim_threshold": 0,
                        "peak": 30000,
                    },
                },
            )
            process_asset_recipe(wav_recipe, root / "wav-output", asset_root=root)
            reject_tamper(
                root / f"wav-output/{RECEIPT_NAME}",
                lambda value: value["toolchain"].__setitem__("wave_module", "third_party"),
                "wav_pcm toolchain is invalid",
            )

            _write_model_glb(root / "source.glb")
            glb_recipe = _write_recipe(
                root / "glb-toolchain.json",
                {
                    "operation": "glb_validate",
                    "input": artifact_reference(root, "source.glb"),
                    "output": {"file": "result.glb", "role": "model"},
                    "options": {"budgets": {}, "max_bytes": 1_048_576},
                },
            )
            process_asset_recipe(glb_recipe, root / "glb-output", asset_root=root)
            reject_tamper(
                root / f"glb-output/{RECEIPT_NAME}",
                lambda value: value["toolchain"].__setitem__(
                    "allowed_extensions",
                    ["KHR_materials_unlit"],
                ),
                "glb_validate toolchain is invalid",
            )
            reject_tamper(
                root / f"glb-output/{RECEIPT_NAME}",
                lambda value: value["toolchain"].__setitem__(
                    "external_uris_allowed",
                    True,
                ),
                "glb_validate toolchain is invalid",
            )

    def test_wav_rejects_non_pcm16_input(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with wave.open(str(root / "eight-bit.wav"), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(1)
                target.setframerate(8000)
                target.writeframes(bytes([128, 129, 127]))
            recipe = _write_recipe(
                root / "wav.json",
                {
                    "operation": "wav_pcm",
                    "input": artifact_reference(root, "eight-bit.wav"),
                    "output": {"file": "result.wav"},
                    "options": {
                        "channel_mode": "mono",
                        "sample_rate": 8000,
                        "trim_threshold": 0,
                        "peak": 30000,
                    },
                },
            )
            with self.assertRaisesRegex(AssetContractError, "16-bit PCM"):
                process_asset_recipe(recipe, root / "output", asset_root=root)
            self.assertFalse((root / "output").exists())

    def test_wav_preflight_bounds_duration_and_resampling_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            cases = (
                ("duration", 8000, 8000 * 601, 8000, "mono", "duration limit"),
                ("frames", 192000, 33_554_433, 192000, "mono", "PCM frame limit"),
                (
                    "amplification",
                    8000,
                    8000 * 600,
                    192000,
                    "mono",
                    "resampled output WAV exceeds",
                ),
                (
                    "output-bytes",
                    8000,
                    700_000,
                    192000,
                    "stereo",
                    "PCM byte limit",
                ),
            )
            for case, sample_rate, frame_count, target_rate, mode, expected in cases:
                with self.subTest(case=case):
                    source = root / f"{case}.wav"
                    _write_declared_pcm16_header(
                        source,
                        channels=1,
                        sample_rate=sample_rate,
                        frame_count=frame_count,
                    )
                    recipe = _write_recipe(
                        root / f"{case}.json",
                        {
                            "operation": "wav_pcm",
                            "input": artifact_reference(root, source.name),
                            "output": {"file": "result.wav"},
                            "options": {
                                "channel_mode": mode,
                                "sample_rate": target_rate,
                                "trim_threshold": 0,
                                "peak": 30000,
                            },
                        },
                    )
                    output = root / f"output-{case}"
                    with self.assertRaisesRegex(AssetContractError, expected):
                        process_asset_recipe(recipe, output, asset_root=root)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
