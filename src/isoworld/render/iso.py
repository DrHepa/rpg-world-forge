from __future__ import annotations


def world_to_screen(
    x: float,
    y: float,
    z: float = 0.0,
    tile_width: float = 64.0,
    tile_height: float = 32.0,
) -> tuple[float, float]:
    return (
        (x - y) * tile_width * 0.5,
        (x + y) * tile_height * 0.5 - z,
    )


def screen_to_world(
    screen_x: float,
    screen_y: float,
    tile_width: float = 64.0,
    tile_height: float = 32.0,
) -> tuple[float, float]:
    x = screen_x / tile_width + screen_y / tile_height
    y = screen_y / tile_height - screen_x / tile_width
    return x, y
