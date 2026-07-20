---
name: implement-pyray-ui
description: Implement accessible screen-space HUD, menus, dialogue, quest, inventory, and interaction presentation in pyray. Use for one defined UI flow at a time.
---

# Implement a pyray UI flow

## Scope

Implement one named `G24:<flow-id>` screen-space flow. Do not combine unrelated
HUD, menu, dialogue, quest, inventory, or interaction flows in one invocation.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G20-G23 gates.
- Current source-tree/runtime/platform/catalog hashes and G24 writable surface.
- Selected world ID, release ID, and verified bundle hash for this UI flow.
- One declared `flow-id`, entry/focus/cancel/completion states, and view models.
- Semantic action schema, bundle locale tables, resource bindings, and UI inventory.

## Outputs

- One game-owned accessible/localized UI flow and focused tests.
- Forge-owned UI inventory and aggregate-gate update outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, all locks, bundles, domain state, shared asset bytes, and
  authored prose immutable. Route a missing native presentation resource to G23.
- Consume view models and emit semantic actions only; never mutate domain state.
- Consume G22 semantic actions; do not translate native device state in G24.
- Use semantic portrait/font bindings, never raw paths, and do not begin a second
  flow in the same invocation.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the UI gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Name the single flow and its entry, focus, cancel, and completion states.
3. Consume render/narrative view models and emit semantic actions only.
4. Support keyboard, pointer, and controller focus with scalable virtual layout.
5. Localize text from bundle locale tables; never embed world prose in game code.
6. Test focus order, unavailable actions, overflow, missing bindings, and resize.
7. Update the Forge-owned UI-flow inventory and aggregate gate outside `GAME_ROOT`.

## Completion

Complete one flow only when keyboard/pointer/controller focus, cancel, overflow,
localization, missing-binding, and resize tests pass with the shared headless
suite and audit. Mark G24 aggregate complete only when all flow IDs have evidence.
