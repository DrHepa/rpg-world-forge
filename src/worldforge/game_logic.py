"""Compatibility façade for the Forge-independent gamepack v1 kernel.

The compiler keeps its integral source validation here.  Execution itself is
owned by :mod:`gamepack_runtime`, which has no dependency on either Forge
authoring code or the legacy RPG runtime.
"""

from __future__ import annotations

from collections.abc import Mapping

from gamepack_runtime.contracts import (
    ANALYSIS_LIMITS,
    ANALYZERS,
    EXECUTION_SEMANTICS,
    MAX_SAFE_INTEGER,
    CandidateAction,
    GameLogicError,
    JsonScalar,
    JsonValue,
    StateClassification,
    TransitionResult,
    canonical_action_hash,
    canonical_events_hash,
    canonical_gamepack_hash,
    canonical_state_bytes,
    canonical_state_hash,
)
from gamepack_runtime.contracts import (
    analysis_requirements_for as _neutral_analysis_requirements_for,
)
from gamepack_runtime.semantics_v1 import (
    GamepackInterpreter as _NeutralGamepackInterpreter,
)
from gamepack_runtime.semantics_v1 import (
    canonical_trace_step,
)


class _Interpreter(_NeutralGamepackInterpreter):
    """Preserve the established Forge API while executing the neutral kernel."""

    def __init__(self, gamepack: Mapping[str, object], *, already_validated: bool = False) -> None:
        if already_validated:
            checked = gamepack
        else:
            # Delayed to preserve the gamepack compiler's import cycle.
            from worldforge.gamepack import GamepackError, validate_gamepack_document

            try:
                checked = validate_gamepack_document(gamepack)
            except GamepackError as exc:
                raise GameLogicError(exc.reason_code, exc.detail) from exc
        super().__init__(
            checked,
            already_validated=True,
            limits=ANALYSIS_LIMITS,
        )

    def transition(
        self,
        state: Mapping[str, JsonValue],
        candidate: CandidateAction,
    ) -> TransitionResult:
        """Preserve the historical Forge state-first malformed-input policy."""

        return self.transition_legacy(state, candidate)


def analysis_requirements_for(
    modules: Mapping[str, object],
    logic: Mapping[str, object],
) -> dict[str, object]:
    return _neutral_analysis_requirements_for(
        modules,
        logic,
        limits=ANALYSIS_LIMITS,
        analyzers=ANALYZERS,
    )


def _interpreter(gamepack: Mapping[str, object]) -> _Interpreter:
    return _Interpreter(gamepack)


def initial_state(gamepack: Mapping[str, object]) -> dict[str, JsonValue]:
    return _interpreter(gamepack).initial_state()


def enumerate_candidates(
    gamepack: Mapping[str, object],
) -> tuple[CandidateAction, ...]:
    return _interpreter(gamepack).enumerate_candidates()


def classify_state(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
) -> StateClassification:
    return _interpreter(gamepack).classify(state)


def transition(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
    candidate: CandidateAction,
) -> TransitionResult:
    return _interpreter(gamepack).transition(state, candidate)


def legal_transitions(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
) -> tuple[TransitionResult, ...]:
    return _interpreter(gamepack).legal_transitions(state)


__all__ = [
    "ANALYSIS_LIMITS",
    "ANALYZERS",
    "EXECUTION_SEMANTICS",
    "MAX_SAFE_INTEGER",
    "CandidateAction",
    "GameLogicError",
    "JsonScalar",
    "JsonValue",
    "StateClassification",
    "TransitionResult",
    "_Interpreter",
    "analysis_requirements_for",
    "canonical_action_hash",
    "canonical_events_hash",
    "canonical_gamepack_hash",
    "canonical_state_bytes",
    "canonical_state_hash",
    "canonical_trace_step",
    "classify_state",
    "enumerate_candidates",
    "initial_state",
    "legal_transitions",
    "transition",
]
