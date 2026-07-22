from __future__ import annotations

import binascii
import copy
import gc
import hashlib
import json
import mmap
import os
import stat
import struct
import tempfile
import tracemalloc
import unittest
import warnings
import wave
import zlib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import isoworld.content.media as media_module
import isoworld.content.resource_snapshot as snapshot_module
from isoworld.content.file_stat import (
    WindowsFileStat,
    descriptor_file_stat,
    path_file_stat,
)
from isoworld.content.loader import load_worldpack
from isoworld.content.media import (
    MediaValidationError,
    media_signature_matches,
    read_validated_media,
)
from isoworld.content.renderpack import RenderPack, RenderPackError, load_renderpack
from isoworld.content.resource_snapshot import ResourceSnapshotError, ResourceSnapshotOwner
from isoworld.render.resources import RaylibAssetRegistry, ResourceError

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


def _write_large_wav(path: Path, *, data_bytes: int = 5 * 1024 * 1024) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22050)
        target.writeframes(b"\0" * (data_bytes - data_bytes % 2))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


class _ByteReadingRaylib:
    def __init__(
        self,
        *,
        unload_fails: bool = False,
        close_audio_fails: bool = False,
    ) -> None:
        self.loaded_payloads: list[bytes] = []
        self.unload_fails = unload_fails
        self.close_audio_fails = close_audio_fails
        self.close_audio_calls = 0
        self.unload_sound_calls = 0

    def init_audio_device(self) -> None:
        pass

    def is_audio_device_ready(self) -> bool:
        return True

    def load_sound(self, path: str) -> object:
        self.loaded_payloads.append(Path(path).read_bytes())
        return object()

    def is_sound_valid(self, value: object) -> bool:
        return True

    def unload_sound(self, value: object) -> None:
        self.unload_sound_calls += 1
        if self.unload_fails:
            raise RuntimeError("native unload failed")

    def close_audio_device(self) -> None:
        self.close_audio_calls += 1
        if self.close_audio_fails:
            raise RuntimeError("audio close failed")


class _WinFunction:
    def __init__(self, result: object) -> None:
        self.result = result

    def __call__(self, *args: object) -> object:
        return self.result


def _reparse_info(info: os.stat_result) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=info.st_mode,
        st_dev=info.st_dev,
        st_ino=info.st_ino,
        st_nlink=info.st_nlink,
        st_size=info.st_size,
        st_mtime_ns=info.st_mtime_ns,
        st_ctime_ns=info.st_ctime_ns,
        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
    )


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
                self.assertEqual(path.resolve(strict=True), validated.path)

    def test_accepts_standard_animated_webp_with_anmf_frame(self) -> None:
        path = self.root / "animated.webp"
        path.write_bytes(_animated_webp())

        validated = read_validated_media(path, "image/webp")

        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), validated.sha256)

    def test_rejects_direct_parent_link_before_opening_target(self) -> None:
        actual = self.root / "actual"
        actual.mkdir()
        (actual / "image.png").write_bytes(_png())
        alias = self.root / "actual-alias"
        try:
            alias.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        with (
            patch.object(
                media_module,
                "_open_resource",
                side_effect=AssertionError("link target was opened"),
            ) as open_resource,
            self.assertRaisesRegex(MediaValidationError, "safe directory"),
        ):
            read_validated_media(alias / "image.png", "image/png")

        open_resource.assert_not_called()

    def test_rejects_linked_ancestor_before_path_normalization_or_open(self) -> None:
        safe = self.root / "safe"
        safe.mkdir()
        (safe / "image.png").write_bytes(_png())
        linked_target = self.root / "outside/nested"
        linked_target.mkdir(parents=True)
        linked = self.root / "linked"
        try:
            linked.symlink_to(linked_target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        source = linked / ".." / "safe/image.png"

        with (
            patch.object(
                media_module,
                "_open_resource",
                side_effect=AssertionError("link target was opened"),
            ) as open_resource,
            self.assertRaisesRegex(MediaValidationError, "safe directory"),
        ):
            read_validated_media(source, "image/png")

        open_resource.assert_not_called()

    def test_rejects_reparse_parent_and_file_before_opening_target(self) -> None:
        reparse_parent = self.root / "reparse-parent"
        reparse_parent.mkdir()
        nested_target = reparse_parent / "image.png"
        nested_target.write_bytes(_png())
        reparse_file = self.root / "reparse-file.png"
        reparse_file.write_bytes(_png())
        canonical_reparse_file = reparse_file.resolve(strict=True)
        real_stat = media_module._non_following_stat

        def reparse_parent_stat(candidate: Path) -> object:
            if candidate == nested_target:
                raise AssertionError("reparse directory target was inspected")
            info = real_stat(candidate)
            return _reparse_info(info) if candidate == reparse_parent else info

        def reparse_file_stat(candidate: Path) -> object:
            info = real_stat(candidate)
            return _reparse_info(info) if candidate == canonical_reparse_file else info

        cases = (
            (nested_target, reparse_parent_stat, "safe directory"),
            (reparse_file, reparse_file_stat, "symbolic link or reparse point"),
        )
        for path, stat_effect, message in cases:
            with (
                self.subTest(path=path.name),
                patch.object(
                    media_module,
                    "_non_following_stat",
                    side_effect=stat_effect,
                ),
                patch.object(
                    media_module,
                    "_open_resource",
                    side_effect=AssertionError("reparse target was opened"),
                ) as open_resource,
                self.assertRaisesRegex(MediaValidationError, message),
            ):
                read_validated_media(path, "image/png")

            open_resource.assert_not_called()

    def test_handle_stat_contract_ignores_divergent_windows_path_stat(self) -> None:
        path = self.root / "unchanged.png"
        path.write_bytes(_png())
        canonical_path = path.resolve(strict=True)
        real_path_stat = os.stat
        real_descriptor_stat = os.fstat
        target_path_states: list[WindowsFileStat] = []
        descriptor_states: list[WindowsFileStat] = []

        def handle_state(info: os.stat_result) -> WindowsFileStat:
            return WindowsFileStat(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_nlink=info.st_nlink,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
                st_file_attributes=getattr(info, "st_file_attributes", 0),
            )

        def legacy_path_stat(candidate: object, *args: object, **kwargs: object) -> object:
            info = real_path_stat(candidate, *args, **kwargs)
            return SimpleNamespace(
                st_dev=info.st_dev + 1,
                st_ino=info.st_ino + 1,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_file_attributes=getattr(info, "st_file_attributes", 0),
            )

        def legacy_descriptor_stat(descriptor: int) -> object:
            info = real_descriptor_stat(descriptor)
            return SimpleNamespace(
                st_dev=info.st_dev + 2,
                st_ino=info.st_ino + 2,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_file_attributes=getattr(info, "st_file_attributes", 0),
            )

        def path_handle_stat(candidate: object) -> WindowsFileStat:
            state = handle_state(real_path_stat(candidate, follow_symlinks=False))
            if Path(candidate) == canonical_path:
                target_path_states.append(state)
            return state

        def descriptor_handle_stat(descriptor: int) -> WindowsFileStat:
            state = handle_state(real_descriptor_stat(descriptor))
            descriptor_states.append(state)
            return state

        descriptor = os.open(path, os.O_RDONLY)
        try:
            divergent_descriptor_state = legacy_descriptor_stat(descriptor)
        finally:
            os.close(descriptor)
        self.assertNotEqual(
            media_module._file_state(legacy_path_stat(path, follow_symlinks=False)),
            media_module._file_state(divergent_descriptor_state),
        )

        with (
            patch.object(media_module, "_DIR_FD_READ", False),
            patch.object(media_module.os, "stat", side_effect=legacy_path_stat) as legacy_stat,
            patch.object(
                media_module.os,
                "fstat",
                side_effect=legacy_descriptor_stat,
            ) as legacy_fstat,
            patch.object(
                media_module,
                "path_file_stat",
                side_effect=path_handle_stat,
            ) as path_stat,
            patch.object(
                media_module,
                "descriptor_file_stat",
                side_effect=descriptor_handle_stat,
            ) as fd_stat,
        ):
            validated = read_validated_media(path, "image/png")

        self.assertEqual(hashlib.sha256(_png()).hexdigest(), validated.sha256)
        self.assertGreater(path_stat.call_count, 0)
        self.assertGreater(fd_stat.call_count, 0)
        self.assertTrue(target_path_states)
        self.assertTrue(descriptor_states)
        self.assertEqual(
            media_module._file_state(target_path_states[0]),
            media_module._file_state(descriptor_states[0]),
        )
        legacy_stat.assert_not_called()
        legacy_fstat.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "native Windows file identity contract")
    def test_native_windows_path_and_descriptor_file_states_agree(self) -> None:
        path = self.root / "native-identity.png"
        payload = _png()
        path.write_bytes(payload)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            path_state = path_file_stat(path)
            descriptor_state = descriptor_file_stat(descriptor)
            fields = (
                "st_mode",
                "st_dev",
                "st_ino",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_file_attributes",
            )

            self.assertIsInstance(path_state, WindowsFileStat)
            self.assertIsInstance(descriptor_state, WindowsFileStat)
            self.assertEqual(
                tuple(getattr(path_state, field) for field in fields),
                tuple(getattr(descriptor_state, field) for field in fields),
            )
            self.assertTrue(stat.S_ISREG(path_state.st_mode))
            self.assertEqual(1, path_state.st_nlink)
            self.assertEqual(len(payload), path_state.st_size)
            os.lseek(descriptor, 0, os.SEEK_SET)
            self.assertEqual(payload, os.read(descriptor, len(payload)))
        finally:
            os.close(descriptor)

        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_rejects_same_byte_file_replacement_before_open(self) -> None:
        path = self.root / "replace-me.png"
        payload = _png()
        path.write_bytes(payload)
        original_open = media_module._open_resource
        replaced = False

        def replace_then_open(snapshot: object, relative: PurePosixPath) -> tuple[int, list[int]]:
            nonlocal replaced
            target = snapshot.target
            target.rename(target.with_name("original.png"))
            target.write_bytes(payload)
            replaced = True
            return original_open(snapshot, relative)

        with (
            patch.object(media_module, "_open_resource", side_effect=replace_then_open),
            self.assertRaisesRegex(MediaValidationError, "identity changed while opening"),
        ):
            read_validated_media(path, "image/png")

        self.assertTrue(replaced)
        self.assertEqual(payload, path.read_bytes())

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

    def test_snapshot_file_state_uses_handle_contract_on_windows(self) -> None:
        source_root = self.root / "source"
        source_root.mkdir()
        source = source_root / "tone.wav"
        source.write_bytes(_wav())
        owner = ResourceSnapshotOwner()
        real_path_stat = os.stat
        real_descriptor_stat = os.fstat

        def windows_state(info: os.stat_result) -> WindowsFileStat:
            return WindowsFileStat(
                st_mode=info.st_mode,
                st_dev=0x1234,
                st_ino=0x5678,
                st_nlink=info.st_nlink,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns,
                st_file_attributes=getattr(info, "st_file_attributes", 0),
            )

        raw_path_stat = patch.object(
            snapshot_module,
            "_entry_stat",
            side_effect=AssertionError("incompatible path stat tuple was used"),
        )
        try:
            with (
                patch.object(snapshot_module, "_platform_name", return_value="nt"),
                patch.object(
                    snapshot_module,
                    "path_file_stat",
                    side_effect=lambda candidate: windows_state(
                        real_path_stat(candidate, follow_symlinks=False)
                    ),
                ) as path_stat,
                patch.object(
                    snapshot_module,
                    "descriptor_file_stat",
                    side_effect=lambda descriptor: windows_state(real_descriptor_stat(descriptor)),
                ) as fd_stat,
            ):
                with raw_path_stat as incompatible_stat:
                    captured = owner.materialize(
                        source_root,
                        PurePosixPath("tone.wav"),
                        "audio/wav",
                        limit=len(_wav()),
                    )
                self.assertEqual(hashlib.sha256(_wav()).hexdigest(), captured.sha256)
                incompatible_stat.assert_not_called()
                self.assertGreater(path_stat.call_count, 0)
                self.assertGreater(fd_stat.call_count, 0)
                owner.close()
        finally:
            owner.close()

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
        loaded.close()

    def test_copy_seam_uses_validated_capture_after_source_replacement(self) -> None:
        resource = self.root / "tone.wav"
        original = _wav()
        replacement = original + b"substituted"
        resource.write_bytes(original)
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
                    hashlib.sha256(original).hexdigest(),
                )
            ],
        )
        original_copy = media_module._copy_capture_to_descriptor

        def replace_then_copy(*args: object, **kwargs: object) -> None:
            resource.write_bytes(replacement)
            original_copy(*args, **kwargs)

        with patch(
            "isoworld.content.media._copy_capture_to_descriptor",
            side_effect=replace_then_copy,
        ):
            loaded = load_renderpack(renderpack, self.pack)

        item = loaded.assets[0].files[0]
        self.assertEqual(original, loaded.resolve_file(item).read_bytes())
        self.assertEqual(hashlib.sha256(original).hexdigest(), item.sha256)
        loaded.close()

    def test_raylib_reads_snapshot_after_source_replacement_and_deletion(self) -> None:
        resource = self.root / "tone.wav"
        original = _wav()
        resource.write_bytes(original)
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
                    hashlib.sha256(original).hexdigest(),
                )
            ],
        )
        snapshot_parent = self.root / "snapshots"
        snapshot_parent.mkdir()
        with patch.object(tempfile, "tempdir", str(snapshot_parent)):
            loaded = load_renderpack(renderpack, self.pack)
            resource.write_bytes(original + b"replacement")
            resource.unlink()
            fake = _ByteReadingRaylib(unload_fails=True)
            registry = RaylibAssetRegistry(fake, loaded)
            registry.load()
            with self.assertRaisesRegex(ResourceError, "unload sound neutral_sfx/0"):
                registry.close()
            self.assertEqual(1, len(registry.sounds["neutral_sfx"]))
            fake.unload_fails = False
            registry.close()

            self.assertEqual([original], fake.loaded_payloads)
            self.assertTrue(loaded.root.exists(), "the registry must only borrow the renderpack")
            loaded.close()
        self.assertEqual([], list(snapshot_parent.iterdir()))

    def test_snapshot_rejects_in_place_byte_change_before_fake_raylib_load(self) -> None:
        resource = self.root / "tone.wav"
        original = _wav()
        resource.write_bytes(original)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(original).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        item = loaded.assets[0].files[0]
        snapshot = loaded.resolve_file(item)
        original_state = snapshot.stat()
        if os.name == "posix":
            self.assertEqual(0o400, snapshot.stat().st_mode & 0o777)

        os.chmod(snapshot, 0o600)
        changed = bytes((original[0] ^ 0xFF,)) + original[1:]
        snapshot.write_bytes(changed)
        fake = _ByteReadingRaylib()
        registry = RaylibAssetRegistry(fake, loaded)
        with self.assertRaisesRegex(ResourceError, "Snapshot file state changed"):
            registry.load()
        self.assertEqual([], fake.loaded_payloads)

        snapshot.write_bytes(original)
        os.utime(
            snapshot,
            ns=(original_state.st_atime_ns, original_state.st_mtime_ns),
        )
        os.chmod(snapshot, 0o400 if os.name == "posix" else stat.S_IREAD)
        loaded.close()

    def test_registry_surfaces_audio_close_failure_and_retries_remaining_state(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        fake = _ByteReadingRaylib(close_audio_fails=True)
        registry = RaylibAssetRegistry(fake, loaded)
        registry.load()

        with self.assertRaisesRegex(ResourceError, "close audio device: audio close failed"):
            registry.close()
        self.assertEqual({}, registry.sounds)
        self.assertTrue(registry._audio_initialized)
        self.assertTrue(loaded.root.exists(), "the registry must not close its renderpack")

        fake.close_audio_fails = False
        registry.close()
        self.assertEqual(2, fake.close_audio_calls)
        self.assertFalse(registry._audio_initialized)
        loaded.close()

    def test_registry_attempts_every_unload_and_retains_each_failed_handle(self) -> None:
        first = self.root / "first.wav"
        second = self.root / "second.wav"
        payload = _wav()
        first.write_bytes(payload)
        second.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "first_sfx",
                    "sfx",
                    "audio",
                    first.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                ),
                _asset(
                    "second_sfx",
                    "sfx",
                    "audio",
                    second.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                ),
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        fake = _ByteReadingRaylib(unload_fails=True)
        registry = RaylibAssetRegistry(fake, loaded)
        registry.load()

        with self.assertRaisesRegex(ResourceError, "first_sfx/0") as caught:
            registry.close()
        self.assertIn("second_sfx/0", str(caught.exception))
        self.assertEqual(2, fake.unload_sound_calls)
        self.assertEqual({"first_sfx", "second_sfx"}, set(registry.sounds))
        self.assertTrue(registry._audio_initialized)

        fake.unload_fails = False
        registry.close()
        self.assertEqual(4, fake.unload_sound_calls)
        self.assertEqual({}, registry.sounds)
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "POSIX privacy modes")
    def test_snapshot_rejects_a_world_readable_sealed_file(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        item = loaded.assets[0].files[0]
        snapshot = loaded.resolve_file(item)
        os.chmod(snapshot, 0o644)
        with self.assertRaisesRegex(RenderPackError, "state changed|not sealed and private"):
            loaded.resolve_file(item)
        os.chmod(snapshot, 0o400)
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptor semantics")
    def test_creation_stays_in_owned_directory_when_visible_parent_is_replaced(self) -> None:
        source_root = self.root / "source"
        source = source_root / "audio/tone.wav"
        source.parent.mkdir(parents=True)
        source.write_bytes(_wav())
        attacker = self.root / "attacker"
        attacker.mkdir()
        owner = ResourceSnapshotOwner()
        original_open = snapshot_module._open_new_file
        swapped: dict[str, Path] = {}

        def replace_parent_then_open(**kwargs: object) -> int:
            parent = Path(kwargs["parent_path"])
            moved = parent.with_name(f"{parent.name}-owned")
            parent.rename(moved)
            parent.symlink_to(attacker, target_is_directory=True)
            swapped.update(parent=parent, moved=moved)
            return original_open(**kwargs)

        with (
            patch.object(
                snapshot_module,
                "_open_new_file",
                side_effect=replace_parent_then_open,
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "directory"),
        ):
            owner.materialize(
                source_root,
                PurePosixPath("audio/tone.wav"),
                "audio/wav",
                limit=1024 * 1024,
            )

        self.assertFalse((attacker / "tone.wav").exists())
        self.assertFalse((swapped["moved"] / "tone.wav").exists())
        swapped["parent"].unlink()
        swapped["moved"].rename(swapped["parent"])
        owner.close()

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_intermediate_symlink_to_moved_owned_directory_is_rejected(self) -> None:
        resource = self.root / "audio/tone.wav"
        resource.parent.mkdir()
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    "audio/tone.wav",
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        owned_parent = loaded.root / "audio"
        moved_parent = loaded.root / "audio-owned"
        owned_parent.rename(moved_parent)
        owned_parent.symlink_to(moved_parent.name, target_is_directory=True)

        with self.assertRaisesRegex(RenderPackError, "no longer a directory"):
            loaded.resolve_file(loaded.assets[0].files[0])
        with self.assertRaises(RenderPackError):
            loaded.close()
        self.assertTrue(owned_parent.is_symlink())
        owner = loaded._snapshot_owner
        assert owner is not None
        self.assertFalse(owner.closed)

        owned_parent.unlink()
        moved_parent.rename(owned_parent)
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "POSIX no-replace cleanup claims")
    def test_file_cleanup_claim_never_deletes_a_replacement_and_is_retryable(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        original_claim = snapshot_module._claim_entry
        seam: dict[str, Path] = {}

        def replace_file_at_claim(**kwargs: object) -> None:
            parent = Path(kwargs["parent_path"])
            source_name = str(kwargs["source_name"])
            if not bool(kwargs["directory"]) and not seam:
                source = parent / source_name
                moved = parent / f"{source_name}.owned"
                source.rename(moved)
                source.write_bytes(b"foreign replacement")
                seam.update(source=source, moved=moved)
            original_claim(**kwargs)

        with (
            patch.object(
                snapshot_module,
                "_claim_entry",
                side_effect=replace_file_at_claim,
            ),
            self.assertRaisesRegex(RenderPackError, "identity changed before cleanup"),
        ):
            loaded.close()

        self.assertEqual(b"foreign replacement", seam["source"].read_bytes())
        owner = loaded._snapshot_owner
        assert owner is not None
        self.assertFalse(owner.closed)
        seam["source"].unlink()
        seam["moved"].rename(seam["source"])
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "POSIX no-replace cleanup claims")
    def test_root_cleanup_claim_never_deletes_a_replacement_and_is_retryable(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        original_claim = snapshot_module._claim_entry
        seam: dict[str, Path] = {}

        def replace_root_at_claim(**kwargs: object) -> None:
            parent = Path(kwargs["parent_path"])
            source_name = str(kwargs["source_name"])
            if bool(kwargs["directory"]) and parent == loaded.root.parent and not seam:
                source = parent / source_name
                moved = parent / f"{source_name}-owned"
                source.rename(moved)
                source.mkdir(mode=0o700)
                seam.update(source=source, moved=moved)
            original_claim(**kwargs)

        with (
            patch.object(
                snapshot_module,
                "_claim_entry",
                side_effect=replace_root_at_claim,
            ),
            self.assertRaisesRegex(RenderPackError, "identity changed before cleanup"),
        ):
            loaded.close()

        self.assertTrue(seam["source"].is_dir())
        owner = loaded._snapshot_owner
        assert owner is not None
        self.assertFalse(owner.closed)
        seam["source"].rmdir()
        seam["moved"].rename(seam["source"])
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "mocked Windows raw-handle semantics")
    def test_windows_lock_closes_raw_handle_and_preserves_validation_and_close_errors(
        self,
    ) -> None:
        kernel32 = SimpleNamespace(CreateFileW=_WinFunction(321))
        validation_error = ResourceSnapshotError("injected attribute validation failure")
        close_error = ResourceSnapshotError("injected raw handle close failure")

        with (
            patch.object(
                snapshot_module.ctypes,
                "WinDLL",
                create=True,
                return_value=kernel32,
            ),
            patch.object(
                snapshot_module,
                "_windows_attributes",
                side_effect=validation_error,
            ),
            patch.object(
                snapshot_module,
                "_windows_close_handle",
                side_effect=close_error,
            ) as close_handle,
            self.assertRaises(ResourceSnapshotError) as caught,
        ):
            snapshot_module._windows_lock_directory(Path("mocked-windows-directory"))

        close_handle.assert_called_once_with(321)
        self.assertIs(validation_error, caught.exception.__cause__)
        self.assertIn("attribute validation failure", str(caught.exception))
        self.assertIn("raw handle close failure", str(caught.exception))

    @unittest.skipUnless(os.name == "posix", "mocked Windows CreateDirectory semantics")
    def test_windows_directory_collisions_are_normalized_for_allocation_retry(self) -> None:
        for error in (80, 183):
            with self.subTest(error=error):
                advapi32 = SimpleNamespace(
                    ConvertStringSecurityDescriptorToSecurityDescriptorW=_WinFunction(1)
                )
                kernel32 = SimpleNamespace(
                    CreateDirectoryW=_WinFunction(0),
                    LocalFree=_WinFunction(None),
                )
                with (
                    patch.object(
                        snapshot_module.ctypes,
                        "WinDLL",
                        create=True,
                        side_effect=[advapi32, kernel32],
                    ),
                    patch.object(
                        snapshot_module.ctypes,
                        "get_last_error",
                        create=True,
                        return_value=error,
                    ),
                    patch.object(
                        snapshot_module.ctypes,
                        "FormatError",
                        create=True,
                        side_effect=lambda value: f"winerror {value}",
                    ),
                    patch.object(
                        snapshot_module,
                        "_windows_private_sid_strings",
                        return_value=(
                            "S-1-5-21-test",
                            "S-1-5-18",
                            "S-1-5-32-544",
                        ),
                    ),
                    self.assertRaises(FileExistsError) as caught,
                ):
                    snapshot_module._windows_create_private_directory(Path("mocked-collision"))

                self.assertEqual(error, caught.exception.errno)

    def test_windows_private_acl_accepts_exact_creation_principals(self) -> None:
        principals = (
            "S-1-5-21-current-user",
            "S-1-5-18",
            "S-1-5-32-544",
        )
        with (
            patch.object(
                snapshot_module,
                "_windows_private_sid_strings",
                return_value=principals,
            ),
            patch.object(
                snapshot_module,
                "_windows_acl_sid_inventory",
                return_value=(principals[2], (principals[1], principals[0], principals[2])),
            ),
        ):
            snapshot_module._windows_acl_is_private(Path("mocked-private-directory"))

    def test_windows_private_acl_rejects_foreign_allow_ace(self) -> None:
        principals = (
            "S-1-5-21-current-user",
            "S-1-5-18",
            "S-1-5-32-544",
        )
        with (
            patch.object(
                snapshot_module,
                "_windows_private_sid_strings",
                return_value=principals,
            ),
            patch.object(
                snapshot_module,
                "_windows_acl_sid_inventory",
                return_value=(principals[0], (*principals, "S-1-5-21-foreign")),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "ACL is not private"),
        ):
            snapshot_module._windows_acl_is_private(Path("mocked-foreign-directory"))

    def test_windows_private_acl_requires_every_creation_principal(self) -> None:
        principals = (
            "S-1-5-21-current-user",
            "S-1-5-18",
            "S-1-5-32-544",
        )
        with (
            patch.object(
                snapshot_module,
                "_windows_private_sid_strings",
                return_value=principals,
            ),
            patch.object(
                snapshot_module,
                "_windows_acl_sid_inventory",
                return_value=(principals[0], principals[:2]),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "ACL is not private"),
        ):
            snapshot_module._windows_acl_is_private(Path("mocked-incomplete-directory"))

    @unittest.skipUnless(os.name == "nt", "native Windows ACL semantics")
    def test_windows_created_private_acl_passes_native_validation(self) -> None:
        path = self.root / "native-private-directory"
        snapshot_module._windows_create_private_directory(path)
        try:
            snapshot_module._windows_acl_is_private(path)
        finally:
            path.rmdir()

    @unittest.skipUnless(os.name == "posix", "mocked Windows reopen semantics")
    def test_windows_reopen_closes_pending_and_registered_handles_on_identity_failure(
        self,
    ) -> None:
        owner = ResourceSnapshotOwner()
        owner._ensure_parent(PurePosixPath("audio/tone.wav"))
        root_info = owner._active_root.lstat()
        validation_error = OSError("injected reopened identity failure")

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                snapshot_module,
                "_windows_lock_directory",
                side_effect=[401, 402],
            ),
            patch.object(Path, "lstat", side_effect=[root_info, validation_error]),
            patch.object(
                snapshot_module,
                "_windows_close_handle",
                side_effect=[
                    ResourceSnapshotError("injected pending handle close failure"),
                    None,
                ],
            ) as close_handle,
            self.assertRaises(ResourceSnapshotError) as caught,
        ):
            owner._reopen_windows_directory_handles()

        self.assertEqual([((402,), {}), ((401,), {})], close_handle.call_args_list)
        self.assertIs(validation_error, caught.exception.__cause__)
        self.assertIn("reopened identity failure", str(caught.exception))
        self.assertIn("pending handle close failure", str(caught.exception))
        self.assertEqual({PurePosixPath("audio"): 402}, owner._directory_handles)
        owner._directory_handles.clear()
        owner.close()

    @unittest.skipUnless(os.name == "posix", "mocked Windows partial-handle recovery")
    def test_windows_reopen_reuses_partial_root_handle_without_leaking_it(self) -> None:
        owner = ResourceSnapshotOwner()
        owner._ensure_parent(PurePosixPath("audio/tone.wav"))
        root_relative = PurePosixPath(".")
        child_relative = PurePosixPath("audio")
        owner._directory_handles = {root_relative: 501}
        root_info = owner._active_root.lstat()
        child_info = (owner._active_root / "audio").lstat()

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                snapshot_module,
                "_windows_lock_directory",
                return_value=502,
            ) as lock_directory,
            patch.object(Path, "lstat", side_effect=[root_info, child_info]),
            patch.object(snapshot_module, "_windows_close_handle") as close_handle,
        ):
            owner._reopen_windows_directory_handles()

        self.assertEqual(
            {root_relative: 501, child_relative: 502},
            owner._directory_handles,
        )
        lock_directory.assert_called_once_with(owner._active_root / "audio")
        close_handle.assert_not_called()
        owner._directory_handles.clear()
        owner.close()

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptor semantics")
    def test_root_creation_failure_closes_parent_descriptor_without_finalizer_warning(
        self,
    ) -> None:
        snapshot_parent = self.root / "failed-root-parent"
        snapshot_parent.mkdir()
        opened: list[int] = []
        original_open_parent = ResourceSnapshotOwner._open_absolute_directory
        owner = object.__new__(ResourceSnapshotOwner)

        def capture_parent(path: Path) -> int:
            descriptor = original_open_parent(path)
            opened.append(descriptor)
            return descriptor

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with (
                patch.object(tempfile, "tempdir", str(snapshot_parent)),
                patch.object(
                    ResourceSnapshotOwner,
                    "_open_absolute_directory",
                    side_effect=capture_parent,
                ),
                patch.object(
                    snapshot_module.os,
                    "mkdir",
                    side_effect=OSError("injected root creation failure"),
                ),
                self.assertRaisesRegex(OSError, "root creation failure"),
            ):
                owner.__init__()

            self.assertTrue(owner.closed)
            self.assertEqual(1, len(opened))
            with self.assertRaises(OSError):
                os.fstat(opened[0])
            del owner
            gc.collect()

        self.assertFalse(
            any("finalize runtime resource snapshot" in str(item.message) for item in caught)
        )
        self.assertEqual([], list(snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptor semantics")
    def test_root_identity_failure_closes_parent_descriptor_without_finalizer_warning(
        self,
    ) -> None:
        snapshot_parent = self.root / "failed-identity-parent"
        snapshot_parent.mkdir()
        opened: list[int] = []
        original_open_parent = ResourceSnapshotOwner._open_absolute_directory
        original_stat = snapshot_module.os.stat
        owner = object.__new__(ResourceSnapshotOwner)

        def capture_parent(path: Path) -> int:
            descriptor = original_open_parent(path)
            opened.append(descriptor)
            return descriptor

        def fail_root_identity(path: object, *args: object, **kwargs: object) -> os.stat_result:
            if str(path).startswith("isoworld-renderpack-") and kwargs.get("dir_fd") in opened:
                raise OSError("injected root identity failure")
            return original_stat(path, *args, **kwargs)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with (
                patch.object(tempfile, "tempdir", str(snapshot_parent)),
                patch.object(
                    ResourceSnapshotOwner,
                    "_open_absolute_directory",
                    side_effect=capture_parent,
                ),
                patch.object(snapshot_module.os, "stat", side_effect=fail_root_identity),
                self.assertRaisesRegex(ResourceSnapshotError, "identity was not acquired"),
            ):
                owner.__init__()

            self.assertTrue(owner.closed)
            self.assertEqual(1, len(opened))
            with self.assertRaises(OSError):
                os.fstat(opened[0])
            del owner
            gc.collect()

        self.assertFalse(
            any("finalize runtime resource snapshot" in str(item.message) for item in caught)
        )
        leftovers = list(snapshot_parent.iterdir())
        self.assertEqual(1, len(leftovers))
        leftovers[0].rmdir()

    @unittest.skipUnless(os.name == "posix", "mocked Windows parent-handle semantics")
    def test_windows_root_creation_failure_closes_parent_handle(self) -> None:
        snapshot_parent = self.root / "failed-windows-root-parent"
        snapshot_parent.mkdir()
        owner = object.__new__(ResourceSnapshotOwner)

        with (
            patch.object(tempfile, "tempdir", str(snapshot_parent)),
            patch.object(snapshot_module, "_platform_name", return_value="nt"),
            patch.object(snapshot_module, "_windows_lock_directory", return_value=777),
            patch.object(
                snapshot_module,
                "_windows_create_private_directory",
                side_effect=OSError("injected Windows root creation failure"),
            ),
            patch.object(snapshot_module, "_windows_close_handle") as close_handle,
            self.assertRaisesRegex(OSError, "Windows root creation failure"),
        ):
            owner.__init__()

        self.assertTrue(owner.closed)
        close_handle.assert_called_once_with(777)

    @unittest.skipUnless(os.name == "posix", "mocked Windows initialization retry")
    def test_windows_parent_handle_close_failure_remains_retryable_until_closed(self) -> None:
        snapshot_parent = self.root / "failed-windows-parent-close"
        snapshot_parent.mkdir()
        owner = object.__new__(ResourceSnapshotOwner)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with (
                patch.object(tempfile, "tempdir", str(snapshot_parent)),
                patch.object(snapshot_module, "_platform_name", return_value="nt"),
                patch.object(
                    snapshot_module,
                    "_windows_lock_directory",
                    return_value=778,
                ),
                patch.object(
                    snapshot_module,
                    "_windows_create_private_directory",
                    side_effect=OSError("injected Windows root creation failure"),
                ),
                patch.object(
                    snapshot_module,
                    "_windows_close_handle",
                    side_effect=[
                        ResourceSnapshotError("injected Windows parent handle close failure"),
                        None,
                    ],
                ) as close_handle,
            ):
                with self.assertRaisesRegex(
                    ResourceSnapshotError,
                    "Windows parent handle close failure",
                ):
                    owner.__init__()

                self.assertFalse(owner.closed)
                self.assertTrue(owner._root_removed)
                self.assertEqual(778, owner._parent_handle)
                owner.close()
                self.assertTrue(owner.closed)
                self.assertIsNone(owner._parent_handle)
                self.assertEqual(
                    [((778,), {}), ((778,), {})],
                    close_handle.call_args_list,
                )
            del owner
            gc.collect()

        self.assertFalse(
            any("finalize runtime resource snapshot" in str(item.message) for item in caught)
        )

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptor semantics")
    def test_new_directory_open_failure_rolls_back_provisional_linux_ownership(self) -> None:
        owner = ResourceSnapshotOwner()
        original_open = snapshot_module.os.open
        root_descriptor = owner._directory_descriptors[PurePosixPath(".")]

        def fail_new_directory_open(path: object, *args: object, **kwargs: object) -> int:
            if path == "audio" and kwargs.get("dir_fd") == root_descriptor:
                raise OSError("injected directory descriptor failure")
            return original_open(path, *args, **kwargs)

        with (
            patch.object(
                snapshot_module.os,
                "open",
                side_effect=fail_new_directory_open,
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "directory descriptor failure"),
        ):
            owner._ensure_parent(PurePosixPath("audio/tone.wav"))

        relative = PurePosixPath("audio")
        self.assertFalse((owner.root / "audio").exists())
        self.assertNotIn(relative, owner._directories)
        self.assertNotIn(relative, owner._directory_descriptors)
        owner.close()

    @unittest.skipUnless(os.name == "posix", "POSIX partial ownership semantics")
    def test_failed_partial_unlink_records_exact_inventory_for_close_retry(self) -> None:
        owner = ResourceSnapshotOwner()
        relative = PurePosixPath("partial.bin")
        target, parent_relative, descriptor, identity = owner._open_target(relative)
        os.write(descriptor, b"owned partial bytes")
        os.close(descriptor)
        original_unlink = snapshot_module.os.unlink
        failed = False

        def fail_first_claim_unlink(path: object, *args: object, **kwargs: object) -> None:
            nonlocal failed
            if not failed and str(path).startswith(".isoworld-delete-"):
                failed = True
                raise OSError("injected partial unlink failure")
            original_unlink(path, *args, **kwargs)

        with (
            patch.object(snapshot_module.os, "unlink", side_effect=fail_first_claim_unlink),
            self.assertRaisesRegex(ResourceSnapshotError, "partial unlink failure"),
        ):
            owner._remove_partial(parent_relative, relative.name, identity)

        self.assertTrue(failed)
        self.assertEqual(b"owned partial bytes", target.read_bytes())
        self.assertEqual(0o400, stat.S_IMODE(target.stat().st_mode))
        self.assertIn(relative, owner._files)
        owner.close()
        self.assertTrue(owner.closed)
        self.assertFalse(owner.root.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX partial ownership semantics")
    def test_failed_partial_claim_records_restored_target_for_close_retry(self) -> None:
        owner = ResourceSnapshotOwner()
        relative = PurePosixPath("partial-claim.bin")
        target, parent_relative, descriptor, identity = owner._open_target(relative)
        os.write(descriptor, b"claim retry bytes")
        os.close(descriptor)

        with (
            patch.object(
                snapshot_module,
                "_claim_entry",
                side_effect=ResourceSnapshotError("injected partial claim failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "ownership was recorded for retry"),
        ):
            owner._remove_partial(parent_relative, relative.name, identity)

        self.assertEqual(b"claim retry bytes", target.read_bytes())
        self.assertEqual(0o400, stat.S_IMODE(target.stat().st_mode))
        self.assertIn(relative, owner._files)
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX close ordering semantics")
    def test_target_close_failure_registers_partial_before_combined_error(self) -> None:
        owner = ResourceSnapshotOwner()
        relative = PurePosixPath("close-failure.bin")
        original_entry_stat = owner._entry_stat
        original_close = snapshot_module.os.close
        stat_calls = 0
        close_failures = 0

        def fail_first_entry_stat(
            parent_relative: PurePosixPath,
            name: str,
        ) -> os.stat_result:
            nonlocal stat_calls
            stat_calls += 1
            if stat_calls == 1:
                raise ResourceSnapshotError("injected target identity validation failure")
            return original_entry_stat(parent_relative, name)

        def close_then_fail_twice(descriptor: int) -> None:
            nonlocal close_failures
            original_close(descriptor)
            if close_failures < 2:
                close_failures += 1
                raise OSError("injected target descriptor close failure")

        with (
            patch.object(
                ResourceSnapshotOwner,
                "_entry_stat",
                side_effect=fail_first_entry_stat,
            ),
            patch.object(snapshot_module.os, "close", side_effect=close_then_fail_twice),
            self.assertRaises(ResourceSnapshotError) as caught,
        ):
            owner._open_target(relative)

        self.assertEqual(2, close_failures)
        self.assertIn("target descriptor close failure", str(caught.exception))
        self.assertIn("partial ownership was recorded", str(caught.exception))
        self.assertIn(relative, owner._files)
        target = owner._active_root / relative
        self.assertEqual(0o400, stat.S_IMODE(target.stat().st_mode))
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX materialization reconciliation")
    def test_materialize_validation_and_close_failure_tracks_partial_for_retry(self) -> None:
        owner = ResourceSnapshotOwner()
        relative = PurePosixPath("validation-close.wav")
        original_close = snapshot_module.os.close
        close_failed = False

        def write_then_fail_validation(
            *args: object,
            **kwargs: object,
        ) -> object:
            descriptor = int(kwargs["materialize_descriptor"])
            os.write(descriptor, b"identity-bound partial")
            raise MediaValidationError("injected material validation failure")

        def close_then_fail_once(descriptor: int) -> None:
            nonlocal close_failed
            original_close(descriptor)
            if not close_failed:
                close_failed = True
                raise OSError("injected material target close failure")

        with (
            patch.object(
                snapshot_module,
                "read_validated_resource",
                side_effect=write_then_fail_validation,
            ),
            patch.object(snapshot_module.os, "close", side_effect=close_then_fail_once),
            self.assertRaises(ResourceSnapshotError) as caught,
        ):
            owner.materialize(
                self.root,
                relative,
                "audio/wav",
                limit=1024,
            )

        self.assertTrue(close_failed)
        self.assertIn("material validation failure", str(caught.exception))
        self.assertIn("material target close failure", str(caught.exception))
        self.assertIn("ownership was recorded", str(caught.exception))
        self.assertIn(relative, owner._files)
        target = owner._active_root / relative
        self.assertTrue(target.is_file())
        self.assertEqual(b"identity-bound partial", target.read_bytes())
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX close ordering semantics")
    def test_nested_directory_close_failure_retains_retryable_ownership(self) -> None:
        owner = ResourceSnapshotOwner()
        relative = PurePosixPath("audio")
        owner._ensure_parent(relative / "tone.wav")
        descriptor = owner._directory_descriptors[relative]

        with (
            patch.object(
                snapshot_module.os,
                "close",
                side_effect=OSError("injected nested descriptor close failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "nested descriptor close failure"),
        ):
            owner._remove_owned_directory(relative, owner._directories[relative])

        self.assertTrue((owner._active_root / relative).is_dir())
        self.assertEqual(descriptor, owner._directory_descriptors[relative])
        self.assertIn(relative, owner._directories)
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX close ordering semantics")
    def test_root_handle_close_failure_does_not_remove_or_forget_root(self) -> None:
        owner = ResourceSnapshotOwner()
        root_relative = PurePosixPath(".")
        descriptor = owner._directory_descriptors[root_relative]

        with (
            patch.object(
                snapshot_module.os,
                "close",
                side_effect=OSError("injected root descriptor close failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "root descriptor close failure"),
        ):
            owner.close()

        self.assertFalse(owner.closed)
        self.assertFalse(owner._root_removed)
        self.assertTrue(owner._active_root.is_dir())
        self.assertEqual(descriptor, owner._directory_descriptors[root_relative])
        self.assertIn(root_relative, owner._directories)
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "mocked Windows close ordering semantics")
    def test_windows_root_handle_close_failure_retains_retryable_ownership(self) -> None:
        owner = ResourceSnapshotOwner()
        root_relative = PurePosixPath(".")
        owner._directory_handles[root_relative] = 601

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(ResourceSnapshotOwner, "_check_open", return_value=None),
            patch.object(ResourceSnapshotOwner, "_scan_inventory", return_value=None),
            patch.object(ResourceSnapshotOwner, "_claim_root", return_value=None),
            patch.object(ResourceSnapshotOwner, "_validate_directory", return_value=None),
            patch.object(
                snapshot_module,
                "_windows_close_handle",
                side_effect=ResourceSnapshotError("injected Windows root handle close failure"),
            ) as close_handle,
            self.assertRaisesRegex(
                ResourceSnapshotError,
                "Windows root handle close failure",
            ),
        ):
            owner.close()

        close_handle.assert_called_once_with(601)
        self.assertFalse(owner.closed)
        self.assertFalse(owner._root_removed)
        self.assertTrue(owner._active_root.is_dir())
        self.assertEqual(601, owner._directory_handles[root_relative])
        self.assertIn(root_relative, owner._directories)
        owner._directory_handles.pop(root_relative)
        owner.close()
        self.assertTrue(owner.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX close ordering semantics")
    def test_parent_handle_close_failure_finalizes_on_retry_after_root_removal(self) -> None:
        owner = ResourceSnapshotOwner()
        root_relative = PurePosixPath(".")
        parent_descriptor = owner._parent_descriptor
        assert parent_descriptor is not None
        original_close = snapshot_module.os.close
        parent_failed = False

        def fail_parent_once(descriptor: int) -> None:
            nonlocal parent_failed
            if descriptor == parent_descriptor and not parent_failed:
                parent_failed = True
                raise OSError("injected parent descriptor close failure")
            original_close(descriptor)

        with (
            patch.object(snapshot_module.os, "close", side_effect=fail_parent_once),
            self.assertRaisesRegex(ResourceSnapshotError, "parent descriptor close failure"),
        ):
            owner.close()

        self.assertTrue(parent_failed)
        self.assertFalse(owner.closed)
        self.assertTrue(owner._root_removed)
        self.assertFalse(owner._active_root.exists())
        self.assertEqual(parent_descriptor, owner._parent_descriptor)
        self.assertIn(root_relative, owner._directories)
        owner.close()
        self.assertTrue(owner.closed)
        self.assertIsNone(owner._parent_descriptor)

    @unittest.skipUnless(os.name == "posix", "simulated unsupported POSIX platform")
    def test_darwin_fails_before_allocating_an_unclosable_snapshot(self) -> None:
        with (
            patch.object(snapshot_module.sys, "platform", "darwin"),
            self.assertRaisesRegex(
                ResourceSnapshotError,
                "support Linux and Windows",
            ),
        ):
            ResourceSnapshotOwner()

    @unittest.skipUnless(os.name == "posix", "mocked Windows ownership path")
    def test_new_directory_handle_failure_rolls_back_provisional_windows_ownership(self) -> None:
        owner = ResourceSnapshotOwner()

        def create_directory(path: Path) -> None:
            path.mkdir(mode=0o700)

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                snapshot_module,
                "path_file_stat",
                side_effect=lambda candidate: os.stat(candidate, follow_symlinks=False),
            ),
            patch.object(snapshot_module, "descriptor_file_stat", side_effect=os.fstat),
            patch.object(
                ResourceSnapshotOwner,
                "_validate_directory",
                return_value=None,
            ),
            patch.object(
                snapshot_module,
                "_windows_create_private_directory",
                side_effect=create_directory,
            ),
            patch.object(
                snapshot_module,
                "_windows_lock_directory",
                side_effect=ResourceSnapshotError("injected Windows handle failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "Windows handle failure"),
        ):
            owner._ensure_parent(PurePosixPath("audio/tone.wav"))

        relative = PurePosixPath("audio")
        self.assertFalse((owner.root / "audio").exists())
        self.assertNotIn(relative, owner._directories)
        self.assertNotIn(relative, owner._directory_handles)
        owner.close()

    @unittest.skipUnless(os.name == "posix", "POSIX initialization cleanup semantics")
    def test_failed_initialization_root_removal_rolls_back_and_retries(self) -> None:
        snapshot_parent = self.root / "initialization-snapshots"
        snapshot_parent.mkdir()
        owner = object.__new__(ResourceSnapshotOwner)
        original_rmdir = snapshot_module.os.rmdir
        rmdir_calls = 0

        def fail_first_rmdir(*args: object, **kwargs: object) -> None:
            nonlocal rmdir_calls
            rmdir_calls += 1
            if rmdir_calls == 1:
                raise OSError("injected root removal failure")
            original_rmdir(*args, **kwargs)

        with (
            patch.object(tempfile, "tempdir", str(snapshot_parent)),
            patch.object(
                snapshot_module,
                "_private_directory",
                side_effect=ResourceSnapshotError("injected initialization failure"),
            ),
            patch.object(
                snapshot_module.os,
                "rmdir",
                side_effect=fail_first_rmdir,
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "ownership was restored"),
        ):
            owner.__init__()

        self.assertFalse(owner.closed)
        self.assertTrue(owner.root.is_dir())
        self.assertTrue(owner._active_root_name.startswith("isoworld-renderpack-"))
        self.assertIn(PurePosixPath("."), owner._directory_descriptors)
        owner.close()
        self.assertTrue(owner.closed)
        self.assertEqual([], list(snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "POSIX initialization close semantics")
    def test_failed_initialization_parent_close_failure_remains_finalizable(self) -> None:
        snapshot_parent = self.root / "initialization-parent-close"
        snapshot_parent.mkdir()
        owner = object.__new__(ResourceSnapshotOwner)
        original_close = snapshot_module.os.close
        parent_close_failed = False

        def fail_parent_once(descriptor: int) -> None:
            nonlocal parent_close_failed
            if descriptor == getattr(owner, "_parent_descriptor", None) and not parent_close_failed:
                parent_close_failed = True
                raise OSError("injected initialization parent close failure")
            original_close(descriptor)

        with (
            patch.object(tempfile, "tempdir", str(snapshot_parent)),
            patch.object(
                snapshot_module,
                "_private_directory",
                side_effect=ResourceSnapshotError("injected initialization failure"),
            ),
            patch.object(snapshot_module.os, "close", side_effect=fail_parent_once),
            self.assertRaisesRegex(
                ResourceSnapshotError,
                "initialization parent close failure",
            ),
        ):
            owner.__init__()

        root_relative = PurePosixPath(".")
        self.assertTrue(parent_close_failed)
        self.assertFalse(owner.closed)
        self.assertTrue(owner._root_removed)
        self.assertFalse(owner.root.exists())
        self.assertIn(root_relative, owner._directories)
        self.assertIsNotNone(owner._parent_descriptor)
        owner.close()
        self.assertTrue(owner.closed)
        self.assertEqual([], list(snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "mocked Windows rollback semantics")
    def test_windows_failed_unlink_restores_readonly_seal_before_retry(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        owner = loaded._snapshot_owner
        assert owner is not None
        owner._claim_root()
        relative = PurePosixPath(resource.name)
        record = owner._files[relative]

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                snapshot_module,
                "path_file_stat",
                side_effect=lambda candidate: os.stat(candidate, follow_symlinks=False),
            ),
            patch.object(snapshot_module, "descriptor_file_stat", side_effect=os.fstat),
            patch.object(
                ResourceSnapshotOwner,
                "_validate_file",
                return_value=record,
            ),
            patch.object(
                snapshot_module,
                "_validate_file_privacy",
                return_value=None,
            ),
            patch.object(
                Path,
                "unlink",
                side_effect=OSError("injected Windows unlink failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "Windows unlink failure"),
        ):
            owner._remove_owned_file(relative, record)

        restored = owner._active_root / relative.name
        self.assertTrue(restored.is_file())
        self.assertEqual(0o400, stat.S_IMODE(restored.stat().st_mode))
        self.assertIn(relative, owner._files)
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "mocked Windows handle semantics")
    def test_windows_directory_claim_failure_restores_stable_handle_for_retry(self) -> None:
        resource = self.root / "audio/tone.wav"
        resource.parent.mkdir()
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    "audio/tone.wav",
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        owner = loaded._snapshot_owner
        assert owner is not None
        owner._claim_root()
        relative = PurePosixPath("audio")
        identity = owner._directories[relative]
        owner._directory_handles[relative] = 101

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                ResourceSnapshotOwner,
                "_validate_directory",
                return_value=None,
            ),
            patch.object(snapshot_module, "_windows_close_handle") as close_handle,
            patch.object(
                snapshot_module,
                "_windows_lock_directory",
                return_value=202,
            ),
            patch.object(
                snapshot_module,
                "_claim_entry",
                side_effect=ResourceSnapshotError("injected Windows claim failure"),
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "Windows claim failure"),
        ):
            owner._remove_owned_directory(relative, identity)

        close_handle.assert_called_once_with(101)
        self.assertEqual(202, owner._directory_handles[relative])
        self.assertIn(relative, owner._directories)
        owner._directory_handles.pop(relative)
        loaded.close()

    @unittest.skipUnless(os.name == "posix", "mocked Windows root-claim semantics")
    def test_windows_root_reopen_failure_rolls_back_before_claim_commit(self) -> None:
        owner = ResourceSnapshotOwner()
        original_name = owner._active_root_name
        reopen_calls = 0

        def fail_then_reopen() -> None:
            nonlocal reopen_calls
            reopen_calls += 1
            if reopen_calls == 1:
                raise ResourceSnapshotError("injected root handle reopen failure")
            owner._directory_handles = {
                relative: 100 + index for index, relative in enumerate(owner._directories)
            }

        def reopen_all() -> None:
            owner._directory_handles = {
                relative: 200 + index for index, relative in enumerate(owner._directories)
            }

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                ResourceSnapshotOwner,
                "_release_windows_directory_handles",
                return_value=None,
            ),
            patch.object(
                ResourceSnapshotOwner,
                "_reopen_windows_directory_handles",
                side_effect=fail_then_reopen,
            ) as reopen,
            self.assertRaisesRegex(ResourceSnapshotError, "root handle reopen failure"),
        ):
            owner._claim_root()

        self.assertEqual(2, reopen.call_count)
        self.assertEqual(original_name, owner._active_root_name)
        self.assertFalse(owner._root_claimed)
        self.assertTrue(owner._active_root.is_dir())

        with (
            patch.object(snapshot_module.os, "name", "nt"),
            patch.object(
                ResourceSnapshotOwner,
                "_release_windows_directory_handles",
                return_value=None,
            ),
            patch.object(
                ResourceSnapshotOwner,
                "_reopen_windows_directory_handles",
                side_effect=reopen_all,
            ),
            patch.object(ResourceSnapshotOwner, "_scan_inventory", return_value=None),
        ):
            owner._claim_root()

        self.assertTrue(owner._root_claimed)
        owner.close()

    def test_large_snapshot_has_bounded_python_allocation(self) -> None:
        resource = self.root / "large.wav"
        _write_large_wav(resource)
        digest = _sha256_file(resource)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    digest,
                )
            ],
        )

        original_capture = media_module._capture_descriptor

        def reset_peak_then_capture(*args: object, **kwargs: object) -> object:
            tracemalloc.reset_peak()
            return original_capture(*args, **kwargs)

        gc.collect()
        tracemalloc.start()
        try:
            with patch(
                "isoworld.content.media._capture_descriptor",
                side_effect=reset_peak_then_capture,
            ):
                loaded = load_renderpack(renderpack, self.pack)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * 1024 * 1024)
        self.assertEqual(digest, _sha256_file(loaded.resolve_file(loaded.assets[0].files[0])))
        loaded.close()

    def test_failure_and_success_lifecycles_leave_no_snapshot_entries(self) -> None:
        snapshot_parent = self.root / "snapshots"
        snapshot_parent.mkdir()
        valid = _wav()

        def renderpack_for(name: str, payload: bytes, digest: str) -> Path:
            resource = self.root / name
            resource.write_bytes(payload)
            return _renderpack(
                self.root,
                self.pack,
                [
                    _asset(
                        "neutral_sfx",
                        "sfx",
                        "audio",
                        name,
                        "audio/wav",
                        digest,
                    )
                ],
            )

        with patch.object(tempfile, "tempdir", str(snapshot_parent)):
            mismatch = renderpack_for("mismatch.wav", valid, "0" * 64)
            with self.assertRaisesRegex(RenderPackError, "sha256 does not match"):
                load_renderpack(mismatch, self.pack)
            self.assertEqual([], list(snapshot_parent.iterdir()))

            invalid = b"not a wav"
            bad_signature = renderpack_for(
                "invalid.wav",
                invalid,
                hashlib.sha256(invalid).hexdigest(),
            )
            with self.assertRaisesRegex(RenderPackError, "declared media type"):
                load_renderpack(bad_signature, self.pack)
            self.assertEqual([], list(snapshot_parent.iterdir()))

            partial = renderpack_for(
                "partial.wav",
                valid,
                hashlib.sha256(valid).hexdigest(),
            )

            def fail_partial_write(descriptor: int, payload: bytes) -> None:
                os.write(descriptor, payload[:8])
                raise OSError("injected partial snapshot write")

            with (
                patch(
                    "isoworld.content.media._write_descriptor",
                    side_effect=fail_partial_write,
                ),
                self.assertRaisesRegex(RenderPackError, "partial snapshot write"),
            ):
                load_renderpack(partial, self.pack)
            self.assertEqual([], list(snapshot_parent.iterdir()))

            success = renderpack_for(
                "success.wav",
                valid,
                hashlib.sha256(valid).hexdigest(),
            )
            loaded = load_renderpack(success, self.pack)
            self.assertEqual(1, len(list(snapshot_parent.iterdir())))
            loaded.close()
            loaded.close()
            self.assertEqual([], list(snapshot_parent.iterdir()))

            large = self.root / "signature.wav"
            _write_large_wav(large)
            self.assertTrue(media_signature_matches(large, "audio/wav"))
            self.assertEqual([], list(snapshot_parent.iterdir()))

    def test_root_identity_replacement_is_not_deleted(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack, self.pack)
        original_root = loaded.root
        moved_root = original_root.with_name(f"{original_root.name}-moved")
        replacement_root = original_root
        original_root.rename(moved_root)
        replacement_root.mkdir()

        with self.assertRaisesRegex(RenderPackError, "identity changed"):
            loaded.close()
        self.assertTrue(replacement_root.exists())
        replacement_root.rmdir()
        moved_root.rename(original_root)
        loaded.close()
        self.assertFalse(original_root.exists())

    def test_owned_renderpack_copy_context_and_direct_construction_semantics(self) -> None:
        resource = self.root / "tone.wav"
        payload = _wav()
        resource.write_bytes(payload)
        renderpack_path = _renderpack(
            self.root,
            self.pack,
            [
                _asset(
                    "neutral_sfx",
                    "sfx",
                    "audio",
                    resource.name,
                    "audio/wav",
                    hashlib.sha256(payload).hexdigest(),
                )
            ],
        )
        loaded = load_renderpack(renderpack_path, self.pack)
        self.assertIs(loaded, copy.copy(loaded))
        self.assertIs(loaded, copy.deepcopy(loaded))
        owned_root = loaded.root
        with loaded as entered:
            self.assertIs(loaded, entered)
            self.assertTrue(entered.resolve_file(entered.assets[0].files[0]).exists())
        self.assertFalse(owned_root.exists())

        direct = RenderPack("world", "0" * 64, "1" * 64, self.root, (), ())
        self.assertIs(direct, copy.copy(direct))
        self.assertIs(direct, copy.deepcopy(direct))
        direct.close()

        finalized = load_renderpack(renderpack_path, self.pack)
        finalized_root = finalized.root
        del finalized
        gc.collect()
        self.assertFalse(finalized_root.exists())


if __name__ == "__main__":
    unittest.main()
