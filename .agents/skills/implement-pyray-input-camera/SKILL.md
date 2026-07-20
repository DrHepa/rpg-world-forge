---
name: implement-pyray-input-camera
description: Implement semantic input mapping, virtual resolution, Camera2D control, and inverse isometric pointer picking in a pyray game. Use for the input-and-camera phase only.
---

# Implement input and camera

## Scope

Implement only G22 device-to-semantic action mapping, virtual viewport,
Camera2D state, coordinate conversion, and inverse-isometric pointer picking.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G20-G21 gates.
- Current source-tree/runtime/platform/catalog hashes and G22 writable surface.
- Selected world ID, release ID, and verified bundle hash for picking fixtures.
- Semantic action schema, platform input classes, viewport/zoom policy, and G21
  tested inverse projection.
- Representative keyboard, pointer, gamepad, resize, DPI, and tile-edge fixtures.

## Outputs

- Game-owned input/camera adapter and pure coordinate-conversion helpers.
- Semantic edge/deadzone, viewport, camera, picking, and native-availability tests.
- Forge-owned G22 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, bundles, projection definition, and simulation immutable.
- Keep camera state in presentation; pass semantic actions only to simulation.
- Do not draw maps, load assets, or implement gameplay reduction.
- Let G20 invoke this polling port; let G24 interpret UI focus/navigation actions.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the input/camera gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Map keyboard, pointer, and gamepad state to semantic typed actions.
3. Distinguish pressed, released, held, and analog values with deadzones.
4. Compute letterboxing and virtual coordinates from the real window size.
5. Apply Camera2D inverse transform, then consume G21's tested inverse projection.
6. Test resize, zoom limits, tile edges, letterbox bars, and action edge delivery.

## Completion

Complete when action edges/deadzones, resize/DPI conversion, camera bounds, and
tile-edge picking pass in semantic tests and required native probes, followed by
the shared headless suite and clean-game audit.
