from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import re
import shutil
import stat
import struct
import sys
import tempfile
import wave
import zlib
from array import array
from pathlib import Path
from typing import Any

from worldforge.asset_formats.gltf import (
    DEFAULT_ALLOWED_EXTENSIONS,
    MAX_GLB_BYTES,
    GLBError,
    inspect_glb,
)
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    normalized_relative_path,
    read_json_object,
    require_content_hash,
    sha256_file,
    verify_artifact_reference,
    write_json_atomic,
)

RECIPE_FORMAT = "rpg-world-forge.asset_processing_recipe"
RECEIPT_FORMAT = "rpg-world-forge.asset_processing_receipt"
FORMAT_VERSION = 1
RECEIPT_NAME = "processing.receipt.json"

_OPERATIONS = frozenset({"atlas", "file_validate", "glb_validate", "png_canonical", "wav_pcm"})
_GLB_ROLES = frozenset({"animation", "collision", "model", "skeleton"})
_FILE_ROLE_MEDIA = frozenset(
    {
        ("font", "font/otf"),
        ("font", "font/ttf"),
        ("fragment_shader", "text/x-glsl"),
        ("vertex_shader", "text/x-glsl"),
    }
)
_FILE_SUFFIXES = {
    ("font", "font/otf"): frozenset({".otf"}),
    ("font", "font/ttf"): frozenset({".ttf"}),
    ("fragment_shader", "text/x-glsl"): frozenset({".frag", ".fs", ".glsl"}),
    ("vertex_shader", "text/x-glsl"): frozenset({".glsl", ".vert", ".vs"}),
}
_ID_PATTERN = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_URL_PATTERN = re.compile(r"(?:\b[a-z][a-z0-9+.-]{1,31}://|\bwww\.)", re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:access[_-]?token|api[_-]?key|authorization|bearer|"
    r"client[_-]?secret|credential|password|private[_-]?key|secret|signed[_-]?url|token)"
    r"(?![a-z0-9])|\bsk-[a-z0-9_-]{8,}|\b(?:ghp|github_pat)_[a-z0-9_]{12,}|"
    r"\bAKIA[A-Z0-9]{16}\b|-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----",
    re.IGNORECASE,
)
_PROVIDER_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:anthropic|blender(?:[-_]mcp)?|cohere|google[-_ ]?(?:genai|"
    r"generativeai|gemini)|groq|hugging[-_ ]?face|mcp|mistral(?:ai)?|modly(?:[-_]cli"
    r"[-_]mcp)?|ollama|openai|provider|vertexai)(?![a-z0-9])",
    re.IGNORECASE,
)
_INCLUDE_PATTERN = re.compile(r"^\s*#\s*include\b", re.IGNORECASE | re.MULTILINE)
_MAX_IMAGE_EDGE = 16384
_MAX_IMAGE_PIXELS = 64 * 1024 * 1024
_MAX_FONT_BYTES = 64 * 1024 * 1024
_MAX_GLSL_BYTES = 1024 * 1024
_MIN_SAMPLE_RATE = 8000
_MAX_SAMPLE_RATE = 192000
_MAX_PCM_DURATION_SECONDS = 600
_MAX_PCM_FRAMES = 32 * 1024 * 1024
_MAX_PCM_BYTES = 64 * 1024 * 1024
_MAX_RESAMPLE_RATIO = 24
_PCM_CHUNK_FRAMES = 65536


def _exact_keys(value: object, expected: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetContractError(f"{context} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise AssetContractError(f"{context} has invalid fields ({'; '.join(details)})")
    return value


def _integer(
    value: object,
    context: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssetContractError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise AssetContractError(f"{context} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise AssetContractError(f"{context} must be at most {maximum}")
    return value


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise AssetContractError(f"{context} is not a portable identifier")
    return value


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise AssetContractError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _safe_output_file(value: object, context: str) -> str:
    relative = normalized_relative_path(value)
    if relative is None:
        raise AssetContractError(f"{context} is an unsafe output path: {value!r}")
    result = relative.as_posix()
    if result == RECEIPT_NAME:
        raise AssetContractError(f"{context} uses the reserved receipt path")
    return result


def _require_output_extension(
    output_file: str,
    allowed: frozenset[str],
    context: str,
) -> None:
    if Path(output_file).suffix.casefold() not in allowed:
        expected = ", ".join(sorted(allowed))
        raise AssetContractError(f"{context} must use one of these extensions: {expected}")


def _artifact_shape(reference: object, context: str) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise AssetContractError(f"{context} must be an artifact reference")
    unknown = set(reference) - {"file", "sha256", "size"}
    missing = {"file", "sha256"} - set(reference)
    if unknown or missing:
        raise AssetContractError(f"{context} has invalid artifact-reference fields")
    if normalized_relative_path(reference.get("file")) is None:
        raise AssetContractError(f"{context}/file is unsafe")
    _digest(reference.get("sha256"), f"{context}/sha256")
    if "size" in reference:
        _integer(reference["size"], f"{context}/size", minimum=0)
    return reference


def _load_pillow() -> tuple[Any, Any, str]:
    try:
        import PIL
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise AssetContractError(
            "Pillow is required for png_canonical and atlas processing"
        ) from exc
    return Image, UnidentifiedImageError, str(PIL.__version__)


def _image_size(width: int, height: int, context: str) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise AssetContractError(f"{context} dimensions must be positive")
    if width > _MAX_IMAGE_EDGE or height > _MAX_IMAGE_EDGE:
        raise AssetContractError(f"{context} exceeds the {_MAX_IMAGE_EDGE}-pixel edge limit")
    if width * height > _MAX_IMAGE_PIXELS:
        raise AssetContractError(f"{context} exceeds the image-pixel limit")
    return width, height


def _rgba_without_metadata(image_module: Any, source: Any, context: str) -> Any:
    _image_size(source.width, source.height, context)
    converted = source.convert("RGBA")
    clean = image_module.new("RGBA", converted.size, (0, 0, 0, 0))
    clean.frombytes(converted.tobytes())
    return clean


def _open_rgba(path: Path, context: str) -> tuple[Any, str]:
    image_module, unidentified_error, version = _load_pillow()
    try:
        with image_module.open(path) as source:
            _image_size(source.width, source.height, context)
            source.load()
            result = _rgba_without_metadata(image_module, source, context)
    except (OSError, ValueError, unidentified_error, image_module.DecompressionBombError) as exc:
        raise AssetContractError(f"Could not decode {context}: {exc}") from exc
    return result, version


def _save_png(image: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        destination,
        format="PNG",
        optimize=False,
        compress_level=9,
    )


def _rgba(value: object, context: str) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        raise AssetContractError(f"{context} must contain four channel integers")
    channels = tuple(_integer(item, context, minimum=0, maximum=255) for item in value)
    return channels  # type: ignore[return-value]


def _rgb(value: object, context: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3:
        raise AssetContractError(f"{context} must contain three channel integers")
    channels = tuple(_integer(item, context, minimum=0, maximum=255) for item in value)
    return channels  # type: ignore[return-value]


def _apply_matte_key(image_module: Any, image: Any, raw: object) -> Any:
    matte = _exact_keys(raw, frozenset({"rgb", "tolerance"}), "options/matte_alpha_key")
    key = _rgb(matte["rgb"], "options/matte_alpha_key/rgb")
    tolerance = _integer(
        matte["tolerance"],
        "options/matte_alpha_key/tolerance",
        minimum=0,
        maximum=255,
    )
    keyed = image_module.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = bytearray(image.tobytes())
    for offset in range(0, len(pixels), 4):
        red, green, blue = pixels[offset : offset + 3]
        if max(abs(red - key[0]), abs(green - key[1]), abs(blue - key[2])) <= tolerance:
            pixels[offset : offset + 4] = b"\0\0\0\0"
    keyed.frombytes(bytes(pixels))
    return keyed


def _process_png(
    recipe: dict[str, Any], recipe_root: Path, stage: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    _exact_keys(
        recipe,
        frozenset(
            {"format", "format_version", "operation", "input", "output", "options", "content_hash"}
        ),
        "png_canonical recipe",
    )
    source_path = verify_artifact_reference(recipe_root, recipe["input"], context="input")
    output = _exact_keys(recipe["output"], frozenset({"file"}), "output")
    output_file = _safe_output_file(output["file"], "output/file")
    _require_output_extension(output_file, frozenset({".png"}), "output/file")
    options = recipe["options"]
    if not isinstance(options, dict):
        raise AssetContractError("options must be an object")
    allowed_options = {"crop", "matte_alpha_key", "pad", "resize"}
    unknown = set(options) - allowed_options
    if unknown:
        raise AssetContractError(f"options contains unknown fields: {', '.join(sorted(unknown))}")

    image_module, _, pillow_version = _load_pillow()
    image, _ = _open_rgba(source_path, "input PNG")
    if "matte_alpha_key" in options:
        image = _apply_matte_key(image_module, image, options["matte_alpha_key"])
    if "crop" in options:
        crop = _exact_keys(
            options["crop"],
            frozenset({"bottom", "left", "right", "top"}),
            "options/crop",
        )
        left = _integer(crop["left"], "options/crop/left", minimum=0)
        top = _integer(crop["top"], "options/crop/top", minimum=0)
        right = _integer(crop["right"], "options/crop/right", minimum=1)
        bottom = _integer(crop["bottom"], "options/crop/bottom", minimum=1)
        if left >= right or top >= bottom or right > image.width or bottom > image.height:
            raise AssetContractError("options/crop is outside the input image")
        image = image.crop((left, top, right, bottom))
    if "resize" in options:
        resize = _exact_keys(options["resize"], frozenset({"height", "width"}), "options/resize")
        width = _integer(resize["width"], "options/resize/width", minimum=1)
        height = _integer(resize["height"], "options/resize/height", minimum=1)
        _image_size(width, height, "resized PNG")
        image = image.resize((width, height), resample=image_module.Resampling.NEAREST)
    if "pad" in options:
        pad = _exact_keys(
            options["pad"],
            frozenset({"bottom", "color", "left", "right", "top"}),
            "options/pad",
        )
        left = _integer(pad["left"], "options/pad/left", minimum=0)
        top = _integer(pad["top"], "options/pad/top", minimum=0)
        right = _integer(pad["right"], "options/pad/right", minimum=0)
        bottom = _integer(pad["bottom"], "options/pad/bottom", minimum=0)
        color = _rgba(pad["color"], "options/pad/color")
        width, height = _image_size(
            image.width + left + right,
            image.height + top + bottom,
            "padded PNG",
        )
        padded = image_module.new("RGBA", (width, height), color)
        padded.paste(image, (left, top))
        image = padded

    _image_size(image.width, image.height, "processed PNG")
    destination = stage / output_file
    _save_png(image, destination)
    inputs = [
        {
            "id": "source",
            "artifact": artifact_reference(recipe_root, recipe["input"]["file"]),
        }
    ]
    outputs = [
        {
            "role": "texture",
            "media_type": "image/png",
            "artifact": artifact_reference(stage, output_file),
            "details": {"height": image.height, "mode": "RGBA", "width": image.width},
        }
    ]
    return inputs, outputs, _image_toolchain(pillow_version)


def _image_toolchain(pillow_version: str) -> dict[str, str]:
    return {
        "pillow_version": pillow_version,
        "processor": "worldforge.asset_processing",
        "python_version": platform.python_version(),
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
    }


def _atlas_input(value: object, recipe_root: Path, index: int) -> tuple[dict[str, Any], Path]:
    context = f"inputs/{index}"
    item = _exact_keys(
        value,
        frozenset({"artifact", "clip_id", "duration_ticks", "id", "loop", "pivot"}),
        context,
    )
    frame_id = _identifier(item["id"], f"{context}/id")
    clip_id = _identifier(item["clip_id"], f"{context}/clip_id")
    duration = _integer(
        item["duration_ticks"], f"{context}/duration_ticks", minimum=1, maximum=10000
    )
    if not isinstance(item["loop"], bool):
        raise AssetContractError(f"{context}/loop must be boolean")
    pivot = item["pivot"]
    if not isinstance(pivot, list) or len(pivot) != 2:
        raise AssetContractError(f"{context}/pivot must contain two integers")
    pivot_value = [
        _integer(
            pivot[0],
            f"{context}/pivot/0",
            minimum=-_MAX_IMAGE_EDGE,
            maximum=_MAX_IMAGE_EDGE,
        ),
        _integer(
            pivot[1],
            f"{context}/pivot/1",
            minimum=-_MAX_IMAGE_EDGE,
            maximum=_MAX_IMAGE_EDGE,
        ),
    ]
    path = verify_artifact_reference(recipe_root, item["artifact"], context=f"{context}/artifact")
    return (
        {
            "artifact": artifact_reference(recipe_root, item["artifact"]["file"]),
            "clip_id": clip_id,
            "duration_ticks": duration,
            "id": frame_id,
            "loop": item["loop"],
            "pivot": pivot_value,
        },
        path,
    )


def _process_atlas(
    recipe: dict[str, Any], recipe_root: Path, stage: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    _exact_keys(
        recipe,
        frozenset(
            {"format", "format_version", "operation", "inputs", "output", "options", "content_hash"}
        ),
        "atlas recipe",
    )
    raw_inputs = recipe["inputs"]
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise AssetContractError("inputs must be a non-empty list")
    parsed = [_atlas_input(value, recipe_root, index) for index, value in enumerate(raw_inputs)]
    parsed.sort(key=lambda value: value[0]["id"])
    frame_ids = [item[0]["id"] for item in parsed]
    if len(frame_ids) != len(set(frame_ids)):
        raise AssetContractError("atlas frame IDs must be unique")

    options = _exact_keys(
        recipe["options"],
        frozenset({"cell_height", "cell_width", "columns"}),
        "options",
    )
    cell_width = _integer(options["cell_width"], "options/cell_width", minimum=1)
    cell_height = _integer(options["cell_height"], "options/cell_height", minimum=1)
    columns = _integer(options["columns"], "options/columns", minimum=1)
    rows = (len(parsed) + columns - 1) // columns
    width, height = _image_size(
        cell_width * columns,
        cell_height * rows,
        "atlas",
    )

    output = _exact_keys(recipe["output"], frozenset({"clipset_file", "texture_file"}), "output")
    texture_file = _safe_output_file(output["texture_file"], "output/texture_file")
    clipset_file = _safe_output_file(output["clipset_file"], "output/clipset_file")
    _require_output_extension(texture_file, frozenset({".png"}), "output/texture_file")
    _require_output_extension(clipset_file, frozenset({".json"}), "output/clipset_file")
    if texture_file == clipset_file:
        raise AssetContractError("atlas outputs must use distinct files")

    image_module, _, pillow_version = _load_pillow()
    atlas = image_module.new("RGBA", (width, height), (0, 0, 0, 0))
    frame_records: dict[str, list[tuple[str, dict[str, int]]]] = {}
    clip_contracts: dict[str, tuple[tuple[int, int], bool]] = {}
    receipt_inputs: list[dict[str, Any]] = []
    for position, (item, source_path) in enumerate(parsed):
        image, _ = _open_rgba(source_path, f"atlas input {item['id']}")
        if image.size != (cell_width, cell_height):
            raise AssetContractError(
                f"atlas input {item['id']} is {image.width}x{image.height}; "
                f"expected {cell_width}x{cell_height}"
            )
        x = (position % columns) * cell_width
        y = (position // columns) * cell_height
        atlas.paste(image, (x, y))
        clip_id = item["clip_id"]
        contract = (tuple(item["pivot"]), item["loop"])
        previous = clip_contracts.setdefault(clip_id, contract)
        if previous != contract:
            raise AssetContractError(f"clip {clip_id} has inconsistent pivot or loop values")
        frame_records.setdefault(clip_id, []).append(
            (
                item["id"],
                {
                    "duration_ticks": item["duration_ticks"],
                    "height": cell_height,
                    "width": cell_width,
                    "x": x,
                    "y": y,
                },
            )
        )
        receipt_inputs.append({"id": item["id"], "artifact": item["artifact"]})

    clips: list[dict[str, Any]] = []
    for clip_id in sorted(frame_records):
        pivot, loop = clip_contracts[clip_id]
        frames = [frame for _, frame in sorted(frame_records[clip_id], key=lambda value: value[0])]
        clips.append({"frames": frames, "id": clip_id, "loop": loop, "pivot": list(pivot)})
    clipset = {"clips": clips, "format": "isoworld.clipset", "format_version": 1}

    _save_png(atlas, stage / texture_file)
    write_json_atomic(stage / clipset_file, clipset)
    outputs = [
        {
            "role": "texture",
            "media_type": "image/png",
            "artifact": artifact_reference(stage, texture_file),
            "details": {"height": height, "mode": "RGBA", "width": width},
        },
        {
            "role": "clipset",
            "media_type": "application/json",
            "artifact": artifact_reference(stage, clipset_file),
            "details": {"clips": len(clips), "frames": len(parsed)},
        },
    ]
    return receipt_inputs, outputs, _image_toolchain(pillow_version)


def _round_divide(value: int, divisor: int) -> int:
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    sign = -1 if value < 0 else 1
    return sign * ((abs(value) + divisor // 2) // divisor)


def _frame_is_quiet(samples: array, frame: int, channels: int, threshold: int) -> bool:
    offset = frame * channels
    return all(abs(samples[offset + channel]) <= threshold for channel in range(channels))


def _trim_frames(samples: array, channels: int, threshold: int) -> array:
    frame_count = len(samples) // channels
    first = 0
    while first < frame_count and _frame_is_quiet(samples, first, channels, threshold):
        first += 1
    last = frame_count
    while last > first and _frame_is_quiet(samples, last - 1, channels, threshold):
        last -= 1
    if last < frame_count:
        del samples[last * channels :]
    if first:
        del samples[: first * channels]
    return samples


def _convert_channels(samples: array, source_channels: int, mode: str) -> tuple[array, int]:
    target_channels = 1 if mode == "mono" else 2
    if source_channels == target_channels:
        return samples, target_channels
    converted = array("h")
    if target_channels == 1:
        for offset in range(0, len(samples), 2):
            converted.append(_round_divide(samples[offset] + samples[offset + 1], 2))
    else:
        for sample in samples:
            converted.extend((sample, sample))
    return converted, target_channels


def _resampled_frame_count(frame_count: int, source_rate: int, target_rate: int) -> int:
    if frame_count == 0:
        return 0
    return max(1, _round_divide(frame_count * target_rate, source_rate))


def _validate_pcm_extent(
    frame_count: int,
    sample_rate: int,
    channels: int,
    *,
    context: str,
) -> None:
    if frame_count < 0:
        raise AssetContractError(f"{context} has an invalid frame count")
    if channels not in {1, 2}:
        raise AssetContractError(f"{context} must be mono or stereo")
    if not _MIN_SAMPLE_RATE <= sample_rate <= _MAX_SAMPLE_RATE:
        raise AssetContractError(f"{context} sample rate is outside the supported range")
    if frame_count > _MAX_PCM_FRAMES:
        raise AssetContractError(f"{context} exceeds the PCM frame limit")
    if frame_count > sample_rate * _MAX_PCM_DURATION_SECONDS:
        raise AssetContractError(
            f"{context} exceeds the {_MAX_PCM_DURATION_SECONDS}-second duration limit"
        )
    if frame_count * channels * 2 > _MAX_PCM_BYTES:
        raise AssetContractError(f"{context} exceeds the PCM byte limit")


def _resample_linear(
    samples: array,
    channels: int,
    source_rate: int,
    target_rate: int,
) -> array:
    if not samples or source_rate == target_rate:
        return samples
    frame_count = len(samples) // channels
    output_count = _resampled_frame_count(frame_count, source_rate, target_rate)
    _validate_pcm_extent(output_count, target_rate, channels, context="resampled output WAV")
    output = array("h", [0]) * (output_count * channels)
    for output_index in range(output_count):
        numerator = output_index * source_rate
        left = numerator // target_rate
        remainder = numerator % target_rate
        output_offset = output_index * channels
        if left >= frame_count - 1:
            source_offset = (frame_count - 1) * channels
            for channel in range(channels):
                output[output_offset + channel] = samples[source_offset + channel]
            continue
        left_offset = left * channels
        right_offset = left_offset + channels
        for channel in range(channels):
            output[output_offset + channel] = _round_divide(
                samples[left_offset + channel] * (target_rate - remainder)
                + samples[right_offset + channel] * remainder,
                target_rate,
            )
    return output


def _normalize_peak(samples: array, target_peak: int) -> tuple[array, int]:
    actual_peak = max((abs(sample) for sample in samples), default=0)
    if actual_peak == 0:
        return samples, 0
    for index, sample in enumerate(samples):
        samples[index] = max(
            -32768,
            min(32767, _round_divide(sample * target_peak, actual_peak)),
        )
    return samples, max(abs(sample) for sample in samples)


def _read_pcm16(
    path: Path,
    *,
    target_rate: int | None = None,
    target_channels: int | None = None,
) -> tuple[array, int, int]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            compression = source.getcomptype()
            frame_count = source.getnframes()
            if channels not in {1, 2}:
                raise AssetContractError("wav_pcm accepts only mono or stereo input")
            if sample_width != 2 or compression != "NONE":
                raise AssetContractError("wav_pcm accepts only uncompressed 16-bit PCM input")
            _validate_pcm_extent(frame_count, sample_rate, channels, context="input WAV")
            if target_rate is not None and target_channels is not None:
                if target_rate > sample_rate * _MAX_RESAMPLE_RATIO:
                    raise AssetContractError("wav_pcm resampling ratio exceeds the safe limit")
                output_count = _resampled_frame_count(frame_count, sample_rate, target_rate)
                _validate_pcm_extent(
                    output_count,
                    target_rate,
                    target_channels,
                    context="resampled output WAV",
                )

            samples = array("h")
            remaining = frame_count
            bytes_per_frame = channels * 2
            while remaining:
                requested = min(remaining, _PCM_CHUNK_FRAMES)
                payload = source.readframes(requested)
                if not payload or len(payload) % bytes_per_frame:
                    raise AssetContractError("input WAV contains incomplete PCM frames")
                chunk = array("h")
                chunk.frombytes(payload)
                if sys.byteorder != "little":
                    chunk.byteswap()
                samples.extend(chunk)
                remaining -= len(payload) // bytes_per_frame
    except (EOFError, OSError, wave.Error) as exc:
        raise AssetContractError(f"Could not decode input WAV: {exc}") from exc
    if len(samples) != frame_count * channels:
        raise AssetContractError("input WAV contains incomplete PCM frames")
    return samples, sample_rate, channels


def _write_pcm16(path: Path, samples: array, rate: int, channels: int) -> None:
    _validate_pcm_extent(len(samples) // channels, rate, channels, context="output WAV")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = samples
    if sys.byteorder != "little":
        encoded = array("h", samples)
        encoded.byteswap()
    payload = memoryview(encoded).cast("B")
    chunk_bytes = _PCM_CHUNK_FRAMES * channels * 2
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.setcomptype("NONE", "not compressed")
        for offset in range(0, len(payload), chunk_bytes):
            target.writeframesraw(payload[offset : offset + chunk_bytes])


def _process_wav(
    recipe: dict[str, Any], recipe_root: Path, stage: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    _exact_keys(
        recipe,
        frozenset(
            {"format", "format_version", "operation", "input", "output", "options", "content_hash"}
        ),
        "wav_pcm recipe",
    )
    source_path = verify_artifact_reference(recipe_root, recipe["input"], context="input")
    output = _exact_keys(recipe["output"], frozenset({"file"}), "output")
    output_file = _safe_output_file(output["file"], "output/file")
    _require_output_extension(output_file, frozenset({".wav"}), "output/file")
    options = _exact_keys(
        recipe["options"],
        frozenset({"channel_mode", "peak", "sample_rate", "trim_threshold"}),
        "options",
    )
    mode = options["channel_mode"]
    if mode not in {"mono", "stereo"}:
        raise AssetContractError("options/channel_mode must be mono or stereo")
    target_rate = _integer(
        options["sample_rate"],
        "options/sample_rate",
        minimum=_MIN_SAMPLE_RATE,
        maximum=_MAX_SAMPLE_RATE,
    )
    threshold = _integer(
        options["trim_threshold"],
        "options/trim_threshold",
        minimum=0,
        maximum=32767,
    )
    target_peak = _integer(options["peak"], "options/peak", minimum=1, maximum=32767)

    output_channels = 1 if mode == "mono" else 2
    samples, source_rate, source_channels = _read_pcm16(
        source_path,
        target_rate=target_rate,
        target_channels=output_channels,
    )
    samples = _trim_frames(samples, source_channels, threshold)
    samples, output_channels = _convert_channels(samples, source_channels, mode)
    samples = _resample_linear(samples, output_channels, source_rate, target_rate)
    samples, output_peak = _normalize_peak(samples, target_peak)
    _write_pcm16(stage / output_file, samples, target_rate, output_channels)
    inputs = [
        {
            "id": "source",
            "artifact": artifact_reference(recipe_root, recipe["input"]["file"]),
        }
    ]
    outputs = [
        {
            "role": "audio",
            "media_type": "audio/wav",
            "artifact": artifact_reference(stage, output_file),
            "details": {
                "channels": output_channels,
                "frames": len(samples) // output_channels,
                "peak": output_peak,
                "sample_rate": target_rate,
                "sample_width": 2,
            },
        }
    ]
    toolchain = {
        "processor": "worldforge.asset_processing",
        "python_version": platform.python_version(),
        "wave_module": "stdlib",
    }
    return inputs, outputs, toolchain


def _process_glb(
    recipe: dict[str, Any], recipe_root: Path, stage: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _exact_keys(
        recipe,
        frozenset(
            {"format", "format_version", "operation", "input", "output", "options", "content_hash"}
        ),
        "glb_validate recipe",
    )
    source_path = verify_artifact_reference(recipe_root, recipe["input"], context="input")
    output = _exact_keys(recipe["output"], frozenset({"file", "role"}), "output")
    output_file = _safe_output_file(output["file"], "output/file")
    if not output_file.casefold().endswith(".glb"):
        raise AssetContractError("output/file must use the .glb extension")
    role = output["role"]
    if role not in _GLB_ROLES:
        raise AssetContractError("output/role must be animation, collision, model, or skeleton")
    options = _exact_keys(recipe["options"], frozenset({"budgets", "max_bytes"}), "options")
    max_bytes = _integer(
        options["max_bytes"], "options/max_bytes", minimum=1, maximum=MAX_GLB_BYTES
    )
    budgets = options["budgets"]
    if not isinstance(budgets, dict):
        raise AssetContractError("options/budgets must be an object")
    inspection_budgets = dict(budgets)
    max_texture_size = inspection_budgets.pop("max_texture_size", None)
    if max_texture_size is not None and (
        isinstance(max_texture_size, bool)
        or not isinstance(max_texture_size, int)
        or max_texture_size < 1
    ):
        raise AssetContractError("options/budgets/max_texture_size must be a positive integer")
    try:
        details = inspect_glb(
            source_path,
            allow_external_uris=False,
            budgets=inspection_budgets,
            max_bytes=max_bytes,
        )
    except GLBError as exc:
        raise AssetContractError(f"Input GLB is not a safe neutral handoff: {exc}") from exc

    metrics = details["metrics"]
    required_metric = {
        "animation": "animations",
        "collision": "meshes",
        "model": "meshes",
        "skeleton": "skins",
    }[role]
    if metrics[required_metric] < 1:
        raise AssetContractError(f"GLB role {role} requires at least one {required_metric} entry")
    if max_texture_size is not None and details["max_texture_dimension"] > max_texture_size:
        raise AssetContractError(
            "Input GLB embedded texture exceeds max_texture_size: "
            f"{details['max_texture_dimension']} > {max_texture_size}"
        )

    destination = stage / output_file
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, destination)
    inputs = [
        {
            "id": "source",
            "artifact": artifact_reference(recipe_root, recipe["input"]["file"]),
        }
    ]
    outputs = [
        {
            "role": role,
            "media_type": "model/gltf-binary",
            "artifact": artifact_reference(stage, output_file),
            "details": details,
        }
    ]
    toolchain: dict[str, Any] = {
        "allowed_extensions": sorted(DEFAULT_ALLOWED_EXTENSIONS),
        "external_uris_allowed": False,
        "inspector": "worldforge.asset_formats.gltf",
        "processor": "worldforge.asset_processing",
        "python_version": platform.python_version(),
    }
    return inputs, outputs, toolchain


def _validate_file_contract(output_file: str, role: object, media_type: object) -> tuple[str, str]:
    if not isinstance(role, str) or not isinstance(media_type, str):
        raise AssetContractError("output role and media_type must be strings")
    pair = (role, media_type)
    if pair not in _FILE_ROLE_MEDIA:
        raise AssetContractError("output role/media_type is not allowed for file_validate")
    _require_output_extension(output_file, _FILE_SUFFIXES[pair], "output/file")
    return pair


def _validate_sfnt(payload: bytes, media_type: str) -> dict[str, int]:
    label = "TTF" if media_type == "font/ttf" else "OTF"
    valid_signatures = (
        frozenset({b"\x00\x01\x00\x00", b"true", b"typ1"})
        if media_type == "font/ttf"
        else frozenset({b"OTTO"})
    )
    if len(payload) < 12 or payload[:4] not in valid_signatures:
        raise AssetContractError(f"Input {label} has an invalid sfnt header")
    table_count, search_range, entry_selector, range_shift = struct.unpack_from(">HHHH", payload, 4)
    if not 1 <= table_count <= 4096:
        raise AssetContractError(f"Input {label} has an invalid table count")
    directory_end = 12 + table_count * 16
    if directory_end > len(payload):
        raise AssetContractError(f"Input {label} has a truncated table directory")
    greatest_power = 1 << (table_count.bit_length() - 1)
    if (
        search_range != greatest_power * 16
        or entry_selector != greatest_power.bit_length() - 1
        or range_shift != table_count * 16 - search_range
    ):
        raise AssetContractError(f"Input {label} has an invalid sfnt search header")
    tags: set[bytes] = set()
    table_ranges: list[tuple[int, int, bytes]] = []
    for index in range(table_count):
        tag, _, offset, length = struct.unpack_from(">4sIII", payload, 12 + index * 16)
        if tag in tags or any(character < 0x20 or character > 0x7E for character in tag):
            raise AssetContractError(f"Input {label} has an invalid table tag")
        tags.add(tag)
        if (
            offset % 4
            or offset < directory_end
            or offset > len(payload)
            or length > len(payload) - offset
        ):
            raise AssetContractError(f"Input {label} table {tag!r} is outside the file")
        table_ranges.append((offset, offset + length, tag))
    cursor = directory_end
    for start, end, tag in sorted(table_ranges):
        if start < cursor:
            raise AssetContractError(f"Input {label} table {tag!r} overlaps another table")
        if any(payload[cursor:start]):
            raise AssetContractError(f"Input {label} contains non-zero bytes outside its tables")
        cursor = end
    if any(payload[cursor:]):
        raise AssetContractError(f"Input {label} contains non-zero padding or trailing bytes")
    return {"byte_length": len(payload)}


def _validate_glsl(payload: bytes) -> dict[str, int]:
    if not payload or len(payload) > _MAX_GLSL_BYTES:
        raise AssetContractError(f"Input GLSL must contain 1 to {_MAX_GLSL_BYTES} UTF-8 bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssetContractError(f"Input GLSL is not valid UTF-8: {exc}") from exc
    if text.startswith("\ufeff"):
        raise AssetContractError("Input GLSL must not contain a UTF-8 byte-order mark")
    if not text.strip():
        raise AssetContractError("Input GLSL must contain non-whitespace source text")
    if any(
        (ord(character) < 0x20 and character not in "\t\n\r") or 0x7F <= ord(character) <= 0x9F
        for character in text
    ):
        raise AssetContractError("Input GLSL contains NUL or forbidden control characters")
    if _INCLUDE_PATTERN.search(text):
        raise AssetContractError("Input GLSL contains a forbidden external include")
    if _URL_PATTERN.search(text):
        raise AssetContractError("Input GLSL contains a forbidden URL")
    if _SECRET_PATTERN.search(text):
        raise AssetContractError("Input GLSL contains secret or credential-like text")
    if _PROVIDER_PATTERN.search(text):
        raise AssetContractError("Input GLSL contains provider or authoring-tool text")
    return {"byte_length": len(payload)}


def _inspect_validated_file(
    path: Path,
    *,
    role: str,
    media_type: str,
    expected_sha256: str | None = None,
) -> tuple[bytes, dict[str, int]]:
    limit = _MAX_GLSL_BYTES if media_type == "text/x-glsl" else _MAX_FONT_BYTES
    try:
        if path.stat().st_size > limit:
            raise AssetContractError(f"Input {media_type} exceeds the {limit}-byte limit")
        payload = path.read_bytes()
    except OSError as exc:
        raise AssetContractError(f"Could not read input {media_type}: {exc}") from exc
    if len(payload) > limit:
        raise AssetContractError(f"Input {media_type} exceeds the {limit}-byte limit")
    if expected_sha256 is not None and hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise AssetContractError("Input file changed while file_validate was reading it")
    if role == "font":
        details = _validate_sfnt(payload, media_type)
    else:
        details = _validate_glsl(payload)
    return payload, details


def _process_file(
    recipe: dict[str, Any], recipe_root: Path, stage: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    _exact_keys(
        recipe,
        frozenset(
            {"format", "format_version", "operation", "input", "output", "options", "content_hash"}
        ),
        "file_validate recipe",
    )
    source_path = verify_artifact_reference(recipe_root, recipe["input"], context="input")
    output = _exact_keys(
        recipe["output"],
        frozenset({"file", "media_type", "role"}),
        "output",
    )
    output_file = _safe_output_file(output["file"], "output/file")
    role, media_type = _validate_file_contract(
        output_file,
        output["role"],
        output["media_type"],
    )
    _exact_keys(recipe["options"], frozenset(), "options")
    payload, details = _inspect_validated_file(
        source_path,
        role=role,
        media_type=media_type,
        expected_sha256=recipe["input"]["sha256"],
    )
    destination = stage / output_file
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    inputs = [
        {
            "id": "source",
            "artifact": artifact_reference(recipe_root, recipe["input"]["file"]),
        }
    ]
    outputs = [
        {
            "role": role,
            "media_type": media_type,
            "artifact": artifact_reference(stage, output_file),
            "details": details,
        }
    ]
    toolchain = {
        "processor": "worldforge.asset_processing",
        "python_version": platform.python_version(),
        "validator": "strict_builtin_v1",
    }
    return inputs, outputs, toolchain


def _validate_recipe_header(recipe: dict[str, Any]) -> str:
    if recipe.get("format") != RECIPE_FORMAT or recipe.get("format_version") != FORMAT_VERSION:
        raise AssetContractError("Unsupported asset-processing recipe format")
    operation = recipe.get("operation")
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise AssetContractError(f"Unsupported asset-processing operation: {operation!r}")
    require_content_hash(recipe, context="asset-processing recipe")
    return operation


def _destination(output_directory: str | Path) -> Path:
    supplied = Path(output_directory)
    if not supplied.name or supplied.name in {".", ".."}:
        raise AssetContractError("output_directory must name a directory")
    parent = supplied.parent
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve()
    destination = resolved_parent / supplied.name
    if destination.exists() or destination.is_symlink():
        raise AssetContractError(f"Refusing to overwrite output directory {destination}")
    return destination


def _remove_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.unlink()
        except OSError:
            pass


def _remove_owned_directory(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.rmdir()
        except OSError:
            # Preserve anything introduced by another writer during rollback.
            pass


def _rename_directory_noreplace(source: Path, destination: Path) -> bool:
    """Use Linux renameat2 when available; return false for a safe fallback."""

    if os.name != "posix":
        return False
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        return False
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,  # AT_FDCWD
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise AssetContractError(f"Refusing to overwrite output directory {destination}")
    unsupported = {errno.EINVAL, errno.ENOSYS}
    if hasattr(errno, "ENOTSUP"):
        unsupported.add(errno.ENOTSUP)
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    if error in unsupported:
        return False
    raise OSError(error, os.strerror(error), destination)


def _publish_directory_fallback(source: Path, destination: Path) -> None:
    """Publish without replacement on filesystems lacking renameat2."""

    created_directories: list[tuple[Path, tuple[int, int]]] = []
    published_files: list[tuple[Path, tuple[int, int]]] = []
    try:
        try:
            destination.mkdir()
        except FileExistsError as exc:
            raise AssetContractError(
                f"Refusing to overwrite output directory {destination}"
            ) from exc
        info = destination.lstat()
        created_directories.append((destination, (info.st_dev, info.st_ino)))
        for staged_directory in sorted(
            (path for path in source.rglob("*") if path.is_dir()),
            key=lambda path: (len(path.relative_to(source).parts), path.as_posix()),
        ):
            published_directory = destination / staged_directory.relative_to(source)
            published_directory.mkdir()
            info = published_directory.lstat()
            created_directories.append((published_directory, (info.st_dev, info.st_ino)))
        for staged_file in sorted(
            (path for path in source.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(source).as_posix(),
        ):
            published_file = destination / staged_file.relative_to(source)
            staged_info = staged_file.lstat()
            try:
                os.link(staged_file, published_file)
            except FileExistsError as exc:
                raise AssetContractError(f"Refusing to overwrite {published_file}") from exc
            published_files.append((published_file, (staged_info.st_dev, staged_info.st_ino)))
        shutil.rmtree(source)
    except Exception:
        for path, identity in reversed(published_files):
            _remove_owned_file(path, identity)
        for path, identity in reversed(created_directories):
            _remove_owned_directory(path, identity)
        raise


def _publish_directory_noreplace(source: Path, destination: Path) -> None:
    if not _rename_directory_noreplace(source, destination):
        _publish_directory_fallback(source, destination)


def process_asset_recipe(
    recipe_path: str | Path,
    output_directory: str | Path,
    *,
    asset_root: str | Path,
) -> dict[str, Any]:
    """Process one allowlisted offline recipe and publish without replacement."""

    source = Path(recipe_path)
    root = Path(asset_root).resolve()
    if not root.is_dir() or Path(asset_root).is_symlink():
        raise AssetContractError(f"asset_root is not a safe directory: {asset_root}")
    try:
        source.resolve().relative_to(root)
    except ValueError as exc:
        raise AssetContractError(f"Processing recipe must live under asset_root {root}") from exc
    recipe = read_json_object(source)
    operation = _validate_recipe_header(recipe)
    destination = _destination(output_directory)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise AssetContractError(f"output_directory must live under asset_root {root}") from exc
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
    try:
        if operation == "png_canonical":
            inputs, outputs, toolchain = _process_png(recipe, root, stage)
        elif operation == "atlas":
            inputs, outputs, toolchain = _process_atlas(recipe, root, stage)
        elif operation == "wav_pcm":
            inputs, outputs, toolchain = _process_wav(recipe, root, stage)
        elif operation == "glb_validate":
            inputs, outputs, toolchain = _process_glb(recipe, root, stage)
        else:
            inputs, outputs, toolchain = _process_file(recipe, root, stage)
        receipt = bind_content_hash(
            {
                "format": RECEIPT_FORMAT,
                "format_version": FORMAT_VERSION,
                "inputs": inputs,
                "operation": operation,
                "outputs": outputs,
                "recipe": {
                    "content_hash": recipe["content_hash"],
                    "sha256": sha256_file(source),
                },
                "toolchain": toolchain,
            }
        )
        write_json_atomic(stage / RECEIPT_NAME, receipt)
        _publish_directory_noreplace(stage, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return receipt


def _verify_output_entry(root: Path, value: object, index: int) -> tuple[str, str, Path]:
    context = f"outputs/{index}"
    item = _exact_keys(
        value,
        frozenset({"artifact", "details", "media_type", "role"}),
        context,
    )
    if not isinstance(item["role"], str) or not item["role"]:
        raise AssetContractError(f"{context}/role must be a string")
    if not isinstance(item["media_type"], str) or not item["media_type"]:
        raise AssetContractError(f"{context}/media_type must be a string")
    if not isinstance(item["details"], dict):
        raise AssetContractError(f"{context}/details must be an object")
    path = verify_artifact_reference(root, item["artifact"], context=f"{context}/artifact")
    return item["role"], item["media_type"], path


def _verify_png_details(path: Path, details: dict[str, Any], context: str) -> None:
    _exact_keys(details, frozenset({"height", "mode", "width"}), context)
    image, _ = _open_rgba(path, context)
    if details != {"height": image.height, "mode": "RGBA", "width": image.width}:
        raise AssetContractError(f"{context} does not match the PNG")


def _verify_wav_details(path: Path, details: dict[str, Any], context: str) -> None:
    _exact_keys(
        details,
        frozenset({"channels", "frames", "peak", "sample_rate", "sample_width"}),
        context,
    )
    samples, sample_rate, channels = _read_pcm16(path)
    peak = max((abs(sample) for sample in samples), default=0)
    actual = {
        "channels": channels,
        "frames": len(samples) // channels,
        "peak": peak,
        "sample_rate": sample_rate,
        "sample_width": 2,
    }
    if details != actual:
        raise AssetContractError(f"{context} does not match the WAV")


def _verify_clipset(path: Path, details: dict[str, Any], context: str) -> None:
    _exact_keys(details, frozenset({"clips", "frames"}), context)
    clipset = read_json_object(path)
    _exact_keys(clipset, frozenset({"clips", "format", "format_version"}), "clipset")
    if clipset.get("format") != "isoworld.clipset" or clipset.get("format_version") != 1:
        raise AssetContractError("Output clipset has an unsupported format")
    clips = clipset.get("clips")
    if not isinstance(clips, list) or not clips:
        raise AssetContractError("Output clipset must contain clips")
    frame_count = 0
    previous_id = ""
    for index, value in enumerate(clips):
        clip = _exact_keys(
            value, frozenset({"frames", "id", "loop", "pivot"}), f"clipset/clips/{index}"
        )
        clip_id = _identifier(clip["id"], f"clipset/clips/{index}/id")
        if clip_id <= previous_id:
            raise AssetContractError("Output clipset IDs are not canonical")
        previous_id = clip_id
        if not isinstance(clip["frames"], list) or not clip["frames"]:
            raise AssetContractError(f"clipset/clips/{index}/frames must be non-empty")
        frame_count += len(clip["frames"])
    if details != {"clips": len(clips), "frames": frame_count}:
        raise AssetContractError(f"{context} does not match the clipset")


def _verify_glb_details(
    path: Path,
    details: dict[str, Any],
    context: str,
    *,
    role: str,
) -> None:
    try:
        actual = inspect_glb(path, allow_external_uris=False)
    except GLBError as exc:
        raise AssetContractError(f"{context} references an unsafe GLB: {exc}") from exc
    required_metric = {
        "animation": "animations",
        "collision": "meshes",
        "model": "meshes",
        "skeleton": "skins",
    }[role]
    if actual["metrics"][required_metric] < 1:
        raise AssetContractError(
            f"{context} GLB role {role} requires at least one {required_metric} entry"
        )
    if details != actual:
        raise AssetContractError(f"{context} does not match the GLB inspection")


def _verify_file_details(
    path: Path,
    details: dict[str, Any],
    context: str,
    *,
    relative: str,
    role: str,
    media_type: str,
) -> None:
    _exact_keys(details, frozenset({"byte_length"}), context)
    _validate_file_contract(relative, role, media_type)
    _, actual = _inspect_validated_file(path, role=role, media_type=media_type)
    if details != actual:
        raise AssetContractError(f"{context} does not match the validated file")


def _verify_receipt_inputs(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise AssetContractError("receipt inputs must be a non-empty list")
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _exact_keys(raw, frozenset({"artifact", "id"}), f"inputs/{index}")
        identifier = item["id"]
        if identifier != "source":
            identifier = _identifier(identifier, f"inputs/{index}/id")
        if identifier in seen:
            raise AssetContractError("receipt input IDs must be unique")
        seen.add(identifier)
        _artifact_shape(item["artifact"], f"inputs/{index}/artifact")
    if len(value) > 1 and [item["id"] for item in value] != sorted(item["id"] for item in value):
        raise AssetContractError("receipt input IDs are not canonical")


def verify_processing_receipt(receipt_path: str | Path) -> dict[str, Any]:
    """Verify a processing receipt and every output byte it binds."""

    path = Path(receipt_path)
    receipt = read_json_object(path)
    _exact_keys(
        receipt,
        frozenset(
            {
                "content_hash",
                "format",
                "format_version",
                "inputs",
                "operation",
                "outputs",
                "recipe",
                "toolchain",
            }
        ),
        "processing receipt",
    )
    if path.name != RECEIPT_NAME:
        raise AssetContractError(f"Processing receipt must be named {RECEIPT_NAME}")
    if receipt["format"] != RECEIPT_FORMAT or receipt["format_version"] != FORMAT_VERSION:
        raise AssetContractError("Unsupported processing receipt format")
    if receipt["operation"] not in _OPERATIONS:
        raise AssetContractError("Processing receipt has an unsupported operation")
    require_content_hash(receipt, context="processing receipt")
    recipe = _exact_keys(receipt["recipe"], frozenset({"content_hash", "sha256"}), "recipe")
    _digest(recipe["content_hash"], "recipe/content_hash")
    _digest(recipe["sha256"], "recipe/sha256")
    if not isinstance(receipt["toolchain"], dict) or not receipt["toolchain"]:
        raise AssetContractError("toolchain must be a non-empty object")
    if receipt["operation"] == "file_validate":
        file_toolchain = _exact_keys(
            receipt["toolchain"],
            frozenset({"processor", "python_version", "validator"}),
            "toolchain",
        )
        if (
            file_toolchain["processor"] != "worldforge.asset_processing"
            or file_toolchain["validator"] != "strict_builtin_v1"
            or not isinstance(file_toolchain["python_version"], str)
            or not file_toolchain["python_version"]
        ):
            raise AssetContractError("file_validate toolchain is invalid")
    _verify_receipt_inputs(receipt["inputs"])

    outputs = receipt["outputs"]
    if not isinstance(outputs, list) or not outputs:
        raise AssetContractError("receipt outputs must be a non-empty list")
    root = path.resolve().parent
    roles: list[str] = []
    role_media: list[tuple[str, str]] = []
    files: set[str] = set()
    for index, raw in enumerate(outputs):
        role, media_type, output_path = _verify_output_entry(root, raw, index)
        relative = raw["artifact"]["file"]
        if relative in files:
            raise AssetContractError("receipt output files must be unique")
        files.add(relative)
        roles.append(role)
        role_media.append((role, media_type))
        details = raw["details"]
        if role == "texture" and media_type == "image/png":
            _require_output_extension(
                relative, frozenset({".png"}), f"outputs/{index}/artifact/file"
            )
            _verify_png_details(output_path, details, f"outputs/{index}/details")
        elif role == "audio" and media_type == "audio/wav":
            _require_output_extension(
                relative, frozenset({".wav"}), f"outputs/{index}/artifact/file"
            )
            _verify_wav_details(output_path, details, f"outputs/{index}/details")
        elif role == "clipset" and media_type == "application/json":
            _require_output_extension(
                relative,
                frozenset({".json"}),
                f"outputs/{index}/artifact/file",
            )
            _verify_clipset(output_path, details, f"outputs/{index}/details")
        elif role in _GLB_ROLES and media_type == "model/gltf-binary":
            _require_output_extension(
                relative, frozenset({".glb"}), f"outputs/{index}/artifact/file"
            )
            _verify_glb_details(
                output_path,
                details,
                f"outputs/{index}/details",
                role=role,
            )
        elif (role, media_type) in _FILE_ROLE_MEDIA:
            _verify_file_details(
                output_path,
                details,
                f"outputs/{index}/details",
                relative=relative,
                role=role,
                media_type=media_type,
            )
        else:
            raise AssetContractError(f"outputs/{index} has an unsupported role/media type")

    if receipt["operation"] == "glb_validate":
        valid_roles = len(roles) == 1 and roles[0] in _GLB_ROLES
    elif receipt["operation"] == "file_validate":
        valid_roles = len(role_media) == 1 and role_media[0] in _FILE_ROLE_MEDIA
    else:
        expected_roles = {
            "atlas": ["texture", "clipset"],
            "png_canonical": ["texture"],
            "wav_pcm": ["audio"],
        }[receipt["operation"]]
        valid_roles = roles == expected_roles
    if not valid_roles:
        raise AssetContractError("receipt outputs do not match its operation")
    return receipt
