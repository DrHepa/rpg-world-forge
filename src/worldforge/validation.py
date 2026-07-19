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
    "interactions",
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


def _validate_location(
    value: Any,
    path: str,
    maps: dict[str, dict[str, Any]],
    tile_types: dict[str, dict[str, Any]],
    *,
    require_walkable: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        return [ValidationIssue(path, "must be a location object")]
    issues.extend(_require(value, ("map_id", "x", "y"), path))
    map_id = value.get("map_id")
    if not isinstance(map_id, str) or map_id not in maps:
        issues.append(ValidationIssue(f"{path}/map_id", f"unknown map: {map_id}"))
        return issues
    if not all(
        isinstance(value.get(field), int) and not isinstance(value.get(field), bool)
        for field in ("x", "y")
    ):
        issues.append(ValidationIssue(path, "x and y must be integers"))
        return issues
    world_map = maps[map_id]
    x, y = value["x"], value["y"]
    width = world_map.get("width")
    height = world_map.get("height")
    rows = world_map.get("rows")
    legend = world_map.get("legend")
    if not isinstance(width, int) or not isinstance(height, int):
        return issues
    if x < 0 or y < 0 or x >= width or y >= height:
        issues.append(ValidationIssue(path, "position is outside the map"))
        return issues
    if require_walkable and isinstance(rows, list) and isinstance(legend, dict):
        try:
            tile_id = legend[rows[y][x]]
        except (IndexError, KeyError, TypeError):
            return issues
        if tile_id in tile_types and tile_types[tile_id].get("walkable") is False:
            issues.append(ValidationIssue(path, "position is on a non-walkable tile"))
    return issues


def _validate_effects(value: Any, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, list) or not value:
        return [ValidationIssue(path, "must contain at least one effect")]
    for index, effect in enumerate(value):
        effect_path = f"{path}/{index}"
        if not isinstance(effect, dict):
            issues.append(ValidationIssue(effect_path, "must be an object"))
            continue
        kind = effect.get("kind")
        if kind not in {"set_flag", "clear_flag", "change_resource"}:
            issues.append(ValidationIssue(f"{effect_path}/kind", f"unsupported effect: {kind}"))
        if effect.get("target", "self") not in {"self", "target"}:
            issues.append(ValidationIssue(f"{effect_path}/target", "must be self or target"))
        if kind in {"set_flag", "clear_flag"}:
            issues.extend(_valid_id(effect.get("flag"), f"{effect_path}/flag"))
        if kind == "change_resource":
            issues.extend(_valid_id(effect.get("resource"), f"{effect_path}/resource"))
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
    return issues


def validate_project(
    project: SourceProject,
    *,
    profile: str = "release",
) -> list[ValidationIssue]:
    if profile not in {"draft", "release"}:
        raise ValueError("profile must be draft or release")
    issues: list[ValidationIssue] = []
    world = project.world
    issues.extend(_require(world, ("id", "title", "language", "start_map_id", "ui"), "world"))
    if "id" in world:
        issues.extend(_valid_id(world["id"], "world/id"))
    if not isinstance(world.get("title"), str) or not world.get("title", "").strip():
        issues.append(ValidationIssue("world/title", "must contain a title"))
    if not isinstance(world.get("language"), str) or len(world.get("language", "")) < 2:
        issues.append(ValidationIssue("world/language", "invalid language"))
    ui = world.get("ui")
    required_ui = {"move_help", "switch_help", "active_actor"}
    capabilities = world.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        issues.append(ValidationIssue("world/capabilities", "must be a list of strings"))
    else:
        capability_ui = {
            "path_navigation": {"navigate_help"},
            "contextual_interactions": {"interact_help"},
            "costed_abilities": {"ability_help"},
            "world_clock": {"clock_label"},
        }
        for capability in capabilities:
            required_ui.update(capability_ui.get(capability, set()))
    if not isinstance(ui, dict):
        issues.append(ValidationIssue("world/ui", "must be an object of localized strings"))
    else:
        for key in sorted(required_ui - set(ui)):
            issues.append(ValidationIssue(f"world/ui/{key}", "UI string is missing"))

    simulation = world.get("simulation", {})
    if not isinstance(simulation, dict):
        issues.append(ValidationIssue("world/simulation", "must be an object"))
        simulation = {}
    for field, default, minimum, maximum in (
        ("start_day", 1, 1, None),
        ("start_minute", 480, 0, 1439),
        ("ticks_per_minute", 20, 1, None),
        ("movement_interval_ticks", 4, 1, None),
    ):
        value = simulation.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            issues.append(
                ValidationIssue(f"world/simulation/{field}", f"must be an integer >= {minimum}")
            )
        elif maximum is not None and value > maximum:
            issues.append(ValidationIssue(f"world/simulation/{field}", f"must be <= {maximum}"))

    for collection in REQUIRED_COLLECTIONS:
        if collection not in project.collections:
            issues.append(
                ValidationIssue(f"collections/{collection}", "required collection is missing")
            )
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
            identifier = item["id"]
            id_issues = _valid_id(identifier, f"{path}/id")
            issues.extend(id_issues)
            if id_issues:
                continue
            if identifier in index:
                issues.append(ValidationIssue(f"{path}/id", f"duplicate ID: {identifier}"))
            index[identifier] = item
        indexes[collection] = index

    tile_types = indexes.get("tile_types", {})
    maps = indexes.get("maps", {})
    actors = indexes.get("actors", {})
    facts = indexes.get("facts", {})
    factions = indexes.get("factions", {})
    abilities = indexes.get("abilities", {})
    schedules = indexes.get("schedules", {})
    interactions = indexes.get("interactions", {})
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
        issues.extend(
            _require(world_map, ("display_name", "width", "height", "rows", "legend"), path)
        )
        width = world_map.get("width")
        height = world_map.get("height")
        rows = world_map.get("rows")
        legend = world_map.get("legend")
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            issues.append(ValidationIssue(f"{path}/width", "must be a positive integer"))
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            issues.append(ValidationIssue(f"{path}/height", "must be a positive integer"))
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            issues.append(ValidationIssue(f"{path}/rows", "must be a list of strings"))
            rows = []
        if isinstance(height, int) and len(rows) != height:
            issues.append(ValidationIssue(f"{path}/rows", "row count differs from height"))
        for index, row in enumerate(rows):
            if isinstance(width, int) and len(row) != width:
                issues.append(
                    ValidationIssue(f"{path}/rows/{index}", "row length differs from width")
                )
        if not isinstance(legend, dict):
            issues.append(
                ValidationIssue(f"{path}/legend", "must be a character -> tile_id object")
            )
            legend = {}
        for symbol, tile_id in legend.items():
            if not isinstance(symbol, str) or len(symbol) != 1:
                issues.append(
                    ValidationIssue(f"{path}/legend", "each symbol must be one character")
                )
            if not isinstance(tile_id, str) or tile_id not in tile_types:
                issues.append(
                    ValidationIssue(f"{path}/legend/{symbol}", f"unknown tile: {tile_id}")
                )
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
    if not isinstance(start_map, str) or start_map not in maps:
        issues.append(ValidationIssue("world/start_map_id", f"unknown map: {start_map}"))

    spawn_cells: dict[tuple[str, int, int], str] = {}
    for actor_id, actor in actors.items():
        path = f"actors/{actor_id}"
        issues.extend(_require(actor, ("display_name", "playable", "spawn", "color"), path))
        if "playable" in actor and not isinstance(actor["playable"], bool):
            issues.append(ValidationIssue(f"{path}/playable", "must be a boolean"))
        if "color" in actor:
            issues.extend(_validate_color(actor["color"], f"{path}/color"))
        spawn = actor.get("spawn")
        issues.extend(
            _validate_location(
                spawn,
                f"{path}/spawn",
                maps,
                tile_types,
                require_walkable=True,
            )
        )
        if (
            isinstance(spawn, dict)
            and isinstance(spawn.get("map_id"), str)
            and isinstance(spawn.get("x"), int)
            and not isinstance(spawn.get("x"), bool)
            and isinstance(spawn.get("y"), int)
            and not isinstance(spawn.get("y"), bool)
        ):
            cell = (spawn["map_id"], spawn["x"], spawn["y"])
            if cell in spawn_cells:
                issues.append(
                    ValidationIssue(
                        f"{path}/spawn",
                        f"cell is already occupied by actor: {spawn_cells[cell]}",
                    )
                )
            spawn_cells[cell] = actor_id
        arc_id = actor.get("personal_arc_id")
        if arc_id is not None and arc_id not in arcs:
            issues.append(ValidationIssue(f"{path}/personal_arc_id", f"unknown arc: {arc_id}"))
        schedule_id = actor.get("schedule_id")
        if schedule_id is not None and (
            not isinstance(schedule_id, str) or schedule_id not in schedules
        ):
            issues.append(
                ValidationIssue(f"{path}/schedule_id", f"unknown schedule: {schedule_id}")
            )
        schedule_mode = actor.get(
            "schedule_mode", "when_inactive" if actor.get("playable") is True else "always"
        )
        if schedule_mode not in {"always", "when_inactive", "never"}:
            issues.append(
                ValidationIssue(
                    f"{path}/schedule_mode",
                    "must be always, when_inactive, or never",
                )
            )
        resources = actor.get("resources", {})
        if not isinstance(resources, dict):
            issues.append(ValidationIssue(f"{path}/resources", "must be an object"))
        else:
            for resource_id, amount in resources.items():
                issues.extend(_valid_id(resource_id, f"{path}/resources/{resource_id}"))
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    issues.append(
                        ValidationIssue(
                            f"{path}/resources/{resource_id}",
                            "must be a non-negative integer",
                        )
                    )
        for field, index in (("ability_ids", abilities), ("faction_ids", factions)):
            refs = actor.get(field, [])
            if not isinstance(refs, list):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a list"))
            else:
                for ref in refs:
                    if not isinstance(ref, str) or ref not in index:
                        issues.append(
                            ValidationIssue(f"{path}/{field}", f"unknown reference: {ref}")
                        )
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
                groups[group] = {ref for ref in refs if isinstance(ref, str)}
                for ref in refs:
                    if not isinstance(ref, str) or ref not in facts:
                        issues.append(
                            ValidationIssue(f"{path}/knowledge/{group}", f"unknown fact: {ref}")
                        )
            forbidden = groups.get("forbidden", set())
            known = (
                groups.get("knows", set())
                | groups.get("suspects", set())
                | groups.get("secrets", set())
            )
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

    for ability_id, ability in abilities.items():
        path = f"abilities/{ability_id}"
        issues.extend(
            _require(
                ability,
                ("display_name", "target", "range", "costs", "cooldown_minutes", "effects"),
                path,
            )
        )
        if ability.get("target") not in {"self", "actor"}:
            issues.append(ValidationIssue(f"{path}/target", "must be self or actor"))
        ability_range = ability.get("range")
        if (
            not isinstance(ability_range, int)
            or isinstance(ability_range, bool)
            or ability_range < 0
        ):
            issues.append(ValidationIssue(f"{path}/range", "must be a non-negative integer"))
        cooldown = ability.get("cooldown_minutes")
        if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
            issues.append(
                ValidationIssue(f"{path}/cooldown_minutes", "must be a non-negative integer")
            )
        costs = ability.get("costs")
        if not isinstance(costs, dict) or not costs:
            issues.append(
                ValidationIssue(f"{path}/costs", "must contain at least one resource cost")
            )
        else:
            for resource_id, amount in costs.items():
                issues.extend(_valid_id(resource_id, f"{path}/costs/{resource_id}"))
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    issues.append(
                        ValidationIssue(f"{path}/costs/{resource_id}", "must be a positive integer")
                    )
        issues.extend(_validate_effects(ability.get("effects"), f"{path}/effects"))

    for actor_id, actor in actors.items():
        resources = actor.get("resources", {})
        if not isinstance(resources, dict):
            continue
        ability_ids = actor.get("ability_ids", [])
        if not isinstance(ability_ids, list):
            continue
        for ability_id in ability_ids:
            ability = abilities.get(ability_id) if isinstance(ability_id, str) else None
            if ability is None or not isinstance(ability.get("costs"), dict):
                continue
            for resource_id in ability["costs"]:
                if resource_id not in resources:
                    issues.append(
                        ValidationIssue(
                            f"actors/{actor_id}/resources/{resource_id}",
                            f"resource required by ability: {ability_id}",
                        )
                    )

    for arc_id, arc in arcs.items():
        path = f"personal_arcs/{arc_id}"
        issues.extend(_require(arc, ("actor_id", "acts"), path))
        actor_id = arc.get("actor_id")
        if not isinstance(actor_id, str) or actor_id not in actors:
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
        covered_minutes: set[int] = set()
        for index, entry in enumerate(entries):
            entry_path = f"{path}/entries/{index}"
            if not isinstance(entry, dict):
                issues.append(ValidationIssue(entry_path, "must be an object"))
                continue
            issues.extend(
                _require(
                    entry,
                    ("start_minute", "end_minute", "map_id", "x", "y", "activity"),
                    entry_path,
                )
            )
            for field in ("start_minute", "end_minute"):
                value = entry.get(field)
                maximum = 1439 if field == "start_minute" else 1440
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    or value > maximum
                ):
                    issues.append(
                        ValidationIssue(f"{entry_path}/{field}", f"must be in 0..{maximum}")
                    )
            if entry.get("start_minute") == entry.get("end_minute"):
                issues.append(
                    ValidationIssue(entry_path, "schedule segment cannot have zero duration")
                )
            start = entry.get("start_minute")
            end = entry.get("end_minute")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= start <= 1439
                and 0 <= end <= 1440
                and start != end
            ):
                minutes = (
                    set(range(start, end))
                    if start < end
                    else set(range(start, 1440)) | set(range(0, end))
                )
                if covered_minutes & minutes:
                    issues.append(ValidationIssue(entry_path, "schedule segments overlap"))
                covered_minutes.update(minutes)
            issues.extend(
                _validate_location(
                    entry,
                    entry_path,
                    maps,
                    tile_types,
                    require_walkable=True,
                )
            )
            fallbacks = entry.get("fallbacks", [])
            if not isinstance(fallbacks, list):
                issues.append(ValidationIssue(f"{entry_path}/fallbacks", "must be a list"))
            else:
                for fallback_index, fallback in enumerate(fallbacks):
                    issues.extend(
                        _validate_location(
                            fallback,
                            f"{entry_path}/fallbacks/{fallback_index}",
                            maps,
                            tile_types,
                            require_walkable=True,
                        )
                    )

    for actor_id, actor in actors.items():
        schedule_id = actor.get("schedule_id")
        schedule = schedules.get(schedule_id) if isinstance(schedule_id, str) else None
        spawn = actor.get("spawn")
        if schedule is None or not isinstance(spawn, dict):
            continue
        for entry in schedule.get("entries", []):
            if not isinstance(entry, dict):
                continue
            fallbacks = entry.get("fallbacks", [])
            destinations = [entry] + (list(fallbacks) if isinstance(fallbacks, list) else [])
            for destination in destinations:
                if isinstance(destination, dict) and destination.get("map_id") != spawn.get(
                    "map_id"
                ):
                    issues.append(
                        ValidationIssue(
                            f"actors/{actor_id}/schedule_id",
                            "M1 schedules cannot route an actor between maps",
                        )
                    )

    for interaction_id, interaction in interactions.items():
        path = f"interactions/{interaction_id}"
        issues.extend(
            _require(
                interaction,
                ("display_name", "prompt", "map_id", "x", "y", "range", "effects"),
                path,
            )
        )
        issues.extend(
            _validate_location(
                interaction,
                path,
                maps,
                tile_types,
                require_walkable=False,
            )
        )
        interaction_range = interaction.get("range")
        if (
            not isinstance(interaction_range, int)
            or isinstance(interaction_range, bool)
            or interaction_range < 0
        ):
            issues.append(ValidationIssue(f"{path}/range", "must be a non-negative integer"))
        for field in ("required_flags", "forbidden_flags"):
            values = interaction.get(field, [])
            if not isinstance(values, list):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a list"))
            else:
                for index, flag in enumerate(values):
                    issues.extend(_valid_id(flag, f"{path}/{field}/{index}"))
        if "repeatable" in interaction and not isinstance(interaction["repeatable"], bool):
            issues.append(ValidationIssue(f"{path}/repeatable", "must be a boolean"))
        issues.extend(_validate_effects(interaction.get("effects"), f"{path}/effects"))

    for collection, items in (
        ("dialogues", indexes.get("dialogues", {})),
        ("quests", indexes.get("quests", {})),
    ):
        for item_id, item in items.items():
            nodes = item.get("nodes") if collection == "dialogues" else item.get("stages")
            field = "nodes" if collection == "dialogues" else "stages"
            path = f"{collection}/{item_id}/{field}"
            if not isinstance(nodes, list) or not nodes:
                issues.append(ValidationIssue(path, "must contain elements"))
                continue
            node_ids = {
                node.get("id")
                for node in nodes
                if isinstance(node, dict) and isinstance(node.get("id"), str)
            }
            for position, node in enumerate(nodes):
                if not isinstance(node, dict):
                    issues.append(ValidationIssue(f"{path}/{position}", "must be an object"))
                    continue
                for target in node.get("next", []):
                    if not isinstance(target, str) or target not in node_ids:
                        issues.append(
                            ValidationIssue(f"{path}/{position}/next", f"unknown target: {target}")
                        )

    for path, value in _walk_strings(world, "world"):
        if PLACEHOLDER_PATTERN.search(value):
            issues.append(ValidationIssue(path, "unresolved placeholder"))
    for collection, items in project.collections.items():
        for index, item in enumerate(items):
            for path, value in _walk_strings(item, f"collections/{collection}/{index}"):
                if PLACEHOLDER_PATTERN.search(value):
                    issues.append(ValidationIssue(path, "unresolved placeholder"))

    return issues
