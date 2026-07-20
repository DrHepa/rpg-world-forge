---
name: implement-pyray-resources
description: Implement validated texture, atlas, font, shader, render-texture, and native-handle ownership for a pyray game. Use for the graphics-resource phase only.
---

# Implement graphics resources

## Scope

Implement only G23 verified graphics-resource acquisition, ownership, lookup,
and reverse-order teardown for textures, atlases, fonts, shaders, and targets.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G20-G22 gates.
- Current source-tree/runtime/platform/catalog hashes and G23 writable surface.
- Selected world ID, release ID, and verified bundle/renderpack hashes.
- Verified renderpack inventory/hashes, semantic bindings, and platform lock.
- Current `game_data/shared.lock.json` hash and any declared game-owned graphics
  additions beneath `game_data/shared/**`.
- Required/optional resource policy and failure-injection fixtures.

## Outputs

- One game-owned native graphics registry/adapter with semantic lookup.
- When needed, one reviewed graphics asset change, its origin/license entry in
  `THIRD_PARTY_NOTICES.md`, and a regenerated canonical shared-asset lock.
- Fake-adapter failure tests and hidden-window native load/draw/unload evidence.
- Forge-owned G23 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, runtime/platform/catalog locks, bundles, simulation, and
  render planning immutable. Change the shared lock only through its lock script.
- Let one registry own every handle; validate acquisitions and unwind partial
  loads in reverse order before window teardown.
- Never load during update/draw or expose paths/handles to simulation.
- Let G20 orchestrate lifecycle; G23 alone owns graphics handles and cleanup.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the resource gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Resolve only hash-verified renderpack paths beneath the imported bundle root
   or hash-locked game-owned paths beneath `game_data/shared/**`.
3. Let one registry own each native handle and its load/unload state.
4. Validate handles and atlas clips; unload partial acquisitions on every failure.
5. Load before the loop and unload in reverse order before closing the window.
6. If shared graphics changed, record origin/license in
   `THIRD_PARTY_NOTICES.md`, run `python scripts/lock_shared_assets.py`, and
   verify that no undeclared, authoring, or provider metadata entered the lock.
7. Test the resource plan with a fake adapter and smoke-test native teardown.

## Completion

Complete when missing/invalid/partial loads clean up safely, required resources
draw in a hidden-window smoke test, optional fallbacks work, all handles unload
once, and focused/headless tests plus the audit pass.
