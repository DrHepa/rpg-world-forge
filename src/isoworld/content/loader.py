from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from isoworld.content.models import (
    AbilityDefinition,
    ActorDefinition,
    ClockDefinition,
    ConditionDefinition,
    DialogueChoiceDefinition,
    DialogueDefinition,
    DialogueNodeDefinition,
    EffectDefinition,
    FactDefinition,
    FactionDefinition,
    InteractionDefinition,
    Location,
    MapDefinition,
    QuestDefinition,
    QuestStageDefinition,
    SceneDefinition,
    ScheduleDefinition,
    ScheduleEntry,
    Spawn,
    TileType,
    WorldPack,
)


class WorldPackError(ValueError):
    """Raised when a compiled pack cannot be loaded safely."""


MAX_WORLDPACK_BYTES = 64 * 1024 * 1024
M2_COLLECTIONS = {"facts", "factions", "dialogues", "quests", "scenes"}


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


def _optional_string(raw: Any, context: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise WorldPackError(f"{context}: must be a string or null")
    return raw


def _optional_integer(raw: Any, context: str) -> int | None:
    if raw is None:
        return None
    return _integer(raw, context)


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
            kind=_optional_string(raw["kind"], f"{context}/kind") or "",
            target=_optional_string(raw.get("target", "self"), f"{context}/target") or "self",
            resource=_optional_string(raw.get("resource"), f"{context}/resource"),
            amount=_integer(raw.get("amount", 0), f"{context}/amount"),
            flag=_optional_string(raw.get("flag"), f"{context}/flag"),
            fact_id=_optional_string(raw.get("fact_id"), f"{context}/fact_id"),
            knowledge_status=_optional_string(
                raw.get("knowledge_status"), f"{context}/knowledge_status"
            ),
            target_actor_id=_optional_string(
                raw.get("target_actor_id"), f"{context}/target_actor_id"
            ),
            dimension=_optional_string(raw.get("dimension"), f"{context}/dimension"),
            faction_id=_optional_string(raw.get("faction_id"), f"{context}/faction_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldPackError(f"{context}: invalid effect") from exc


def _effects(raw: Any, context: str) -> tuple[EffectDefinition, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise WorldPackError(f"{context}: must be a list of effect objects")
    return tuple(_effect(item, f"{context}/{index}") for index, item in enumerate(raw))


def _condition(raw: dict[str, Any], context: str) -> ConditionDefinition:
    try:
        return ConditionDefinition(
            kind=_optional_string(raw["kind"], f"{context}/kind") or "",
            negate=_boolean(raw.get("negate", False), f"{context}/negate"),
            flag=_optional_string(raw.get("flag"), f"{context}/flag"),
            fact_id=_optional_string(raw.get("fact_id"), f"{context}/fact_id"),
            knowledge_status=_optional_string(
                raw.get("knowledge_status"), f"{context}/knowledge_status"
            ),
            actor_id=_optional_string(raw.get("actor_id"), f"{context}/actor_id"),
            target_actor_id=_optional_string(
                raw.get("target_actor_id"), f"{context}/target_actor_id"
            ),
            dimension=_optional_string(raw.get("dimension"), f"{context}/dimension"),
            faction_id=_optional_string(raw.get("faction_id"), f"{context}/faction_id"),
            value=_integer(raw.get("value", 0), f"{context}/value"),
            quest_id=_optional_string(raw.get("quest_id"), f"{context}/quest_id"),
            quest_status=_optional_string(raw.get("quest_status"), f"{context}/quest_status"),
            event_kind=_optional_string(raw.get("event_kind"), f"{context}/event_kind"),
            subject_id=_optional_string(raw.get("subject_id"), f"{context}/subject_id"),
            map_id=_optional_string(raw.get("map_id"), f"{context}/map_id"),
            x=_optional_integer(raw.get("x"), f"{context}/x"),
            y=_optional_integer(raw.get("y"), f"{context}/y"),
            start_minute=_optional_integer(raw.get("start_minute"), f"{context}/start_minute"),
            end_minute=_optional_integer(raw.get("end_minute"), f"{context}/end_minute"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldPackError(f"{context}: invalid condition") from exc


def _conditions(raw: Any, context: str) -> tuple[ConditionDefinition, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise WorldPackError(f"{context}: must be a list of condition objects")
    return tuple(_condition(item, f"{context}/{index}") for index, item in enumerate(raw))


def _knowledge(raw: Any, context: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise WorldPackError(f"{context}: must be an object")
    values: dict[str, str] = {}
    for group, status in (("knows", "known"), ("suspects", "suspected"), ("secrets", "secret")):
        for fact_id in _string_tuple(raw.get(group, []), f"{context}/{group}"):
            values[fact_id] = status
    forbidden = _string_tuple(raw.get("forbidden", []), f"{context}/forbidden")
    return tuple(sorted(values.items())), tuple(sorted(forbidden))


def _relationships(raw: Any, context: str) -> tuple[tuple[str, tuple[tuple[str, int], ...]], ...]:
    if not isinstance(raw, dict):
        raise WorldPackError(f"{context}: must be an object")
    result: list[tuple[str, tuple[tuple[str, int], ...]]] = []
    for actor_id, dimensions in sorted(raw.items()):
        if not isinstance(actor_id, str):
            raise WorldPackError(f"{context}: actor IDs must be strings")
        result.append(
            (actor_id, tuple(sorted(_integer_dict(dimensions, f"{context}/{actor_id}").items())))
        )
    return tuple(result)


def _dialogues(raw: list[dict[str, Any]]) -> dict[str, DialogueDefinition]:
    result: dict[str, DialogueDefinition] = {}
    for item in raw:
        dialogue_id = item["id"]
        nodes: dict[str, DialogueNodeDefinition] = {}
        for node_position, node in enumerate(item.get("nodes", [])):
            context = f"dialogues/{dialogue_id}/nodes/{node_position}"
            if node.get("id") in nodes:
                raise WorldPackError(f"{context}: duplicate node ID")
            choice_ids: set[str] = set()
            for choice in node.get("choices", []):
                choice_id = choice.get("id")
                if choice_id in choice_ids:
                    raise WorldPackError(f"{context}: duplicate choice ID")
                choice_ids.add(choice_id)
            choices = tuple(
                DialogueChoiceDefinition(
                    id=choice["id"],
                    text=choice["text"],
                    next_node_id=_optional_string(
                        choice.get("next_node_id"),
                        f"{context}/choices/{choice_position}/next_node_id",
                    ),
                    conditions=_conditions(
                        choice.get("conditions", []),
                        f"{context}/choices/{choice_position}/conditions",
                    ),
                    effects=_effects(
                        choice.get("effects", []),
                        f"{context}/choices/{choice_position}/effects",
                    ),
                )
                for choice_position, choice in enumerate(node.get("choices", []))
            )
            nodes[node["id"]] = DialogueNodeDefinition(
                id=node["id"],
                speaker_id=node["speaker_id"],
                text=node["text"],
                fact_refs=_string_tuple(node.get("fact_refs", []), f"{context}/fact_refs"),
                choices=choices,
                on_enter=_effects(node.get("on_enter", []), f"{context}/on_enter"),
                allow_exit=_boolean(node.get("allow_exit", True), f"{context}/allow_exit"),
            )
        result[dialogue_id] = DialogueDefinition(
            id=dialogue_id,
            display_name=item["display_name"],
            actor_id=item["actor_id"],
            range=_integer(item.get("range", 1), f"dialogues/{dialogue_id}/range"),
            start_node_id=item["start_node_id"],
            conditions=_conditions(
                item.get("conditions", []), f"dialogues/{dialogue_id}/conditions"
            ),
            nodes=nodes,
        )
    return result


def _quests(raw: list[dict[str, Any]]) -> dict[str, QuestDefinition]:
    result: dict[str, QuestDefinition] = {}
    for item in raw:
        quest_id = item["id"]
        stages: dict[str, QuestStageDefinition] = {}
        for position, stage in enumerate(item.get("stages", [])):
            context = f"quests/{quest_id}/stages/{position}"
            if stage.get("id") in stages:
                raise WorldPackError(f"{context}: duplicate stage ID")
            stages[stage["id"]] = QuestStageDefinition(
                id=stage["id"],
                description=stage["description"],
                completion_conditions=_conditions(
                    stage.get("completion_conditions", []),
                    f"{context}/completion_conditions",
                ),
                failure_conditions=_conditions(
                    stage.get("failure_conditions", []),
                    f"{context}/failure_conditions",
                ),
                on_complete=_effects(stage.get("on_complete", []), f"{context}/on_complete"),
                on_fail=_effects(stage.get("on_fail", []), f"{context}/on_fail"),
                next_stage_id=_optional_string(
                    stage.get("next_stage_id"), f"{context}/next_stage_id"
                ),
            )
        result[quest_id] = QuestDefinition(
            id=quest_id,
            title=item["title"],
            start_stage_id=item["start_stage_id"],
            auto_start_conditions=_conditions(
                item.get("auto_start_conditions", []),
                f"quests/{quest_id}/auto_start_conditions",
            ),
            stages=stages,
        )
    return result


def _scenes(raw: list[dict[str, Any]]) -> dict[str, SceneDefinition]:
    result: dict[str, SceneDefinition] = {}
    for item in raw:
        scene_id = item["id"]
        context = f"scenes/{scene_id}"
        result[scene_id] = SceneDefinition(
            id=scene_id,
            title=item["title"],
            text=item["text"],
            start_minute=_integer(item.get("start_minute", 0), f"{context}/start_minute"),
            end_minute=_integer(item.get("end_minute", 1440), f"{context}/end_minute"),
            conditions=_conditions(item.get("conditions", []), f"{context}/conditions"),
            effects=_effects(item.get("effects", []), f"{context}/effects"),
            once=_boolean(item.get("once", True), f"{context}/once"),
            priority=_integer(item.get("priority", 0), f"{context}/priority"),
        )
    return result


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


SUPPORTED_EFFECTS = {
    "set_flag",
    "clear_flag",
    "change_resource",
    "learn_fact",
    "change_relationship",
    "change_reputation",
}
SUPPORTED_CONDITIONS = {
    "flag_set",
    "flag_unset",
    "fact_status",
    "relationship_at_least",
    "reputation_at_least",
    "quest_status",
    "event",
    "time_window",
    "actor_at",
}
KNOWLEDGE_STATUSES = {"unknown", "suspected", "known", "secret"}


def _validate_effect_contract(effect: EffectDefinition, pack: WorldPack, context: str) -> None:
    if effect.kind not in SUPPORTED_EFFECTS or effect.target not in {"self", "target"}:
        raise WorldPackError(f"{context} has an unsupported effect")
    if effect.kind in {"set_flag", "clear_flag"} and not effect.flag:
        raise WorldPackError(f"{context} has an invalid flag effect")
    if effect.kind == "change_resource" and (not effect.resource or effect.amount == 0):
        raise WorldPackError(f"{context} has an invalid resource effect")
    if effect.kind == "learn_fact" and (
        effect.fact_id not in pack.facts
        or effect.knowledge_status not in KNOWLEDGE_STATUSES - {"unknown"}
    ):
        raise WorldPackError(f"{context} has an invalid knowledge effect")
    if effect.kind == "change_relationship" and (
        effect.target_actor_id not in pack.actors or not effect.dimension or effect.amount == 0
    ):
        raise WorldPackError(f"{context} has an invalid relationship effect")
    if effect.kind == "change_reputation" and (
        effect.faction_id not in pack.factions or effect.amount == 0
    ):
        raise WorldPackError(f"{context} has an invalid reputation effect")


def _validate_condition_contract(
    condition: ConditionDefinition, pack: WorldPack, context: str
) -> None:
    if condition.kind not in SUPPORTED_CONDITIONS:
        raise WorldPackError(f"{context} has an unsupported condition")
    if condition.actor_id is not None and condition.actor_id not in pack.actors:
        raise WorldPackError(f"{context} references an unknown actor")
    if condition.kind in {"flag_set", "flag_unset"} and not condition.flag:
        raise WorldPackError(f"{context} has an invalid flag condition")
    if condition.kind == "fact_status" and (
        condition.fact_id not in pack.facts or condition.knowledge_status not in KNOWLEDGE_STATUSES
    ):
        raise WorldPackError(f"{context} has an invalid knowledge condition")
    if condition.kind == "relationship_at_least" and (
        condition.target_actor_id not in pack.actors or not condition.dimension
    ):
        raise WorldPackError(f"{context} has an invalid relationship condition")
    if condition.kind == "reputation_at_least" and condition.faction_id not in pack.factions:
        raise WorldPackError(f"{context} has an invalid reputation condition")
    if condition.kind == "quest_status" and (
        condition.quest_id not in pack.quests
        or condition.quest_status not in {"inactive", "active", "completed", "failed"}
    ):
        raise WorldPackError(f"{context} has an invalid quest condition")
    if condition.kind == "event" and not condition.event_kind:
        raise WorldPackError(f"{context} has an invalid event condition")
    if condition.kind == "time_window" and (
        condition.start_minute is None
        or condition.end_minute is None
        or not 0 <= condition.start_minute <= 1439
        or not 0 <= condition.end_minute <= 1440
        or condition.start_minute == condition.end_minute
    ):
        raise WorldPackError(f"{context} has an invalid time window")
    if condition.kind == "actor_at":
        world_map = pack.maps.get(condition.map_id or "")
        if (
            world_map is None
            or condition.x is None
            or condition.y is None
            or condition.x < 0
            or condition.y < 0
            or condition.x >= world_map.width
            or condition.y >= world_map.height
        ):
            raise WorldPackError(f"{context} has an invalid location condition")


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
        if not isinstance(actor.display_name, str) or not actor.display_name:
            raise WorldPackError(f"Actor {actor.id} has an invalid display name")
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
        knowledge = dict(actor.knowledge)
        if any(
            fact_id not in pack.facts for fact_id in set(knowledge) | set(actor.forbidden_fact_ids)
        ):
            raise WorldPackError(f"Actor {actor.id} references an unknown fact")
        if set(knowledge) & set(actor.forbidden_fact_ids):
            raise WorldPackError(f"Actor {actor.id} knows a forbidden fact")
        if any(status not in KNOWLEDGE_STATUSES - {"unknown"} for status in knowledge.values()):
            raise WorldPackError(f"Actor {actor.id} has an invalid knowledge status")
        for target_actor_id, dimensions in actor.relationships:
            if target_actor_id not in pack.actors or any(
                not dimension or value < -100 or value > 100 for dimension, value in dimensions
            ):
                raise WorldPackError(f"Actor {actor.id} has an invalid relationship")
        if any(
            faction_id not in pack.factions or value < -100 or value > 100
            for faction_id, value in actor.faction_reputation
        ):
            raise WorldPackError(f"Actor {actor.id} has an invalid faction reputation")
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
    for effects, context in [
        *((ability.effects, f"Ability {ability.id}") for ability in pack.abilities.values()),
        *((item.effects, f"Interaction {item.id}") for item in pack.interactions.values()),
    ]:
        for effect in effects:
            _validate_effect_contract(effect, pack, context)
    for fact in pack.facts.values():
        if (
            not isinstance(fact.statement, str)
            or not fact.statement
            or fact.kind not in {"truth", "secret", "rumor"}
            or fact.truth not in {"true", "false", "unknown"}
        ):
            raise WorldPackError(f"Fact {fact.id} has an invalid contract")
    for faction in pack.factions.values():
        if not isinstance(faction.display_name, str) or not faction.display_name:
            raise WorldPackError(f"Faction {faction.id} has an invalid contract")
    for dialogue in pack.dialogues.values():
        if (
            not isinstance(dialogue.display_name, str)
            or not dialogue.display_name
            or dialogue.actor_id not in pack.actors
            or dialogue.range < 0
            or dialogue.start_node_id not in dialogue.nodes
            or not dialogue.nodes
        ):
            raise WorldPackError(f"Dialogue {dialogue.id} has an invalid contract")
        for condition in dialogue.conditions:
            _validate_condition_contract(condition, pack, f"Dialogue {dialogue.id}")
        for node in dialogue.nodes.values():
            if (
                node.speaker_id not in pack.actors
                or not isinstance(node.text, str)
                or not node.text
                or any(fact_id not in pack.facts for fact_id in node.fact_refs)
            ):
                raise WorldPackError(f"Dialogue {dialogue.id} has an invalid node")
            for effect in node.on_enter:
                _validate_effect_contract(effect, pack, f"Dialogue {dialogue.id}")
            choice_ids: set[str] = set()
            for choice in node.choices:
                if (
                    not isinstance(choice.text, str)
                    or not choice.text
                    or choice.id in choice_ids
                    or (
                        choice.next_node_id is not None
                        and choice.next_node_id not in dialogue.nodes
                    )
                ):
                    raise WorldPackError(f"Dialogue {dialogue.id} has an invalid choice")
                choice_ids.add(choice.id)
                for condition in choice.conditions:
                    _validate_condition_contract(condition, pack, f"Dialogue {dialogue.id}")
                for effect in choice.effects:
                    _validate_effect_contract(effect, pack, f"Dialogue {dialogue.id}")
    for quest in pack.quests.values():
        if (
            not isinstance(quest.title, str)
            or not quest.title
            or not quest.stages
            or quest.start_stage_id not in quest.stages
        ):
            raise WorldPackError(f"Quest {quest.id} has an invalid contract")
        for condition in quest.auto_start_conditions:
            _validate_condition_contract(condition, pack, f"Quest {quest.id}")
        for stage in quest.stages.values():
            if (
                not isinstance(stage.description, str)
                or not stage.description
                or (stage.next_stage_id is not None and stage.next_stage_id not in quest.stages)
            ):
                raise WorldPackError(f"Quest {quest.id} has an invalid next stage")
            for condition in stage.completion_conditions + stage.failure_conditions:
                _validate_condition_contract(condition, pack, f"Quest {quest.id}")
            for effect in stage.on_complete + stage.on_fail:
                _validate_effect_contract(effect, pack, f"Quest {quest.id}")
    for scene in pack.scenes.values():
        if (
            not isinstance(scene.title, str)
            or not scene.title
            or not isinstance(scene.text, str)
            or not scene.text
            or not 0 <= scene.start_minute <= 1439
            or not 0 <= scene.end_minute <= 1440
            or scene.start_minute == scene.end_minute
        ):
            raise WorldPackError(f"Scene {scene.id} has an invalid time window")
        for condition in scene.conditions:
            _validate_condition_contract(condition, pack, f"Scene {scene.id}")
        for effect in scene.effects:
            _validate_effect_contract(effect, pack, f"Scene {scene.id}")


def load_worldpack(path: str | Path) -> WorldPack:
    pack_path = Path(path)
    try:
        if pack_path.stat().st_size > MAX_WORLDPACK_BYTES:
            raise WorldPackError("The worldpack exceeds the 64 MiB limit")
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorldPackError(f"Could not load {pack_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorldPackError("The worldpack root must be an object")
    if raw.get("format") != "isoworld.worldpack":
        raise WorldPackError("Unknown worldpack format")
    version = raw.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2, 3}:
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
        if version == 3 and not M2_COLLECTIONS <= set(collections):
            raise WorldPackError("Worldpack version 3 is missing narrative collections")
        for collection_name, items in collections.items():
            identifiers = [item.get("id") for item in items]
            if not all(isinstance(identifier, str) for identifier in identifiers):
                raise WorldPackError(f"Collection {collection_name} has a non-string ID")
            if len(set(identifiers)) != len(identifiers):
                raise WorldPackError(f"Collection {collection_name} has a duplicate ID")
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
        actors: dict[str, ActorDefinition] = {}
        for item in collections["actors"]:
            actor_id = item["id"]
            knowledge, forbidden_fact_ids = _knowledge(
                item.get("knowledge", {}), f"actors/{actor_id}/knowledge"
            )
            actors[actor_id] = ActorDefinition(
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
                knowledge=knowledge,
                forbidden_fact_ids=forbidden_fact_ids,
                relationships=_relationships(
                    item.get("relationships", {}), f"actors/{actor_id}/relationships"
                ),
                faction_reputation=tuple(
                    sorted(
                        _integer_dict(
                            item.get("faction_reputation", {}),
                            f"actors/{actor_id}/faction_reputation",
                        ).items()
                    )
                ),
            )
        facts = {
            item["id"]: FactDefinition(
                id=item["id"],
                statement=item["statement"],
                kind=item.get("kind", "truth"),
                truth=item.get("truth", "true"),
            )
            for item in collections.get("facts", [])
        }
        factions = {
            item["id"]: FactionDefinition(
                id=item["id"],
                display_name=item["display_name"],
            )
            for item in collections.get("factions", [])
        }
        dialogues = _dialogues(collections.get("dialogues", []))
        quests = _quests(collections.get("quests", []))
        scenes = _scenes(collections.get("scenes", []))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
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
        "dialogue_help": "Q: talk; number keys: choose; Esc: leave",
        "scene_help": "Space: continue",
        "quest_label": "Quest",
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
        if key
        not in {
            "tile_types",
            "maps",
            "actors",
            "abilities",
            "schedules",
            "interactions",
            "facts",
            "factions",
            "dialogues",
            "quests",
            "scenes",
        }
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
        facts=facts,
        factions=factions,
        dialogues=dialogues,
        quests=quests,
        scenes=scenes,
        collections=extra_collections,
    )
    if pack.start_map_id not in pack.maps:
        raise WorldPackError("The starting map does not exist")
    if not pack.playable_actor_ids:
        raise WorldPackError("The worldpack contains no playable actors")
    _validate_runtime_pack(pack)
    return pack
