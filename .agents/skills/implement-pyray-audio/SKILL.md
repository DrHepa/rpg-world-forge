---
name: implement-pyray-audio
description: Implement deterministic domain-event sound effects, streamed music/ambience, volume buses, and safe audio-device ownership in pyray. Use for the audio phase only.
---

# Implement pyray audio

## Scope

Implement only G25 event-to-audio presentation, sound/music ownership, buses,
stream updates, and declared silent fallback. Do not change simulation outcomes.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G20-G24 gates.
- Current source-tree/runtime/platform/catalog hashes and G25 writable surface.
- Selected world ID, release ID, and verified bundle/renderpack hashes.
- Ordered domain events, verified semantic audio bindings, and platform lock.
- Current `game_data/shared.lock.json` hash and any declared game-owned common/UI
  audio additions beneath `game_data/shared/audio/**`.
- Required/optional audio policy, deterministic variation seed, and bus policy.

## Outputs

- Game-owned audio registry/adapter, event mapping, buses, and optional silent mode.
- When needed, one reviewed common/UI audio change, its origin/license entry in
  `THIRD_PARTY_NOTICES.md`, and a regenerated canonical shared-asset lock.
- Fake-adapter plus required native device/stream lifecycle tests.
- Forge-owned G25 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, runtime/platform/catalog locks, bundles, simulation, and
  domain events immutable. Change the shared lock only through its lock script.
- Let one adapter own the device and handles; initialize before load and unload
  before close. Update streams once per display frame.
- Never use real-time Python callbacks; audio failure cannot change simulation.
- Let G20 orchestrate lifecycle; G25 alone owns the audio device and handles.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the audio gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Resolve semantic audio bindings only from the verified renderpack or the
   hash-locked game-owned shared audio root.
3. Initialize the device before loading and own sounds/music in one registry.
4. Map ordered domain events to deterministic variations and logical buses.
5. Update active music streams once per display frame.
6. If shared audio changed, record origin/license in
   `THIRD_PARTY_NOTICES.md`, run `python scripts/lock_shared_assets.py`, and
   verify that no undeclared, authoring, or provider metadata entered the lock.
7. Test with a fake adapter, then smoke-test load, play, stop, unload, and silence.

## Completion

Complete when deterministic event mapping, buses, stream updates, failure unwind,
and optional silence pass fake tests plus every required native audio row. Then
run the shared headless suite and clean-game audit.
