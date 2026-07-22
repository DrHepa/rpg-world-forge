from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from isoworld.core.app import GameApp
from isoworld.render.iso import screen_to_world, world_to_screen
from isoworld.render.render_state import build_render_state
from isoworld.render.resources import ResourceError
from isoworld.world.state import GameAction, initial_world_state, reduce_world

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "content/compiled/foundation.worldpack.json"


class _LifecyclePyray:
    def __init__(
        self,
        events: list[str],
        *,
        window_error: BaseException | None = None,
        close_error: BaseException | None = None,
        exit_key_error: BaseException | None = None,
        target_fps_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.window_error = window_error
        self.close_error = close_error
        self.exit_key_error = exit_key_error
        self.target_fps_error = target_fps_error

    def init_window(self, width: int, height: int, title: str) -> None:
        self.events.append("init-window")

    def set_exit_key(self, key: int) -> None:
        self.events.append("set-exit-key")
        if self.exit_key_error is not None:
            raise self.exit_key_error

    def set_target_fps(self, fps: int) -> None:
        self.events.append("set-target-fps")
        if self.target_fps_error is not None:
            raise self.target_fps_error

    def window_should_close(self) -> bool:
        self.events.append("window-check")
        if self.window_error is not None:
            raise self.window_error
        return True

    def close_window(self) -> None:
        self.events.append("close-window")
        if self.close_error is not None:
            raise self.close_error


class _LifecycleRenderer:
    screen_width = 320
    screen_height = 180

    def __init__(
        self,
        events: list[str],
        *,
        detach_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.detach_error = detach_error

    def attach_resources(self, resources: object | None) -> None:
        if resources is None:
            self.events.append("detach-renderer")
            if self.detach_error is not None:
                raise self.detach_error
        else:
            self.events.append("attach-renderer")


class _LifecycleRegistry:
    def __init__(
        self,
        events: list[str],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.close_error = close_error

    def load(self) -> None:
        self.events.append("load-registry")

    def close(self) -> None:
        self.events.append("close-registry")
        if self.close_error is not None:
            raise self.close_error

    def sync_audio(self, state: object) -> None:
        pass


class _BorrowedRenderPack:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


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

    def test_window_and_renderer_cleanup_continue_after_registry_close_failure(self) -> None:
        events: list[str] = []
        pyray = _LifecyclePyray(events)
        renderer = _LifecycleRenderer(events)
        registry = _LifecycleRegistry(
            events,
            close_error=ResourceError("native unload failed"),
        )
        renderpack = _BorrowedRenderPack()
        app = GameApp(self.pack, renderpack=renderpack)  # type: ignore[arg-type]
        app.renderer = renderer  # type: ignore[assignment]

        with (
            patch.dict(sys.modules, {"pyray": pyray}),
            patch("isoworld.core.app.RaylibAssetRegistry", return_value=registry),
            self.assertRaisesRegex(ResourceError, "native unload failed"),
        ):
            app.run()

        self.assertEqual(
            ["close-registry", "detach-renderer", "close-window"],
            events[-3:],
        )
        self.assertEqual(0, renderpack.close_calls)

    def test_exit_key_setup_failure_still_detaches_renderer_and_closes_window(self) -> None:
        events: list[str] = []
        primary = RuntimeError("exit-key setup failed")
        pyray = _LifecyclePyray(events, exit_key_error=primary)
        renderer = _LifecycleRenderer(events)
        app = GameApp(self.pack)
        app.renderer = renderer  # type: ignore[assignment]

        with (
            patch.dict(sys.modules, {"pyray": pyray}),
            self.assertRaises(RuntimeError) as caught,
        ):
            app.run()

        self.assertIs(primary, caught.exception)
        self.assertEqual(
            ["init-window", "set-exit-key", "detach-renderer", "close-window"],
            events,
        )

    def test_target_fps_setup_failure_keeps_primary_and_aggregates_teardown(self) -> None:
        events: list[str] = []
        primary = RuntimeError("target-fps setup failed")
        pyray = _LifecyclePyray(
            events,
            target_fps_error=primary,
            close_error=RuntimeError("window cleanup failed"),
        )
        renderer = _LifecycleRenderer(
            events,
            detach_error=RuntimeError("detach failed"),
        )
        app = GameApp(self.pack)
        app.renderer = renderer  # type: ignore[assignment]

        with (
            patch.dict(sys.modules, {"pyray": pyray}),
            self.assertRaises(RuntimeError) as caught,
        ):
            app.run()

        self.assertIs(primary, caught.exception)
        cleanup = caught.exception.__cause__
        self.assertIsInstance(cleanup, ResourceError)
        assert cleanup is not None
        self.assertIn("detach failed", str(cleanup))
        self.assertIn("window cleanup failed", str(cleanup))
        self.assertEqual(
            [
                "init-window",
                "set-exit-key",
                "set-target-fps",
                "detach-renderer",
                "close-window",
            ],
            events,
        )

    def test_primary_runtime_error_survives_aggregated_cleanup_failures(self) -> None:
        events: list[str] = []
        primary = ResourceError("frame failed")
        pyray = _LifecyclePyray(
            events,
            window_error=primary,
            close_error=RuntimeError("window cleanup failed"),
        )
        renderer = _LifecycleRenderer(
            events,
            detach_error=RuntimeError("detach failed"),
        )
        registry = _LifecycleRegistry(
            events,
            close_error=ResourceError("unload failed"),
        )
        renderpack = _BorrowedRenderPack()
        app = GameApp(self.pack, renderpack=renderpack)  # type: ignore[arg-type]
        app.renderer = renderer  # type: ignore[assignment]

        with (
            patch.dict(sys.modules, {"pyray": pyray}),
            patch("isoworld.core.app.RaylibAssetRegistry", return_value=registry),
            self.assertRaises(ResourceError) as caught,
        ):
            app.run()

        self.assertIs(primary, caught.exception)
        cleanup = caught.exception.__cause__
        self.assertIsInstance(cleanup, ResourceError)
        assert cleanup is not None
        self.assertIn("unload failed", str(cleanup))
        self.assertIn("detach failed", str(cleanup))
        self.assertIn("window cleanup failed", str(cleanup))
        self.assertEqual(
            ["window-check", "close-registry", "detach-renderer", "close-window"],
            events[-4:],
        )
        self.assertEqual(0, renderpack.close_calls)


if __name__ == "__main__":
    unittest.main()
