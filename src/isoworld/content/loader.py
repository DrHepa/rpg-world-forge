from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from isoworld.content.models import (
    AbilityDefinition,
    ActorDefinition,
    ClockDefinition,
    EffectDefinition,
    InteractionDefinition,
    Location,
    MapDefinition,
    ScheduleDefinition,
    ScheduleEntry,
    Spawn,
    TileType,
    WorldPack,
)


class WorldPackError(ValueError):
    """Raised when a compiled pack cannot be loaded safely."""


def _integer(raw: Any, context: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise WorldPackError(f"{context}: must be an integer")
    return raw


def _boolean(raw: Any, context: str) -> bool:
    if not isinstance(raw, bool):
        raise WorldPackError(f"{context}: must be a boolean")
    return raw


def _integer_dict(raw: Any, context: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise WorldPackError(f"{context}: must be an object")
    result: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise WorldPackError(f"{context}: keys must be strings")
        result[key] = _integer(value, f"{context}/{key}")
    return result


def _string_tuple(raw: Any, context: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise WorldPackError(f"{context}: must be a list of strings")
    return tuple(raw)


def _color(raw: Any, context: str) -> tuple[int, int, int, int]:
    if not isinstance(raw, list) or len(raw) not in (3, 4):
        raise WorldPackError(f"{context}: color must contain 3 or 4 integers")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in raw):
        raise WorldPackError(f"{context}: color channels must be integers")
    values = tuple(raw)
    if any(value < 0 or value > 255 for value in values):
        raise WorldPackError(f"{context}: color channel outside 0..255")
    if len(values) == 3:
        return values + (255,)
    return values  # type: ignore[return-value]


def _effect(raw: dict[str, Any], context: str) -> EffectDefinition:
    try:
        return EffectDefinition(
            kind=str(raw["kind"]),
            target=str(raw.get("target", "self")),
            resource=raw.get("resource"),
            amount=_integer(raw.get("amount", 0), f"{context}/amount"),
            flag=raw.get("flag"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldPackError(f"{context}: invalid effect") from exc


def _location(raw: dict[str, Any], context: str) -> Location:
    try:
        if not isinstance(raw["map_id"], str):
            raise TypeError
        return Location(
            map_id=raw["map_id"],
            x=_integer(raw["x"], f"{context}/x"),
            y=_integer(raw["y"], f"{context}/y"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldPackError(f"{context}: invalid location") from exc


def _verify_hash(raw: dict[str, Any]) -> str:
    supplied = raw.get("content_hash")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise WorldPackError("The worldpack has no valid content hash")
    payload = dict(raw)
    payload.pop("content_hash", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != supplied:
        raise WorldPackError("The worldpack content hash does not match its contents")
    return supplied


def _validate_runtime_pack(pack: WorldPack) -> None:
    clock = pack.clock
    if (
        clock.start_day < 1
        or not 0 <= clock.start_minute < 1440
        or clock.ticks_per_minute < 1
        or clock.movement_interval_ticks < 1
    ):
        raise WorldPackError("The world clock configuration is invalid")
    for tile in pack.tile_types.values():
        if not isinstance(tile.walkable, bool) or not isinstance(tile.arable, bool):
            raise WorldPackError(f"Tile {tile.id} has invalid boolean properties")
    for world_map in pack.maps.values():
        if (
            world_map.width < 1
            or world_map.height < 1
            or len(world_map.rows) != world_map.height
            or any(len(row) != world_map.width for row in world_map.rows)
        ):
            raise WorldPackError(f"Map {world_map.id} has invalid dimensions")
        for row in world_map.rows:
            for symbol in row:
                tile_id = world_map.legend.get(symbol)
                if not isinstance(tile_id, str) or tile_id not in pack.tile_types:
                    raise WorldPackError(f"Map {world_map.id} references an unknown tile")
    occupied: set[tuple[str, int, int]] = set()
    for actor in pack.actors.values():
        if actor.spawn.map_id not in pack.maps or not pack.is_walkable(
            actor.spawn.map_id, actor.spawn.x, actor.spawn.y
        ):
            raise WorldPackError(f"Actor {actor.id} has an invalid spawn")
        cell = (actor.spawn.map_id, actor.spawn.x, actor.spawn.y)
        if cell in occupied:
            raise WorldPackError("Two actors share a spawn cell")
        occupied.add(cell)
        if any(value < 0 for _, value in actor.resources):
            raise WorldPackError(f"Actor {actor.id} has a negative resource")
        if actor.schedule_id is not None and (
            not isinstance(actor.schedule_id, str) or actor.schedule_id not in pack.schedules
        ):
            raise WorldPackError(f"Actor {actor.id} references an unknown schedule")
        if actor.schedule_mode not in {"always", "when_inactive", "never"}:
            raise WorldPackError(f"Actor {actor.id} has an invalid schedule mode")
        if any(
            not isinstance(ability_id, str) or ability_id not in pack.abilities
            for ability_id in actor.ability_ids
        ):
            raise WorldPackError(f"Actor {actor.id} references an unknown ability")
    for ability in pack.abilities.values():
        if (
            ability.target not in {"self", "actor"}
            or ability.range < 0
            or ability.cooldown_minutes < 0
            or not ability.costs
            or any(cost <= 0 for cost in ability.costs.values())
        ):
            raise WorldPackError(f"Ability {ability.id} has an invalid contract")
    for schedule in pack.schedules.values():
        if not schedule.entries:
            raise WorldPackError(f"Schedule {schedule.id} has no entries")
        for entry in schedule.entries:
            if not entry.destinations or not 0 <= entry.start_minute <= 1439:
                raise WorldPackError(f"Schedule {schedule.id} has an invalid segment")
            if not 0 <= entry.end_minute <= 1440 or entry.start_minute == entry.end_minute:
                raise WorldPackError(f"Schedule {schedule.id} has an invalid segment")
            if any(
                location.map_id not in pack.maps
                or not pack.is_walkable(location.map_id, location.x, location.y)
                for location in entry.destinations
            ):
                raise WorldPackError(f"Schedule {schedule.id} has an invalid destination")
    for interaction in pack.interactions.values():
        location = interaction.location
        world_map = pack.maps.get(location.map_id)
        if (
            world_map is None
            or location.x < 0
            or location.y < 0
            or location.x >= world_map.width
            or location.y >= world_map.height
            or interaction.range < 0
            or not interaction.effects
        ):
            raise WorldPackError(f"Interaction {interaction.id} has an invalid contract")
    supported_effects = {"set_flag", "clear_flag", "change_resource"}
    for effects, context in [
        *((ability.effects, f"Ability {ability.id}") for ability in pack.abilities.values()),
        *((item.effects, f"Interaction {item.id}") for item in pack.interactions.values()),
    ]:
        if any(effect.kind not in supported_effects for effect in effects):
            raise WorldPackError(f"{context} has an unsupported effect")


def load_worldpack(path: str | Path) -> WorldPack:
    pack_path = Path(path)
    try:
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorldPackError(f"Could not load {pack_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorldPackError("The worldpack root must be an object")
    if raw.get("format") != "isoworld.worldpack":
        raise WorldPackError("Unknown worldpack format")
    version = raw.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2}:
        raise WorldPackError("Unsupported worldpack version")
    content_hash = _verify_hash(raw)

    try:
        world = raw["world"]
        collections = raw["collections"]
        if not isinstance(world, dict) or not isinstance(collections, dict):
            raise TypeError
        if not all(
            isinstance(name, str)
            and isinstance(items, list)
            and all(isinstance(item, dict) for item in items)
            for name, items in collections.items()
        ):
            raise TypeError("collections must contain lists of objects")
        tile_types = {
            item["id"]: TileType(
                id=item["id"],
                display_name=item["display_name"],
                color=_color(item["color"], f"tile_types/{item['id']}"),
                walkable=_boolean(item["walkable"], f"tile_types/{item['id']}/walkable"),
                arable=_boolean(item["arable"], f"tile_types/{item['id']}/arable"),
                height=_integer(item.get("height", 0), f"tile_types/{item['id']}/height"),
            )
            for item in collections["tile_types"]
        }
        maps = {
            item["id"]: MapDefinition(
                id=item["id"],
                display_name=item["display_name"],
                width=_integer(item["width"], f"maps/{item['id']}/width"),
                height=_integer(item["height"], f"maps/{item['id']}/height"),
                rows=_string_tuple(item["rows"], f"maps/{item['id']}/rows"),
                legend=dict(item["legend"]),
            )
            for item in collections["maps"]
        }
        abilities = {
            item["id"]: AbilityDefinition(
                id=item["id"],
                display_name=item["display_name"],
                target=item.get("target", "self"),
                range=_integer(item.get("range", 0), f"abilities/{item['id']}/range"),
                costs=_integer_dict(item.get("costs", {}), f"abilities/{item['id']}/costs"),
                cooldown_minutes=_integer(
                    item.get("cooldown_minutes", 0),
                    f"abilities/{item['id']}/cooldown_minutes",
                ),
                effects=tuple(
                    _effect(effect, f"abilities/{item['id']}/effects")
                    for effect in item.get("effects", [])
                ),
            )
            for item in collections.get("abilities", [])
        }
        schedules: dict[str, ScheduleDefinition] = {}
        for item in collections.get("schedules", []):
            entries: list[ScheduleEntry] = []
            for position, entry in enumerate(item.get("entries", [])):
                destinations = [_location(entry, f"schedules/{item['id']}/entries/{position}")]
                destinations.extend(
                    _location(value, f"schedules/{item['id']}/entries/{position}/fallbacks")
                    for value in entry.get("fallbacks", [])
                )
                entries.append(
                    ScheduleEntry(
                        start_minute=_integer(
                            entry["start_minute"],
                            f"schedules/{item['id']}/entries/{position}/start_minute",
                        ),
                        end_minute=_integer(
                            entry["end_minute"],
                            f"schedules/{item['id']}/entries/{position}/end_minute",
                        ),
                        activity=str(entry["activity"]),
                        destinations=tuple(destinations),
                    )
                )
            schedules[item["id"]] = ScheduleDefinition(item["id"], tuple(entries))
        interactions = {
            item["id"]: InteractionDefinition(
                id=item["id"],
                display_name=item["display_name"],
                prompt=item["prompt"],
                location=_location(item, f"interactions/{item['id']}"),
                range=_integer(item.get("range", 1), f"interactions/{item['id']}/range"),
                required_flags=frozenset(item.get("required_flags", [])),
                forbidden_flags=frozenset(item.get("forbidden_flags", [])),
                repeatable=_boolean(
                    item.get("repeatable", False), f"interactions/{item['id']}/repeatable"
                ),
                effects=tuple(
                    _effect(effect, f"interactions/{item['id']}/effects")
                    for effect in item.get("effects", [])
                ),
            )
            for item in collections.get("interactions", [])
        }
        actors = {
            item["id"]: ActorDefinition(
                id=item["id"],
                display_name=item["display_name"],
                playable=_boolean(item["playable"], f"actors/{item['id']}/playable"),
                spawn=Spawn(
                    map_id=item["spawn"]["map_id"],
                    x=_integer(item["spawn"]["x"], f"actors/{item['id']}/spawn/x"),
                    y=_integer(item["spawn"]["y"], f"actors/{item['id']}/spawn/y"),
                ),
                color=_color(item["color"], f"actors/{item['id']}"),
                personal_arc_id=item.get("personal_arc_id"),
                schedule_id=item.get("schedule_id"),
                schedule_mode=item.get(
                    "schedule_mode", "when_inactive" if item["playable"] else "always"
                ),
                ability_ids=_string_tuple(
                    item.get("ability_ids", []), f"actors/{item['id']}/ability_ids"
                ),
                resources=tuple(
                    sorted(
                        _integer_dict(
                            item.get("resources", {}), f"actors/{item['id']}/resources"
                        ).items()
                    )
                ),
            )
            for item in collections["actors"]
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldPackError(f"Malformed worldpack: {exc}") from exc

    simulation = world.get("simulation", {})
    if not isinstance(simulation, dict):
        raise WorldPackError("world/simulation must be an object")
    clock = ClockDefinition(
        start_day=_integer(simulation.get("start_day", 1), "world/simulation/start_day"),
        start_minute=_integer(
            simulation.get("start_minute", 8 * 60), "world/simulation/start_minute"
        ),
        ticks_per_minute=_integer(
            simulation.get("ticks_per_minute", 20), "world/simulation/ticks_per_minute"
        ),
        movement_interval_ticks=_integer(
            simulation.get("movement_interval_ticks", 4),
            "world/simulation/movement_interval_ticks",
        ),
    )
    default_ui = {
        "active_actor": "Active character",
        "move_help": "Arrow keys or WASD: move",
        "switch_help": "Tab: switch character",
        "navigate_help": "Left click: navigate",
        "interact_help": "E: interact",
        "ability_help": "1: use first ability",
        "clock_label": "Day",
    }
    if not isinstance(world.get("ui"), dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in world["ui"].items()
    ):
        raise WorldPackError("world/ui must be an object of strings")
    default_ui.update(world["ui"])
    extra_collections = {
        key: tuple(value)
        for key, value in collections.items()
        if key not in {"tile_types", "maps", "actors", "abilities", "schedules", "interactions"}
    }
    for field in ("id", "title", "language", "start_map_id"):
        if not isinstance(world.get(field), str):
            raise WorldPackError(f"world/{field} must be a string")
    pack = WorldPack(
        format_version=version,
        world_id=world["id"],
        title=world["title"],
        language=world["language"],
        start_map_id=world["start_map_id"],
        content_hash=content_hash,
        ui=default_ui,
        clock=clock,
        tile_types=tile_types,
        maps=maps,
        actors=actors,
        abilities=abilities,
        schedules=schedules,
        interactions=interactions,
        collections=extra_collections,
    )
    if pack.start_map_id not in pack.maps:
        raise WorldPackError("The starting map does not exist")
    if not pack.playable_actor_ids:
        raise WorldPackError("The worldpack contains no playable actors")
    _validate_runtime_pack(pack)
    return pack
