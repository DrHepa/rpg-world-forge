---
name: implement-gameplay-time-schedules
description: Implement or modify the deterministic world clock, calendars, NPC schedules, and route fallback policy in a standalone game. Use for the time-and-schedules phase only.
---

# Implement time and schedules

## Scope

Implement only G11 authoritative world time, derived calendars, NPC schedules,
and schedule fallback routing. Navigation owns route mechanics; G11 requests it.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04/G10 gates.
- Current source-tree/runtime/platform/catalog hashes and G11 writable surface.
- Selected world ID, release ID, and verified bundle hash for G11 fixtures.
- Validated clock/calendar and schedule definitions from imported content.
- Explicit condition, priority, missed-window, and fallback policies.

## Outputs

- Game-owned time/schedule composition/extensions and typed schedule actions.
- Immutable clock/schedule summaries and focused boundary/replay tests.
- Forge-owned G11 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, and bundles immutable.
- Use integer simulation ticks only; never query wall-clock or raylib frame time.
- Do not reimplement navigation, dialogue, UI, or rendering.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the time/schedule gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Keep authoritative time as integer simulation ticks and derived calendar data.
3. Resolve schedule conditions and fallback entries in a documented stable order.
4. Route scheduled movement through navigation actions and reservation rules.
5. Test day boundaries, missed windows, unavailable destinations, and replay.
6. Publish clock/schedule summaries through immutable render state.

## Completion

Complete when schedule resolution and fallback order are deterministic across
boundary/missed-window fixtures, replay hashes match, focused/headless tests and
the audit pass, and evidence is recorded outside `GAME_ROOT`. If the vendored
runtime already meets G11, verify it without duplicating it in game code.
