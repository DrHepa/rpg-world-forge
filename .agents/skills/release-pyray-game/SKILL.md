---
name: release-pyray-game
description: Package an already verified standalone pyray/raylib game as the deterministic M4 unpack-and-run source ZIP. Use only for G32 source packaging, not native installers, implementation, testing, or optimization.
---

# Release a pyray game

## Scope

Package only the exact G32 verified game into one deterministic portable source
ZIP. Do not build a wheel, native executable, installer, or platform matrix, and
do not implement, fix, optimize, migrate, import, or change configuration.

## Inputs

- Absolute `FORGE_ROOT`, read-only external `GAME_ROOT`, and external output root.
- Clean commit or verified clean staging-tree hash plus exact runtime,
  `platform.lock.json`, `requirements.lock`, catalog, bundle, license, and notice
  identities, including `game_data/shared.lock.json`.
- Matching final G30 evidence and matching G31 evidence when used.

## Outputs

- One source ZIP in the external output root with a deterministic manifest, file
  hashes, verifier, tests, notices/licenses, known limits, and run instructions.
- Forge-owned G32 release record keyed to every input and archive hash.

## Invariants

- Keep `GAME_ROOT` read-only; build and stage only in the explicit external root.
- Never package canon, `AGENTS.md`, `.agents`, Forge skills, prompts, candidates,
  provider/model data, credentials, phase evidence, live world paths, or symlinks.
- Any source, lock, catalog, bundle, notice, test, or packaging-config change
  returns to its owning phase and requires a new exact-tree G30.

## Workflow

1. Read the release gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Require final G30 evidence keyed to this exact commit/source-tree, runtime,
   platform, and complete catalog hashes; require G31 evidence only if G31 ran.
3. Freeze game code, runtime snapshot hash, and every imported bundle release.
4. Run `scripts/package_game.py` with a new path outside `GAME_ROOT`; it must
   snapshot and reverify the exact tree before publishing the archive.
5. Audit the ZIP allowlist, data hashes, entry point, and offline headless startup
   after extraction with no Forge checkout or network access.
6. Verify licenses/notices for raylib, its binding, runtime, bundles, and media.
7. Return the archive hash, exact Python/platform lock requirements, known limits,
   extraction/run instructions, and rollback instructions.

## Completion

Complete only when the source ZIP builds from the exact verified inputs and
passes allowlist inspection, isolated re-verification, offline headless startup,
embedded data hashes, entry-point smoke, and notice/license checks. Record its
hash externally. Native artifacts require a later dedicated skill and milestone.
