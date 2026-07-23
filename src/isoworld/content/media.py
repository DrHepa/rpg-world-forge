from __future__ import annotations

import binascii
import hashlib
import mmap
import os
import re
import stat
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    path_file_stat,
)
from isoworld.runtime_io import RuntimeIOError, decode_json_object

MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_FONT_BYTES = 64 * 1024 * 1024
MAX_GLSL_BYTES = 1024 * 1024
MAX_JSON_MEDIA_BYTES = 16 * 1024 * 1024
MAX_DECODED_IMAGE_BYTES = 512 * 1024 * 1024
MAX_IMAGE_PIXELS = 268_435_456
MAX_RETAINED_MEDIA_BYTES = 4 * 1024 * 1024
MAX_OGG_IDENTIFICATION_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_DIR_FD_READ = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.open, os.stat)
)
_GLSL_MAIN = re.compile(r"\bvoid\s+main\s*\(")


class MediaValidationError(ValueError):
    """Raised when a direct runtime resource is unsafe or structurally malformed."""


@dataclass(frozen=True, slots=True)
class ValidatedMedia:
    path: Path
    payload: bytes | None
    sha256: str


@dataclass(frozen=True, slots=True)
class _PathSnapshot:
    root: Path
    target: Path
    directories: tuple[tuple[Path, tuple[int, int]], ...]
    target_state: tuple[int, int, int, int, int, int, int]


def _identity(info: FileStat) -> tuple[int, int]:
    return file_identity(info)


def _file_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
    )


def _non_following_stat(path: Path) -> FileStat:
    return path_file_stat(path)


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _lexical_absolute_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _directory_snapshot(root: Path) -> tuple[tuple[Path, tuple[int, int]], ...]:
    current = Path(root.anchor)
    directories: list[tuple[Path, tuple[int, int]]] = []
    offset = 0
    if root.anchor:
        try:
            info = _non_following_stat(current)
        except OSError as exc:
            raise MediaValidationError(
                f"Resource parent is missing or unreadable: {current}: {exc}"
            ) from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise MediaValidationError(f"Resource parent is not a safe directory: {current}")
        directories.append((current, _identity(info)))
        offset = 1
    for part in root.parts[offset:]:
        if current == Path():
            current = Path(part)
        else:
            current /= part
        try:
            info = _non_following_stat(current)
        except OSError as exc:
            raise MediaValidationError(
                f"Resource parent is missing or unreadable: {current}: {exc}"
            ) from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise MediaValidationError(f"Resource parent is not a safe directory: {current}")
        directories.append((current, _identity(info)))
    if not directories:
        try:
            info = _non_following_stat(root)
        except OSError as exc:
            raise MediaValidationError(
                f"Resource parent is missing or unreadable: {root}: {exc}"
            ) from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise MediaValidationError(f"Resource parent is not a safe directory: {root}")
        directories.append((root, _identity(info)))
    return tuple(directories)


def _canonical_root_snapshot(root: Path) -> tuple[Path, tuple[tuple[Path, tuple[int, int]], ...]]:
    lexical_root = _lexical_absolute_path(root)
    lexical_directories = _directory_snapshot(lexical_root)
    try:
        canonical_root = lexical_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MediaValidationError(
            f"Resource parent is missing or unreadable: {lexical_root}: {exc}"
        ) from exc
    if canonical_root == lexical_root:
        return canonical_root, lexical_directories

    canonical_directories = _directory_snapshot(canonical_root)
    if canonical_directories[-1][1] != lexical_directories[-1][1]:
        raise MediaValidationError(f"Resource parent changed while resolving: {lexical_root}")
    return canonical_root, canonical_directories


def _path_snapshot(root: Path, relative: PurePosixPath, *, limit: int) -> _PathSnapshot:
    absolute_root, captured_directories = _canonical_root_snapshot(root)
    directories = list(captured_directories)

    current = absolute_root
    for part in relative.parts[:-1]:
        current /= part
        try:
            info = _non_following_stat(current)
        except OSError as exc:
            raise MediaValidationError(
                f"Resource parent is missing or unreadable: {current}: {exc}"
            ) from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise MediaValidationError(f"Resource parent is not a safe directory: {current}")
        directories.append((current, _identity(info)))

    target = absolute_root.joinpath(*relative.parts)
    try:
        info = _non_following_stat(target)
    except OSError as exc:
        raise MediaValidationError(
            f"Processed asset is missing or unreadable: {target}: {exc}"
        ) from exc
    if _is_link_or_reparse(info):
        raise MediaValidationError(
            f"Processed asset must not be a symbolic link or reparse point: {target}"
        )
    if not stat.S_ISREG(info.st_mode):
        raise MediaValidationError(f"Processed asset is not a regular file: {target}")
    if info.st_nlink != 1:
        raise MediaValidationError(f"Processed asset must not be hard-linked: {target}")
    if info.st_size > limit:
        raise MediaValidationError(f"Processed asset exceeds the {limit}-byte limit: {target}")
    return _PathSnapshot(absolute_root, target, tuple(directories), _file_state(info))


def _verify_snapshot(snapshot: _PathSnapshot, relative: PurePosixPath, *, limit: int) -> None:
    current = _path_snapshot(snapshot.root, relative, limit=limit)
    if current.directories != snapshot.directories or current.target_state != snapshot.target_state:
        raise MediaValidationError(f"Resource identity changed while reading: {snapshot.target}")


def _open_resource(snapshot: _PathSnapshot, relative: PurePosixPath) -> tuple[int, list[int]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if not _DIR_FD_READ:
        try:
            return os.open(snapshot.target, flags), []
        except OSError as exc:
            raise MediaValidationError(f"Could not open resource {snapshot.target}: {exc}") from exc

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(snapshot.root, directory_flags)
        descriptors.append(root_descriptor)
        expected_root = next(
            identity for path, identity in snapshot.directories if path == snapshot.root
        )
        if _identity(os.fstat(root_descriptor)) != expected_root:
            raise MediaValidationError(f"Resource root changed while opening: {snapshot.root}")
        parent_descriptor = root_descriptor
        current = snapshot.root
        identities = dict(snapshot.directories)
        for part in relative.parts[:-1]:
            current /= part
            child = os.open(part, directory_flags, dir_fd=parent_descriptor)
            descriptors.append(child)
            if _identity(os.fstat(child)) != identities[current]:
                raise MediaValidationError(f"Resource parent changed while opening: {current}")
            parent_descriptor = child
        descriptor = os.open(relative.name, flags, dir_fd=parent_descriptor)
        return descriptor, descriptors
    except MediaValidationError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    except OSError as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise MediaValidationError(f"Could not open resource {snapshot.target}: {exc}") from exc


def _capture_descriptor(
    descriptor: int,
    *,
    expected_size: int,
    limit: int,
    retain_limit: int,
) -> tuple[BinaryIO, bytes | None, str, int]:
    capture = tempfile.SpooledTemporaryFile(max_size=retain_limit, mode="w+b")
    if expected_size > retain_limit:
        capture.rollover()
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise MediaValidationError(f"Processed asset exceeds the {limit}-byte limit")
            digest.update(chunk)
            capture.write(chunk)
        capture.seek(0)
        retained = capture.read() if total <= retain_limit else None
        capture.seek(0)
        return capture, retained, digest.hexdigest(), total
    except Exception:
        capture.close()
        raise


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    position = 0
    while position < len(payload):
        written = os.write(descriptor, payload[position:])
        if written <= 0:
            raise OSError("Could not make progress while materializing resource bytes")
        position += written


def _copy_capture_to_descriptor(
    capture: BinaryIO,
    destination: int,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Stream one validated capture into an already exclusive regular file."""

    before = descriptor_file_stat(destination)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != 0:
        raise MediaValidationError("Snapshot destination is not a new private regular file")
    destination_identity = _identity(before)
    copied_hash = hashlib.sha256()
    copied_size = 0
    capture.seek(0)
    while True:
        chunk = capture.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        copied_size += len(chunk)
        copied_hash.update(chunk)
        _write_descriptor(destination, chunk)
    os.fsync(destination)
    after = descriptor_file_stat(destination)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or _identity(after) != destination_identity
        or after.st_size != copied_size
    ):
        raise MediaValidationError("Snapshot destination identity changed while writing")
    if copied_size != expected_size or copied_hash.hexdigest() != expected_sha256:
        raise MediaValidationError("Snapshot bytes differ from the validated resource capture")


def _retained_limit(media_type: str, effective_limit: int) -> int:
    if media_type in {"application/json", "text/x-glsl"}:
        return effective_limit
    return min(effective_limit, MAX_RETAINED_MEDIA_BYTES)


def _media_limit(media_type: str, requested: int) -> int:
    if media_type == "text/x-glsl":
        return min(requested, MAX_GLSL_BYTES)
    if media_type in {"font/ttf", "font/otf"}:
        return min(requested, MAX_FONT_BYTES)
    if media_type == "application/json":
        return min(requested, MAX_JSON_MEDIA_BYTES)
    return min(requested, MAX_MEDIA_BYTES)


def _read_resource_snapshot(
    root: str | Path,
    relative: PurePosixPath,
    *,
    media_type: str | None,
    limit: int,
    materialize_descriptor: int | None = None,
) -> ValidatedMedia:
    effective_limit = (
        min(limit, MAX_MEDIA_BYTES) if media_type is None else _media_limit(media_type, limit)
    )
    snapshot = _path_snapshot(Path(root), relative, limit=effective_limit)
    descriptor: int | None = None
    directories: list[int] = []
    capture: BinaryIO | None = None
    try:
        descriptor, directories = _open_resource(snapshot, relative)
        before = descriptor_file_stat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or _file_state(before) != snapshot.target_state
        ):
            raise MediaValidationError(
                f"Resource identity changed while opening: {snapshot.target}"
            )
        capture, payload, digest, total = _capture_descriptor(
            descriptor,
            expected_size=before.st_size,
            limit=effective_limit,
            retain_limit=(
                min(effective_limit, MAX_RETAINED_MEDIA_BYTES)
                if media_type is None
                else _retained_limit(media_type, effective_limit)
            ),
        )
        if media_type is not None:
            if payload is not None:
                _validate_media_payload(payload, media_type, snapshot.target)
            else:
                capture.flush()
                with mmap.mmap(capture.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                    _validate_media_payload(mapped, media_type, snapshot.target)
        after = descriptor_file_stat(descriptor)
        if _file_state(after) != _file_state(before):
            raise MediaValidationError(
                f"Resource identity changed while reading: {snapshot.target}"
            )
        _verify_snapshot(snapshot, relative, limit=effective_limit)
        if materialize_descriptor is not None:
            _copy_capture_to_descriptor(
                capture,
                materialize_descriptor,
                expected_sha256=digest,
                expected_size=total,
            )
        return ValidatedMedia(snapshot.target, payload, digest)
    except MediaValidationError:
        raise
    except OSError as exc:
        raise MediaValidationError(f"Could not read resource {snapshot.target}: {exc}") from exc
    finally:
        if capture is not None:
            capture.close()
        if descriptor is not None:
            os.close(descriptor)
        for directory in reversed(directories):
            os.close(directory)


def read_resource_snapshot(
    root: str | Path,
    relative: PurePosixPath,
    *,
    limit: int = MAX_MEDIA_BYTES,
    materialize_descriptor: int | None = None,
) -> ValidatedMedia:
    """Hash one stable resource read and optionally materialize that exact capture."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MEDIA_BYTES:
        raise ValueError("limit must be a positive resource bound")
    return _read_resource_snapshot(
        root,
        relative,
        media_type=None,
        limit=limit,
        materialize_descriptor=materialize_descriptor,
    )


def read_validated_resource(
    root: str | Path,
    relative: PurePosixPath,
    media_type: str,
    *,
    limit: int = MAX_MEDIA_BYTES,
    materialize_descriptor: int | None = None,
) -> ValidatedMedia:
    """Validate one stable read and optionally materialize that exact capture."""

    return _read_resource_snapshot(
        root,
        relative,
        media_type=media_type,
        limit=limit,
        materialize_descriptor=materialize_descriptor,
    )


def read_validated_media(
    path: str | Path,
    media_type: str,
    *,
    limit: int = MAX_MEDIA_BYTES,
) -> ValidatedMedia:
    source = _lexical_absolute_path(Path(path))
    return read_validated_resource(
        source.parent, PurePosixPath(source.name), media_type, limit=limit
    )


def _png_scanline_layout(
    width: int,
    height: int,
    bits_per_pixel: int,
    interlace: int,
) -> tuple[tuple[int, int], ...]:
    if interlace == 0:
        return (((width * bits_per_pixel + 7) // 8, height),)
    passes = (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    )
    layout: list[tuple[int, int]] = []
    for start_x, start_y, step_x, step_y in passes:
        pass_width = 0 if width <= start_x else (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else (height - start_y + step_y - 1) // step_y
        if pass_width and pass_height:
            layout.append(((pass_width * bits_per_pixel + 7) // 8, pass_height))
    return tuple(layout)


def _png_crc(payload: bytes | mmap.mmap, kind: bytes, start: int, end: int) -> int:
    checksum = binascii.crc32(kind)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + _READ_CHUNK_BYTES, end)
        checksum = binascii.crc32(payload[cursor:chunk_end], checksum)
        cursor = chunk_end
    return checksum & 0xFFFFFFFF


def _validate_png(payload: bytes | mmap.mmap) -> None:
    if len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise MediaValidationError("PNG has an invalid or truncated signature")
    position = 8
    ihdr: tuple[int, int, int, int, int] | None = None
    decompressor: zlib.Decompress | None = None
    scan_layout: tuple[tuple[int, int], ...] = ()
    layout_index = 0
    rows_remaining = 0
    row_remaining = 0
    expected = 0
    decoded_total = 0
    seen_idat = False
    idat_ended = False
    seen_plte = False
    seen_iend = False

    def advance_layout() -> None:
        nonlocal layout_index, rows_remaining
        while layout_index < len(scan_layout) and rows_remaining == 0:
            layout_index += 1
            if layout_index < len(scan_layout):
                rows_remaining = scan_layout[layout_index][1]

    def consume_scanlines(output: bytes) -> None:
        nonlocal decoded_total, row_remaining, rows_remaining
        cursor = 0
        decoded_total += len(output)
        if decoded_total > expected:
            raise MediaValidationError("PNG decompressed data exceeds declared dimensions")
        while cursor < len(output):
            advance_layout()
            if layout_index >= len(scan_layout):
                raise MediaValidationError("PNG decompressed data exceeds declared dimensions")
            if row_remaining == 0:
                if output[cursor] > 4:
                    raise MediaValidationError("PNG scanline uses an invalid filter type")
                cursor += 1
                row_remaining = scan_layout[layout_index][0]
            consumed = min(row_remaining, len(output) - cursor)
            cursor += consumed
            row_remaining -= consumed
            if row_remaining == 0:
                rows_remaining -= 1
        advance_layout()

    def feed_idat(start: int, end: int) -> None:
        nonlocal decoded_total
        assert decompressor is not None
        cursor = start
        try:
            while cursor < end:
                chunk_end = min(cursor + _READ_CHUNK_BYTES, end)
                pending = payload[cursor:chunk_end]
                cursor = chunk_end
                while pending:
                    remaining = expected + 1 - decoded_total
                    if remaining <= 0:
                        raise MediaValidationError(
                            "PNG decompressed data exceeds declared dimensions"
                        )
                    output = decompressor.decompress(pending, min(65536, remaining))
                    consume_scanlines(output)
                    if decompressor.unused_data:
                        raise MediaValidationError("PNG contains data after its zlib stream")
                    unconsumed = decompressor.unconsumed_tail
                    if unconsumed and len(unconsumed) == len(pending) and not output:
                        raise MediaValidationError("PNG zlib stream made no progress")
                    pending = unconsumed
        except zlib.error as exc:
            raise MediaValidationError(f"PNG has an invalid zlib stream: {exc}") from exc

    while position < len(payload):
        if len(payload) - position < 12:
            raise MediaValidationError("PNG has a truncated chunk header")
        length = struct.unpack_from(">I", payload, position)[0]
        kind = payload[position + 4 : position + 8]
        data_start = position + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(payload):
            raise MediaValidationError("PNG has a truncated chunk payload")
        if not all(65 <= value <= 90 or 97 <= value <= 122 for value in kind):
            raise MediaValidationError("PNG has an invalid chunk type")
        expected_crc = struct.unpack_from(">I", payload, data_end)[0]
        if _png_crc(payload, kind, data_start, data_end) != expected_crc:
            raise MediaValidationError("PNG chunk CRC does not match")
        if position == 8 and kind != b"IHDR":
            raise MediaValidationError("PNG IHDR must be first")
        if kind != b"IDAT" and seen_idat:
            idat_ended = True
        if kind == b"IHDR":
            if ihdr is not None or length != 13:
                raise MediaValidationError("PNG has an invalid IHDR")
            data = payload[data_start:data_end]
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                width == 0
                or height == 0
                or width * height > MAX_IMAGE_PIXELS
                or color_type not in valid_depths
                or bit_depth not in valid_depths[color_type]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise MediaValidationError("PNG has invalid IHDR fields")
            ihdr = width, height, bit_depth, color_type, interlace
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            scan_layout = _png_scanline_layout(width, height, bit_depth * channels, interlace)
            expected = sum((row_bytes + 1) * rows for row_bytes, rows in scan_layout)
            if expected > MAX_DECODED_IMAGE_BYTES:
                raise MediaValidationError(
                    "PNG decoded image exceeds the structural validation limit"
                )
            rows_remaining = scan_layout[0][1]
            decompressor = zlib.decompressobj()
        elif kind == b"PLTE":
            if ihdr is None or seen_plte or seen_idat or length == 0 or length > 768 or length % 3:
                raise MediaValidationError("PNG has an invalid PLTE chunk")
            _, _, bit_depth, color_type, _ = ihdr
            if color_type in {0, 4} or (color_type == 3 and length // 3 > 1 << bit_depth):
                raise MediaValidationError("PNG palette is incompatible with IHDR")
            seen_plte = True
        elif kind == b"IDAT":
            if ihdr is None or seen_iend or idat_ended:
                raise MediaValidationError("PNG has an out-of-order IDAT chunk")
            seen_idat = True
            feed_idat(data_start, data_end)
        elif kind == b"IEND":
            if length != 0 or seen_iend:
                raise MediaValidationError("PNG has an invalid IEND")
            seen_iend = True
            if chunk_end != len(payload):
                raise MediaValidationError("PNG contains bytes after IEND")
        position = chunk_end
    if ihdr is None or not seen_idat or not seen_iend or decompressor is None:
        raise MediaValidationError("PNG is missing required IHDR, IDAT, or IEND chunks")
    if ihdr[3] == 3 and not seen_plte:
        raise MediaValidationError("Indexed PNG is missing its required PLTE chunk")
    advance_layout()
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decoded_total != expected
        or layout_index != len(scan_layout)
        or row_remaining != 0
    ):
        raise MediaValidationError("PNG IDAT stream does not match declared dimensions")


def _validate_jpeg(payload: bytes) -> None:
    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        raise MediaValidationError("JPEG has an invalid or truncated SOI")
    position = 2
    saw_frame = False
    saw_scan = False
    while position < len(payload):
        if payload[position] != 0xFF:
            raise MediaValidationError("JPEG contains data outside a scan")
        marker_start = position
        while position < len(payload) and payload[position] == 0xFF:
            position += 1
        if position >= len(payload):
            raise MediaValidationError("JPEG ends in a truncated marker")
        marker = payload[position]
        position += 1
        if marker == 0xD9:
            if position != len(payload) or not saw_frame or not saw_scan:
                raise MediaValidationError("JPEG has an invalid EOI or missing frame data")
            return
        if marker in {0x00, 0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            raise MediaValidationError("JPEG contains an invalid standalone marker")
        if position + 2 > len(payload):
            raise MediaValidationError("JPEG has a truncated segment length")
        length = struct.unpack_from(">H", payload, position)[0]
        if length < 2 or position + length > len(payload):
            raise MediaValidationError("JPEG has a truncated segment")
        segment = payload[position + 2 : position + length]
        position += length
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if len(segment) < 6:
                raise MediaValidationError("JPEG has a truncated frame header")
            height, width, components = struct.unpack_from(">HHB", segment, 1)
            if (
                width == 0
                or height == 0
                or width * height > MAX_IMAGE_PIXELS
                or components == 0
                or len(segment) != 6 + components * 3
            ):
                raise MediaValidationError("JPEG has invalid frame dimensions")
            saw_frame = True
        if marker == 0xDA:
            if not segment or segment[0] == 0 or len(segment) != 4 + segment[0] * 2:
                raise MediaValidationError("JPEG has an invalid scan header")
            saw_scan = True
            scan_start = position
            while position < len(payload):
                if payload[position] != 0xFF:
                    position += 1
                    continue
                scan_marker = position
                while position < len(payload) and payload[position] == 0xFF:
                    position += 1
                if position >= len(payload):
                    raise MediaValidationError("JPEG scan is truncated")
                escaped = payload[position]
                if escaped == 0x00 or 0xD0 <= escaped <= 0xD7:
                    position += 1
                    continue
                position = scan_marker
                break
            if position == scan_start:
                raise MediaValidationError("JPEG scan has no entropy-coded data")
        if position <= marker_start:
            raise MediaValidationError("JPEG parser made no progress")
    raise MediaValidationError("JPEG is missing EOI")


def _webp_u24(payload: bytes | mmap.mmap, start: int) -> int:
    return int.from_bytes(payload[start : start + 3], "little")


def _validate_webp_bitstream(
    kind: bytes,
    payload: bytes | mmap.mmap,
    start: int,
    end: int,
) -> tuple[int, int]:
    length = end - start
    if kind == b"VP8 ":
        if length < 10 or payload[start + 3 : start + 6] != b"\x9d\x01\x2a":
            raise MediaValidationError("WebP VP8 frame header is invalid")
        width = struct.unpack_from("<H", payload, start + 6)[0] & 0x3FFF
        height = struct.unpack_from("<H", payload, start + 8)[0] & 0x3FFF
    elif kind == b"VP8L":
        if length < 5 or payload[start] != 0x2F:
            raise MediaValidationError("WebP VP8L frame header is invalid")
        bits = int.from_bytes(payload[start + 1 : start + 5], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    else:
        raise MediaValidationError("WebP frame has no supported bitstream")
    if width == 0 or height == 0 or width * height > MAX_IMAGE_PIXELS:
        raise MediaValidationError("WebP frame dimensions are invalid")
    return width, height


def _validate_webp_animation_frame(
    payload: bytes | mmap.mmap,
    start: int,
    end: int,
    canvas: tuple[int, int],
) -> None:
    if end - start < 24:
        raise MediaValidationError("WebP ANMF frame is truncated")
    frame_x = _webp_u24(payload, start) * 2
    frame_y = _webp_u24(payload, start + 3) * 2
    frame_width = _webp_u24(payload, start + 6) + 1
    frame_height = _webp_u24(payload, start + 9) + 1
    if payload[start + 15] & 0xFC:
        raise MediaValidationError("WebP ANMF frame has reserved flags")
    if (
        frame_width * frame_height > MAX_IMAGE_PIXELS
        or frame_x + frame_width > canvas[0]
        or frame_y + frame_height > canvas[1]
    ):
        raise MediaValidationError("WebP ANMF frame exceeds its canvas")

    position = start + 16
    bitstreams = 0
    alpha_seen = False
    while position < end:
        if end - position < 8:
            raise MediaValidationError("WebP ANMF has a truncated subchunk header")
        kind = payload[position : position + 4]
        length = struct.unpack_from("<I", payload, position + 4)[0]
        data_start = position + 8
        data_end = data_start + length
        padded_end = data_end + (length & 1)
        if padded_end > end:
            raise MediaValidationError("WebP ANMF has a truncated subchunk")
        if kind == b"ALPH":
            if alpha_seen or bitstreams or length == 0:
                raise MediaValidationError("WebP ANMF has an invalid alpha subchunk")
            alpha_seen = True
        elif kind in {b"VP8 ", b"VP8L"}:
            if bitstreams:
                raise MediaValidationError("WebP ANMF contains multiple frame bitstreams")
            dimensions = _validate_webp_bitstream(kind, payload, data_start, data_end)
            if dimensions != (frame_width, frame_height):
                raise MediaValidationError("WebP ANMF dimensions do not match its frame header")
            bitstreams += 1
        else:
            raise MediaValidationError("WebP ANMF contains an unsupported subchunk")
        position = padded_end
    if position != end or bitstreams != 1:
        raise MediaValidationError("WebP ANMF must contain exactly one frame bitstream")


def _validate_webp(payload: bytes | mmap.mmap) -> None:
    if len(payload) < 20 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        raise MediaValidationError("WebP has an invalid or truncated RIFF header")
    if struct.unpack_from("<I", payload, 4)[0] != len(payload) - 8:
        raise MediaValidationError("WebP RIFF size does not match the file")
    position = 12
    top_level_frames = 0
    animated_frames = 0
    animation_headers = 0
    extended_flags: int | None = None
    canvas: tuple[int, int] | None = None
    while position < len(payload):
        if len(payload) - position < 8:
            raise MediaValidationError("WebP has a truncated chunk header")
        kind = payload[position : position + 4]
        length = struct.unpack_from("<I", payload, position + 4)[0]
        start = position + 8
        end = start + length
        padded_end = end + (length & 1)
        if padded_end > len(payload):
            raise MediaValidationError("WebP has a truncated chunk")
        if kind in {b"VP8 ", b"VP8L"}:
            dimensions = _validate_webp_bitstream(kind, payload, start, end)
            if canvas is not None and dimensions != canvas:
                raise MediaValidationError("WebP image dimensions do not match its canvas")
            top_level_frames += 1
        elif kind == b"VP8X":
            if length != 10 or extended_flags is not None or position != 12:
                raise MediaValidationError("WebP VP8X header is invalid")
            extended_flags = payload[start]
            if extended_flags & 0xC1:
                raise MediaValidationError("WebP VP8X header has reserved flags")
            canvas = (_webp_u24(payload, start + 4) + 1, _webp_u24(payload, start + 7) + 1)
            if canvas[0] * canvas[1] > MAX_IMAGE_PIXELS:
                raise MediaValidationError("WebP VP8X dimensions are invalid")
        elif kind == b"ANIM":
            if length != 6 or animation_headers:
                raise MediaValidationError("WebP ANIM header is invalid")
            animation_headers += 1
        elif kind == b"ANMF":
            if canvas is None:
                raise MediaValidationError("WebP ANMF requires a VP8X canvas")
            _validate_webp_animation_frame(payload, start, end, canvas)
            animated_frames += 1
        position = padded_end
    if position != len(payload):
        raise MediaValidationError("WebP chunk layout is incomplete")
    animated = extended_flags is not None and bool(extended_flags & 0x02)
    if animated:
        if animation_headers != 1 or animated_frames == 0 or top_level_frames:
            raise MediaValidationError("Animated WebP has an incomplete frame structure")
    elif animation_headers or animated_frames or top_level_frames != 1:
        raise MediaValidationError("Static WebP must contain exactly one primary image frame")


def _validate_wav(payload: bytes | mmap.mmap) -> None:
    if len(payload) < 44 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        raise MediaValidationError("WAV has an invalid or truncated RIFF header")
    if struct.unpack_from("<I", payload, 4)[0] != len(payload) - 8:
        raise MediaValidationError("WAV RIFF size does not match the file")
    position = 12
    format_seen = False
    data_seen = False
    while position < len(payload):
        if len(payload) - position < 8:
            raise MediaValidationError("WAV has a truncated chunk header")
        kind = payload[position : position + 4]
        length = struct.unpack_from("<I", payload, position + 4)[0]
        start = position + 8
        end = start + length
        padded_end = end + (length & 1)
        if padded_end > len(payload):
            raise MediaValidationError("WAV has a truncated chunk")
        if kind == b"fmt ":
            if format_seen or length < 16:
                raise MediaValidationError("WAV has an invalid fmt chunk")
            audio_format, channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from(
                "<HHIIHH", payload, start
            )
            if (
                audio_format not in {1, 3, 0xFFFE}
                or channels == 0
                or sample_rate == 0
                or byte_rate == 0
                or block_align == 0
                or bits == 0
            ):
                raise MediaValidationError("WAV fmt values are invalid")
            format_seen = True
        elif kind == b"data":
            if length == 0:
                raise MediaValidationError("WAV data chunk is empty")
            data_seen = True
        position = padded_end
    if position != len(payload) or not format_seen or not data_seen:
        raise MediaValidationError("WAV is missing complete fmt or data chunks")


def _validate_ogg(payload: bytes | mmap.mmap) -> None:
    position = 0
    pages = 0
    first_packet = bytearray()
    first_packet_complete = False
    packet_count = 0
    last_header_type = 0
    serial: int | None = None
    expected_sequence = 0
    continued_packet = False
    while position < len(payload):
        if len(payload) - position < 27 or payload[position : position + 4] != b"OggS":
            raise MediaValidationError("Ogg has an invalid or truncated page header")
        page_segments = payload[position + 26]
        header_end = position + 27 + page_segments
        if header_end > len(payload):
            raise MediaValidationError("Ogg has a truncated lacing table")
        lacing = payload[position + 27 : header_end]
        page_end = header_end + sum(lacing)
        if page_end > len(payload):
            raise MediaValidationError("Ogg has a truncated page payload")
        if payload[position + 4] != 0:
            raise MediaValidationError("Ogg uses an unsupported stream version")
        header_type = payload[position + 5]
        if header_type & ~0x07:
            raise MediaValidationError("Ogg page uses reserved header flags")
        if bool(header_type & 0x01) != continued_packet:
            raise MediaValidationError("Ogg packet continuation is inconsistent")
        page_serial = struct.unpack_from("<I", payload, position + 14)[0]
        sequence = struct.unpack_from("<I", payload, position + 18)[0]
        last_header_type = header_type
        if pages == 0:
            if not header_type & 0x02 or sequence != 0:
                raise MediaValidationError("Ogg first page must begin a logical stream")
            serial = page_serial
        elif page_serial != serial or sequence != expected_sequence or header_type & 0x02:
            raise MediaValidationError("Ogg page sequence is not contiguous")
        expected_sequence = sequence + 1
        cursor = header_end
        for length in lacing:
            if not first_packet_complete:
                if len(first_packet) + length > MAX_OGG_IDENTIFICATION_BYTES:
                    raise MediaValidationError("Ogg identification packet exceeds its limit")
                first_packet.extend(payload[cursor : cursor + length])
            cursor += length
            if length < 255:
                packet_count += 1
                first_packet_complete = True
        continued_packet = bool(lacing and lacing[-1] == 255)
        pages += 1
        position = page_end
    if (
        pages == 0
        or not first_packet_complete
        or packet_count < 2
        or continued_packet
        or not last_header_type & 0x04
    ):
        raise MediaValidationError("Ogg has no complete identification/data stream")
    if not (
        first_packet.startswith(b"\x01vorbis")
        or first_packet.startswith(b"OpusHead")
        or first_packet.startswith(b"\x7fFLAC")
    ):
        raise MediaValidationError("Ogg identification packet uses an unsupported codec")


def _mp3_frame_length(header: int) -> int:
    version_bits = (header >> 19) & 0x3
    layer_bits = (header >> 17) & 0x3
    bitrate_index = (header >> 12) & 0xF
    sample_index = (header >> 10) & 0x3
    padding = (header >> 9) & 0x1
    if (
        header >> 21 != 0x7FF
        or version_bits == 1
        or layer_bits == 0
        or bitrate_index in {0, 15}
        or sample_index == 3
    ):
        return 0
    mpeg1 = version_bits == 3
    if mpeg1:
        tables = {
            3: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
            2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
            1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        }
    else:
        tables = {
            3: (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
            2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
            1: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        }
    bitrate = tables[layer_bits][bitrate_index] * 1000
    sample_rate = (44100, 48000, 32000)[sample_index]
    if version_bits == 2:
        sample_rate //= 2
    elif version_bits == 0:
        sample_rate //= 4
    if layer_bits == 3:
        return (12 * bitrate // sample_rate + padding) * 4
    coefficient = 144 if mpeg1 or layer_bits == 2 else 72
    return coefficient * bitrate // sample_rate + padding


def _validate_mp3(payload: bytes | mmap.mmap) -> None:
    position = 0
    if payload[:3] == b"ID3":
        if (
            len(payload) < 10
            or payload[3] not in {2, 3, 4}
            or any(value & 0x80 for value in payload[6:10])
        ):
            raise MediaValidationError("MP3 has an invalid or truncated ID3 header")
        tag_size = sum(
            value << shift for value, shift in zip(payload[6:10], (21, 14, 7, 0), strict=True)
        )
        position = 10 + tag_size
        if position > len(payload):
            raise MediaValidationError("MP3 ID3 tag exceeds the file")
    frames = 0
    while position < len(payload):
        if len(payload) - position == 128 and payload[position : position + 3] == b"TAG":
            position = len(payload)
            break
        if len(payload) - position < 4:
            raise MediaValidationError("MP3 ends in a truncated frame")
        header = int.from_bytes(payload[position : position + 4], "big")
        length = _mp3_frame_length(header)
        if length < 4 or position + length > len(payload):
            raise MediaValidationError("MP3 has an invalid or truncated audio frame")
        position += length
        frames += 1
    if frames == 0 or position != len(payload):
        raise MediaValidationError("MP3 contains no complete audio frame")


def _range_has_nonzero(payload: bytes | mmap.mmap, start: int, end: int) -> bool:
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + _READ_CHUNK_BYTES, end)
        if any(payload[cursor:chunk_end]):
            return True
        cursor = chunk_end
    return False


def _validate_sfnt(payload: bytes | mmap.mmap, media_type: str) -> None:
    signatures = {b"\x00\x01\x00\x00", b"true", b"typ1"} if media_type == "font/ttf" else {b"OTTO"}
    if len(payload) < 12 or payload[:4] not in signatures:
        raise MediaValidationError("Font has an invalid or truncated sfnt header")
    table_count, search_range, entry_selector, range_shift = struct.unpack_from(">HHHH", payload, 4)
    if not 1 <= table_count <= 4096:
        raise MediaValidationError("Font has an invalid table count")
    greatest_power = 1 << (table_count.bit_length() - 1)
    if (
        search_range != greatest_power * 16
        or entry_selector != greatest_power.bit_length() - 1
        or range_shift != table_count * 16 - search_range
    ):
        raise MediaValidationError("Font has an invalid sfnt search header")
    directory_end = 12 + table_count * 16
    if directory_end > len(payload):
        raise MediaValidationError("Font has a truncated table directory")
    tags: set[bytes] = set()
    ranges: list[tuple[int, int]] = []
    for index in range(table_count):
        tag, _, offset, length = struct.unpack_from(">4sIII", payload, 12 + index * 16)
        if tag in tags or any(value < 0x20 or value > 0x7E for value in tag):
            raise MediaValidationError("Font has an invalid or duplicate table tag")
        tags.add(tag)
        if (
            offset % 4
            or offset < directory_end
            or offset > len(payload)
            or length > len(payload) - offset
        ):
            raise MediaValidationError("Font table lies outside the file")
        ranges.append((offset, offset + length))
    cursor = directory_end
    for start, end in sorted(ranges):
        if start < cursor:
            raise MediaValidationError("Font tables overlap")
        if _range_has_nonzero(payload, cursor, start):
            raise MediaValidationError("Font has non-zero bytes outside tables")
        cursor = end
    if _range_has_nonzero(payload, cursor, len(payload)):
        raise MediaValidationError("Font has non-zero trailing bytes")


def _glsl_code_tokens(text: str) -> str | None:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if character == "\n":
                line_comment = False
                output.append(character)
            else:
                output.append(" ")
        elif block_comment:
            output.append("\n" if character == "\n" else " ")
            if character == "*" and following == "/":
                output.append(" ")
                index += 1
                block_comment = False
        elif quote is not None:
            output.append(" ")
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character == "/" and following == "/":
            output.extend((" ", " "))
            index += 1
            line_comment = True
        elif character == "/" and following == "*":
            output.extend((" ", " "))
            index += 1
            block_comment = True
        elif character in {'"', "'"}:
            output.append(" ")
            quote = character
        else:
            output.append(character)
        index += 1
    if quote is not None or block_comment:
        return None
    return "".join(output)


def _balanced_glsl(text: str) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if character == "\n":
                line_comment = False
        elif block_comment:
            if character == "*" and following == "/":
                block_comment = False
                index += 1
        elif quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character == "/" and following == "/":
            line_comment = True
            index += 1
        elif character == "/" and following == "*":
            block_comment = True
            index += 1
        elif character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0 and quote is None and not block_comment


def _validate_glsl(payload: bytes) -> None:
    if not payload or len(payload) > MAX_GLSL_BYTES:
        raise MediaValidationError("GLSL is empty or exceeds its byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MediaValidationError(f"GLSL is not valid UTF-8: {exc}") from exc
    if text.startswith("\ufeff") or not text.strip():
        raise MediaValidationError("GLSL must be non-empty UTF-8 without a byte-order mark")
    if any(
        (ord(character) < 0x20 and character not in "\t\n\r") or 0x7F <= ord(character) <= 0x9F
        for character in text
    ):
        raise MediaValidationError("GLSL contains forbidden control characters")
    code = _glsl_code_tokens(text)
    if code is None or _GLSL_MAIN.search(code) is None or not _balanced_glsl(code):
        raise MediaValidationError("GLSL has no complete main function or balanced structure")


def _validate_media_payload(payload: bytes | mmap.mmap, media_type: str, source: Path) -> None:
    try:
        if media_type == "image/png":
            _validate_png(payload)
        elif media_type == "image/jpeg":
            _validate_jpeg(payload)
        elif media_type == "image/webp":
            _validate_webp(payload)
        elif media_type == "audio/wav":
            _validate_wav(payload)
        elif media_type == "audio/ogg":
            _validate_ogg(payload)
        elif media_type == "audio/mpeg":
            _validate_mp3(payload)
        elif media_type in {"font/ttf", "font/otf"}:
            _validate_sfnt(payload, media_type)
        elif media_type == "application/json":
            decode_json_object(payload, source=source)
        elif media_type == "text/x-glsl":
            _validate_glsl(payload)
        else:
            raise MediaValidationError(f"Unsupported runtime media type: {media_type}")
    except RuntimeIOError as exc:
        raise MediaValidationError(str(exc)) from exc


def media_signature_matches(path: Path, media_type: str) -> bool:
    """Validate one media file structurally from a stable, bounded read."""

    try:
        read_validated_media(path, media_type)
    except MediaValidationError:
        return False
    return True
