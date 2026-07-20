---
name: implement-pyray-isometric-rendering
description: Implement deterministic 2D/2.5D isometric projection, culling, depth ordering, animation selection, and render passes in pyray. Use for the world-rendering phase only.
---

# Implement isometric rendering

## Scope

Implement only G21 isometric projection/inverse, culling, stable depth planning,
tick-based animation selection, render passes, and drawing-port behavior.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G20 gate.
- Current source-tree/runtime/platform/catalog hashes and G21 writable surface.
- Selected world ID, release ID, and verified bundle hash for reference scenes.
- Immutable render-state contract, isometric coordinate/elevation convention,
  semantic asset bindings, and representative reference scenes.
- G20 drawing port and viewport contract; G23 native resources may be fallbacks.

## Outputs

- Game-owned pure projection/inverse helpers and render-command planner/renderer.
- Projection, depth-tie, pivot, culling, pass-order, and scene tests.
- Forge-owned G21 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, bundles, and authoritative state immutable.
- Consume immutable render state only; do not poll input, advance simulation,
  load resources, or let native handles enter render planning.
- Keep inverse projection pure for later G22 picking.
- Invoke only the screen-UI pass position; G24 owns UI layout and behavior.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the rendering gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Consume immutable render state; never read or mutate authoritative world state.
3. Own pure projection/inverse helpers and use authored pivots/elevation.
4. Cull before emitting draw commands and sort by explicit stable depth keys.
5. Render terrain, overlays, merged entities, foreground, and effects, then
   invoke the G24-owned screen-UI pass.
6. Test projection, depth ties, pivots, culling, and tick-based animation frames.

## Completion

Complete when projection/inverse round trips, stable depth/culling/pass tests and
representative scene checks pass with primitive fallbacks, the shared headless
suite and clean-game audit pass, and evidence is recorded outside `GAME_ROOT`.
