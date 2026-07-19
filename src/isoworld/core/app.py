from __future__ import annotations

from pathlib import Path

from isoworld.content.models import WorldPack
from isoworld.core.fixed_step import FixedStep
from isoworld.persistence import load_game, save_game
from isoworld.render.iso import screen_to_world
from isoworld.render.render_state import RenderState, build_render_state
from isoworld.render.renderer import IsometricRenderer
from isoworld.world.narrative import available_dialogue_choices
from isoworld.world.simulation import Simulation
from isoworld.world.state import GameAction, WorldState


class GameApp:
    def __init__(
        self,
        pack: WorldPack,
        state: WorldState | None = None,
        quick_save_path: Path | None = None,
    ) -> None:
        self.pack = pack
        self.simulation = Simulation(pack, state)
        initial = build_render_state(self.simulation.state, pack)
        self._render_front: RenderState = initial
        self._render_back: RenderState = initial
        self.clock = FixedStep(tick_rate=20)
        self.renderer = IsometricRenderer()
        self.quick_save_path = quick_save_path
        self._quit_requested = False

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
        state = self.simulation.state
        if state.active_scene_id is not None:
            if key_pressed(pr.KEY_SPACE):  # type: ignore[attr-defined]
                self.simulation.dispatch(GameAction(kind="dismiss_scene"))
                self._sync_render_state()
            return
        if state.dialogue is not None:
            number_keys = (
                pr.KEY_ONE,  # type: ignore[attr-defined]
                pr.KEY_TWO,  # type: ignore[attr-defined]
                pr.KEY_THREE,  # type: ignore[attr-defined]
                pr.KEY_FOUR,  # type: ignore[attr-defined]
                pr.KEY_FIVE,  # type: ignore[attr-defined]
                pr.KEY_SIX,  # type: ignore[attr-defined]
                pr.KEY_SEVEN,  # type: ignore[attr-defined]
                pr.KEY_EIGHT,  # type: ignore[attr-defined]
                pr.KEY_NINE,  # type: ignore[attr-defined]
            )
            choices = available_dialogue_choices(state, self.pack)
            for index, key in enumerate(number_keys):
                if index < len(choices) and key_pressed(key):
                    self.simulation.dispatch(
                        GameAction(kind="choose_dialogue", choice_id=choices[index].id)
                    )
                    self._sync_render_state()
                    return
            if key_pressed(pr.KEY_ESCAPE):  # type: ignore[attr-defined]
                self.simulation.dispatch(GameAction(kind="end_dialogue"))
                self._sync_render_state()
            return
        if key_pressed(pr.KEY_ESCAPE):  # type: ignore[attr-defined]
            self._quit_requested = True
            return
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

        if key_pressed(pr.KEY_E):  # type: ignore[attr-defined]
            self.simulation.dispatch(GameAction(kind="interact"))
            self._sync_render_state()

        if key_pressed(pr.KEY_Q):  # type: ignore[attr-defined]
            self.simulation.dispatch(GameAction(kind="start_dialogue"))
            self._sync_render_state()

        if key_pressed(pr.KEY_ONE):  # type: ignore[attr-defined]
            active_id = self.simulation.state.active_actor_id
            active = self.simulation.state.actor(active_id)
            definition = self.pack.actors[active_id]
            ability_id = definition.ability_ids[0] if definition.ability_ids else None
            target_id = None
            if ability_id is not None and self.pack.abilities[ability_id].target == "actor":
                candidates = [
                    actor
                    for actor in self.simulation.state.actors
                    if actor.actor_id != active_id
                    and actor.map_id == active.map_id
                    and abs(actor.x - active.x) + abs(actor.y - active.y)
                    <= self.pack.abilities[ability_id].range
                ]
                if candidates:
                    target_id = min(
                        candidates,
                        key=lambda actor: (
                            abs(actor.x - active.x) + abs(actor.y - active.y),
                            actor.actor_id,
                        ),
                    ).actor_id
            self.simulation.dispatch(
                GameAction(
                    kind="use_ability",
                    ability_id=ability_id,
                    target_actor_id=target_id,
                )
            )
            self._sync_render_state()

        if pr.is_mouse_button_pressed(pr.MOUSE_BUTTON_LEFT):  # type: ignore[attr-defined]
            mouse = pr.get_mouse_position()  # type: ignore[attr-defined]
            world_x, world_y = screen_to_world(
                mouse.x - self.renderer.screen_width * 0.5,
                mouse.y - 110.0,
                self.renderer.tile_width,
                self.renderer.tile_height,
            )
            active = self.simulation.state.actor(self.simulation.state.active_actor_id)
            self.simulation.dispatch(
                GameAction(
                    kind="navigate",
                    map_id=active.map_id,
                    x=round(world_x),
                    y=round(world_y),
                )
            )
            self._sync_render_state()

        if self.quick_save_path is not None and key_pressed(  # type: ignore[attr-defined]
            pr.KEY_F5  # type: ignore[attr-defined]
        ):
            save_game(self.quick_save_path, self.simulation.state, self.pack)
            self._sync_render_state()

        if (
            self.quick_save_path is not None
            and self.quick_save_path.is_file()
            and key_pressed(pr.KEY_F9)  # type: ignore[attr-defined]
        ):
            self.simulation.restore(load_game(self.quick_save_path, self.pack))
            self._sync_render_state()

    def run(self) -> int:
        try:
            import pyray as pr
        except ImportError as exc:
            raise SystemExit(
                "pyray is not installed. Install the game extra with: pip install -e '.[game]'"
            ) from exc

        renderer = self.renderer
        pr.init_window(renderer.screen_width, renderer.screen_height, self.pack.title)
        pr.set_exit_key(0)
        pr.set_target_fps(60)
        try:
            while not self._quit_requested and not pr.window_should_close():
                self._handle_input(pr)
                self.clock.advance(pr.get_frame_time(), self._update)
                pr.begin_drawing()
                renderer.draw(pr, self._render_front)
                pr.end_drawing()
        finally:
            pr.close_window()
        return 0
