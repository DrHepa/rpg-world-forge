from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from isoworld.core.app import GameApp
from isoworld.render.iso import screen_to_world, world_to_screen
from isoworld.render.render_state import build_render_state
from isoworld.world.state import GameAction, initial_world_state, reduce_world

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "content/compiled/foundation.worldpack.json"


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_worldpack(PACK_PATH)

    def test_isometric_projection_round_trip(self) -> None:
        for point in ((0.0, 0.0), (3.0, 7.0), (-2.5, 4.25)):
            screen = world_to_screen(*point)
            world = screen_to_world(*screen)
            self.assertAlmostEqual(point[0], world[0])
            self.assertAlmostEqual(point[1], world[1])

    def test_non_walkable_tile_blocks_movement(self) -> None:
        state = initial_world_state(self.pack)
        blocked = reduce_world(state, GameAction(kind="move", dx=1), self.pack)
        self.assertEqual((1, 1), (blocked.actor("explorer").x, blocked.actor("explorer").y))
        moved = reduce_world(state, GameAction(kind="move", dx=-1), self.pack)
        self.assertEqual((0, 1), (moved.actor("explorer").x, moved.actor("explorer").y))

    def test_switches_between_pack_defined_playable_actors(self) -> None:
        state = initial_world_state(self.pack)
        selected = reduce_world(
            state,
            GameAction(kind="select_actor", actor_id="maker"),
            self.pack,
        )
        self.assertEqual("maker", selected.active_actor_id)

    def test_render_state_is_frozen_snapshot(self) -> None:
        snapshot = build_render_state(initial_world_state(self.pack), self.pack)
        with self.assertRaises(FrozenInstanceError):
            snapshot.tick = 100  # type: ignore[misc]

    def test_headless_ticks_are_deterministic(self) -> None:
        first = GameApp(self.pack).run_headless(25)
        second = GameApp(self.pack).run_headless(25)
        self.assertEqual(first, second)
        self.assertEqual(25, first.tick)

    def test_quick_load_is_disabled_while_recording_replay(self) -> None:
        class ReplayInput:
            def __getattr__(self, name: str) -> str:
                return name

            def is_key_pressed(self, key: str) -> bool:
                return key == "KEY_F9"

            def is_mouse_button_pressed(self, _key: str) -> bool:
                return False

        with tempfile.TemporaryDirectory() as directory:
            quick_save = Path(directory) / "quick-save.json"
            quick_save.write_text("placeholder", encoding="utf-8")
            app = GameApp(
                self.pack,
                quick_save_path=quick_save,
                replay_recording=True,
            )
            state = app.run_headless(1)

            with patch("isoworld.core.app.load_game") as load:
                app._handle_input(ReplayInput())

        load.assert_not_called()
        self.assertIs(state, app.simulation.state)
        self.assertEqual(1, app.simulation.state.tick)


if __name__ == "__main__":
    unittest.main()
