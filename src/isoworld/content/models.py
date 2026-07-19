from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Color = tuple[int, int, int, int]


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
class Spawn:
    map_id: str
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ActorDefinition:
    id: str
    display_name: str
    playable: bool
    spawn: Spawn
    color: Color
    personal_arc_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorldPack:
    world_id: str
    title: str
    language: str
    start_map_id: str
    content_hash: str
    ui: dict[str, str]
    tile_types: dict[str, TileType]
    maps: dict[str, MapDefinition]
    actors: dict[str, ActorDefinition]
    collections: dict[str, tuple[dict[str, Any], ...]]

    @property
    def playable_actor_ids(self) -> tuple[str, ...]:
        return tuple(actor.id for actor in self.actors.values() if actor.playable)
