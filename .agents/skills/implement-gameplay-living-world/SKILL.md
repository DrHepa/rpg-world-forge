---
name: implement-gameplay-living-world
description: Implement one deterministic construction, needs, goals, economy, scarcity, or delayed-consequence system slice in a standalone game. Use for one G14 living-world slice only, not narrative campaigns or presentation.
---

# Implement living-world systems

## Scope

Implement one named `G14:<system-id>` slice for construction, needs, goals,
economy, scarcity, production, or delayed consequences. Narrative campaigns
remain in G13; presentation remains in G20-G25.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04 and required
  G10-G13 dependency gates.
- Current source-tree/runtime/platform/catalog hashes and G14 writable surface.
- Selected world ID, release ID, and verified bundle hash for this system slice.
- One declared `system-id`, its typed content, budgets/capacities, and dependencies.
- Existing ordered domain-event contracts and Forge-owned system inventory.

## Outputs

- One game-owned deterministic reducer/composition slice and focused tests.
- Typed cross-system events plus an external inventory/aggregate-gate update.

## Invariants

- Keep `src/isoworld`, locks, bundles, narrative graphs, and presentation immutable.
- Route cross-system effects through ordered events with explicit priority,
  capacity, cancellation, scarcity, and tie-breaking policies.
- Do not begin a second system slice in the same invocation.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the living-world gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Define the selected system slice as typed data and deterministic reducers.
3. Route cross-system effects through ordered domain events, not direct UI hooks.
4. Specify capacity, priority, scarcity, cancellation, and tie-breaking policies.
5. Preserve navigation, narrative, and save/replay invariants after each change.
6. Test causal chains, resource conservation, delayed effects, and state hashes.
7. Update the Forge-owned system inventory and aggregate gate outside `GAME_ROOT`.

## Completion

Complete one slice only when causal-chain, conservation, cancellation, and
replay tests pass with the shared headless suite and clean-game audit. Mark G14
aggregate complete only when every declared system ID has matching evidence. If
the runtime already supplies a slice, verify it without duplicating game code.
