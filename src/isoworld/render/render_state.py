from __future__ import annotations

from dataclasses import dataclass

from isoworld.content.models import Color, WorldPack
from isoworld.world.navigation import Cell
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
    route: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class InteractionView:
    interaction_id: str
    display_name: str
    x: int
    y: int
    available: bool


@dataclass(frozen=True, slots=True)
class RenderState:
    world_title: str
    map_title: str
    tick: int
    time_text: str
    tiles: tuple[TileView, ...]
    actors: tuple[ActorView, ...]
    interactions: tuple[InteractionView, ...]
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
            route=actor.route,
        )
        for actor in state.actors
        if actor.map_id == active.map_id
    )
    interactions = tuple(
        InteractionView(
            interaction_id=item.id,
            display_name=item.display_name,
            x=item.location.x,
            y=item.location.y,
            available=(
                item.required_flags <= state.flags
                and not (item.forbidden_flags & state.flags)
                and (item.repeatable or item.id not in state.completed_interactions)
            ),
        )
        for item in pack.interactions.values()
        if item.location.map_id == active.map_id
    )
    active_name = pack.actors[state.active_actor_id].display_name
    resources = ", ".join(f"{item.id}: {item.value}" for item in active.resources) or "-"
    time_text = (
        f"{pack.ui['clock_label']} {state.day} "
        f"{state.minute_of_day // 60:02d}:{state.minute_of_day % 60:02d}"
    )
    hud_lines = (
        f"{pack.ui['active_actor']}: {active_name} | {resources}",
        f"{pack.ui['move_help']} | {pack.ui['navigate_help']}",
        f"{pack.ui['switch_help']} | {pack.ui['interact_help']} | {pack.ui['ability_help']}",
        state.last_message,
    )
    return RenderState(
        world_title=pack.title,
        map_title=world_map.display_name,
        tick=state.tick,
        time_text=time_text,
        tiles=tiles,
        actors=actors,
        interactions=interactions,
        hud_lines=hud_lines,
    )
