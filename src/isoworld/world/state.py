from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from isoworld.content.models import EffectDefinition, WorldPack
from isoworld.world.navigation import Cell, find_path


@dataclass(frozen=True, slots=True)
class ResourceValue:
    id: str
    value: int


@dataclass(frozen=True, slots=True)
class Cooldown:
    ability_id: str
    ready_at_minute: int


@dataclass(frozen=True, slots=True)
class KnowledgeValue:
    fact_id: str
    status: str


@dataclass(frozen=True, slots=True)
class RelationshipValue:
    target_actor_id: str
    dimension: str
    value: int


@dataclass(frozen=True, slots=True)
class ReputationValue:
    faction_id: str
    value: int


@dataclass(frozen=True, slots=True)
class NeedValue:
    need_id: str
    value: int


@dataclass(frozen=True, slots=True)
class StockpileState:
    stockpile_id: str
    resources: tuple[ResourceValue, ...] = ()

    def resource(self, resource_id: str) -> int:
        item = next((item for item in self.resources if item.id == resource_id), None)
        return item.value if item is not None else 0

    def with_resource(self, resource_id: str, value: int) -> StockpileState:
        resources = {item.id: item.value for item in self.resources}
        resources[resource_id] = max(0, value)
        return replace(
            self,
            resources=tuple(ResourceValue(key, resources[key]) for key in sorted(resources)),
        )

    @property
    def total(self) -> int:
        return sum(item.value for item in self.resources)


@dataclass(frozen=True, slots=True)
class ConstructionState:
    instance_id: str
    blueprint_id: str
    map_id: str
    x: int
    y: int
    builder_actor_id: str
    status: str
    complete_at_minute: int


@dataclass(frozen=True, slots=True)
class ProductionJob:
    construction_instance_id: str
    recipe_id: str
    actor_id: str
    complete_at_minute: int


@dataclass(frozen=True, slots=True)
class PendingConsequence:
    consequence_id: str
    due_at_minute: int
    source_actor_id: str


@dataclass(frozen=True, slots=True)
class QuestState:
    quest_id: str
    status: str = "inactive"
    stage_id: str | None = None


@dataclass(frozen=True, slots=True)
class DialogueState:
    dialogue_id: str
    node_id: str
    initiator_actor_id: str
    partner_actor_id: str


@dataclass(frozen=True, slots=True)
class DomainEvent:
    kind: str
    actor_id: str | None = None
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActorState:
    actor_id: str
    map_id: str
    x: int
    y: int
    resources: tuple[ResourceValue, ...] = ()
    cooldowns: tuple[Cooldown, ...] = ()
    route: tuple[Cell, ...] = ()
    blocked_ticks: int = 0
    knowledge: tuple[KnowledgeValue, ...] = ()
    relationships: tuple[RelationshipValue, ...] = ()
    faction_reputation: tuple[ReputationValue, ...] = ()
    needs: tuple[NeedValue, ...] = ()
    active_goal_id: str | None = None

    def resource(self, resource_id: str) -> int:
        item = next((item for item in self.resources if item.id == resource_id), None)
        return item.value if item is not None else 0

    def with_resource(self, resource_id: str, value: int) -> ActorState:
        resources = {item.id: item.value for item in self.resources}
        resources[resource_id] = max(0, value)
        return replace(
            self,
            resources=tuple(ResourceValue(key, resources[key]) for key in sorted(resources)),
        )

    def cooldown_until(self, ability_id: str) -> int:
        item = next((item for item in self.cooldowns if item.ability_id == ability_id), None)
        return item.ready_at_minute if item is not None else 0

    def with_cooldown(self, ability_id: str, ready_at: int) -> ActorState:
        values = {item.ability_id: item.ready_at_minute for item in self.cooldowns}
        values[ability_id] = ready_at
        return replace(
            self,
            cooldowns=tuple(Cooldown(key, values[key]) for key in sorted(values)),
        )

    def knowledge_status(self, fact_id: str) -> str:
        item = next((item for item in self.knowledge if item.fact_id == fact_id), None)
        return item.status if item is not None else "unknown"

    def with_knowledge(self, fact_id: str, status: str) -> ActorState:
        values = {item.fact_id: item.status for item in self.knowledge}
        if status == "unknown":
            values.pop(fact_id, None)
        else:
            values[fact_id] = status
        return replace(
            self,
            knowledge=tuple(KnowledgeValue(key, values[key]) for key in sorted(values)),
        )

    def relationship(self, target_actor_id: str, dimension: str) -> int:
        item = next(
            (
                item
                for item in self.relationships
                if item.target_actor_id == target_actor_id and item.dimension == dimension
            ),
            None,
        )
        return item.value if item is not None else 0

    def with_relationship(self, target_actor_id: str, dimension: str, value: int) -> ActorState:
        values = {(item.target_actor_id, item.dimension): item.value for item in self.relationships}
        values[(target_actor_id, dimension)] = max(-100, min(100, value))
        return replace(
            self,
            relationships=tuple(
                RelationshipValue(
                    target,
                    relationship_dimension,
                    values[(target, relationship_dimension)],
                )
                for target, relationship_dimension in sorted(values)
            ),
        )

    def reputation(self, faction_id: str) -> int:
        item = next(
            (item for item in self.faction_reputation if item.faction_id == faction_id), None
        )
        return item.value if item is not None else 0

    def with_reputation(self, faction_id: str, value: int) -> ActorState:
        values = {item.faction_id: item.value for item in self.faction_reputation}
        values[faction_id] = max(-100, min(100, value))
        return replace(
            self,
            faction_reputation=tuple(ReputationValue(key, values[key]) for key in sorted(values)),
        )

    def need(self, need_id: str) -> int:
        item = next((item for item in self.needs if item.need_id == need_id), None)
        return item.value if item is not None else 100

    def with_need(self, need_id: str, value: int) -> ActorState:
        values = {item.need_id: item.value for item in self.needs}
        values[need_id] = max(0, min(100, value))
        return replace(
            self,
            needs=tuple(NeedValue(key, values[key]) for key in sorted(values)),
        )


@dataclass(frozen=True, slots=True)
class WorldState:
    tick: int
    day: int
    minute_of_day: int
    minute_tick: int
    active_actor_id: str
    actors: tuple[ActorState, ...]
    flags: frozenset[str] = frozenset()
    completed_interactions: frozenset[str] = frozenset()
    quests: tuple[QuestState, ...] = ()
    dialogue: DialogueState | None = None
    active_scene_id: str | None = None
    triggered_scenes: frozenset[str] = frozenset()
    recent_events: tuple[DomainEvent, ...] = ()
    stockpiles: tuple[StockpileState, ...] = ()
    constructions: tuple[ConstructionState, ...] = ()
    production_jobs: tuple[ProductionJob, ...] = ()
    pending_consequences: tuple[PendingConsequence, ...] = ()
    triggered_consequences: frozenset[str] = frozenset()
    last_message: str = ""

    @property
    def absolute_minute(self) -> int:
        return (self.day - 1) * 1440 + self.minute_of_day

    def actor(self, actor_id: str) -> ActorState:
        for actor in self.actors:
            if actor.actor_id == actor_id:
                return actor
        raise KeyError(actor_id)

    def quest(self, quest_id: str) -> QuestState:
        item = next((item for item in self.quests if item.quest_id == quest_id), None)
        return item if item is not None else QuestState(quest_id)

    def stockpile(self, stockpile_id: str) -> StockpileState:
        for stockpile in self.stockpiles:
            if stockpile.stockpile_id == stockpile_id:
                return stockpile
        raise KeyError(stockpile_id)


@dataclass(frozen=True, slots=True)
class GameAction:
    kind: str
    actor_id: str | None = None
    dx: int = 0
    dy: int = 0
    map_id: str | None = None
    x: int | None = None
    y: int | None = None
    ability_id: str | None = None
    target_actor_id: str | None = None
    interaction_id: str | None = None
    dialogue_id: str | None = None
    choice_id: str | None = None
    blueprint_id: str | None = None
    construction_instance_id: str | None = None
    recipe_id: str | None = None
    stockpile_id: str | None = None
    resource_id: str | None = None
    amount: int = 0
    direction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "actor_id": self.actor_id,
            "dx": self.dx,
            "dy": self.dy,
            "map_id": self.map_id,
            "x": self.x,
            "y": self.y,
            "ability_id": self.ability_id,
            "target_actor_id": self.target_actor_id,
            "interaction_id": self.interaction_id,
            "dialogue_id": self.dialogue_id,
            "choice_id": self.choice_id,
            "blueprint_id": self.blueprint_id,
            "construction_instance_id": self.construction_instance_id,
            "recipe_id": self.recipe_id,
            "stockpile_id": self.stockpile_id,
            "resource_id": self.resource_id,
            "amount": self.amount,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GameAction:
        kind = raw.get("kind")
        if kind not in {
            "tick",
            "select_actor",
            "move",
            "navigate",
            "interact",
            "use_ability",
            "start_dialogue",
            "choose_dialogue",
            "end_dialogue",
            "dismiss_scene",
            "build",
            "start_production",
            "transfer_resource",
        }:
            raise ValueError("action kind is unknown")
        for field in (
            "actor_id",
            "map_id",
            "ability_id",
            "target_actor_id",
            "interaction_id",
            "dialogue_id",
            "choice_id",
            "blueprint_id",
            "construction_instance_id",
            "recipe_id",
            "stockpile_id",
            "resource_id",
            "direction",
        ):
            if raw.get(field) is not None and not isinstance(raw[field], str):
                raise ValueError(f"action {field} must be a string or null")
        for field in ("dx", "dy", "amount"):
            value = raw.get(field, 0)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"action {field} must be an integer")
        for field in ("x", "y"):
            value = raw.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"action {field} must be an integer or null")
        return cls(
            kind=kind,
            actor_id=raw.get("actor_id"),
            dx=raw.get("dx", 0),
            dy=raw.get("dy", 0),
            map_id=raw.get("map_id"),
            x=raw.get("x"),
            y=raw.get("y"),
            ability_id=raw.get("ability_id"),
            target_actor_id=raw.get("target_actor_id"),
            interaction_id=raw.get("interaction_id"),
            dialogue_id=raw.get("dialogue_id"),
            choice_id=raw.get("choice_id"),
            blueprint_id=raw.get("blueprint_id"),
            construction_instance_id=raw.get("construction_instance_id"),
            recipe_id=raw.get("recipe_id"),
            stockpile_id=raw.get("stockpile_id"),
            resource_id=raw.get("resource_id"),
            amount=raw.get("amount", 0),
            direction=raw.get("direction"),
        )


def initial_world_state(pack: WorldPack) -> WorldState:
    playable = pack.playable_actor_ids
    actors = tuple(
        ActorState(
            actor_id=actor.id,
            map_id=actor.spawn.map_id,
            x=actor.spawn.x,
            y=actor.spawn.y,
            resources=tuple(
                ResourceValue(resource_id, value) for resource_id, value in actor.resources
            ),
            knowledge=tuple(KnowledgeValue(fact_id, status) for fact_id, status in actor.knowledge),
            relationships=tuple(
                RelationshipValue(target_actor_id, dimension, value)
                for target_actor_id, dimensions in actor.relationships
                for dimension, value in dimensions
            ),
            faction_reputation=tuple(
                ReputationValue(faction_id, value) for faction_id, value in actor.faction_reputation
            ),
            needs=tuple(NeedValue(need_id, value) for need_id, value in actor.needs),
        )
        for actor in pack.actors.values()
    )
    state = WorldState(
        tick=0,
        day=pack.clock.start_day,
        minute_of_day=pack.clock.start_minute,
        minute_tick=0,
        active_actor_id=playable[0],
        actors=actors,
        quests=tuple(QuestState(quest_id) for quest_id in sorted(pack.quests)),
        stockpiles=tuple(
            StockpileState(
                stockpile.id,
                tuple(
                    ResourceValue(resource_id, value) for resource_id, value in stockpile.resources
                ),
            )
            for stockpile in pack.stockpiles.values()
        ),
    )
    from isoworld.world.narrative import initialize_narrative

    return initialize_narrative(state, pack)


def _replace_actor(state: WorldState, updated: ActorState) -> WorldState:
    return replace(
        state,
        actors=tuple(
            updated if actor.actor_id == updated.actor_id else actor for actor in state.actors
        ),
    )


def _occupied(state: WorldState, map_id: str, *, except_actor: str | None = None) -> set[Cell]:
    return {
        (actor.x, actor.y)
        for actor in state.actors
        if actor.map_id == map_id and actor.actor_id != except_actor
    }


def _advance_clock(state: WorldState, pack: WorldPack) -> WorldState:
    minute_tick = state.minute_tick + 1
    day = state.day
    minute = state.minute_of_day
    if minute_tick >= pack.clock.ticks_per_minute:
        minute_tick = 0
        minute += 1
        if minute >= 1440:
            day += 1
            minute = 0
    return replace(
        state,
        tick=state.tick + 1,
        day=day,
        minute_of_day=minute,
        minute_tick=minute_tick,
        last_message="",
    )


def _schedule_enabled(state: WorldState, actor: ActorState, pack: WorldPack) -> bool:
    definition = pack.actors[actor.actor_id]
    if actor.active_goal_id is not None:
        return False
    if definition.schedule_id is None or definition.schedule_mode == "never":
        return False
    if definition.schedule_mode == "when_inactive" and actor.actor_id == state.active_actor_id:
        return False
    return True


def _plan_schedules(state: WorldState, pack: WorldPack) -> WorldState:
    from isoworld.world.living_world import dynamic_blocked_cells

    result = state
    for actor in sorted(result.actors, key=lambda item: item.actor_id):
        actor = result.actor(actor.actor_id)
        if actor.route or not _schedule_enabled(result, actor, pack):
            continue
        schedule_id = pack.actors[actor.actor_id].schedule_id
        if schedule_id is None:
            continue
        entry = pack.schedules[schedule_id].entry_at(result.minute_of_day)
        if entry is None:
            continue
        candidates = [value for value in entry.destinations if value.map_id == actor.map_id]
        if any((actor.x, actor.y) == (value.x, value.y) for value in candidates):
            continue
        blocked = _occupied(
            result, actor.map_id, except_actor=actor.actor_id
        ) | dynamic_blocked_cells(result, pack, actor.map_id)
        for destination in candidates:
            route = find_path(
                pack,
                actor.map_id,
                (actor.x, actor.y),
                (destination.x, destination.y),
                blocked=blocked,
            )
            if route:
                result = _replace_actor(result, replace(actor, route=route, blocked_ticks=0))
                break
    return result


def _advance_routes(state: WorldState, pack: WorldPack) -> WorldState:
    from isoworld.world.living_world import is_walkable

    if state.tick % pack.clock.movement_interval_ticks:
        return state
    current_occupancy = {(actor.map_id, actor.x, actor.y): actor.actor_id for actor in state.actors}
    reservations: set[tuple[str, int, int]] = set()
    updates: dict[str, ActorState] = {}
    for actor in sorted(state.actors, key=lambda item: item.actor_id):
        if not actor.route:
            continue
        target = actor.route[0]
        key = (actor.map_id, target[0], target[1])
        occupied_by = current_occupancy.get(key)
        available = (
            is_walkable(state, pack, actor.map_id, *target)
            and key not in reservations
            and (occupied_by is None or occupied_by == actor.actor_id)
        )
        if available:
            reservations.add(key)
            updates[actor.actor_id] = replace(
                actor,
                x=target[0],
                y=target[1],
                route=actor.route[1:],
                blocked_ticks=0,
            )
        else:
            blocked_ticks = actor.blocked_ticks + 1
            updates[actor.actor_id] = replace(
                actor,
                route=() if blocked_ticks >= 3 else actor.route,
                blocked_ticks=blocked_ticks,
            )
    if not updates:
        return state
    return replace(
        state,
        actors=tuple(updates.get(actor.actor_id, actor) for actor in state.actors),
    )


def _apply_effects(
    state: WorldState,
    effects: tuple[EffectDefinition, ...],
    *,
    source_actor_id: str,
    target_actor_id: str,
    events: list[DomainEvent] | None = None,
    pack: WorldPack | None = None,
) -> WorldState:
    result = state
    for effect in effects:
        if effect.kind == "set_flag" and effect.flag:
            result = replace(result, flags=result.flags | {effect.flag})
            if events is not None:
                events.append(DomainEvent("flag_changed", source_actor_id, effect.flag))
        elif effect.kind == "clear_flag" and effect.flag:
            result = replace(result, flags=result.flags - {effect.flag})
            if events is not None:
                events.append(DomainEvent("flag_changed", source_actor_id, effect.flag))
        elif effect.kind == "change_resource" and effect.resource:
            actor_id = source_actor_id if effect.target == "self" else target_actor_id
            actor = result.actor(actor_id)
            actor = actor.with_resource(
                effect.resource,
                actor.resource(effect.resource) + effect.amount,
            )
            result = _replace_actor(result, actor)
        elif effect.kind == "learn_fact" and effect.fact_id and effect.knowledge_status:
            actor_id = source_actor_id if effect.target == "self" else target_actor_id
            if pack is not None and effect.fact_id in pack.actors[actor_id].forbidden_fact_ids:
                continue
            actor = result.actor(actor_id).with_knowledge(effect.fact_id, effect.knowledge_status)
            result = _replace_actor(result, actor)
            if events is not None:
                events.append(DomainEvent("fact_learned", actor_id, effect.fact_id))
        elif effect.kind == "change_relationship" and effect.target_actor_id and effect.dimension:
            actor_id = source_actor_id if effect.target == "self" else target_actor_id
            actor = result.actor(actor_id)
            actor = actor.with_relationship(
                effect.target_actor_id,
                effect.dimension,
                actor.relationship(effect.target_actor_id, effect.dimension) + effect.amount,
            )
            result = _replace_actor(result, actor)
            if events is not None:
                events.append(DomainEvent("relationship_changed", actor_id, effect.target_actor_id))
        elif effect.kind == "change_reputation" and effect.faction_id:
            actor_id = source_actor_id if effect.target == "self" else target_actor_id
            actor = result.actor(actor_id)
            actor = actor.with_reputation(
                effect.faction_id,
                actor.reputation(effect.faction_id) + effect.amount,
            )
            result = _replace_actor(result, actor)
            if events is not None:
                events.append(DomainEvent("reputation_changed", actor_id, effect.faction_id))
        elif effect.kind == "change_stockpile_resource" and effect.stockpile_id and effect.resource:
            stockpile = result.stockpile(effect.stockpile_id)
            value = stockpile.resource(effect.resource) + effect.amount
            if pack is not None:
                available = pack.stockpiles[effect.stockpile_id].capacity - (
                    stockpile.total - stockpile.resource(effect.resource)
                )
                value = min(value, available)
            stockpile = stockpile.with_resource(effect.resource, value)
            result = replace(
                result,
                stockpiles=tuple(
                    stockpile if item.stockpile_id == stockpile.stockpile_id else item
                    for item in result.stockpiles
                ),
            )
            if events is not None:
                events.append(DomainEvent("stockpile_changed", source_actor_id, effect.resource))
        elif effect.kind == "change_need" and effect.need_id:
            actor_id = source_actor_id if effect.target == "self" else target_actor_id
            actor = result.actor(actor_id)
            actor = actor.with_need(effect.need_id, actor.need(effect.need_id) + effect.amount)
            result = _replace_actor(result, actor)
            if events is not None:
                events.append(DomainEvent("need_changed", actor_id, effect.need_id))
    return result


def _move(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    from isoworld.world.living_world import is_walkable

    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    target = (actor.x + action.dx, actor.y + action.dy)
    if not is_walkable(state, pack, actor.map_id, *target):
        return replace(state, last_message="Movement blocked")
    if target in _occupied(state, actor.map_id, except_actor=actor_id):
        return replace(state, last_message="Cell occupied")
    result = _replace_actor(
        replace(state, last_message=""),
        replace(actor, x=target[0], y=target[1], route=(), blocked_ticks=0),
    )
    return replace(
        result,
        recent_events=(DomainEvent("location_entered", actor_id, actor.map_id),),
    )


def _navigate(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    from isoworld.world.living_world import dynamic_blocked_cells

    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    if action.x is None or action.y is None or action.map_id not in {None, actor.map_id}:
        return replace(state, last_message="Invalid navigation target")
    route = find_path(
        pack,
        actor.map_id,
        (actor.x, actor.y),
        (action.x, action.y),
        blocked=_occupied(state, actor.map_id, except_actor=actor_id)
        | dynamic_blocked_cells(state, pack, actor.map_id),
    )
    if not route and (actor.x, actor.y) != (action.x, action.y):
        return replace(state, last_message="No route")
    return _replace_actor(
        replace(state, last_message="Route planned" if route else "Already there"),
        replace(actor, route=route, blocked_ticks=0),
    )


def _interact(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    candidates = [
        interaction
        for interaction in pack.interactions.values()
        if interaction.location.map_id == actor.map_id
        and abs(interaction.location.x - actor.x) + abs(interaction.location.y - actor.y)
        <= interaction.range
        and interaction.required_flags <= state.flags
        and not (interaction.forbidden_flags & state.flags)
        and (interaction.repeatable or interaction.id not in state.completed_interactions)
    ]
    if action.interaction_id is not None:
        candidates = [item for item in candidates if item.id == action.interaction_id]
    if not candidates:
        return replace(state, last_message="Nothing to interact with")
    interaction = min(
        candidates,
        key=lambda item: (
            abs(item.location.x - actor.x) + abs(item.location.y - actor.y),
            item.id,
        ),
    )
    events = [DomainEvent("interaction_completed", actor_id, interaction.id)]
    result = _apply_effects(
        state,
        interaction.effects,
        source_actor_id=actor_id,
        target_actor_id=actor_id,
        events=events,
        pack=pack,
    )
    if not interaction.repeatable:
        result = replace(
            result,
            completed_interactions=result.completed_interactions | {interaction.id},
        )
    return replace(result, last_message=interaction.prompt, recent_events=tuple(events))


def _use_ability(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    definition = pack.actors[actor_id]
    ability_id = action.ability_id or (
        definition.ability_ids[0] if definition.ability_ids else None
    )
    if ability_id is None or ability_id not in definition.ability_ids:
        return replace(state, last_message="Ability unavailable")
    ability = pack.abilities[ability_id]
    if actor.cooldown_until(ability_id) > state.absolute_minute:
        return replace(state, last_message="Ability is cooling down")
    if any(actor.resource(key) < value for key, value in ability.costs.items()):
        return replace(state, last_message="Not enough resources")

    target_id = actor_id
    if ability.target == "actor":
        target_id = action.target_actor_id or ""
        try:
            target = state.actor(target_id)
        except KeyError:
            return replace(state, last_message="Invalid ability target")
        distance = abs(target.x - actor.x) + abs(target.y - actor.y)
        if target.map_id != actor.map_id or distance > ability.range:
            return replace(state, last_message="Ability target is out of range")

    updated_actor = actor
    for resource_id, cost in ability.costs.items():
        updated_actor = updated_actor.with_resource(
            resource_id,
            updated_actor.resource(resource_id) - cost,
        )
    updated_actor = updated_actor.with_cooldown(
        ability_id,
        state.absolute_minute + ability.cooldown_minutes,
    )
    result = _replace_actor(state, updated_actor)
    events = [DomainEvent("ability_used", actor_id, ability_id)]
    result = _apply_effects(
        result,
        ability.effects,
        source_actor_id=actor_id,
        target_actor_id=target_id,
        events=events,
        pack=pack,
    )
    return replace(result, last_message=ability.display_name, recent_events=tuple(events))


def reduce_world(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    from isoworld.world.living_world import (
        advance_living_world,
        build,
        start_production,
        transfer_resource,
    )
    from isoworld.world.narrative import handle_narrative_action, postprocess_narrative

    state = replace(state, recent_events=())
    if state.active_scene_id is not None or state.dialogue is not None:
        return handle_narrative_action(state, action, pack)
    if action.kind in {
        "start_dialogue",
        "choose_dialogue",
        "end_dialogue",
        "dismiss_scene",
    }:
        return handle_narrative_action(state, action, pack)
    if action.kind == "tick":
        advanced = _advance_clock(state, pack)
        events: list[DomainEvent] = []
        minute_changed = (state.day, state.minute_of_day) != (
            advanced.day,
            advanced.minute_of_day,
        )
        if minute_changed:
            events.append(DomainEvent("minute_changed"))
            advanced = advance_living_world(advanced, pack, events)
            events = list(advanced.recent_events)
        planned = _plan_schedules(advanced, pack)
        result = _advance_routes(planned, pack)
        for actor in result.actors:
            previous = state.actor(actor.actor_id)
            if (previous.map_id, previous.x, previous.y) != (actor.map_id, actor.x, actor.y):
                events.append(DomainEvent("location_entered", actor.actor_id, actor.map_id))
        result = replace(result, recent_events=tuple(events))
        return postprocess_narrative(result, pack, tuple(events), state.active_actor_id)
    if action.kind == "select_actor" and action.actor_id in pack.playable_actor_ids:
        return replace(
            state,
            active_actor_id=action.actor_id,
            recent_events=(),
            last_message="",
        )
    if action.kind == "move":
        result = _move(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    if action.kind == "navigate":
        return replace(_navigate(state, action, pack), recent_events=())
    if action.kind == "interact":
        result = _interact(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    if action.kind == "use_ability":
        result = _use_ability(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    if action.kind == "build":
        result = build(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    if action.kind == "start_production":
        result = start_production(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    if action.kind == "transfer_resource":
        result = transfer_resource(state, action, pack)
        return postprocess_narrative(
            result, pack, result.recent_events, action.actor_id or state.active_actor_id
        )
    return replace(state, recent_events=())
