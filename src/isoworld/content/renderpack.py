from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isoworld.content.models import WorldPack

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_RENDERPACK_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
ASSET_KINDS = {
    "font",
    "music",
    "portrait",
    "shader",
    "sfx",
    "sprite",
    "spritesheet",
    "tileset",
    "ui",
    "vfx",
}
OUTPUT_ROLES = {
    "audio",
    "clipset",
    "font",
    "fragment_shader",
    "texture",
    "vertex_shader",
}
ROLE_MEDIA_TYPES = {
    "audio": {"audio/mpeg", "audio/ogg", "audio/wav"},
    "clipset": {"application/json"},
    "font": {"font/otf", "font/ttf"},
    "fragment_shader": {"text/x-glsl"},
    "texture": {"image/jpeg", "image/png", "image/webp"},
    "vertex_shader": {"text/x-glsl"},
}
VISUAL_KINDS = {"portrait", "sprite", "spritesheet", "tileset", "ui", "vfx"}


class RenderPackError(ValueError):
    """Raised when processed presentation content is unsafe or incompatible."""


@dataclass(frozen=True, slots=True)
class AssetFile:
    role: str
    path: str
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ClipFrame:
    x: int
    y: int
    width: int
    height: int
    duration_ticks: int


@dataclass(frozen=True, slots=True)
class AnimationClip:
    id: str
    frames: tuple[ClipFrame, ...]
    pivot_x: float
    pivot_y: float
    loop: bool

    def frame_at(self, tick: int) -> ClipFrame:
        total = sum(frame.duration_ticks for frame in self.frames)
        position = max(tick, 0)
        if self.loop:
            position %= total
        else:
            position = min(position, total - 1)
        elapsed = 0
        for frame in self.frames:
            elapsed += frame.duration_ticks
            if position < elapsed:
                return frame
        return self.frames[-1]


@dataclass(frozen=True, slots=True)
class RenderAsset:
    id: str
    kind: str
    files: tuple[AssetFile, ...]
    clips: tuple[AnimationClip, ...] = ()

    def files_for(self, role: str) -> tuple[AssetFile, ...]:
        return tuple(item for item in self.files if item.role == role)

    def clip(self, clip_id: str) -> AnimationClip:
        for item in self.clips:
            if item.id == clip_id:
                return item
        raise KeyError(clip_id)


@dataclass(frozen=True, slots=True)
class RenderBinding:
    slot: str
    asset_id: str
    clip: str | None = None
    moving_clip: str | None = None
    scale: float = 1.0
    layer: int = 0


@dataclass(frozen=True, slots=True)
class RenderPack:
    world_id: str
    world_content_hash: str
    content_hash: str
    root: Path
    assets: tuple[RenderAsset, ...]
    bindings: tuple[RenderBinding, ...]

    def asset(self, asset_id: str) -> RenderAsset:
        for item in self.assets:
            if item.id == asset_id:
                return item
        raise KeyError(asset_id)

    def binding(self, slot: str) -> RenderBinding | None:
        return next((item for item in self.bindings if item.slot == slot), None)

    def resolve_file(self, item: AssetFile) -> Path:
        return (self.root / item.path).resolve()


def _read_json(path: Path, *, limit: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size > limit:
            raise RenderPackError(f"{path} exceeds the {limit}-byte limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except RenderPackError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderPackError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RenderPackError(f"{path} must contain a JSON object")
    return value


def _canonical_hash(raw: dict[str, Any]) -> str:
    payload = dict(raw)
    payload.pop("content_hash", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise RenderPackError("Asset path must be a non-empty string")
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise RenderPackError(f"Asset path escapes the renderpack root: {relative}")
    if not path.is_file():
        raise RenderPackError(f"Processed asset is missing: {relative}")
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise RenderPackError(f"Processed asset exceeds the file limit: {relative}")
    return path


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderPackError(f"{context} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RenderPackError(f"{context} must be finite")
    return result


def _binding_kind_is_compatible(slot: str, kind: Any) -> bool:
    if not isinstance(kind, str):
        return False
    category, *parts = slot.split(":")
    if category == "actor":
        return kind in {"sprite", "spritesheet"}
    if category == "tile_type":
        return kind in {"sprite", "spritesheet", "tileset"}
    if category == "interaction":
        return kind in {"sprite", "spritesheet", "vfx"}
    if category == "portrait":
        return kind in {"portrait", "sprite"}
    if category == "event":
        return kind == "sfx"
    if category == "music":
        return kind == "music"
    if category == "ui" and parts == ["font"]:
        return kind == "font"
    if category in {"ability", "scene", "ui"}:
        return kind in VISUAL_KINDS | {"font", "shader"}
    return False


def load_clipset(path: str | Path) -> tuple[AnimationClip, ...]:
    path = Path(path)
    raw = _read_json(path, limit=MAX_RENDERPACK_BYTES)
    if raw.get("format") != "isoworld.clipset" or raw.get("format_version") != 1:
        raise RenderPackError(f"Unknown clipset format: {path}")
    raw_clips = raw.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        raise RenderPackError(f"Clipset must contain clips: {path}")
    clips: list[AnimationClip] = []
    seen: set[str] = set()
    for clip_index, raw_clip in enumerate(raw_clips):
        context = f"{path}: clips/{clip_index}"
        if not isinstance(raw_clip, dict):
            raise RenderPackError(f"{context} must be an object")
        clip_id = raw_clip.get("id")
        if not isinstance(clip_id, str) or not ID_PATTERN.fullmatch(clip_id):
            raise RenderPackError(f"{context}/id is invalid")
        if clip_id in seen:
            raise RenderPackError(f"{context}/id is duplicated")
        seen.add(clip_id)
        pivot = raw_clip.get("pivot")
        if not isinstance(pivot, list) or len(pivot) != 2:
            raise RenderPackError(f"{context}/pivot must contain two values")
        pivot_x = _number(pivot[0], f"{context}/pivot/0")
        pivot_y = _number(pivot[1], f"{context}/pivot/1")
        loop = raw_clip.get("loop")
        if not isinstance(loop, bool):
            raise RenderPackError(f"{context}/loop must be boolean")
        raw_frames = raw_clip.get("frames")
        if not isinstance(raw_frames, list) or not raw_frames:
            raise RenderPackError(f"{context}/frames must be a non-empty list")
        frames: list[ClipFrame] = []
        for frame_index, raw_frame in enumerate(raw_frames):
            frame_context = f"{context}/frames/{frame_index}"
            if not isinstance(raw_frame, dict):
                raise RenderPackError(f"{frame_context} must be an object")
            values = [raw_frame.get(field) for field in ("x", "y", "width", "height")]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                raise RenderPackError(f"{frame_context} rectangle values must be integers")
            x, y, width, height = values
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise RenderPackError(f"{frame_context} rectangle is invalid")
            duration = raw_frame.get("duration_ticks")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or not 1 <= duration <= 10000
            ):
                raise RenderPackError(f"{frame_context}/duration_ticks must be in 1..10000")
            frames.append(ClipFrame(x, y, width, height, duration))
        clips.append(AnimationClip(clip_id, tuple(frames), pivot_x, pivot_y, loop))
    return tuple(clips)


def load_renderpack(path: str | Path, worldpack: WorldPack) -> RenderPack:
    renderpack_path = Path(path)
    raw = _read_json(renderpack_path, limit=MAX_RENDERPACK_BYTES)
    if raw.get("format") != "isoworld.renderpack" or raw.get("format_version") != 1:
        raise RenderPackError("Unknown renderpack format")
    supplied_hash = raw.get("content_hash")
    if not isinstance(supplied_hash, str) or not SHA256_PATTERN.fullmatch(supplied_hash):
        raise RenderPackError("The renderpack has no valid content hash")
    if _canonical_hash(raw) != supplied_hash:
        raise RenderPackError("The renderpack content hash does not match its contents")
    if raw.get("world_id") != worldpack.world_id:
        raise RenderPackError("The renderpack belongs to a different world")
    if raw.get("world_content_hash") != worldpack.content_hash:
        raise RenderPackError("The renderpack was built for different world content")

    root = renderpack_path.parent.resolve()
    raw_assets = raw.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise RenderPackError("The renderpack must contain assets")
    assets: list[RenderAsset] = []
    assets_by_id: dict[str, RenderAsset] = {}
    for asset_index, raw_asset in enumerate(raw_assets):
        context = f"assets/{asset_index}"
        if not isinstance(raw_asset, dict):
            raise RenderPackError(f"{context} must be an object")
        asset_id = raw_asset.get("id")
        if not isinstance(asset_id, str) or not ID_PATTERN.fullmatch(asset_id):
            raise RenderPackError(f"{context}/id is invalid")
        if asset_id in assets_by_id:
            raise RenderPackError(f"{context}/id is duplicated")
        kind = raw_asset.get("kind")
        if not isinstance(kind, str) or kind not in ASSET_KINDS:
            raise RenderPackError(f"{context}/kind is invalid")
        raw_files = raw_asset.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise RenderPackError(f"{context}/files must be a non-empty list")
        files: list[AssetFile] = []
        seen_paths: set[str] = set()
        clips: tuple[AnimationClip, ...] = ()
        for file_index, raw_file in enumerate(raw_files):
            file_context = f"{context}/files/{file_index}"
            if not isinstance(raw_file, dict):
                raise RenderPackError(f"{file_context} must be an object")
            role = raw_file.get("role")
            if not isinstance(role, str) or role not in OUTPUT_ROLES:
                raise RenderPackError(f"{file_context}/role is invalid")
            relative = raw_file.get("path")
            resolved = _safe_file(root, relative)
            if relative in seen_paths:
                raise RenderPackError(f"{file_context}/path is duplicated")
            seen_paths.add(relative)
            sha256 = raw_file.get("sha256")
            if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
                raise RenderPackError(f"{file_context}/sha256 is invalid")
            if _sha256_file(resolved) != sha256:
                raise RenderPackError(f"{file_context}/sha256 does not match the file")
            media_type = raw_file.get("media_type")
            if not isinstance(media_type, str) or media_type not in ROLE_MEDIA_TYPES[role]:
                raise RenderPackError(f"{file_context}/media_type is invalid")
            files.append(AssetFile(role, relative, sha256, media_type))
            if role == "clipset":
                if clips:
                    raise RenderPackError(f"{context} contains multiple clipsets")
                clips = load_clipset(resolved)
        asset = RenderAsset(asset_id, kind, tuple(files), clips)
        roles = {item.role for item in files}
        for role in roles:
            if role != "audio" and sum(item.role == role for item in files) > 1:
                raise RenderPackError(f"{context} contains multiple {role} files")
        if kind in VISUAL_KINDS and "texture" not in roles:
            raise RenderPackError(f"{context} visual asset has no texture")
        if kind in {"spritesheet", "tileset"} and "clipset" not in roles:
            raise RenderPackError(f"{context} has no clipset")
        if kind in {"music", "sfx"} and "audio" not in roles:
            raise RenderPackError(f"{context} has no audio output")
        if kind == "font" and "font" not in roles:
            raise RenderPackError(f"{context} has no font output")
        if kind == "shader" and not ({"vertex_shader", "fragment_shader"} & roles):
            raise RenderPackError(f"{context} has no shader output")
        assets.append(asset)
        assets_by_id[asset_id] = asset

    raw_bindings = raw.get("bindings")
    if not isinstance(raw_bindings, list):
        raise RenderPackError("bindings must be a list")
    bindings: list[RenderBinding] = []
    seen_slots: set[str] = set()
    for binding_index, raw_binding in enumerate(raw_bindings):
        context = f"bindings/{binding_index}"
        if not isinstance(raw_binding, dict):
            raise RenderPackError(f"{context} must be an object")
        slot = raw_binding.get("slot")
        if not isinstance(slot, str) or not SLOT_PATTERN.fullmatch(slot):
            raise RenderPackError(f"{context}/slot is invalid")
        if slot in seen_slots:
            raise RenderPackError(f"{context}/slot is duplicated")
        seen_slots.add(slot)
        asset_id = raw_binding.get("asset_id")
        asset = assets_by_id.get(asset_id) if isinstance(asset_id, str) else None
        if asset is None:
            raise RenderPackError(f"{context}/asset_id is unknown")
        if not _binding_kind_is_compatible(slot, asset.kind):
            raise RenderPackError(f"{context}/asset_id has an incompatible kind")
        clip = raw_binding.get("clip")
        moving_clip = raw_binding.get("moving_clip")
        available_clips = {item.id for item in asset.clips}
        for field, clip_id in (("clip", clip), ("moving_clip", moving_clip)):
            if clip_id is not None and (
                not isinstance(clip_id, str) or clip_id not in available_clips
            ):
                raise RenderPackError(f"{context}/{field} is unknown")
        scale = _number(raw_binding.get("scale", 1.0), f"{context}/scale")
        if not 0 < scale <= 16:
            raise RenderPackError(f"{context}/scale must be in the range (0, 16]")
        layer = raw_binding.get("layer", 0)
        if isinstance(layer, bool) or not isinstance(layer, int) or not -1000 <= layer <= 1000:
            raise RenderPackError(f"{context}/layer must be an integer in -1000..1000")
        bindings.append(RenderBinding(slot, asset_id, clip, moving_clip, scale, layer))
    return RenderPack(
        world_id=worldpack.world_id,
        world_content_hash=worldpack.content_hash,
        content_hash=supplied_hash,
        root=root,
        assets=tuple(assets),
        bindings=tuple(bindings),
    )
