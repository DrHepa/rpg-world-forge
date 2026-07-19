from __future__ import annotations

from dataclasses import dataclass

from isoworld.content.models import Color, WorldPack
from isoworld.world.navigation import Cell
from isoworld.world.state import WorldState


@dataclass(frozen=True, slots=True)
class TileView:
    tile_type_id: str
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
class EventView:
    kind: str
    actor_id: str | None
    subject_id: str | None


@dataclass(frozen=True, slots=True)
class OverlayView:
    title: str
    lines: tuple[str, ...]
    choices: tuple[str, ...]
    help_text: str
    speaker_id: str | None = None


@dataclass(frozen=True, slots=True)
class RenderState:
    revision: int
    world_title: str
    map_id: str
    map_title: str
    tick: int
    time_text: str
    tiles: tuple[TileView, ...]
    actors: tuple[ActorView, ...]
    interactions: tuple[InteractionView, ...]
    events: tuple[EventView, ...]
    hud_lines: tuple[str, ...]
    overlay: OverlayView | None


def _wrap_text(value: str, width: int = 72) -> tuple[str, ...]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return tuple(lines)


def build_render_state(state: WorldState, pack: WorldPack, *, revision: int = 0) -> RenderState:
    active = state.actor(state.active_actor_id)
    world_map = pack.maps[active.map_id]
    tiles = tuple(
        TileView(
            tile_type_id=world_map.tile_id_at(x, y),
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
    active_quests = []
    for progress in state.quests:
        if progress.status != "active" or progress.stage_id is None:
            continue
        quest = pack.quests[progress.quest_id]
        active_quests.append(
            f"{pack.ui['quest_label']}: {quest.title} — "
            f"{quest.stages[progress.stage_id].description}"
        )
    if active_quests:
        hud_lines = hud_lines[:3] + tuple(active_quests[:2]) + hud_lines[3:]

    overlay = None
    if state.active_scene_id is not None:
        scene = pack.scenes[state.active_scene_id]
        overlay = OverlayView(
            title=scene.title,
            lines=_wrap_text(scene.text),
            choices=(),
            help_text=pack.ui["scene_help"],
            speaker_id=None,
        )
    elif state.dialogue is not None:
        from isoworld.world.narrative import available_dialogue_choices

        dialogue = pack.dialogues[state.dialogue.dialogue_id]
        node = dialogue.nodes[state.dialogue.node_id]
        speaker = pack.actors[node.speaker_id].display_name
        choices = available_dialogue_choices(state, pack)
        overlay = OverlayView(
            title=f"{dialogue.display_name} — {speaker}",
            lines=_wrap_text(node.text),
            choices=tuple(f"{index}. {choice.text}" for index, choice in enumerate(choices, 1)),
            help_text=pack.ui["dialogue_help"],
            speaker_id=node.speaker_id,
        )
    return RenderState(
        revision=revision,
        world_title=pack.title,
        map_id=world_map.id,
        map_title=world_map.display_name,
        tick=state.tick,
        time_text=time_text,
        tiles=tiles,
        actors=actors,
        interactions=interactions,
        events=tuple(
            EventView(event.kind, event.actor_id, event.subject_id) for event in state.recent_events
        ),
        hud_lines=hud_lines,
        overlay=overlay,
    )
