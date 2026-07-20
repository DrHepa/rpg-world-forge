---
name: update-game-runtime
description: Upgrade the vendored isoworld runtime snapshot in a standalone game. Use only for an explicit runtime-version update, migration, or compatibility repair.
---

# Update a game runtime snapshot

## Scope

Replace the complete vendored runtime and its lock in G01. Do not perform a
platform migration, import content, or implement game-specific behavior.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT`.
- Expected current runtime hash and candidate Forge runtime revision.
- Current game source-tree, platform-lock, and catalog hashes plus previous gate.
- Imported bundle/runtime requirements, save versions, and prior test evidence.

## Outputs

- One atomic whole-snapshot replacement of `src/isoworld` and
  `runtime.lock.json`.
- Forge-owned compatibility results and G04-G32 invalidation record.

## Invariants

- Treat the expected current hash as an optimistic lock.
- Never hand-copy modules, leave a partial snapshot, change the platform lock,
  mutate bundles, or introduce a live `worldforge` dependency.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.
- Roll back the snapshot and lock together on any failure.

## Workflow

1. Read the runtime-update gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Record the current runtime hash, imported bundles, save versions, and tests.
3. Check every imported bundle against the candidate runtime contract.
4. Run the Forge-side snapshot update with an expected current hash.
5. Review the complete snapshot diff; never hand-copy individual modules.
6. Run compatibility, replay/save migration, boundary, and game tests.
7. If G04 or later evidence exists, invalidate it and rerun G04-G32 as applicable.

## Completion

Complete only when every retained bundle and supported save contract passes,
the runtime lock matches the whole snapshot, game tests and boundary audit pass,
and invalidated downstream work is recorded outside `GAME_ROOT`.
