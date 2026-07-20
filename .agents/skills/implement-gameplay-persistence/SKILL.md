---
name: implement-gameplay-persistence
description: Implement versioned save serialization, restore, compatibility checks, atomic writes, and migrations in a standalone game. Use for G15 persistence only; G04 owns replay infrastructure.
---

# Implement persistence

## Scope

Implement only G15 save serialization, restore, compatibility, atomic storage,
and tested migrations. G04 owns generic replay/digest infrastructure; G15 uses it.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with passing G04/G10-G14 gates.
- Current source-tree/runtime/platform/catalog hashes and G15 writable surface.
- Selected world ID, release ID, and verified bundle hash for save fixtures.
- Save schema/version policy, writable user-data root, and supported migrations.
- Runtime, world/release, bundle, tick, and deterministic state identity contracts.

## Outputs

- Game-owned save policy/adapters, schemas/migrations, and corruption fixtures.
- Round-trip, wrong-bundle, migration, and replay-equivalence tests.
- Forge-owned G15 completion record outside `GAME_ROOT`.

## Invariants

- Keep `src/isoworld`, locks, bundles, and G04 harness immutable.
- Separate settings from saves; never serialize native handles, transient render
  state, authoring paths, agent files, or provider metadata.
- Write atomically and preserve the previous valid save on failure.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the persistence gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Keep saves in a writable user-data root, separate from packaged bundles.
3. Record save format, runtime contract, world/release hash, tick, and state.
4. Reject incompatible content explicitly; migrate only through tested versions.
5. Write atomically and preserve the previous valid save on failure.
6. Test round trips, corruption, migrations, G04 replay hashes, and wrong bundles.

## Completion

Complete when supported saves round-trip to the same state digest, corruption
and wrong content fail safely, every migration is deterministic, focused/headless
tests and the clean-game audit pass, and restore compatibility is documented. If
the runtime already meets G15, verify it without duplicating it in game code.
