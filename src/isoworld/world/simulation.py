from __future__ import annotations

from isoworld.content.models import WorldPack
from isoworld.world.state import GameAction, WorldState, initial_world_state, reduce_world


class Simulation:
    def __init__(self, pack: WorldPack) -> None:
        self.pack = pack
        self.state = initial_world_state(pack)

    def dispatch(self, action: GameAction) -> WorldState:
        self.state = reduce_world(self.state, action, self.pack)
        return self.state

    def tick(self) -> WorldState:
        return self.dispatch(GameAction(kind="tick"))
