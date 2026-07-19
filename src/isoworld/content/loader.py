from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from isoworld.content.models import (
    ActorDefinition,
    MapDefinition,
    Spawn,
    TileType,
    WorldPack,
)


class WorldPackError(ValueError):
    """Raised when a compiled pack cannot be loaded safely."""


def _color(raw: Any, context: str) -> tuple[int, int, int, int]:
    if not isinstance(raw, list) or len(raw) not in (3, 4):
        raise WorldPackError(f"{context}: color must contain 3 or 4 integers")
    values = tuple(int(value) for value in raw)
    if any(value < 0 or value > 255 for value in values):
        raise WorldPackError(f"{context}: color channel outside 0..255")
    if len(values) == 3:
        return values + (255,)
    return values  # type: ignore[return-value]


def load_worldpack(path: str | Path) -> WorldPack:
    pack_path = Path(path)
    try:
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorldPackError(f"Could not load {pack_path}: {exc}") from exc

    if raw.get("format") != "isoworld.worldpack":
        raise WorldPackError("Unknown worldpack format")
    if raw.get("format_version") != 1:
        raise WorldPackError("Unsupported worldpack version")

    world = raw["world"]
    tile_types = {
        item["id"]: TileType(
            id=item["id"],
            display_name=item["display_name"],
            color=_color(item["color"], f"tile_types/{item['id']}"),
            walkable=bool(item["walkable"]),
            arable=bool(item["arable"]),
            height=int(item.get("height", 0)),
        )
        for item in raw["collections"]["tile_types"]
    }
    maps = {
        item["id"]: MapDefinition(
            id=item["id"],
            display_name=item["display_name"],
            width=int(item["width"]),
            height=int(item["height"]),
            rows=tuple(item["rows"]),
            legend=dict(item["legend"]),
        )
        for item in raw["collections"]["maps"]
    }
    actors = {
        item["id"]: ActorDefinition(
            id=item["id"],
            display_name=item["display_name"],
            playable=bool(item["playable"]),
            spawn=Spawn(
                map_id=item["spawn"]["map_id"],
                x=int(item["spawn"]["x"]),
                y=int(item["spawn"]["y"]),
            ),
            color=_color(item["color"], f"actors/{item['id']}"),
            personal_arc_id=item.get("personal_arc_id"),
        )
        for item in raw["collections"]["actors"]
    }

    collections = {
        key: tuple(value)
        for key, value in raw["collections"].items()
        if key not in {"tile_types", "maps", "actors"}
    }
    pack = WorldPack(
        world_id=world["id"],
        title=world["title"],
        language=world["language"],
        start_map_id=world["start_map_id"],
        content_hash=raw["content_hash"],
        ui=dict(world["ui"]),
        tile_types=tile_types,
        maps=maps,
        actors=actors,
        collections=collections,
    )
    if pack.start_map_id not in pack.maps:
        raise WorldPackError("The starting map does not exist")
    if not pack.playable_actor_ids:
        raise WorldPackError("The worldpack contains no playable actors")
    return pack
