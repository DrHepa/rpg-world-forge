from __future__ import annotations

from isoworld.content.models import WorldPack
from isoworld.world.state import GameAction, WorldState, initial_world_state, reduce_world


class Simulation:
    def __init__(self, pack: WorldPack, state: WorldState | None = None) -> None:
        self.pack = pack
        self.state = state or initial_world_state(pack)
        self.action_log: list[GameAction] = []

    def dispatch(self, action: GameAction, *, record: bool = True) -> WorldState:
        self.state = reduce_world(self.state, action, self.pack)
        if record:
            self.action_log.append(action)
        return self.state

    def tick(self) -> WorldState:
        return self.dispatch(GameAction(kind="tick"))

    def restore(self, state: WorldState) -> None:
        self.state = state
        self.action_log.clear()
