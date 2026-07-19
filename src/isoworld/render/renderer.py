from __future__ import annotations

from typing import Any

from isoworld.render.iso import world_to_screen
from isoworld.render.render_state import RenderState


class IsometricRenderer:
    def __init__(self, screen_width: int = 1280, screen_height: int = 720) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.tile_width = 64.0
        self.tile_height = 32.0

    def draw(self, pr: Any, state: RenderState) -> None:
        pr.clear_background(pr.Color(12, 10, 18, 255))
        origin_x = self.screen_width * 0.5
        origin_y = 110.0

        for tile in state.tiles:
            x, y = world_to_screen(
                tile.x,
                tile.y,
                tile.elevation,
                self.tile_width,
                self.tile_height,
            )
            center = pr.Vector2(origin_x + x, origin_y + y)
            color = pr.Color(*tile.color)
            points = (
                pr.Vector2(center.x, center.y - self.tile_height * 0.5),
                pr.Vector2(center.x + self.tile_width * 0.5, center.y),
                pr.Vector2(center.x, center.y + self.tile_height * 0.5),
                pr.Vector2(center.x - self.tile_width * 0.5, center.y),
            )
            for index in range(4):
                pr.draw_triangle(points[0], points[index], points[(index + 1) % 4], color)
            for index in range(4):
                pr.draw_line_v(points[index], points[(index + 1) % 4], pr.Color(35, 30, 48, 255))

        for interaction in state.interactions:
            x, y = world_to_screen(
                interaction.x,
                interaction.y,
                10.0,
                self.tile_width,
                self.tile_height,
            )
            position = pr.Vector2(origin_x + x, origin_y + y)
            color = pr.GOLD if interaction.available else pr.DARKGRAY
            pr.draw_rectangle(int(position.x - 5), int(position.y - 5), 10, 10, color)

        for actor in state.actors:
            if not actor.active:
                continue
            for route_x, route_y in actor.route:
                x, y = world_to_screen(route_x, route_y, 6.0, self.tile_width, self.tile_height)
                pr.draw_circle(int(origin_x + x), int(origin_y + y), 3.0, pr.SKYBLUE)

        for actor in state.actors:
            x, y = world_to_screen(actor.x, actor.y, 18.0, self.tile_width, self.tile_height)
            position = pr.Vector2(origin_x + x, origin_y + y)
            color = pr.Color(*actor.color)
            pr.draw_circle_v(position, 11.0 if actor.active else 8.0, color)
            if actor.active:
                pr.draw_circle_lines(int(position.x), int(position.y), 15.0, pr.RAYWHITE)
            pr.draw_text(
                actor.display_name, int(position.x + 14), int(position.y - 8), 14, pr.RAYWHITE
            )

        pr.draw_text(state.world_title, 24, 20, 28, pr.RAYWHITE)
        pr.draw_text(state.map_title, 24, 54, 18, pr.Color(184, 160, 230, 255))
        pr.draw_text(state.time_text, self.screen_width - 230, 24, 20, pr.GOLD)
        for index, line in enumerate(state.hud_lines):
            if line:
                pr.draw_text(line, 24, self.screen_height - 104 + index * 22, 17, pr.LIGHTGRAY)
