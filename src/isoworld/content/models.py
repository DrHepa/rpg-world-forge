from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Color = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ClockDefinition:
    start_day: int = 1
    start_minute: int = 8 * 60
    ticks_per_minute: int = 20
    movement_interval_ticks: int = 4


@dataclass(frozen=True, slots=True)
class TileType:
    id: str
    display_name: str
    color: Color
    walkable: bool
    arable: bool
    height: int = 0


@dataclass(frozen=True, slots=True)
class MapDefinition:
    id: str
    display_name: str
    width: int
    height: int
    rows: tuple[str, ...]
    legend: dict[str, str]

    def tile_id_at(self, x: int, y: int) -> str:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise IndexError((x, y))
        return self.legend[self.rows[y][x]]


@dataclass(frozen=True, slots=True)
class Location:
    map_id: str
    x: int
    y: int


Spawn = Location


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    kind: str
    target: str = "self"
    resource: str | None = None
    amount: int = 0
    flag: str | None = None


@dataclass(frozen=True, slots=True)
class AbilityDefinition:
    id: str
    display_name: str
    target: str
    range: int
    costs: dict[str, int]
    cooldown_minutes: int
    effects: tuple[EffectDefinition, ...]


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    start_minute: int
    end_minute: int
    activity: str
    destinations: tuple[Location, ...]

    def contains(self, minute: int) -> bool:
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    id: str
    entries: tuple[ScheduleEntry, ...]

    def entry_at(self, minute: int) -> ScheduleEntry | None:
        return next((entry for entry in self.entries if entry.contains(minute)), None)


@dataclass(frozen=True, slots=True)
class InteractionDefinition:
    id: str
    display_name: str
    prompt: str
    location: Location
    range: int
    required_flags: frozenset[str]
    forbidden_flags: frozenset[str]
    repeatable: bool
    effects: tuple[EffectDefinition, ...]


@dataclass(frozen=True, slots=True)
class ActorDefinition:
    id: str
    display_name: str
    playable: bool
    spawn: Spawn
    color: Color
    personal_arc_id: str | None = None
    schedule_id: str | None = None
    schedule_mode: str = "never"
    ability_ids: tuple[str, ...] = ()
    resources: tuple[tuple[str, int], ...] = ()

    @property
    def initial_resources(self) -> dict[str, int]:
        return dict(self.resources)


@dataclass(frozen=True, slots=True)
class WorldPack:
    format_version: int
    world_id: str
    title: str
    language: str
    start_map_id: str
    content_hash: str
    ui: dict[str, str]
    clock: ClockDefinition
    tile_types: dict[str, TileType]
    maps: dict[str, MapDefinition]
    actors: dict[str, ActorDefinition]
    abilities: dict[str, AbilityDefinition]
    schedules: dict[str, ScheduleDefinition]
    interactions: dict[str, InteractionDefinition]
    collections: dict[str, tuple[dict[str, Any], ...]]

    @property
    def playable_actor_ids(self) -> tuple[str, ...]:
        return tuple(actor.id for actor in self.actors.values() if actor.playable)

    def is_walkable(self, map_id: str, x: int, y: int) -> bool:
        world_map = self.maps[map_id]
        if x < 0 or y < 0 or x >= world_map.width or y >= world_map.height:
            return False
        return self.tile_types[world_map.tile_id_at(x, y)].walkable
