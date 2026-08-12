"""Edge-triggered input projection into semantic, renderer-neutral intents."""

from __future__ import annotations

from gamepack_raylib_2d.types import InputFrame, SemanticIntent

_PUZZLE_BOARD_LEFT = 128.0
_PUZZLE_BOARD_TOP = 216.0
_PUZZLE_CELL_WIDTH = 128.0
_PUZZLE_BOARD_BOTTOM = 344.0
_NARRATIVE_LEFT = 96.0
_NARRATIVE_RIGHT = 704.0
_NARRATIVE_FIRST_TOP = 240.0
_NARRATIVE_CHOICE_HEIGHT = 80.0


class InputRouter:
    __slots__ = ("_adapter_id", "_focus")

    def __init__(self, adapter_id: str) -> None:
        if adapter_id not in {"gamepack_raylib_2d_puzzle", "gamepack_raylib_2d_text"}:
            raise ValueError("unsupported bounded adapter ID")
        self._adapter_id = adapter_id
        self._focus = 0

    def map_frame(self, frame: InputFrame) -> tuple[SemanticIntent, ...]:
        if type(frame) is not InputFrame:
            raise ValueError("input frame must be an exact InputFrame")
        if self._adapter_id == "gamepack_raylib_2d_puzzle":
            return self._puzzle(frame)
        return self._narrative(frame)

    def _puzzle(self, frame: InputFrame) -> tuple[SemanticIntent, ...]:
        intents: list[SemanticIntent] = []
        for key in frame.keys_pressed:
            if key in {"RIGHT", "TAB"}:
                self._focus = (self._focus + 1) % 3
                intents.append(SemanticIntent("focus_cell", self._focus, False))
            elif key == "LEFT":
                self._focus = (self._focus - 1) % 3
                intents.append(SemanticIntent("focus_cell", self._focus, False))
            elif key in {"ENTER", "SPACE"}:
                intents.append(SemanticIntent("select_cell", self._focus))
            elif key == "R":
                intents.append(SemanticIntent("restart"))
        if (
            frame.pointer_pressed
            and _PUZZLE_BOARD_LEFT <= frame.pointer_x < _PUZZLE_BOARD_LEFT + 3 * _PUZZLE_CELL_WIDTH
            and _PUZZLE_BOARD_TOP <= frame.pointer_y < _PUZZLE_BOARD_BOTTOM
        ):
            self._focus = int((frame.pointer_x - _PUZZLE_BOARD_LEFT) // _PUZZLE_CELL_WIDTH)
            intents.append(SemanticIntent("select_cell", self._focus))
        return tuple(intents)

    def _narrative(self, frame: InputFrame) -> tuple[SemanticIntent, ...]:
        intents: list[SemanticIntent] = []
        for key in frame.keys_pressed:
            if key in {"DOWN", "TAB"}:
                self._focus = (self._focus + 1) % 2
                intents.append(SemanticIntent("focus_choice", self._focus, False))
            elif key == "UP":
                self._focus = (self._focus - 1) % 2
                intents.append(SemanticIntent("focus_choice", self._focus, False))
            elif key in {"ENTER", "SPACE"}:
                intents.append(SemanticIntent("choose", self._focus))
            elif key in {"1", "2"}:
                self._focus = int(key) - 1
                intents.append(SemanticIntent("choose", self._focus))
        if (
            frame.pointer_pressed
            and _NARRATIVE_LEFT <= frame.pointer_x < _NARRATIVE_RIGHT
            and _NARRATIVE_FIRST_TOP
            <= frame.pointer_y
            < _NARRATIVE_FIRST_TOP + 2 * _NARRATIVE_CHOICE_HEIGHT
        ):
            self._focus = int((frame.pointer_y - _NARRATIVE_FIRST_TOP) // _NARRATIVE_CHOICE_HEIGHT)
            intents.append(SemanticIntent("choose", self._focus))
        return tuple(intents)


__all__ = ["InputFrame", "InputRouter"]
