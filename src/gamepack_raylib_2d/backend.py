"""Minimal backend protocol, deterministic recorder, and isolated pyray binding."""

from __future__ import annotations

import importlib
from collections import deque
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from gamepack_raylib_2d.types import FontHandle, InputFrame, TextureHandle

Color = tuple[int, int, int, int]


@runtime_checkable
class RaylibBackend(Protocol):
    def open_window(self, width: int, height: int, title: str, *, hidden: bool) -> None: ...

    def close_window(self) -> None: ...

    def should_close(self) -> bool: ...

    def frame_delta(self) -> float: ...

    def poll_input(self) -> InputFrame: ...

    def begin_frame(self) -> None: ...

    def end_frame(self) -> None: ...

    def clear(self, color: Color) -> None: ...

    def load_texture_png(
        self,
        payload: bytes,
        *,
        identity: str,
        width: int,
        height: int,
    ) -> TextureHandle: ...

    def load_font_ttf(
        self,
        payload: bytes,
        *,
        identity: str,
        font_size: int,
    ) -> FontHandle: ...

    def unload_texture(self, handle: TextureHandle) -> None: ...

    def unload_font(self, handle: FontHandle) -> None: ...

    def draw_texture(
        self,
        handle: TextureHandle,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None: ...

    def draw_rectangle(
        self,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        color: Color,
        outline: bool = False,
    ) -> None: ...

    def draw_text(
        self,
        text: str,
        *,
        x: float,
        y: float,
        size: float,
        color: Color,
        font: FontHandle | None,
    ) -> None: ...


class RecordingBackend:
    """In-memory backend used to prove adapter behavior without native graphics."""

    __slots__ = ("_frames", "_opened", "events")

    def __init__(self, frames: Iterable[InputFrame] = ()) -> None:
        self._frames = deque(frames)
        self._opened = False
        self.events: list[tuple[object, ...]] = []

    def open_window(self, width: int, height: int, title: str, *, hidden: bool) -> None:
        if self._opened:
            raise RuntimeError("window is already open")
        self._opened = True
        self.events.append(("open_window", width, height, title, hidden))

    def close_window(self) -> None:
        if self._opened:
            self.events.append(("close_window",))
            self._opened = False

    def should_close(self) -> bool:
        return not self._frames

    def frame_delta(self) -> float:
        return 1.0 / 60.0

    def poll_input(self) -> InputFrame:
        return self._frames.popleft() if self._frames else InputFrame()

    def begin_frame(self) -> None:
        self.events.append(("begin_frame",))

    def end_frame(self) -> None:
        self.events.append(("end_frame",))

    def clear(self, color: Color) -> None:
        self.events.append(("clear", color))

    def load_texture_png(
        self,
        payload: bytes,
        *,
        identity: str,
        width: int,
        height: int,
    ) -> TextureHandle:
        handle = TextureHandle(identity, width, height)
        self.events.append(("load_texture", identity, len(payload), width, height))
        return handle

    def load_font_ttf(
        self,
        payload: bytes,
        *,
        identity: str,
        font_size: int,
    ) -> FontHandle:
        handle = FontHandle(identity, font_size)
        self.events.append(("load_font", identity, len(payload), font_size))
        return handle

    def unload_texture(self, handle: TextureHandle) -> None:
        self.events.append(("unload_texture", handle.identity))

    def unload_font(self, handle: FontHandle) -> None:
        self.events.append(("unload_font", handle.identity))

    def draw_texture(
        self,
        handle: TextureHandle,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        self.events.append(("draw_texture", handle.identity, x, y, width, height))

    def draw_rectangle(
        self,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        color: Color,
        outline: bool = False,
    ) -> None:
        self.events.append(("draw_rectangle", x, y, width, height, color, outline))

    def draw_text(
        self,
        text: str,
        *,
        x: float,
        y: float,
        size: float,
        color: Color,
        font: FontHandle | None,
    ) -> None:
        self.events.append(
            ("draw_text", text, x, y, size, color, None if font is None else font.identity)
        )


class PyrayBackend:
    """Thin pyray owner; importing this module never loads the native binding."""

    __slots__ = ("_opened", "_pr")

    def __init__(self) -> None:
        self._pr = importlib.import_module("pyray")
        self._opened = False

    @property
    def native_module(self) -> object:
        return self._pr

    def open_window(self, width: int, height: int, title: str, *, hidden: bool) -> None:
        pr = self._pr
        if hidden:
            pr.set_config_flags(pr.FLAG_WINDOW_HIDDEN)
        pr.init_window(width, height, title)
        self._opened = True
        pr.set_exit_key(0)
        pr.set_target_fps(60)

    def close_window(self) -> None:
        if self._opened:
            self._pr.close_window()
            self._opened = False

    def should_close(self) -> bool:
        return bool(self._pr.window_should_close())

    def frame_delta(self) -> float:
        return float(self._pr.get_frame_time())

    def poll_input(self) -> InputFrame:
        pr = self._pr
        keys = tuple(
            name
            for name, value in (
                ("LEFT", pr.KEY_LEFT),
                ("RIGHT", pr.KEY_RIGHT),
                ("UP", pr.KEY_UP),
                ("DOWN", pr.KEY_DOWN),
                ("TAB", pr.KEY_TAB),
                ("ENTER", pr.KEY_ENTER),
                ("SPACE", pr.KEY_SPACE),
                ("R", pr.KEY_R),
                ("1", pr.KEY_ONE),
                ("2", pr.KEY_TWO),
            )
            if pr.is_key_pressed(value)
        )
        point = pr.get_mouse_position()
        return InputFrame(
            keys_pressed=keys,
            pointer_pressed=bool(pr.is_mouse_button_pressed(pr.MOUSE_BUTTON_LEFT)),
            pointer_x=float(point.x),
            pointer_y=float(point.y),
        )

    def begin_frame(self) -> None:
        self._pr.begin_drawing()

    def end_frame(self) -> None:
        self._pr.end_drawing()

    def clear(self, color: Color) -> None:
        self._pr.clear_background(self._pr.Color(*color))

    def load_texture_png(
        self,
        payload: bytes,
        *,
        identity: str,
        width: int,
        height: int,
    ) -> TextureHandle:
        pr = self._pr
        image = pr.load_image_from_memory(".png", payload, len(payload))
        try:
            if not pr.is_image_valid(image) or image.width != width or image.height != height:
                raise RuntimeError("decoded PNG dimensions differ from the sealed resource")
            texture = pr.load_texture_from_image(image)
            if not pr.is_texture_valid(texture):
                raise RuntimeError("raylib rejected the sealed PNG texture")
        finally:
            pr.unload_image(image)
        return TextureHandle(identity, width, height, texture)

    def load_font_ttf(
        self,
        payload: bytes,
        *,
        identity: str,
        font_size: int,
    ) -> FontHandle:
        font = self._pr.load_font_from_memory(
            ".ttf",
            payload,
            len(payload),
            font_size,
            None,
            0,
        )
        if not self._pr.is_font_valid(font):
            raise RuntimeError("raylib rejected the sealed TTF font")
        return FontHandle(identity, font_size, font)

    def unload_texture(self, handle: TextureHandle) -> None:
        if handle.native is not None:
            self._pr.unload_texture(handle.native)

    def unload_font(self, handle: FontHandle) -> None:
        if handle.native is not None:
            self._pr.unload_font(handle.native)

    def draw_texture(
        self,
        handle: TextureHandle,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        pr = self._pr
        pr.draw_texture_pro(
            handle.native,
            pr.Rectangle(0.0, 0.0, float(handle.width), float(handle.height)),
            pr.Rectangle(x, y, width, height),
            pr.Vector2(0.0, 0.0),
            0.0,
            pr.WHITE,
        )

    def draw_rectangle(
        self,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        color: Color,
        outline: bool = False,
    ) -> None:
        pr = self._pr
        if outline:
            pr.draw_rectangle_lines_ex(pr.Rectangle(x, y, width, height), 3.0, pr.Color(*color))
        else:
            pr.draw_rectangle_rec(pr.Rectangle(x, y, width, height), pr.Color(*color))

    def draw_text(
        self,
        text: str,
        *,
        x: float,
        y: float,
        size: float,
        color: Color,
        font: FontHandle | None,
    ) -> None:
        pr = self._pr
        if font is None:
            pr.draw_text(text, int(x), int(y), int(size), pr.Color(*color))
            return
        pr.draw_text_ex(
            font.native,
            text,
            pr.Vector2(x, y),
            size,
            max(1.0, size / 12.0),
            pr.Color(*color),
        )


__all__ = [
    "Color",
    "PyrayBackend",
    "RaylibBackend",
    "RecordingBackend",
]
