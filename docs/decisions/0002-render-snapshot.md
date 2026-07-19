# ADR-0002: Immutable RenderState between world and renderer

- Status: accepted
- Date: 2026-07-19

## Decision

The renderer consumes a frozen `RenderState` and never reads `WorldState`
directly. The application maintains front/back snapshots to decouple simulation
from presentation.

## Consequences

- Rendering cannot accidentally mutate the simulation.
- World tests do not require raylib or a window.
- Interpolation, replay capture, or threaded rendering can be added without
  rewriting game rules.
