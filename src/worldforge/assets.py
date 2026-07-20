from __future__ import annotations

import hashlib
import json
import re
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isoworld.content.media import media_signature_matches
from isoworld.content.renderpack import RenderPackError, load_clipset
from worldforge.integrity import declared_hash_matches
from worldforge.validation import ID_PATTERN, PLACEHOLDER_PATTERN

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
ASSET_STATUSES = {"planned", "generated", "approved", "processed"}
ASSET_ORIGINS = {
    "codex_assisted",
    "gpt_image",
    "human",
    "local_model",
    "procedural",
    "third_party",
}
AI_ORIGINS = {"codex_assisted", "gpt_image", "local_model"}
GENERATION_ROUTES = {"openai", "modly"}
ASSET_PHASES = {"art_direction", "production", "release"}
OUTPUT_ROLES = {
    "audio",
    "clipset",
    "font",
    "fragment_shader",
    "texture",
    "vertex_shader",
}
MEDIA_TYPES = {
    "application/json",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "font/otf",
    "font/ttf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/x-glsl",
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
MEDIA_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "font/otf": "otf",
    "font/ttf": "ttf",
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "text/x-glsl": "glsl",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
MAX_JSON_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AssetIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class AssetManifestError(ValueError):
    """Raised when an asset manifest cannot be read or initialized."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise AssetManifestError(f"{path} exceeds the {MAX_JSON_BYTES}-byte JSON limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except AssetManifestError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetManifestError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetManifestError(f"{path} must contain a JSON object")
    return value


def _resolve_inside(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _walk_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}/{index}")


def _verified_worldpack(path: Path) -> dict[str, Any]:
    pack = _read_json(path)
    format_version = pack.get("format_version")
    if (
        pack.get("format") != "isoworld.worldpack"
        or isinstance(format_version, bool)
        or not isinstance(format_version, int)
        or format_version not in {1, 2, 3, 4, 5}
    ):
        raise AssetManifestError("The input file is not a compatible worldpack")
    content_hash = pack.get("content_hash")
    if not isinstance(content_hash, str) or not SHA256_PATTERN.fullmatch(content_hash):
        raise AssetManifestError("The worldpack does not contain a valid hash")
    if not declared_hash_matches(pack):
        raise AssetManifestError("The worldpack content hash does not match its contents")
    world = pack.get("world")
    world_id = world.get("id") if isinstance(world, dict) else None
    if not isinstance(world_id, str) or not ID_PATTERN.fullmatch(world_id):
        raise AssetManifestError("The worldpack does not contain a valid world ID")
    return pack


def init_asset_manifest(
    worldpack_path: str | Path,
    output_path: str | Path,
    *,
    target_dimension: str | None = None,
    target_id: str = "primary",
    enable_modly: bool = False,
) -> dict[str, Any]:
    if target_dimension is not None:
        from worldforge.asset_manifest_v3 import init_asset_manifest_v3

        return init_asset_manifest_v3(
            worldpack_path,
            output_path,
            target_id=target_id,
            dimension=target_dimension,
            enable_modly=enable_modly,
        )
    if enable_modly:
        raise AssetManifestError("--enable-modly requires --target-dimension and manifest v3")
    pack = _verified_worldpack(Path(worldpack_path))
    from worldforge.asset_io import prepare_output_path, write_json_atomic

    output = prepare_output_path(output_path)
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise AssetManifestError(f"The asset manifest already exists: {output}")
    for directory in ("specs", "generated", "processed", "references", "recipes", "qa"):
        prepare_output_path(output.parent / directory / ".directory-probe")

    manifest: dict[str, Any] = {
        "format": "rpg-world-forge.asset_manifest",
        "format_version": 2,
        "world_id": pack["world"]["id"],
        "world_content_hash": pack["content_hash"],
        "phase": "art_direction",
        "generation_policy": {
            "enabled_routes": ["openai"],
            "local_model_route": "modly",
        },
        "assets": [],
        "bindings": [],
    }
    write_json_atomic(output, manifest)
    return manifest


def _validate_specification(
    specification: Path,
    *,
    asset_id: Any,
    kind: Any,
    issue_path: str,
) -> list[AssetIssue]:
    issues: list[AssetIssue] = []
    try:
        raw = _read_json(specification)
    except AssetManifestError as exc:
        return [AssetIssue(issue_path, str(exc))]
    if raw.get("format") != "rpg-world-forge.asset_spec":
        issues.append(AssetIssue(issue_path, "unknown asset-spec format"))
    if raw.get("format_version") != 1:
        issues.append(AssetIssue(issue_path, "unsupported asset-spec version"))
    if raw.get("id") != asset_id:
        issues.append(AssetIssue(issue_path, "asset-spec ID does not match the asset"))
    if raw.get("kind") != kind:
        issues.append(AssetIssue(issue_path, "asset-spec kind does not match the asset"))
    if not isinstance(raw.get("purpose"), str) or not raw["purpose"].strip():
        issues.append(AssetIssue(issue_path, "asset-spec purpose is required"))
    criteria = raw.get("acceptance_criteria")
    if (
        not isinstance(criteria, list)
        or not criteria
        or not all(isinstance(item, str) and item.strip() for item in criteria)
    ):
        issues.append(AssetIssue(issue_path, "asset-spec acceptance criteria are required"))
    technical = raw.get("technical")
    if not isinstance(technical, dict):
        issues.append(AssetIssue(issue_path, "asset-spec technical contract is required"))
        return issues
    runtime_format = technical.get("runtime_format")
    if not isinstance(runtime_format, str) or not runtime_format.strip():
        issues.append(AssetIssue(issue_path, "technical runtime_format is required"))
    budget = technical.get("memory_budget_bytes")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        issues.append(AssetIssue(issue_path, "positive technical memory_budget_bytes is required"))
    if kind in VISUAL_KINDS:
        for field in ("width", "height"):
            value = technical.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                issues.append(AssetIssue(issue_path, f"positive technical {field} is required"))
    if kind in {"music", "sfx"}:
        sample_rate = technical.get("sample_rate")
        channels = technical.get("channels")
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or not 8000 <= sample_rate <= 192000
        ):
            issues.append(AssetIssue(issue_path, "technical sample_rate must be in 8000..192000"))
        if isinstance(channels, bool) or not isinstance(channels, int) or channels not in {1, 2}:
            issues.append(AssetIssue(issue_path, "technical channels must be 1 or 2"))
    return issues


def _media_signature_matches(path: Path, media_type: str) -> bool:
    return media_signature_matches(path, media_type)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as source:
            header = source.read(24)
    except OSError:
        return None
    if not header.startswith(b"\x89PNG\r\n\x1a\n") or len(header) < 24:
        return None
    return struct.unpack(">II", header[16:24])


def _wav_properties(path: Path) -> tuple[int, int] | None:
    try:
        with wave.open(str(path), "rb") as source:
            return source.getframerate(), source.getnchannels()
    except (OSError, EOFError, wave.Error):
        return None


def _validate_outputs(
    asset: dict[str, Any],
    *,
    root: Path,
    item_path: str,
    required: bool,
    technical: dict[str, Any],
) -> list[AssetIssue]:
    outputs = asset.get("outputs")
    issues: list[AssetIssue] = []
    if outputs is None and not required:
        return issues
    if not isinstance(outputs, list):
        return [AssetIssue(f"{item_path}/outputs", "must be a list")]
    if required and not outputs:
        issues.append(AssetIssue(f"{item_path}/outputs", "processed assets require outputs"))
    seen_paths: set[str] = set()
    roles: set[str] = set()
    role_counts: dict[str, int] = {}
    total_size = 0
    for output_index, output in enumerate(outputs):
        output_path = f"{item_path}/outputs/{output_index}"
        if not isinstance(output, dict):
            issues.append(AssetIssue(output_path, "must be an object"))
            continue
        role = output.get("role")
        if not isinstance(role, str) or role not in OUTPUT_ROLES:
            issues.append(AssetIssue(f"{output_path}/role", "unknown output role"))
        else:
            roles.add(role)
            role_counts[role] = role_counts.get(role, 0) + 1
        media_type = output.get("media_type")
        if not isinstance(media_type, str) or media_type not in MEDIA_TYPES:
            issues.append(AssetIssue(f"{output_path}/media_type", "unknown media type"))
        elif (
            isinstance(role, str)
            and role in ROLE_MEDIA_TYPES
            and media_type not in ROLE_MEDIA_TYPES[role]
        ):
            issues.append(
                AssetIssue(
                    f"{output_path}/media_type",
                    f"media type {media_type} is incompatible with the {role} role",
                )
            )
        elif role != "clipset":
            expected_format = technical.get("runtime_format")
            actual_format = MEDIA_FORMATS.get(media_type)
            if expected_format and actual_format and expected_format != actual_format:
                issues.append(
                    AssetIssue(
                        f"{output_path}/media_type",
                        f"format {actual_format} does not match specification {expected_format}",
                    )
                )
        relative = output.get("runtime_file")
        runtime_file = _resolve_inside(root, relative)
        if not isinstance(relative, str) or relative in seen_paths:
            issues.append(AssetIssue(f"{output_path}/runtime_file", "missing or duplicate path"))
        else:
            seen_paths.add(relative)
        if runtime_file is None or not runtime_file.is_file():
            issues.append(AssetIssue(f"{output_path}/runtime_file", "processed file is missing"))
        else:
            total_size += runtime_file.stat().st_size
        expected_hash = output.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
            issues.append(AssetIssue(f"{output_path}/sha256", "invalid SHA-256 hash"))
        elif runtime_file is not None and runtime_file.is_file():
            actual = _sha256_file(runtime_file)
            if actual != expected_hash:
                issues.append(AssetIssue(f"{output_path}/sha256", "does not match the file"))
        if (
            runtime_file is not None
            and runtime_file.is_file()
            and isinstance(media_type, str)
            and media_type in MEDIA_TYPES
            and not _media_signature_matches(runtime_file, media_type)
        ):
            issues.append(
                AssetIssue(f"{output_path}/media_type", "does not match the file contents")
            )
        if role == "clipset" and runtime_file is not None and runtime_file.is_file():
            try:
                load_clipset(runtime_file)
            except RenderPackError as exc:
                issues.append(AssetIssue(f"{output_path}/runtime_file", str(exc)))
        if role == "texture" and media_type == "image/png" and runtime_file is not None:
            dimensions = _png_dimensions(runtime_file)
            expected = (technical.get("width"), technical.get("height"))
            if dimensions is not None and dimensions != expected:
                issues.append(
                    AssetIssue(
                        f"{output_path}/runtime_file",
                        f"PNG dimensions {dimensions} do not match specification {expected}",
                    )
                )
        if role == "audio" and media_type == "audio/wav" and runtime_file is not None:
            properties = _wav_properties(runtime_file)
            expected = (technical.get("sample_rate"), technical.get("channels"))
            if properties is not None and properties != expected:
                issues.append(
                    AssetIssue(
                        f"{output_path}/runtime_file",
                        f"WAV properties {properties} do not match specification {expected}",
                    )
                )

    kind = asset.get("kind")
    if required:
        for role, count in sorted(role_counts.items()):
            if role != "audio" and count > 1:
                issues.append(
                    AssetIssue(
                        f"{item_path}/outputs",
                        f"the {role} role may appear only once",
                    )
                )
        if kind in VISUAL_KINDS and "texture" not in roles:
            issues.append(AssetIssue(f"{item_path}/outputs", "visual assets require a texture"))
        if kind in {"spritesheet", "tileset"} and "clipset" not in roles:
            issues.append(AssetIssue(f"{item_path}/outputs", f"{kind} assets require a clipset"))
        if kind in {"music", "sfx"} and "audio" not in roles:
            issues.append(
                AssetIssue(f"{item_path}/outputs", "audio assets require an audio output")
            )
        if kind == "font" and "font" not in roles:
            issues.append(AssetIssue(f"{item_path}/outputs", "font assets require a font output"))
        if kind == "shader" and not ({"vertex_shader", "fragment_shader"} & roles):
            issues.append(AssetIssue(f"{item_path}/outputs", "shader assets require shader output"))
        budget = technical.get("memory_budget_bytes")
        if isinstance(budget, int) and not isinstance(budget, bool) and total_size > budget:
            issues.append(
                AssetIssue(
                    f"{item_path}/outputs",
                    f"processed files use {total_size} bytes and exceed the {budget}-byte budget",
                )
            )
    return issues


def _validate_provenance(
    asset: dict[str, Any],
    *,
    root: Path,
    item_path: str,
    required: bool,
    enabled_routes: set[str],
) -> list[AssetIssue]:
    if not required:
        return []
    provenance = asset.get("provenance")
    if not isinstance(provenance, dict):
        return [AssetIssue(f"{item_path}/provenance", "provenance is required")]
    issues: list[AssetIssue] = []
    origin = provenance.get("origin")
    if not isinstance(origin, str) or origin not in ASSET_ORIGINS:
        issues.append(AssetIssue(f"{item_path}/provenance/origin", "unknown origin"))
    if isinstance(origin, str) and origin in AI_ORIGINS:
        for field in ("model_id", "model_version", "recipe_file", "generation_route"):
            if not provenance.get(field):
                issues.append(
                    AssetIssue(
                        f"{item_path}/provenance/{field}",
                        "required for assisted generation",
                    )
                )
        route = provenance.get("generation_route")
        if not isinstance(route, str) or route not in GENERATION_ROUTES:
            issues.append(
                AssetIssue(f"{item_path}/provenance/generation_route", "unknown generation route")
            )
        elif route not in enabled_routes:
            issues.append(
                AssetIssue(
                    f"{item_path}/provenance/generation_route",
                    "generation route is disabled by the manifest policy",
                )
            )
        if origin in {"codex_assisted", "gpt_image"} and route != "openai":
            issues.append(
                AssetIssue(
                    f"{item_path}/provenance/generation_route",
                    "OpenAI-assisted origins require the openai route",
                )
            )
        if origin == "local_model" and route != "modly":
            issues.append(
                AssetIssue(
                    f"{item_path}/provenance/generation_route",
                    "local models must run through the modly route",
                )
            )
        recipe = _resolve_inside(root, provenance.get("recipe_file"))
        if recipe is None:
            issues.append(AssetIssue(f"{item_path}/provenance/recipe_file", "unsafe path"))
        elif not recipe.is_file():
            issues.append(AssetIssue(f"{item_path}/provenance/recipe_file", "file does not exist"))
        if origin == "local_model" or route == "modly":
            for field in ("extension_id", "extension_version", "workflow_file"):
                if not provenance.get(field):
                    issues.append(
                        AssetIssue(
                            f"{item_path}/provenance/{field}",
                            "required for the Modly route",
                        )
                    )
            workflow = _resolve_inside(root, provenance.get("workflow_file"))
            if workflow is None:
                issues.append(AssetIssue(f"{item_path}/provenance/workflow_file", "unsafe path"))
            elif not workflow.is_file():
                issues.append(
                    AssetIssue(f"{item_path}/provenance/workflow_file", "file does not exist")
                )
    return issues


def _clip_ids(root: Path, asset: dict[str, Any]) -> set[str]:
    for output in asset.get("outputs", []):
        if isinstance(output, dict) and output.get("role") == "clipset":
            path = _resolve_inside(root, output.get("runtime_file"))
            if path is None or not path.is_file():
                return set()
            try:
                raw = _read_json(path)
            except AssetManifestError:
                return set()
            clips = raw.get("clips")
            if isinstance(clips, list):
                return {
                    item["id"]
                    for item in clips
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and ID_PATTERN.fullmatch(item["id"])
                }
    return set()


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
    if category == "construction":
        return kind in {"sprite", "spritesheet", "tileset"}
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


def _validate_bindings(
    raw: dict[str, Any],
    *,
    root: Path,
    assets_by_id: dict[str, dict[str, Any]],
    profile: str,
    worldpack: dict[str, Any] | None,
) -> list[AssetIssue]:
    bindings = raw.get("bindings")
    if not isinstance(bindings, list):
        return [AssetIssue("bindings", "must be a list")]
    issues: list[AssetIssue] = []
    seen: set[str] = set()
    bound_slots: set[str] = set()
    for index, binding in enumerate(bindings):
        item_path = f"bindings/{index}"
        if not isinstance(binding, dict):
            issues.append(AssetIssue(item_path, "must be an object"))
            continue
        slot = binding.get("slot")
        if not isinstance(slot, str) or not SLOT_PATTERN.fullmatch(slot):
            issues.append(AssetIssue(f"{item_path}/slot", "invalid semantic slot"))
            continue
        if slot in seen:
            issues.append(AssetIssue(f"{item_path}/slot", f"duplicate slot: {slot}"))
        seen.add(slot)
        bound_slots.add(slot)
        asset_id = binding.get("asset_id")
        asset = assets_by_id.get(asset_id) if isinstance(asset_id, str) else None
        if asset is None:
            issues.append(AssetIssue(f"{item_path}/asset_id", "unknown asset"))
            continue
        if not _binding_kind_is_compatible(slot, asset["kind"]):
            issues.append(AssetIssue(f"{item_path}/asset_id", "asset kind is incompatible"))
        scale = binding.get("scale", 1.0)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 16:
            issues.append(AssetIssue(f"{item_path}/scale", "must be in the range (0, 16]"))
        layer = binding.get("layer", 0)
        if isinstance(layer, bool) or not isinstance(layer, int) or not -1000 <= layer <= 1000:
            issues.append(AssetIssue(f"{item_path}/layer", "must be an integer in -1000..1000"))
        clips = _clip_ids(root, asset)
        for field in ("clip", "moving_clip"):
            clip_id = binding.get(field)
            if clip_id is not None and (not isinstance(clip_id, str) or clip_id not in clips):
                issues.append(AssetIssue(f"{item_path}/{field}", "unknown clip"))
        if slot.split(":", 1)[0] in {
            "actor",
            "tile_type",
            "interaction",
            "construction",
            "portrait",
        }:
            if not binding.get("clip"):
                issues.append(AssetIssue(f"{item_path}/clip", "visual binding requires a clip"))

    if profile == "release" and worldpack is not None:
        collections = worldpack.get("collections", {})
        for category in ("actors", "tile_types"):
            prefix = "actor" if category == "actors" else "tile_type"
            for item in collections.get(category, []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    slot = f"{prefix}:{item['id']}"
                    if slot not in bound_slots:
                        issues.append(
                            AssetIssue("bindings", f"release is missing required slot {slot}")
                        )
    return issues


def validate_asset_manifest(
    manifest_path: str | Path,
    *,
    profile: str = "draft",
    worldpack_path: str | Path | None = None,
) -> list[AssetIssue]:
    if profile not in {"build", "draft", "release"}:
        raise AssetManifestError("profile must be build, draft, or release")
    if profile == "build":
        path = Path(manifest_path)
        raw = _read_json(path)
        if raw.get("format_version") != 3:
            raise AssetManifestError("the build profile is only valid for asset manifest v3")
    path = Path(manifest_path)
    raw = _read_json(path)
    root = path.parent.resolve()
    issues: list[AssetIssue] = []

    if raw.get("format_version") == 3:
        from worldforge.asset_manifest_v3 import validate_asset_manifest_v3

        return validate_asset_manifest_v3(
            path,
            profile=profile,
            worldpack_path=worldpack_path,
        )

    if raw.get("format") != "rpg-world-forge.asset_manifest":
        issues.append(AssetIssue("format", "unknown format"))
    version = raw.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
        issues.append(AssetIssue("format_version", "unsupported version"))
    phase = raw.get("phase")
    if version == 2 and (not isinstance(phase, str) or phase not in ASSET_PHASES):
        issues.append(AssetIssue("phase", "unknown asset-production phase"))
    if version == 2 and profile == "release" and raw.get("phase") != "release":
        issues.append(AssetIssue("phase", "release validation requires the release phase"))
    enabled_routes = set(GENERATION_ROUTES)
    if version == 2:
        policy = raw.get("generation_policy")
        if not isinstance(policy, dict):
            issues.append(AssetIssue("generation_policy", "must be an object"))
        else:
            configured = policy.get("enabled_routes")
            if (
                not isinstance(configured, list)
                or not configured
                or not all(
                    isinstance(item, str) and item in GENERATION_ROUTES for item in configured
                )
                or len(set(configured)) != len(configured)
            ):
                issues.append(
                    AssetIssue(
                        "generation_policy/enabled_routes",
                        "must contain unique openai and/or modly routes",
                    )
                )
            else:
                enabled_routes = set(configured)
            if policy.get("local_model_route") != "modly":
                issues.append(
                    AssetIssue(
                        "generation_policy/local_model_route",
                        "local models must be restricted to modly",
                    )
                )
    if not isinstance(raw.get("world_id"), str) or not ID_PATTERN.fullmatch(raw["world_id"]):
        issues.append(AssetIssue("world_id", "invalid world ID"))
    content_hash = raw.get("world_content_hash")
    if not isinstance(content_hash, str) or not SHA256_PATTERN.fullmatch(content_hash):
        issues.append(AssetIssue("world_content_hash", "invalid SHA-256 hash"))

    worldpack: dict[str, Any] | None = None
    if worldpack_path is not None:
        try:
            worldpack = _verified_worldpack(Path(worldpack_path))
        except AssetManifestError as exc:
            issues.append(AssetIssue("worldpack", str(exc)))
        else:
            if worldpack["world"]["id"] != raw.get("world_id"):
                issues.append(AssetIssue("world_id", "does not match the worldpack"))
            if worldpack["content_hash"] != content_hash:
                issues.append(
                    AssetIssue(
                        "world_content_hash",
                        "canon changed; restart or migrate the asset plan",
                    )
                )

    assets = raw.get("assets")
    if not isinstance(assets, list):
        return issues + [AssetIssue("assets", "must be a list")]
    if profile == "release" and not assets:
        issues.append(AssetIssue("assets", "a release must contain assets"))

    seen: set[str] = set()
    assets_by_id: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(assets):
        item_path = f"assets/{index}"
        if not isinstance(asset, dict):
            issues.append(AssetIssue(item_path, "must be an object"))
            continue
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not ID_PATTERN.fullmatch(asset_id):
            issues.append(AssetIssue(f"{item_path}/id", "invalid ID"))
        elif asset_id in seen:
            issues.append(AssetIssue(f"{item_path}/id", f"duplicate ID: {asset_id}"))
        else:
            seen.add(asset_id)
            assets_by_id[asset_id] = asset
        kind = asset.get("kind")
        if not isinstance(kind, str) or kind not in ASSET_KINDS:
            issues.append(AssetIssue(f"{item_path}/kind", "unknown asset kind"))
        status = asset.get("status")
        if not isinstance(status, str) or status not in ASSET_STATUSES:
            issues.append(AssetIssue(f"{item_path}/status", "unknown status"))
        if profile == "release" and status != "processed":
            issues.append(AssetIssue(f"{item_path}/status", "release requires processed status"))

        specification = _resolve_inside(root, asset.get("specification_file"))
        technical: dict[str, Any] = {}
        if specification is None:
            issues.append(AssetIssue(f"{item_path}/specification_file", "unsafe or missing path"))
        elif not specification.is_file():
            issues.append(AssetIssue(f"{item_path}/specification_file", "file does not exist"))
        elif version == 2:
            issues.extend(
                _validate_specification(
                    specification,
                    asset_id=asset_id,
                    kind=kind,
                    issue_path=f"{item_path}/specification_file",
                )
            )
            try:
                specification_data = _read_json(specification)
            except AssetManifestError:
                pass
            else:
                if isinstance(specification_data.get("technical"), dict):
                    technical = specification_data["technical"]

        provenance_required = (
            isinstance(status, str) and status in {"generated", "approved", "processed"}
        ) or profile == "release"
        issues.extend(
            _validate_provenance(
                asset,
                root=root,
                item_path=item_path,
                required=provenance_required,
                enabled_routes=enabled_routes,
            )
        )

        if (
            isinstance(status, str) and status in {"approved", "processed"}
        ) or profile == "release":
            license_data = asset.get("license")
            if not isinstance(license_data, dict):
                issues.append(AssetIssue(f"{item_path}/license", "license record is required"))
            else:
                for field in (
                    "asset_license",
                    "source_license",
                    "model_license",
                    "weights_license",
                    "dataset_license",
                ):
                    if not license_data.get(field):
                        issues.append(
                            AssetIssue(f"{item_path}/license/{field}", "value is required")
                        )
            if not asset.get("approved_by"):
                issues.append(
                    AssetIssue(f"{item_path}/approved_by", "authorized approval is required")
                )

        references = asset.get("references", [])
        if not isinstance(references, list):
            issues.append(AssetIssue(f"{item_path}/references", "must be a list"))
        else:
            for reference_index, reference in enumerate(references):
                reference_path = f"{item_path}/references/{reference_index}"
                if not isinstance(reference, dict):
                    issues.append(AssetIssue(reference_path, "must be an object"))
                    continue
                source = _resolve_inside(root, reference.get("file"))
                if source is None or not source.is_file():
                    issues.append(AssetIssue(f"{reference_path}/file", "reference is missing"))
                for field in ("permission", "license"):
                    if not isinstance(reference.get(field), str) or not reference[field].strip():
                        issues.append(AssetIssue(f"{reference_path}/{field}", "value is required"))

        processed_required = status == "processed" or profile == "release"
        if processed_required and version == 2:
            qa = asset.get("qa")
            if not isinstance(qa, dict):
                issues.append(AssetIssue(f"{item_path}/qa", "QA evidence is required"))
            else:
                report = _resolve_inside(root, qa.get("report_file"))
                if report is None or not report.is_file() or report.stat().st_size == 0:
                    issues.append(AssetIssue(f"{item_path}/qa/report_file", "QA report is missing"))
                if qa.get("in_engine_passed") is not True:
                    issues.append(AssetIssue(f"{item_path}/qa/in_engine_passed", "must be true"))
                if qa.get("raylib_load_passed") is not True:
                    issues.append(AssetIssue(f"{item_path}/qa/raylib_load_passed", "must be true"))
        if version == 2:
            issues.extend(
                _validate_outputs(
                    asset,
                    root=root,
                    item_path=item_path,
                    required=processed_required,
                    technical=technical,
                )
            )
        elif processed_required:
            runtime_file = _resolve_inside(root, asset.get("runtime_file"))
            if runtime_file is None or not runtime_file.is_file():
                issues.append(AssetIssue(f"{item_path}/runtime_file", "processed file is missing"))
            expected_hash = asset.get("sha256")
            if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
                issues.append(AssetIssue(f"{item_path}/sha256", "invalid SHA-256 hash"))
            elif runtime_file is not None and runtime_file.is_file():
                actual = _sha256_file(runtime_file)
                if actual != expected_hash:
                    issues.append(AssetIssue(f"{item_path}/sha256", "does not match the file"))

        for value_path, value in _walk_strings(asset, item_path):
            if PLACEHOLDER_PATTERN.search(value):
                issues.append(AssetIssue(value_path, "unresolved placeholder"))

    if version == 2:
        issues.extend(
            _validate_bindings(
                raw,
                root=root,
                assets_by_id=assets_by_id,
                profile=profile,
                worldpack=worldpack,
            )
        )
    return issues
