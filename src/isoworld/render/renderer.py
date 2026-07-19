from __future__ import annotations

from typing import Any

from isoworld.render.iso import screen_to_world, world_to_screen
from isoworld.render.render_state import ActorView, InteractionView, RenderState, TileView
from isoworld.render.resources import RaylibAssetRegistry


class IsometricRenderer:
    def __init__(self, screen_width: int = 1280, screen_height: int = 720) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.tile_width = 64.0
        self.tile_height = 32.0
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.camera_zoom = 1.0
        self.resources: RaylibAssetRegistry | None = None

    def attach_resources(self, resources: RaylibAssetRegistry | None) -> None:
        self.resources = resources

    def _camera(self, pr: Any) -> Any:
        return pr.Camera2D(
            pr.Vector2(self.screen_width * 0.5, 110.0),
            pr.Vector2(self.camera_x, self.camera_y),
            0.0,
            self.camera_zoom,
        )

    def handle_camera_input(self, pr: Any) -> None:
        wheel = pr.get_mouse_wheel_move()
        if wheel:
            self.camera_zoom = max(0.5, min(3.0, self.camera_zoom + wheel * 0.125))
        if pr.is_mouse_button_down(pr.MOUSE_BUTTON_MIDDLE):
            delta = pr.get_mouse_delta()
            self.camera_x -= delta.x / self.camera_zoom
            self.camera_y -= delta.y / self.camera_zoom

    def grid_from_screen(self, pr: Any, position: Any) -> tuple[float, float]:
        projected = pr.get_screen_to_world_2d(position, self._camera(pr))
        return screen_to_world(projected.x, projected.y, self.tile_width, self.tile_height)

    def _draw_text(
        self,
        pr: Any,
        value: str,
        x: float,
        y: float,
        size: float,
        color: Any,
    ) -> None:
        font = self.resources.default_font() if self.resources is not None else None
        if font is None:
            pr.draw_text(value, int(x), int(y), int(size), color)
        else:
            pr.draw_text_ex(font, value, pr.Vector2(x, y), size, 1.0, color)

    def _draw_tile(self, pr: Any, tile: TileView, tick: int) -> None:
        x, y = world_to_screen(
            tile.x,
            tile.y,
            tile.elevation,
            self.tile_width,
            self.tile_height,
        )
        if self.resources is not None:
            binding = self.resources.binding(f"tile_type:{tile.tile_type_id}")
            if binding is not None and self.resources.draw_binding(
                binding,
                anchor_x=x,
                anchor_y=y,
                tick=tick,
            ):
                return
        center = pr.Vector2(x, y)
        color = pr.Color(*tile.color)
        points = (
            pr.Vector2(center.x, center.y - self.tile_height * 0.5),
            pr.Vector2(center.x + self.tile_width * 0.5, center.y),
            pr.Vector2(center.x, center.y + self.tile_height * 0.5),
            pr.Vector2(center.x - self.tile_width * 0.5, center.y),
        )
        pr.draw_triangle(points[0], points[1], points[2], color)
        pr.draw_triangle(points[0], points[2], points[3], color)
        for index in range(4):
            pr.draw_line_v(points[index], points[(index + 1) % 4], pr.Color(35, 30, 48, 255))

    def _draw_interaction(self, pr: Any, item: InteractionView, tick: int) -> None:
        x, y = world_to_screen(item.x, item.y, 0.0, self.tile_width, self.tile_height)
        if self.resources is not None:
            binding = self.resources.binding(f"interaction:{item.interaction_id}")
            if binding is not None and self.resources.draw_binding(
                binding,
                anchor_x=x,
                anchor_y=y,
                tick=tick,
                tint=pr.WHITE if item.available else pr.GRAY,
            ):
                return
        color = pr.GOLD if item.available else pr.DARKGRAY
        pr.draw_rectangle(int(x - 5), int(y - 5), 10, 10, color)

    def _draw_actor(self, pr: Any, actor: ActorView, tick: int) -> None:
        x, y = world_to_screen(actor.x, actor.y, 0.0, self.tile_width, self.tile_height)
        rendered = False
        if self.resources is not None:
            binding = self.resources.binding(f"actor:{actor.actor_id}")
            if binding is not None:
                rendered = self.resources.draw_binding(
                    binding,
                    anchor_x=x,
                    anchor_y=y,
                    tick=tick,
                    moving=bool(actor.route),
                )
        if not rendered:
            fallback_y = y - 18.0
            color = pr.Color(*actor.color)
            pr.draw_circle_v(pr.Vector2(x, fallback_y), 11.0 if actor.active else 8.0, color)
            if actor.active:
                pr.draw_circle_lines(int(x), int(fallback_y), 15.0, pr.RAYWHITE)
        if actor.active:
            pr.draw_circle_lines(int(x), int(y), 5.0, pr.RAYWHITE)
        self._draw_text(pr, actor.display_name, x + 14, y - 28, 14, pr.RAYWHITE)

    def _entity_key(self, kind: str, item: ActorView | InteractionView) -> tuple[Any, ...]:
        slot = f"{kind}:{item.actor_id if kind == 'actor' else item.interaction_id}"
        layer = self.resources.layer_for(slot) if self.resources is not None else 0
        identifier = item.actor_id if kind == "actor" else item.interaction_id
        return (item.x + item.y, layer, item.y, item.x, kind, identifier)

    def draw(self, pr: Any, state: RenderState) -> None:
        pr.clear_background(pr.Color(12, 10, 18, 255))
        pr.begin_mode_2d(self._camera(pr))
        try:
            for tile in sorted(
                state.tiles,
                key=lambda item: (item.x + item.y, item.elevation, item.y, item.x),
            ):
                self._draw_tile(pr, tile, state.tick)

            for actor in state.actors:
                if not actor.active:
                    continue
                for route_x, route_y in actor.route:
                    x, y = world_to_screen(route_x, route_y, 6.0, self.tile_width, self.tile_height)
                    pr.draw_circle(int(x), int(y), 3.0, pr.SKYBLUE)

            entities: list[tuple[str, ActorView | InteractionView]] = [
                *(("interaction", item) for item in state.interactions),
                *(("actor", item) for item in state.actors),
            ]
            for kind, item in sorted(entities, key=lambda value: self._entity_key(*value)):
                if kind == "actor":
                    self._draw_actor(pr, item, state.tick)  # type: ignore[arg-type]
                else:
                    self._draw_interaction(pr, item, state.tick)  # type: ignore[arg-type]
        finally:
            pr.end_mode_2d()

        self._draw_text(pr, state.world_title, 24, 20, 28, pr.RAYWHITE)
        self._draw_text(pr, state.map_title, 24, 54, 18, pr.Color(184, 160, 230, 255))
        self._draw_text(pr, state.time_text, self.screen_width - 230, 24, 20, pr.GOLD)
        hud_y = self.screen_height - (len(state.hud_lines) * 22 + 16)
        for index, line in enumerate(state.hud_lines):
            if line:
                self._draw_text(pr, line, 24, hud_y + index * 22, 17, pr.LIGHTGRAY)

        if state.overlay is not None:
            overlay = state.overlay
            x = 130
            y = 150
            width = self.screen_width - 260
            height = self.screen_height - 300
            pr.draw_rectangle(x, y, width, height, pr.Color(18, 15, 28, 245))
            pr.draw_rectangle_lines(x, y, width, height, pr.Color(194, 137, 255, 255))
            text_x = x + 28
            if self.resources is not None and overlay.speaker_id is not None:
                portrait = self.resources.binding(f"portrait:{overlay.speaker_id}")
                if portrait is not None and self.resources.draw_binding(
                    portrait,
                    anchor_x=x + 92,
                    anchor_y=y + 170,
                    tick=state.tick,
                ):
                    text_x = x + 190
            self._draw_text(pr, overlay.title, text_x, y + 24, 25, pr.RAYWHITE)
            line_y = y + 72
            for line in overlay.lines:
                self._draw_text(pr, line, text_x, line_y, 19, pr.LIGHTGRAY)
                line_y += 26
            line_y += 12
            for choice in overlay.choices:
                self._draw_text(pr, choice, text_x, line_y, 18, pr.GOLD)
                line_y += 25
            self._draw_text(
                pr,
                overlay.help_text,
                text_x,
                y + height - 36,
                16,
                pr.Color(184, 160, 230, 255),
            )
