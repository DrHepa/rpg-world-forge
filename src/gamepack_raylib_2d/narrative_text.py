"""Authored-text-only branching narrative controller and renderer."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

from gamepack_raylib_2d.backend import RaylibBackend
from gamepack_raylib_2d.executable_shape import (
    AdapterExecutableShapeError,
    inspect_adapter_executable_shape,
)
from gamepack_raylib_2d.resources import RaylibResourceError, ResourceManager
from gamepack_raylib_2d.types import FontHandle, SemanticIntent
from gamepack_runtime import GameSession, TransitionResult

_WHITE = (245, 245, 245, 255)
_BLACK = (18, 18, 22, 255)
_FOCUS = (255, 216, 64, 255)
_PANEL = (38, 46, 66, 255)
_ENDING = (32, 84, 58, 255)


class NarrativeTextController:
    __slots__ = (
        "_accepted",
        "_action_ids",
        "_cursor_id",
        "_focus",
        "_intents",
        "_max_actions",
        "_units",
        "session",
    )

    def __init__(
        self,
        gamepack: Mapping[str, object],
        *,
        locale: str = "en",
        max_actions: int,
    ) -> None:
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError("narrative max_actions must be a positive exact integer")
        if locale != "en":
            raise RaylibResourceError(
                "locale_unsupported",
                "the bounded narrative adapter supports only locale en",
            )
        executable_shape = inspect_adapter_executable_shape(
            gamepack,
            "gamepack_raylib_2d_text",
        )
        localization = gamepack.get("localization")
        if (
            not isinstance(localization, Mapping)
            or localization.get("source_locale") != "en"
            or localization.get("supported_locales") != ["en"]
        ):
            raise RaylibResourceError(
                "locale_unsupported",
                "gamepack localization does not expose the exact locale en",
            )
        logic = gamepack.get("logic")
        modules = gamepack.get("modules")
        if not isinstance(logic, Mapping) or not isinstance(modules, Mapping):
            raise RaylibResourceError("narrative_contract_invalid", "gamepack narrative is absent")
        cursor = logic.get("narrative_cursor")
        if not isinstance(cursor, Mapping) or cursor.get("id") != "wf_internal_narrative_cursor":
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "compiler-owned narrative cursor is absent",
            )
        narrative_modules = modules.get("narrative")
        if not isinstance(narrative_modules, list) or len(narrative_modules) != 1:
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "bounded adapter requires one authored narrative module",
            )
        raw_units = narrative_modules[0].get("units")
        if not isinstance(raw_units, list):
            raise RaylibResourceError("narrative_contract_invalid", "narrative units are absent")
        units: dict[str, dict[str, Any]] = {}
        for raw in raw_units:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                raise RaylibResourceError(
                    "narrative_contract_invalid",
                    "narrative unit identity is invalid",
                )
            units[raw["id"]] = raw
        self.session = GameSession(gamepack)
        self._action_ids = executable_shape.narrative_action_ids
        self._cursor_id = cursor["id"]
        self._units = units
        self._intents: deque[SemanticIntent] = deque()
        self._max_actions = max_actions
        self._focus = 0
        self._accepted: list[TransitionResult] = []
        self._validate_current_unit()

    @property
    def accepted_results(self) -> list[TransitionResult]:
        return list(self._accepted)

    def _current_unit(self) -> dict[str, Any]:
        cursor = self.session.state.get(self._cursor_id)
        unit = self._units.get(str(cursor))
        if unit is None:
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "narrative cursor references an unknown unit",
            )
        return unit

    def _validate_current_unit(self) -> None:
        unit = self._current_unit()
        if unit.get("unit_type") == "choice":
            options = unit.get("options")
            if not isinstance(options, list) or len(options) != 2:
                raise RaylibResourceError(
                    "narrative_contract_invalid",
                    "bounded narrative choice requires exactly two authored options",
                )
            for option in options:
                if (
                    not isinstance(option, Mapping)
                    or not isinstance(option.get("id"), str)
                    or not isinstance(option.get("label"), str)
                ):
                    raise RaylibResourceError(
                        "narrative_contract_invalid",
                        "authored narrative option is incomplete",
                    )
        elif unit.get("unit_type") != "ending":
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "bounded narrative unit is neither choice nor ending",
            )

    def queue_intent(self, intent: SemanticIntent) -> None:
        if type(intent) is not SemanticIntent:
            raise ValueError("narrative intent must be an exact SemanticIntent")
        if len(self._intents) >= 128:
            raise ValueError("narrative intent queue exceeds its bound")
        self._intents.append(intent)

    def step(self) -> TransitionResult | None:
        if not self._intents:
            return None
        intent = self._intents.popleft()
        if intent.kind in {"focus_choice", "focus_next"}:
            self._focus = (
                (self._focus + 1) % 2 if intent.value is None else max(0, min(1, intent.value))
            )
            return None
        if intent.kind != "choose" or intent.value is None or not 0 <= intent.value <= 1:
            raise ValueError(f"unsupported narrative intent: {intent.kind}")
        unit = self._current_unit()
        options = unit.get("options")
        if not isinstance(options, list) or len(options) != 2:
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "current narrative unit has no authored choices",
            )
        option = options[intent.value]
        unit_id = unit.get("id")
        option_id = option.get("id")
        if not isinstance(unit_id, str) or not isinstance(option_id, str):
            raise AdapterExecutableShapeError("current narrative option identity is invalid")
        action_id = self._action_ids.get((unit_id, option_id))
        if action_id is None:
            raise AdapterExecutableShapeError("current narrative option is not dispatchable")
        if len(self._accepted) >= self._max_actions:
            raise ValueError("narrative action budget is exhausted")
        result = self.session.apply(action_id, {})
        if result.accepted:
            self._accepted.append(result)
            self._focus = intent.value
            self._validate_current_unit()
        return result

    def structured_state(self) -> dict[str, object]:
        unit = self._current_unit()
        options = unit.get("options", [])
        choices: list[dict[str, object]] = []
        if isinstance(options, list):
            unit_id = unit.get("id")
            if not isinstance(unit_id, str):
                raise AdapterExecutableShapeError("current narrative unit identity is invalid")
            for index, option in enumerate(options):
                option_id = option.get("id")
                if not isinstance(option_id, str):
                    raise AdapterExecutableShapeError(
                        "current narrative option identity is invalid"
                    )
                action_id = self._action_ids.get((unit_id, option_id))
                if action_id is None:
                    raise AdapterExecutableShapeError(
                        "current narrative option is not dispatchable"
                    )
                choices.append(
                    {
                        "index": index + 1,
                        "action_id": action_id,
                        "label": option["label"],
                    }
                )
        state = self.session.state
        knowledge = state.get("knowledge")
        if not isinstance(knowledge, list):
            raise RaylibResourceError(
                "narrative_contract_invalid",
                "narrative knowledge state is invalid",
            )
        return {
            "adapter_id": "gamepack_raylib_2d_text",
            "cursor": state[self._cursor_id],
            "title": unit["title"],
            "choices": choices,
            "focused_choice": self._focus,
            "knowledge": list(knowledge),
            "ending_ids": list(self.session.classification.ending_ids),
            "state_hash": self.session.state_hash,
        }

    def render(self, backend: RaylibBackend, resources: ResourceManager) -> None:
        state = self.structured_state()
        ending = bool(state["ending_ids"])
        binding_id = "ending_panel" if ending else "choice_panel"
        font = resources.handle(binding_id)
        if type(font) is not FontHandle:
            raise RuntimeError(f"{binding_id} did not load as a font")
        backend.draw_rectangle(
            x=64,
            y=64,
            width=672,
            height=400,
            color=_ENDING if ending else _PANEL,
        )
        backend.draw_text(
            str(state["title"]),
            x=96,
            y=96,
            size=38,
            color=_WHITE,
            font=font,
        )
        choices = state["choices"]
        if isinstance(choices, list):
            for index, choice in enumerate(choices):
                y = 240.0 + index * 80.0
                backend.draw_rectangle(
                    x=96,
                    y=y,
                    width=608,
                    height=64,
                    color=_FOCUS if index == self._focus else _WHITE,
                    outline=True,
                )
                backend.draw_text(
                    f"{choice['index']}. {choice['label']}",
                    x=116,
                    y=y + 16,
                    size=28,
                    color=_WHITE,
                    font=font,
                )
        knowledge = state["knowledge"]
        backend.draw_text(
            "Knowledge: " + (", ".join(knowledge) if knowledge else "none"),
            x=96,
            y=400,
            size=24,
            color=_WHITE,
            font=font,
        )
        resources.mark_drawn(binding_id)


__all__ = ["NarrativeTextController"]
