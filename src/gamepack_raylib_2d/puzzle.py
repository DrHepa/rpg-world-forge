"""Bounded symbolic-board controller and color-independent puzzle rendering."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from gamepack_raylib_2d.backend import RaylibBackend
from gamepack_raylib_2d.executable_shape import inspect_adapter_executable_shape
from gamepack_raylib_2d.resources import ResourceManager
from gamepack_raylib_2d.types import SemanticIntent, TextureHandle
from gamepack_runtime import GameSession, TransitionResult

_BOARD_X = 128.0
_BOARD_Y = 216.0
_CELL_SIZE = 128.0
_WHITE = (245, 245, 245, 255)
_BLACK = (18, 18, 22, 255)
_FOCUS = (255, 216, 64, 255)
_SELECTED = (68, 214, 255, 255)
_SUCCESS = (130, 255, 160, 255)
_ERROR = (255, 128, 112, 255)


class PuzzleController:
    __slots__ = (
        "_accepted",
        "_feedback",
        "_focus",
        "_intents",
        "_max_actions",
        "_selected",
        "session",
    )

    def __init__(self, gamepack: Mapping[str, object], *, max_actions: int) -> None:
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError("puzzle max_actions must be a positive exact integer")
        inspect_adapter_executable_shape(gamepack, "gamepack_raylib_2d_puzzle")
        self.session = GameSession(gamepack)
        self._intents: deque[SemanticIntent] = deque()
        self._max_actions = max_actions
        self._focus = 0
        self._selected: int | None = None
        self._feedback = "Select two adjacent cells."
        self._accepted: list[TransitionResult] = []

    @property
    def selected_cell(self) -> int | None:
        return self._selected

    @property
    def accepted_results(self) -> list[TransitionResult]:
        return list(self._accepted)

    def queue_intent(self, intent: SemanticIntent) -> None:
        if type(intent) is not SemanticIntent:
            raise ValueError("puzzle intent must be an exact SemanticIntent")
        if len(self._intents) >= 128:
            raise ValueError("puzzle intent queue exceeds its bound")
        self._intents.append(intent)

    def step(self) -> TransitionResult | None:
        if not self._intents:
            return None
        intent = self._intents.popleft()
        if intent.kind in {"focus_cell", "focus_next"}:
            self._focus = (
                (self._focus + 1) % 3 if intent.value is None else max(0, min(2, intent.value))
            )
            return None
        if intent.kind == "select_cell":
            if intent.value is None or not 0 <= intent.value <= 2:
                raise ValueError("puzzle cell intent is outside the board")
            self._focus = intent.value
            if self._selected is None:
                self._selected = intent.value
                self._feedback = f"Cell {intent.value + 1} selected."
                return None
            first = self._selected
            self._require_action_budget()
            self._selected = None
            result = self.session.apply(
                "swap_tiles",
                {"first_index": first, "second_index": intent.value},
            )
        elif intent.kind == "restart":
            self._require_action_budget()
            self._selected = None
            result = self.session.apply("restart_board", {})
        else:
            raise ValueError(f"unsupported puzzle intent: {intent.kind}")
        if result.accepted:
            self._accepted.append(result)
            if self.session.classification.terminal:
                self._feedback = "Puzzle complete."
            elif intent.kind == "restart":
                self._feedback = "Board restarted."
            else:
                self._feedback = "Move accepted."
        else:
            self._feedback = f"Move rejected: {result.rejection_reason}."
        return result

    def _require_action_budget(self) -> None:
        if len(self._accepted) >= self._max_actions:
            raise ValueError("puzzle action budget is exhausted")

    def structured_state(self) -> dict[str, object]:
        state = self.session.state
        classification = self.session.classification
        return {
            "adapter_id": "gamepack_raylib_2d_puzzle",
            "board": list(state["board"]),  # type: ignore[arg-type]
            "target": list(state["target"]),  # type: ignore[arg-type]
            "move_count": state["move_count"],
            "focused_cell": self._focus,
            "selected_cell": self._selected,
            "feedback": self._feedback,
            "ending_ids": list(classification.ending_ids),
            "state_hash": self.session.state_hash,
        }

    def render(self, backend: RaylibBackend, resources: ResourceManager) -> None:
        texture = resources.handle("board_texture")
        if type(texture) is not TextureHandle:
            raise RuntimeError("board_texture did not load as a texture")
        backend.draw_texture(
            texture,
            x=_BOARD_X,
            y=_BOARD_Y,
            width=3 * _CELL_SIZE,
            height=_CELL_SIZE,
        )
        resources.mark_drawn("board_texture")
        state = self.session.state
        board = state["board"]
        target = state["target"]
        if not isinstance(board, list) or not isinstance(target, list):
            raise RuntimeError("puzzle board state is invalid")
        for index, symbol in enumerate(board):
            x = _BOARD_X + index * _CELL_SIZE
            color = _FOCUS if index == self._focus else _WHITE
            backend.draw_rectangle(
                x=x + 4,
                y=_BOARD_Y + 4,
                width=_CELL_SIZE - 8,
                height=_CELL_SIZE - 8,
                color=color,
                outline=True,
            )
            if index == self._selected:
                backend.draw_rectangle(
                    x=x + 12,
                    y=_BOARD_Y + 12,
                    width=_CELL_SIZE - 24,
                    height=_CELL_SIZE - 24,
                    color=_SELECTED,
                    outline=True,
                )
            backend.draw_text(
                str(symbol),
                x=x + 52,
                y=_BOARD_Y + 34,
                size=44,
                color=_BLACK,
                font=None,
            )
            backend.draw_text(
                str(index + 1),
                x=x + 10,
                y=_BOARD_Y + 92,
                size=20,
                color=_BLACK,
                font=None,
            )
        backend.draw_text(
            "Target: " + " ".join(str(item) for item in target),
            x=128,
            y=370,
            size=24,
            color=_WHITE,
            font=None,
        )
        backend.draw_text(
            f"Moves: {state['move_count']} | {_feedback_text(self._feedback)}",
            x=128,
            y=410,
            size=22,
            color=_SUCCESS if self.session.classification.terminal else _ERROR,
            font=None,
        )
        backend.draw_text(
            "Arrows/Tab: focus  Enter/Space: select  R: restart",
            x=128,
            y=460,
            size=20,
            color=_WHITE,
            font=None,
        )


def _feedback_text(value: str) -> str:
    return value if len(value) <= 72 else value[:69] + "..."


__all__ = ["PuzzleController"]
