---
name: implement-gameplay-interactions
description: Implement contextual interactions, abilities, costs, cooldowns, and deterministic outcome events in a standalone game. Use for the interaction-and-abilities phase only.
---

# Implement interactions and abilities

## Scope

Implement only G12 contextual action availability and authoritative reduction
for interactions, abilities, costs, and cooldowns. Presentation consumes results.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04/G10-G11 gates.
- Current source-tree/runtime/platform/catalog hashes and G12 writable surface.
- Selected world ID, release ID, and verified bundle hash for G12 fixtures.
- Validated interaction/ability definitions and authoritative state contracts.
- Explicit range, target, cost, condition, cooldown, and conflict policies.

## Outputs

- Game-owned interaction composition/extensions and typed outcome events.
- Semantic prompt/result views plus focused availability/reduction/replay tests.
- Forge-owned G12 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, and bundles immutable.
- Revalidate at reduction time; a UI prompt never authorizes an action.
- Do not implement dialogue graphs, UI widgets, particles, audio, or native input.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the interaction gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Derive available actions from state, range, conditions, resources, and cooldowns.
3. Validate again when the action is reduced; UI availability is not authority.
4. Emit typed domain events for outcomes and presentation feedback.
5. Test invalid targets, simultaneous changes, costs, cooldown expiry, and replay.
6. Expose semantic prompts/results, never native input codes or texture handles.

## Completion

Complete when availability and reduction agree under state changes, invalid
actions cannot spend resources, cooldown/cost fixtures replay identically, and
focused/headless tests plus the clean-game audit pass. If the vendored runtime
already meets G12, verify it without duplicating it in game code.
