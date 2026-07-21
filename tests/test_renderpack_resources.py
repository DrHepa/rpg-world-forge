from __future__ import annotations

import binascii
import hashlib
import json
import mmap
import os
import struct
import tempfile
import unittest
import wave
import zlib
from pathlib import Path
from unittest.mock import patch

import isoworld.content.media as media_module
from isoworld.content.loader import load_worldpack
from isoworld.content.media import MediaValidationError, read_validated_media
from isoworld.content.renderpack import RenderPackError, load_renderpack

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png() -> bytes:
    row = b"\0" + bytes((20, 40, 60, 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(row))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg() -> bytes:
    frame = bytes((8,)) + struct.pack(">HHB", 1, 1, 1) + b"\x01\x11\0"
    scan = b"\x01\x01\0\0\x3f\0"
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">H", len(frame) + 2)
        + frame
        + b"\xff\xda"
        + struct.pack(">H", len(scan) + 2)
        + scan
        + b"\x01"
        + b"\xff\xd9"
    )


def _webp() -> bytes:
    frame = b"\0\0\0\x9d\x01\x2a" + struct.pack("<HH", 1, 1)
    body = b"WEBP" + b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _webp_chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def _animated_webp() -> bytes:
    frame = b"\0\0\0\x9d\x01\x2a" + struct.pack("<HH", 1, 1)
    vp8x = bytes((0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    frame_header = b"\0" * 15 + b"\0"
    body = (
        b"WEBP"
        + _webp_chunk(b"VP8X", vp8x)
        + _webp_chunk(b"ANIM", b"\0" * 6)
        + _webp_chunk(b"ANMF", frame_header + _webp_chunk(b"VP8 ", frame))
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _wav() -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tone.wav"
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(22050)
            target.writeframes(b"\0\0" * 8)
        return path.read_bytes()


def _ogg() -> bytes:
    packet = struct.pack("<8sBBHIhB", b"OpusHead", 1, 1, 0, 48000, 0, 0)
    audio_packet = b"\0"
    header = bytearray(29)
    header[:4] = b"OggS"
    header[4] = 0
    header[5] = 0x06
    struct.pack_into("<Q", header, 6, 0)
    struct.pack_into("<I", header, 14, 7)
    struct.pack_into("<I", header, 18, 0)
    header[26] = 2
    header[27] = len(packet)
    header[28] = len(audio_packet)
    page = header + packet + audio_packet
    return bytes(page)


def _mp3() -> bytes:
    header = 0xFFFB9064
    length = media_module._mp3_frame_length(header)
    return header.to_bytes(4, "big") + b"\0" * (length - 4)


def _sfnt(signature: bytes) -> bytes:
    return (
        signature
        + struct.pack(">HHHH", 1, 16, 0, 0)
        + struct.pack(">4sIII", b"head", 0, 28, 4)
        + b"\0" * 4
    )


def _clipset() -> bytes:
    return json.dumps(
        {
            "format": "isoworld.clipset",
            "format_version": 1,
            "clips": [
                {
                    "id": "idle",
                    "pivot": [0, 0],
                    "loop": True,
                    "frames": [{"x": 0, "y": 0, "width": 1, "height": 1, "duration_ticks": 1}],
                }
            ],
        }
    ).encode()


def _canonical_hash(raw: dict[str, object]) -> str:
    payload = dict(raw)
    payload.pop("content_hash", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _renderpack(
    root: Path,
    pack: object,
    assets: list[dict[str, object]],
) -> Path:
    raw: dict[str, object] = {
        "format": "isoworld.renderpack",
        "format_version": 1,
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "assets": assets,
        "bindings": [],
    }
    raw["content_hash"] = _canonical_hash(raw)
    path = root / "renderpack.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def _asset(
    asset_id: str,
    kind: str,
    role: str,
    relative: str,
    media_type: str,
    sha256: str,
) -> dict[str, object]:
    return {
        "id": asset_id,
        "kind": kind,
        "files": [
            {
                "role": role,
                "path": relative,
                "sha256": sha256,
                "media_type": media_type,
            }
        ],
    }


class DirectMediaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_accepts_structurally_valid_supported_media(self) -> None:
        cases = {
            "image/png": ("image.png", _png()),
            "image/jpeg": ("image.jpg", _jpeg()),
            "image/webp": ("image.webp", _webp()),
            "audio/wav": ("audio.wav", _wav()),
            "audio/ogg": ("audio.ogg", _ogg()),
            "audio/mpeg": ("audio.mp3", _mp3()),
            "font/ttf": ("font.ttf", _sfnt(b"\x00\x01\x00\x00")),
            "font/otf": ("font.otf", _sfnt(b"OTTO")),
            "text/x-glsl": ("shader.glsl", b"void main() { gl_Position = vec4(0.0); }\n"),
            "application/json": ("clips.json", _clipset()),
        }
        for media_type, (name, payload) in cases.items():
            with self.subTest(media_type=media_type):
                path = self.root / name
                path.write_bytes(payload)

                validated = read_validated_media(path, media_type)

                self.assertEqual(payload, validated.payload)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), validated.sha256)

    def test_accepts_standard_animated_webp_with_anmf_frame(self) -> None:
        path = self.root / "animated.webp"
        path.write_bytes(_animated_webp())

        validated = read_validated_media(path, "image/webp")

        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), validated.sha256)

    def test_large_media_uses_spooled_mmap_without_retaining_a_second_payload(self) -> None:
        path = self.root / "tone.wav"
        path.write_bytes(_wav())
        original_validate = media_module._validate_media_payload

        with (
            patch.object(media_module, "MAX_RETAINED_MEDIA_BYTES", 8),
            patch(
                "isoworld.content.media._validate_media_payload",
                wraps=original_validate,
            ) as validate,
        ):
            validated = read_validated_media(path, "audio/wav")

        self.assertIsNone(validated.payload)
        self.assertIsInstance(validate.call_args.args[0], mmap.mmap)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), validated.sha256)

    def test_rejects_png_invalid_filter_and_missing_indexed_palette(self) -> None:
        invalid_filter = (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(b"\x05" + bytes((20, 40, 60, 255))))
            + _png_chunk(b"IEND", b"")
        )
        indexed_without_palette = (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(b"\0\0"))
            + _png_chunk(b"IEND", b"")
        )
        for name, payload, message in (
            ("filter.png", invalid_filter, "filter"),
            ("palette.png", indexed_without_palette, "PLTE"),
        ):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(payload)
                with self.assertRaisesRegex(MediaValidationError, message):
                    read_validated_media(path, "image/png")

    def test_rejects_glsl_main_declared_only_inside_comments(self) -> None:
        path = self.root / "commented.glsl"
        path.write_bytes(b"// void main() {}\n/* void main() {} */\n")

        with self.assertRaisesRegex(MediaValidationError, "main function"):
            read_validated_media(path, "text/x-glsl")

    def test_rejects_truncated_or_malformed_payload_for_every_family(self) -> None:
        cases = {
            "image/png": _png()[:-12],
            "image/jpeg": _jpeg()[:-2],
            "image/webp": _webp()[:-1],
            "audio/wav": _wav()[:-1],
            "audio/ogg": _ogg()[:-1],
            "audio/mpeg": _mp3()[:-1],
            "font/ttf": _sfnt(b"\x00\x01\x00\x00")[:10],
            "font/otf": _sfnt(b"OTTO")[:10],
            "text/x-glsl": b"void main() {",
            "application/json": b'{"format":',
        }
        for index, (media_type, payload) in enumerate(cases.items()):
            with self.subTest(media_type=media_type):
                path = self.root / f"bad-{index}.bin"
                path.write_bytes(payload)
                with self.assertRaises(MediaValidationError):
                    read_validated_media(path, media_type)

    def test_rejects_resource_over_its_effective_limit(self) -> None:
        path = self.root / "shader.glsl"
        path.write_bytes(b"void main() {}\n")

        with self.assertRaisesRegex(MediaValidationError, "exceeds"):
            read_validated_media(path, "text/x-glsl", limit=4)

    def test_detects_identity_change_during_validation(self) -> None:
        path = self.root / "tone.wav"
        path.write_bytes(_wav())
        original_validate = media_module._validate_media_payload

        def mutate_after_validation(payload: bytes, media_type: str, source: Path) -> None:
            original_validate(payload, media_type, source)
            info = path.stat()
            try:
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
            except OSError as exc:
                self.skipTest(f"cannot mutate open-file metadata on this platform: {exc}")

        with (
            patch(
                "isoworld.content.media._validate_media_payload",
                side_effect=mutate_after_validation,
            ),
            self.assertRaisesRegex(MediaValidationError, "identity changed"),
        ):
            read_validated_media(path, "audio/wav")


class RenderPackResourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_worldpack(WORLDPACK)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_rejects_nonportable_and_noncanonical_paths(self) -> None:
        invalid = (
            "/absolute.wav",
            "folder\\tone.wav",
            "../tone.wav",
            "folder/../tone.wav",
            "./tone.wav",
            "folder//tone.wav",
            "e\u0301.wav",
            "AUX.wav",
            "tone.wav.",
        )
        for relative in invalid:
            with self.subTest(relative=relative):
                renderpack = _renderpack(
                    self.root,
                    self.pack,
                    [_asset("neutral_sfx", "sfx", "audio", relative, "audio/wav", "0" * 64)],
                )
                with self.assertRaisesRegex(RenderPackError, "portable and canonical"):
                    load_renderpack(renderpack, self.pack)

    def test_rejects_casefold_collision_across_assets(self) -> None:
        first = self.root / "Assets/Tone.wav"
        first.parent.mkdir()
        first.write_bytes(_wav())
        assets = [
            _asset(
                "first_sfx",
                "sfx",
                "audio",
                "Assets/Tone.wav",
                "audio/wav",
                hashlib.sha256(first.read_bytes()).hexdigest(),
            ),
            _asset(
                "second_sfx",
                "sfx",
                "audio",
                "assets/tone.wav",
                "audio/wav",
                "0" * 64,
            ),
        ]
        renderpack = _renderpack(self.root, self.pack, assets)

        with self.assertRaisesRegex(RenderPackError, "NFC/casefold"):
            load_renderpack(renderpack, self.pack)

    def test_rejects_exact_duplicate_path_across_assets(self) -> None:
        resource = self.root / "tone.wav"
        resource.write_bytes(_wav())
        digest = hashlib.sha256(resource.read_bytes()).hexdigest()
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset("first_sfx", "sfx", "audio", "tone.wav", "audio/wav", digest),
                _asset("second_sfx", "sfx", "audio", "tone.wav", "audio/wav", digest),
            ],
        )

        with self.assertRaisesRegex(RenderPackError, "canonical ownership"):
            load_renderpack(renderpack, self.pack)

    def test_wraps_huge_clipset_integer_as_renderpack_error(self) -> None:
        texture = self.root / "sheet.png"
        texture.write_bytes(_png())
        clipset = self.root / "clips.json"
        clipset.write_bytes(
            json.dumps(
                {
                    "format": "isoworld.clipset",
                    "format_version": 1,
                    "clips": [
                        {
                            "id": "idle",
                            "pivot": [10**400, 0],
                            "loop": True,
                            "frames": [
                                {
                                    "x": 0,
                                    "y": 0,
                                    "width": 1,
                                    "height": 1,
                                    "duration_ticks": 1,
                                }
                            ],
                        }
                    ],
                }
            ).encode()
        )
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                {
                    "id": "neutral_sheet",
                    "kind": "spritesheet",
                    "files": [
                        {
                            "role": "texture",
                            "path": texture.name,
                            "sha256": hashlib.sha256(texture.read_bytes()).hexdigest(),
                            "media_type": "image/png",
                        },
                        {
                            "role": "clipset",
                            "path": clipset.name,
                            "sha256": hashlib.sha256(clipset.read_bytes()).hexdigest(),
                            "media_type": "application/json",
                        },
                    ],
                }
            ],
        )

        with self.assertRaisesRegex(RenderPackError, "pivot/0 must be finite"):
            load_renderpack(renderpack, self.pack)

    def test_rejects_symlink_hardlink_and_nonregular_resources(self) -> None:
        cases: list[tuple[str, str]] = []
        target = self.root / "target.wav"
        target.write_bytes(_wav())
        symlink = self.root / "symlink.wav"
        try:
            symlink.symlink_to(target.name)
        except OSError:
            pass
        else:
            cases.append(("symlink.wav", "symbolic link"))
        hardlink = self.root / "hardlink.wav"
        try:
            os.link(target, hardlink)
        except OSError:
            pass
        else:
            cases.append(("hardlink.wav", "hard-linked"))
        directory = self.root / "directory.wav"
        directory.mkdir()
        cases.append(("directory.wav", "regular file"))

        for index, (relative, message) in enumerate(cases):
            with self.subTest(relative=relative):
                renderpack = _renderpack(
                    self.root,
                    self.pack,
                    [
                        _asset(
                            f"unsafe_{index}",
                            "sfx",
                            "audio",
                            relative,
                            "audio/wav",
                            "0" * 64,
                        )
                    ],
                )
                with self.assertRaisesRegex(RenderPackError, message):
                    load_renderpack(renderpack, self.pack)

    def test_rejects_symlinked_resource_parent(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "tone.wav").write_bytes(_wav())
        linked = self.root / "linked"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        renderpack = _renderpack(
            self.root,
            self.pack,
            [_asset("unsafe_sfx", "sfx", "audio", "linked/tone.wav", "audio/wav", "0" * 64)],
        )

        with self.assertRaisesRegex(RenderPackError, "safe directory"):
            load_renderpack(renderpack, self.pack)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_rejects_fifo_resource_without_blocking(self) -> None:
        fifo = self.root / "tone.wav"
        os.mkfifo(fifo)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [_asset("unsafe_sfx", "sfx", "audio", "tone.wav", "audio/wav", "0" * 64)],
        )

        with self.assertRaisesRegex(RenderPackError, "regular file"):
            load_renderpack(renderpack, self.pack)

    def test_hash_and_structure_use_one_stable_resource_read(self) -> None:
        resource = self.root / "tone.wav"
        resource.write_bytes(_wav())
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    "tone.wav",
                    "audio/wav",
                    hashlib.sha256(resource.read_bytes()).hexdigest(),
                )
            ],
        )
        original_read = media_module._capture_descriptor

        with patch(
            "isoworld.content.media._capture_descriptor",
            wraps=original_read,
        ) as capture_payload:
            loaded = load_renderpack(renderpack, self.pack)

        self.assertEqual("neutral_sfx", loaded.assets[0].id)
        self.assertEqual(1, capture_payload.call_count)


if __name__ == "__main__":
    unittest.main()
