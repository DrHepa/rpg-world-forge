"""Bounded fixed-step accumulation independent from display timing."""

from __future__ import annotations

import math


class FixedStepClock:
    __slots__ = ("_accumulator", "_max_catchup_steps", "_max_frame_delta", "_step")

    def __init__(
        self,
        *,
        frequency: int = 60,
        max_frame_delta: float = 0.25,
        max_catchup_steps: int = 5,
    ) -> None:
        if type(frequency) is not int or frequency != 60:
            raise ValueError("the bounded adapters require an exact 60 Hz step")
        if type(max_catchup_steps) is not int or not 1 <= max_catchup_steps <= 8:
            raise ValueError("max_catchup_steps must be an exact integer from 1 through 8")
        if type(max_frame_delta) not in {int, float} or not 0.0 < max_frame_delta <= 0.25:
            raise ValueError("max_frame_delta must be finite and at most 0.25 seconds")
        self._step = 1.0 / frequency
        self._max_frame_delta = float(max_frame_delta)
        self._max_catchup_steps = max_catchup_steps
        self._accumulator = 0.0

    @property
    def step_seconds(self) -> float:
        return self._step

    def consume(self, frame_delta: float) -> int:
        if type(frame_delta) not in {int, float} or not math.isfinite(float(frame_delta)):
            raise ValueError("frame delta must be a finite number")
        if frame_delta < 0.0:
            raise ValueError("frame delta cannot be negative")
        self._accumulator += min(float(frame_delta), self._max_frame_delta)
        steps = min(int((self._accumulator + 1e-12) / self._step), self._max_catchup_steps)
        self._accumulator -= steps * self._step
        if steps == self._max_catchup_steps and self._accumulator >= self._step:
            self._accumulator %= self._step
        return steps


__all__ = ["FixedStepClock"]
