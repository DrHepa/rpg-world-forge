from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.asset_contracts import (
    ASSET_KINDS,
    GLB_OUTPUT_ROLES,
    KIND_REPRESENTATIONS,
    OUTPUT_ROLE_MEDIA,
    REPRESENTATIONS,
    runtime_output_contract_issue,
)
from isoworld.content.file_stat import is_link_or_reparse, path_file_stat
from isoworld.content.gltf import MAX_GLB_BYTES, METRIC_NAMES, GLBError, inspect_glb
from isoworld.content.media import (
    MAX_MEDIA_BYTES,
    MediaValidationError,
)
from isoworld.content.models import WorldPack
from isoworld.content.portability import portable_path_key, portable_relative_path
from isoworld.content.resource_snapshot import (
    MAX_OWNED_RESOURCE_BYTES,
    ResourceSnapshotError,
    ResourceSnapshotOwner,
    note_cleanup_failure,
)
from isoworld.runtime_io import MAX_JSON_BYTES, RuntimeIOError, decode_json_object

ASSETPACK_FORMAT = "rpg-world-forge.assetpack"
ASSETPACK_FORMAT_VERSION = 1
MAX_ASSETPACK_BYTES = MAX_JSON_BYTES
MAX_ASSETS = 100_000
MAX_BINDINGS = 100_000
MAX_FILES = 100_000
MAX_FILES_PER_ASSET = 256
MAX_TOTAL_RESOURCE_BYTES = MAX_OWNED_RESOURCE_BYTES
MAX_TREE_DEPTH = 64
MAX_TREE_DIRECTORIES = MAX_FILES * 4
MAX_TREE_NODES = MAX_FILES * 5
MAX_TREE_PATH_BYTES = 1024

ID_PATTERN = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "format_version",
        "world_id",
        "world_content_hash",
        "target_id",
        "target_hash",
        "dimension",
        "delivery_profile",
        "coordinate_system",
        "assets",
        "bindings",
        "content_hash",
    }
)
_COORDINATE_KEYS = frozenset({"handedness", "up_axis", "forward_axis", "units_per_meter"})
_ASSET_KEYS = frozenset({"id", "kind", "representation", "files", "metrics"})
_FILE_KEYS = frozenset({"role", "path", "sha256", "size", "media_type"})
_BINDING_KEYS = frozenset({"slot", "asset_id", "representation", "entrypoint"})
_ENTRYPOINT_KEYS = frozenset({"node", "default_animation", "moving_animation", "scale", "layer"})
_ROLE_MEDIA_TYPES = {
    role: frozenset(media_types) for role, media_types in OUTPUT_ROLE_MEDIA.items()
}
_MEDIA_EXTENSIONS = {
    "application/json": frozenset({".json"}),
    "audio/mpeg": frozenset({".mp3"}),
    "audio/ogg": frozenset({".ogg"}),
    "audio/wav": frozenset({".wav"}),
    "font/otf": frozenset({".otf"}),
    "font/ttf": frozenset({".ttf"}),
    "image/jpeg": frozenset({".jpeg", ".jpg"}),
    "image/png": frozenset({".png"}),
    "image/webp": frozenset({".webp"}),
    "model/gltf-binary": frozenset({".glb"}),
    "text/x-glsl": frozenset({".frag", ".glsl", ".vert"}),
}
_GLB_REQUIRED_METRICS = {
    "animation": "animations",
    "collision": "meshes",
    "model": "meshes",
    "skeleton": "skins",
}
_METRIC_MAXIMUMS = {
    "triangles": 1_000_000_000,
    "vertices": 1_000_000_000,
    "materials": 100_000,
    "textures": 100_000,
    "nodes": 1_000_000,
    "meshes": 1_000_000,
    "skins": 100_000,
    "bones": 1_000_000,
    "influences": 1024,
    "animations": 100_000,
    "external_uris": 0,
}
_MISSING_ANIMATION_NAMES = "GLB is missing required animations names:"
_FORBIDDEN_JSON_KEYS = frozenset(
    {
        "api_key",
        "auth_token",
        "authorization",
        "credential",
        "executor",
        "mcp",
        "mcp_endpoint",
        "mcp_server",
        "mcp_servers",
        "orchestrator",
        "provider",
        "provider_id",
        "provider_name",
        "providers",
        "secret",
        "token",
        "weights",
        "weights_file",
        "weights_hash",
        "workflow",
        "workflow_file",
        "workflow_hash",
        "workflow_id",
    }
)
_CREDENTIAL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_AUTHORING_VALUE_KEYS = frozenset(
    {
        "adapter",
        "backend",
        "client",
        "engine",
        "executor",
        "generator",
        "integration",
        "model",
        "orchestrator",
        "provider",
        "service",
        "tool",
        "transport",
    }
)
_AUTHORING_VALUE_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    r"anthropic|blender[-_]mcp|cohere|diffusers|google[-_]genai|"
    r"huggingface|langchain|litellm|modly(?:[-_]cli[-_]mcp)?|"
    r"ollama|openai|sentence[-_]transformers|transformers|vertexai"
    r")(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
)
_FORBIDDEN_WEIGHT_SUFFIXES = frozenset({".ckpt", ".gguf", ".pt", ".pth", ".safetensors"})


class AssetPackError(ValueError):
    """Raised when an engine-neutral runtime assetpack is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class AssetPack:
    world_id: str
    world_content_hash: str
    target_id: str
    target_hash: str
    content_hash: str
    root: Path
    document: dict[str, Any] = dataclass_field(repr=False, compare=False)
    _snapshot_owner: ResourceSnapshotOwner = dataclass_field(repr=False, compare=False)

    def resolve_file(self, path: str) -> Path:
        normalized = portable_relative_path(path)
        if normalized is None:
            raise AssetPackError("assetpack resource path is not portable and canonical")
        try:
            return self._snapshot_owner.resolve_file(normalized)
        except ResourceSnapshotError as exc:
            raise AssetPackError(str(exc)) from exc

    def close(self) -> None:
        try:
            self._snapshot_owner.close()
        except ResourceSnapshotError as exc:
            raise AssetPackError(str(exc)) from exc

    def __enter__(self) -> AssetPack:
        if self._snapshot_owner.closed:
            raise AssetPackError("assetpack snapshot is already closed")
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        try:
            self.close()
        except AssetPackError as cleanup_error:
            if not note_cleanup_failure(
                exc,
                cleanup_error,
                context="assetpack snapshot cleanup",
            ):
                raise


def _exact(value: object, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetPackError(f"{context} must be an object")
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing:
        raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    return value


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise AssetPackError(f"{context} must be a portable ID")
    return value


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise AssetPackError(f"{context} must be a lowercase SHA-256")
    return value


def _canonical_hash(document: dict[str, Any]) -> str:
    payload = dict(document)
    payload.pop("content_hash", None)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coordinate_system(value: object) -> None:
    raw = _exact(value, _COORDINATE_KEYS, "assetpack.coordinate_system")
    if raw["handedness"] != "right":
        raise AssetPackError("assetpack.coordinate_system.handedness must be right")
    if raw["up_axis"] != "Y":
        raise AssetPackError("assetpack.coordinate_system.up_axis must be Y")
    if raw["forward_axis"] != "-Z":
        raise AssetPackError("assetpack.coordinate_system.forward_axis must be -Z")
    units = raw["units_per_meter"]
    if isinstance(units, bool) or not isinstance(units, (int, float)):
        raise AssetPackError("assetpack.coordinate_system.units_per_meter must be numeric")
    try:
        finite = math.isfinite(float(units))
    except OverflowError:
        finite = False
    if not finite or not 0 < units <= 1_000_000:
        raise AssetPackError(
            "assetpack.coordinate_system.units_per_meter must be finite and positive"
        )


def _validate_kind_representation(kind: str, representation: str, *, context: str) -> None:
    allowed = KIND_REPRESENTATIONS[kind]
    if representation in allowed:
        return
    if allowed == {"audio"}:
        expected = "audio"
    elif allowed == {"3d"}:
        expected = "3d"
    else:
        expected = "2d or 2_5d"
    raise AssetPackError(f"{context} kind {kind} requires {expected} representation")


def _implicit_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _walk_asset_tree(
    root: Path,
    *,
    max_nodes: int = MAX_TREE_NODES,
    max_directories: int = MAX_TREE_DIRECTORIES,
    max_depth: int = MAX_TREE_DEPTH,
    max_path_bytes: int = MAX_TREE_PATH_BYTES,
) -> tuple[set[str], set[str]]:
    asset_root = root / "assets"
    try:
        root_info = path_file_stat(root)
        asset_info = path_file_stat(asset_root)
    except OSError as exc:
        raise AssetPackError(f"assetpack resource tree is missing or unreadable: {exc}") from exc
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise AssetPackError("assetpack root must be a real directory")
    if is_link_or_reparse(asset_info) or not stat.S_ISDIR(asset_info.st_mode):
        raise AssetPackError("assetpack assets must be a real directory")
    files: set[str] = set()
    directories: set[str] = {"assets"}
    keys: dict[tuple[str, ...], str] = {}
    nodes = 2
    for current, names, file_names in os.walk(asset_root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        file_names.sort()
        current_info = path_file_stat(current_path)
        if is_link_or_reparse(current_info) or not stat.S_ISDIR(current_info.st_mode):
            raise AssetPackError("assetpack traversal reached an unsafe directory")
        relative_current = current_path.relative_to(root)
        if len(relative_current.parts) > max_depth:
            raise AssetPackError("assetpack exceeds the directory depth bound")
        prospective_nodes = nodes + len(names) + len(file_names)
        prospective_directories = len(directories) + len(names)
        if prospective_nodes > max_nodes:
            raise AssetPackError("assetpack exceeds the tree node bound")
        if prospective_directories > max_directories:
            raise AssetPackError("assetpack exceeds the directory bound")
        for name in names:
            path = current_path / name
            info = path_file_stat(path)
            if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise AssetPackError(f"assetpack contains an unsafe directory entry: {path}")
            relative = path.relative_to(root).as_posix()
            if (
                len(PurePosixPath(relative).parts) > max_depth
                or len(relative.encode("utf-8")) > max_path_bytes
            ):
                raise AssetPackError("assetpack path exceeds its bound")
            normalized = portable_relative_path(relative)
            if normalized is None:
                raise AssetPackError(f"assetpack directory is not portable: {relative}")
            key = portable_path_key(normalized)
            if key in keys:
                raise AssetPackError(
                    f"assetpack paths collide under NFC/casefold: {keys[key]!r}, {relative!r}"
                )
            keys[key] = relative
            directories.add(relative)
        for name in file_names:
            path = current_path / name
            info = path_file_stat(path)
            if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise AssetPackError(f"assetpack contains an unsafe file entry: {path}")
            relative = path.relative_to(root).as_posix()
            if (
                len(PurePosixPath(relative).parts) > max_depth
                or len(relative.encode("utf-8")) > max_path_bytes
            ):
                raise AssetPackError("assetpack path exceeds its bound")
            normalized = portable_relative_path(relative)
            if normalized is None:
                raise AssetPackError(f"assetpack file path is not portable: {relative}")
            key = portable_path_key(normalized)
            if key in keys:
                raise AssetPackError(
                    f"assetpack paths collide under NFC/casefold: {keys[key]!r}, {relative!r}"
                )
            keys[key] = relative
            files.add(relative)
            if len(files) > MAX_FILES:
                raise AssetPackError(f"assetpack exceeds the {MAX_FILES}-file bound")
        nodes = prospective_nodes
    return files, directories


def _authoring_metadata_detail(value: object, *, parent_key: str | None = None) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _FORBIDDEN_JSON_KEYS:
                return f"authoring-only JSON field {key!r}"
            if normalized in _CREDENTIAL_KEYS or normalized.endswith(
                ("_api_key", "_authorization", "_password", "_private_key")
            ):
                return f"credential-like JSON field {key!r}"
            detail = _authoring_metadata_detail(child, parent_key=normalized)
            if detail is not None:
                return detail
    elif isinstance(value, list):
        for child in value:
            detail = _authoring_metadata_detail(child, parent_key=parent_key)
            if detail is not None:
                return detail
    elif isinstance(value, str):
        normalized = value.casefold().replace("\\", "/")
        path_parts = set(normalized.split("/"))
        if normalized.endswith(".blend") or normalized.startswith("mcp://"):
            return "authoring-only JSON value"
        if Path(normalized).suffix in _FORBIDDEN_WEIGHT_SUFFIXES or "weights" in path_parts:
            return "model-weights JSON value"
        if path_parts & {"workflow", "workflows"}:
            return "authoring-workflow JSON value"
        if (
            parent_key in _AUTHORING_VALUE_KEYS and _AUTHORING_VALUE_PATTERN.search(normalized)
        ) or "mcp://" in normalized:
            return "provider or authoring-tool JSON value"
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            return "credential-like JSON value"
    return None


def _zero_metrics() -> dict[str, int]:
    return dict.fromkeys(METRIC_NAMES, 0)


def _sum_metrics(values: list[dict[str, int]]) -> dict[str, int]:
    return {name: sum(value[name] for value in values) for name in METRIC_NAMES}


def _verified_metrics(value: object, *, context: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(METRIC_NAMES):
        raise AssetPackError(f"{context} must contain the exact GLB metric set")
    result: dict[str, int] = {}
    for name in METRIC_NAMES:
        metric = value[name]
        maximum = _METRIC_MAXIMUMS[name]
        if isinstance(metric, bool) or not isinstance(metric, int) or not 0 <= metric <= maximum:
            raise AssetPackError(f"{context}.{name} must be an integer in 0..{maximum}")
        result[name] = metric
    return result


def _entrypoint(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetPackError(f"{context} must be an object")
    raw = value
    unknown = set(raw) - _ENTRYPOINT_KEYS
    missing = {"node", "scale", "layer"} - set(raw)
    if unknown:
        raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")
    node = raw["node"]
    if not isinstance(node, str) or not node or len(node) > 256:
        raise AssetPackError(f"{context}.node is invalid")
    scale = raw["scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise AssetPackError(f"{context}.scale must be numeric")
    try:
        finite_scale = math.isfinite(float(scale))
    except OverflowError:
        finite_scale = False
    if not finite_scale or not 0 < scale <= 1_000_000:
        raise AssetPackError(f"{context}.scale must be finite and positive")
    layer = raw["layer"]
    if isinstance(layer, bool) or not isinstance(layer, int) or not -100_000 <= layer <= 100_000:
        raise AssetPackError(f"{context}.layer is invalid")
    for name in ("default_animation", "moving_animation"):
        animation = raw.get(name)
        if animation is not None and (
            not isinstance(animation, str) or not animation or len(animation) > 256
        ):
            raise AssetPackError(f"{context}.{name} is invalid")
    return raw


def _verify_bindings(
    value: object,
    representations: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_BINDINGS:
        raise AssetPackError(f"assetpack.bindings must be a list of at most {MAX_BINDINGS} items")
    bindings: list[dict[str, Any]] = []
    slots: set[str] = set()
    for index, item in enumerate(value):
        context = f"assetpack.bindings[{index}]"
        if not isinstance(item, dict):
            raise AssetPackError(f"{context} must be an object")
        unknown = set(item) - _BINDING_KEYS
        missing = {"slot", "asset_id", "representation"} - set(item)
        if unknown:
            raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")
        slot = item["slot"]
        if not isinstance(slot, str) or SLOT_PATTERN.fullmatch(slot) is None:
            raise AssetPackError(f"{context}.slot is invalid")
        if slot in slots:
            raise AssetPackError(f"duplicate runtime binding slot: {slot}")
        slots.add(slot)
        asset_id = item["asset_id"]
        if asset_id not in representations:
            raise AssetPackError(f"{context}.asset_id does not reference a packaged asset")
        representation = item["representation"]
        if representation != representations[asset_id]:
            raise AssetPackError(f"{context}.representation does not match its asset")
        verified = {
            "slot": slot,
            "asset_id": asset_id,
            "representation": representation,
        }
        if representation == "3d":
            if "entrypoint" not in item:
                raise AssetPackError(f"{context} is missing fields: entrypoint")
            verified["entrypoint"] = _entrypoint(
                item["entrypoint"],
                context=f"{context}.entrypoint",
            )
        elif "entrypoint" in item:
            raise AssetPackError(f"{context}.entrypoint is only valid for 3D bindings")
        bindings.append(verified)
    if bindings != sorted(bindings, key=lambda item: item["slot"]):
        raise AssetPackError("assetpack.bindings are not in canonical slot order")
    return bindings


def _glb_contains_unique_animation(path: Path, animation: str, *, context: str) -> bool:
    try:
        inspect_glb(path, required_animation_names={animation})
    except GLBError as exc:
        if str(exc).startswith(_MISSING_ANIMATION_NAMES):
            return False
        raise AssetPackError(f"{context}: {exc}") from exc
    return True


def _validate_3d_entrypoints(
    bindings: list[dict[str, Any]],
    glb_files: dict[str, list[tuple[str, Path]]],
) -> None:
    for binding in bindings:
        if binding["representation"] != "3d":
            continue
        asset_id = binding["asset_id"]
        context = f"3D binding {binding['slot']!r} for asset {asset_id}"
        entrypoint = binding["entrypoint"]
        files = glb_files.get(asset_id, [])
        model_files = [path for role, path in files if role == "model"]
        if len(model_files) != 1:
            raise AssetPackError(f"{context} requires exactly one model GLB")
        try:
            inspect_glb(model_files[0], required_node_names={entrypoint["node"]})
        except GLBError as exc:
            raise AssetPackError(f"{context} node entrypoint: {exc}") from exc
        animations = {
            entrypoint[name]
            for name in ("default_animation", "moving_animation")
            if name in entrypoint
        }
        for animation in sorted(animations):
            matches = sum(
                _glb_contains_unique_animation(
                    path,
                    animation,
                    context=f"{context} animation {animation!r} in {role} GLB",
                )
                for role, path in files
            )
            if matches != 1:
                raise AssetPackError(
                    f"{context} animation {animation!r} must exist in exactly one GLB; "
                    f"found {matches}"
                )


def _capture_runtime_file(
    owner: ResourceSnapshotOwner,
    source_root: Path,
    asset_id: str,
    value: object,
    *,
    context: str,
) -> tuple[dict[str, Any], dict[str, int], Path]:
    entry = _exact(value, _FILE_KEYS, context)
    role = entry["role"]
    media_type = entry["media_type"]
    if role not in _ROLE_MEDIA_TYPES or media_type not in _ROLE_MEDIA_TYPES[role]:
        raise AssetPackError(f"{context} role and media_type are incompatible")
    raw_path = entry["path"]
    normalized = portable_relative_path(raw_path)
    if (
        normalized is None
        or len(raw_path) > 1024
        or len(normalized.parts) < 3
        or normalized.parts[:2] != ("assets", asset_id)
        or Path(normalized.name).suffix.casefold() not in _MEDIA_EXTENSIONS[media_type]
    ):
        raise AssetPackError(f"{context}.path is outside its portable runtime asset directory")
    expected_sha = _digest(entry["sha256"], f"{context}.sha256")
    size = entry["size"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= MAX_OWNED_RESOURCE_BYTES
    ):
        raise AssetPackError(f"{context}.size must be an integer in 1..{MAX_OWNED_RESOURCE_BYTES}")
    metrics = _zero_metrics()
    try:
        if media_type == "model/gltf-binary":
            if size > MAX_GLB_BYTES:
                raise AssetPackError(f"{context} exceeds the {MAX_GLB_BYTES}-byte GLB limit")
            captured = owner.materialize_file(
                source_root,
                normalized,
                limit=MAX_GLB_BYTES,
            )
            snapshot_path = captured.path
            actual_sha = captured.sha256
            actual_size = captured.size
            if actual_sha != expected_sha:
                raise AssetPackError(f"{context}.sha256 does not match the captured file")
            if actual_size != size:
                raise AssetPackError(f"{context}.size does not match the captured file")
            try:
                metrics = inspect_glb(snapshot_path, max_bytes=MAX_GLB_BYTES)["metrics"]
            except GLBError as exc:
                raise AssetPackError(f"{context}: {exc}") from exc
            required_metric = _GLB_REQUIRED_METRICS[role]
            if metrics[required_metric] < 1:
                raise AssetPackError(f"{context} requires at least one {required_metric} entry")
        else:
            if size > MAX_MEDIA_BYTES:
                raise AssetPackError(f"{context} exceeds the {MAX_MEDIA_BYTES}-byte media limit")
            resource = owner.materialize(
                source_root,
                normalized,
                media_type,
                limit=MAX_MEDIA_BYTES,
            )
            snapshot_path = owner.resolve_file(normalized)
            actual_sha = resource.sha256
            actual_size = snapshot_path.stat().st_size
            if media_type == "application/json":
                if resource.payload is None:
                    raise AssetPackError(f"{context} JSON could not be retained within its bound")
                document = decode_json_object(resource.payload, source=raw_path)
                detail = _authoring_metadata_detail(document)
                if detail is not None:
                    raise AssetPackError(f"{context} contains {detail}")
    except (MediaValidationError, ResourceSnapshotError, RuntimeIOError) as exc:
        raise AssetPackError(f"{context}: {exc}") from exc
    if actual_sha != expected_sha:
        raise AssetPackError(f"{context}.sha256 does not match the captured file")
    if actual_size != size:
        raise AssetPackError(f"{context}.size does not match the captured file")
    return entry, metrics, snapshot_path


def _load_assetpack_snapshot(
    owner: ResourceSnapshotOwner,
    source_root: Path,
    payload: dict[str, Any],
    worldpack: WorldPack | None,
) -> AssetPack:
    _exact(payload, _TOP_LEVEL_KEYS, "assetpack")
    version = payload["format_version"]
    if (
        payload["format"] != ASSETPACK_FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != ASSETPACK_FORMAT_VERSION
    ):
        raise AssetPackError("unsupported assetpack format or version")
    supplied_hash = _digest(payload["content_hash"], "assetpack.content_hash")
    if _canonical_hash(payload) != supplied_hash:
        raise AssetPackError("assetpack content hash does not match its contents")
    world_id = _identifier(payload["world_id"], "assetpack.world_id")
    world_hash = _digest(payload["world_content_hash"], "assetpack.world_content_hash")
    if worldpack is not None and (
        worldpack.world_id != world_id or worldpack.content_hash != world_hash
    ):
        raise AssetPackError("assetpack does not match the verified worldpack")
    target_id = _identifier(payload["target_id"], "assetpack.target_id")
    target_hash = _digest(payload["target_hash"], "assetpack.target_hash")
    if payload["dimension"] != "3d" or payload["delivery_profile"] != "assetpack_v1":
        raise AssetPackError("assetpack is not an engine-neutral 3D handoff")
    _coordinate_system(payload["coordinate_system"])

    raw_assets = payload["assets"]
    if not isinstance(raw_assets, list) or not raw_assets or len(raw_assets) > MAX_ASSETS:
        raise AssetPackError(f"assetpack.assets must contain 1..{MAX_ASSETS} items")
    representations: dict[str, str] = {}
    expected_files: set[str] = set()
    path_keys: dict[tuple[str, ...], str] = {}
    glb_files: dict[str, list[tuple[str, Path]]] = {}
    model_found = False
    canonical_assets: list[dict[str, Any]] = []
    total_bytes = 0
    for asset_index, value in enumerate(raw_assets):
        context = f"assetpack.assets[{asset_index}]"
        asset = _exact(value, _ASSET_KEYS, context)
        asset_id = _identifier(asset["id"], f"{context}.id")
        if asset_id in representations:
            raise AssetPackError(f"duplicate asset ID: {asset_id}")
        kind = asset["kind"]
        representation = asset["representation"]
        if kind not in ASSET_KINDS:
            raise AssetPackError(f"{context}.kind is invalid")
        if representation not in REPRESENTATIONS:
            raise AssetPackError(f"{context}.representation is invalid")
        _validate_kind_representation(kind, representation, context=context)
        representations[asset_id] = representation
        files = asset["files"]
        if not isinstance(files, list) or not files or len(files) > MAX_FILES_PER_ASSET:
            raise AssetPackError(f"{context}.files must contain 1..{MAX_FILES_PER_ASSET} items")
        if len(expected_files) + len(files) > MAX_FILES:
            raise AssetPackError(f"assetpack exceeds the {MAX_FILES}-file bound")
        for file_index, item in enumerate(files):
            file_context = f"{context}.files[{file_index}]"
            if not isinstance(item, dict):
                raise AssetPackError(f"{file_context} must be an object")
            raw_path = item.get("path")
            normalized = portable_relative_path(raw_path)
            if normalized is None:
                raise AssetPackError(f"{file_context}.path is not portable and canonical")
            key = portable_path_key(normalized)
            prior = path_keys.get(key)
            if prior is not None:
                raise AssetPackError(
                    f"assetpack paths collide under NFC/casefold: {prior!r}, {raw_path!r}"
                )
            path_keys[key] = raw_path
        inspections: list[dict[str, int]] = []
        roles: list[str] = []
        for file_index, item in enumerate(files):
            file_context = f"{context}.files[{file_index}]"
            entry, metrics, snapshot_path = _capture_runtime_file(
                owner,
                source_root,
                asset_id,
                item,
                context=file_context,
            )
            raw_path = entry["path"]
            normalized = portable_relative_path(raw_path)
            assert normalized is not None
            expected_files.add(raw_path)
            total_bytes += entry["size"]
            if total_bytes > MAX_TOTAL_RESOURCE_BYTES:
                raise AssetPackError(
                    f"assetpack exceeds the {MAX_TOTAL_RESOURCE_BYTES}-byte total bound"
                )
            roles.append(entry["role"])
            inspections.append(metrics)
            model_found = model_found or (
                entry["role"] == "model" and entry["media_type"] == "model/gltf-binary"
            )
            if entry["role"] in GLB_OUTPUT_ROLES:
                glb_files.setdefault(asset_id, []).append((entry["role"], snapshot_path))
        if files != sorted(files, key=lambda item: (item["role"], item["path"])):
            raise AssetPackError(f"{context}.files are not in canonical role/path order")
        issue = runtime_output_contract_issue(kind, representation, roles)
        if issue is not None:
            raise AssetPackError(f"{context}: {issue}")
        declared_metrics = _verified_metrics(asset["metrics"], context=f"{context}.metrics")
        if declared_metrics != _sum_metrics(inspections):
            raise AssetPackError(f"{context}.metrics do not match its packaged files")
        canonical_assets.append(asset)
    if not model_found:
        raise AssetPackError("assetpack requires a primary model/gltf-binary output")
    if canonical_assets != sorted(canonical_assets, key=lambda item: item["id"]):
        raise AssetPackError("assetpack.assets are not in canonical ID order")

    bindings = _verify_bindings(payload["bindings"], representations)
    _validate_3d_entrypoints(bindings, glb_files)
    expected_directories = _implicit_directories(expected_files)
    before_files, before_directories = _walk_asset_tree(source_root)
    if before_files != expected_files or before_directories != expected_directories:
        raise AssetPackError("assetpack resource tree differs from its exact manifest inventory")
    for relative in sorted(expected_files):
        try:
            owner.resolve_file(PurePosixPath(relative))
        except ResourceSnapshotError as exc:
            raise AssetPackError(f"assetpack snapshot lost resource {relative}: {exc}") from exc
    after_files, after_directories = _walk_asset_tree(source_root)
    if (after_files, after_directories) != (before_files, before_directories):
        raise AssetPackError("assetpack resource tree changed during validation")
    return AssetPack(
        world_id,
        world_hash,
        target_id,
        target_hash,
        supplied_hash,
        owner.root,
        payload,
        owner,
    )


def load_assetpack(
    path: str | Path,
    worldpack: WorldPack | None = None,
) -> AssetPack:
    """Load and integrally validate one assetpack into a private stdlib-only snapshot."""

    source = Path(os.path.abspath(path))
    normalized_manifest = portable_relative_path(source.name)
    if normalized_manifest is None:
        raise AssetPackError("assetpack manifest filename is not portable")
    try:
        owner = ResourceSnapshotOwner()
    except ResourceSnapshotError as exc:
        raise AssetPackError(f"could not create private assetpack snapshot: {exc}") from exc
    completed = False
    try:
        try:
            manifest = owner.materialize_file(
                source.parent,
                normalized_manifest,
                limit=MAX_ASSETPACK_BYTES,
            )
            try:
                payload = decode_json_object(manifest.path.read_bytes(), source=source)
            except (OSError, RuntimeIOError) as exc:
                raise AssetPackError(f"could not read assetpack manifest: {exc}") from exc
            result = _load_assetpack_snapshot(owner, source.parent, payload, worldpack)
        except AssetPackError:
            raise
        except (OSError, ResourceSnapshotError, RuntimeIOError) as exc:
            raise AssetPackError(str(exc)) from exc
        completed = True
        return result
    finally:
        if not completed:
            primary = sys.exception()
            try:
                owner.close()
            except ResourceSnapshotError as cleanup_error:
                if not note_cleanup_failure(
                    primary,
                    cleanup_error,
                    context="assetpack snapshot cleanup",
                ):
                    raise AssetPackError(
                        f"could not close failed assetpack snapshot: {cleanup_error}"
                    ) from cleanup_error


__all__ = [
    "ASSETPACK_FORMAT",
    "ASSETPACK_FORMAT_VERSION",
    "AssetPack",
    "AssetPackError",
    "load_assetpack",
]
