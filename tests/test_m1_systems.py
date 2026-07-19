from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.persistence import (
    PersistenceError,
    load_game,
    load_replay,
    save_game,
    state_digest,
    write_replay,
)
from isoworld.world.navigation import find_path
from isoworld.world.simulation import Simulation
from isoworld.world.state import GameAction, initial_world_state, reduce_world

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "content/compiled/foundation.worldpack.json"


class M1SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_worldpack(PACK_PATH)

    def test_astar_routes_around_non_walkable_terrain(self) -> None:
        route = find_path(self.pack, "test_garden", (1, 1), (4, 1))
        self.assertTrue(route)
        self.assertEqual((4, 1), route[-1])
        self.assertNotIn((2, 1), route)
        self.assertEqual(route, find_path(self.pack, "test_garden", (1, 1), (4, 1)))

    def test_navigation_action_advances_route_on_fixed_intervals(self) -> None:
        simulation = Simulation(self.pack)
        simulation.dispatch(GameAction(kind="navigate", x=4, y=1, map_id="test_garden"))
        self.assertTrue(simulation.state.actor("explorer").route)
        for _ in range(40):
            simulation.tick()
        explorer = simulation.state.actor("explorer")
        self.assertEqual((4, 1), (explorer.x, explorer.y))
        self.assertEqual((), explorer.route)

    def test_cell_reservation_prevents_two_actors_entering_same_cell(self) -> None:
        state = initial_world_state(self.pack)
        actors = []
        for actor in state.actors:
            if actor.actor_id == "explorer":
                actor = replace(actor, x=4, y=4, route=((4, 5),))
            elif actor.actor_id == "maker":
                actor = replace(actor, x=4, y=6, route=((4, 5),))
            elif actor.actor_id == "guide":
                actor = replace(actor, x=9, y=9)
            actors.append(actor)
        state = replace(state, tick=3, actors=tuple(actors))
        result = reduce_world(state, GameAction(kind="tick"), self.pack)
        cells = [(actor.x, actor.y) for actor in result.actors]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual((4, 5), (result.actor("explorer").x, result.actor("explorer").y))
        self.assertEqual((4, 6), (result.actor("maker").x, result.actor("maker").y))

    def test_clock_advances_in_world_minutes(self) -> None:
        simulation = Simulation(self.pack)
        for _ in range(self.pack.clock.ticks_per_minute):
            simulation.tick()
        self.assertEqual(481, simulation.state.minute_of_day)
        self.assertEqual(0, simulation.state.minute_tick)

    def test_schedule_moves_non_playable_actor_to_destination(self) -> None:
        simulation = Simulation(self.pack)
        for _ in range(8):
            simulation.tick()
        guide = simulation.state.actor("guide")
        self.assertEqual((7, 5), (guide.x, guide.y))

    def test_schedule_uses_fallback_when_primary_cell_is_occupied(self) -> None:
        state = initial_world_state(self.pack)
        actors = tuple(
            replace(actor, x=7, y=5) if actor.actor_id == "maker" else actor
            for actor in state.actors
        )
        state = replace(state, actors=actors)
        for _ in range(4):
            state = reduce_world(state, GameAction(kind="tick"), self.pack)
        guide = state.actor("guide")
        self.assertEqual((6, 5), (guide.x, guide.y))

    def test_contextual_interaction_applies_once(self) -> None:
        state = initial_world_state(self.pack)
        result = reduce_world(state, GameAction(kind="interact"), self.pack)
        self.assertEqual(5, result.actor("explorer").resource("energy"))
        self.assertIn("stone_awakened", result.flags)
        self.assertIn("resonant_stone", result.completed_interactions)
        repeated = reduce_world(result, GameAction(kind="interact"), self.pack)
        self.assertEqual(5, repeated.actor("explorer").resource("energy"))

    def test_ability_spends_resources_sets_cooldown_and_effect(self) -> None:
        state = initial_world_state(self.pack)
        used = reduce_world(
            state,
            GameAction(kind="use_ability", ability_id="focus"),
            self.pack,
        )
        self.assertEqual(2, used.actor("explorer").resource("energy"))
        self.assertIn("focused", used.flags)
        self.assertEqual(510, used.actor("explorer").cooldown_until("focus"))
        blocked = reduce_world(
            used,
            GameAction(kind="use_ability", ability_id="focus"),
            self.pack,
        )
        self.assertEqual(2, blocked.actor("explorer").resource("energy"))
        self.assertEqual("Ability is cooling down", blocked.last_message)

    def test_save_round_trip_is_versioned_and_exact(self) -> None:
        simulation = Simulation(self.pack)
        simulation.dispatch(GameAction(kind="interact"))
        for _ in range(11):
            simulation.tick()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            save_game(path, simulation.state, self.pack)
            loaded = load_game(path, self.pack)
        self.assertEqual(simulation.state, loaded)
        self.assertEqual(state_digest(simulation.state), state_digest(loaded))

    def test_tampered_save_is_rejected(self) -> None:
        state = initial_world_state(self.pack)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            save_game(path, state, self.pack)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["state"]["tick"] = 99
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(PersistenceError):
                load_game(path, self.pack)

    def test_action_replay_reproduces_final_state(self) -> None:
        simulation = Simulation(self.pack)
        simulation.dispatch(GameAction(kind="interact"))
        simulation.dispatch(GameAction(kind="navigate", x=4, y=1))
        for _ in range(20):
            simulation.tick()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.replay.json"
            write_replay(path, simulation.action_log, simulation.state, self.pack)
            actions, replayed = load_replay(path, self.pack)
        self.assertEqual(len(simulation.action_log), len(actions))
        self.assertEqual(simulation.state, replayed)

    def test_replay_with_unknown_actor_is_rejected_cleanly(self) -> None:
        state = initial_world_state(self.pack)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.replay.json"
            write_replay(
                path,
                [GameAction(kind="move", actor_id="missing_actor", dx=1)],
                state,
                self.pack,
            )
            with self.assertRaises(PersistenceError):
                load_replay(path, self.pack)

    def test_tampered_worldpack_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            raw = json.loads(PACK_PATH.read_text(encoding="utf-8"))
            raw["world"]["title"] = "Tampered"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(WorldPackError):
                load_worldpack(path)

    def test_rehashed_worldpack_with_unsafe_clock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            raw = json.loads(PACK_PATH.read_text(encoding="utf-8"))
            raw["world"]["simulation"]["ticks_per_minute"] = 0
            raw.pop("content_hash")
            canonical = json.dumps(
                raw,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            raw["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(WorldPackError):
                load_worldpack(path)


if __name__ == "__main__":
    unittest.main()
