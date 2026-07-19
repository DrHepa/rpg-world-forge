from __future__ import annotations

from dataclasses import dataclass

from isoworld.content.models import Color, WorldPack
from isoworld.world.state import WorldState


@dataclass(frozen=True, slots=True)
class TileView:
    x: int
    y: int
    elevation: int
    color: Color


@dataclass(frozen=True, slots=True)
class ActorView:
    actor_id: str
    display_name: str
    x: int
    y: int
    color: Color
    active: bool


@dataclass(frozen=True, slots=True)
class RenderState:
    world_title: str
    map_title: str
    tick: int
    tiles: tuple[TileView, ...]
    actors: tuple[ActorView, ...]
    hud_lines: tuple[str, ...]


def build_render_state(state: WorldState, pack: WorldPack) -> RenderState:
    active = state.actor(state.active_actor_id)
    world_map = pack.maps[active.map_id]
    tiles = tuple(
        TileView(
            x=x,
            y=y,
            elevation=pack.tile_types[world_map.tile_id_at(x, y)].height,
            color=pack.tile_types[world_map.tile_id_at(x, y)].color,
        )
        for y in range(world_map.height)
        for x in range(world_map.width)
    )
    actors = tuple(
        ActorView(
            actor_id=actor.actor_id,
            display_name=pack.actors[actor.actor_id].display_name,
            x=actor.x,
            y=actor.y,
            color=pack.actors[actor.actor_id].color,
            active=actor.actor_id == state.active_actor_id,
        )
        for actor in state.actors
        if actor.map_id == active.map_id
    )
    active_name = pack.actors[state.active_actor_id].display_name
    hud_lines = (
        pack.ui["move_help"],
        pack.ui["switch_help"],
        f"{pack.ui['active_actor']}: {active_name}",
    )
    return RenderState(
        world_title=pack.title,
        map_title=world_map.display_name,
        tick=state.tick,
        tiles=tiles,
        actors=actors,
        hud_lines=hud_lines,
    )
