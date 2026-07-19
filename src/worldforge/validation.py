from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from worldforge.project import SourceProject


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PLACEHOLDER_PATTERN = re.compile(
    r"(\{\{[^}]+\}\}|\bTODO\b|\bTBD\b|<\s*(?:fill|replace|pending)[^>]*>)",
    re.IGNORECASE,
)
REQUIRED_COLLECTIONS = ("tile_types", "maps", "actors")
KNOWN_COLLECTIONS = (
    "tile_types",
    "maps",
    "actors",
    "facts",
    "factions",
    "abilities",
    "schedules",
    "dialogues",
    "quests",
    "scenes",
    "personal_arcs",
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _walk_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}/{index}")


def _require(item: dict[str, Any], fields: tuple[str, ...], path: str) -> list[ValidationIssue]:
    return [
        ValidationIssue(f"{path}/{field}", "required field is missing")
        for field in fields
        if field not in item
    ]


def _valid_id(value: Any, path: str) -> list[ValidationIssue]:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        return [ValidationIssue(path, "invalid ID; use 2..64-character ASCII snake_case")]
    return []


def _validate_color(value: Any, path: str) -> list[ValidationIssue]:
    if not isinstance(value, list) or len(value) not in (3, 4):
        return [ValidationIssue(path, "color must contain 3 or 4 channels")]
    if any(not isinstance(channel, int) or channel < 0 or channel > 255 for channel in value):
        return [ValidationIssue(path, "each channel must be an integer in 0..255")]
    return []


def validate_project(
    project: SourceProject,
    *,
    profile: str = "release",
) -> list[ValidationIssue]:
    if profile not in {"draft", "release"}:
        raise ValueError("profile must be draft or release")
    issues: list[ValidationIssue] = []
    world = project.world
    issues.extend(
        _require(world, ("id", "title", "language", "start_map_id", "ui"), "world")
    )
    if "id" in world:
        issues.extend(_valid_id(world["id"], "world/id"))
    if not isinstance(world.get("title"), str) or not world.get("title", "").strip():
        issues.append(ValidationIssue("world/title", "must contain a title"))
    if not isinstance(world.get("language"), str) or len(world.get("language", "")) < 2:
        issues.append(ValidationIssue("world/language", "invalid language"))
    ui = world.get("ui")
    required_ui = {"move_help", "switch_help", "active_actor"}
    if not isinstance(ui, dict):
        issues.append(ValidationIssue("world/ui", "must be an object of localized strings"))
    else:
        for key in sorted(required_ui - set(ui)):
            issues.append(ValidationIssue(f"world/ui/{key}", "UI string is missing"))

    for collection in REQUIRED_COLLECTIONS:
        if collection not in project.collections:
            issues.append(ValidationIssue(f"collections/{collection}", "required collection is missing"))
    for collection in project.collections:
        if collection not in KNOWN_COLLECTIONS:
            issues.append(ValidationIssue(f"collections/{collection}", "unknown collection"))

    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for collection, items in project.collections.items():
        index: dict[str, dict[str, Any]] = {}
        for position, item in enumerate(items):
            path = f"collections/{collection}/{position}"
            if "id" not in item:
                issues.append(ValidationIssue(f"{path}/id", "required field is missing"))
                continue
            issues.extend(_valid_id(item["id"], f"{path}/id"))
            if item["id"] in index:
                issues.append(ValidationIssue(f"{path}/id", f"duplicate ID: {item['id']}"))
            index[item["id"]] = item
        indexes[collection] = index

    tile_types = indexes.get("tile_types", {})
    maps = indexes.get("maps", {})
    actors = indexes.get("actors", {})
    facts = indexes.get("facts", {})
    factions = indexes.get("factions", {})
    abilities = indexes.get("abilities", {})
    schedules = indexes.get("schedules", {})
    arcs = indexes.get("personal_arcs", {})

    policy = world.get("content_policy", {})
    if not isinstance(policy, dict):
        issues.append(ValidationIssue("world/content_policy", "must be an object"))
        policy = {}

    for tile_id, tile in tile_types.items():
        path = f"tile_types/{tile_id}"
        issues.extend(_require(tile, ("display_name", "color", "walkable", "arable"), path))
        if "color" in tile:
            issues.extend(_validate_color(tile["color"], f"{path}/color"))
        for field in ("walkable", "arable"):
            if field in tile and not isinstance(tile[field], bool):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a boolean"))

    for map_id, world_map in maps.items():
        path = f"maps/{map_id}"
        issues.extend(_require(world_map, ("display_name", "width", "height", "rows", "legend"), path))
        width = world_map.get("width")
        height = world_map.get("height")
        rows = world_map.get("rows")
        legend = world_map.get("legend")
        if not isinstance(width, int) or width <= 0:
            issues.append(ValidationIssue(f"{path}/width", "must be a positive integer"))
        if not isinstance(height, int) or height <= 0:
            issues.append(ValidationIssue(f"{path}/height", "must be a positive integer"))
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            issues.append(ValidationIssue(f"{path}/rows", "must be a list of strings"))
            rows = []
        if isinstance(height, int) and len(rows) != height:
            issues.append(ValidationIssue(f"{path}/rows", "row count differs from height"))
        for index, row in enumerate(rows):
            if isinstance(width, int) and len(row) != width:
                issues.append(ValidationIssue(f"{path}/rows/{index}", "row length differs from width"))
        if not isinstance(legend, dict):
            issues.append(ValidationIssue(f"{path}/legend", "must be a character -> tile_id object"))
            legend = {}
        for symbol, tile_id in legend.items():
            if not isinstance(symbol, str) or len(symbol) != 1:
                issues.append(ValidationIssue(f"{path}/legend", "each symbol must be one character"))
            if tile_id not in tile_types:
                issues.append(ValidationIssue(f"{path}/legend/{symbol}", f"unknown tile: {tile_id}"))
        for row_index, row in enumerate(rows):
            for column, symbol in enumerate(row):
                if symbol not in legend:
                    issues.append(
                        ValidationIssue(
                            f"{path}/rows/{row_index}/{column}",
                            f"symbol is missing from legend: {symbol}",
                        )
                    )

    start_map = world.get("start_map_id")
    if start_map not in maps:
        issues.append(ValidationIssue("world/start_map_id", f"unknown map: {start_map}"))

    for actor_id, actor in actors.items():
        path = f"actors/{actor_id}"
        issues.extend(_require(actor, ("display_name", "playable", "spawn", "color"), path))
        if "playable" in actor and not isinstance(actor["playable"], bool):
            issues.append(ValidationIssue(f"{path}/playable", "must be a boolean"))
        if "color" in actor:
            issues.extend(_validate_color(actor["color"], f"{path}/color"))
        spawn = actor.get("spawn")
        if not isinstance(spawn, dict):
            issues.append(ValidationIssue(f"{path}/spawn", "must be an object"))
        else:
            issues.extend(_require(spawn, ("map_id", "x", "y"), f"{path}/spawn"))
            map_id = spawn.get("map_id")
            if map_id not in maps:
                issues.append(ValidationIssue(f"{path}/spawn/map_id", f"unknown map: {map_id}"))
            elif all(isinstance(spawn.get(key), int) for key in ("x", "y")):
                world_map = maps[map_id]
                x, y = spawn["x"], spawn["y"]
                if x < 0 or y < 0 or x >= world_map["width"] or y >= world_map["height"]:
                    issues.append(ValidationIssue(f"{path}/spawn", "position is outside the map"))
                else:
                    tile_id = world_map["legend"][world_map["rows"][y][x]]
                    if not tile_types[tile_id]["walkable"]:
                        issues.append(ValidationIssue(f"{path}/spawn", "position is on a non-walkable tile"))
        arc_id = actor.get("personal_arc_id")
        if arc_id is not None and arc_id not in arcs:
            issues.append(ValidationIssue(f"{path}/personal_arc_id", f"unknown arc: {arc_id}"))
        schedule_id = actor.get("schedule_id")
        if schedule_id is not None and schedule_id not in schedules:
            issues.append(ValidationIssue(f"{path}/schedule_id", f"unknown schedule: {schedule_id}"))
        for field, index in (("ability_ids", abilities), ("faction_ids", factions)):
            refs = actor.get(field, [])
            if not isinstance(refs, list):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a list"))
            else:
                for ref in refs:
                    if ref not in index:
                        issues.append(ValidationIssue(f"{path}/{field}", f"unknown reference: {ref}"))
        knowledge = actor.get("knowledge", {})
        if not isinstance(knowledge, dict):
            issues.append(ValidationIssue(f"{path}/knowledge", "must be an object"))
        else:
            groups: dict[str, set[str]] = {}
            for group in ("knows", "suspects", "secrets", "forbidden"):
                refs = knowledge.get(group, [])
                if not isinstance(refs, list):
                    issues.append(ValidationIssue(f"{path}/knowledge/{group}", "must be a list"))
                    continue
                groups[group] = set(refs)
                for ref in refs:
                    if ref not in facts:
                        issues.append(
                            ValidationIssue(
                                f"{path}/knowledge/{group}", f"unknown fact: {ref}"
                            )
                        )
            forbidden = groups.get("forbidden", set())
            known = groups.get("knows", set()) | groups.get("suspects", set()) | groups.get("secrets", set())
            for conflict in sorted(forbidden & known):
                issues.append(
                    ValidationIssue(
                        f"{path}/knowledge",
                        f"fact {conflict} is both known and forbidden",
                    )
                )

    playable_actors = [actor for actor in actors.values() if actor.get("playable") is True]
    if profile == "release" and not playable_actors:
        issues.append(ValidationIssue("actors", "at least one playable actor is required"))
    expected_playable = policy.get("exact_playable_actor_count")
    if expected_playable is not None and profile == "release":
        if not isinstance(expected_playable, int) or expected_playable < 1:
            issues.append(
                ValidationIssue(
                    "world/content_policy/exact_playable_actor_count",
                    "must be a positive integer",
                )
            )
        elif len(playable_actors) != expected_playable:
            issues.append(
                ValidationIssue(
                    "actors",
                    f"expected {expected_playable} playable actors, found {len(playable_actors)}",
                )
            )
    if profile == "release" and policy.get("playable_requires_personal_arc") is True:
        for actor in playable_actors:
            if "personal_arc_id" not in actor:
                issues.append(
                    ValidationIssue(
                        f"actors/{actor['id']}/personal_arc_id",
                        "project requires a personal arc for every playable actor",
                    )
                )

    for arc_id, arc in arcs.items():
        path = f"personal_arcs/{arc_id}"
        issues.extend(_require(arc, ("actor_id", "acts"), path))
        actor_id = arc.get("actor_id")
        if actor_id not in actors:
            issues.append(ValidationIssue(f"{path}/actor_id", f"unknown actor: {actor_id}"))
        elif actors[actor_id].get("personal_arc_id") != arc_id:
            issues.append(ValidationIssue(f"{path}/actor_id", "actor does not reference this arc"))
        acts = arc.get("acts")
        if not isinstance(acts, list) or not acts:
            issues.append(ValidationIssue(f"{path}/acts", "must contain at least one act"))

    for schedule_id, schedule in schedules.items():
        path = f"schedules/{schedule_id}"
        entries = schedule.get("entries")
        if not isinstance(entries, list) or not entries:
            issues.append(ValidationIssue(f"{path}/entries", "must contain schedule segments"))
            continue
        for index, entry in enumerate(entries):
            entry_path = f"{path}/entries/{index}"
            if not isinstance(entry, dict):
                issues.append(ValidationIssue(entry_path, "must be an object"))
                continue
            issues.extend(_require(entry, ("start_minute", "end_minute", "map_id", "x", "y", "activity"), entry_path))
            if entry.get("map_id") not in maps:
                issues.append(ValidationIssue(f"{entry_path}/map_id", "unknown map"))

    for collection, items in (("dialogues", indexes.get("dialogues", {})), ("quests", indexes.get("quests", {}))):
        for item_id, item in items.items():
            nodes = item.get("nodes") if collection == "dialogues" else item.get("stages")
            field = "nodes" if collection == "dialogues" else "stages"
            path = f"{collection}/{item_id}/{field}"
            if not isinstance(nodes, list) or not nodes:
                issues.append(ValidationIssue(path, "must contain elements"))
                continue
            node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
            for position, node in enumerate(nodes):
                if not isinstance(node, dict):
                    issues.append(ValidationIssue(f"{path}/{position}", "must be an object"))
                    continue
                for target in node.get("next", []):
                    if target not in node_ids:
                        issues.append(ValidationIssue(f"{path}/{position}/next", f"unknown target: {target}"))

    for path, value in _walk_strings(world, "world"):
        if PLACEHOLDER_PATTERN.search(value):
            issues.append(ValidationIssue(path, "unresolved placeholder"))
    for collection, items in project.collections.items():
        for index, item in enumerate(items):
            for path, value in _walk_strings(item, f"collections/{collection}/{index}"):
                if PLACEHOLDER_PATTERN.search(value):
                    issues.append(ValidationIssue(path, "unresolved placeholder"))

    return issues
