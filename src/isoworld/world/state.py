from __future__ import annotations

from dataclasses import dataclass, replace

from isoworld.content.models import WorldPack


@dataclass(frozen=True, slots=True)
class ActorState:
    actor_id: str
    map_id: str
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class WorldState:
    tick: int
    active_actor_id: str
    actors: tuple[ActorState, ...]

    def actor(self, actor_id: str) -> ActorState:
        for actor in self.actors:
            if actor.actor_id == actor_id:
                return actor
        raise KeyError(actor_id)


@dataclass(frozen=True, slots=True)
class GameAction:
    kind: str
    actor_id: str | None = None
    dx: int = 0
    dy: int = 0


def initial_world_state(pack: WorldPack) -> WorldState:
    playable = pack.playable_actor_ids
    actors = tuple(
        ActorState(
            actor_id=actor.id,
            map_id=actor.spawn.map_id,
            x=actor.spawn.x,
            y=actor.spawn.y,
        )
        for actor in pack.actors.values()
    )
    return WorldState(tick=0, active_actor_id=playable[0], actors=actors)


def reduce_world(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    if action.kind == "tick":
        return replace(state, tick=state.tick + 1)

    if action.kind == "select_actor" and action.actor_id in pack.playable_actor_ids:
        return replace(state, active_actor_id=action.actor_id)

    if action.kind != "move":
        return state

    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    world_map = pack.maps[actor.map_id]
    target_x = actor.x + action.dx
    target_y = actor.y + action.dy
    if target_x < 0 or target_y < 0 or target_x >= world_map.width or target_y >= world_map.height:
        return state
    tile_id = world_map.tile_id_at(target_x, target_y)
    if not pack.tile_types[tile_id].walkable:
        return state

    moved = replace(actor, x=target_x, y=target_y)
    actors = tuple(moved if item.actor_id == actor_id else item for item in state.actors)
    return replace(state, actors=actors)
