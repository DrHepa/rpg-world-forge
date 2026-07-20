---
name: implement-gameplay-narrative
description: Implement deterministic facts, knowledge boundaries, relationships, dialogue, quests, scenes, and actor campaigns in a standalone game. Use for the narrative-execution phase only.
---

# Implement narrative execution

## Scope

Implement only G13 deterministic execution of authored facts, knowledge,
relationships, dialogue, quests, scenes, and personal campaigns. Do not author
content or implement economy/living-world reducers or presentation.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04/G10-G12 gates.
- Current source-tree/runtime/platform/catalog hashes and G13 writable surface.
- Selected world ID, release ID, and verified bundle hash for G13 fixtures.
- Validated narrative IDs, graphs, conditions, effects, and knowledge boundaries.
- Explicit ordering, ownership, reachability, failure, and softlock policies.

## Outputs

- Game-owned narrative composition/extensions and typed narrative events/views.
- Focused knowledge, reachability, ownership, time-window, softlock, and replay tests.
- Forge-owned G13 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, bundles, and authored prose immutable.
- Evaluate authoritative state deterministically and never expose global knowledge.
- Never generate prose at runtime or call an LLM; UI, portraits, voice, and effects
  belong to G24/G23/G25/G21.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the narrative gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Load only validated worldpack narrative IDs and typed cross-references.
3. Evaluate conditions against authoritative state with stable ordering.
4. Enforce who knows each fact; never expose global knowledge through dialogue.
5. Emit typed dialogue, quest, scene, and campaign events for presentation.
6. Test unreachable nodes, time windows, softlocks, campaign ownership, and replay.

## Completion

Complete when all declared narrative graphs are reachable or intentionally
terminal, knowledge boundaries and ownership hold, no tested route softlocks,
replay hashes match, and focused/headless tests plus the audit pass. If the
vendored runtime already meets G13, verify it without duplicating it in game code.
