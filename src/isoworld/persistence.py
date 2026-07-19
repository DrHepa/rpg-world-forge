from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from isoworld.content.models import WorldPack
from isoworld.world.state import (
    ActorState,
    Cooldown,
    GameAction,
    ResourceValue,
    WorldState,
    initial_world_state,
    reduce_world,
)

SAVE_FORMAT = "isoworld.save"
SAVE_VERSION = 1
REPLAY_FORMAT = "isoworld.replay"
REPLAY_VERSION = 1
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
            }
            for actor in state.actors
        ],
        "flags": sorted(state.flags),
        "completed_interactions": sorted(state.completed_interactions),
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
    source = Path(path)
    try:
        if source.stat().st_size > MAX_PERSISTENCE_BYTES:
            raise PersistenceError("The persistence document exceeds the 64 MiB limit")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistenceError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersistenceError("The persistence document must be an object")
    return raw


def _write_object(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _compatible(raw: dict[str, Any], pack: WorldPack, expected_format: str, version: int) -> None:
    if raw.get("format") != expected_format or raw.get("format_version") != version:
        raise PersistenceError(f"Unsupported {expected_format} format or version")
    if raw.get("world_id") != pack.world_id:
        raise PersistenceError("The persistence document belongs to a different world")
    if raw.get("world_content_hash") != pack.content_hash:
        raise PersistenceError("The worldpack changed; migrate or restart this state")


def state_from_dict(raw: dict[str, Any], pack: WorldPack) -> WorldState:
    try:
        actors = tuple(
            ActorState(
                actor_id=item["actor_id"],
                map_id=item["map_id"],
                x=int(item["x"]),
                y=int(item["y"]),
                resources=tuple(
                    ResourceValue(key, int(value))
                    for key, value in sorted(item.get("resources", {}).items())
                ),
                cooldowns=tuple(
                    Cooldown(key, int(value))
                    for key, value in sorted(item.get("cooldowns", {}).items())
                ),
                route=tuple((int(cell[0]), int(cell[1])) for cell in item.get("route", [])),
                blocked_ticks=int(item.get("blocked_ticks", 0)),
            )
            for item in raw["actors"]
        )
        state = WorldState(
            tick=int(raw["tick"]),
            day=int(raw["day"]),
            minute_of_day=int(raw["minute_of_day"]),
            minute_tick=int(raw["minute_tick"]),
            active_actor_id=raw["active_actor_id"],
            actors=actors,
            flags=frozenset(raw.get("flags", [])),
            completed_interactions=frozenset(raw.get("completed_interactions", [])),
            last_message=str(raw.get("last_message", "")),
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PersistenceError(f"Malformed saved state: {exc}") from exc
    _validate_state(state, pack)
    return state


def _validate_state(state: WorldState, pack: WorldPack) -> None:
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
        if actor.map_id not in pack.maps or not pack.is_walkable(actor.map_id, actor.x, actor.y):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid position")
        key = (actor.map_id, actor.x, actor.y)
        if key in occupied:
            raise PersistenceError("Two actors occupy the same cell")
        occupied.add(key)
        if any(item.value < 0 for item in actor.resources):
            raise PersistenceError(f"Actor {actor.actor_id} has a negative resource")
        if any(
            item.ability_id not in pack.abilities or item.ready_at_minute < 0
            for item in actor.cooldowns
        ):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid cooldown")
        if any(not pack.is_walkable(actor.map_id, *cell) for cell in actor.route):
            raise PersistenceError(f"Actor {actor.actor_id} has an invalid route")
    if not state.completed_interactions <= set(pack.interactions):
        raise PersistenceError("Saved interactions do not match the worldpack")
    if not all(isinstance(flag, str) for flag in state.flags):
        raise PersistenceError("Saved flags must be strings")


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
