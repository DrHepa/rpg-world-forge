from __future__ import annotations

from isoworld.content.models import WorldPack
from isoworld.core.fixed_step import FixedStep
from isoworld.render.render_state import RenderState, build_render_state
from isoworld.render.renderer import IsometricRenderer
from isoworld.world.simulation import Simulation
from isoworld.world.state import GameAction, WorldState


class GameApp:
    def __init__(self, pack: WorldPack) -> None:
        self.pack = pack
        self.simulation = Simulation(pack)
        initial = build_render_state(self.simulation.state, pack)
        self._render_front: RenderState = initial
        self._render_back: RenderState = initial
        self.clock = FixedStep(tick_rate=20)

    def _sync_render_state(self) -> None:
        self._render_back = build_render_state(self.simulation.state, self.pack)
        self._render_front, self._render_back = self._render_back, self._render_front

    def _update(self, _dt: float) -> None:
        self.simulation.tick()
        self._sync_render_state()

    def run_headless(self, ticks: int) -> WorldState:
        for _ in range(max(ticks, 0)):
            self._update(self.clock.dt)
        return self.simulation.state

    def _handle_input(self, pr: object) -> None:
        key_pressed = pr.is_key_pressed  # type: ignore[attr-defined]
        movement = (
            ((pr.KEY_LEFT, pr.KEY_A), -1, 0),  # type: ignore[attr-defined]
            ((pr.KEY_RIGHT, pr.KEY_D), 1, 0),  # type: ignore[attr-defined]
            ((pr.KEY_UP, pr.KEY_W), 0, -1),  # type: ignore[attr-defined]
            ((pr.KEY_DOWN, pr.KEY_S), 0, 1),  # type: ignore[attr-defined]
        )
        for keys, dx, dy in movement:
            if any(key_pressed(key) for key in keys):
                self.simulation.dispatch(GameAction(kind="move", dx=dx, dy=dy))
                self._sync_render_state()

        if key_pressed(pr.KEY_TAB):  # type: ignore[attr-defined]
            playable = self.pack.playable_actor_ids
            current = playable.index(self.simulation.state.active_actor_id)
            selected = playable[(current + 1) % len(playable)]
            self.simulation.dispatch(GameAction(kind="select_actor", actor_id=selected))
            self._sync_render_state()

    def run(self) -> int:
        try:
            import pyray as pr
        except ImportError as exc:
            raise SystemExit(
                "pyray is not installed. Install the game extra with: pip install -e '.[game]'"
            ) from exc

        renderer = IsometricRenderer()
        pr.init_window(renderer.screen_width, renderer.screen_height, self.pack.title)
        pr.set_target_fps(60)
        try:
            while not pr.window_should_close():
                self._handle_input(pr)
                self.clock.advance(pr.get_frame_time(), self._update)
                pr.begin_drawing()
                renderer.draw(pr, self._render_front)
                pr.end_drawing()
        finally:
            pr.close_window()
        return 0
