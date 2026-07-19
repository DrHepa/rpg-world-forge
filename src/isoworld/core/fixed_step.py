from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class FixedStep:
    tick_rate: int = 20
    max_updates_per_frame: int = 5
    accumulator: float = 0.0

    @property
    def dt(self) -> float:
        return 1.0 / self.tick_rate

    def advance(self, frame_time: float, update: Callable[[float], None]) -> int:
        self.accumulator += min(max(frame_time, 0.0), 0.25)
        updates = 0
        while self.accumulator >= self.dt and updates < self.max_updates_per_frame:
            update(self.dt)
            self.accumulator -= self.dt
            updates += 1
        if updates == self.max_updates_per_frame:
            self.accumulator = min(self.accumulator, self.dt)
        return updates
