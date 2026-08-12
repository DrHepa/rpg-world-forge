"""In-memory deterministic session over one immutable gamepack."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from gamepack_runtime.contracts import (
    CandidateAction,
    GameLogicError,
    JsonValue,
    StateClassification,
    TransitionResult,
    canonical_state_hash,
    snapshot_strict_candidate,
    snapshot_strict_state,
)
from gamepack_runtime.semantics_v1 import GamepackInterpreter


class GameSession:
    """Commit accepted v1 transitions and retain no external services."""

    def __init__(self, gamepack: Mapping[str, object]) -> None:
        self._interpreter = GamepackInterpreter(gamepack)
        self._state = self._interpreter.initial_state()

    @property
    def state(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._state)

    @property
    def state_hash(self) -> str:
        return canonical_state_hash(self._state)

    @property
    def classification(self) -> StateClassification:
        return self._interpreter.classify(self._state)

    def apply(
        self,
        action_id: str,
        parameters: Mapping[str, JsonValue],
    ) -> TransitionResult:
        candidate = snapshot_strict_candidate(CandidateAction(action_id, parameters))
        result = self._interpreter.transition(self._state, candidate)
        if result.accepted:
            self._state = copy.deepcopy(result.post_state)
        return result

    def restore(self, state: Mapping[str, JsonValue]) -> None:
        """Replace session state only after exact runtime-domain validation."""

        checked = snapshot_strict_state(state)
        self._interpreter.classify(checked)
        for state_id, schema in self._interpreter.state_schema.items():
            if schema.get("mutability") == "constant" and checked[state_id] != schema.get(
                "initial"
            ):
                raise GameLogicError(
                    "state_constant_mismatch",
                    f"constant state {state_id} differs from its initial value",
                )
        self._state = copy.deepcopy(checked)
