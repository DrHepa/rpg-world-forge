from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worldforge.asset_io import AssetContractError

PRODUCTION_MAX_IMAGE_EDGE = 32768
MAX_IMAGE_PIXELS = 64 * 1024 * 1024
IMAGE_FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True)
class ImageInspection:
    format: str
    height: int
    media_type: str
    width: int


def _load_pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise AssetContractError("Pillow is required to validate image artifacts") from exc
    return Image, UnidentifiedImageError


def _validate_extent(
    width: int,
    height: int,
    context: str,
    *,
    max_edge: int,
    max_pixels: int,
) -> None:
    if width <= 0 or height <= 0:
        raise AssetContractError(f"{context} dimensions must be positive")
    if width > max_edge or height > max_edge:
        raise AssetContractError(f"{context} exceeds the {max_edge}-pixel edge limit")
    if width * height > max_pixels:
        raise AssetContractError(f"{context} exceeds the image-pixel limit")


def inspect_image_file(
    path: Path,
    context: str,
    *,
    max_edge: int = PRODUCTION_MAX_IMAGE_EDGE,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> ImageInspection:
    """Fully decode a bounded PNG, JPEG, or WebP and report its byte-derived identity."""

    image_module, unidentified_error = _load_pillow()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", image_module.DecompressionBombWarning)
            with image_module.open(path) as source:
                image_format = source.format
                if image_format not in IMAGE_FORMAT_MEDIA_TYPES:
                    raise AssetContractError(
                        f"{context} uses unsupported decoded image format {image_format!r}"
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise AssetContractError(f"{context} must contain exactly one image frame")
                _validate_extent(
                    source.width,
                    source.height,
                    context,
                    max_edge=max_edge,
                    max_pixels=max_pixels,
                )
                source.load()
                width, height = source.size
    except AssetContractError:
        raise
    except (
        OSError,
        ValueError,
        unidentified_error,
        image_module.DecompressionBombError,
        image_module.DecompressionBombWarning,
    ) as exc:
        raise AssetContractError(f"Could not decode {context}: {exc}") from exc
    return ImageInspection(
        format=image_format,
        height=height,
        media_type=IMAGE_FORMAT_MEDIA_TYPES[image_format],
        width=width,
    )
