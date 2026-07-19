from __future__ import annotations

import heapq
from collections.abc import Iterable

from isoworld.content.models import WorldPack

Cell = tuple[int, int]


def neighbors(cell: Cell) -> tuple[Cell, ...]:
    x, y = cell
    return ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y))


def find_path(
    pack: WorldPack,
    map_id: str,
    start: Cell,
    goal: Cell,
    *,
    blocked: Iterable[Cell] = (),
) -> tuple[Cell, ...]:
    """Return a deterministic A* path excluding start, or an empty tuple."""
    if map_id not in pack.maps or not pack.is_walkable(map_id, *goal):
        return ()
    if start == goal:
        return ()
    blocked_cells = set(blocked)
    blocked_cells.discard(start)
    if goal in blocked_cells:
        return ()

    frontier: list[tuple[int, int, Cell]] = []
    sequence = 0
    heapq.heappush(frontier, (0, sequence, start))
    came_from: dict[Cell, Cell | None] = {start: None}
    cost: dict[Cell, int] = {start: 0}
    while frontier:
        _, _, current = heapq.heappop(frontier)
        if current == goal:
            break
        for candidate in neighbors(current):
            if candidate in blocked_cells or not pack.is_walkable(map_id, *candidate):
                continue
            candidate_cost = cost[current] + 1
            if candidate_cost >= cost.get(candidate, 2**31):
                continue
            cost[candidate] = candidate_cost
            came_from[candidate] = current
            sequence += 1
            heuristic = abs(candidate[0] - goal[0]) + abs(candidate[1] - goal[1])
            heapq.heappush(frontier, (candidate_cost + heuristic, sequence, candidate))
    if goal not in came_from:
        return ()
    path: list[Cell] = []
    current = goal
    while current != start:
        path.append(current)
        parent = came_from[current]
        if parent is None:
            return ()
        current = parent
    path.reverse()
    return tuple(path)
