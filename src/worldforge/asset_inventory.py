from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from worldforge.asset_contracts import (
    ASSET_KINDS,
    KIND_REPRESENTATIONS,
    REPRESENTATIONS,
    SEMANTIC_SLOT_PATTERN,
)
from worldforge.asset_io import (
    AssetContractError,
    bind_content_hash,
    read_json_object,
    require_content_hash,
    sha256_file,
    write_json_atomic,
)
from worldforge.integrity import declared_hash_matches
from worldforge.validation import ID_PATTERN

DIMENSIONS = {"2d", "2_5d", "3d"}
DELIVERY_PROFILES = {"renderpack_v1", "assetpack_v1"}


def _verified_worldpack(path: Path) -> dict[str, Any]:
    raw = read_json_object(path, limit=64 * 1024 * 1024)
    if raw.get("format") != "isoworld.worldpack" or raw.get("format_version") not in {
        1,
        2,
        3,
        4,
        5,
    }:
        raise AssetContractError("The inventory input is not a compatible worldpack")
    if not declared_hash_matches(raw):
        raise AssetContractError("The worldpack content hash does not match its contents")
    world = raw.get("world")
    if not isinstance(world, dict) or not isinstance(world.get("id"), str):
        raise AssetContractError("The worldpack has no valid world identity")
    return raw


def create_asset_target(
    worldpack_path: str | Path,
    output_path: str | Path,
    *,
    target_id: str,
    dimension: str,
) -> dict[str, Any]:
    if not ID_PATTERN.fullmatch(target_id):
        raise AssetContractError("target_id must be a canonical lowercase ID")
    if dimension not in DIMENSIONS:
        raise AssetContractError("dimension must be 2d, 2_5d, or 3d")
    worldpack = _verified_worldpack(Path(worldpack_path))
    if dimension == "2d":
        coordinates: dict[str, Any] = {
            "origin": "top_left",
            "x_axis": "right",
            "y_axis": "down",
            "pixels_per_unit": 32,
        }
        delivery = "renderpack_v1"
        runtime_adapter: str | None = "isoworld_raylib_2_5d"
    elif dimension == "2_5d":
        coordinates = {
            "origin": "tile_anchor",
            "x_axis": "east",
            "y_axis": "south",
            "up_axis": "screen_up",
            "tile_width_pixels": 64,
            "tile_height_pixels": 32,
        }
        delivery = "renderpack_v1"
        runtime_adapter = "isoworld_raylib_2_5d"
    else:
        coordinates = {
            "handedness": "right",
            "up_axis": "Y",
            "forward_axis": "-Z",
            "units_per_meter": 1.0,
        }
        delivery = "assetpack_v1"
        runtime_adapter = None
    target = bind_content_hash(
        {
            "format": "rpg-world-forge.asset_target",
            "format_version": 1,
            "id": target_id,
            "world_id": worldpack["world"]["id"],
            "world_content_hash": worldpack["content_hash"],
            "dimension": dimension,
            "delivery_profile": delivery,
            "runtime_adapter": runtime_adapter,
            "coordinate_system": coordinates,
        }
    )
    write_json_atomic(output_path, target)
    return target


def _checked_contract(
    path: Path,
    *,
    expected_format: str,
    worldpack: dict[str, Any],
) -> dict[str, Any]:
    raw = read_json_object(path)
    if raw.get("format") != expected_format or raw.get("format_version") != 1:
        raise AssetContractError(f"{path} is not a supported {expected_format} contract")
    require_content_hash(raw, context=expected_format)
    if raw.get("world_id") != worldpack["world"]["id"]:
        raise AssetContractError(f"{expected_format} world_id does not match the worldpack")
    if raw.get("world_content_hash") != worldpack["content_hash"]:
        raise AssetContractError(f"{expected_format} is stale for the worldpack")
    return raw


def _collection(raw: dict[str, Any], name: str) -> list[dict[str, Any]]:
    collections = raw.get("collections")
    if not isinstance(collections, dict):
        return []
    value = collections.get(name, [])
    if not isinstance(value, list):
        return []
    return sorted(
        (item for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)),
        key=lambda item: item["id"],
    )


def _requirement(
    asset_id: str,
    kind: str,
    representation: str,
    purpose: str,
    sources: Iterable[str],
    slots: Iterable[str],
    *,
    required: bool,
) -> dict[str, Any]:
    return {
        "id": asset_id,
        "kind": kind,
        "representation": representation,
        "required": required,
        "purpose": purpose,
        "canonical_sources": sorted(set(sources)),
        "semantic_slots": sorted(set(slots)),
    }


def _derived_id(prefix: str, source_id: str, suffix: str) -> str:
    candidate = f"{prefix}_{source_id}_{suffix}" if suffix else f"{prefix}_{source_id}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    reserved = len(prefix) + len(suffix) + len(digest) + 3
    return f"{prefix}_{source_id[: 64 - reserved]}_{suffix}_{digest}"


def _visual_kind(dimension: str, subject: str) -> tuple[str, str]:
    if dimension == "3d":
        if subject == "actor":
            return "character_3d", "3d"
        return "environment_3d", "3d"
    if subject == "actor":
        return "spritesheet", dimension
    if subject == "terrain":
        return "tileset", dimension
    return "sprite", dimension


def _derived_requirements(worldpack: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    actor_kind, actor_representation = _visual_kind(dimension, "actor")
    for actor in _collection(worldpack, "actors"):
        actor_id = actor["id"]
        requirements.append(
            _requirement(
                _derived_id("actor", actor_id, "visual"),
                actor_kind,
                actor_representation,
                f"Gameplay representation for actor {actor_id}",
                [f"actors:{actor_id}"],
                [f"actor:{actor_id}"],
                required=True,
            )
        )
        requirements.append(
            _requirement(
                _derived_id("actor", actor_id, "portrait"),
                "portrait",
                "2d",
                f"Optional dialogue and roster portrait for actor {actor_id}",
                [f"actors:{actor_id}"],
                [f"portrait:{actor_id}"],
                required=False,
            )
        )

    terrain_kind, terrain_representation = _visual_kind(dimension, "terrain")
    for tile in _collection(worldpack, "tile_types"):
        tile_id = tile["id"]
        requirements.append(
            _requirement(
                _derived_id("terrain", tile_id, "visual"),
                terrain_kind,
                terrain_representation,
                f"Terrain representation for tile type {tile_id}",
                [f"tile_types:{tile_id}"],
                [f"tile_type:{tile_id}"],
                required=True,
            )
        )

    construction_kind, construction_representation = _visual_kind(dimension, "construction")
    for construction in _collection(worldpack, "constructions"):
        construction_id = construction["id"]
        requirements.append(
            _requirement(
                _derived_id("construction", construction_id, "visual"),
                construction_kind,
                construction_representation,
                f"Built-state representation for construction {construction_id}",
                [f"constructions:{construction_id}"],
                [f"construction:{construction_id}"],
                required=True,
            )
        )

    for ability in _collection(worldpack, "abilities"):
        ability_id = ability["id"]
        requirements.append(
            _requirement(
                _derived_id("ability", ability_id, "vfx"),
                "vfx_3d" if dimension == "3d" else "vfx",
                dimension,
                f"Optional activation feedback for ability {ability_id}",
                [f"abilities:{ability_id}"],
                [f"ability:{ability_id}"],
                required=False,
            )
        )

    for scene in _collection(worldpack, "scenes"):
        scene_id = scene["id"]
        requirements.append(
            _requirement(
                _derived_id("scene", scene_id, "illustration"),
                "portrait",
                "2d",
                f"Optional authored illustration for scene {scene_id}",
                [f"scenes:{scene_id}"],
                [f"scene:{scene_id}"],
                required=False,
            )
        )

    requirements.extend(
        (
            _requirement(
                "ui_default_font",
                "font",
                "2d",
                "Default readable UI typeface",
                ["world:localization"],
                ["ui:font"],
                # The reference runtime has a built-in fallback. A custom font
                # remains inventory-visible but must not block an otherwise
                # complete 2D/2.5D or 3D release.
                required=False,
            ),
            _requirement(
                "music_default",
                "music",
                "audio",
                "Default exploration music bed",
                ["world:genre", "world:themes"],
                ["music:default"],
                required=True,
            ),
            _requirement(
                "interaction_completed_sfx",
                "sfx",
                "audio",
                "Feedback for a completed contextual interaction",
                ["world:mechanics"],
                ["event:interaction_completed"],
                required=True,
            ),
        )
    )
    return sorted(requirements, key=lambda item: item["id"])


def derive_asset_inventory(
    worldpack_path: str | Path,
    target_path: str | Path,
    visual_bible_path: str | Path,
    audio_bible_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Derive a stable inventory from locked canon and approved direction contracts."""

    worldpack = _verified_worldpack(Path(worldpack_path))
    target_file = Path(target_path)
    target = _checked_contract(
        target_file,
        expected_format="rpg-world-forge.asset_target",
        worldpack=worldpack,
    )
    visual_file = Path(visual_bible_path)
    visual = _checked_contract(
        visual_file,
        expected_format="rpg-world-forge.visual_bible",
        worldpack=worldpack,
    )
    audio_file = Path(audio_bible_path)
    audio = _checked_contract(
        audio_file,
        expected_format="rpg-world-forge.audio_bible",
        worldpack=worldpack,
    )
    for contract, label in ((visual, "visual bible"), (audio, "audio bible")):
        if contract.get("target_id") != target.get("id"):
            raise AssetContractError(f"The {label} targets a different asset target")
        if contract.get("target_hash") != target.get("content_hash"):
            raise AssetContractError(f"The {label} is stale for the asset target")
        if not isinstance(contract.get("approved_by"), str) or not contract["approved_by"].strip():
            raise AssetContractError(f"The {label} must be approved before inventory derivation")
    dimension = target.get("dimension")
    if dimension not in DIMENSIONS:
        raise AssetContractError("The asset target has an unsupported dimension")
    inventory = bind_content_hash(
        {
            "format": "rpg-world-forge.asset_inventory",
            "format_version": 1,
            "world_id": worldpack["world"]["id"],
            "world_content_hash": worldpack["content_hash"],
            "target_id": target["id"],
            "target_hash": target["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
            "requirements": _derived_requirements(worldpack, dimension),
            "manual_additions": [],
        }
    )
    write_json_atomic(output_path, inventory)
    return inventory


def validate_asset_inventory(
    inventory_path: str | Path,
    *,
    worldpack_path: str | Path | None = None,
) -> list[str]:
    try:
        raw = read_json_object(inventory_path)
    except AssetContractError as exc:
        return [str(exc)]
    issues: list[str] = []
    expected_fields = {
        "format",
        "format_version",
        "world_id",
        "world_content_hash",
        "target_id",
        "target_hash",
        "visual_bible_hash",
        "audio_bible_hash",
        "requirements",
        "manual_additions",
        "content_hash",
    }
    missing_fields = sorted(expected_fields - set(raw))
    unknown_fields = sorted(set(raw) - expected_fields)
    if missing_fields:
        issues.append(f"missing fields: {', '.join(missing_fields)}")
    if unknown_fields:
        issues.append(f"unknown fields: {', '.join(unknown_fields)}")
    if raw.get("format") != "rpg-world-forge.asset_inventory" or raw.get("format_version") != 1:
        issues.append("unsupported asset inventory")
    try:
        require_content_hash(raw, context="asset inventory")
    except AssetContractError as exc:
        issues.append(str(exc))
    for field in ("world_id", "target_id"):
        if not isinstance(raw.get(field), str) or not ID_PATTERN.fullmatch(raw[field]):
            issues.append(f"invalid {field}")
    for field in (
        "world_content_hash",
        "target_hash",
        "visual_bible_hash",
        "audio_bible_hash",
    ):
        value = raw.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            issues.append(f"{field} must be a lowercase SHA-256 digest")
    requirements = raw.get("requirements")
    additions = raw.get("manual_additions")
    if not isinstance(requirements, list):
        issues.append("requirements must be a list")
        requirements = []
    elif not requirements:
        issues.append("requirements must not be empty")
    if not isinstance(additions, list):
        issues.append("manual_additions must be a list")
        additions = []
    seen_ids: set[str] = set()
    seen_slots: set[str] = set()
    for collection_name, entries in (
        ("requirements", requirements),
        ("manual_additions", additions),
    ):
        canonical_ids = [
            item.get("id")
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if len(canonical_ids) == len(entries) and canonical_ids != sorted(canonical_ids):
            issues.append(f"{collection_name} must use canonical ID order")
        for index, item in enumerate(entries):
            context = f"{collection_name}/{index}"
            if not isinstance(item, dict):
                issues.append(f"{context} must be an object")
                continue
            expected_item_fields = {
                "id",
                "kind",
                "representation",
                "required",
                "purpose",
                "canonical_sources",
                "semantic_slots",
            }
            missing_item = sorted(expected_item_fields - set(item))
            unknown_item = sorted(set(item) - expected_item_fields)
            if missing_item:
                issues.append(f"{context} missing fields: {', '.join(missing_item)}")
            if unknown_item:
                issues.append(f"{context} unknown fields: {', '.join(unknown_item)}")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
                issues.append(f"{context}/id is invalid")
            elif item_id in seen_ids:
                issues.append(f"duplicate inventory ID: {item_id}")
            else:
                seen_ids.add(item_id)
            if not isinstance(item.get("required"), bool):
                issues.append(f"{context}/required must be boolean")
            kind = item.get("kind")
            representation = item.get("representation")
            if not isinstance(kind, str) or kind not in ASSET_KINDS:
                issues.append(f"{context}/kind is invalid")
            if not isinstance(representation, str) or representation not in REPRESENTATIONS:
                issues.append(f"{context}/representation is invalid")
            if (
                isinstance(kind, str)
                and kind in KIND_REPRESENTATIONS
                and isinstance(representation, str)
                and representation not in KIND_REPRESENTATIONS[kind]
            ):
                allowed = ", ".join(sorted(KIND_REPRESENTATIONS[kind]))
                issues.append(f"{context}/representation must be {allowed} for {kind}")
            purpose = item.get("purpose")
            if not isinstance(purpose, str) or not purpose or len(purpose) > 4096:
                issues.append(f"{context}/purpose must be non-empty bounded text")
            for field in ("canonical_sources", "semantic_slots"):
                values = item.get(field)
                required_values = field == "canonical_sources"
                if (
                    not isinstance(values, list)
                    or (required_values and not values)
                    or not all(isinstance(value, str) and value for value in values)
                    or values != sorted(set(values))
                ):
                    issues.append(f"{context}/{field} must be a sorted unique string list")
            for slot in (
                item.get("semantic_slots", [])
                if isinstance(item.get("semantic_slots"), list)
                else []
            ):
                if not isinstance(slot, str) or SEMANTIC_SLOT_PATTERN.fullmatch(slot) is None:
                    issues.append(f"{context}/semantic_slots contains an invalid slot")
                    continue
                if slot in seen_slots:
                    issues.append(f"duplicate inventory semantic slot: {slot}")
                seen_slots.add(slot)
    if worldpack_path is not None:
        try:
            worldpack = _verified_worldpack(Path(worldpack_path))
        except AssetContractError as exc:
            issues.append(str(exc))
        else:
            if raw.get("world_id") != worldpack["world"]["id"]:
                issues.append("inventory world_id does not match the worldpack")
            if raw.get("world_content_hash") != worldpack["content_hash"]:
                issues.append("inventory is stale for the worldpack")
    return issues


def contract_sha256(path: str | Path) -> str:
    """Return a file hash for manifest references without reserializing it."""

    return sha256_file(path)
