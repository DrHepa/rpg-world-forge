---
name: implement-gameplay-navigation
description: Implement or modify deterministic collision, pathfinding, movement, and cell-reservation behavior in the standalone game. Use for the navigation phase only.
---

# Implement gameplay navigation

## Scope

Implement only G10 collision, deterministic pathfinding, movement, occupancy,
and reservations. Do not implement schedules, interaction rules, camera, or draw.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04 harness.
- Current source-tree/runtime/platform/catalog hashes and G10 writable surface.
- Selected world ID, release ID, and verified bundle hash for G10 fixtures.
- Validated map walkability/cost data and actor movement contracts.
- Explicit tie-breaking, occupancy, reservation, and replanning policies.

## Outputs

- Game-owned navigation composition/extensions under `src/game`.
- Typed movement actions, immutable route/position views, and focused tests.
- Forge-owned G10 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, and imported bundles immutable.
- Keep navigation deterministic and free of `pyray`, wall-clock, schedule, UI,
  and rendering concerns.
- Resolve contention through explicit stable policy, not iteration accident.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the navigation gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Define walkability, costs, occupancy, reservations, and tie-breaking.
3. Express movement through typed actions and deterministic state transitions.
4. Test blocked routes, contention, replanning, map boundaries, and replay hashes.
5. Expose only immutable route and actor-position data to presentation.

## Completion

Complete when identical inputs produce identical paths and G04 replay hashes.
Run focused/headless tests and the clean-game audit, then record evidence outside
`GAME_ROOT`. If the vendored runtime already satisfies G10, record verification
without duplicating it in game code.
