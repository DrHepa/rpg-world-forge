from __future__ import annotations

from dataclasses import replace

from isoworld.content.models import GoalDefinition, Location, WorldPack
from isoworld.world.navigation import Cell, find_path
from isoworld.world.state import (
    ActorState,
    ConstructionState,
    DomainEvent,
    GameAction,
    PendingConsequence,
    ProductionJob,
    StockpileState,
    WorldState,
    _apply_effects,
    _replace_actor,
)


def _replace_stockpile(state: WorldState, updated: StockpileState) -> WorldState:
    return replace(
        state,
        stockpiles=tuple(
            updated if item.stockpile_id == updated.stockpile_id else item
            for item in state.stockpiles
        ),
    )


def construction_cells(
    state: WorldState,
    pack: WorldPack,
    map_id: str,
    *,
    blocking_only: bool = False,
) -> set[Cell]:
    result: set[Cell] = set()
    for construction in state.constructions:
        if construction.map_id != map_id or construction.status not in {"building", "completed"}:
            continue
        blueprint = pack.constructions[construction.blueprint_id]
        if blocking_only and not blueprint.blocks_movement:
            continue
        result.update((construction.x + dx, construction.y + dy) for dx, dy in blueprint.footprint)
    return result


def is_walkable(state: WorldState, pack: WorldPack, map_id: str, x: int, y: int) -> bool:
    return pack.is_walkable(map_id, x, y) and (x, y) not in construction_cells(
        state, pack, map_id, blocking_only=True
    )


def dynamic_blocked_cells(state: WorldState, pack: WorldPack, map_id: str) -> set[Cell]:
    return construction_cells(state, pack, map_id, blocking_only=True)


def scarcity_percent(state: WorldState, pack: WorldPack, resource_id: str) -> int:
    target = pack.resources[resource_id].scarcity_target
    if target <= 0:
        return 0
    available = sum(actor.resource(resource_id) for actor in state.actors) + sum(
        stockpile.resource(resource_id) for stockpile in state.stockpiles
    )
    return max(0, min(100, 100 - (available * 100 // target)))


def _adjacent(actor: ActorState, location: Location) -> bool:
    return actor.map_id == location.map_id and (
        abs(actor.x - location.x) + abs(actor.y - location.y) <= 1
    )


def _instance_id(blueprint_id: str, map_id: str, x: int, y: int) -> str:
    return f"{blueprint_id}__{map_id}__{x}_{y}"


def _reserved_output_units(state: WorldState, pack: WorldPack, stockpile_id: str) -> int:
    total = 0
    constructions = {item.instance_id: item for item in state.constructions}
    for job in state.production_jobs:
        construction = constructions.get(job.construction_instance_id)
        if construction is None:
            continue
        if pack.constructions[construction.blueprint_id].stockpile_id == stockpile_id:
            total += sum(value for _, value in pack.production_recipes[job.recipe_id].outputs)
    return total


def build(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    blueprint = pack.constructions.get(action.blueprint_id or "")
    map_id = action.map_id or actor.map_id
    if blueprint is None or action.x is None or action.y is None or map_id != actor.map_id:
        return replace(state, last_message="Invalid construction request")
    anchor = Location(map_id, action.x, action.y)
    if not _adjacent(actor, anchor):
        return replace(state, last_message="Construction site is out of range")
    cells = {(anchor.x + dx, anchor.y + dy) for dx, dy in blueprint.footprint}
    if (
        any(not pack.is_walkable(map_id, x, y) for x, y in cells)
        or cells & construction_cells(state, pack, map_id)
        or any(other.map_id == map_id and (other.x, other.y) in cells for other in state.actors)
    ):
        return replace(state, last_message="Construction site is blocked")
    if any(actor.resource(resource_id) < cost for resource_id, cost in blueprint.costs):
        return replace(state, last_message="Not enough construction resources")
    updated = actor
    for resource_id, cost in blueprint.costs:
        updated = updated.with_resource(resource_id, updated.resource(resource_id) - cost)
    result = _replace_actor(state, updated)
    instance = ConstructionState(
        _instance_id(blueprint.id, map_id, anchor.x, anchor.y),
        blueprint.id,
        map_id,
        anchor.x,
        anchor.y,
        actor_id,
        "building",
        state.absolute_minute + blueprint.build_minutes,
    )
    result = replace(
        result,
        constructions=tuple(
            sorted(result.constructions + (instance,), key=lambda item: item.instance_id)
        ),
        recent_events=(DomainEvent("construction_started", actor_id, blueprint.id),),
        last_message=f"Construction started: {blueprint.display_name}",
    )
    return process_consequences(result, pack, result.recent_events, actor_id)


def start_production(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    recipe = pack.production_recipes.get(action.recipe_id or "")
    construction = next(
        (
            item
            for item in state.constructions
            if item.instance_id == action.construction_instance_id
        ),
        None,
    )
    if (
        recipe is None
        or construction is None
        or construction.status != "completed"
        or recipe.required_construction_id != construction.blueprint_id
        or any(
            job.construction_instance_id == construction.instance_id
            for job in state.production_jobs
        )
    ):
        return replace(state, last_message="Production unavailable")
    if actor.map_id != construction.map_id or (
        abs(actor.x - construction.x) + abs(actor.y - construction.y) > 1
    ):
        return replace(state, last_message="Production site is out of range")
    stockpile_id = pack.constructions[construction.blueprint_id].stockpile_id
    if stockpile_id is None:
        return replace(state, last_message="Production has no stockpile")
    stockpile = state.stockpile(stockpile_id)
    if any(stockpile.resource(key) < amount for key, amount in recipe.inputs):
        return replace(state, last_message="Production inputs are missing")
    future_total = (
        stockpile.total
        - sum(value for _, value in recipe.inputs)
        + _reserved_output_units(state, pack, stockpile_id)
        + sum(value for _, value in recipe.outputs)
    )
    if future_total > pack.stockpiles[stockpile_id].capacity:
        return replace(state, last_message="Stockpile lacks output capacity")
    updated = stockpile
    for resource_id, amount in recipe.inputs:
        updated = updated.with_resource(resource_id, updated.resource(resource_id) - amount)
    result = _replace_stockpile(state, updated)
    job = ProductionJob(
        construction.instance_id,
        recipe.id,
        actor_id,
        state.absolute_minute + recipe.duration_minutes,
    )
    result = replace(
        result,
        production_jobs=tuple(
            sorted(result.production_jobs + (job,), key=lambda item: item.construction_instance_id)
        ),
        recent_events=(DomainEvent("production_started", actor_id, recipe.id),),
        last_message=f"Production started: {recipe.display_name}",
    )
    return process_consequences(result, pack, result.recent_events, actor_id)


def transfer_resource(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    stockpile_definition = pack.stockpiles.get(action.stockpile_id or "")
    if (
        stockpile_definition is None
        or action.resource_id not in pack.resources
        or action.amount <= 0
        or action.direction not in {"deposit", "withdraw"}
        or not _adjacent(actor, stockpile_definition.location)
    ):
        return replace(state, last_message="Invalid resource transfer")
    stockpile = state.stockpile(stockpile_definition.id)
    if action.direction == "deposit":
        if actor.resource(action.resource_id) < action.amount:
            return replace(state, last_message="Actor lacks that resource")
        if (
            stockpile.total
            + _reserved_output_units(state, pack, stockpile_definition.id)
            + action.amount
            > stockpile_definition.capacity
        ):
            return replace(state, last_message="Stockpile is full")
        actor = actor.with_resource(
            action.resource_id, actor.resource(action.resource_id) - action.amount
        )
        stockpile = stockpile.with_resource(
            action.resource_id, stockpile.resource(action.resource_id) + action.amount
        )
    else:
        if stockpile.resource(action.resource_id) < action.amount:
            return replace(state, last_message="Stockpile lacks that resource")
        actor = actor.with_resource(
            action.resource_id, actor.resource(action.resource_id) + action.amount
        )
        stockpile = stockpile.with_resource(
            action.resource_id, stockpile.resource(action.resource_id) - action.amount
        )
    result = _replace_actor(_replace_stockpile(state, stockpile), actor)
    events = (DomainEvent("resource_transferred", actor_id, action.resource_id),)
    result = replace(result, recent_events=events, last_message="Resource transferred")
    return process_consequences(result, pack, events, actor_id)


def advance_living_world(
    state: WorldState,
    pack: WorldPack,
    events: list[DomainEvent],
) -> WorldState:
    result = state
    actors: list[ActorState] = []
    for actor in result.actors:
        updated = actor
        for need_id, _ in pack.actors[actor.actor_id].needs:
            need = pack.needs[need_id]
            if result.absolute_minute % need.decay_interval_minutes == 0:
                updated = updated.with_need(need_id, updated.need(need_id) - need.decay_amount)
                events.append(DomainEvent("need_changed", actor.actor_id, need_id))
        actors.append(updated)
    result = replace(result, actors=tuple(actors))

    completed: list[ConstructionState] = []
    for item in result.constructions:
        if item.status == "building" and item.complete_at_minute <= result.absolute_minute:
            item = replace(item, status="completed")
            events.append(
                DomainEvent("construction_completed", item.builder_actor_id, item.blueprint_id)
            )
        completed.append(item)
    result = replace(result, constructions=tuple(completed))

    remaining_jobs: list[ProductionJob] = []
    for job in result.production_jobs:
        if job.complete_at_minute > result.absolute_minute:
            remaining_jobs.append(job)
            continue
        recipe = pack.production_recipes[job.recipe_id]
        construction = next(
            item
            for item in result.constructions
            if item.instance_id == job.construction_instance_id
        )
        stockpile_id = pack.constructions[construction.blueprint_id].stockpile_id
        if stockpile_id is None:
            continue
        stockpile = result.stockpile(stockpile_id)
        for resource_id, amount in recipe.outputs:
            amount = min(amount, pack.stockpiles[stockpile_id].capacity - stockpile.total)
            stockpile = stockpile.with_resource(
                resource_id, stockpile.resource(resource_id) + amount
            )
        result = _replace_stockpile(result, stockpile)
        events.append(DomainEvent("production_completed", job.actor_id, recipe.id))
    result = replace(result, production_jobs=tuple(remaining_jobs))
    result = process_consequences(result, pack, tuple(events), state.active_actor_id)
    events[:] = list(result.recent_events)
    return plan_goals(result, pack, events)


def _goal_chain(goal: GoalDefinition, pack: WorldPack) -> tuple[GoalDefinition, ...]:
    chain = [goal]
    while chain[-1].parent_id is not None:
        chain.append(pack.goals[chain[-1].parent_id or ""])
    return tuple(reversed(chain))


def _route_to_adjacent(
    state: WorldState,
    pack: WorldPack,
    actor: ActorState,
    location: Location,
) -> tuple[Cell, ...]:
    if actor.map_id != location.map_id:
        return ()
    occupied = {
        (item.x, item.y)
        for item in state.actors
        if item.actor_id != actor.actor_id and item.map_id == actor.map_id
    }
    blocked = occupied | dynamic_blocked_cells(state, pack, actor.map_id)
    candidates = sorted(
        ((location.x + dx, location.y + dy) for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1))),
        key=lambda cell: (abs(cell[0] - actor.x) + abs(cell[1] - actor.y), cell[1], cell[0]),
    )
    for target in candidates:
        if target in blocked or not pack.is_walkable(actor.map_id, *target):
            continue
        route = find_path(
            pack,
            actor.map_id,
            (actor.x, actor.y),
            target,
            blocked=blocked,
        )
        if route or target == (actor.x, actor.y):
            return route
    return ()


def _execute_goal(
    state: WorldState,
    actor: ActorState,
    goal: GoalDefinition,
    pack: WorldPack,
) -> WorldState:
    action = goal.action
    if action is None:
        return state
    if action.kind == "satisfy_need" and action.need_id:
        need = pack.needs[action.need_id]
        if actor.resource(need.resource_id) >= need.consume_amount:
            actor = actor.with_resource(
                need.resource_id, actor.resource(need.resource_id) - need.consume_amount
            ).with_need(action.need_id, actor.need(action.need_id) + need.restore_amount)
            result = _replace_actor(state, replace(actor, active_goal_id=None, route=()))
            return replace(
                result,
                recent_events=result.recent_events
                + (DomainEvent("need_satisfied", actor.actor_id, action.need_id),),
            )
        stockpile_id = action.stockpile_id
        stockpile_definition = pack.stockpiles.get(stockpile_id or "")
        if stockpile_definition is None:
            return state
        if _adjacent(actor, stockpile_definition.location):
            stockpile = state.stockpile(stockpile_definition.id)
            if stockpile.resource(need.resource_id) < need.consume_amount:
                return state
            stockpile = stockpile.with_resource(
                need.resource_id, stockpile.resource(need.resource_id) - need.consume_amount
            )
            actor = actor.with_need(
                action.need_id, actor.need(action.need_id) + need.restore_amount
            )
            result = _replace_actor(
                _replace_stockpile(state, stockpile),
                replace(actor, active_goal_id=None, route=()),
            )
            return replace(
                result,
                recent_events=result.recent_events
                + (DomainEvent("need_satisfied", actor.actor_id, action.need_id),),
            )
        route = _route_to_adjacent(state, pack, actor, stockpile_definition.location)
        return _replace_actor(state, replace(actor, route=route, blocked_ticks=0))
    if action.kind == "produce" and action.recipe_id:
        recipe = pack.production_recipes[action.recipe_id]
        sites = sorted(
            (
                item
                for item in state.constructions
                if item.blueprint_id == recipe.required_construction_id
                and item.status == "completed"
            ),
            key=lambda item: item.instance_id,
        )
        if not sites:
            return state
        site = sites[0]
        if actor.map_id == site.map_id and abs(actor.x - site.x) + abs(actor.y - site.y) <= 1:
            return start_production(
                state,
                GameAction(
                    "start_production",
                    actor_id=actor.actor_id,
                    construction_instance_id=site.instance_id,
                    recipe_id=recipe.id,
                ),
                pack,
            )
        route = _route_to_adjacent(state, pack, actor, Location(site.map_id, site.x, site.y))
        return _replace_actor(state, replace(actor, route=route, blocked_ticks=0))
    if action.kind == "build" and action.blueprint_id and action.location:
        existing = next(
            (
                item
                for item in state.constructions
                if item.blueprint_id == action.blueprint_id
                and (item.map_id, item.x, item.y)
                == (action.location.map_id, action.location.x, action.location.y)
            ),
            None,
        )
        if existing is not None:
            return _replace_actor(state, replace(actor, active_goal_id=None, route=()))
        if _adjacent(actor, action.location):
            return build(
                state,
                GameAction(
                    "build",
                    actor_id=actor.actor_id,
                    blueprint_id=action.blueprint_id,
                    map_id=action.location.map_id,
                    x=action.location.x,
                    y=action.location.y,
                ),
                pack,
            )
        route = _route_to_adjacent(state, pack, actor, action.location)
        return _replace_actor(state, replace(actor, route=route, blocked_ticks=0))
    if action.kind == "travel" and action.location:
        if actor.map_id != action.location.map_id:
            return state
        blocked = dynamic_blocked_cells(state, pack, actor.map_id) | {
            (item.x, item.y)
            for item in state.actors
            if item.actor_id != actor.actor_id and item.map_id == actor.map_id
        }
        route = find_path(
            pack,
            actor.map_id,
            (actor.x, actor.y),
            (action.location.x, action.location.y),
            blocked=blocked,
        )
        return _replace_actor(state, replace(actor, route=route, blocked_ticks=0))
    return state


def plan_goals(state: WorldState, pack: WorldPack, events: list[DomainEvent]) -> WorldState:
    from isoworld.world.narrative import conditions_met

    result = state
    children: dict[str, list[GoalDefinition]] = {}
    for goal in pack.goals.values():
        if goal.parent_id is not None:
            children.setdefault(goal.parent_id, []).append(goal)
    for actor_definition in sorted(pack.actors.values(), key=lambda item: item.id):
        actor = result.actor(actor_definition.id)
        candidates: list[tuple[int, int, str, GoalDefinition]] = []
        stack = [(goal_id, 0) for goal_id in reversed(actor_definition.goal_ids)]
        while stack:
            goal_id, depth = stack.pop()
            goal = pack.goals[goal_id]
            chain = _goal_chain(goal, pack)
            if all(
                conditions_met(
                    result,
                    item.conditions,
                    source_actor_id=actor.actor_id,
                    events=tuple(events),
                    pack=pack,
                )
                for item in chain
            ):
                if goal.action is not None:
                    candidates.append((-goal.priority, -depth, goal.id, goal))
                stack.extend(
                    (item.id, depth + 1)
                    for item in sorted(
                        children.get(goal.id, []),
                        key=lambda item: item.id,
                        reverse=True,
                    )
                )
        if not candidates:
            if actor.active_goal_id is not None:
                result = _replace_actor(result, replace(actor, active_goal_id=None, route=()))
            continue
        goal = min(candidates)[3]
        actor = replace(actor, active_goal_id=goal.id)
        result = _replace_actor(result, actor)
        result = _execute_goal(result, actor, goal, pack)
        events.extend(event for event in result.recent_events if event not in events)
    return replace(result, recent_events=tuple(events))


def process_consequences(
    state: WorldState,
    pack: WorldPack,
    events: tuple[DomainEvent, ...],
    source_actor_id: str,
) -> WorldState:
    from isoworld.world.narrative import conditions_met

    result = state
    mutable_events = list(events)
    pending = list(result.pending_consequences)
    for event in mutable_events:
        for definition in sorted(pack.consequences.values(), key=lambda item: item.id):
            if definition.once and definition.id in result.triggered_consequences:
                continue
            if any(item.consequence_id == definition.id for item in pending):
                continue
            if event.kind != definition.trigger_event or (
                definition.subject_id is not None and event.subject_id != definition.subject_id
            ):
                continue
            actor_id = event.actor_id or source_actor_id
            if not conditions_met(
                result,
                definition.conditions,
                source_actor_id=actor_id,
                events=tuple(mutable_events),
                pack=pack,
            ):
                continue
            pending.append(
                PendingConsequence(
                    definition.id,
                    result.absolute_minute + definition.delay_minutes,
                    actor_id,
                )
            )
            mutable_events.append(DomainEvent("consequence_scheduled", actor_id, definition.id))
    result = replace(
        result,
        pending_consequences=tuple(
            sorted(pending, key=lambda item: (item.due_at_minute, item.consequence_id))
        ),
    )
    due = [
        item for item in result.pending_consequences if item.due_at_minute <= result.absolute_minute
    ]
    for pending_item in due:
        definition = pack.consequences[pending_item.consequence_id]
        result = _apply_effects(
            result,
            definition.effects,
            source_actor_id=pending_item.source_actor_id,
            target_actor_id=pending_item.source_actor_id,
            events=mutable_events,
            pack=pack,
        )
        result = replace(
            result,
            pending_consequences=tuple(
                item for item in result.pending_consequences if item != pending_item
            ),
            triggered_consequences=(
                result.triggered_consequences | {definition.id}
                if definition.once
                else result.triggered_consequences
            ),
        )
        resolved = DomainEvent("consequence_resolved", pending_item.source_actor_id, definition.id)
        mutable_events.append(resolved)
        result = process_consequences(
            result,
            pack,
            (resolved,),
            pending_item.source_actor_id,
        )
        mutable_events.extend(
            event for event in result.recent_events if event not in mutable_events
        )
    return replace(result, recent_events=tuple(mutable_events))
