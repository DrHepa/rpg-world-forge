from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from isoworld.content.models import WorldPack
from isoworld.runtime_io import RuntimeIOError, read_json_object, write_json_atomic
from isoworld.world.state import (
    ActorState,
    ConstructionState,
    Cooldown,
    DialogueState,
    DomainEvent,
    GameAction,
    KnowledgeValue,
    NeedValue,
    PendingConsequence,
    ProductionJob,
    QuestState,
    RelationshipValue,
    ReputationValue,
    ResourceValue,
    StockpileState,
    WorldState,
    initial_world_state,
    reduce_world,
)

SAVE_FORMAT = "isoworld.save"
SAVE_VERSION = 3
REPLAY_FORMAT = "isoworld.replay"
REPLAY_VERSION = 3
MAX_PERSISTENCE_BYTES = 64 * 1024 * 1024
MAX_REPLAY_ACTIONS = 1_000_000


class PersistenceError(ValueError):
    """Raised when a save or replay is malformed or incompatible."""


def state_to_dict(state: WorldState) -> dict[str, Any]:
    return {
        "tick": state.tick,
        "day": state.day,
        "minute_of_day": state.minute_of_day,
        "minute_tick": state.minute_tick,
        "active_actor_id": state.active_actor_id,
        "actors": [
            {
                "actor_id": actor.actor_id,
                "map_id": actor.map_id,
                "x": actor.x,
                "y": actor.y,
                "resources": {item.id: item.value for item in actor.resources},
                "cooldowns": {item.ability_id: item.ready_at_minute for item in actor.cooldowns},
                "route": [[x, y] for x, y in actor.route],
                "blocked_ticks": actor.blocked_ticks,
                "knowledge": {item.fact_id: item.status for item in actor.knowledge},
                "relationships": [
                    {
                        "target_actor_id": item.target_actor_id,
                        "dimension": item.dimension,
                        "value": item.value,
                    }
                    for item in actor.relationships
                ],
                "faction_reputation": {
                    item.faction_id: item.value for item in actor.faction_reputation
                },
                "needs": {item.need_id: item.value for item in actor.needs},
                "active_goal_id": actor.active_goal_id,
            }
            for actor in state.actors
        ],
        "flags": sorted(state.flags),
        "completed_interactions": sorted(state.completed_interactions),
        "quests": [
            {
                "quest_id": quest.quest_id,
                "status": quest.status,
                "stage_id": quest.stage_id,
            }
            for quest in state.quests
        ],
        "dialogue": None
        if state.dialogue is None
        else {
            "dialogue_id": state.dialogue.dialogue_id,
            "node_id": state.dialogue.node_id,
            "initiator_actor_id": state.dialogue.initiator_actor_id,
            "partner_actor_id": state.dialogue.partner_actor_id,
        },
        "active_scene_id": state.active_scene_id,
        "triggered_scenes": sorted(state.triggered_scenes),
        "recent_events": [
            {
                "kind": event.kind,
                "actor_id": event.actor_id,
                "subject_id": event.subject_id,
            }
            for event in state.recent_events
        ],
        "stockpiles": [
            {
                "stockpile_id": item.stockpile_id,
                "resources": {resource.id: resource.value for resource in item.resources},
            }
            for item in state.stockpiles
        ],
        "constructions": [
            {
                "instance_id": item.instance_id,
                "blueprint_id": item.blueprint_id,
                "map_id": item.map_id,
                "x": item.x,
                "y": item.y,
                "builder_actor_id": item.builder_actor_id,
                "status": item.status,
                "complete_at_minute": item.complete_at_minute,
            }
            for item in state.constructions
        ],
        "production_jobs": [
            {
                "construction_instance_id": item.construction_instance_id,
                "recipe_id": item.recipe_id,
                "actor_id": item.actor_id,
                "complete_at_minute": item.complete_at_minute,
            }
            for item in state.production_jobs
        ],
        "pending_consequences": [
            {
                "consequence_id": item.consequence_id,
                "due_at_minute": item.due_at_minute,
                "source_actor_id": item.source_actor_id,
            }
            for item in state.pending_consequences
        ],
        "triggered_consequences": sorted(state.triggered_consequences),
        "last_message": state.last_message,
    }


def state_digest(state: WorldState) -> str:
    canonical = json.dumps(
        state_to_dict(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_object(path: str | Path) -> dict[str, Any]:
    try:
        return read_json_object(path, limit=MAX_PERSISTENCE_BYTES)
    except RuntimeIOError as exc:
        raise PersistenceError(f"Could not read {path}: {exc}") from exc


def _write_object(path: str | Path, value: dict[str, Any]) -> None:
    try:
        write_json_atomic(path, value)
    except RuntimeIOError as exc:
        raise PersistenceError(f"Could not write {path}: {exc}") from exc


def _compatible(raw: dict[str, Any], pack: WorldPack, expected_format: str, version: int) -> None:
    if raw.get("format") != expected_format or raw.get("format_version") != version:
        raise PersistenceError(f"Unsupported {expected_format} format or version")
    if raw.get("world_id") != pack.world_id:
        raise PersistenceError("The persistence document belongs to a different world")
    if raw.get("world_content_hash") != pack.content_hash:
        raise PersistenceError("The worldpack changed; migrate or restart this state")


def _saved_integer(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PersistenceError(f"Malformed saved state: {context} must be an integer")
    return value


def state_from_dict(raw: dict[str, Any], pack: WorldPack) -> WorldState:
    try:
        actors = tuple(
            ActorState(
                actor_id=item["actor_id"],
                map_id=item["map_id"],
                x=_saved_integer(item["x"], "actor x"),
                y=_saved_integer(item["y"], "actor y"),
                resources=tuple(
                    ResourceValue(key, _saved_integer(value, "actor resource"))
                    for key, value in sorted(item.get("resources", {}).items())
                ),
                cooldowns=tuple(
                    Cooldown(key, _saved_integer(value, "actor cooldown"))
                    for key, value in sorted(item.get("cooldowns", {}).items())
                ),
                route=tuple(
                    (
                        _saved_integer(cell[0], "route x"),
                        _saved_integer(cell[1], "route y"),
                    )
                    for cell in item.get("route", [])
                ),
                blocked_ticks=_saved_integer(item.get("blocked_ticks", 0), "blocked ticks"),
                knowledge=tuple(
                    KnowledgeValue(key, str(value))
                    for key, value in sorted(item.get("knowledge", {}).items())
                ),
                relationships=tuple(
                    RelationshipValue(
                        value["target_actor_id"],
                        value["dimension"],
                        _saved_integer(value["value"], "relationship value"),
                    )
                    for value in item.get("relationships", [])
                ),
                faction_reputation=tuple(
                    ReputationValue(key, _saved_integer(value, "reputation value"))
                    for key, value in sorted(item.get("faction_reputation", {}).items())
                ),
                needs=tuple(
                    NeedValue(key, _saved_integer(value, "need value"))
                    for key, value in sorted(item.get("needs", {}).items())
                ),
                active_goal_id=item.get("active_goal_id"),
            )
            for item in raw["actors"]
        )
        dialogue_raw = raw.get("dialogue")
        dialogue = (
            None
            if dialogue_raw is None
            else DialogueState(
                dialogue_raw["dialogue_id"],
                dialogue_raw["node_id"],
                dialogue_raw["initiator_actor_id"],
                dialogue_raw["partner_actor_id"],
            )
        )
        state = WorldState(
            tick=_saved_integer(raw["tick"], "tick"),
            day=_saved_integer(raw["day"], "day"),
            minute_of_day=_saved_integer(raw["minute_of_day"], "minute_of_day"),
            minute_tick=_saved_integer(raw["minute_tick"], "minute_tick"),
            active_actor_id=raw["active_actor_id"],
            actors=actors,
            flags=frozenset(raw.get("flags", [])),
            completed_interactions=frozenset(raw.get("completed_interactions", [])),
            quests=tuple(
                QuestState(item["quest_id"], item["status"], item.get("stage_id"))
                for item in raw.get("quests", [])
            ),
            dialogue=dialogue,
            active_scene_id=raw.get("active_scene_id"),
            triggered_scenes=frozenset(raw.get("triggered_scenes", [])),
            recent_events=tuple(
                DomainEvent(item["kind"], item.get("actor_id"), item.get("subject_id"))
                for item in raw.get("recent_events", [])
            ),
            stockpiles=tuple(
                StockpileState(
                    item["stockpile_id"],
                    tuple(
                        ResourceValue(key, _saved_integer(value, "stockpile resource"))
                        for key, value in sorted(item.get("resources", {}).items())
                    ),
                )
                for item in raw.get("stockpiles", [])
            ),
            constructions=tuple(
                ConstructionState(
                    item["instance_id"],
                    item["blueprint_id"],
                    item["map_id"],
                    _saved_integer(item["x"], "construction x"),
                    _saved_integer(item["y"], "construction y"),
                    item["builder_actor_id"],
                    item["status"],
                    _saved_integer(item["complete_at_minute"], "construction completion minute"),
                )
                for item in raw.get("constructions", [])
            ),
            production_jobs=tuple(
                ProductionJob(
                    item["construction_instance_id"],
                    item["recipe_id"],
                    item["actor_id"],
                    _saved_integer(item["complete_at_minute"], "production completion minute"),
                )
                for item in raw.get("production_jobs", [])
            ),
            pending_consequences=tuple(
                PendingConsequence(
                    item["consequence_id"],
                    _saved_integer(item["due_at_minute"], "consequence due minute"),
                    item["source_actor_id"],
                )
                for item in raw.get("pending_consequences", [])
            ),
            triggered_consequences=frozenset(raw.get("triggered_consequences", [])),
            last_message=str(raw.get("last_message", "")),
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PersistenceError(f"Malformed saved state: {exc}") from exc
    _validate_state(state, pack)
    return state


def _validate_state(state: WorldState, pack: WorldPack) -> None:
    from isoworld.world.living_world import dynamic_blocked_cells

    expected = set(pack.actors)
    actual = {actor.actor_id for actor in state.actors}
    if actual != expected or len(actual) != len(state.actors):
        raise PersistenceError("Saved actors do not match the worldpack")
    if state.active_actor_id not in pack.playable_actor_ids:
        raise PersistenceError("The active actor is not playable")
    if state.tick < 0 or state.day < 1:
        raise PersistenceError("Saved time cannot be negative")
    if not 0 <= state.minute_of_day < 1440:
        raise PersistenceError("minute_of_day is outside 0..1439")
    if not 0 <= state.minute_tick < pack.clock.ticks_per_minute:
        raise PersistenceError("minute_tick is outside the clock range")
    occupied: set[tuple[str, int, int]] = set()
    for actor in state.actors:
        if (
            actor.map_id not in pack.maps
            or not pack.is_walkable(actor.map_id, actor.x, actor.y)
            or (actor.x, actor.y) in dynamic_blocked_cells(state, pack, actor.map_id)
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid position")
        key = (actor.map_id, actor.x, actor.y)
        if key in occupied:
            raise PersistenceError("Two actors occupy the same cell")
        occupied.add(key)
        if any(
            item.value < 0 or (pack.resources and item.id not in pack.resources)
            for item in actor.resources
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid resource")
        if any(
            item.ability_id not in pack.abilities or item.ready_at_minute < 0
            for item in actor.cooldowns
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid cooldown")
        if any(
            not pack.is_walkable(actor.map_id, *cell)
            or cell in dynamic_blocked_cells(state, pack, actor.map_id)
            for cell in actor.route
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid route")
        if any(
            item.fact_id not in pack.facts
            or item.status not in {"suspected", "known", "secret"}
            or item.fact_id in pack.actors[actor.actor_id].forbidden_fact_ids
            for item in actor.knowledge
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has invalid knowledge")
        if any(
            item.target_actor_id not in pack.actors
            or not item.dimension
            or not -100 <= item.value <= 100
            for item in actor.relationships
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid relationship")
        if any(
            item.faction_id not in pack.factions or not -100 <= item.value <= 100
            for item in actor.faction_reputation
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid reputation")
        if any(
            item.need_id not in pack.needs or not 0 <= item.value <= 100 for item in actor.needs
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has invalid needs")
        if {item.need_id for item in actor.needs} != {
            need_id for need_id, _ in pack.actors[actor.actor_id].needs
        }:
            raise PersistenceError(f"Actor {actor.actor_id} has incomplete needs")
        if actor.active_goal_id is not None and actor.active_goal_id not in pack.goals:
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid active goal")
    if not state.completed_interactions <= set(pack.interactions):
        raise PersistenceError("Saved interactions do not match the worldpack")
    if not all(isinstance(flag, str) for flag in state.flags):
        raise PersistenceError("Saved flags must be strings")
    if {item.quest_id for item in state.quests} != set(pack.quests) or len(state.quests) != len(
        pack.quests
    ):
        raise PersistenceError("Saved quests do not match the worldpack")
    for quest in state.quests:
        definition = pack.quests[quest.quest_id]
        if quest.status not in {"inactive", "active", "completed", "failed"}:
            raise PersistenceError(f"Quest {quest.quest_id} has an invalid status")
        if quest.stage_id is not None and quest.stage_id not in definition.stages:
            raise PersistenceError(f"Quest {quest.quest_id} has an invalid stage")
        if quest.status == "active" and quest.stage_id is None:
            raise PersistenceError(f"Quest {quest.quest_id} has no active stage")
    if state.dialogue is not None:
        dialogue = pack.dialogues.get(state.dialogue.dialogue_id)
        if (
            dialogue is None
            or state.dialogue.node_id not in dialogue.nodes
            or state.dialogue.initiator_actor_id not in pack.actors
            or state.dialogue.partner_actor_id != dialogue.actor_id
        ):
            raise PersistenceError("Saved dialogue is invalid")
    if state.active_scene_id is not None and state.active_scene_id not in pack.scenes:
        raise PersistenceError("Saved scene is invalid")
    valid_scene_keys = set(pack.scenes)
    if any(
        not isinstance(key, str) or key.split(":", 1)[0] not in valid_scene_keys
        for key in state.triggered_scenes
    ):
        raise PersistenceError("Saved scene history is invalid")
    if any(
        not isinstance(event.kind, str)
        or not event.kind
        or (event.actor_id is not None and event.actor_id not in pack.actors)
        or (event.subject_id is not None and not isinstance(event.subject_id, str))
        for event in state.recent_events
    ):
        raise PersistenceError("Saved events are invalid")
    if {item.stockpile_id for item in state.stockpiles} != set(pack.stockpiles) or len(
        state.stockpiles
    ) != len(pack.stockpiles):
        raise PersistenceError("Saved stockpiles do not match the worldpack")
    for stockpile in state.stockpiles:
        definition = pack.stockpiles[stockpile.stockpile_id]
        if (
            any(item.id not in pack.resources or item.value < 0 for item in stockpile.resources)
            or stockpile.total > definition.capacity
        ):
            raise PersistenceError(f"Stockpile {stockpile.stockpile_id} is invalid")
    construction_ids = {item.instance_id for item in state.constructions}
    if len(construction_ids) != len(state.constructions):
        raise PersistenceError("Saved construction IDs are not unique")
    occupied_construction_cells: set[tuple[str, int, int]] = set()
    for item in state.constructions:
        blueprint = pack.constructions.get(item.blueprint_id)
        if (
            blueprint is None
            or item.map_id not in pack.maps
            or item.status not in {"building", "completed"}
            or item.builder_actor_id not in pack.actors
            or item.complete_at_minute < 0
        ):
            raise PersistenceError(f"Construction {item.instance_id} is invalid")
        expected_id = f"{item.blueprint_id}__{item.map_id}__{item.x}_{item.y}"
        if (
            item.instance_id != expected_id
            or (item.status == "building" and item.complete_at_minute <= state.absolute_minute)
            or (item.status == "completed" and item.complete_at_minute > state.absolute_minute)
        ):
            raise PersistenceError(f"Construction {item.instance_id} has invalid timing")
        for dx, dy in blueprint.footprint:
            cell = (item.map_id, item.x + dx, item.y + dy)
            if cell in occupied_construction_cells or not pack.is_walkable(*cell):
                raise PersistenceError(f"Construction {item.instance_id} has invalid cells")
            occupied_construction_cells.add(cell)
    if any(
        job.construction_instance_id not in construction_ids
        or job.recipe_id not in pack.production_recipes
        or job.actor_id not in pack.actors
        or job.complete_at_minute <= state.absolute_minute
        for job in state.production_jobs
    ):
        raise PersistenceError("Saved production jobs are invalid")
    constructions_by_id = {item.instance_id: item for item in state.constructions}
    if any(
        pack.production_recipes[job.recipe_id].required_construction_id
        != constructions_by_id[job.construction_instance_id].blueprint_id
        for job in state.production_jobs
    ):
        raise PersistenceError("Saved production job uses the wrong construction")
    if len({job.construction_instance_id for job in state.production_jobs}) != len(
        state.production_jobs
    ):
        raise PersistenceError("A construction has multiple production jobs")
    if any(
        item.consequence_id not in pack.consequences
        or item.source_actor_id not in pack.actors
        or item.due_at_minute <= state.absolute_minute
        for item in state.pending_consequences
    ):
        raise PersistenceError("Saved pending consequences are invalid")
    if len({item.consequence_id for item in state.pending_consequences}) != len(
        state.pending_consequences
    ):
        raise PersistenceError("Saved pending consequences are duplicated")
    if not state.triggered_consequences <= set(pack.consequences):
        raise PersistenceError("Saved consequence history is invalid")


def save_game(path: str | Path, state: WorldState, pack: WorldPack) -> None:
    _validate_state(state, pack)
    _write_object(
        path,
        {
            "format": SAVE_FORMAT,
            "format_version": SAVE_VERSION,
            "world_id": pack.world_id,
            "world_content_hash": pack.content_hash,
            "state": state_to_dict(state),
            "state_digest": state_digest(state),
        },
    )


def load_game(path: str | Path, pack: WorldPack) -> WorldState:
    raw = _read_object(path)
    _compatible(raw, pack, SAVE_FORMAT, SAVE_VERSION)
    state_raw = raw.get("state")
    if not isinstance(state_raw, dict):
        raise PersistenceError("The save has no state object")
    state = state_from_dict(state_raw, pack)
    if raw.get("state_digest") != state_digest(state):
        raise PersistenceError("The saved state digest does not match")
    return state


def write_replay(
    path: str | Path,
    actions: list[GameAction] | tuple[GameAction, ...],
    final_state: WorldState,
    pack: WorldPack,
) -> None:
    _write_object(
        path,
        {
            "format": REPLAY_FORMAT,
            "format_version": REPLAY_VERSION,
            "world_id": pack.world_id,
            "world_content_hash": pack.content_hash,
            "actions": [action.to_dict() for action in actions],
            "final_state_digest": state_digest(final_state),
        },
    )


def replay_actions(pack: WorldPack, actions: list[GameAction]) -> WorldState:
    state = initial_world_state(pack)
    for index, action in enumerate(actions):
        try:
            state = reduce_world(state, action, pack)
        except (KeyError, TypeError, ValueError) as exc:
            raise PersistenceError(f"Replay action {index} is invalid: {exc}") from exc
    return state


def load_replay(path: str | Path, pack: WorldPack) -> tuple[list[GameAction], WorldState]:
    raw = _read_object(path)
    _compatible(raw, pack, REPLAY_FORMAT, REPLAY_VERSION)
    raw_actions = raw.get("actions")
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise PersistenceError("Replay actions must be a list of objects")
    if len(raw_actions) > MAX_REPLAY_ACTIONS:
        raise PersistenceError("Replay exceeds the one-million-action limit")
    try:
        actions = [GameAction.from_dict(item) for item in raw_actions]
    except (TypeError, ValueError) as exc:
        raise PersistenceError(f"Malformed replay action: {exc}") from exc
    state = replay_actions(pack, actions)
    if raw.get("final_state_digest") != state_digest(state):
        raise PersistenceError("Replay result differs from its recorded final state")
    return actions, state
