---
name: implement-pyray-app-loop
description: Implement pyray window ownership and fixed-step application-loop orchestration around an existing deterministic runtime. Use for G20 only; input, rendering, graphics resources, UI, and audio belong to later phases.
---

# Implement the pyray application loop

## Scope

Implement only G20 native window ownership and the bounded fixed-step application
loop. Invoke input/render/audio ports; do not implement their phase-owned logic.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04 and required
  G10-G15 domain gates.
- Current source-tree/runtime/platform/catalog hashes and G20 writable surface.
- Selected world ID, release ID, and verified bundle hash for shell tests.
- Valid locks/catalog, fixed tick duration, catch-up bound, and shutdown policy.
- Port contracts for semantic input, immutable render state, drawing, and audio.

## Outputs

- Game-owned pyray application shell and lifecycle adapter under `src/game`.
- Fixed-step/headless-parity and partial/complete teardown tests.
- Forge-owned G20 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, bundles, and simulation contracts immutable.
- Keep `import pyray as pr` behind the adapter and own only window lifecycle.
- Do not map devices, define projection/camera, load native resources, implement
  UI/audio, or acquire their handles in this phase.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the application-loop gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Validate selected bundle data before creating native resources.
3. Initialize configured window flags and the window; expose ports for later
   graphics-resource and audio phases without acquiring their handles here.
4. Invoke the semantic-input port once per display frame and advance bounded ticks.
5. Invoke the drawing port with only the latest immutable render state.
6. Use `try/finally` and close the window on complete or partial failure.

## Completion

Complete when headless and windowed runs produce matching simulation digests,
tick catch-up remains bounded, every partial/normal exit closes the window once,
and focused/headless tests plus the clean-game audit pass.
