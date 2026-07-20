from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from isoworld.content.media import media_signature_matches
from worldforge.asset_formats.gltf import METRIC_NAMES, GLBError, inspect_glb
from worldforge.asset_io import (
    AssetContractError,
    bind_content_hash,
    encoded_json,
    read_json_object,
    require_content_hash,
    resolve_artifact,
    sha256_file,
    verify_artifact_reference,
)
from worldforge.game_boundary import authoring_metadata_detail

ASSETPACK_FORMAT = "rpg-world-forge.assetpack"
ASSETPACK_FORMAT_VERSION = 1

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERN = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")

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
_MANIFEST_BINDING_KEYS = frozenset({"slot", "asset_id", "representation", "presentation"})
_PACK_BINDING_KEYS = frozenset({"slot", "asset_id", "representation", "entrypoint"})
_ENTRYPOINT_KEYS = frozenset({"node", "default_animation", "moving_animation", "scale", "layer"})

_KINDS = frozenset(
    {
        "animation_3d",
        "character_3d",
        "collision_3d",
        "environment_3d",
        "font",
        "material_set",
        "model_3d",
        "music",
        "portrait",
        "rig",
        "sfx",
        "shader",
        "sprite",
        "spritesheet",
        "tileset",
        "ui",
        "vfx",
        "vfx_3d",
    }
)
_REPRESENTATIONS = frozenset({"2d", "2_5d", "3d", "audio"})
_ROLE_MEDIA_TYPES = {
    "animation": frozenset({"model/gltf-binary"}),
    "audio": frozenset({"audio/mpeg", "audio/ogg", "audio/wav"}),
    "clipset": frozenset({"application/json"}),
    "collision": frozenset({"model/gltf-binary"}),
    "font": frozenset({"font/otf", "font/ttf"}),
    "fragment_shader": frozenset({"text/x-glsl"}),
    "material_metadata": frozenset({"application/json"}),
    "model": frozenset({"model/gltf-binary"}),
    "model_metadata": frozenset({"application/json"}),
    "preview": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "skeleton": frozenset({"model/gltf-binary"}),
    "texture": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "vertex_shader": frozenset({"text/x-glsl"}),
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
_GLB_ROLES = frozenset({"animation", "collision", "model", "skeleton"})
_GLB_REQUIRED_METRICS = {
    "animation": "animations",
    "collision": "meshes",
    "model": "meshes",
    "skeleton": "skins",
}
_MISSING_ANIMATION_NAMES = "GLB is missing required animations names:"
_AUDIO_KINDS = frozenset({"music", "sfx"})
_THREE_D_KINDS = frozenset(
    {
        "animation_3d",
        "character_3d",
        "collision_3d",
        "environment_3d",
        "material_set",
        "model_3d",
        "rig",
        "vfx_3d",
    }
)
_FORBIDDEN_SOURCE_PARTS = frozenset(
    {
        ".agents",
        ".worldforge",
        "evidence",
        "mcp",
        "provider",
        "providers",
        "receipts",
        "source",
        "weights",
    }
)


class AssetPackError(ValueError):
    """Raised when a 3D release cannot become a neutral runtime assetpack."""


def _primary_roles(kind: str, representation: str) -> frozenset[str]:
    if representation == "3d":
        if kind == "animation_3d":
            return frozenset({"animation"})
        if kind == "collision_3d":
            return frozenset({"collision"})
        if kind == "rig":
            return frozenset({"skeleton"})
        return frozenset({"model"})
    if representation == "audio":
        return frozenset({"audio"})
    if kind == "font":
        return frozenset({"font"})
    if kind == "shader":
        return frozenset({"fragment_shader", "vertex_shader"})
    return frozenset({"texture"})


def _expect_exact_keys(value: dict[str, Any], expected: frozenset[str], context: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")


def _valid_id(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise AssetPackError(f"{context} must be a portable ID")
    return value


def _valid_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise AssetPackError(f"{context} must be a lowercase SHA-256")
    return value


def _validate_kind_representation(kind: str, representation: str, *, context: str) -> None:
    if kind in _AUDIO_KINDS and representation != "audio":
        raise AssetPackError(f"{context} kind {kind} requires audio representation")
    if kind in _THREE_D_KINDS and representation != "3d":
        raise AssetPackError(f"{context} kind {kind} requires 3d representation")


def _lstat(path: Path, *, context: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AssetPackError(f"Could not inspect {context} {path}: {exc}") from exc


_DIR_FD_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.link, os.open, os.stat, os.unlink)
)


def _safe_directory_identity(path: Path) -> tuple[int, int]:
    info = _lstat(path, context="publication directory")
    if info is None:
        raise AssetPackError(f"Publication directory disappeared: {path}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AssetPackError(f"Publication parent is not a safe directory: {path}")
    return info.st_dev, info.st_ino


@contextmanager
def _open_verified_parent(path: Path, expected: tuple[int, int]) -> Iterator[int | None]:
    if _safe_directory_identity(path) != expected:
        raise AssetPackError(f"Publication parent changed before use: {path}")
    if not _DIR_FD_PUBLICATION:
        yield None
        if _safe_directory_identity(path) != expected:
            raise AssetPackError(f"Publication parent changed during use: {path}")
        return
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AssetPackError(f"Could not pin publication parent {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            raise AssetPackError(f"Publication parent changed before use: {path}")
        yield descriptor
    finally:
        os.close(descriptor)


def _entry_info(parent_fd: int | None, parent: Path, name: str) -> os.stat_result | None:
    try:
        if parent_fd is not None:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return (parent / name).lstat()
    except FileNotFoundError:
        return None


def _unlink_owned_entry(
    parent_fd: int | None,
    parent: Path,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        info = _entry_info(parent_fd, parent, name)
    except OSError:
        return
    if info is None or not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        return
    try:
        if parent_fd is not None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            (parent / name).unlink()
    except OSError:
        pass


def _create_temporary_entry(parent_fd: int | None, parent: Path, prefix: str) -> tuple[int, str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            if parent_fd is not None:
                return os.open(name, flags, 0o600, dir_fd=parent_fd), name
            return os.open(parent / name, flags, 0o600), name
        except FileExistsError:
            continue
    raise AssetPackError(f"Could not allocate a temporary output in {parent}")


def _ensure_safe_directory(
    path: Path,
    created: list[tuple[Path, int, int]],
) -> Path:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        info = _lstat(current, context="publication directory")
        if info is None:
            made = False
            try:
                current.mkdir()
                made = True
            except FileExistsError:
                pass
            info = _lstat(current, context="publication directory")
            if info is None:
                raise AssetPackError(f"Could not create publication directory {current}")
            if made:
                created.append((current, info.st_dev, info.st_ino))
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AssetPackError(f"Publication parent is not a safe directory: {current}")
    return absolute


def _reject_existing_path(path: Path, *, context: str) -> None:
    info = _lstat(path, context=context)
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode):
        raise AssetPackError(f"Refusing symbolic link at {context}: {path}")
    raise AssetPackError(f"Refusing to overwrite {path}")


def _unlink_if_same(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if (info.st_dev, info.st_ino) == identity and stat.S_ISREG(info.st_mode):
        try:
            path.unlink()
        except OSError:
            pass


def _rmdir_if_same(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if (info.st_dev, info.st_ino) == identity and stat.S_ISDIR(info.st_mode):
        try:
            path.rmdir()
        except OSError:
            pass


def _copy_exclusive(
    source: Path,
    destination: Path,
    parent_identity: tuple[int, int],
) -> tuple[int, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with _open_verified_parent(destination.parent, parent_identity) as parent_fd:
        try:
            if parent_fd is not None:
                descriptor = os.open(destination.name, flags, 0o644, dir_fd=parent_fd)
            else:
                descriptor = os.open(destination, flags, 0o644)
        except FileExistsError as exc:
            raise AssetPackError(f"Refusing to overwrite {destination}") from exc
        except OSError as exc:
            raise AssetPackError(f"Could not create runtime file {destination}: {exc}") from exc
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        try:
            with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
                descriptor = -1
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            _unlink_owned_entry(
                parent_fd,
                destination.parent,
                destination.name,
                identity,
            )
            raise
        return identity


def _publish_json_exclusive(
    path: Path,
    value: dict[str, Any],
    parent_identity: tuple[int, int],
) -> tuple[int, int]:
    try:
        payload = encoded_json(value)
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    with _open_verified_parent(path.parent, parent_identity) as parent_fd:
        descriptor, temporary_name = _create_temporary_entry(
            parent_fd,
            path.parent,
            f".{path.name}.",
        )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        linked = False
        try:
            with os.fdopen(descriptor, "wb") as output_stream:
                output_stream.write(payload)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            try:
                if parent_fd is not None:
                    os.link(
                        temporary_name,
                        path.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                else:
                    os.link(path.parent / temporary_name, path)
            except FileExistsError as exc:
                raise AssetPackError(f"Refusing to overwrite {path}") from exc
            linked = True
        except Exception:
            if linked:
                _unlink_owned_entry(parent_fd, path.parent, path.name, identity)
            raise
        finally:
            _unlink_owned_entry(parent_fd, path.parent, temporary_name, identity)
        return identity


def _load_worldpack(path: Path) -> dict[str, Any]:
    try:
        worldpack = read_json_object(path)
        require_content_hash(worldpack, context="worldpack")
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    version = worldpack.get("format_version")
    if (
        worldpack.get("format") != "isoworld.worldpack"
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version not in {1, 2, 3, 4, 5}
    ):
        raise AssetPackError("The input file is not a compatible worldpack")
    world = worldpack.get("world")
    if not isinstance(world, dict):
        raise AssetPackError("worldpack.world must be an object")
    _valid_id(world.get("id"), context="worldpack.world.id")
    return worldpack


def _coordinate_system(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetPackError(f"{context} must be an object")
    _expect_exact_keys(value, _COORDINATE_KEYS, context)
    if value.get("handedness") != "right":
        raise AssetPackError(f"{context}.handedness must be right")
    if value.get("up_axis") != "Y":
        raise AssetPackError(f"{context}.up_axis must be Y")
    if value.get("forward_axis") != "-Z":
        raise AssetPackError(f"{context}.forward_axis must be -Z")
    units = value.get("units_per_meter")
    if isinstance(units, bool) or not isinstance(units, (int, float)) or not 0 < units <= 1_000_000:
        raise AssetPackError(f"{context}.units_per_meter must be positive")
    return dict(value)


def _load_target(manifest_root: Path, reference: Any) -> dict[str, Any]:
    try:
        target_path = verify_artifact_reference(
            manifest_root,
            reference,
            context="manifest.target",
        )
        target = read_json_object(target_path)
        require_content_hash(target, context="asset target")
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    if target.get("format") != "rpg-world-forge.asset_target" or target.get("format_version") != 1:
        raise AssetPackError("manifest.target does not reference an asset target v1")
    if target.get("dimension") != "3d":
        raise AssetPackError("assetpack requires a 3d target")
    if target.get("delivery_profile") != "assetpack_v1":
        raise AssetPackError("assetpack requires the assetpack_v1 delivery profile")
    _valid_id(target.get("id"), context="asset target id")
    _coordinate_system(target.get("coordinate_system"), context="asset target coordinate_system")
    return target


def _specification_contract(
    manifest_root: Path,
    asset: dict[str, Any],
    *,
    target: dict[str, Any],
) -> tuple[dict[str, int], int, int | None, frozenset[str]]:
    asset_id = asset["id"]
    try:
        specification_path = verify_artifact_reference(
            manifest_root,
            asset.get("specification"),
            context=f"asset {asset_id} specification",
        )
        specification = read_json_object(specification_path)
        require_content_hash(specification, context=f"asset {asset_id} specification")
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    if (
        specification.get("format") != "rpg-world-forge.asset_spec"
        or specification.get("format_version") != 2
    ):
        raise AssetPackError(f"asset {asset_id} requires an asset specification v2")
    for field, expected in (
        ("id", asset_id),
        ("kind", asset["kind"]),
        ("representation", asset["representation"]),
        ("target_id", target.get("id")),
        ("target_hash", target.get("content_hash")),
    ):
        if specification.get(field) != expected:
            raise AssetPackError(f"asset {asset_id} specification {field} does not match")
    technical = specification.get("technical")
    if not isinstance(technical, dict):
        raise AssetPackError(f"asset {asset_id} technical contract is missing")
    max_bytes = technical.get("memory_budget_bytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise AssetPackError(f"asset {asset_id} memory_budget_bytes is invalid")

    budgets: dict[str, int] = {}
    max_texture_size: int | None = None
    required_animations: frozenset[str] = frozenset()
    if asset["representation"] == "3d":
        if technical.get("runtime_format") != "glb":
            raise AssetPackError(f"asset {asset_id} technical 3D contract is invalid")
        dimensions = technical.get("physical_dimensions_m")
        if (
            not isinstance(dimensions, list)
            or len(dimensions) != 3
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
                for value in dimensions
            )
        ):
            raise AssetPackError(f"asset {asset_id} physical_dimensions_m is invalid")
        raw_budgets = technical.get("budgets")
        if not isinstance(raw_budgets, dict):
            raise AssetPackError(f"asset {asset_id} 3D budgets are missing")
        budget_fields = {
            "max_triangles": "triangles",
            "max_vertices": "vertices",
            "max_materials": "materials",
        }
        for field, metric in budget_fields.items():
            value = raw_budgets.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise AssetPackError(f"asset {asset_id} {field} is invalid")
            budgets[metric] = value
        texture_size = raw_budgets.get("max_texture_size")
        if isinstance(texture_size, bool) or not isinstance(texture_size, int) or texture_size <= 0:
            raise AssetPackError(f"asset {asset_id} max_texture_size is invalid")
        max_texture_size = texture_size
        for field, metric, maximum in (
            ("max_bones", "bones", 4096),
            ("max_influences", "influences", 16),
        ):
            value = raw_budgets.get(field)
            if value is not None:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= maximum
                ):
                    raise AssetPackError(f"asset {asset_id} {field} is invalid")
                budgets[metric] = value
        raw_animations = technical.get("required_animations", [])
        if (
            not isinstance(raw_animations, list)
            or any(
                not isinstance(name, str) or ID_PATTERN.fullmatch(name) is None
                for name in raw_animations
            )
            or raw_animations != sorted(set(raw_animations))
        ):
            raise AssetPackError(f"asset {asset_id} required_animations is invalid")
        required_animations = frozenset(raw_animations)
    return budgets, max_bytes, max_texture_size, required_animations


def _safe_runtime_source(manifest_root: Path, relative: Any, *, context: str) -> Path:
    try:
        source = resolve_artifact(manifest_root, relative, max_bytes=2 * 1024 * 1024 * 1024)
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    assert source is not None
    lowered_parts = {part.casefold() for part in Path(str(relative)).parts}
    if source.suffix.casefold() == ".blend" or lowered_parts & _FORBIDDEN_SOURCE_PARTS:
        raise AssetPackError(f"{context} points into authoring-only storage")
    return source


def _zero_metrics() -> dict[str, int]:
    return dict.fromkeys(METRIC_NAMES, 0)


def _sum_metrics(values: list[dict[str, int]]) -> dict[str, int]:
    return {name: sum(value[name] for value in values) for name in METRIC_NAMES}


def _validate_runtime_json(path: Path, *, context: str) -> None:
    try:
        payload = read_json_object(path)
    except AssetContractError as exc:
        raise AssetPackError(f"{context}: {exc}") from exc
    detail = authoring_metadata_detail(payload)
    if detail is not None:
        raise AssetPackError(f"{context} contains {detail}")


def _inspect_output(
    manifest_root: Path,
    asset: dict[str, Any],
    output: Any,
    *,
    budgets: dict[str, int],
    max_bytes: int,
    max_texture_size: int | None,
) -> tuple[Path, dict[str, int], str, str]:
    asset_id = asset["id"]
    if not isinstance(output, dict):
        raise AssetPackError(f"asset {asset_id} outputs must contain objects")
    unknown = set(output) - {"role", "runtime_file", "sha256", "size", "media_type"}
    if unknown:
        raise AssetPackError(
            f"asset {asset_id} output contains unknown fields: {', '.join(sorted(unknown))}"
        )
    role = output.get("role")
    media_type = output.get("media_type")
    if role not in _ROLE_MEDIA_TYPES or media_type not in _ROLE_MEDIA_TYPES[role]:
        raise AssetPackError(f"asset {asset_id} output role and media_type are incompatible")
    source = _safe_runtime_source(
        manifest_root,
        output.get("runtime_file"),
        context=f"asset {asset_id} {role} output",
    )
    if source.suffix.casefold() not in _MEDIA_EXTENSIONS[media_type]:
        raise AssetPackError(f"asset {asset_id} {role} extension and media_type disagree")
    declared_sha = _valid_sha256(output.get("sha256"), context=f"asset {asset_id} output sha256")
    if sha256_file(source) != declared_sha:
        raise AssetPackError(f"asset {asset_id} {role} output SHA-256 does not match")
    declared_size = output.get("size")
    if declared_size is not None and (
        isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size != source.stat().st_size
    ):
        raise AssetPackError(f"asset {asset_id} {role} output size does not match")
    if source.stat().st_size > max_bytes:
        raise AssetPackError(f"asset {asset_id} {role} exceeds its memory budget")

    metrics = _zero_metrics()
    if role in _GLB_ROLES:
        try:
            inspection = inspect_glb(source, budgets=budgets, max_bytes=max_bytes)
        except GLBError as exc:
            raise AssetPackError(f"asset {asset_id} {role} output: {exc}") from exc
        metrics = inspection["metrics"]
        required_metric = _GLB_REQUIRED_METRICS[role]
        if metrics[required_metric] < 1:
            raise AssetPackError(
                f"asset {asset_id} {role} output requires at least one {required_metric} entry"
            )
        if max_texture_size is not None and inspection["max_texture_dimension"] > max_texture_size:
            raise AssetPackError(
                f"asset {asset_id} embedded texture exceeds max_texture_size: "
                f"{inspection['max_texture_dimension']} > {max_texture_size}"
            )
    elif not media_signature_matches(source, media_type):
        raise AssetPackError(f"asset {asset_id} {role} bytes do not match {media_type}")
    elif media_type == "application/json":
        _validate_runtime_json(source, context=f"asset {asset_id} {role} output")
    return source, metrics, role, media_type


def _entrypoint(value: Any, *, context: str, from_manifest: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetPackError(f"{context} must be an object")
    raw = dict(value)
    if from_manifest and "type" in raw and raw.pop("type") != "visual_3d":
        raise AssetPackError(f"{context}.type must be visual_3d when present")
    unknown = set(raw) - _ENTRYPOINT_KEYS
    if unknown:
        raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    if not {"node", "scale", "layer"}.issubset(raw):
        raise AssetPackError(f"{context} requires node, scale, and layer")
    node = raw.get("node")
    if not isinstance(node, str) or not node or len(node) > 256:
        raise AssetPackError(f"{context}.node is invalid")
    scale = raw.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 1_000_000:
        raise AssetPackError(f"{context}.scale must be positive")
    layer = raw.get("layer")
    if isinstance(layer, bool) or not isinstance(layer, int) or not -100_000 <= layer <= 100_000:
        raise AssetPackError(f"{context}.layer is invalid")
    for name in ("default_animation", "moving_animation"):
        animation = raw.get(name)
        if animation is not None and (
            not isinstance(animation, str) or not animation or len(animation) > 256
        ):
            raise AssetPackError(f"{context}.{name} is invalid")
    return raw


def _compile_bindings(
    value: Any,
    asset_representations: dict[str, str],
    skipped_asset_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AssetPackError("manifest.bindings must be a list")
    result: list[dict[str, Any]] = []
    slots: set[str] = set()
    for index, binding in enumerate(value):
        context = f"manifest.bindings[{index}]"
        if not isinstance(binding, dict):
            raise AssetPackError(f"{context} must be an object")
        unknown = set(binding) - _MANIFEST_BINDING_KEYS
        missing = {"slot", "asset_id", "representation"} - set(binding)
        if unknown:
            raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")
        slot = binding.get("slot")
        if not isinstance(slot, str) or not SLOT_PATTERN.fullmatch(slot):
            raise AssetPackError(f"{context}.slot is invalid")
        if slot in slots:
            raise AssetPackError(f"duplicate runtime binding slot: {slot}")
        slots.add(slot)
        asset_id = binding.get("asset_id")
        if asset_id in skipped_asset_ids:
            continue
        if asset_id not in asset_representations:
            raise AssetPackError(f"{context}.asset_id does not reference a packaged asset")
        representation = binding.get("representation")
        if representation != asset_representations[asset_id]:
            raise AssetPackError(f"{context}.representation does not match its asset")
        compiled = {
            "slot": slot,
            "asset_id": asset_id,
            "representation": representation,
        }
        if representation == "3d":
            compiled["entrypoint"] = _entrypoint(
                binding.get("presentation"),
                context=f"{context}.presentation",
                from_manifest=True,
            )
        elif "presentation" in binding:
            raise AssetPackError(f"{context}.presentation is only valid for 3D bindings")
        result.append(compiled)
    return sorted(result, key=lambda item: item["slot"])


def _verify_bindings(
    value: Any,
    asset_representations: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AssetPackError("assetpack.bindings must be a list")
    result: list[dict[str, Any]] = []
    slots: set[str] = set()
    for index, binding in enumerate(value):
        context = f"assetpack.bindings[{index}]"
        if not isinstance(binding, dict):
            raise AssetPackError(f"{context} must be an object")
        unknown = set(binding) - _PACK_BINDING_KEYS
        missing = {"slot", "asset_id", "representation"} - set(binding)
        if unknown:
            raise AssetPackError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            raise AssetPackError(f"{context} is missing fields: {', '.join(sorted(missing))}")
        slot = binding.get("slot")
        if not isinstance(slot, str) or not SLOT_PATTERN.fullmatch(slot):
            raise AssetPackError(f"{context}.slot is invalid")
        if slot in slots:
            raise AssetPackError(f"duplicate runtime binding slot: {slot}")
        slots.add(slot)
        asset_id = binding.get("asset_id")
        if asset_id not in asset_representations:
            raise AssetPackError(f"{context}.asset_id does not reference a packaged asset")
        representation = binding.get("representation")
        if representation != asset_representations[asset_id]:
            raise AssetPackError(f"{context}.representation does not match its asset")
        verified = {
            "slot": slot,
            "asset_id": asset_id,
            "representation": representation,
        }
        if representation == "3d":
            verified["entrypoint"] = _entrypoint(
                binding.get("entrypoint"),
                context=f"{context}.entrypoint",
                from_manifest=False,
            )
        elif "entrypoint" in binding:
            raise AssetPackError(f"{context}.entrypoint is only valid for 3D bindings")
        result.append(verified)
    return sorted(result, key=lambda item: item["slot"])


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


def _build_assets(
    manifest: dict[str, Any],
    *,
    manifest_root: Path,
    target: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[tuple[Path, dict[str, Any]]],
    dict[str, str],
    set[str],
    dict[str, list[tuple[str, Path]]],
]:
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetPackError("asset manifest release must contain assets")
    compiled_assets: list[dict[str, Any]] = []
    prepared: list[tuple[Path, dict[str, Any]]] = []
    representations: dict[str, str] = {}
    skipped_asset_ids: set[str] = set()
    glb_files: dict[str, list[tuple[str, Path]]] = {}
    seen_ids: set[str] = set()
    for asset in sorted(
        assets,
        key=lambda item: item.get("id", "") if isinstance(item, dict) else "",
    ):
        if not isinstance(asset, dict):
            raise AssetPackError("manifest.assets must contain objects")
        asset_id = _valid_id(asset.get("id"), context="asset id")
        if asset_id in seen_ids:
            raise AssetPackError(f"duplicate asset ID: {asset_id}")
        seen_ids.add(asset_id)
        kind = asset.get("kind")
        if kind not in _KINDS:
            raise AssetPackError(f"asset {asset_id} kind is not runtime-safe")
        representation = asset.get("representation")
        if representation not in _REPRESENTATIONS:
            raise AssetPackError(f"asset {asset_id} representation is not runtime-safe")
        _validate_kind_representation(kind, representation, context=f"asset {asset_id}")
        representations[asset_id] = representation
        if asset.get("status") != "processed":
            if asset.get("required", True) is False:
                representations.pop(asset_id)
                skipped_asset_ids.add(asset_id)
                continue
            raise AssetPackError(f"asset {asset_id} must be processed")
        budgets, max_bytes, max_texture_size, required_animations = _specification_contract(
            manifest_root,
            asset,
            target=target,
        )
        outputs = asset.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise AssetPackError(f"asset {asset_id} requires processed outputs")

        inspected: list[tuple[Path, dict[str, int], str, str]] = []
        total_bytes = 0
        role_counts: dict[str, int] = {}
        for output in outputs:
            item = _inspect_output(
                manifest_root,
                asset,
                output,
                budgets=budgets,
                max_bytes=max_bytes,
                max_texture_size=max_texture_size,
            )
            inspected.append(item)
            total_bytes += item[0].stat().st_size
            role_counts[item[2]] = role_counts.get(item[2], 0) + 1
        if total_bytes > max_bytes:
            raise AssetPackError(f"asset {asset_id} outputs exceed their memory budget")
        if not any(role_counts.get(role) == 1 for role in _primary_roles(kind, representation)):
            raise AssetPackError(
                f"asset {asset_id} does not have exactly one primary runtime output"
            )

        inspected.sort(key=lambda item: (item[2], item[0].name))
        runtime_files: list[dict[str, Any]] = []
        inspections: list[dict[str, int]] = []
        for index, (source, metrics, role, media_type) in enumerate(inspected):
            extension = source.suffix.casefold()
            relative = Path("assets") / asset_id / f"{index:02d}_{role}{extension}"
            entry = {
                "role": role,
                "path": relative.as_posix(),
                "sha256": sha256_file(source),
                "size": source.stat().st_size,
                "media_type": media_type,
            }
            runtime_files.append(entry)
            inspections.append(metrics)
            prepared.append((source, entry))
            if role in _GLB_ROLES:
                glb_files.setdefault(asset_id, []).append((role, source))
        metrics = _sum_metrics(inspections)
        for name, maximum in budgets.items():
            if metrics[name] > maximum:
                raise AssetPackError(
                    f"asset {asset_id} {name} budget exceeded: {metrics[name]} > {maximum}"
                )
        for animation in sorted(required_animations):
            matches = sum(
                _glb_contains_unique_animation(
                    source,
                    animation,
                    context=f"asset {asset_id} required animation {animation!r} in {role} GLB",
                )
                for source, _, role, media_type in inspected
                if media_type == "model/gltf-binary"
            )
            if matches != 1:
                raise AssetPackError(
                    f"asset {asset_id} required animation {animation!r} must exist in exactly "
                    f"one GLB; found {matches}"
                )
        compiled_assets.append(
            {
                "id": asset_id,
                "kind": kind,
                "representation": representation,
                "files": runtime_files,
                "metrics": metrics,
            }
        )
    return compiled_assets, prepared, representations, skipped_asset_ids, glb_files


def build_assetpack(
    manifest_path: str | Path,
    worldpack_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Build a provider-neutral, runtime-only assetpack for a 3D target."""

    manifest_file = Path(manifest_path)
    output_file = Path(os.path.abspath(output_path))
    _reject_existing_path(output_file, context="assetpack output")
    from worldforge.assets import validate_asset_manifest

    manifest_issues = validate_asset_manifest(
        manifest_file,
        profile="build",
        worldpack_path=worldpack_path,
    )
    if manifest_issues:
        raise AssetPackError("; ".join(str(issue) for issue in manifest_issues))
    try:
        manifest = read_json_object(manifest_file)
        require_content_hash(manifest, context="asset manifest")
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    if (
        manifest.get("format") != "rpg-world-forge.asset_manifest"
        or manifest.get("format_version") != 3
    ):
        raise AssetPackError("Building an assetpack requires asset manifest version 3")
    if manifest.get("phase") != "production":
        raise AssetPackError("Building an assetpack requires a validated production plan")

    worldpack = _load_worldpack(Path(worldpack_path))
    world_id = worldpack["world"]["id"]
    world_hash = worldpack["content_hash"]
    if manifest.get("world_id") != world_id or manifest.get("world_content_hash") != world_hash:
        raise AssetPackError("asset manifest does not match the verified worldpack")
    manifest_root = manifest_file.parent.resolve()
    target = _load_target(manifest_root, manifest.get("target"))
    if target.get("world_id") != world_id or target.get("world_content_hash") != world_hash:
        raise AssetPackError("asset target does not match the verified worldpack")

    assets, prepared, representations, skipped_asset_ids, glb_files = _build_assets(
        manifest,
        manifest_root=manifest_root,
        target=target,
    )
    if not any(
        file["role"] == "model" and file["media_type"] == "model/gltf-binary"
        for asset in assets
        for file in asset["files"]
    ):
        raise AssetPackError("assetpack requires a primary model/gltf-binary output")
    bindings = _compile_bindings(
        manifest.get("bindings"),
        representations,
        skipped_asset_ids,
    )
    _validate_3d_entrypoints(bindings, glb_files)

    payload = bind_content_hash(
        {
            "format": ASSETPACK_FORMAT,
            "format_version": ASSETPACK_FORMAT_VERSION,
            "world_id": world_id,
            "world_content_hash": world_hash,
            "target_id": target["id"],
            "target_hash": target["content_hash"],
            "dimension": "3d",
            "delivery_profile": target["delivery_profile"],
            "coordinate_system": _coordinate_system(
                target["coordinate_system"],
                context="asset target coordinate_system",
            ),
            "assets": assets,
            "bindings": bindings,
        }
    )

    created_directories: list[tuple[Path, int, int]] = []
    copied: list[tuple[Path, int, int]] = []
    published_identity: tuple[int, int] | None = None
    try:
        publication_root = _ensure_safe_directory(output_file.parent, created_directories)
        publication_root_identity = _safe_directory_identity(publication_root)
        output_file = publication_root / output_file.name
        _reject_existing_path(output_file, context="assetpack output")
        destinations: list[tuple[Path, Path, tuple[int, int]]] = []
        for source, entry in prepared:
            requested_destination = output_file.parent / entry["path"]
            safe_parent = _ensure_safe_directory(
                requested_destination.parent,
                created_directories,
            )
            safe_parent_identity = _safe_directory_identity(safe_parent)
            destination = safe_parent / requested_destination.name
            _reject_existing_path(destination, context="runtime file")
            destinations.append((source, destination, safe_parent_identity))
        for source, destination, parent_identity in destinations:
            identity = _copy_exclusive(source, destination, parent_identity)
            copied.append((destination, *identity))
        published_identity = _publish_json_exclusive(
            output_file,
            payload,
            publication_root_identity,
        )
        if verify_assetpack(output_file, worldpack_path) != payload:
            raise AssetPackError("Published assetpack differs from its verified payload")
    except Exception:
        if published_identity is not None:
            _unlink_if_same(output_file, published_identity)
        for copied_file, device, inode in reversed(copied):
            _unlink_if_same(copied_file, (device, inode))
        for directory, device, inode in reversed(created_directories):
            _rmdir_if_same(directory, (device, inode))
        raise
    return payload


def _verified_metrics(value: Any, *, context: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(METRIC_NAMES):
        raise AssetPackError(f"{context} must contain the exact GLB metric set")
    result: dict[str, int] = {}
    for name in METRIC_NAMES:
        metric = value[name]
        if isinstance(metric, bool) or not isinstance(metric, int) or metric < 0:
            raise AssetPackError(f"{context}.{name} must be a non-negative integer")
        if name == "external_uris" and metric != 0:
            raise AssetPackError(f"{context}.external_uris must be zero")
        result[name] = metric
    return result


def _verify_runtime_file(
    root: Path,
    asset_id: str,
    entry: Any,
    *,
    context: str,
) -> tuple[dict[str, Any], dict[str, int], Path]:
    if not isinstance(entry, dict):
        raise AssetPackError(f"{context} must be an object")
    _expect_exact_keys(entry, _FILE_KEYS, context)
    role = entry.get("role")
    media_type = entry.get("media_type")
    if role not in _ROLE_MEDIA_TYPES or media_type not in _ROLE_MEDIA_TYPES[role]:
        raise AssetPackError(f"{context} role and media_type are incompatible")
    relative = entry.get("path")
    if (
        not isinstance(relative, str)
        or not relative.startswith(f"assets/{asset_id}/")
        or Path(relative).suffix.casefold() not in _MEDIA_EXTENSIONS[media_type]
    ):
        raise AssetPackError(f"{context}.path is outside its runtime asset directory")
    try:
        source = resolve_artifact(root, relative, max_bytes=2 * 1024 * 1024 * 1024)
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    assert source is not None
    expected_sha = _valid_sha256(entry.get("sha256"), context=f"{context}.sha256")
    if sha256_file(source) != expected_sha:
        raise AssetPackError(f"{context}.sha256 does not match the file")
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size != source.stat().st_size:
        raise AssetPackError(f"{context}.size does not match the file")
    metrics = _zero_metrics()
    if role in _GLB_ROLES:
        try:
            metrics = inspect_glb(source)["metrics"]
        except GLBError as exc:
            raise AssetPackError(f"{context}: {exc}") from exc
        required_metric = _GLB_REQUIRED_METRICS[role]
        if metrics[required_metric] < 1:
            raise AssetPackError(f"{context} requires at least one {required_metric} entry")
    elif not media_signature_matches(source, media_type):
        raise AssetPackError(f"{context} bytes do not match {media_type}")
    elif media_type == "application/json":
        _validate_runtime_json(source, context=context)
    return entry, metrics, source


def verify_assetpack(
    path: str | Path,
    worldpack_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify assetpack structure, files, media, GLB metrics, and hashes."""

    assetpack_path = Path(path)
    try:
        payload = read_json_object(assetpack_path)
        require_content_hash(payload, context="assetpack")
    except AssetContractError as exc:
        raise AssetPackError(str(exc)) from exc
    _expect_exact_keys(payload, _TOP_LEVEL_KEYS, "assetpack")
    if payload.get("format") != ASSETPACK_FORMAT or payload.get("format_version") != 1:
        raise AssetPackError("unsupported assetpack format or version")
    world_id = _valid_id(payload.get("world_id"), context="assetpack.world_id")
    world_hash = _valid_sha256(
        payload.get("world_content_hash"),
        context="assetpack.world_content_hash",
    )
    if worldpack_path is not None:
        worldpack = _load_worldpack(Path(worldpack_path))
        if worldpack["world"]["id"] != world_id or worldpack["content_hash"] != world_hash:
            raise AssetPackError("assetpack does not match the verified worldpack")
    _valid_id(payload.get("target_id"), context="assetpack.target_id")
    _valid_sha256(payload.get("target_hash"), context="assetpack.target_hash")
    if payload.get("dimension") != "3d" or payload.get("delivery_profile") != "assetpack_v1":
        raise AssetPackError("assetpack is not an engine-neutral 3D handoff")
    _coordinate_system(payload.get("coordinate_system"), context="assetpack.coordinate_system")

    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetPackError("assetpack.assets must be a non-empty list")
    root = assetpack_path.parent.resolve()
    representations: dict[str, str] = {}
    model_found = False
    seen_paths: set[str] = set()
    glb_files: dict[str, list[tuple[str, Path]]] = {}
    canonical_assets: list[dict[str, Any]] = []
    for asset_index, asset in enumerate(assets):
        context = f"assetpack.assets[{asset_index}]"
        if not isinstance(asset, dict):
            raise AssetPackError(f"{context} must be an object")
        _expect_exact_keys(asset, _ASSET_KEYS, context)
        asset_id = _valid_id(asset.get("id"), context=f"{context}.id")
        if asset_id in representations:
            raise AssetPackError(f"duplicate asset ID: {asset_id}")
        if asset.get("kind") not in _KINDS:
            raise AssetPackError(f"{context}.kind is invalid")
        representation = asset.get("representation")
        if representation not in _REPRESENTATIONS:
            raise AssetPackError(f"{context}.representation is invalid")
        _validate_kind_representation(asset["kind"], representation, context=context)
        representations[asset_id] = representation
        files = asset.get("files")
        if not isinstance(files, list) or not files:
            raise AssetPackError(f"{context}.files must be a non-empty list")
        inspections: list[dict[str, int]] = []
        roles: dict[str, int] = {}
        for file_index, file in enumerate(files):
            entry, metrics, source = _verify_runtime_file(
                root,
                asset_id,
                file,
                context=f"{context}.files[{file_index}]",
            )
            if entry["path"] in seen_paths:
                raise AssetPackError(f"duplicate assetpack path: {entry['path']}")
            seen_paths.add(entry["path"])
            roles[entry["role"]] = roles.get(entry["role"], 0) + 1
            model_found = model_found or (
                entry["role"] == "model" and entry["media_type"] == "model/gltf-binary"
            )
            inspections.append(metrics)
            if entry["role"] in _GLB_ROLES:
                glb_files.setdefault(asset_id, []).append((entry["role"], source))
        if files != sorted(files, key=lambda item: (item["role"], item["path"])):
            raise AssetPackError(f"{context}.files are not in canonical role/path order")
        if not any(roles.get(role) == 1 for role in _primary_roles(asset["kind"], representation)):
            raise AssetPackError(f"{context} does not have exactly one primary runtime output")
        declared_metrics = _verified_metrics(asset.get("metrics"), context=f"{context}.metrics")
        if declared_metrics != _sum_metrics(inspections):
            raise AssetPackError(f"{context}.metrics do not match its packaged files")
        canonical_assets.append(asset)
    if not model_found:
        raise AssetPackError("assetpack requires a primary model/gltf-binary output")
    if canonical_assets != sorted(canonical_assets, key=lambda item: item["id"]):
        raise AssetPackError("assetpack.assets are not in canonical ID order")

    bindings = _verify_bindings(payload.get("bindings"), representations)
    if bindings != payload["bindings"]:
        raise AssetPackError("assetpack.bindings are not in canonical slot order")
    _validate_3d_entrypoints(bindings, glb_files)
    return payload


__all__ = [
    "ASSETPACK_FORMAT",
    "ASSETPACK_FORMAT_VERSION",
    "AssetPackError",
    "build_assetpack",
    "verify_assetpack",
]
