---
name: import-world-bundle
description: Import one verified immutable world release into an existing standalone game and update its runtime-neutral catalog. Use only for game content ingestion.
---

# Import a world bundle

## Scope

Verify and atomically import one immutable release in G03. Do not author,
compile, repair, or mount world content and do not change the runtime.

## Inputs

- Absolute `FORGE_ROOT`, external `GAME_ROOT`, and candidate bundle path.
- Expected bundle hash plus explicit world and stable release IDs.
- Current source-tree, runtime/platform-lock, and catalog hashes plus previous gate.
- Vendored runtime API and feature support.

## Outputs

- One new immutable `game_data/worlds/<world>/<release>` copy and one atomic
  `worlds.lock.json` update.
- Forge-owned import evidence and downstream invalidation record.

## Invariants

- Write only the catalog and new release path; preserve every existing release.
- Reject incompatible, mutable, symlinked, provider-bearing, or hash-mismatched
  input before touching `GAME_ROOT`.
- Never copy `AGENTS.md`, `.agents`, Forge skills, canon, prompts, phase reports,
  or production evidence into `GAME_ROOT`.

## Workflow

1. Read the bundle gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Verify the source bundle and its expected hash before touching the game.
3. Check the bundle's runtime version/features against the vendored runtime.
   If incompatible, stop and enter G01 before retrying this import.
4. Run `worldforge import-bundle` with explicit bundle and game paths.
5. Verify the copied release and updated `worlds.lock.json` from inside the game.
6. Run the headless load test for the exact world/release selection.
7. Invalidate catalog-wide G30-G32 evidence and record world-specific gaps.

## Completion

Complete only when source and copied hashes agree, all catalog entries reverify,
the exact selection loads headlessly, and the clean-game audit passes. A changed
release hash requires a new release ID.
